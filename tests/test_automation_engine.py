import json
import random
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend.api import RadioAPI
from backend.broadcast_safety import BroadcastSafety, PROTOCOL_VERSION
from backend.content_planner import ContentPlan, ContentPlanner
from backend.context_engine import ContextEngine
from backend.db import Database
from backend.host_brain import HostBrain
from backend.music_knowledge import MusicKnowledgeBase
from backend.pilot_clock import (
    HARD_POINT_TOLERANCE_SECONDS,
    PILOT_CLOCK_VERSION,
    PilotClock,
)
from backend.speech_normalizer import normalize_linguistic
from backend.transition_director import TransitionDirector
from backend.voice_director import VoiceDirector


class ContextAndPlanningTests(unittest.TestCase):
    def test_new_broadcast_defaults_are_random_and_host_every_track(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Database(Path(directory) / "radio.db").settings()
            self.assertEqual(settings["rotation"], "random")
            self.assertEqual(settings["host_every"], "1")
            self.assertEqual(settings["talk_probability"], "100")
            self.assertEqual(settings["host_name"], "Адам Вектор")
            self.assertEqual(settings["silence_probability"], "0")
            self.assertEqual(settings["strict_live_ai_host"], "0")
            self.assertEqual(settings["story_probability"], "100")
            self.assertEqual(settings["pregen_depth"], "2")
            self.assertEqual(settings["intro_variants_per_provider"], "2")
            self.assertEqual(settings["rubric_probability"], "12")
            self.assertEqual(settings["story_every"], "2")
            self.assertEqual(settings["fact_probability"], "70")
            self.assertEqual(settings["ai_max_tokens"], "1000")
            self.assertEqual(settings["host_ai_provider"], "secondary")
            self.assertEqual(settings["dj_ai_provider"], "parallel")
            self.assertEqual(settings["queue_size"], "10")
            self.assertEqual(settings["queue_refill_threshold"], "7")
            self.assertEqual(settings["queue_critical_threshold"], "2")
            self.assertEqual(settings["artist_cooldown_tracks"], "15")
            self.assertEqual(settings["track_cooldown_tracks"], "200")
            self.assertEqual(settings["dynamic_discovery_enabled"], "1")
            self.assertEqual(settings["licensed_sources_confirmed"], "0")
            self.assertEqual(settings["pilot_clock_enabled"], "1")
            self.assertEqual(settings["responsible_editor"], "")
            self.assertEqual(settings["silence_watchdog_enabled"], "1")
            self.assertEqual(settings["silence_warning_seconds"], "3")
            self.assertEqual(settings["silence_fallback_seconds"], "7")

    def test_pilot_clock_is_one_exact_hour_with_valid_segments_and_hard_points(self):
        clock = PilotClock()
        rundown = clock.rundown("2026-08-04T08:22:00+03:00", "Ірина Редактор")
        self.assertEqual(rundown["version"], PILOT_CLOCK_VERSION)
        self.assertEqual(rundown["total_seconds"], 3600)
        self.assertEqual(rundown["segment_count"], 12)
        self.assertEqual(rundown["hard_points"], [":00", ":15", ":30", ":45"])
        self.assertTrue(all(
            180 <= segment["duration_seconds"] <= 420
            for segment in rundown["segments"]
        ))
        self.assertEqual(
            [segment["start_minute"] for segment in rundown["segments"]],
            list(range(0, 60, 5)),
        )
        self.assertTrue(all(
            segment["responsible_editor"] == "Ірина Редактор"
            and segment["source_policy"]
            and segment["fallback"]
            and segment["forbidden_claims"]
            for segment in rundown["segments"]
        ))

    def test_pilot_clock_measures_five_second_hard_point_tolerance(self):
        clock = PilotClock()
        on_time = clock.snapshot("2026-08-04T08:00:04+03:00")
        late = clock.snapshot("2026-08-04T08:00:06+03:00")
        story = clock.snapshot("2026-08-04T08:12:00+03:00")
        self.assertEqual(on_time["segment"]["slot_id"], "hour_open")
        self.assertTrue(on_time["hard_point_due"])
        self.assertEqual(on_time["timing_error_seconds"], 4)
        self.assertEqual(
            on_time["segment"]["timing_tolerance_seconds"],
            HARD_POINT_TOLERANCE_SECONDS,
        )
        self.assertFalse(late["hard_point_due"])
        self.assertTrue(late["hard_point_missed"])
        self.assertEqual(story["segment"]["slot_id"], "story_a")

    def test_context_and_content_plan_carry_complete_rundown_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            db.save_settings({"responsible_editor": "Ірина Редактор"})
            engine = ContextEngine(db)
            context = engine.snapshot(
                {"id": 1, "artist": "A", "energy": 4},
                {"id": 2, "artist": "B", "energy": 7, "vocal_start_ms": 12000},
                "2026-08-04T08:00:04+03:00",
            )
            plan = ContentPlanner(db).plan(context)
            self.assertEqual(context["clock"]["version"], PILOT_CLOCK_VERSION)
            self.assertEqual(plan.clock_slot_id, "hour_open")
            self.assertEqual(plan.content_type, "top_of_hour")
            self.assertTrue(plan.hard_point)
            self.assertEqual(plan.timing_error_seconds, 4)
            self.assertEqual(plan.responsible_editor, "Ірина Редактор")
            self.assertEqual(plan.verification_status, "context_engine")
            self.assertTrue(plan.thesis)
            self.assertTrue(plan.source_policy)
            self.assertTrue(plan.entry_cue)
            self.assertTrue(plan.exit_cue)
            self.assertTrue(plan.fallback)
            self.assertTrue(plan.forbidden_claims)

    def test_existing_default_ai_token_limit_is_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "radio.db"
            db = Database(path)
            db.save_settings({"ai_max_tokens": "360"})
            migrated = Database(path).settings()
            self.assertEqual(migrated["ai_max_tokens"], "1000")

            migrated_db = Database(path)
            migrated_db.save_settings({"ai_max_tokens": "320"})
            remigrated = Database(path).settings()
            self.assertEqual(remigrated["ai_max_tokens"], "1000")

    def test_legacy_builtin_host_name_is_migrated_to_adam_vector(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "radio.db"
            Database(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "UPDATE settings SET value='Люмен' WHERE key='host_name'"
                )
                connection.execute("PRAGMA user_version=14")

            migrated = Database(path).settings()
            self.assertEqual(migrated["host_name"], "Адам Вектор")

    def test_adam_vector_prompt_has_character_and_ai_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            prompt = HostBrain(db).persona_prompt()
            self.assertIn("Адам Вектор", prompt)
            self.assertIn("допитливість, точність і суха самоіронія", prompt)
            self.assertIn("не прикидаєшся людиною", prompt)
            self.assertIn("миттєво вимикаєш гумор і сарказм", prompt)
            self.assertIn("редакційну відповідальність", prompt)

    def test_station_clock_is_pending_until_transition_is_aired(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            engine = ContextEngine(db)
            scheduled = "2026-08-04T08:03:00+03:00"
            first = engine.time_context(scheduled)
            self.assertEqual(first.daypart, "morning")
            self.assertTrue(first.time_check_pending)
            db.remember(
                "last_time_check",
                '{"hour_key":"2026-08-04T08"}',
                scheduled,
                "2026-08-04T09:03:00+03:00",
            )
            second = engine.time_context(scheduled)
            self.assertFalse(second.time_check_pending)

    def test_content_planner_prioritizes_top_of_hour(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            planner = ContentPlanner(db)
            plan = planner.plan({
                "time": {
                    "iso": "2026-08-04T08:03:00+03:00",
                    "daypart": "morning",
                    "time_check_pending": True,
                },
                "weather": {"available": False},
                "current_track": {"energy": 5},
                "next_track": {"id": 2, "energy": 6, "vocal_start_ms": 12000},
            })
            self.assertEqual(plan.content_type, "top_of_hour")
            self.assertTrue(plan.must_say_time)

    def test_similarity_gate_rejects_repeated_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            planner = ContentPlanner(db)
            db.add_history({
                "display_text": "Ранок набирає обертів. На черзі тестовий трек.",
                "opening": "Ранок набирає обертів",
                "created_at": datetime.now().astimezone().isoformat(),
            })
            self.assertGreaterEqual(
                planner.similarity(
                    "Ранок набирає обертів. На черзі інший трек.",
                    db.recent_history(1)[0]["display_text"],
                ),
                0.76,
            )

    def test_host_every_track_never_plans_a_silent_generic_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            db.save_settings({
                "host_every": "1",
                "talk_probability": "0",
                "story_probability": "0",
                "silence_probability": "0",
                "rubric_probability": "0",
            })
            planner = ContentPlanner(db, random.Random(4))
            plan = planner.plan({
                "time": {
                    "iso": "2026-08-04T14:08:00+03:00",
                    "daypart": "day",
                    "time_check_pending": False,
                    "radio_clock_slot": "",
                },
                "weather": {"available": False},
                "current_track": {"energy": 5},
                "next_track": {"id": 2, "energy": 5, "vocal_start_ms": 0},
            })
            self.assertEqual(plan.content_type, "talk")

    def test_adam_vector_can_choose_a_deliberate_music_only_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            db.save_settings({
                "story_probability": "0",
                "rubric_probability": "0",
                "silence_probability": "100",
            })
            planner = ContentPlanner(db, random.Random(2))
            plan = planner.plan({
                "time": {
                    "iso": "2026-08-04T22:08:00+03:00",
                    "daypart": "night",
                    "time_check_pending": False,
                    "radio_clock_slot": "",
                },
                "weather": {"available": False},
                "current_track": {"energy": 5},
                "next_track": {"id": 2, "energy": 5, "vocal_start_ms": 0},
            })
            self.assertEqual(plan.content_type, "clean_segue")
            self.assertEqual(plan.structure, "silence")
            self.assertEqual(plan.mention_policy, "implicit")

    def test_generic_structure_avoids_the_last_three_patterns(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            db.save_settings({
                "story_probability": "0",
                "rubric_probability": "0",
                "silence_probability": "0",
            })
            for structure in ("announce", "mood", "transition"):
                db.add_history({
                    "content_type": "talk",
                    "structure": structure,
                    "display_text": f"Тестова репліка {structure}",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            planner = ContentPlanner(db, random.Random(5))
            plan = planner.plan({
                "time": {
                    "iso": "2026-08-04T14:08:00+03:00",
                    "daypart": "day",
                    "time_check_pending": False,
                    "radio_clock_slot": "",
                },
                "weather": {"available": False},
                "current_track": {"energy": 3, "mood": "cold"},
                "next_track": {"id": 2, "energy": 8, "mood": "dark", "vocal_start_ms": 0},
                "session": {"phase": "flow"},
                "music_transition": {"kind": "high_energy"},
            })
            self.assertEqual(plan.content_type, "talk")
            self.assertNotIn(plan.structure, {"announce", "mood", "transition"})
            self.assertIn(plan.length_class, {"short", "medium", "long"})
            self.assertEqual(plan.reaction, "high_energy")

    def test_implicit_mood_line_does_not_have_to_repeat_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            planner = ContentPlanner(Database(Path(directory) / "radio.db"))
            accepted, error = planner.quality_gate(
                "Світло можна не вмикати, далі буде темно, але не тихо.",
                {"artist": "Witchz", "title": "The Magick"},
                {"time": {}},
                mention_policy="implicit",
                structure="mood",
            )
            self.assertTrue(accepted, error)

    def test_context_contains_persona_session_and_music_reaction(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            engine = ContextEngine(db)
            context = engine.snapshot(
                {"artist": "A", "energy": 3, "mood": "cold"},
                {"artist": "B", "energy": 9, "mood": "dark", "genre": "electronic"},
            )
            persona = context["personality"]["persona"]
            self.assertEqual(persona["name"], "Адам Вектор")
            self.assertIn("цифровий", persona["identity"])
            self.assertIn("точність", " ".join(persona["core_traits"]))
            self.assertIn("виправлення помилки", persona["serious_mode"]["triggers"])
            self.assertEqual(context["session"]["phase"], "opening")
            self.assertEqual(context["music_transition"]["kind"], "high_energy")
            self.assertEqual(context["music_transition"]["next_genre"], "electronic")

    def test_verified_story_card_is_selected_and_remembered_as_a_series(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            db.merge_tracks([{"rank": 1, "artist": "Test Band", "title": "Test Song"}])
            track = db.tracks()[0]
            knowledge = MusicKnowledgeBase(db)
            missing_source = knowledge.add_card(track["id"], {
                "category": "SONG_ORIGIN",
                "story_data": ["Перевірений фрагмент"],
                "confidence": "verified",
            })
            self.assertFalse(missing_source["ok"])
            first = knowledge.add_card(track["id"], {
                "category": "SONG_ORIGIN",
                "hook": "Цю пісню могли не записати",
                "story_data": [
                    "Гітарист придумав риф наприкінці сесії",
                    "Інші учасники спочатку відмовлялися його грати",
                    "У підсумку композицію залишили",
                ],
                "source_url": "https://example.com/source-one",
                "source_title": "Verified interview",
                "confidence": "verified",
                "duration_class": "normal",
                "series_key": "test-song",
                "episode": 1,
                "tease_next": "чому учасники були проти",
            })
            self.assertTrue(first["ok"])
            knowledge.add_card(track["id"], {
                "category": "BAND_ARGUMENT",
                "hook": "Суперечка на цьому не закінчилася",
                "story_data": ["Другий перевірений епізод"],
                "source_url": "https://example.com/source-two",
                "confidence": "verified",
                "duration_class": "short",
                "series_key": "test-song",
                "episode": 2,
            })
            db.save_settings({"story_probability": "100"})
            planner = ContentPlanner(db, random.Random(1))
            context = {
                "time": {
                    "iso": "2026-08-04T14:08:00+03:00",
                    "daypart": "day",
                    "time_check_pending": False,
                    "radio_clock_slot": "",
                },
                "weather": {"available": False},
                "current_track": {"id": 9, "energy": 5},
                "next_track": {**track, "energy": 6, "vocal_start_ms": 0},
                "host_memory": [],
            }
            plan = planner.plan(context)
            self.assertEqual(plan.content_type, "story")
            self.assertEqual(plan.story_episode, 1)
            planner.mark_aired({
                "next_track_id": track["id"],
                "context_json": json.dumps(context, ensure_ascii=False),
                "plan_json": json.dumps(plan.to_dict(), ensure_ascii=False),
            })
            next_plan = planner.plan(context)
            self.assertEqual(next_plan.story_episode, 2)
            self.assertEqual(next_plan.story_callback, "чому учасники були проти")
            used = db.stories_for_track(track["id"], verified_only=True)
            self.assertEqual(sum(item["use_count"] for item in used), 1)

    def test_single_story_card_is_not_reused_immediately_after_airing(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            db.merge_tracks([{"rank": 1, "artist": "Test Band", "title": "Test Song"}])
            track = db.tracks()[0]
            knowledge = MusicKnowledgeBase(db)
            knowledge.add_card(track["id"], {
                "category": "SONG_ORIGIN",
                "hook": "Цю пісню могли не записати",
                "story_data": ["Одна перевірена історія для тесту"],
                "source_url": "https://example.com/source-one",
                "source_title": "Verified interview",
                "confidence": "verified",
                "duration_class": "short",
            })
            db.save_settings({"story_probability": "100"})
            planner = ContentPlanner(db, random.Random(1))
            context = {
                "time": {
                    "iso": "2026-08-04T14:08:00+03:00",
                    "daypart": "day",
                    "time_check_pending": False,
                    "radio_clock_slot": "",
                },
                "weather": {"available": False},
                "current_track": {"id": 9, "energy": 5},
                "next_track": {**track, "energy": 6, "vocal_start_ms": 0},
                "host_memory": [],
            }
            first_plan = planner.plan(context)
            self.assertEqual(first_plan.content_type, "story")
            planner.mark_aired({
                "next_track_id": track["id"],
                "context_json": json.dumps(context, ensure_ascii=False),
                "plan_json": json.dumps(first_plan.to_dict(), ensure_ascii=False),
            })
            second_plan = planner.plan(context)
            self.assertNotEqual(second_plan.content_type, "story")

    def test_story_is_guaranteed_when_recent_window_has_none(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            db.merge_tracks([{"rank": 1, "artist": "Test Artist", "title": "Test Song"}])
            track = db.tracks()[0]
            db.add_story({
                "track_id": track["id"],
                "story_key": "guaranteed-story",
                "category": "SONG_ORIGIN",
                "hook": "Перевірений початок",
                "story_data_json": json.dumps(["Перевірена деталь"], ensure_ascii=False),
                "source_url": "https://example.com/source",
                "source_title": "Source",
                "confidence": "verified",
                "duration_class": "short",
            })
            db.save_settings({"story_probability": "0", "story_every": "4"})
            for offset in range(3):
                db.add_history({
                    "current_track_id": track["id"],
                    "next_track_id": track["id"],
                    "content_type": "talk",
                    "display_text": f"Репліка {offset}",
                    "created_at": f"2026-08-04T12:0{offset}:00+00:00",
                })
            planner = ContentPlanner(db, random.Random(9))
            plan = planner.plan({
                "time": {
                    "iso": "2026-08-04T14:08:00+03:00",
                    "daypart": "day",
                    "time_check_pending": False,
                    "radio_clock_slot": "",
                },
                "weather": {"available": False},
                "current_track": {"id": 999, "energy": 5},
                "next_track": {**track, "energy": 5, "vocal_start_ms": 0},
                "host_memory": [],
            })
            self.assertEqual(plan.content_type, "story")

    def test_first_transition_can_tell_sourced_story_about_finished_track(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            db.merge_tracks([
                {"rank": 1, "artist": "First Artist", "title": "Opening Song"},
                {"rank": 2, "artist": "Next Artist", "title": "Next Song"},
            ])
            current, next_track = db.tracks()[:2]
            db.add_story({
                "track_id": current["id"],
                "story_key": "opening-song-comment",
                "category": "ARTIST_COMMENT",
                "hook": "Виконавець пояснив головну деталь",
                "story_data_json": json.dumps([
                    "Виконавець пояснив, що пісня виросла з особистої нотатки"
                ], ensure_ascii=False),
                "source_url": "https://example.com/interview",
                "source_title": "Verified artist interview",
                "confidence": "verified",
                "duration_class": "short",
            })
            db.save_settings({
                "story_probability": "100",
                "silence_probability": "0",
                "rubric_probability": "0",
            })
            plan = ContentPlanner(db, random.Random(3)).plan({
                "time": {
                    "iso": "2026-08-04T14:08:00+03:00",
                    "daypart": "day",
                    "time_check_pending": False,
                    "radio_clock_slot": "",
                },
                "weather": {"available": False},
                "current_track": {**current, "energy": 5},
                "next_track": {**next_track, "energy": 6, "vocal_start_ms": 0},
                "host_memory": [],
            })

            self.assertEqual(plan.content_type, "story")
            self.assertEqual(plan.story_subject_role, "current")
            self.assertEqual(plan.story_subject_track_id, current["id"])
            self.assertIn("щойно зіграну", plan.directive)


class BroadcastSafetyTests(unittest.TestCase):
    def test_dead_air_protocol_is_deterministic_and_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            safety = BroadcastSafety(db)
            result = safety.protocol("dead_air", {"silent_seconds": 7.2})
            self.assertTrue(result["ok"])
            self.assertEqual(result["version"], PROTOCOL_VERSION)
            self.assertEqual(result["severity"], "critical")
            self.assertTrue(result["automatic"])
            self.assertIn("технічну паузу", result["display_text"])
            events = db.broadcast_events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "dead_air")
            self.assertEqual(
                json.loads(events[0]["details_json"])["silent_seconds"], 7.2,
            )

    def test_safety_alert_requires_official_source_and_editor(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            safety = BroadcastSafety(db)
            blocked = safety.protocol("safety_alert", {
                "official_text": "Перейдіть в укриття.",
                "source_url": "not-a-url",
            })
            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["status"], "human_review_required")
            self.assertEqual(db.broadcast_events(), [])

            accepted = safety.protocol("safety_alert", {
                "official_text": "Перейдіть в укриття.",
                "source_url": "https://official.example/alert/42",
                "responsible_editor": "Ірина Редактор",
            })
            self.assertTrue(accepted["ok"])
            self.assertEqual(accepted["display_text"], "Перейдіть в укриття.")
            self.assertEqual(len(db.broadcast_events()), 1)

    def test_correction_requires_traceable_source_and_has_no_humor(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            safety = BroadcastSafety(db)
            blocked = safety.correction(
                "Було 20 градусів", "Було 18 градусів", "", "Метеослужба", "",
            )
            self.assertFalse(blocked["ok"])
            self.assertEqual(db.broadcast_events(), [])

            accepted = safety.correction(
                "Було 20 градусів",
                "Було 18 градусів",
                "https://weather.example/report",
                "Офіційна метеослужба",
                "Ірина Редактор",
            )
            self.assertTrue(accepted["ok"])
            self.assertEqual(accepted["status"], "queued")
            self.assertIn("Правильно: Було 18 градусів", accepted["display_text"])
            self.assertNotIn("жарт", accepted["display_text"].casefold())
            self.assertEqual(safety.status()["open_corrections"], 1)
            db.resolve_broadcast_event(accepted["event_id"], "aired")
            self.assertEqual(safety.status()["open_corrections"], 0)

    def test_api_uses_configured_editor_for_correction(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"responsible_editor": "Ірина Редактор"})
            result = api.queue_correction(
                "Неточна дата",
                "Точна дата — 10 серпня",
                "https://official.example/date",
                "Офіційний календар",
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["responsible_editor"], "Ірина Редактор")
            resolved = api.resolve_broadcast_event(result["event_id"], "aired")
            self.assertEqual(resolved, {"ok": True, "status": "aired"})


class VoiceAndTransitionTests(unittest.TestCase):
    def test_voice_director_keeps_three_text_layers(self):
        director = VoiceDirector()
        track = {
            "artist": "Scorpions",
            "title": "Living and Dying",
            "artist_speech": "Скорпіонс",
            "title_speech": "Лівін енд Дайін",
            "energy": 8,
            "pronunciation_review": 0,
        }
        result = director.direct(
            "За 11 хвилин — Scorpions — «Living and Dying».",
            [track],
            {"time": {"daypart": "morning"}, "personality": {}},
            track,
            7,
        )
        self.assertIn("Scorpions", result["linguistic_text"])
        self.assertIn("одинадцять", result["linguistic_text"])
        self.assertIn("Скорпіонс", result["tts_text"])
        self.assertEqual(result["profile"]["rate"], "+4%")

    def test_contextual_numbers_reject_impossible_time(self):
        self.assertIn("плюс сім градусів", normalize_linguistic("Надворі +7°."))
        self.assertIn("дві тисячі двадцять п'ятий рік", normalize_linguistic("2025 рік"))
        with self.assertRaises(ValueError):
            normalize_linguistic("Зустрічаємося о 19:99.")

    def test_transition_director_hits_the_post(self):
        director = TransitionDirector(27)
        plan = director.plan(
            {"duration_ms": 220000, "outro_start_ms": 210000, "end_type": "fade"},
            {"vocal_start_ms": 14500},
            "talk",
            short_ms=5200,
            full_ms=11800,
        )
        self.assertEqual(plan.transition_type, "talk_up")
        self.assertEqual(plan.variant, "full")
        self.assertLess(plan.voice_duration_ms, 14500)

    def test_voice_profile_caps_a_transition_at_ten_seconds(self):
        profile = VoiceDirector().profile(
            {"time": {"daypart": "day"}, "personality": {}},
            {"energy": 5},
            24,
        )
        self.assertEqual(profile.target_seconds, 10.0)

    def test_story_voice_profile_uses_twenty_to_forty_second_window(self):
        profile = VoiceDirector().profile(
            {"time": {"daypart": "day"}, "personality": {}},
            {"energy": 5},
            60,
            "story",
        )
        self.assertEqual(profile.target_seconds, 40.0)
        self.assertEqual(profile.target_words_min, 35)
        self.assertEqual(profile.target_words_max, 55)

    def test_transition_director_uses_measured_outro_and_respects_hard_end(self):
        director = TransitionDirector(27)
        outro = director.plan(
            {"duration_ms": 220000, "outro_start_ms": 208000, "end_type": "fade"},
            {"vocal_start_ms": 0},
            "talk",
            short_ms=5000,
            full_ms=9000,
        )
        hard = director.plan(
            {"duration_ms": 220000, "outro_start_ms": 208000, "end_type": "hard"},
            {"vocal_start_ms": 0},
            "talk",
            short_ms=5000,
            full_ms=9000,
        )
        self.assertEqual(outro.transition_type, "talk_over_outro")
        self.assertEqual(hard.transition_type, "between")

        clipped_outro = director.plan(
            {
                "duration_ms": 220000,
                "hard_end_ms": 211000,
                "outro_start_ms": 208000,
                "end_type": "fade",
            },
            {"vocal_start_ms": 0},
            "talk",
            short_ms=5000,
            full_ms=9000,
        )
        self.assertEqual(clipped_outro.transition_type, "between")


class PreparedTransitionTests(unittest.TestCase):
    def test_prepared_and_aired_transition_is_logged_in_hour_rundown(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            current, next_track = api.db.tracks()[0:2]
            scheduled = "2026-08-04T08:00:04+03:00"
            plan = ContentPlan(
                content_type="liner",
                style="straight_radio",
                announce_mode="station_id",
                target_seconds=4,
                liner_text="Адам Вектор, цифровий ведучий LUMEN RADIO.",
                clock_version=PILOT_CLOCK_VERSION,
                clock_slot_id="hour_open",
                clock_slot_name="Відкриття години",
                hard_time="2026-08-04T08:00:00+03:00",
                planned_start="2026-08-04T08:00:00+03:00",
                planned_end="2026-08-04T08:05:00+03:00",
                hard_point=True,
                timing_tolerance_seconds=5,
                timing_error_seconds=4,
                thesis="Відкрити годину",
                source_policy="Час лише з ContextEngine",
                fallback="Перевірений liner",
                forbidden_claims=["непідтверджені новини"],
                responsible_editor="Ірина Редактор",
            )
            with patch.object(api.content_planner, "plan", return_value=plan), patch.object(
                api, "_speech_asset", return_value={"ok": False, "error": "offline"}
            ):
                prepared = api.prepare_transition(
                    current["id"], next_track["id"], scheduled, force=True,
                )
            self.assertTrue(prepared["ok"])
            events = api.db.rundown_events("2026-08-04T08")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["slot_id"], "hour_open")
            self.assertEqual(events[0]["timing_status"], "planned")

            api.mark_transition_aired(current["id"], next_track["id"])
            rundown = api.pilot_hour(scheduled)
            self.assertEqual(rundown["metrics"]["aired_events"], 1)
            self.assertEqual(
                rundown["segments"][0]["items"][0]["slot_id"], "hour_open"
            )
            self.assertIn(
                rundown["segments"][0]["items"][0]["timing_status"],
                {"on_time", "early", "late"},
            )

    def test_live_transition_voices_local_fallback_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            current = api.db.tracks()[9]
            next_track = next(
                track for track in api.db.tracks()
                if track["artist"] == "Scorpions"
                and track["title"].startswith("Living and Dying")
            )
            plan = ContentPlan(
                content_type="talk",
                style="straight_radio",
                announce_mode="forward",
                target_seconds=10,
            )
            with patch.object(api.content_planner, "plan", return_value=plan), patch.object(
                api, "_speech_asset", return_value={"ok": False, "error": "offline"}
            ) as synth:
                prepared = api.prepare_transition(
                    current["id"], next_track["id"],
                    datetime.now().astimezone().isoformat(), force=True,
                )
            self.assertTrue(prepared["ok"])
            self.assertEqual(prepared["transition"]["content_type"], "talk")
            self.assertEqual(prepared["transition"]["provider"], "template")
            self.assertTrue(prepared["transition"].get("display_full"))
            self.assertTrue(prepared["transition"].get("speech_full"))
            self.assertEqual(synth.call_count, 1)

    def test_transition_is_generated_and_cached_before_retrieval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = RadioAPI(root)
            api.db.save_settings({"strict_live_ai_host": "0"})
            current = api.db.tracks()[9]
            next_track = next(
                track for track in api.db.tracks()
                if track["artist"] == "Scorpions"
                and track["title"].startswith("Living and Dying")
            )
            api.db.update_track(next_track["id"], vocal_start_ms=14500)
            plan = ContentPlan(
                content_type="talk",
                style="straight_radio",
                announce_mode="forward",
                target_seconds=11,
                directive="Коротка тестова підводка.",
            )
            cache = root / "cache" / "tts"
            cache.mkdir(parents=True)
            fake = cache / "prepared.mp3"
            fake.write_bytes(b"prepared-audio")
            asset = {
                "ok": True,
                "path": fake.relative_to(root).as_posix(),
                "duration_ms": 5200,
                "speech_text": "готово",
                "rate": "+0%",
                "cached": True,
            }
            with patch.object(api.content_planner, "plan", return_value=plan), patch.object(
                api, "_speech_asset", return_value=asset
            ) as synth:
                prepared = api.prepare_transition(
                    current["id"], next_track["id"],
                    datetime.now().astimezone().isoformat(), force=True,
                )
                calls_after_prepare = synth.call_count
                retrieved = api.get_prepared_transition(current["id"], next_track["id"])
            self.assertTrue(prepared["ok"])
            self.assertEqual(prepared["transition"]["status"], "ready")
            self.assertEqual(retrieved["status"], "ready")
            self.assertTrue(retrieved["audio"].startswith("data:audio/mpeg;base64,"))
            self.assertEqual(synth.call_count, calls_after_prepare)
            self.assertEqual(calls_after_prepare, 1)

            api.db.save_transition({
                "current_track_id": current["id"],
                "next_track_id": next_track["id"],
                "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            })
            expired = api.get_prepared_transition(current["id"], next_track["id"])
            self.assertEqual(expired["status"], "emergency")
            self.assertEqual(expired["transition_type"], "between")
            self.assertEqual(expired["provider"], "local-emergency")
            self.assertTrue(expired["speech_text"])

    def test_prepared_text_uses_local_system_voice_fallback_when_edge_tts_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"strict_live_ai_host": "0"})
            current = api.db.tracks()[9]
            next_track = next(
                track for track in api.db.tracks()
                if track["artist"] == "Scorpions"
                and track["title"].startswith("Living and Dying")
            )
            plan = ContentPlan(
                content_type="talk",
                style="straight_radio",
                announce_mode="forward",
                target_seconds=10,
            )
            with patch.object(api.content_planner, "plan", return_value=plan), patch.object(
                api, "_speech_asset", return_value={"ok": False, "error": "offline"}
            ):
                prepared = api.prepare_transition(
                    current["id"], next_track["id"],
                    datetime.now().astimezone().isoformat(), force=True,
                )
            retrieved = api.get_prepared_transition(current["id"], next_track["id"])
            self.assertTrue(prepared["ok"])
            self.assertEqual(retrieved["status"], "ready")
            self.assertTrue(retrieved["speech_text"])
            self.assertFalse(retrieved["audio"])
            self.assertIn("offline", retrieved["provider_error"])


if __name__ == "__main__":
    unittest.main()
