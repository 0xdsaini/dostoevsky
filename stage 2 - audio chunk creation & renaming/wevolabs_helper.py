
#!/usr/bin/env python3

import csv
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.csv"
AUDIO_DIR = ROOT / "audio_in"
WEVO_URL = "https://wevolabs.com/"


def read_manifest():
    if not MANIFEST.exists():
        sys.exit("manifest.csv not found. Run your preparation script first.")

    with MANIFEST.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def sync_status(rows):
    for row in rows:
        audio = ROOT / row["audio_file"]
        row["status"] = "DONE" if audio.is_file() else "PENDING"

    fields = list(rows[0].keys()) if rows else []

    with MANIFEST.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def next_chunk():
    rows = read_manifest()

    if not rows:
        sys.exit("The manifest contains no chunks.")

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    sync_status(rows)

    pending = next(
        (r for r in rows if r["status"] != "DONE"),
        None
    )

    if pending is None:
        print("All chunk MP3s are present. Ready for your manual merge.")
        return

    text_path = ROOT / pending["text_file"]
    audio_path = ROOT / pending["audio_file"]

    if not text_path.is_file():
        sys.exit(f"Missing text file: {text_path}")

    text = text_path.read_text(encoding="utf-8").strip()

    # Copy the chunk to the macOS clipboard.
    subprocess.run(
        ["pbcopy"],
        input=text,
        text=True,
        check=True
    )

    print("\nNEXT WEVO LABS GENERATION")
    print("=" * 32)
    print(f"Chapter: {pending['chapter_title']}")
    print(
        f"Chunk: {pending['chunk']} "
        f"of {pending['total_chunks']}"
    )
    print(f"Characters: {pending['characters']}")
    print(f"\nExpected MP3 filename:\n{audio_path.name}")
    print(f"\nSave location:\n{audio_path}")
    print("\nText copied to clipboard.")
    print("In WevoLabs, paste, generate, and download.")
    print("Save the downloaded MP3 at the exact path above.")

    webbrowser.open(WEVO_URL)


def progress():
    rows = read_manifest()
    sync_status(rows)

    done = sum(r["status"] == "DONE" for r in rows)
    total = len(rows)

    print(f"Completed: {done}/{total}")

    if total:
        print(f"Progress: {done / total * 100:.1f}%")

    remaining = total - done
    print(f"Remaining: {remaining} chunks")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("next", "progress"):
        sys.exit(
            "Usage:\n"
            "  python wevolabs_helper.py next\n"
            "  python wevolabs_helper.py progress"
        )

    if sys.argv[1] == "next":
        next_chunk()
    else:
        progress()
