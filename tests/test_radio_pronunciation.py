import unittest

from radio_pronunciation import RadioPronunciation


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
            "Імеджин Дреґонс — Беливер",
        )

    def test_ru_artist_with_number(self):
        self.assertEqual(
            self.pronouncer.convert("Би-2 — Полковнику никто не пишет"),
            "Бі два — Полковніку нікто не пишет",
        )

    def test_ukrainian_okean_elzy(self):
        self.assertEqual(
            self.pronouncer.convert("Океан Ельзи — Без бою"),
            "Океан Ельзи — Без бою",
        )


if __name__ == "__main__":
    unittest.main()
