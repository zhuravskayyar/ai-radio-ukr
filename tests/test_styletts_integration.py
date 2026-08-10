import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.api import RadioAPI
from backend.db import Database
from backend.tts_styletts import (
    _normalize_text,
    _prune_single_speaker_model,
    _to_speed,
    _write_wav,
)


class StyleTTSHelpersTests(unittest.TestCase):
    def test_fixed_voice_model_releases_unused_training_modules(self):
        class FakeModel:
            pass

        model = FakeModel()
        for attribute in (
            "weights", "diffusion", "predictor_encoder", "style_encoder",
            "sampler", "to_mel", "noise",
        ):
            setattr(model, attribute, object())
        model.decoder = object()

        _prune_single_speaker_model(model)

        self.assertTrue(hasattr(model, "decoder"))
        self.assertFalse(hasattr(model, "weights"))
        self.assertFalse(hasattr(model, "diffusion"))

    def test_radio_rate_maps_to_supported_styletts_speed(self):
        self.assertEqual(_to_speed("+4%"), 1.04)
        self.assertEqual(_to_speed("-2%"), 0.98)
        self.assertEqual(_to_speed("+90%"), 1.3)
        self.assertEqual(_to_speed("invalid"), 1.0)

    def test_text_normalization_handles_broadcast_dashes(self):
        self.assertEqual(_normalize_text("Остап — в ефірі"), "Остап: в ефірі.")

    def test_wav_writer_creates_real_24khz_mono_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.wav"
            _write_wav(path, np.zeros(2400, dtype=np.float32))
            with wave.open(str(path), "rb") as audio:
                self.assertEqual(audio.getframerate(), 24_000)
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getsampwidth(), 2)


class StyleTTSRadioIntegrationTests(unittest.TestCase):
    def test_styletts_is_enabled_for_new_radio_databases(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Database(Path(directory) / "radio.db").settings()
        self.assertEqual(settings["use_styletts"], "1")

    def test_radio_uses_separate_wav_cache_and_correct_data_mime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = RadioAPI(root)

            def fake_styletts(text, voice, rate, out_path):
                del text, voice, rate
                _write_wav(out_path, np.zeros(2400, dtype=np.float32))
                return True

            with patch(
                "backend.tts_styletts.synthesize_styletts",
                side_effect=fake_styletts,
            ) as synthesize:
                first = api._speech_asset("Локальний голос працює.")
                second = api._speech_asset("Локальний голос працює.")
                audio = api._audio_data(first["path"])

            self.assertTrue(first["ok"])
            self.assertEqual(first["provider"], "styletts2")
            self.assertTrue(first["path"].endswith(".wav"))
            self.assertEqual(first["duration_ms"], 100)
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            self.assertTrue(audio.startswith("data:audio/wav;base64,"))
            synthesize.assert_called_once()

    def test_switching_tts_engine_invalidates_prepared_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            current, next_track = api.db.tracks()[:2]
            api.db.save_transition({
                "current_track_id": current["id"],
                "next_track_id": next_track["id"],
                "status": "ready",
                "audio_full_path": "cache/tts/old.mp3",
            })
            self.assertIsNotNone(api.db.transition(current["id"], next_track["id"]))

            api.save_settings({"use_styletts": "0"})

            self.assertIsNone(api.db.transition(current["id"], next_track["id"]))


if __name__ == "__main__":
    unittest.main()
