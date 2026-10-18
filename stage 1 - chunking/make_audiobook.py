
#!/usr/bin/env python3
"""
The Idiot — audiobook preparation pipeline
Author: You + ChatGPT

Automates:
1. Downloading the source text
2. Extracting all four parts and chapters
3. Splitting text into WevoLabs-safe chunks
4. Creating a manifest and generation queue
5. Tracking generated audio
6. Identifying missing audio files

Does NOT generate audio or merge MP3 files.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------- CONFIGURATION ----------------

BOOK_URL = (
    "https://www.gutenberg.org/cache/epub/2638/"
    "pg2638-images.html"
)

CHUNK_LIMIT = 4500
TIMEOUT = 60

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "source"
TEXT_DIR = ROOT / "text_chunks"
AUDIO_DIR = ROOT / "audio_in"

MANIFEST = ROOT / "manifest.csv"
QUEUE = ROOT / "generation_queue.csv"
NEXT_CHUNK = ROOT / "NEXT_CHUNK.txt"

HEADERS = {
    "User-Agent": "TheIdiotAudiobookPrep/1.0"
}

PART_RE = re.compile(r"PART\s+([IVXLCDM]+)", re.I)
CHAPTER_RE = re.compile(r"([IVXLCDM]+)\.", re.I)


# ---------------- UTILITIES ----------------

def normalize(text):
    """Normalize whitespace without deleting punctuation."""
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def roman_value(value):
    """Convert Roman numerals to integers for correct sorting."""
    values = {
        "I": 1, "V": 5, "X": 10,
        "L": 50, "C": 100, "D": 500, "M": 1000
    }

    value = value.upper()
    total = 0

    for i, char in enumerate(value):
        current = values[char]
        if i + 1 < len(value) and current < values[value[i + 1]]:
            total -= current
        else:
            total += current

    return total


def part_sort_key(part):
    return roman_value(part)


def safe_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------- DOWNLOAD ----------------

def download_book(force=False):
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    source_file = SOURCE_DIR / "the_idiot.html"

    if source_file.exists() and not force:
        print(f"Using cached source: {source_file}")
        return source_file

    print("Downloading The Idiot from Project Gutenberg...")

    response = requests.get(
        BOOK_URL,
        headers=HEADERS,
        timeout=TIMEOUT
    )
    response.raise_for_status()

    if "html" not in response.headers.get("Content-Type", "").lower():
        raise RuntimeError("The source did not return HTML.")

    safe_write(source_file, response.text)

    print(f"Downloaded: {source_file}")
    return source_file


# ---------------- CHAPTER EXTRACTION ----------------

def extract_chapters(html_file):
    soup = BeautifulSoup(
        html_file.read_text(encoding="utf-8"),
        "html.parser"
    )

    chapters = []
    current_part = None
    chapter_number = 0

    # The Gutenberg edition uses PART headings and Roman numeral
    # chapter headings. We gather paragraphs until the next heading.
    for heading in soup.find_all(["h2", "h3", "h4"]):
        title = normalize(heading.get_text(" ", strip=True))

        if heading.name == "h2":
            match = PART_RE.fullmatch(title)
            if match:
                current_part = match.group(1).upper()
                chapter_number = 0
            else:
                current_part = None
            continue

        if current_part is None:
            continue

        match = CHAPTER_RE.fullmatch(title)
        if not match:
            continue

        chapter_number += 1
        roman = match.group(1).upper()
        paragraphs = []

        # Read the chapter's sibling elements until the next heading.
        for sibling in heading.next_siblings:
            if getattr(sibling, "name", None) in (
                "h2", "h3", "h4"
            ):
                break

            if getattr(sibling, "name", None) == "p":
                paragraph = normalize(
                    sibling.get_text(" ", strip=True)
                )
                if paragraph:
                    paragraphs.append(paragraph)

        body = "\n\n".join(paragraphs)

        if not body:
            print(
                f"WARNING: Empty chapter: Part {current_part}, "
                f"Chapter {roman}"
            )
            continue

        chapters.append({
            "part": current_part,
            "chapter": chapter_number,
            "roman": roman,
            "title": f"Part {current_part}, Chapter {roman}",
            "body": body
        })

    if not chapters:
        raise RuntimeError(
            "No chapters extracted. The HTML structure may have changed."
        )

    return chapters


# ---------------- CHUNKING ----------------

def split_text(text, limit=CHUNK_LIMIT):
    """
    Split on paragraph boundaries where possible.
    Oversized paragraphs are split at word boundaries.
    No text is intentionally discarded.
    """
    if limit < 100:
        raise ValueError("Chunk limit is too small.")

    paragraphs = [
        p.strip()
        for p in re.split(r"\n\s*\n", text)
        if p.strip()
    ]

    chunks = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > limit:
            if current:
                chunks.append(current)
                current = ""

            words = paragraph.split()
            piece = ""

            for word in words:
                candidate = f"{piece} {word}".strip()

                if len(candidate) <= limit:
                    piece = candidate
                else:
                    if piece:
                        chunks.append(piece)
                    piece = word

                    # An individual token longer than the limit is rare.
                    # Preserve it by splitting that token if necessary.
                    if len(piece) > limit:
                        for start in range(0, len(piece), limit):
                            chunks.append(piece[start:start + limit])
                        piece = ""

            if piece:
                current = piece

            continue

        candidate = (
            f"{current}\n\n{paragraph}" if current else paragraph
        )

        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)

    assert all(len(chunk) <= limit for chunk in chunks)
    return chunks


# ---------------- PREPARE FILES ----------------

def prepare(force=False):
    html_file = download_book(force=force)
    chapters = extract_chapters(html_file)

    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    manifest_rows = []
    total_chunks = 0

    for chapter in chapters:
        part = chapter["part"]
        number = chapter["chapter"]
        roman = chapter["roman"]

        chapter_id = f"Part_{part}_Chapter_{number:02d}"
        chapter_folder = TEXT_DIR / chapter_id

        # Put a spoken chapter heading at the beginning.
        narration = (
            f"Part {part_sort_key(part)}, Chapter {roman}.\n\n"
            + chapter["body"]
        )

        chunks = split_text(narration)

        for chunk_number, chunk in enumerate(chunks, start=1):
            filename = (
                f"{chapter_id}_chunk_{chunk_number:03d}.txt"
            )
            text_path = chapter_folder / filename

            safe_write(text_path, chunk + "\n")

            audio_filename = filename.removesuffix(".txt") + ".mp3"
            audio_path = AUDIO_DIR / audio_filename

            manifest_rows.append({
                "part": part,
                "chapter": number,
                "chapter_roman": roman,
                "chapter_title": chapter["title"],
                "chunk": chunk_number,
                "total_chunks": len(chunks),
                "characters": len(chunk),
                "text_file": str(text_path.relative_to(ROOT)),
                "audio_file": str(audio_path.relative_to(ROOT)),
                "status": (
                    "DONE" if audio_path.exists() else "PENDING"
                )
            })

            total_chunks += 1

    fields = [
        "part", "chapter", "chapter_roman", "chapter_title",
        "chunk", "total_chunks", "characters",
        "text_file", "audio_file", "status"
    ]

    with MANIFEST.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(manifest_rows)

    with QUEUE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(manifest_rows)

    update_next_chunk(manifest_rows)

    print("\nPREPARATION COMPLETE")
    print("--------------------")
    print(f"Chapters extracted: {len(chapters)}")
    print(f"Text chunks created: {total_chunks}")
    print(f"Character limit: {CHUNK_LIMIT}")
    print(f"Text directory: {TEXT_DIR}")
    print(f"Audio drop folder: {AUDIO_DIR}")
    print(f"Manifest: {MANIFEST}")
    print(f"Generation queue: {QUEUE}")
    print(f"Next chunk: {NEXT_CHUNK}")

    print("\nReview the extracted chapter count and sample text")
    print("before starting the full narration.")


# ---------------- PROGRESS TRACKING ----------------

def load_manifest():
    if not MANIFEST.exists():
        raise RuntimeError("Run prepare first.")

    with MANIFEST.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_manifest(rows):
    fields = [
        "part", "chapter", "chapter_roman", "chapter_title",
        "chunk", "total_chunks", "characters",
        "text_file", "audio_file", "status"
    ]

    with MANIFEST.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    with QUEUE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def update_next_chunk(rows):
    for row in rows:
        audio_path = ROOT / row["audio_file"]

        if not audio_path.exists():
            text_path = ROOT / row["text_file"]
            safe_write(
                NEXT_CHUNK,
                "Generate this chunk next in WevoLabs:\n\n"
                f"Text: {text_path}\n"
                f"Save MP3 as: {audio_path}\n"
                f"Chapter: {row['chapter_title']}\n"
                f"Chunk: {row['chunk']} of {row['total_chunks']}\n"
                f"Characters: {row['characters']}\n"
            )
            return

    safe_write(
        NEXT_CHUNK,
        "All expected chunk MP3 files are present.\n"
        "Check the files and listen for errors before merging.\n"
    )


def sync():
    rows = load_manifest()

    for row in rows:
        audio_path = ROOT / row["audio_file"]
        row["status"] = "DONE" if audio_path.is_file() else "PENDING"

    save_manifest(rows)
    update_next_chunk(rows)

    completed = sum(r["status"] == "DONE" for r in rows)
    total = len(rows)
    percent = 100 * completed / total if total else 0

    print(f"Audio chunks complete: {completed}/{total} ({percent:.1f}%)")
    print(f"Next chunk instructions: {NEXT_CHUNK}")


def show_next():
    sync()
    print("\n" + NEXT_CHUNK.read_text(encoding="utf-8"))


# ---------------- COMMAND LINE ----------------

def main():
    parser = argparse.ArgumentParser(
        description="Prepare The Idiot for WevoLabs narration."
    )

    parser.add_argument(
        "command",
        choices=["prepare", "next", "sync"],
        help="prepare text, show next chunk, or sync audio progress"
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="download the source HTML again"
    )

    args = parser.parse_args()

    try:
        if args.command == "prepare":
            prepare(force=args.force)
        elif args.command == "next":
            show_next()
        elif args.command == "sync":
            sync()
    except (requests.RequestException, OSError, RuntimeError) as error:
        sys.exit(f"ERROR: {error}")


if __name__ == "__main__":
    main()
