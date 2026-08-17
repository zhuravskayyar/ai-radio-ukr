import unittest
from unittest.mock import patch

import radio_pronunciation as pronunciation_module
from radio_pronunciation import PronunciationEngine, RadioPronunciation, cmudict_lookup


class RadioPronunciationTests(unittest.TestCase):
    def setUp(self):
        self.pronouncer = RadioPronunciation()

    def test_the_weeknd_blinding_lights(self):
        self.assertEqual(
            self.pronouncer.convert("The Weeknd — Blinding Lights"),
            "Зе Вікенд — Блайндінґ Лайтс",
        )

    def test_imagine_dragons_believer(self):
        self.assertEqual(
            self.pronouncer.convert("Imagine Dragons — Believer"),
            "Імеджин Дреґонс — Белівер",
        )

    def test_ru_artist_with_number(self):
        self.assertEqual(
            self.pronouncer.convert("Би-2 — Полковнику никто не пишет"),
            "Бі Два — Полковніку нікто не пишет",
        )

    def test_ukrainian_okean_elzy(self):
        self.assertEqual(
            self.pronouncer.convert("Океан Ельзи — Без бою"),
            "Океан Ельзи — Без бою",
        )

    def test_requested_three_tier_examples(self):
        expected = {
            "Ocean": "Оушн",
            "The Ocean": "Зе Оушн",
            "Ocean Eyes": "Оушн Айз",
            "U2 — One": "Ю Ту — Ван",
            "Twenty One Pilots": "Твенті Ван Пайлотс",
        }
        for original, spoken in expected.items():
            with self.subTest(original=original):
                self.assertEqual(self.pronouncer.convert(original), spoken)

    def test_exact_entry_wins_before_cmudict(self):
        calls = []
        engine = PronunciationEngine(
            entries=[{
                "original": "Ocean",
                "spoken": "Оушн",
                "kind": "word",
                "language": "en",
            }],
            cmu_lookup=lambda word: calls.append(word) or "OW1 SH AH0 N",
        )
        result = engine.transcribe_with_meta("Ocean")
        self.assertEqual(result.spoken, "Оушн")
        self.assertEqual(result.source, "exact")
        self.assertEqual(result.confidence, 1.0)
        self.assertEqual(calls, [])

    def test_cmudict_is_used_before_patterns(self):
        engine = PronunciationEngine(
            dictionary_path=None,
            entries=[],
            cmu_lookup=lambda _word: "OW1 SH AH0 N",
        )
        result = engine.transcribe_with_meta("ocean")
        self.assertEqual(result.spoken, "оушен")
        self.assertEqual(result.source, "cmudict")
        self.assertEqual(result.confidence, 0.88)

    def test_patterns_are_a_low_confidence_fallback(self):
        engine = PronunciationEngine(
            dictionary_path=None,
            entries=[],
            cmu_lookup=lambda _word: None,
        )
        result = engine.transcribe_with_meta("nightwave")
        self.assertNotRegex(result.spoken, r"[A-Za-z]")
        self.assertEqual(result.source, "pattern")
        self.assertEqual(result.confidence, 0.55)

    @unittest.skipIf(
        pronunciation_module.pronouncing is None or pronunciation_module.cmudict is None,
        "CMU pronunciation packages are not installed",
    )
    def test_pronouncing_miss_does_not_rebuild_direct_cmudict(self):
        cmudict_lookup.cache_clear()
        with (
            patch.object(
                pronunciation_module.pronouncing,
                "phones_for_word",
                return_value=[],
            ),
            patch.object(
                pronunciation_module.cmudict,
                "dict",
                side_effect=AssertionError("direct CMUdict must not be rebuilt"),
            ),
        ):
            self.assertIsNone(cmudict_lookup("zzvectorradiomiss"))
        cmudict_lookup.cache_clear()

    def test_music_context_variants_are_explicit(self):
        self.assertEqual(self.pronouncer.convert("live"), "лайв")
        self.assertEqual(self.pronouncer.convert("live", context="verb"), "лів")
        self.assertEqual(self.pronouncer.convert("bass", context="fish"), "бас")

    def test_russian_rules_do_not_globally_harden_g(self):
        engine = PronunciationEngine(
            dictionary_path=None,
            entries=[],
            cmu_lookup=lambda _word: None,
        )
        self.assertNotIn("Ґ", engine.transcribe("Группа ещё играет"))


if __name__ == "__main__":
    unittest.main()
