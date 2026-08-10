import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import Qwen_python_20260804_4sskbslqs as lumen_downloader
from backend.api import RadioAPI


class LumenDownloaderIntegrationTests(unittest.TestCase):
    def test_downloader_uses_audio_only_without_requiring_ffmpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                output=directory,
                limit=1,
                number_playlist=False,
                cookies=None,
                video=False,
                video_format="mp4",
                convert=True,
                audio_format="mp3",
                quality="0",
                retries=5,
                search=True,
                music_search=True,
                candidates=5,
            )
            with patch.object(lumen_downloader, "find_ffmpeg_location", return_value=None):
                options = lumen_downloader.build_options(args)

            self.assertEqual(options["format"], "bestaudio/best")
            self.assertNotIn("merge_output_format", options)
            self.assertNotIn("postprocessors", options)

    def test_audio_download_does_not_depend_on_youtube_thumbnail(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                output=directory,
                limit=1,
                number_playlist=False,
                cookies=None,
                video=False,
                video_format="mp4",
                convert=True,
                audio_format="mp3",
                quality="0",
                retries=5,
                search=True,
                music_search=True,
                candidates=5,
            )
            with patch.object(
                lumen_downloader, "find_ffmpeg_location", return_value=directory,
            ):
                options = lumen_downloader.build_options(args)

            processors = [item["key"] for item in options["postprocessors"]]
            self.assertIn("FFmpegExtractAudio", processors)
            self.assertIn("FFmpegMetadata", processors)
            self.assertNotIn("EmbedThumbnail", processors)
            self.assertNotIn("writethumbnail", options)

    def test_build_options_includes_search_fallback_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                output=directory,
                limit=1,
                number_playlist=False,
                cookies=None,
                video=False,
                video_format="mp4",
                convert=False,
                audio_format="mp3",
                quality="0",
                retries=3,
                search=True,
                music_search=True,
                candidates=5,
            )
            options = lumen_downloader.build_options(args)

        self.assertEqual(options["default_search"], "ytsearch5")
        self.assertEqual(options["playlist_items"], "1-5")
        self.assertTrue(options["geo_bypass"])
        self.assertTrue(options["allow_unplayable_formats"])
        self.assertTrue(options["nocheckcertificate"])
        self.assertTrue(options["no_warnings"])

    def test_radio_resolve_downloads_and_saves_a_local_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = RadioAPI(root)
            track = api.db.add_local_track("Linkin Park", "Numb", "")
            audio_path = root / "downloads" / "Numb [official].webm"
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(b"local-audio")
            downloaded = {
                "path": audio_path,
                "info": {
                    "id": "official",
                    "title": "Numb (Official Music Video)",
                    "artist": "Linkin Park",
                    "uploader": "Linkin Park",
                    "duration": 187,
                    "webpage_url": "https://example.test/official",
                },
            }

            with patch.object(
                api, "_download_audio_with_lumen", return_value=downloaded,
            ) as download:
                result = api.resolve_track(track["id"])

            self.assertTrue(result["ok"])
            self.assertEqual(result["local_path"], "downloads/Numb [official].webm")
            self.assertEqual(result["track"]["local_path"], result["local_path"])
            self.assertEqual(result["track"]["duration_ms"], 187_000)
            self.assertTrue(download.call_args.kwargs["search"])
            self.assertTrue(download.call_args.kwargs["music_search"])

    def test_ai_recommendation_goes_directly_to_lumen_downloader_search(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = RadioAPI(root)
            api.db.save_settings({
                "dynamic_discovery_enabled": "1",
                "licensed_sources_confirmed": "1",
            })
            audio_path = root / "downloads" / "queue" / "Numb [official].webm"
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(b"local-audio")
            info = {
                "id": "official",
                "title": "Linkin Park - Numb (Official Music Video)",
                "artist": "Linkin Park",
                "uploader": "Linkin Park",
                "duration": 187,
                "webpage_url": "https://example.test/official",
            }

            def fake_download(item, output_dir, **kwargs):
                self.assertEqual(item, "Linkin Park - Numb")
                self.assertTrue(kwargs["search"])
                self.assertTrue(kwargs["music_search"])
                self.assertTrue(kwargs["validator"](info))
                return {"path": audio_path, "info": info}

            plan = {
                "tracks": [{
                    "artist": "Linkin Park", "title": "Numb", "reason": "alt rock",
                }],
                "target_mood": ["melancholic"],
                "avoid": [],
            }
            with patch.object(api, "_queue_search_plan", return_value=plan), patch.object(
                api, "_download_audio_with_lumen", side_effect=fake_download,
            ):
                track = api._discover_queue_track([])

            self.assertEqual(track["artist"], "Linkin Park")
            self.assertEqual(track["title"], "Numb")
            self.assertEqual(track["library_source"], "ai")
            self.assertEqual(track["local_path"], "downloads/queue/Numb [official].webm")
            self.assertGreaterEqual(track["match_score"], 0.75)

    def test_discovery_uses_similar_tracks_when_primary_recommendation_is_fake(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = RadioAPI(root)
            api.db.save_settings({
                "dynamic_discovery_enabled": "1",
                "licensed_sources_confirmed": "1",
            })
            audio_path = root / "downloads" / "queue" / "Numb [official].webm"
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(b"local-audio")
            info = {
                "id": "official",
                "title": "Linkin Park - Numb (Official Music Video)",
                "artist": "Linkin Park",
                "uploader": "Linkin Park",
                "duration": 187,
                "webpage_url": "https://example.test/official",
            }
            plan = {
                "tracks": [{"artist": "Fake Artist", "title": "Fake Song"}],
                "similar_tracks": [{"artist": "Linkin Park", "title": "Numb"}],
                "backup_tracks": [],
                "target_mood": ["alt rock"],
                "avoid": [],
            }
            calls = []

            def fake_download(item, output_dir, **kwargs):
                calls.append(item)
                if len(calls) == 1:
                    raise RuntimeError("no exact result")
                self.assertTrue(kwargs["validator"](info))
                return {"path": audio_path, "info": info}

            with patch.object(api, "_queue_search_plan", return_value=plan), patch.object(
                api, "_download_audio_with_lumen", side_effect=fake_download,
            ):
                track = api._discover_queue_track([])

            self.assertEqual(calls, ["Fake Artist - Fake Song", "Linkin Park - Numb"])
            self.assertEqual((track["artist"], track["title"]), ("Linkin Park", "Numb"))

    def test_discovery_reuses_one_ai_plan_for_multiple_downloaded_tracks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = RadioAPI(root)
            api.db.save_settings({
                "dynamic_discovery_enabled": "1",
                "licensed_sources_confirmed": "1",
            })
            plan = {
                "tracks": [
                    {"artist": "Linkin Park", "title": "Numb"},
                    {"artist": "The Hardkiss", "title": "Журавлі"},
                ],
                "similar_tracks": [],
                "backup_tracks": [],
                "target_mood": ["alt rock"],
                "avoid": [],
                "provider": "test-dj",
            }
            metadata = {
                "Linkin Park - Numb": {
                    "id": "numb-id", "title": "Linkin Park - Numb (Official Video)",
                    "artist": "Linkin Park", "uploader": "Linkin Park",
                    "duration": 187,
                },
                "The Hardkiss - Журавлі": {
                    "id": "zhuravli-id", "title": "The Hardkiss - Журавлі",
                    "artist": "The Hardkiss", "uploader": "The Hardkiss",
                    "duration": 226,
                },
            }

            def fake_download(item, output_dir, **kwargs):
                info = {**metadata[item], "webpage_url": f"https://example.test/{metadata[item]['id']}"}
                self.assertTrue(kwargs["validator"](info))
                path = Path(output_dir) / f"{metadata[item]['id']}.mp3"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"local-audio")
                return {"path": path, "info": info}

            with patch.object(api, "_queue_search_plan", return_value=plan) as search, patch.object(
                api, "_download_audio_with_lumen", side_effect=fake_download,
            ):
                first = api._discover_queue_track([])
                second = api._discover_queue_track([first["id"]])

            self.assertEqual(search.call_count, 1)
            self.assertEqual((first["artist"], first["title"]), ("Linkin Park", "Numb"))
            self.assertEqual((second["artist"], second["title"]), ("The Hardkiss", "Журавлі"))

    def test_web_player_has_no_youtube_stream_fallback(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "ui" / "app.js").read_text(encoding="utf-8")
        index = (root / "ui" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("loadVideoById", app)
        self.assertNotIn("cueVideoById", app)
        self.assertNotIn("youtube.com/iframe_api", index)


if __name__ == "__main__":
    unittest.main()
