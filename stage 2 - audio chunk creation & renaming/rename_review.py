
#!/usr/bin/env python3
"""Interactive local MP3 review and safe renaming tool for macOS."""

import csv
import curses
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "verification" / "audio_verification.csv"
MANIFEST = ROOT / "manifest.csv"
AUDIO_DIR = ROOT / "audio_in"

FIELDS = [
    "audio_file", "matched_chunk", "expected_audio_filename",
    "score", "second_score", "margin", "sequence_score",
    "word_coverage", "status", "transcript"
]


def read_csv(path):
    if not path.exists():
        raise SystemExit(f"Missing file: {path}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields=None):
    if fields is None:
        fields = list(rows[0].keys()) if rows else FIELDS
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def absolute_audio(row):
    return ROOT / row["audio_file"]


def expected_text_path(row):
    # The verification report stores the matched manifest text path.
    value = row.get("matched_chunk", "")
    if not value:
        return None

    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def stop_audio(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    return None


def wrap_lines(text, width):
    result = []
    for paragraph in text.splitlines():
        if not paragraph:
            result.append("")
        else:
            result.extend(
                textwrap.wrap(
                    paragraph,
                    width=max(10, width),
                    replace_whitespace=True,
                    break_long_words=True,
                    break_on_hyphens=False
                ) or [""]
            )
    return result


def save_report(rows):
    write_csv(REPORT, rows, FIELDS)


def rename_file(row, target_name):
    source = absolute_audio(row)
    target = AUDIO_DIR / Path(target_name).name

    if not source.is_file():
        return False, f"Source file not found: {source.name}"

    if target.suffix.lower() != ".mp3":
        return False, "Target must have an .mp3 extension."

    if source.resolve() == target.resolve():
        return True, "Filename is already correct."

    if target.exists():
        return False, f"Target already exists: {target.name}"

    source.rename(target)

    # Keep the verification report in sync with the rename.
    row["audio_file"] = str(target.relative_to(ROOT))
    row["expected_audio_filename"] = target.name
    row["status"] = "RENAMED_REVIEWED"

    return True, f"Renamed to {target.name}"


def select_chunk(stdscr, chunks):
    """Search the manifest and let the user select a target chunk."""
    curses.echo()
    curses.curs_set(1)

    height, width = stdscr.getmaxyx()
    stdscr.clear()
    stdscr.addstr(0, 0, "Search chapter, chunk filename, or part:")
    stdscr.refresh()

    query = stdscr.getstr(1, 0, max(1, width - 1)).decode(
        "utf-8", errors="replace"
    ).strip().lower()

    curses.noecho()
    curses.curs_set(0)

    if not query:
        return None

    matches = [
        c for c in chunks
        if query in (
            c.get("chapter_title", "") + " "
            + c.get("text_file", "") + " "
            + c.get("audio_file", "")
        ).lower()
    ]

    if not matches:
        stdscr.clear()
        stdscr.addstr(0, 0, "No matching chunks. Press any key.")
        stdscr.getch()
        return None

    offset = 0

    while True:
        stdscr.clear()
        stdscr.addstr(
            0, 0,
            f"Matches: {len(matches)} | Enter number | Esc cancels"
        )

        visible = matches[:max(1, height - 3)]
        for i, chunk in enumerate(visible, start=1):
            line = (
                f"{i:2}. {chunk.get('chapter_title', '')} | "
                f"{chunk.get('audio_file', '')}"
            )
            stdscr.addnstr(i, 0, line, max(1, width - 1))

        stdscr.refresh()
        key = stdscr.getch()

        if key == 27:
            return None

        if key in (10, 13):
            curses.echo()
            stdscr.addstr(height - 1, 0, "Choose number: ")
            answer = stdscr.getstr(
                height - 1, 15, 8
            ).decode("utf-8", errors="replace").strip()
            curses.noecho()

            if answer.isdigit():
                number = int(answer)
                if 1 <= number <= len(visible):
                    return visible[number - 1]


def confirm_rename(stdscr, row, target_name):
    """Return 'yes', 'all', or 'no' for a rename confirmation."""
    height, width = stdscr.getmaxyx()
    stdscr.clear()

    source = absolute_audio(row)
    stdscr.addstr(0, 0, "CONFIRM RENAME")
    stdscr.addnstr(2, 0, f"From: {source.name}", width - 1)
    stdscr.addnstr(3, 0, f"To:   {target_name}", width - 1)
    stdscr.addnstr(5, 0, "Y = rename this | A = Yes to All | any other key = cancel", width - 1)
    stdscr.refresh()

    key = stdscr.getch()
    if key in (ord("y"), ord("Y")):
        return "yes"
    if key in (ord("a"), ord("A")):
        return "all"
    return "no"


def rename_all_remaining(rows, start_index, stdscr):
    """Apply suggested filenames to remaining rows without overwriting files."""
    renamed = 0
    skipped = 0
    failures = 0

    for candidate in rows[start_index:]:
        target_name = candidate.get("expected_audio_filename", "").strip()
        if not target_name:
            skipped += 1
            continue

        ok, result = rename_file(candidate, target_name)
        if ok:
            renamed += 1
        elif result.startswith("Target already exists:"):
            skipped += 1
        else:
            failures += 1

    save_report(rows)
    height, width = stdscr.getmaxyx()
    stdscr.clear()
    stdscr.addnstr(0, 0, "YES TO ALL COMPLETE", width - 1)
    stdscr.addnstr(2, 0, f"Renamed/already correct: {renamed}", width - 1)
    stdscr.addnstr(3, 0, f"Skipped (missing name or target exists): {skipped}", width - 1)
    stdscr.addnstr(4, 0, f"Other failures: {failures}", width - 1)
    stdscr.addnstr(6, 0, "Press any key to return.", width - 1)
    stdscr.refresh()
    stdscr.getch()


def app(stdscr):
    if not shutil.which("afplay"):
        raise SystemExit("macOS afplay was not found.")

    rows = read_csv(REPORT)
    chunks = read_csv(MANIFEST)

    # Only review existing MP3s represented in the verification report.
    rows = [
        row for row in rows
        if absolute_audio(row).is_file()
    ]

    if not rows:
        raise SystemExit("No reported MP3 files were found.")

    index = 0
    scroll = 0
    player = None
    message = "Ready. Press P to play the selected MP3."
    yes_to_all = False

    curses.curs_set(0)
    stdscr.keypad(True)

    while True:
        row = rows[index]
        source = absolute_audio(row)
        target_name = row.get("expected_audio_filename", "")
        text_path = expected_text_path(row)

        expected_text = ""
        if text_path and text_path.is_file():
            expected_text = text_path.read_text(
                encoding="utf-8", errors="replace"
            )

        height, width = stdscr.getmaxyx()
        stdscr.erase()

        def put(y, x, value, attr=0):
            if 0 <= y < height and x < width:
                stdscr.addnstr(y, x, str(value), width - x - 1, attr)

        put(
            0, 0,
            f" MP3 REVIEW  [{index + 1}/{len(rows)}] "
            "| P Play/stop | N/P Next/prev | R Rename | M Match | Q Quit",
            curses.A_REVERSE
        )
        put(1, 0, f"Current:  {source.name}")
        put(2, 0, f"Suggested: {target_name or '(none)'}")
        put(3, 0, f"Status: {row.get('status', '')} | "
                   f"Score: {row.get('score', '')}")
        put(4, 0, f"Location: {source}")

        put(6, 0, "EXPECTED TEXT", curses.A_BOLD)
        put(7, 0, f"File: {text_path or '(no matched text)'}")

        body_start = 9
        body_height = max(1, height - body_start - 5)
        lines = wrap_lines(expected_text, width - 2)

        scroll = max(0, min(scroll, max(0, len(lines) - body_height)))

        for line_num, line in enumerate(
            lines[scroll:scroll + body_height]
        ):
            put(body_start + line_num, 1, line)

        footer = height - 5
        put(footer, 0, "RECOGNIZED AUDIO (first 10 seconds)", curses.A_BOLD)

        transcript = row.get("transcript", "") or "(No transcript available)"
        for i, line in enumerate(wrap_lines(transcript, width - 2)[:2]):
            put(footer + 1 + i, 1, line)

        put(height - 2, 0, message)
        put(
            height - 1, 0,
            "Arrows scroll text | PgUp/PgDn scroll | "
            "R applies suggested name | M choose another chunk"
        )
        stdscr.refresh()

        key = stdscr.getch()

        if key in (ord("q"), ord("Q")):
            player = stop_audio(player)
            save_report(rows)
            break

        elif key in (curses.KEY_DOWN, curses.KEY_NPAGE):
            scroll += body_height if key == curses.KEY_NPAGE else 1

        elif key in (curses.KEY_UP, curses.KEY_PPAGE):
            scroll -= body_height if key == curses.KEY_PPAGE else 1

        elif key in (ord("n"), ord("N"), curses.KEY_RIGHT):
            player = stop_audio(player)
            index = (index + 1) % len(rows)
            scroll = 0
            message = "Next file."

        elif key in (ord("b"), ord("B"), curses.KEY_LEFT):
            player = stop_audio(player)
            index = (index - 1) % len(rows)
            scroll = 0
            message = "Previous file."

        elif key in (ord("p"), ord("P")):
            if player and player.poll() is None:
                player = stop_audio(player)
                message = "Playback stopped."
            else:
                player = stop_audio(player)
                player = subprocess.Popen(
                    ["afplay", str(source)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                message = "Playing audio..."

        elif key in (ord("r"), ord("R")):
            if not target_name:
                message = "No suggested filename. Use M to select a chunk."
                continue

            if yes_to_all:
                ok, message = rename_file(row, target_name)
                save_report(rows)
            else:
                choice = confirm_rename(stdscr, row, target_name)
                if choice == "yes":
                    ok, message = rename_file(row, target_name)
                    save_report(rows)
                elif choice == "all":
                    yes_to_all = True
                    rename_all_remaining(rows, index, stdscr)
                    message = "Yes to All finished. Check the summary screen."
                else:
                    message = "Rename cancelled."

        elif key in (ord("m"), ord("M")):
            selected = select_chunk(stdscr, chunks)

            if selected:
                proposed = Path(selected["audio_file"]).name

                if yes_to_all:
                    ok, message = rename_file(row, proposed)
                    if ok:
                        row["matched_chunk"] = selected["text_file"]
                    save_report(rows)
                else:
                    choice = confirm_rename(stdscr, row, proposed)
                    if choice == "yes":
                        ok, message = rename_file(row, proposed)
                        if ok:
                            row["matched_chunk"] = selected["text_file"]
                        save_report(rows)
                    elif choice == "all":
                        # The current manual match is applied, then remaining rows
                        # use their own suggested filenames from the verification report.
                        ok, message = rename_file(row, proposed)
                        if ok:
                            row["matched_chunk"] = selected["text_file"]
                        save_report(rows)
                        yes_to_all = True
                        rename_all_remaining(rows, index + 1, stdscr)
                        message = "Yes to All finished. Check the summary screen."
                    else:
                        message = "Rename cancelled."

        else:
            message = "Use P to play, R to rename, M to choose a chunk."

    player = stop_audio(player)


if __name__ == "__main__":
    try:
        curses.wrapper(app)
    except KeyboardInterrupt:
        print("\nExited.")
