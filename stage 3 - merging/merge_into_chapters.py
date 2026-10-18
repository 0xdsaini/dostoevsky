#!/usr/bin/env python3
"""
Merge MP3 chunks into one MP3 per chapter.

Expected filenames:
    Part_I_Chapter_01_chunk_001.mp3
    Part_I_Chapter_01_chunk_002.mp3
    Part_II_Chapter_03_chunk_010.mp3

Requirements:
    python -m pip install mutagen

Run this script from the directory containing the chunk MP3 files:
    python merge_chapters.py

Outputs are written to ./merged_chapters/
Original chunk files are never modified.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    from mutagen.mp3 import MP3
except ImportError:
    raise SystemExit(
        "\nMissing dependency: mutagen\n"
        "Install it in your active virtual environment with:\n"
        "    python -m pip install mutagen\n"
    )

# The script looks for chunk files in the directory from which you run it.
SOURCE_DIR = Path.cwd()
OUTPUT_DIR = SOURCE_DIR / "merged_chapters"

# Matches the part/chapter prefix and numeric chunk suffix.
FILENAME_RE = re.compile(
    r"^(Part_[IVXLCDM]+_Chapter_\d+)_chunk_(\d+)\.mp3$",
    re.IGNORECASE,
)

# Files smaller than this are flagged for review, not automatically rejected.
# Small files can be legitimate final chunks, so this is only a warning.
SMALL_FILE_WARNING_BYTES = 100_000


def remove_mp3_tags(data: bytes) -> bytes:
    """
    Remove common metadata tags from a chunk before concatenating.

    MP3 audio is stored in frames. ID3 metadata at the beginning/end of
    individual chunks should not be copied into the middle of the merged
    file, because some players may interpret it as junk between frames.

    This function does not decode or re-encode audio.
    """
    # Remove an ID3v2 tag at the start, if present.
    if data.startswith(b"ID3") and len(data) >= 10:
        size_bytes = data[6:10]

        # ID3v2 uses four 7-bit "synchsafe" size bytes.
        if all(byte < 128 for byte in size_bytes):
            tag_size = (
                (size_bytes[0] << 21)
                | (size_bytes[1] << 14)
                | (size_bytes[2] << 7)
                | size_bytes[3]
            )
            # The header is 10 bytes. ID3v2.4 may also have a 10-byte footer.
            footer_size = 10 if data[5] & 0x10 else 0
            end_of_tag = 10 + tag_size + footer_size

            if end_of_tag <= len(data):
                data = data[end_of_tag:]

    # Remove a trailing ID3v1 tag (128 bytes) if present.
    if len(data) >= 128 and data[-128:-125] == b"TAG":
        data = data[:-128]

    # Remove a trailing APE tag if one is present.
    # APE tags end with "APETAGEX"; the size is stored 12 bytes before the end.
    if len(data) >= 32 and data[-32:-24] == b"APETAGEX":
        tag_size = int.from_bytes(data[-20:-16], "little")
        if 32 <= tag_size <= len(data):
            data = data[:-tag_size]

    return data


def group_input_files(source_dir: Path):
    """Return {chapter_name: [(chunk_number, path), ...]}."""
    groups = defaultdict(list)
    ignored_mp3s = []

    for path in source_dir.iterdir():
        if not path.is_file() or path.suffix.lower() != ".mp3":
            continue

        match = FILENAME_RE.match(path.name)
        if not match:
            ignored_mp3s.append(path.name)
            continue

        chapter_name, chunk_number = match.groups()
        # Normalize the prefix to the capitalization used in the filenames.
        groups[chapter_name].append((int(chunk_number), path))

    for chapter_name in groups:
        groups[chapter_name].sort(key=lambda item: item[0])

    return groups, ignored_mp3s


def inspect_chapter(chapter_name, chunks):
    """Report duplicate/missing numbers and unusually small files."""
    numbers = [number for number, _ in chunks]
    problems = []

    duplicates = sorted(
        number for number in set(numbers) if numbers.count(number) > 1
    )
    if duplicates:
        problems.append(f"duplicate chunk number(s): {duplicates}")

    expected = set(range(1, max(numbers) + 1))
    missing = sorted(expected - set(numbers))
    if missing:
        problems.append(f"missing chunk number(s): {missing}")

    for number, path in chunks:
        try:
            size = path.stat().st_size
        except OSError as exc:
            problems.append(f"cannot read {path.name}: {exc}")
            continue

        if size == 0:
            problems.append(f"empty file: {path.name}")
        elif size < SMALL_FILE_WARNING_BYTES:
            print(
                f"  WARNING: {path.name} is only {size:,} bytes. "
                "It may be a short final chunk or an incomplete generation."
            )

    return problems


def validate_mp3(path: Path):
    """Use Mutagen to confirm the file can be parsed and has a duration."""
    audio = MP3(path)
    duration = audio.info.length

    if duration is None or duration <= 0:
        raise ValueError("MP3 has no valid positive duration")

    return duration


def merge_one_chapter(chapter_name, chunks, output_dir: Path):
    """Write one chapter to a temporary file, validate it, then finalize it."""
    output_path = output_dir / f"{chapter_name}.mp3"
    temp_path = output_dir / f".{chapter_name}.mp3.tmp"

    if output_path.exists():
        answer = input(
            f"  Output already exists: {output_path.name}\n"
            "  Replace it? (y/N): "
        ).strip().lower()
        if answer != "y":
            print("  Skipped; existing output was left untouched.")
            return False

    total_input_bytes = sum(path.stat().st_size for _, path in chunks)
    written_bytes = 0

    try:
        # "xb" prevents accidentally overwriting a stale temporary file.
        # If a previous run was interrupted, remove the temporary file manually
        # or let the user decide before rerunning.
        if temp_path.exists():
            temp_path.unlink()

        with temp_path.open("xb") as destination:
            for index, (chunk_number, chunk_path) in enumerate(chunks, start=1):
                data = chunk_path.read_bytes()
                data = remove_mp3_tags(data)

                if not data:
                    raise ValueError(
                        f"{chunk_path.name} contains no audio data after tag removal"
                    )

                destination.write(data)
                written_bytes += len(data)

                print(
                    f"  [{index}/{len(chunks)}] Added "
                    f"{chunk_path.name} ({len(data):,} bytes)"
                )

        duration_seconds = validate_mp3(temp_path)

        # Atomic rename within the same directory; output appears only after
        # the temporary file has been written and passed the parser check.
        temp_path.replace(output_path)

        print(f"  DONE: {output_path.name}")
        print(f"  Chunks: {len(chunks)}")
        print(f"  Input bytes: {total_input_bytes:,}")
        print(f"  Output bytes: {written_bytes:,}")
        print(
            f"  Parsed duration: {int(duration_seconds // 60)} min "
            f"{int(duration_seconds % 60)} sec"
        )
        return True

    except Exception as exc:
        print(f"  ERROR while merging {chapter_name}: {exc}")
        print(f"  Temporary file, if present: {temp_path}")
        print("  Original chunk files have not been modified.")
        return False


def main():
    if not SOURCE_DIR.exists():
        raise SystemExit(f"Source directory does not exist: {SOURCE_DIR}")

    groups, ignored_mp3s = group_input_files(SOURCE_DIR)

    print("=" * 72)
    print("MP3 CHAPTER MERGER")
    print("=" * 72)
    print(f"Input directory : {SOURCE_DIR}")
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Chapters found  : {len(groups)}")

    if ignored_mp3s:
        print(f"MP3 files ignored because their names did not match: {len(ignored_mp3s)}")
        for name in ignored_mp3s[:10]:
            print(f"  - {name}")
        if len(ignored_mp3s) > 10:
            print(f"  ... and {len(ignored_mp3s) - 10} more")

    if not groups:
        raise SystemExit(
            "\nNo matching chunks found. Run this script from the folder "
            "containing files named like Part_I_Chapter_01_chunk_001.mp3."
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    successful = 0
    skipped_or_failed = 0

    for chapter_name in sorted(groups):
        chunks = groups[chapter_name]
        print("\n" + "-" * 72)
        print(f"{chapter_name}: {len(chunks)} chunk(s)")

        problems = inspect_chapter(chapter_name, chunks)
        if problems:
            print("  REVIEW REQUIRED:")
            for problem in problems:
                print(f"  - {problem}")

            answer = input("  Continue with this chapter anyway? (y/N): ")
            if answer.strip().lower() != "y":
                print("  Skipped.")
                skipped_or_failed += 1
                continue

        # Check every chunk is readable before writing the merged chapter.
        invalid = []
        for number, path in chunks:
            try:
                if path.stat().st_size == 0:
                    raise ValueError("file is empty")
                validate_mp3(path)
            except Exception as exc:
                invalid.append((path.name, str(exc)))

        if invalid:
            print("  One or more chunks failed MP3 validation:")
            for name, reason in invalid:
                print(f"  - {name}: {reason}")
            print("  Skipping chapter to avoid silently merging a bad input.")
            skipped_or_failed += 1
            continue

        if merge_one_chapter(chapter_name, chunks, OUTPUT_DIR):
            successful += 1
        else:
            skipped_or_failed += 1

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"Chapters merged successfully: {successful}")
    print(f"Chapters skipped or failed  : {skipped_or_failed}")
    print(f"Output directory            : {OUTPUT_DIR.resolve()}")
    print("Original chunk files were not modified.")
    print(
        "\nNote: successful MP3 parsing verifies basic readability, not that "
        "the narration is complete or that chunk boundaries sound seamless."
    )


if __name__ == "__main__":
    main()
