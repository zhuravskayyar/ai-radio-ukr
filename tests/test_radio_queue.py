import random
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api import RadioAPI
from backend.db import Database
from backend.radio_queue import RadioQueueManager


class RadioQueueTests(unittest.TestCase):
    def make_library(self, directory, count=14):
        root = Path(directory)
        db = Database(root / "radio.db")
        for number in range(count):
            relative = f"downloads/Artist {number} - Track {number}.mp3"
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test-audio")
            track = db.add_local_track(
                f"Artist {number}", f"Track {number}", relative,
            )
            db.update_track(
                track["id"], duration_ms=180_000 + number * 1000,
                energy=(number % 8) + 1,
                match_score=1,
                library_source="ai",
            )
        return db, root

    def test_bootstrap_builds_and_persists_ten_unique_tracks(self):
        with tempfile.TemporaryDirectory() as directory:
            db, root = self.make_library(directory)
            first = RadioQueueManager(db, root, random_source=random.Random(4))
            snapshot = first.bootstrap()

            self.assertEqual(snapshot["size"], 10)
            self.assertEqual(snapshot["target"], 10)
            self.assertEqual(snapshot["refill_threshold"], 7)
            self.assertEqual(snapshot["critical_threshold"], 2)
            first_ids = [track["id"] for track in snapshot["items"]]
            self.assertEqual(len(first_ids), len(set(first_ids)))

            restored = RadioQueueManager(db, root, random_source=random.Random(9))
            restored_ids = [track["id"] for track in restored.bootstrap()["items"]]
            self.assertEqual(restored_ids, first_ids)

    def test_bootstrap_removes_adjacent_same_artist_from_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = Database(root / "radio.db")
            ids = []
            for artist, title in [
                ("Same Artist", "First"),
                ("Same Artist", "Second"),
                ("Other Artist", "Third"),
                ("Same Artist", "Fourth"),
            ]:
                relative = f"downloads/{artist} - {title}.mp3"
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test-audio")
                track = db.add_local_track(artist, title, relative)
                db.update_track(
                    track["id"], duration_ms=180_000,
                    match_score=1, library_source="ai",
                )
                ids.append(track["id"])
            db.replace_radio_queue([
                {"track_id": ids[0], "source_query": "test", "added_at": "1"},
                {"track_id": ids[1], "source_query": "test", "added_at": "2"},
                {"track_id": ids[2], "source_query": "test", "added_at": "3"},
                {"track_id": ids[3], "source_query": "test", "added_at": "4"},
            ])

            snapshot = RadioQueueManager(db, root).bootstrap()

            artists = [track["artist"] for track in snapshot["items"]]
            self.assertNotIn(
                ("Same Artist", "Same Artist"),
                list(zip(artists, artists[1:])),
            )

    def test_queue_search_plan_excludes_previous_tracks(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"station_prompt": "dream pop and shoegaze"})
            providers = [{"name": "test-ai", "url": "https://test.invalid", "key": "test", "model": "x"}]
            ai_response = {
                "provider": "test-ai",
                "candidate": json.dumps({
                    "tracks": [
                        {"artist": "Cocteau Twins", "title": "Heaven or Las Vegas", "reason": "dream pop"},
                        {"artist": "My Bloody Valentine", "title": "Only Shallow", "reason": "shoegaze"},
                    ],
                    "targetMood": ["ethereal", "shoegaze"],
                    "avoid": ["live", "remix"],
                }, ensure_ascii=False),
                "error": "",
            }
            with patch.object(api, "_ai_providers", return_value=providers), patch(
                "backend.api._chat_completion", return_value=ai_response,
            ):
                plan = api._queue_search_plan(
                    api.db.settings(),
                    excluded_tracks=[{"artist": "Cocteau Twins", "title": "Heaven or Las Vegas"}],
                )

            self.assertEqual(len(plan["tracks"]), 1)
            self.assertEqual(plan["tracks"][0]["artist"], "My Bloody Valentine")
            self.assertEqual(plan["tracks"][0]["title"], "Only Shallow")
            self.assertEqual(plan["avoid"], ["live", "remix"])

    def test_queue_search_plan_uses_standard_token_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"station_prompt": "modern alternative rock"})
            providers = [{"name": "test-ai", "url": "https://test.invalid", "key": "test", "model": "x"}]
            ai_response = {
                "provider": "test-ai",
                "candidate": json.dumps({
                    "tracks": [
                        {"artist": "Linkin Park", "title": "Numb"},
                        {"artist": "The Cure", "title": "Lovesong"},
                    ],
                    "targetMood": ["melancholic"],
                    "avoid": [],
                }, ensure_ascii=False),
                "error": "",
            }
            with patch.object(api, "_ai_providers", return_value=providers), patch(
                "backend.api._chat_completion", return_value=ai_response,
            ) as completion:
                api._queue_search_plan(api.db.settings())

            self.assertEqual(completion.call_args.args[5], 1000)

    def test_queue_search_plan_prefers_higher_quality_provider_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"dj_ai_provider": "parallel"})
            providers = [
                {"name": "nvidia", "url": "https://nvidia.invalid", "key": "one", "model": "a"},
                {"name": "secondary", "url": "https://second.invalid", "key": "two", "model": "b"},
            ]

            def fake_completion(spec, *_args):
                tracks = (
                    [
                        {"artist": "Linkin Park", "title": "Numb", "reason": "alt rock"},
                        {"artist": "The Cure", "title": "Lovesong", "reason": "post punk"},
                    ]
                    if spec["name"] == "nvidia"
                    else [
                        {"artist": f"Artist {index}", "title": f"Track {index}", "reason": "fits"}
                        for index in range(1, 11)
                    ]
                )
                similar = [] if spec["name"] == "nvidia" else [
                    {"artist": f"Similar {index}", "title": f"Song {index}", "reason": "near"}
                    for index in range(1, 6)
                ]
                return {
                    "provider": spec["name"],
                    "candidate": json.dumps({
                        "tracks": tracks,
                        "similarTracks": similar,
                        "targetMood": ["dark", "atmospheric"],
                        "avoid": ["cover"],
                    }, ensure_ascii=False),
                    "error": "",
                }

            with patch.object(api, "_ai_providers", return_value=providers), \
                    patch("backend.api._chat_completion", side_effect=fake_completion) as mocked:
                plan = api._queue_search_plan({
                    "station_prompt": "dark atmospheric rock",
                    "dj_ai_provider": "parallel",
                })

            self.assertEqual(mocked.call_count, 2)
            self.assertEqual(plan["provider"], "secondary")
            self.assertEqual(len(plan["tracks"]), 10)
            self.assertGreater(plan["quality_score"], 100)
            diagnostics = {
                item["provider"]: item for item in plan["provider_diagnostics"]
            }
            self.assertGreater(diagnostics["secondary"]["score"], diagnostics["nvidia"]["score"])
            self.assertTrue(any(
                item["artist"] == "Linkin Park" and item["title"] == "Numb"
                for item in plan["backup_tracks"]
            ))

    def test_music_plan_rejects_same_title_assigned_to_two_artists(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            response = {
                "provider": "secondary",
                "candidate": json.dumps({
                    "tracks": [
                        {"artist": "First Artist", "title": "Shared Song"},
                        {"artist": "Wrong Artist", "title": "Shared Song"},
                        {"artist": "Third Artist", "title": "Different Song"},
                    ],
                    "similarTracks": [],
                }),
                "error": "",
            }
            provider = {"name": "secondary", "url": "x", "key": "x", "model": "deepseek/test"}

            with patch.object(api, "_ai_providers", return_value=[provider]), \
                    patch("backend.api._chat_completion", return_value=response):
                plan = api._queue_search_plan({"station_prompt": "alternative rock"})

            self.assertEqual(
                [(item["artist"], item["title"]) for item in plan["tracks"]],
                [("First Artist", "Shared Song"), ("Third Artist", "Different Song")],
            )
            self.assertTrue(any(
                item.get("reason") == "duplicate-title-other-artist"
                for item in plan["skipped"]
            ))

    def test_music_plan_rejects_reason_that_conflicts_with_no_pop_style(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            response = {
                "provider": "secondary",
                "candidate": json.dumps({
                    "tracks": [
                        {"artist": "Pop Artist", "title": "Bright Song", "reason": "поп-рок межа"},
                        {"artist": "Rock Artist", "title": "Dark Song", "reason": "альт рок"},
                    ],
                    "similarTracks": [],
                }, ensure_ascii=False),
                "error": "",
            }
            provider = {
                "name": "secondary", "url": "x", "key": "x", "model": "deepseek/test",
            }

            with patch.object(api, "_ai_providers", return_value=[provider]), \
                    patch("backend.api._chat_completion", return_value=response):
                plan = api._queue_search_plan({
                    "station_prompt": "сучасний alternative rock без попси й каверів",
                })

            self.assertEqual(
                [(item["artist"], item["title"]) for item in plan["tracks"]],
                [("Rock Artist", "Dark Song")],
            )
            self.assertTrue(any(
                item.get("reason") == "style-conflict"
                for item in plan["skipped"]
            ))

    def test_modern_ru_ua_alt_rock_plan_filters_legacy_and_adjacent_artists(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            tracks = [
                {"artist": "Кино", "title": "Группа крови", "reason": "old canon"},
                {"artist": "Би-2", "title": "Серебро", "reason": "old canon"},
                {"artist": "The Hardkiss", "title": "Журавлі", "reason": "uk alt"},
                {"artist": "The Hardkiss", "title": "Кораблі", "reason": "repeat"},
                {"artist": "Один в каное", "title": "У мене немає дому", "reason": "uk indie"},
                {"artist": "Latexfauna", "title": "Bounty", "reason": "uk groove"},
                {"artist": "Vivienne Mort", "title": "Готика", "reason": "uk art"},
                {"artist": "SadSvit", "title": "Касета", "reason": "uk wave"},
                {"artist": "паліндром", "title": "Не дожив", "reason": "uk alt"},
                {"artist": "Дайте танк (!)", "title": "Мы", "reason": "ru indie"},
                {"artist": "Буерак", "title": "Спортивные очки", "reason": "ru post"},
                {"artist": "Молчат Дома", "title": "Судно", "reason": "dark wave"},
                {"artist": "Пошлая Молли", "title": "Любимая песня твоей сестры", "reason": "new rock"},
                {"artist": "Порнофильмы", "title": "Это пройдёт", "reason": "ru punk"},
            ]
            response = {
                "provider": "test-ai",
                "candidate": json.dumps({
                    "tracks": tracks,
                    "similarTracks": [],
                    "targetMood": ["modern", "alt rock"],
                    "avoid": ["legacy"],
                }, ensure_ascii=False),
                "error": "",
            }

            with patch.object(api, "_ai_providers", return_value=[{"name": "test"}]), \
                    patch("backend.api._chat_completion", return_value=response):
                plan = api._queue_search_plan({
                    "station_prompt": "сучасний альт рок рос і укр рок без старого канону",
                })

            artists = [track["artist"] for track in plan["tracks"]]
            self.assertEqual(len(plan["tracks"]), 10)
            self.assertNotIn("Кино", artists)
            self.assertNotIn("Би-2", artists)
            self.assertNotEqual(artists[0], artists[1])
            self.assertTrue(any(item["reason"] == "legacy-regional-rock" for item in plan["skipped"]))
            self.assertTrue(any(item["reason"] == "adjacent-artist-repeat" for item in plan["skipped"]))

    def test_legacy_artist_named_after_without_is_still_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            response = {
                "provider": "test-ai",
                "candidate": json.dumps({
                    "tracks": [
                        {"artist": "Би-2", "title": "Серебро"},
                        {"artist": "Аквариум", "title": "Рок-н-ролл мертв"},
                        {"artist": "The Hardkiss", "title": "Жива"},
                    ],
                }, ensure_ascii=False),
                "error": "",
            }
            with patch.object(api, "_ai_providers", return_value=[{"name": "test"}]), patch(
                "backend.api._chat_completion", return_value=response,
            ):
                plan = api._queue_search_plan({
                    "station_prompt": (
                        "сучасний український та російськомовний alt rock "
                        "без старого канону типу Би-2 та Аквариум"
                    ),
                })

            self.assertEqual(
                [(item["artist"], item["title"]) for item in plan["tracks"]],
                [("The Hardkiss", "Жива")],
            )
            self.assertEqual(
                sum(item["reason"] == "legacy-regional-rock" for item in plan["skipped"]),
                2,
            )

    def test_advance_records_history_and_refills_without_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            db, root = self.make_library(directory)
            discovery_calls = []

            def discoverer(excluded):
                discovery_calls.append(excluded)
                return None

            queue = RadioQueueManager(
                db, root, discoverer=discoverer,
                random_source=random.Random(3),
            )
            before = queue.bootstrap()
            before_ids = [track["id"] for track in before["items"]]
            after = queue.advance(before_ids[0])
            after_ids = [track["id"] for track in after["items"]]

            self.assertEqual(after["size"], 10)
            self.assertEqual(after_ids[0], before_ids[1])
            self.assertNotIn(before_ids[0], after_ids)
            self.assertEqual(len(after_ids), len(set(after_ids)))
            self.assertEqual(discovery_calls, [])
            history = db.recent_radio_history(1)
            self.assertEqual(history[0]["track_id"], before_ids[0])

    def test_advance_consumes_ai_track_file_and_database_row(self):
        with tempfile.TemporaryDirectory() as directory:
            db, root = self.make_library(directory, count=4)
            queue = RadioQueueManager(db, root, random_source=random.Random(3))
            before = queue.bootstrap()
            finished = before["items"][0]
            finished_path = root / finished["local_path"]

            result = queue.advance(finished["id"])

            self.assertTrue(result["consumed_ai_track"])
            self.assertEqual(result["consumed_track_id"], finished["id"])
            self.assertFalse(finished_path.exists())
            self.assertIsNone(db.track(finished["id"]))
            self.assertNotIn(
                finished["id"], [track["id"] for track in result["items"]]
            )

    def test_discovery_requires_both_opt_in_switches(self):
        with tempfile.TemporaryDirectory() as directory:
            db, root = self.make_library(directory, count=4)
            queue = RadioQueueManager(db, root)
            self.assertFalse(queue.status()["discovery_enabled"])
            db.save_settings({"licensed_sources_confirmed": "1"})
            self.assertTrue(queue.status()["discovery_enabled"])

    def test_empty_ai_library_starts_downloading_during_bootstrap(self):
        with tempfile.TemporaryDirectory() as directory:
            db, root = self.make_library(directory, count=0)
            requested = threading.Event()

            def discoverer(excluded):
                requested.set()
                return None

            db.save_settings({"licensed_sources_confirmed": "1"})
            queue = RadioQueueManager(db, root, discoverer=discoverer)
            snapshot = queue.bootstrap()

            self.assertEqual(snapshot["size"], 0)
            self.assertTrue(requested.wait(1))
            queue._refill_thread.join(2)

    def test_failed_refill_reports_error_and_throttles_automatic_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            db, root = self.make_library(directory, count=0)
            calls = []

            def discoverer(excluded):
                calls.append(excluded)
                raise RuntimeError("Downloader unavailable")

            db.save_settings({"licensed_sources_confirmed": "1"})
            queue = RadioQueueManager(db, root, discoverer=discoverer)
            queue.request_refill()
            queue._refill_thread.join(2)

            failed = queue.status()
            self.assertEqual(failed["phase"], "error")
            self.assertEqual(failed["last_error"], "Downloader unavailable")
            self.assertGreater(failed["retry_in_seconds"], 0)
            self.assertLessEqual(failed["retry_in_seconds"], 5)
            queue.request_refill()
            self.assertEqual(len(calls), 1)

    def test_online_refill_starts_only_at_the_seven_track_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            db, root = self.make_library(directory)
            requested = threading.Event()
            discovery_calls = []

            def discoverer(excluded):
                discovery_calls.append(excluded)
                requested.set()
                return None

            db.save_settings({
                "dynamic_discovery_enabled": "1",
                "licensed_sources_confirmed": "1",
            })
            queue = RadioQueueManager(
                db, root, discoverer=discoverer,
                random_source=random.Random(7),
            )
            ids = [track["id"] for track in queue.bootstrap()["items"]]
            self.assertEqual(queue.advance(ids[0])["size"], 9)
            self.assertEqual(queue.advance(ids[1])["size"], 8)
            self.assertEqual(discovery_calls, [])

            queue.advance(ids[2])
            self.assertTrue(requested.wait(1))
            queue._refill_thread.join(2)
            self.assertFalse(queue._refill_thread.is_alive())
            self.assertEqual(len(discovery_calls), 1)

    def test_rescan_preserves_local_track_identity_and_pronunciation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "downloads" / "Known Artist - Known Track.mp3"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test-audio")
            api = RadioAPI(root)
            first = api.scan_music()
            track = next(item for item in first["all_tracks"] if item.get("local_path"))
            api.db.update_track(track["id"], artist_speech="Custom pronunciation")

            second = api.scan_music()
            restored = next(
                item for item in second["all_tracks"]
                if item.get("local_path") == "downloads/Known Artist - Known Track.mp3"
            )
            self.assertEqual(restored["id"], track["id"])
            self.assertEqual(restored["artist_speech"], "Custom pronunciation")

    def test_old_unverified_discovery_cache_is_not_requeued(self):
        with tempfile.TemporaryDirectory() as directory:
            db, root = self.make_library(directory, count=4)
            relative = "downloads/queue/unknown.webm"
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test-audio")
            unknown = db.add_local_track("Unknown Channel", "Generic Dark Ballad", relative)
            db.update_track(unknown["id"], duration_ms=210_000)

            snapshot = RadioQueueManager(db, root).bootstrap()

            self.assertNotIn(unknown["id"], [track["id"] for track in snapshot["items"]])

    def test_bootstrap_hides_old_catalog_and_returns_only_ai_tracks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = RadioAPI(root)
            old_path = root / "downloads" / "Old Catalog - Imported TXT.wav"
            ai_path = root / "downloads" / "queue" / "approved.webm"
            old_path.parent.mkdir(parents=True, exist_ok=True)
            ai_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.write_bytes(b"old")
            ai_path.write_bytes(b"ai")
            old = api.db.add_local_track("Old Catalog", "Imported TXT", "downloads/Old Catalog - Imported TXT.wav")
            selected = api.db.add_local_track("AI Artist", "AI Selection", "downloads/queue/approved.webm")
            api.db.update_track(old["id"], duration_ms=180_000)
            api.db.update_track(
                selected["id"], duration_ms=180_000,
                match_score=1, library_source="ai",
            )

            result = api.bootstrap()

            self.assertEqual([track["id"] for track in result["tracks"]], [selected["id"]])

    def test_shutdown_preserves_warm_ai_buffer_and_manual_music(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = RadioAPI(root)
            ai_path = root / "downloads" / "queue" / "generated.mp3"
            manual_path = root / "music" / "manual.mp3"
            ai_path.parent.mkdir(parents=True, exist_ok=True)
            manual_path.parent.mkdir(parents=True, exist_ok=True)
            ai_path.write_bytes(b"ai")
            manual_path.write_bytes(b"manual")
            generated = api.db.add_local_track(
                "Generated Artist", "Generated Track", "downloads/queue/generated.mp3",
            )
            api.db.update_track(
                generated["id"], library_source="ai", match_score=1,
            )
            api.db.add_local_track(
                "Manual Artist", "Manual Track", "music/manual.mp3",
            )

            result = api.shutdown()

            self.assertTrue(result["ok"])
            self.assertTrue(ai_path.exists())
            self.assertTrue(manual_path.exists())
            self.assertTrue([
                track for track in api.db.tracks()
                if track.get("library_source") == "ai"
            ])
            self.assertEqual(result["preserved_tracks"], 1)
            previous = json.loads(api.db.settings()["ai_previous_playlist"])
            self.assertEqual(previous, [{
                "artist": "Generated Artist", "title": "Generated Track",
            }])

    def test_startup_reuses_verified_ai_cache_and_remembers_last_playlist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = RadioAPI(root)
            stale_path = root / "downloads" / "queue" / "stale.mp3"
            stale_path.parent.mkdir(parents=True, exist_ok=True)
            stale_path.write_bytes(b"stale")
            stale = first.db.add_local_track(
                "Previous Artist", "Previous Track", "downloads/queue/stale.mp3",
            )
            first.db.update_track(
                stale["id"], library_source="ai", match_score=1,
            )

            restarted = RadioAPI(root)

            self.assertTrue(stale_path.exists())
            self.assertTrue([
                track for track in restarted.db.tracks()
                if track.get("library_source") == "ai"
            ])
            previous = json.loads(restarted.db.settings()["ai_previous_playlist"])
            self.assertEqual(previous, [{
                "artist": "Previous Artist", "title": "Previous Track",
            }])

    def test_changed_genre_prompt_discards_previous_playlist_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = RadioAPI(root)
            first.db.save_settings({
                "ai_playlist_prompt": "old darkwave prompt",
                "ai_previous_playlist": json.dumps([{
                    "artist": "Old Artist", "title": "Old Track",
                }]),
                "station_prompt": "new shoegaze and dream pop prompt",
            })
            old_path = root / "downloads" / "queue" / "old.mp3"
            old_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.write_bytes(b"old")
            old_track = first.db.add_local_track(
                "Old Artist", "Old Track", "downloads/queue/old.mp3",
            )
            first.db.update_track(
                old_track["id"], library_source="ai", match_score=1,
            )

            restarted = RadioAPI(root)
            settings = restarted.db.settings()

            self.assertFalse(old_path.exists())
            self.assertFalse([
                track for track in restarted.db.tracks()
                if track.get("library_source") == "ai"
            ])
            self.assertEqual(json.loads(settings["ai_previous_playlist"]), [])
            self.assertEqual(
                settings["ai_playlist_prompt"],
                "new shoegaze and dream pop prompt",
            )

    def test_save_settings_purges_existing_ai_library_on_prompt_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = RadioAPI(root)
            for number in range(17):
                relative = f"downloads/queue/Artist {number} - Track {number}.mp3"
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test-audio")
                track = api.db.add_local_track(f"Artist {number}", f"Track {number}", relative)
                api.db.update_track(track["id"], match_score=1, library_source="ai")

            self.assertEqual(len(api._ai_library_tracks()), 17)

            result = api.save_settings({"station_prompt": "brand new unrelated genre"})

            self.assertTrue(result["prompt_changed"])
            self.assertEqual(result["tracks"], [])
            self.assertEqual(len(api._ai_library_tracks()), 0)
            self.assertEqual(
                [track for track in api.db.tracks() if track.get("library_source") == "ai"],
                [],
            )

            restarted = RadioAPI(root)
            self.assertEqual(len(restarted._ai_library_tracks()), 0)
            self.assertEqual(
                restarted.db.settings()["station_prompt"], "brand new unrelated genre",
            )

    def test_ai_plans_canonical_known_tracks_before_search(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            ai_response = {
                "provider": "test-ai",
                "candidate": """{
                    "tracks": [
                        {"artist": "Linkin Park", "title": "Numb", "reason": "alt rock"},
                        {"artist": "The Cure", "title": "Pictures of You", "reason": "post-punk"}
                    ],
                    "targetMood": ["melancholic", "atmospheric"],
                    "avoid": ["covers"]
                }""",
                "error": "",
            }
            with patch.object(api, "_ai_providers", return_value=[{"name": "test"}]), patch(
                "backend.api._chat_completion", return_value=ai_response,
            ):
                plan = api._queue_search_plan({"station_prompt": "alt rock ballads"})

            self.assertEqual(plan["tracks"][0], {
                "artist": "Linkin Park", "title": "Numb", "reason": "alt rock",
            })
            self.assertNotIn("queries", plan)
            self.assertEqual(
                api._exact_track_query(plan["tracks"][0]),
                'ytsearch5:"Linkin Park" "Numb" official audio',
            )

    def test_ai_plan_recovers_complete_tracks_from_truncated_outer_json(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            truncated = (
                '{"tracks":['
                '{"artist":"Linkin Park","title":"Numb"},'
                '{"artist":"The Cure","title":"Lovesong"},'
            )
            response = {
                "provider": "test-ai", "candidate": truncated, "error": "",
            }
            with patch.object(api, "_ai_providers", return_value=[{"name": "test"}]), patch(
                "backend.api._chat_completion", return_value=response,
            ):
                plan = api._queue_search_plan({"station_prompt": "alt rock ballads"})

            self.assertEqual(
                [(track["artist"], track["title"]) for track in plan["tracks"]],
                [("Linkin Park", "Numb"), ("The Cure", "Lovesong")],
            )

    def test_generic_search_result_cannot_replace_ai_recommendation(self):
        settings = {"queue_min_duration": "120", "queue_max_duration": "480"}
        recommendation = {"artist": "Linkin Park", "title": "Numb"}
        generic = {
            "id": "generic", "url": "https://example.test/generic", "duration": 240,
            "title": "Dark Indie Rock Ballad with Emotional Male Vocals",
            "artist": "Music Lyric Waves", "uploader": "Music Lyric Waves",
        }
        official = {
            "id": "official", "url": "https://example.test/official", "duration": 187,
            "title": "Numb (Official Music Video)", "artist": "Linkin Park",
            "uploader": "Linkin Park", "channel": "Linkin Park",
        }

        self.assertFalse(RadioAPI._queue_candidate_allowed(
            generic, settings, set(), set(), recommendation,
        ))
        self.assertTrue(RadioAPI._queue_candidate_allowed(
            official, settings, set(), set(), recommendation,
        ))
        self.assertEqual(official["match_score"], 1.0)

    def test_single_word_coincidences_and_reversed_credits_are_rejected(self):
        settings = {"queue_min_duration": "60", "queue_max_duration": "600"}
        child_performance = {
            "id": "wrong-one", "url": "https://example.test/wrong-one", "duration": 180,
            "title": "Поливана Альбіна та друзі - Запали Вогонь",
            "artist": "School Festival", "uploader": "School Festival",
            "channel": "School Festival",
        }
        reversed_credit = {
            "id": "wrong-two", "url": "https://example.test/wrong-two", "duration": 210,
            "title": "КОЛІР СОНЦЕ - Птаха",
            "artist": "КОЛІР СОНЦЕ", "uploader": "КОЛІР СОНЦЕ",
            "channel": "КОЛІР СОНЦЕ",
        }
        longer_different_title = {
            "id": "wrong-three", "url": "https://example.test/wrong-three", "duration": 220,
            "title": "The Soft Moon - Black Sabbath (Official Audio)",
            "artist": "The Soft Moon", "uploader": "The Soft Moon",
            "channel": "The Soft Moon",
        }

        self.assertFalse(RadioAPI._queue_candidate_allowed(
            child_performance, settings, set(), set(),
            {"artist": "Альбіна", "title": "Вогонь"},
        ))
        self.assertFalse(RadioAPI._queue_candidate_allowed(
            reversed_credit, settings, set(), set(),
            {"artist": "Птаха", "title": "Сонце"},
        ))
        self.assertFalse(RadioAPI._queue_candidate_allowed(
            longer_different_title, settings, set(), set(),
            {"artist": "The Soft Moon", "title": "Black"},
        ))

    def test_discovery_does_not_fall_back_to_generic_queries_without_ai(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            with patch.object(api, "_ai_providers", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "AI-провайдера"):
                    api._queue_search_plan({"station_prompt": "dark indie"})


if __name__ == "__main__":
    unittest.main()
