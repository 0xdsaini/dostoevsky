#!/usr/bin/env python3
"""
Prepare word-by-word read-along data for The Idiot audiobook.

Run from the repository root:
    python3 prepare_readalong.py

Expected audio:
    build/audio_in/merged_chapters/Part_I_Chapter_01.mp3
Output:
    readalong/Part_I_Chapter_01.json

Requires Vosk, imageio-ffmpeg (or system ffmpeg), and the local model at models/vosk-model-small-en-us-0.15. The bundled ffmpeg converts MP3 files to mono 16 kHz PCM WAV for local transcription (works in GitHub Codespaces/Linux and macOS).
"""
from __future__ import annotations

import difflib
import html
import json
import re
import sys
import time
import urllib.request
import subprocess
import tempfile
import shutil
import wave
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AUDIO_DIR = ROOT / "build" / "audio_in" / "merged_chapters"
OUT_DIR = ROOT / "readalong"
GUTENBERG_URL = "https://www.gutenberg.org/cache/epub/2638/pg2638-images.html"
VOSK_MODEL_DIR = ROOT / "models" / "vosk-model-small-en-us-0.15"
SAMPLE_RATE = 16000

PART_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV"}
CHAPTER_COUNTS = {1: 16, 2: 12, 3: 10, 4: 12}


class TextExtractor(HTMLParser):
    """Extract readable Gutenberg text while preserving paragraph/heading breaks."""
    BLOCKS = {
        "p", "div", "h1", "h2", "h3", "h4", "br", "li", "blockquote",
        "hr", "tr", "section", "title"
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        raw = html.unescape("".join(self.parts))
        raw = raw.replace("\xa0", " ")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        out = []
        for line in lines:
            if line or (out and out[-1] != ""):
                out.append(line)
        return "\n".join(out)


def download_book() -> str:
    req = urllib.request.Request(
        GUTENBERG_URL,
        headers={"User-Agent": "LocalAudiobookReadAlong/1.0 (personal educational use)"}
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        page = response.read().decode("utf-8", errors="replace")
    parser = TextExtractor()
    parser.feed(page)
    return parser.text()


def extract_chapters(book_text: str) -> dict[str, str]:
    """
    Split Gutenberg's four PART sections at standalone Roman chapter headings.
    Chapter titles in this edition are headings such as I., II., III.
    """
    lines = book_text.splitlines()
    # Keep text after the Gutenberg start marker and before its end marker.
    start = next((i for i, x in enumerate(lines) if "START OF THE PROJECT GUTENBERG EBOOK THE IDIOT" in x), 0)
    end = next((i for i, x in enumerate(lines) if "END OF THE PROJECT GUTENBERG EBOOK THE IDIOT" in x), len(lines))
    lines = lines[start:end]

    # The contents page repeats PART I–IV before the actual chapter text.
    # Keep the last occurrence of each standalone part heading so extraction
    # starts at the real body section, not at the table of contents.
    part_occurrences = {1: [], 2: [], 3: [], 4: []}
    for i, line in enumerate(lines):
        m = re.fullmatch(r"PART\s+(I{1,3}|IV)[.]?", line.strip(), flags=re.I)
        if m:
            roman = m.group(1).upper()
            part = {"I": 1, "II": 2, "III": 3, "IV": 4}[roman]
            part_occurrences[part].append(i)
    part_starts = {
        part: positions[-1]
        for part, positions in part_occurrences.items()
        if positions
    }

    chapters = {}
    roman_to_int = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6,
                    "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12,
                    "XIII": 13, "XIV": 14, "XV": 15, "XVI": 16}
    for part in range(1, 5):
        if part not in part_starts:
            raise RuntimeError(f"Could not find PART {PART_ROMAN[part]} in Gutenberg text.")
        begin = part_starts[part] + 1
        later = [v for p, v in part_starts.items() if p > part]
        finish = min(later) if later else len(lines)
        section = lines[begin:finish]
        headings = []
        for i, line in enumerate(section):
            s = line.strip().strip()
            # Avoid matching roman numerals in prose: Gutenberg chapter heading is its own line.
            m = re.fullmatch(r"([IVX]{1,6})\.", s)
            if m and m.group(1) in roman_to_int:
                headings.append((i, roman_to_int[m.group(1)]))
        # De-duplicate adjacent heading detections.
        unique = []
        for item in headings:
            if not unique or item != unique[-1]:
                unique.append(item)
        for idx, (line_idx, chapter_num) in enumerate(unique):
            if chapter_num > CHAPTER_COUNTS[part]:
                continue
            stop = unique[idx + 1][0] if idx + 1 < len(unique) else len(section)
            content = "\n".join(section[line_idx + 1:stop]).strip()
            # Remove empty lines only at boundaries; preserve paragraph breaks.
            content = re.sub(r"\n{3,}", "\n\n", content)
            if content:
                name = f"Part_{PART_ROMAN[part]}_Chapter_{chapter_num:02d}"
                chapters[name] = content

    expected = sum(CHAPTER_COUNTS.values())
    if len(chapters) < expected:
        missing = []
        for p, count in CHAPTER_COUNTS.items():
            for c in range(1, count + 1):
                key = f"Part_{PART_ROMAN[p]}_Chapter_{c:02d}"
                if key not in chapters:
                    missing.append(key)
        raise RuntimeError(
            f"Only extracted {len(chapters)}/{expected} chapters. Missing: {', '.join(missing[:12])}. "
            "The Gutenberg page structure may have changed; no timing files were generated."
        )
    return chapters


def norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"[^a-z0-9']", "", s)


def source_tokens(text: str):
    # Keep punctuation attached to each word; remember whitespace after each token.
    matches = list(re.finditer(r"\S+", text))
    result = []
    for m in matches:
        token = m.group()
        tail = text[m.end():matches[len(result)+1].start()] if len(result)+1 < len(matches) else text[m.end():]
        result.append({"text": token, "spaceAfter": tail, "key": norm(token), "start": None, "end": None})
    return result


def align_words(tokens, recognized):
    source_keys = [t["key"] for t in tokens]
    rec_words = []
    for word in recognized:
        if isinstance(word, dict):
            raw_word, start, end = word["word"], word["start"], word["end"]
        else:
            raw_word, start, end = word.word, word.start, word.end
        key = norm(raw_word)
        if key:
            rec_words.append((key, float(start), float(end)))
    rec_keys = [x[0] for x in rec_words]
    matcher = difflib.SequenceMatcher(a=source_keys, b=rec_keys, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                tokens[i]["start"] = rec_words[j][1]
                tokens[i]["end"] = rec_words[j][2]

    # Fill unaligned source words between known anchors. This is an estimate;
    # exact recognized words retain Vosk's original timestamps.
    anchors = [i for i, t in enumerate(tokens) if t["start"] is not None]
    if not anchors:
        return tokens, 0
    first = anchors[0]
    if first > 0:
        end = tokens[first]["start"]
        step = min(0.22, end / max(1, first))
        for i in range(first):
            tokens[i]["end"] = max(0.0, end - step * (first - i - 1))
            tokens[i]["start"] = max(0.0, tokens[i]["end"] - step)
    for left, right in zip(anchors, anchors[1:]):
        if right - left <= 1:
            continue
        t0 = tokens[left]["end"]
        t1 = tokens[right]["start"]
        gap_count = right - left - 1
        step = max(0.03, (t1 - t0) / (gap_count + 1))
        for k in range(1, gap_count + 1):
            i = left + k
            tokens[i]["start"] = t0 + step * (k - 1)
            tokens[i]["end"] = min(t1, t0 + step * k)
    last = anchors[-1]
    for i in range(last + 1, len(tokens)):
        prev = tokens[i - 1]["end"] or tokens[i - 1]["start"] or 0
        tokens[i]["start"] = prev
        tokens[i]["end"] = prev + 0.18

    matched = sum(t["start"] is not None for t in tokens)
    for t in tokens:
        t.pop("key", None)
        if t["start"] is None:
            t["start"], t["end"] = 0.0, 0.0
        t["start"] = round(float(t["start"]), 3)
        t["end"] = round(max(float(t["start"]), float(t["end"])), 3)
    return tokens, matched


def main():
    if not AUDIO_DIR.exists():
        sys.exit(f"Audio directory not found: {AUDIO_DIR}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading Gutenberg text: {GUTENBERG_URL}")
    try:
        book = download_book()
        chapters = extract_chapters(book)
    except Exception as exc:
        sys.exit(f"Could not prepare Gutenberg chapters: {exc}")

    # Save the source text too, so the repository records the exact edition used.
    source_dir = ROOT / "readalong" / "source_text"
    source_dir.mkdir(parents=True, exist_ok=True)
    for name, text in chapters.items():
        (source_dir / f"{name}.txt").write_text(text, encoding="utf-8")

    if not VOSK_MODEL_DIR.is_dir():
        sys.exit(f"Vosk model folder not found: {VOSK_MODEL_DIR}\nMake sure models/vosk-model-small-en-us-0.15/ exists.")
    try:
        from vosk import Model, KaldiRecognizer
    except ImportError:
        sys.exit("Missing Vosk. Install in your existing venv with: pip install vosk")

    # Resolve FFmpeg once, before processing chapters, so an environment problem
    # fails immediately instead of producing 50 identical errors.
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        try:
            import imageio_ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            sys.exit(
                "FFmpeg is unavailable. Install the bundled binary in the SAME "
                "Python environment with: python -m pip install imageio-ffmpeg"
            )
    try:
        subprocess.run([ffmpeg_bin, "-version"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.exit(
            f"Could not run FFmpeg at {ffmpeg_bin!r}. Run "
            "python -m pip install --upgrade imageio-ffmpeg, then retry. "
            f"Details: {exc}"
        )
    print(f"Using FFmpeg: {ffmpeg_bin}")
    print(f"Loading local Vosk model: {VOSK_MODEL_DIR}")
    model = Model(str(VOSK_MODEL_DIR))
    total = len(chapters)
    made = skipped = failed = 0

    for number, (name, text) in enumerate(chapters.items(), 1):
        audio_path = AUDIO_DIR / f"{name}.mp3"
        output_path = OUT_DIR / f"{name}.json"
        if output_path.exists() and audio_path.exists():
            try:
                old = json.loads(output_path.read_text(encoding="utf-8"))
                if (old.get("audio") == f"../build/audio_in/merged_chapters/{name}.mp3"
                    and old.get("words")
                    and old.get("alignment", {}).get("method", "").startswith("Vosk")):
                    print(f"[{number}/{total}] Cached: {name}")
                    skipped += 1
                    continue
            except Exception:
                pass
        if not audio_path.exists():
            print(f"[{number}/{total}] MISSING AUDIO: {audio_path.name}")
            failed += 1
            continue

        print(f"[{number}/{total}] Transcribing and aligning: {audio_path.name}")
        try:
            # Vosk accepts PCM, not MP3. Convert to mono, 16-bit PCM, 16 kHz WAV.
            with tempfile.TemporaryDirectory(prefix="readalong_") as temp_dir:
                wav_path = Path(temp_dir) / f"{name}.wav"
                subprocess.run(
                    [ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                     "-i", str(audio_path), "-ac", "1", "-ar", str(SAMPLE_RATE),
                     "-c:a", "pcm_s16le", str(wav_path)],
                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                recognized = []
                with wave.open(str(wav_path), "rb") as wav_file:
                    channels = wav_file.getnchannels()
                    sample_rate = wav_file.getframerate()
                    if channels != 1 or sample_rate != SAMPLE_RATE or wav_file.getsampwidth() != 2:
                        raise RuntimeError(
                            f"Unexpected WAV format: {channels} channels, {sample_rate} Hz, "
                            f"{wav_file.getsampwidth() * 8}-bit"
                        )
                    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
                    recognizer.SetWords(True)
                    while True:
                        pcm = wav_file.readframes(4000)
                        if not pcm:
                            break
                        if recognizer.AcceptWaveform(pcm):
                            recognized.extend(json.loads(recognizer.Result()).get("result", []))
                    recognized.extend(json.loads(recognizer.FinalResult()).get("result", []))
            tokens, matched = align_words(source_tokens(text), recognized)
            data = {
                "title": "The Idiot",
                "translator": "Eva Martin",
                "source": "Project Gutenberg eBook #2638",
                "part": name.split("_")[1],
                "chapter": int(name.rsplit("_", 1)[1]),
                "audio": f"../build/audio_in/merged_chapters/{name}.mp3",
                "alignment": {
                    "method": "Vosk word timestamps matched to Gutenberg text",
                    "matched_source_words": matched,
                    "total_source_words": len(tokens),
                    "match_ratio": round(matched / max(1, len(tokens)), 4),
                    "note": "Unmatched source words receive estimated timing; review low match ratios."
                },
                "words": [
                    {"text": t["text"], "spaceAfter": t["spaceAfter"], "start": t["start"], "end": t["end"]}
                    for t in tokens
                ]
            }
            output_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            print(f"    Saved {output_path.relative_to(ROOT)} — matched {matched}/{len(tokens)} source tokens ({data['alignment']['match_ratio']:.1%})")
            made += 1
        except Exception as exc:
            print(f"    ERROR: {exc}")
            failed += 1

    print("\nRead-along preparation complete.")
    print(f"Generated: {made} | Cached: {skipped} | Missing/failed: {failed}")
    print(f"Output folder: {OUT_DIR}")
    print("Commit index.html, readalong/*.json, readalong/source_text/*.txt, and the published MP3 files to GitHub.")


if __name__ == "__main__":
    main()
