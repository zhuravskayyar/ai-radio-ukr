import tempfile
import unittest
from pathlib import Path

from backend.db import Database


class SettingsPersistenceTests(unittest.TestCase):
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
        app = (
            Path(__file__).resolve().parents[1] / "ui" / "app.js"
        ).read_text(encoding="utf-8")
        self.assertIn("function syncSettingControls", app)
        self.assertIn("function collectSettingsValues", app)
        self.assertIn("if (!(key in values)) values[key] = element.value", app)


if __name__ == "__main__":
    unittest.main()
