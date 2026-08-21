import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import Qwen_python_20260804_4sskbslqs as lumen_downloader
from backend.api import RadioAPI


class LumenDownloaderIntegrationTests(unittest.TestCase):
    def test_official_title_with_translation_suffix_is_accepted(self):
        candidate = {
            "id": "EGLoIaHwKfE",
            "url": "https://www.youtube.com/watch?v=EGLoIaHwKfE",
            "title": "SadSvit - Касета (Cassette 2021)",
            "artist": "SadSvit",
            "uploader": "SadSvit",
            "channel": "SadSvit",
            "duration": 145,
        }
        settings = {"queue_min_duration": 120, "queue_max_duration": 480}

        accepted = RadioAPI._queue_candidate_allowed(
            candidate, settings, [], set(),
            recommendation={"artist": "SadSvit", "title": "Касета"},
        )

        self.assertTrue(accepted)
        self.assertEqual(candidate["match_score"], 1.0)

    def test_one_word_title_does_not_accept_a_longer_song_name(self):
        candidate = {
            "id": "wrong-song",
            "url": "https://www.youtube.com/watch?v=wrong-song",
            "title": "The Soft Moon - Black Sabbath (Official Audio)",
            "artist": "The Soft Moon",
            "uploader": "The Soft Moon",
            "channel": "The Soft Moon",
            "duration": 240,
        }
        settings = {"queue_min_duration": 120, "queue_max_duration": 480}

        accepted = RadioAPI._queue_candidate_allowed(
            candidate, settings, [], set(),
            recommendation={"artist": "The Soft Moon", "title": "Black"},
        )

        self.assertFalse(accepted)

    def test_ai_playlist_is_built_before_any_web_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            plan = {
                "tracks": [{"artist": "Linkin Park", "title": "Numb"}],
                "similar_tracks": [],
                "backup_tracks": [],
            }
            with patch.object(
                api, "_queue_search_plan", return_value=plan,
            ) as queue_search:
                count = api._refill_discovery_plan_cache(
                    api.db.settings(), [],
                )

            self.assertEqual(count, 1)
            self.assertFalse(queue_search.call_args.kwargs["use_research_tools"])

    def test_metadata_fallback_still_gives_downloader_a_direct_url(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            direct_url = "https://www.youtube.com/watch?v=metadata1"
            fallback = {
                "ok": True,
                "results": [{
                    "id": "metadata1",
                    "url": direct_url,
                    "title": "Linkin Park - Numb",
                    "source": "youtube:yt-dlp",
                }],
                "attempts": [],
            }
            downloaded = {
                "path": Path(directory) / "Numb.mp3",
                "info": {"id": "metadata1"},
            }
            with patch.object(
                api.music_research, "search_youtube_on_google",
                return_value={"ok": False, "results": []},
            ), patch.object(
                api.music_research, "search_music", return_value=fallback,
            ), patch.object(
                api, "_download_audio_with_lumen", return_value=downloaded,
            ) as download:
                result, resolved = api._download_playlist_entry_from_youtube_url(
                    {"artist": "Linkin Park", "title": "Numb"},
                    Path(directory), validator=lambda _info: True,
                )

            self.assertEqual(result, downloaded)
            self.assertEqual(resolved["resolver"], "YouTube metadata")
            self.assertEqual(download.call_args.args[0], direct_url)
            self.assertFalse(download.call_args.kwargs["search"])
            self.assertFalse(download.call_args.kwargs["music_search"])

    def test_downloader_reports_safe_byte_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "Track [safe-id].webm"
            audio_path.write_bytes(b"audio")
            updates = []

            class FakeYoutubeDL:
                def __init__(self, options):
                    self.options = options

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def extract_info(self, target, download=True):
                    info = {
                        "id": "safe-id",
                        "title": "Track",
                        "_filename": str(audio_path),
                    }
                    hook = self.options["progress_hooks"][0]
                    hook({
                        "status": "downloading",
                        "downloaded_bytes": 50,
                        "total_bytes": 100,
                        "speed": 25,
                        "eta": 2,
                    })
                    hook({
                        "status": "finished",
                        "downloaded_bytes": 100,
                        "total_bytes": 100,
                        "info_dict": info,
                    })
                    return info

                def prepare_filename(self, info):
                    return info["_filename"]

            with patch.object(
                lumen_downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL,
            ), patch.object(
                lumen_downloader, "find_ffmpeg_location", return_value=None,
            ):
                result = lumen_downloader.download_audio_item(
                    "Artist - Track", directory, progress_callback=updates.append,
                )

            self.assertEqual(result["path"], audio_path.resolve())
            self.assertEqual([item["percent"] for item in updates], [50.0, 100.0])
            self.assertEqual(updates[0]["downloaded_bytes"], 50)
            self.assertNotIn("info_dict", updates[-1])

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
            with patch.object(
                lumen_downloader, "find_javascript_runtime",
                return_value=r"C:\Vector Radio\runtime\Scripts\deno.exe",
            ):
                options = lumen_downloader.build_options(args)

        self.assertEqual(options["default_search"], "ytsearch5")
        self.assertEqual(options["playlist_items"], "1-5")
        self.assertTrue(options["geo_bypass"])
        self.assertNotIn("age_limit", options)
        self.assertNotIn("cookiesfrombrowser", options)
        self.assertEqual(
            options["js_runtimes"],
            {"deno": {"path": r"C:\Vector Radio\runtime\Scripts\deno.exe"}},
        )
        self.assertNotIn("allow_unplayable_formats", options)
        self.assertNotIn("nocheckcertificate", options)

    def test_search_skips_unavailable_results_and_does_not_use_music_page(self):
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "Track [safe-id].webm"
            audio_path.write_bytes(b"audio")
            targets = []

            class FakeYoutubeDL:
                def __init__(self, options):
                    self.options = options

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def extract_info(self, target, download=True):
                    targets.append(target)
                    if self.options["ignoreerrors"] is not True:
                        raise AssertionError("search must skip inaccessible entries")
                    if len(targets) == 1:
                        raise RuntimeError("Sign in to confirm your age")
                    info = {
                        "id": "safe-id",
                        "title": "Artist - Track",
                        "_filename": str(audio_path),
                    }
                    return info

                def prepare_filename(self, info):
                    return info["_filename"]

            with patch.object(
                lumen_downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL,
            ), patch.object(
                lumen_downloader, "find_ffmpeg_location", return_value=None,
            ):
                result = lumen_downloader.download_audio_item(
                    "Artist - Track", directory, search=True, music_search=True,
                )

            self.assertEqual(result["path"], audio_path.resolve())
            self.assertEqual(targets[:2], [
                "Artist - Track",
                "Artist - Track official audio",
            ])
            self.assertFalse(any("music.youtube.com" in target for target in targets))

    def test_age_restricted_failure_is_reported_as_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            class FakeYoutubeDL:
                def __init__(self, options):
                    self.options = options

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def extract_info(self, target, download=True):
                    self.options["logger"].error(
                        "ERROR: Sign in to confirm your age",
                    )
                    return {"entries": []}

                def prepare_filename(self, info):
                    return ""

            with patch.object(
                lumen_downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL,
            ), patch.object(
                lumen_downloader, "find_ffmpeg_location", return_value=None,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "віково обмежений результат пропущено",
                ):
                    lumen_downloader.download_audio_item(
                        "Artist - Track", directory,
                        search=True, music_search=True,
                    )

    def test_age_restriction_retries_once_with_selected_browser_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "Track [adult-id].webm"
            option_history = []

            class FakeYoutubeDL:
                def __init__(self, options):
                    self.options = options
                    option_history.append(options.get("cookiesfrombrowser"))

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def extract_info(self, target, download=True):
                    if "cookiesfrombrowser" not in self.options:
                        self.options["logger"].error(
                            "ERROR: Sign in to confirm your age",
                        )
                        return {"entries": []}
                    audio_path.write_bytes(b"authorized-audio")
                    return {
                        "id": "adult-id",
                        "title": "Artist - Track",
                        "_filename": str(audio_path),
                    }

                def prepare_filename(self, info):
                    return info.get("_filename", "")

            with patch.object(
                lumen_downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL,
            ), patch.object(
                lumen_downloader, "find_ffmpeg_location", return_value=None,
            ):
                result = lumen_downloader.download_audio_item(
                    "Artist - Track",
                    directory,
                    search=True,
                    youtube_auth_browser="edge",
                    youtube_auth_profile="Profile 2",
                )

            self.assertEqual(result["path"], audio_path.resolve())
            self.assertEqual(option_history, [
                None,
                ("edge", "Profile 2", None, None),
            ])

    def test_youtube_auth_is_not_used_for_a_non_age_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            option_history = []

            class FakeYoutubeDL:
                def __init__(self, options):
                    self.options = options
                    option_history.append(options.get("cookiesfrombrowser"))

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def extract_info(self, target, download=True):
                    self.options["logger"].error(
                        "ERROR: This video is unavailable",
                    )
                    return {"entries": []}

                def prepare_filename(self, info):
                    return ""

            with patch.object(
                lumen_downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL,
            ), patch.object(
                lumen_downloader, "find_ffmpeg_location", return_value=None,
            ):
                with self.assertRaises(RuntimeError):
                    lumen_downloader.download_audio_item(
                        "Artist - Track",
                        directory,
                        search=True,
                        youtube_auth_browser="chrome",
                    )

            self.assertEqual(option_history, [None])

    def test_failed_youtube_auth_continues_with_next_search_target(self):
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "Track [next-id].webm"
            attempts = []

            class FakeYoutubeDL:
                def __init__(self, options):
                    self.options = options

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def extract_info(self, target, download=True):
                    auth = self.options.get("cookiesfrombrowser")
                    attempts.append((target, auth))
                    if target == "Artist - Track":
                        self.options["logger"].error(
                            "ERROR: Sign in to confirm your age",
                        )
                        return {"entries": []}
                    audio_path.write_bytes(b"next-candidate")
                    return {
                        "id": "next-id",
                        "title": "Artist - Track",
                        "_filename": str(audio_path),
                    }

                def prepare_filename(self, info):
                    return info.get("_filename", "")

            with patch.object(
                lumen_downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL,
            ), patch.object(
                lumen_downloader, "find_ffmpeg_location", return_value=None,
            ):
                result = lumen_downloader.download_audio_item(
                    "Artist - Track",
                    directory,
                    search=True,
                    music_search=True,
                    youtube_auth_browser="chrome",
                )

            self.assertEqual(result["path"], audio_path.resolve())
            self.assertEqual(attempts, [
                ("Artist - Track", None),
                ("Artist - Track", ("chrome",)),
                ("Artist - Track official audio", None),
            ])

    def test_radio_passes_youtube_auth_settings_to_lumen(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({
                "youtube_auth_browser": "firefox",
                "youtube_auth_profile": "radio-profile",
            })

            with patch.object(
                lumen_downloader,
                "download_audio_item",
                return_value={"path": Path(directory) / "audio.mp3", "info": {}},
            ) as download:
                api._download_audio_with_lumen("Artist - Track", directory)

            self.assertEqual(
                download.call_args.kwargs["youtube_auth_browser"], "firefox",
            )
            self.assertEqual(
                download.call_args.kwargs["youtube_auth_profile"], "radio-profile",
            )

    def test_unknown_youtube_auth_browser_is_saved_as_off(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))

            result = api.save_settings({
                "youtube_auth_browser": "unsupported-browser",
            })

            self.assertEqual(result["settings"]["youtube_auth_browser"], "off")

    def test_rejected_search_result_cannot_reuse_an_old_download(self):
        with tempfile.TemporaryDirectory() as directory:
            old_audio = Path(directory) / "Different Song [old-id].mp3"
            old_audio.write_bytes(b"old-audio")

            class FakeYoutubeDL:
                def __init__(self, options):
                    self.options = options

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def extract_info(self, target, download=True):
                    info = {
                        "id": "old-id",
                        "title": "Different Song",
                        "_filename": str(old_audio),
                    }
                    match_filter = self.options["match_filter"]
                    self.assert_rejected(match_filter(info, incomplete=False))
                    return info

                @staticmethod
                def assert_rejected(value):
                    if not value:
                        raise AssertionError("candidate should have been rejected")

                def prepare_filename(self, info):
                    return info["_filename"]

            with patch.object(
                lumen_downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL,
            ), patch.object(
                lumen_downloader, "find_ffmpeg_location", return_value=None,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "не пройшли перевірку",
                ):
                    lumen_downloader.download_audio_item(
                        "Artist - Track", directory,
                        search=True, music_search=True,
                        validator=lambda _info: False,
                    )

            self.assertEqual(old_audio.read_bytes(), b"old-audio")

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

            google_result = {
                "ok": True,
                "results": [{
                    "id": "official-url",
                    "url": "https://www.youtube.com/watch?v=official-url",
                    "title": "Linkin Park - Numb (Official Music Video)",
                    "source": "google:playwright",
                }],
            }
            with patch.object(
                api.music_research, "search_youtube_on_google",
                return_value=google_result,
            ), patch.object(
                api, "_download_audio_with_lumen", return_value=downloaded,
            ) as download:
                result = api.resolve_track(track["id"])

            self.assertTrue(result["ok"])
            self.assertEqual(result["local_path"], "downloads/Numb [official].webm")
            self.assertEqual(result["track"]["local_path"], result["local_path"])
            self.assertEqual(result["track"]["duration_ms"], 187_000)
            self.assertEqual(
                download.call_args.args[0],
                "https://www.youtube.com/watch?v=official-url",
            )
            self.assertFalse(download.call_args.kwargs["search"])
            self.assertFalse(download.call_args.kwargs["music_search"])
            self.assertEqual(result["resolver"], "Google")

    def test_ai_playlist_entry_is_resolved_by_google_before_downloader(self):
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
                self.assertEqual(
                    item, "https://www.youtube.com/watch?v=official-url",
                )
                self.assertFalse(kwargs["search"])
                self.assertFalse(kwargs["music_search"])
                self.assertTrue(kwargs["validator"](info))
                return {"path": audio_path, "info": info}

            plan = {
                "tracks": [{
                    "artist": "Linkin Park", "title": "Numb", "reason": "alt rock",
                }],
                "target_mood": ["melancholic"],
                "avoid": [],
            }
            google_result = {
                "ok": True,
                "results": [{
                    "id": "official-url",
                    "url": "https://www.youtube.com/watch?v=official-url",
                    "title": "Linkin Park - Numb (Official Music Video)",
                    "source": "google:playwright",
                }],
            }
            with patch.object(api, "_queue_search_plan", return_value=plan), patch.object(
                api.music_research, "search_youtube_on_google",
                return_value=google_result,
            ) as google_search, patch.object(
                api, "_download_audio_with_lumen", side_effect=fake_download,
            ):
                track = api._discover_queue_track([])

            self.assertEqual(track["artist"], "Linkin Park")
            self.assertEqual(track["title"], "Numb")
            self.assertEqual(track["library_source"], "ai")
            self.assertEqual(track["local_path"], "downloads/queue/Numb [official].webm")
            self.assertGreaterEqual(track["match_score"], 0.75)
            google_search.assert_called_once_with(
                "Linkin Park", "Numb", limit=5, allow_browser=True,
            )

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
            lookups = []
            downloads = []

            def fake_download(item, output_dir, **kwargs):
                downloads.append(item)
                self.assertTrue(kwargs["validator"](info))
                return {"path": audio_path, "info": info}

            def fake_google(artist, title, **_kwargs):
                lookups.append((artist, title))
                if artist == "Fake Artist":
                    return {"ok": False, "results": []}
                return {
                    "ok": True,
                    "results": [{
                        "id": "official-url",
                        "url": "https://www.youtube.com/watch?v=official-url",
                        "title": "Linkin Park - Numb (Official Music Video)",
                        "source": "google:playwright",
                    }],
                }

            with patch.object(api, "_queue_search_plan", return_value=plan), patch.object(
                api.music_research, "search_youtube_on_google",
                side_effect=fake_google,
            ), patch.object(
                api.music_research, "search_music",
                return_value={"ok": False, "results": [], "attempts": []},
            ), patch.object(
                api, "_download_audio_with_lumen", side_effect=fake_download,
            ):
                track = api._discover_queue_track([])

            self.assertEqual(lookups, [
                ("Fake Artist", "Fake Song"),
                ("Linkin Park", "Numb"),
            ])
            self.assertEqual(downloads, [
                "https://www.youtube.com/watch?v=official-url",
            ])
            self.assertEqual((track["artist"], track["title"]), ("Linkin Park", "Numb"))

    def test_discovery_never_substitutes_another_song_for_fake_ai_title(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = RadioAPI(root)
            api.db.save_settings({
                "dynamic_discovery_enabled": "1",
                "licensed_sources_confirmed": "1",
            })
            plan = {
                "tracks": [{"artist": "Linkin Park", "title": "Imaginary Song"}],
                "similar_tracks": [],
                "backup_tracks": [],
                "target_mood": ["alt rock"],
                "avoid": [],
            }
            with patch.object(api, "_queue_search_plan", return_value=plan), patch.object(
                api.music_research, "search_youtube_on_google",
                return_value={"ok": False, "results": []},
            ), patch.object(
                api.music_research, "search_music",
                return_value={"ok": False, "results": [], "attempts": []},
            ), patch.object(
                api, "_download_audio_with_lumen",
            ) as download:
                with self.assertRaises(RuntimeError):
                    api._discover_queue_track([])

            download.assert_not_called()

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
                "numb-id": {
                    "id": "numb-id", "title": "Linkin Park - Numb (Official Video)",
                    "artist": "Linkin Park", "uploader": "Linkin Park",
                    "duration": 187,
                },
                "zhuravli-id": {
                    "id": "zhuravli-id", "title": "The Hardkiss - Журавлі",
                    "artist": "The Hardkiss", "uploader": "The Hardkiss",
                    "duration": 226,
                },
            }

            def fake_download(item, output_dir, **kwargs):
                video_id = item.rsplit("=", 1)[-1]
                info = {**metadata[video_id], "webpage_url": item}
                self.assertFalse(kwargs["search"])
                self.assertFalse(kwargs["music_search"])
                self.assertTrue(kwargs["validator"](info))
                path = Path(output_dir) / f"{video_id}.mp3"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"local-audio")
                return {"path": path, "info": info}

            def fake_google(artist, title, **_kwargs):
                video_id = "numb-id" if artist == "Linkin Park" else "zhuravli-id"
                return {
                    "ok": True,
                    "results": [{
                        "id": video_id,
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "title": f"{artist} - {title}",
                        "source": "google:playwright",
                    }],
                }

            with patch.object(api, "_queue_search_plan", return_value=plan) as search, patch.object(
                api.music_research, "search_youtube_on_google",
                side_effect=fake_google,
            ), patch.object(
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
        self.assertIn('data-setting="youtube_auth_browser"', app)
        self.assertIn('data-setting="youtube_auth_profile"', app)
        self.assertIn("void applyReadyUpdate(true)", app)
        self.assertIn("window.pywebview.api.apply_update()", app)


if __name__ == "__main__":
    unittest.main()
