"""Copy an existing Vector Radio library into another local installation.

Only audio files already present on the same computer are copied. API keys,
settings, listening history and generated presenter copy are not migrated.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.db import Database  # noqa: E402
from backend.radio_queue import RadioQueueManager  # noqa: E402


MIGRATED_FIELDS = {
    "youtube_id",
    "youtube_title",
    "status",
    "artist_speech",
    "title_speech",
    "match_score",
    "duration_ms",
    "bpm",
    "energy",
    "mood",
    "genre",
    "intro_end_ms",
    "vocal_start_ms",
    "outro_start_ms",
    "hard_end_ms",
    "end_type",
    "artist_speech_confidence",
    "title_speech_confidence",
    "pronunciation_review",
    "artist_language",
    "title_language",
    "pronunciation_source",
}


def existing_ai_tracks(source_root: Path) -> list[dict]:
    database = source_root / "data" / "radio.db"
    if not database.is_file():
        raise FileNotFoundError(f"Source database not found: {database}")
    with closing(sqlite3.connect(database)) as db:
        db.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM tracks "
                "WHERE library_source = 'ai' "
                "AND length(coalesce(local_path, '')) > 0 "
                "ORDER BY rank, id"
            )
        ]


def migrate(source_root: Path, destination_root: Path) -> dict:
    source_root = source_root.resolve()
    destination_root = destination_root.resolve()
    if source_root == destination_root:
        raise ValueError("Source and destination must be different")

    destination_db = Database(destination_root / "data" / "radio.db")
    destination_audio = destination_root / "downloads" / "migrated"
    destination_audio.mkdir(parents=True, exist_ok=True)
    copied = []
    skipped = []

    for source_track in existing_ai_tracks(source_root):
        relative_source = str(source_track.get("local_path") or "").strip()
        source_audio = (source_root / relative_source).resolve()
        if source_root not in source_audio.parents or not source_audio.is_file():
            skipped.append({
                "artist": source_track.get("artist", ""),
                "title": source_track.get("title", ""),
                "reason": "audio file is missing or outside the source project",
            })
            continue

        destination_audio_file = destination_audio / source_audio.name
        if (
            not destination_audio_file.is_file()
            or destination_audio_file.stat().st_size != source_audio.stat().st_size
        ):
            shutil.copy2(source_audio, destination_audio_file)
        relative_destination = destination_audio_file.relative_to(
            destination_root
        ).as_posix()
        track = destination_db.add_local_track(
            source_track["artist"], source_track["title"], relative_destination
        )
        updates = {
            key: source_track[key]
            for key in MIGRATED_FIELDS
            if key in source_track and source_track[key] is not None
        }
        updates.update({
            "local_path": relative_destination,
            "status": "ready",
            "match_score": max(0.75, float(source_track.get("match_score") or 0)),
            "library_source": "ai",
        })
        destination_db.update_track(track["id"], **updates)
        copied.append({
            "artist": source_track["artist"],
            "title": source_track["title"],
            "file": relative_destination,
        })

    queue = RadioQueueManager(destination_db, destination_root).bootstrap()
    return {
        "ok": True,
        "copied": len(copied),
        "skipped": len(skipped),
        "queue_size": queue["size"],
        "tracks": copied,
        "skipped_tracks": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            migrate(args.source, args.destination), ensure_ascii=False, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
