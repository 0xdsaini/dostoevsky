#!/usr/bin/env python3
"""
Merge the already-merged chapter MP3s into one full audiobook MP3.

Expected input:
    ./merged_chapters/Part_I_Chapter_01.mp3
    ./merged_chapters/Part_I_Chapter_02.mp3
    ...
    ./merged_chapters/Part_II_Chapter_01.mp3
    ...

Output:
    ./full_audiobook/The_Idiot_full_audiobook.mp3

Requirements:
    python -m pip install mutagen

Run from the directory containing the merged_chapters folder:
    python merge_full_audiobook.py

This concatenates MP3 data without decoding/re-encoding. Original files are
never modified. It sorts parts and chapters numerically, not alphabetically.
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    from mutagen.mp3 import MP3
except ImportError:
    raise SystemExit(
        "\nMissing dependency: mutagen\n"
        "Install it in your active virtual environment with:\n"
        "    python -m pip install mutagen\n"
    )

BASE_DIR = Path.cwd()
CHAPTER_DIR = BASE_DIR / "merged_chapters"
OUTPUT_DIR = BASE_DIR / "full_audiobook"
OUTPUT_FILE = OUTPUT_DIR / "The_Idiot_full_audiobook.mp3"

# Chapter files expected from merge_chapters.py
CHAPTER_RE = re.compile(
    r"^Part_([IVXLCDM]+)_Chapter_(\d+)\.mp3$",
    re.IGNORECASE,
)

ROMAN_VALUES = {
    "I": 1, "V": 5, "X": 10, "L": 50,
    "C": 100, "D": 500, "M": 1000,
}


def roman_to_int(value: str) -> int:
    """Convert a standard Roman numeral (e.g. IV, III, XII) to an integer."""
    value = value.upper()
    total = 0
    previous = 0

    for char in reversed(value):
        number = ROMAN_VALUES.get(char)
        if number is None:
            raise ValueError(f"Invalid Roman numeral: {value}")

        if number < previous:
            total -= number
        else:
            total += number
            previous = number

    return total


def remove_mp3_tags(data: bytes) -> bytes:
    """Remove common metadata tags so per-file tags aren't copied mid-stream."""
    if data.startswith(b"ID3") and len(data) >= 10:
        size_bytes = data[6:10]
        if all(byte < 128 for byte in size_bytes):
            tag_size = (
                (size_bytes[0] << 21)
                | (size_bytes[1] << 14)
                | (size_bytes[2] << 7)
                | size_bytes[3]
            )
            footer_size = 10 if data[5] & 0x10 else 0
            end_of_tag = 10 + tag_size + footer_size
            if end_of_tag <= len(data):
                data = data[end_of_tag:]

    if len(data) >= 128 and data[-128:-125] == b"TAG":
        data = data[:-128]

    if len(data) >= 32 and data[-32:-24] == b"APETAGEX":
        tag_size = int.from_bytes(data[-20:-16], "little")
        if 32 <= tag_size <= len(data):
            data = data[:-tag_size]

    return data


def chapter_sort_key(path: Path):
    match = CHAPTER_RE.match(path.name)
    if not match:
        return (10**9, 10**9, path.name.lower())

    part_roman, chapter_number = match.groups()
    return (roman_to_int(part_roman), int(chapter_number), path.name.lower())


def validate_mp3(path: Path) -> float:
    audio = MP3(path)
    duration = audio.info.length

    if duration is None or duration <= 0:
        raise ValueError("MP3 has no valid positive duration")

    return duration


def format_duration(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def main():
    print("=" * 72)
    print("FULL AUDIOBOOK MERGER")
    print("=" * 72)
    print(f"Chapter input folder: {CHAPTER_DIR}")
    print(f"Final output file   : {OUTPUT_FILE}")

    if not CHAPTER_DIR.is_dir():
        raise SystemExit(
            "\nCould not find the merged_chapters folder.\n"
            "Run this script from the directory containing merged_chapters, "
            "or edit CHAPTER_DIR in the script."
        )

    chapter_files = [
        path for path in CHAPTER_DIR.iterdir()
        if path.is_file() and CHAPTER_RE.match(path.name)
    ]
    chapter_files.sort(key=chapter_sort_key)

    if not chapter_files:
        raise SystemExit(
            "\nNo merged chapter files found.\n"
            "Expected names such as Part_I_Chapter_01.mp3 inside merged_chapters/."
        )

    print(f"\nFound {len(chapter_files)} chapter files.")
    print("\nChecking all chapters before merging...")

    invalid = []
    total_duration = 0.0
    total_bytes = 0

    for index, path in enumerate(chapter_files, start=1):
        try:
            duration = validate_mp3(path)
            size = path.stat().st_size
            total_duration += duration
            total_bytes += size
            print(
                f"  [{index:03d}/{len(chapter_files):03d}] "
                f"OK  {path.name} | "
                f"{format_duration(duration)} | {size:,} bytes"
            )
        except Exception as exc:
            invalid.append((path.name, str(exc)))
            print(f"  INVALID: {path.name}: {exc}")

    if invalid:
        print("\nSome chapter files failed validation:")
        for name, reason in invalid:
            print(f"  - {name}: {reason}")
        raise SystemExit(
            "\nNothing was merged. Fix or regenerate the invalid chapter files "
            "and run this script again."
        )

    print("\nPre-merge check passed.")
    print(f"Estimated total duration: {format_duration(total_duration)}")
    print(f"Total input size       : {total_bytes:,} bytes")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if OUTPUT_FILE.exists():
        answer = input(
            f"\nThe output already exists:\n  {OUTPUT_FILE}\n"
            "Replace it? (y/N): "
        ).strip().lower()
        if answer != "y":
            raise SystemExit("Cancelled. Existing output was left untouched.")

    temp_file = OUTPUT_FILE.with_name(OUTPUT_FILE.name + ".tmp")
    if temp_file.exists():
        temp_file.unlink()

    print("\nMerging chapters in this order:")
    written_bytes = 0

    try:
        with temp_file.open("xb") as destination:
            for index, path in enumerate(chapter_files, start=1):
                data = remove_mp3_tags(path.read_bytes())
                if not data:
                    raise ValueError(f"No audio data remains in {path.name}")

                destination.write(data)
                written_bytes += len(data)

                print(
                    f"  [{index:03d}/{len(chapter_files):03d}] "
                    f"Added {path.name} ({len(data):,} bytes)"
                )

        final_duration = validate_mp3(temp_file)
        temp_file.replace(OUTPUT_FILE)

        print("\n" + "=" * 72)
        print("SUCCESS")
        print("=" * 72)
        print(f"Output file       : {OUTPUT_FILE.resolve()}")
        print(f"Chapters included : {len(chapter_files)}")
        print(f"Output size       : {written_bytes:,} bytes")
        print(f"Parsed duration   : {format_duration(final_duration)}")
        print(
            "\nThe source chapter files have not been changed. "
            "MP3 parsing confirms basic readability, not narration completeness "
            "or perfectly seamless transitions."
        )

    except Exception as exc:
        print(f"\nERROR: {exc}")
        print(f"Temporary output, if present: {temp_file}")
        print("Source chapter files were not modified.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
