import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.db import Database


class SettingsPersistenceTests(unittest.TestCase):
    def test_v22_enables_automatic_research_and_clears_cached_mock_intro(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "radio.db"
            database = Database(path)
            track_id = database.add_local_track(
                "Test Artist", "Test Track", "",
            )["id"]
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "UPDATE settings SET value='0' "
                    "WHERE key IN ('web_research_enabled','browser_search_enabled')"
                )
                connection.execute(
                    "UPDATE settings SET value='LUMEN RADIO' "
                    "WHERE key='station_name'"
                )
                connection.execute(
                    "UPDATE tracks SET intro='Стара мокова підводка',"
                    "intro_speech='Стара мокова підводка',intro_style='template' "
                    "WHERE id=?",
                    (track_id,),
                )
                connection.execute("PRAGMA user_version=21")

            migrated = Database(path)

            self.assertEqual(migrated.settings()["web_research_enabled"], "1")
            self.assertEqual(migrated.settings()["browser_search_enabled"], "1")
            self.assertEqual(migrated.settings()["station_name"], "Vector Radio")
            self.assertEqual(migrated.track(track_id)["intro"], "")
            self.assertEqual(migrated.track(track_id)["intro_speech"], "")

    def test_user_values_that_match_old_migrations_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "radio.db"
            first = Database(path)
            expected = {
                "host_length": "25",
                "host_sentences": "5",
                "rotation": "chart",
                "chart_name": "LUMEN TOP 100",
                "host_every": "3",
                "talk_probability": "35",
                "story_probability": "70",
                "silence_probability": "7",
                "rubric_probability": "6",
                "ai_max_tokens": "360",
                "station_prompt": (
                    "Темна нічна електроніка, alternative, darkwave, "
                    "atmospheric rock; чергуй відомі та маловідомі треки "
                    "без веселого поп-звучання."
                ),
            }
            first.save_settings(expected)

            reopened = Database(path)

            self.assertEqual(
                {key: reopened.settings()[key] for key in expected},
                expected,
            )

    def test_ui_synchronizes_duplicate_compact_and_advanced_controls(self):
        ui = Path(__file__).resolve().parents[1] / "ui"
        app = (ui / "app.js").read_text(encoding="utf-8")
        index = (ui / "index.html").read_text(encoding="utf-8")
        self.assertIn("function syncSettingControls", app)
        self.assertIn("function collectSettingsValues", app)
        self.assertIn("if (!(key in values)) values[key] = element.value", app)
        markup = app + index
        self.assertNotIn('data-setting="web_research_enabled"', markup)
        self.assertNotIn('data-setting="browser_search_enabled"', markup)
        self.assertIn("Автоматичні web-підводки активні", index)

    def test_database_api_cannot_disable_automatic_web_research(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radio.db")

            database.save_settings({
                "web_research_enabled": "0",
                "browser_search_enabled": "0",
            })

            settings = database.settings()
            self.assertEqual(settings["web_research_enabled"], "1")
            self.assertEqual(settings["browser_search_enabled"], "1")


if __name__ == "__main__":
    unittest.main()
