
#!/usr/bin/env python3
"""Offline audiobook verification. Never modifies audio files."""

import csv
import hashlib
import json
import math
import re
import sys
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import av
from rapidfuzz import fuzz
from vosk import Model, KaldiRecognizer


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.csv"
AUDIO_DIR = ROOT / "audio_in"
MODEL_DIR = ROOT / "models" / "vosk-model-small-en-us-0.15"
REPORT_DIR = ROOT / "verification"
CACHE_FILE = REPORT_DIR / "transcripts.json"
MATCH_CACHE_FILE = REPORT_DIR / "matches.json"
MATCH_ALGORITHM_VERSION = 2

# Tune these after inspecting the first report.
MIN_MATCH_SCORE = 70
MIN_MARGIN = 2
CANDIDATE_COUNT = 25
SAMPLE_RATE = 16000
CHECK_SECONDS = 10


def relative(path):
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def normalize(text):
    text = text.lower().replace("’", "'")
    return re.sub(r"[^a-z0-9']+", " ", text).strip()


def tokens(text):
    return set(normalize(text).split())


def read_manifest():
    if not MANIFEST.exists():
        sys.exit(f"Missing manifest: {MANIFEST}")

    with MANIFEST.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        sys.exit("manifest.csv has no rows.")

    for row in rows:
        row["text_path"] = ROOT / row["text_file"]
        row["expected_audio"] = Path(row["audio_file"]).name

        if not row["text_path"].is_file():
            sys.exit(f"Missing expected text: {row['text_path']}")

        row["expected_text"] = row["text_path"].read_text(
            encoding="utf-8"
        )

    return rows


def load_cache():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print("Transcript cache unreadable; rebuilding it.")
    return {}


def save_cache(cache):
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )



def transcribe(path, model, cache):
    """Transcribe only the first 10 seconds using local Vosk."""
    key = relative(path)
    stat = path.stat()
    cache_key = f"{key}:first10s"

    old = cache.get(cache_key)
    if (
        old
        and old.get("size") == stat.st_size
        and old.get("mtime_ns") == stat.st_mtime_ns
        and old.get("transcript")
    ):
        print(f"  Cached: {path.name}")
        return old["transcript"]

    print(f"  Checking first {CHECK_SECONDS}s: {path.name}")

    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(False)

    with av.open(str(path)) as container:
        stream = next(
            (s for s in container.streams if s.type == "audio"),
            None
        )
        if stream is None:
            raise RuntimeError("No audio stream found.")

        resampler = av.AudioResampler(
            format="s16",
            layout="mono",
            rate=SAMPLE_RATE
        )

        audio_samples = 0

        for frame in container.decode(stream):
            frames = resampler.resample(frame)
            if frames is None:
                continue
            if not isinstance(frames, (list, tuple)):
                frames = [frames]

            for converted in frames:
                if converted is None:
                    continue

                remaining = int(
                    CHECK_SECONDS * SAMPLE_RATE - audio_samples
                )
                if remaining <= 0:
                    break

                data = converted.to_ndarray().tobytes()
                usable_bytes = min(len(data), remaining * 2)
                usable_bytes -= usable_bytes % 2
                data = data[:usable_bytes]

                if data:
                    recognizer.AcceptWaveform(data)
                    audio_samples += len(data) // 2

            if audio_samples >= CHECK_SECONDS * SAMPLE_RATE:
                break

    transcript = json.loads(recognizer.FinalResult()).get(
        "text", ""
    ).strip()

    cache[cache_key] = {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "transcript": transcript
    }
    save_cache(cache)
    return transcript



def build_index(rows):
    """Index words from each chunk opening for fast candidate retrieval."""
    document_words = []
    df = Counter()

    for row in rows:
        opening = normalize(row["expected_text"][:1200])
        words = set(opening.split())
        document_words.append(words)
        df.update(words)

    count = len(rows)
    idf = {
        word: math.log((count + 1) / (freq + 1)) + 1
        for word, freq in df.items()
    }

    postings = defaultdict(list)
    for index, words in enumerate(document_words):
        for word in words:
            postings[word].append(index)

    return document_words, idf, postings


def rank_matches(transcript, rows, document_words, idf, postings):
    """Retrieve likely chunks with an inverted index, then fuzzy-score only top candidates."""
    heard_text = normalize(transcript)
    heard_words = set(heard_text.split())

    if len(heard_words) < 3:
        return []

    votes = Counter()
    for word in heard_words:
        weight = idf.get(word, 1.0)
        for index in postings.get(word, []):
            votes[index] += weight

    if not votes:
        return []

    candidate_indices = [
        index for index, _ in votes.most_common(CANDIDATE_COUNT)
    ]

    ranked = []
    for index in candidate_indices:
        expected_prefix = normalize(rows[index]["expected_text"][:1200])
        expected_words = document_words[index]
        if not expected_prefix:
            continue

        sequence_score = fuzz.partial_ratio(heard_text, expected_prefix)
        overlap = heard_words & expected_words
        word_score = 100 * len(overlap) / len(heard_words)
        score = 0.70 * sequence_score + 0.30 * word_score

        ranked.append({
            "index": index,
            "score": round(score, 2),
            "sequence_score": round(sequence_score, 2),
            "coverage": round(word_score, 2),
        })

    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def file_signature(path):
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def text_signature(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_match_cache():
    """Load persistent match decisions; invalidate only on algorithm version changes."""
    if not MATCH_CACHE_FILE.exists():
        return {"version": MATCH_ALGORITHM_VERSION, "entries": {}}

    try:
        data = json.loads(MATCH_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print("Match cache unreadable; rebuilding match decisions.")
        return {"version": MATCH_ALGORITHM_VERSION, "entries": {}}

    if data.get("version") != MATCH_ALGORITHM_VERSION:
        print("Matching algorithm changed; rebuilding saved match decisions once.")
        return {"version": MATCH_ALGORITHM_VERSION, "entries": {}}

    if not isinstance(data.get("entries"), dict):
        return {"version": MATCH_ALGORITHM_VERSION, "entries": {}}

    return data


def save_match_cache(match_cache):
    MATCH_CACHE_FILE.write_text(
        json.dumps(match_cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    if not AUDIO_DIR.is_dir():
        sys.exit(f"Audio directory not found: {AUDIO_DIR}")

    expected = read_manifest()
    audio_files = sorted(
        p for p in AUDIO_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() == ".mp3"
    )

    if not audio_files:
        sys.exit(f"No MP3 files found under {AUDIO_DIR}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    transcript_cache = load_cache()
    match_cache = load_match_cache()
    saved_matches = match_cache["entries"]

    # Build a manifest lookup and signatures so edited text chunks invalidate
    # only matches that depend on the edited chunk (unmatched entries are also
    # retried when the manifest changes).
    expected_by_text = {}
    for row in expected:
        row["text_signature"] = text_signature(row["expected_text"])
        expected_by_text[row["text_file"]] = row
    manifest_signature = text_signature(json.dumps([
        {"text_file": row["text_file"], "text_signature": row["text_signature"]}
        for row in expected
    ], ensure_ascii=False, sort_keys=True))

    current_paths = {relative(path) for path in audio_files}
    # Drop cache entries for files that no longer exist.
    saved_matches = {
        key: value for key, value in saved_matches.items()
        if key in current_paths
    }
    match_cache["entries"] = saved_matches

    # Decide which files need transcription. Load Vosk only if at least one
    # transcript is missing or stale.
    files_needing_transcription = []
    for path in audio_files:
        key = f"{relative(path)}:first10s"
        old = transcript_cache.get(key)
        sig = file_signature(path)
        if not (old and old.get("size") == sig["size"]
                and old.get("mtime_ns") == sig["mtime_ns"]
                and old.get("transcript")):
            files_needing_transcription.append(path)

    print(f"Expected chunks: {len(expected)}")
    print(f"MP3 files found: {len(audio_files)}")
    print(f"Saved match records: {len(saved_matches)}")
    print(f"New/changed files to match: {sum(1 for p in audio_files if not _match_record_valid(saved_matches.get(relative(p)), p, expected_by_text, manifest_signature))}")
    print("No audio or transcript is sent to a remote service.\n")

    transcripts = {}
    failures = []
    model = None

    for number, path in enumerate(files_needing_transcription, start=1):
        print(f"Transcribing [{number}/{len(files_needing_transcription)}] {path.name}")
        try:
            if model is None:
                if not MODEL_DIR.is_dir():
                    sys.exit(
                        f"Local Vosk model not found:\n{MODEL_DIR}\n"
                        "Download and extract the model into models/ first."
                    )
                print("Loading local speech model...")
                model = Model(str(MODEL_DIR))
            transcripts[path] = transcribe(path, model, transcript_cache)
        except Exception as error:
            failures.append({"audio_file": relative(path), "error": str(error)})
            print(f"  ERROR: {error}")

    # Pull cached transcripts for files that did not need transcription.
    for path in audio_files:
        if path in transcripts:
            continue
        key = f"{relative(path)}:first10s"
        old = transcript_cache.get(key)
        if old and old.get("transcript"):
            transcripts[path] = old["transcript"]

    save_cache(transcript_cache)

    document_words, idf, postings = build_index(expected)
    results_by_path = {}
    newly_matched = []
    reused_count = 0

    # Reuse saved decisions for unchanged files. Only new/changed files or
    # records invalidated by a text/algorithm change go through fuzzy matching.
    for path in audio_files:
        key = relative(path)
        record = saved_matches.get(key)
        if _match_record_valid(record, path, expected_by_text, manifest_signature):
            results_by_path[path] = _result_from_record(path, record)
            reused_count += 1
            continue

        transcript = transcripts.get(path, "")
        ranked = rank_matches(transcript, expected, document_words, idf, postings)
        best = ranked[0] if ranked else None
        second = ranked[1] if len(ranked) > 1 else None

        if best:
            row = expected[best["index"]]
            margin = best["score"] - second["score"] if second else best["score"]
            result = {
                "audio_path": path,
                "audio_file": key,
                "matched_chunk": row["text_file"],
                "expected_audio_filename": row["expected_audio"],
                "score": best["score"],
                "second_score": second["score"] if second else "",
                "margin": round(margin, 2),
                "sequence_score": best["sequence_score"],
                "word_coverage": best["coverage"],
                "transcript": transcript,
                "status": "PENDING",
                "_is_new": True,
            }
        else:
            result = {
                "audio_path": path,
                "audio_file": key,
                "matched_chunk": "",
                "expected_audio_filename": "",
                "score": 0,
                "second_score": "",
                "margin": 0,
                "sequence_score": 0,
                "word_coverage": 0,
                "transcript": transcript,
                "status": "LOW_CONFIDENCE",
                "_is_new": True,
            }

        results_by_path[path] = result
        newly_matched.append(result)

    # Existing assignments are stable. New/changed files cannot steal a chunk
    # from an already-saved assignment; collisions are explicitly flagged.
    claimed = set()
    for path, result in results_by_path.items():
        if result.get("_is_new"):
            continue
        if result["matched_chunk"] and result["status"] in (
            "OK", "MATCHED_BUT_MISNAMED", "UNCERTAIN_MATCH"
        ):
            claimed.add(result["matched_chunk"])

    for result in sorted(newly_matched, key=lambda r: r["score"], reverse=True):
        if result["score"] < MIN_MATCH_SCORE or not result["matched_chunk"]:
            result["status"] = "LOW_CONFIDENCE"
            continue

        target = result["matched_chunk"]
        if target in claimed:
            result["status"] = "POSSIBLE_DUPLICATE"
            continue

        claimed.add(target)
        if result["margin"] < MIN_MARGIN:
            result["status"] = "UNCERTAIN_MATCH"
        elif Path(result["audio_file"]).name == result["expected_audio_filename"]:
            result["status"] = "OK"
        else:
            result["status"] = "MATCHED_BUT_MISNAMED"

    # Persist each decision so unchanged files are not rematched next run.
    for path, result in results_by_path.items():
        key = relative(path)
        if result.get("_is_new"):
            target_row = expected_by_text.get(result["matched_chunk"])
            saved_matches[key] = {
                **file_signature(path),
                "algorithm_version": MATCH_ALGORITHM_VERSION,
                "manifest_signature": manifest_signature,
                "matched_chunk": result["matched_chunk"],
                "matched_text_signature": target_row["text_signature"] if target_row else "",
                "expected_audio_filename": result["expected_audio_filename"],
                "score": result["score"],
                "second_score": result["second_score"],
                "margin": result["margin"],
                "sequence_score": result["sequence_score"],
                "word_coverage": result["word_coverage"],
                "transcript": result["transcript"],
                "status": result["status"],
            }

    match_cache["version"] = MATCH_ALGORITHM_VERSION
    match_cache["entries"] = saved_matches
    save_match_cache(match_cache)

    results = [results_by_path[path] for path in audio_files]
    for result in results:
        result.pop("_is_new", None)

    missing = []
    for row in expected:
        matching = [
            r for r in results
            if r["matched_chunk"] == row["text_file"]
            and r["status"] in ("OK", "MATCHED_BUT_MISNAMED")
        ]
        if not matching:
            missing.append({
                "expected_audio_filename": row["expected_audio"],
                "expected_text_file": row["text_file"],
                "part": row["part"],
                "chapter": row["chapter"],
                "chunk": row["chunk"],
                "status": "MISSING_OR_UNVERIFIED",
            })

    report_rows = [{key: value for key, value in result.items() if key != "audio_path"}
                   for result in results]
    write_csv(
        REPORT_DIR / "audio_verification.csv", report_rows,
        ["audio_file", "matched_chunk", "expected_audio_filename", "score",
         "second_score", "margin", "sequence_score", "word_coverage", "status", "transcript"]
    )
    write_csv(
        REPORT_DIR / "missing_chunks.csv", missing,
        ["expected_audio_filename", "expected_text_file", "part", "chapter", "chunk", "status"]
    )
    write_csv(
        REPORT_DIR / "transcription_errors.csv", failures,
        ["audio_file", "error"]
    )

    counts = Counter(r["status"] for r in results)
    print("\n========== VERIFICATION SUMMARY ==========")
    for status, count in sorted(counts.items()):
        print(f"{status:24} {count}")
    print(f"\nExpected text chunks: {len(expected)}")
    print(f"MP3 files found:      {len(audio_files)}")
    print(f"Match decisions reused: {reused_count}")
    print(f"Files newly matched:    {len(newly_matched)}")
    print(f"Missing/unverified:   {len(missing)}")
    print(f"Transcription errors: {len(failures)}")
    print("\nReports saved locally in:")
    print(REPORT_DIR)
    print("\nReview uncertain matches before treating the book as complete.")

    non_ok = [row for row in results if row["status"] != "OK"]
    if non_ok:
        review_script = ROOT / "rename_review.py"
        if review_script.is_file():
            print(
                f"\nFound {len(non_ok)} non-OK audio files. "
                "Opening interactive review..."
            )
            subprocess.run([sys.executable, str(review_script)], cwd=str(ROOT), check=False)
        else:
            print(f"\nReview script not found: {review_script}")


def _match_record_valid(record, path, expected_by_text, manifest_signature):
    """Return true when a saved match still applies to this unchanged file."""
    if not record:
        return False
    sig = file_signature(path)
    if record.get("size") != sig["size"] or record.get("mtime_ns") != sig["mtime_ns"]:
        return False
    if record.get("algorithm_version") != MATCH_ALGORITHM_VERSION:
        return False

    matched_chunk = record.get("matched_chunk", "")
    if matched_chunk:
        row = expected_by_text.get(matched_chunk)
        if not row or record.get("matched_text_signature") != row["text_signature"]:
            return False
    elif record.get("manifest_signature") != manifest_signature:
        # Unmatched items can become matchable when the manifest changes.
        return False

    return True


def _result_from_record(path, record):
    return {
        "audio_path": path,
        "audio_file": relative(path),
        "matched_chunk": record.get("matched_chunk", ""),
        "expected_audio_filename": record.get("expected_audio_filename", ""),
        "score": record.get("score", 0),
        "second_score": record.get("second_score", ""),
        "margin": record.get("margin", 0),
        "sequence_score": record.get("sequence_score", 0),
        "word_coverage": record.get("word_coverage", 0),
        "transcript": record.get("transcript", ""),
        "status": record.get("status", "LOW_CONFIDENCE"),
    }


if __name__ == "__main__":
    main()
