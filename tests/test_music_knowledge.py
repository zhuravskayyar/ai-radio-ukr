import tempfile
import unittest
from pathlib import Path

from backend.db import Database
from backend.music_knowledge import MusicKnowledgeBase


class MusicKnowledgeEvidenceTests(unittest.TestCase):
    def make_knowledge(self, directory):
        db = Database(Path(directory) / "radio.db")
        db.merge_tracks([{"rank": 1, "artist": "Test Band", "title": "Test Song"}])
        return db, MusicKnowledgeBase(db), db.tracks()[0]

    def test_legacy_source_fields_remain_broadcast_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            db, knowledge, track = self.make_knowledge(directory)
            result = knowledge.add_card(track["id"], {
                "category": "SONG_ORIGIN",
                "story_data": ["Перевірений фрагмент"],
                "source_url": "https://example.com/interview",
                "source_title": "Interview",
                "confidence": "verified",
            })
            self.assertTrue(result["ok"])
            self.assertEqual(result["story"]["verification_status"], "single_source")
            self.assertTrue(result["story"]["verification"]["broadcast_ready"])
            self.assertEqual(db.track(track["id"])["story_count"], 1)

    def test_corroborated_story_has_priority_over_single_source(self):
        with tempfile.TemporaryDirectory() as directory:
            db, knowledge, track = self.make_knowledge(directory)
            knowledge.add_card(track["id"], {
                "story_key": "single",
                "category": "SONG_ORIGIN",
                "story_data": ["Один фрагмент"],
                "source_url": "https://example.com/one",
                "confidence": "verified",
            })
            corroborated = knowledge.add_card(track["id"], {
                "story_key": "corroborated",
                "category": "STUDIO_STORY",
                "story_data": ["Два джерела підтверджують фрагмент"],
                "sources": [
                    {"id": "first", "url": "https://example.com/first", "tier": "B"},
                    {"id": "second", "url": "https://example.org/second", "tier": "B-"},
                ],
                "claims": [{
                    "text": "Два джерела підтверджують фрагмент",
                    "source_ids": ["first", "second"],
                }],
                "confidence": "verified",
            })
            self.assertTrue(corroborated["ok"])
            self.assertEqual(corroborated["story"]["verification_status"], "corroborated")
            self.assertEqual(knowledge.select(track["id"])["story_key"], "corroborated")
            refreshed = db.track(track["id"])
            self.assertEqual(refreshed["story_count"], 2)
            self.assertEqual(refreshed["story_corroborated_count"], 1)

    def test_low_quality_source_cannot_be_marked_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            _db, knowledge, track = self.make_knowledge(directory)
            result = knowledge.add_card(track["id"], {
                "category": "SONG_ORIGIN",
                "story_data": ["Неперевірений переказ"],
                "sources": [{
                    "url": "https://example.com/anonymous-post",
                    "tier": "E",
                }],
                "confidence": "verified",
            })
            self.assertFalse(result["ok"])
            self.assertIn("D та E", result["error"])

    def test_sensitive_story_requires_editor_and_two_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            _db, knowledge, track = self.make_knowledge(directory)
            card = {
                "category": "FAN_STORY",
                "story_data": ["Чутливе твердження"],
                "sources": [
                    {"id": "official", "url": "https://example.com/official", "tier": "A"},
                    {"id": "report", "url": "https://example.org/report", "tier": "B"},
                ],
                "claims": [{
                    "text": "Чутливе твердження",
                    "source_ids": ["official", "report"],
                }],
                "sensitive": True,
                "confidence": "verified",
            }
            rejected = knowledge.add_card(track["id"], card)
            self.assertFalse(rejected["ok"])
            approved = knowledge.add_card(track["id"], {
                **card,
                "reviewed_by": "Редактор",
                "reviewed_at": "2026-08-10T12:00:00+00:00",
            })
            self.assertTrue(approved["ok"])
            self.assertEqual(approved["story"]["verification_status"], "corroborated")

    def test_every_story_fragment_requires_claim_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            _db, knowledge, track = self.make_knowledge(directory)
            result = knowledge.add_card(track["id"], {
                "category": "SONG_ORIGIN",
                "story_data": ["Перший факт", "Другий факт"],
                "sources": [{"id": "one", "url": "https://example.com/one", "tier": "B"}],
                "claims": [{"text": "Перший факт", "source_ids": ["one"]}],
                "confidence": "verified",
            })
            self.assertFalse(result["ok"])
            self.assertIn("Кожен фрагмент", result["error"])


if __name__ == "__main__":
    unittest.main()
