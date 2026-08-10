"""Batch-import source-backed Music Story cards from a UTF-8 JSON file."""
import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from backend.api import RadioAPI


def import_payload(api, payload):
    result = api.import_music_stories(payload)
    if result.get("error"):
        raise ValueError(result["error"])
    return result.get("imported", []), result.get("errors", [])


def main():
    parser = argparse.ArgumentParser(
        description="Import verified Music Story cards into LUMEN Radio"
    )
    parser.add_argument("file", help="UTF-8 JSON file with track story cards")
    args = parser.parse_args()
    payload = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
    imported, errors = import_payload(RadioAPI(ROOT), payload)
    print(f"Імпортовано story cards: {len(imported)}")
    for error in errors:
        print("ПОМИЛКА:", error)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
