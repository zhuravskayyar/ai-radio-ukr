import tempfile
import unittest
from pathlib import Path

from backend.db import Database


class UiThemeTests(unittest.TestCase):
    def test_ui_theme_defaults_to_vector_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "radio.db"
            database = Database(path)
            self.assertEqual(database.settings()["ui_theme"], "vector")

            database.save_settings({"ui_theme": "boombox"})

            reopened = Database(path)
            self.assertEqual(reopened.settings()["ui_theme"], "boombox")

    def test_theme_picker_and_boombox_controls_are_wired(self):
        ui = Path(__file__).resolve().parents[1] / "ui"
        index = (ui / "index.html").read_text(encoding="utf-8")
        app = (ui / "app.js").read_text(encoding="utf-8")
        css = (ui / "boombox.css").read_text(encoding="utf-8")

        self.assertIn('data-theme-choice="vector"', index)
        self.assertIn('data-theme-choice="boombox"', index)
        self.assertIn('data-setting="ui_theme"', index)
        for control_id in (
            "boomboxPrev",
            "boomboxPlay",
            "boomboxStop",
            "boomboxNext",
            "boomboxVolume",
            "boomboxTune",
            "boomboxCassette",
            "boomboxCassetteCover",
            "boomboxCassetteCoverImage",
            "boomboxFrequency",
            "boomboxSegmentMarquee",
            "boomboxSignalLed",
        ):
            self.assertIn(f'id="{control_id}"', index)
        self.assertIn("function applyUiTheme", app)
        self.assertIn("function syncPlaybackUi", app)
        self.assertIn("function syncVolumeControls", app)
        self.assertIn("function connectBoomboxAnalyser", app)
        self.assertIn("function startBoomboxEqualizer", app)
        self.assertIn("function setBoomboxSource", app)
        self.assertIn("function youtubeVideoIdForTrack", app)
        self.assertIn("function syncCassetteCover", app)
        self.assertIn("const SIXTEEN_SEGMENTS", app)
        self.assertIn("function renderSixteenSegmentText", app)
        self.assertIn("function advanceBoomboxMarquee", app)
        self.assertIn("function currentPlaybackTrack", app)
        self.assertIn("function isCurrentAudio", app)
        self.assertIn("state.localAudio === audio", app)
        self.assertIn("state.sequenceId !== tuneSequenceId", app)
        self.assertIn("cassette-changing", app)
        self.assertIn("cassette-swapping", app)
        self.assertIn('data-boombox-source="bluetooth"', index)
        self.assertIn("cassette-tape-move", css)
        self.assertIn("frequency-step", css)
        self.assertIn(".sixteen-segment-a1", css)
        self.assertIn(".sixteen-segment-m", css)
        self.assertIn(".sixteen-segment-marquee", css)
        self.assertIn('data-ui-theme="boombox"', css)


if __name__ == "__main__":
    unittest.main()
