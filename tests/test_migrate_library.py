import tempfile
import unittest
from pathlib import Path

from backend.db import Database
from scripts.migrate_local_library import migrate


class MigrateLocalLibraryTests(unittest.TestCase):
    def test_migrates_existing_audio_without_copying_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "destination"
            source_db = Database(source / "data" / "radio.db")
            source_db.save_settings({"station_prompt": "source-only-style"})
            audio = source / "downloads" / "queue" / "Artist - Track.mp3"
            audio.parent.mkdir(parents=True, exist_ok=True)
            audio.write_bytes(b"local-audio")
            track = source_db.add_local_track(
                "Artist", "Track", "downloads/queue/Artist - Track.mp3"
            )
            source_db.update_track(
                track["id"], status="ready", duration_ms=180_000,
                match_score=0.92, library_source="ai", youtube_id="source-id",
            )

            result = migrate(source, destination)

            destination_db = Database(destination / "data" / "radio.db")
            migrated = next(
                track for track in destination_db.tracks()
                if track["artist"] == "Artist" and track["title"] == "Track"
            )
            self.assertEqual(result["copied"], 1)
            self.assertEqual(result["queue_size"], 1)
            self.assertEqual(migrated["library_source"], "ai")
            self.assertEqual(migrated["youtube_id"], "source-id")
            self.assertTrue((destination / migrated["local_path"]).is_file())
            self.assertNotEqual(
                destination_db.settings()["station_prompt"], "source-only-style"
            )


if __name__ == "__main__":
    unittest.main()
