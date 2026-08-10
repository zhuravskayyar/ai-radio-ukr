"""Import a track list from TXT, JSON array, or pasted console text.

Accepted entries:
    1. Artist - Title
    Artist — Title

JSON arrays are accepted too:
    ["Artist - Title", "Other Artist - Other Title"]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from backend.api import RadioAPI
from backend.parser import parse_chart


def decode_input(raw: str):
    raw = raw.strip().lstrip("\ufeff")
    if not raw:
        return ""
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return "\n".join(str(item) for item in value)
    except json.JSONDecodeError:
        pass
    return raw


def paste_text():
    print("Вставте список. Завершіть окремим рядком END:")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Import tracks into LUMEN Radio")
    parser.add_argument("file", nargs="?", help="UTF-8 TXT or JSON file")
    parser.add_argument("--paste", action="store_true", help="Paste tracks into the console")
    parser.add_argument("--replace", action="store_true", help="Replace the library instead of merging")
    args = parser.parse_args()

    if args.paste:
        raw = paste_text()
    elif args.file:
        raw = Path(args.file).read_text(encoding="utf-8-sig")
    else:
        parser.error("Specify a file or use --paste")

    tracks = parse_chart(decode_input(raw))
    if not tracks:
        raise SystemExit("Не знайдено треків. Формат: 1. Artist - Title")

    api = RadioAPI(ROOT)
    if args.replace:
        api.db.replace_tracks(tracks)
        action = "замінено"
    else:
        api.db.merge_tracks(tracks)
        action = "додано/оновлено"

    print(f"Готово: {action} {len(tracks)} треків. У бібліотеці: {len(api.db.tracks())}.")


if __name__ == "__main__":
    main()
