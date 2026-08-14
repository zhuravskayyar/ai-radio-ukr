import json
import random
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.api import RadioAPI
from backend.content_planner import ContentPlanner
from backend.db import Database
from backend.music_knowledge import MusicKnowledgeBase


def planning_context(track_id=2):
    return {
        "time": {
            "iso": "2026-08-14T14:08:00+02:00",
            "daypart": "day",
            "time_check_pending": False,
            "radio_clock_slot": "",
        },
        "weather": {"available": False},
        "current_track": {"id": 1, "energy": 5},
        "next_track": {
            "id": track_id,
            "artist": "Test Artist",
            "title": "Test Song",
            "energy": 5,
            "vocal_start_ms": 0,
        },
        "host_memory": [],
        "session": {"phase": "flow"},
        "music_transition": {"kind": "neutral"},
    }


class EditorialDistributionTests(unittest.TestCase):
    def test_default_editor_voice_rate_is_about_forty_five_percent(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            planner = ContentPlanner(db, random.Random(20260814))
            settings = db.settings()
            decisions = [
                planner._editor_wants_voice(settings, offset)
                for offset in range(5000)
            ]
            share = sum(decisions) / len(decisions)
            self.assertGreater(share, 0.43)
            self.assertLess(share, 0.47)

    def test_spoken_length_mix_maps_to_twenty_five_fifteen_five(self):
        with tempfile.TemporaryDirectory() as directory:
            planner = ContentPlanner(
                Database(Path(directory) / "radio.db"),
                random.Random(44),
            )
            counts = {"short": 0, "normal": 0, "feature": 0}
            for _ in range(9000):
                length_class, _minimum, _maximum, seconds = planner._choose_length()
                counts[length_class] += 1
                bounds = {
                    "short": (7, 12),
                    "normal": (15, 25),
                    "feature": (30, 45),
                }[length_class]
                self.assertGreaterEqual(seconds, bounds[0])
                self.assertLessEqual(seconds, bounds[1])
            # Convert conditional spoken shares to shares of all tracks.
            all_track_shares = {
                key: count / 9000 * 0.45 for key, count in counts.items()
            }
            self.assertAlmostEqual(all_track_shares["short"], 0.25, delta=0.015)
            self.assertAlmostEqual(all_track_shares["normal"], 0.15, delta=0.015)
            self.assertAlmostEqual(all_track_shares["feature"], 0.05, delta=0.01)

    def test_intro_type_has_four_track_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            db.merge_tracks([{
                "rank": 1, "artist": "Test Artist", "title": "Test Song",
            }])
            track = db.tracks()[0]
            db.add_fact(
                track["id"], "Перевірений історичний факт", True,
                "historical_fact", "https://example.com/fact", "Source",
            )
            db.save_settings({
                "host_every": "1",
                "story_probability": "0",
                "fact_probability": "100",
                "rubric_probability": "0",
                "silence_probability": "0",
            })
            db.add_history({
                "content_type": "fact",
                "intro_type": "historical_fact",
                "display_text": "Стара історична підводка",
                "created_at": "2026-08-14T12:00:00+00:00",
            })
            for offset in range(3):
                db.add_history({
                    "content_type": "clean_segue",
                    "display_text": "",
                    "created_at": f"2026-08-14T12:0{offset + 1}:00+00:00",
                })
            planner = ContentPlanner(db, random.Random(8))
            context = planning_context(track["id"])
            context["next_track"].update(track)
            blocked = planner.plan(context)
            self.assertNotEqual(blocked.content_type, "fact")

            db.add_history({
                "content_type": "clean_segue",
                "display_text": "",
                "created_at": "2026-08-14T12:05:00+00:00",
            })
            allowed = planner.plan(context)
            self.assertEqual(allowed.content_type, "fact")
            self.assertEqual(allowed.intro_type, "historical_fact")


class ListenerFeedbackTests(unittest.TestCase):
    def test_new_process_session_clears_memory_but_keeps_track_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = RadioAPI(root)
            track = first.db.tracks()[0]
            first.db.save_settings({
                "listener_profile": json.dumps({
                    "history": 0.9,
                    "artist_drama": 0.8,
                    "music_theory": 0.7,
                    "strange_facts": 0.6,
                    "nostalgia": 0.4,
                    "lyrics": 0.3,
                }),
            })
            first.db.remember("session-test", "{}", "2026-08-14T12:00:00+00:00")
            first.db.add_history({
                "content_type": "fact",
                "display_text": "Old session copy",
                "created_at": "2026-08-14T12:00:00+00:00",
            })
            first.db.add_radio_history(track, "2026-08-14T12:00:00+00:00")

            restarted = RadioAPI(root)

            self.assertEqual(
                set(restarted.personalization.profile().values()), {0.5},
            )
            self.assertEqual(restarted.db.memory_items(), [])
            self.assertEqual(restarted.db.recent_history(), [])
            self.assertEqual(len(restarted.db.recent_radio_history()), 1)

    def test_story_selector_prefers_listener_interest_with_equal_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            db.merge_tracks([{
                "rank": 1, "artist": "Test Artist", "title": "Test Song",
            }])
            track = db.tracks()[0]
            knowledge = MusicKnowledgeBase(db)
            for category, hook in (
                ("BAND_ARGUMENT", "Суперечка в гурті"),
                ("LIVE_STORY", "Історичний концерт"),
            ):
                result = knowledge.add_card(track["id"], {
                    "category": category,
                    "hook": hook,
                    "story_data": [f"Перевірена деталь: {hook}"],
                    "source_url": f"https://example.com/{category.casefold()}",
                    "source_title": "Source",
                    "confidence": "verified",
                })
                self.assertTrue(result["ok"])
            selected = knowledge.select(
                track["id"],
                intro_type_scores={
                    "artist_story": 0.10,
                    "historical_fact": 0.90,
                },
            )
            self.assertEqual(selected["intro_type"], "historical_fact")

    def test_early_skip_lowers_exposed_interest_once(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            tracks = api.db.tracks()[:2]
            self.assertEqual(len(tracks), 2)
            transition = api.db.save_transition({
                "current_track_id": tracks[0]["id"],
                "next_track_id": tracks[1]["id"],
                "status": "ready",
                "content_type": "fact",
                "plan_json": json.dumps({"intro_type": "historical_fact"}),
                "scheduled_for": datetime.now(timezone.utc).isoformat(),
            })
            api.mark_transition_aired(tracks[0]["id"], tracks[1]["id"])
            before = api.personalization.profile()["history"]
            first = api.record_listener_feedback(
                tracks[1]["id"], "skip", 3, 100,
            )
            after = first["listener_profile"]["history"]
            self.assertTrue(first["recorded"])
            self.assertLess(after, before)
            second = api.record_listener_feedback(
                tracks[1]["id"], "skip", 3, 100,
            )
            self.assertFalse(second["recorded"])
            self.assertEqual(second["listener_profile"]["history"], after)
            exposures = api.db.listener_exposures()
            self.assertEqual(exposures[0]["transition_id"], transition["id"])


if __name__ == "__main__":
    unittest.main()
