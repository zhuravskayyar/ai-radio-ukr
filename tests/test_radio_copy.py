import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api import (
    DEFAULT_TTS_VOICE,
    HOST_PROMPT_VERSION,
    INTRO_STYLES,
    RadioAPI,
    _canonicalize_verified_track_mentions,
    _contains_weather_reference,
    _contains_unmarked_track_credit,
    _replace_track_markers,
    _replace_story_reveal,
    _ground_story_copy,
    _sounds_scripted,
    _unsupported_story_sentence,
    _ukrainian_copy_warnings,
    contextual_fallback_copy,
    fallback_intro_copy,
    persona_fallback_copy,
    split_spoken_sentences,
    spoken_word_count,
)
from backend.db import Database
from backend.speech_normalizer import (
    audit_ai_pronunciation,
    automatic_pronunciations,
    normalize_for_speech,
    normalize_linguistic,
    number_to_words,
    ordinal_locative,
    validate_phonetic_spelling,
)


class SpeechNormalizerTests(unittest.TestCase):
    def test_default_voice_is_ukrainian_male(self):
        self.assertEqual(DEFAULT_TTS_VOICE, "uk-UA-OstapNeural")

    def test_multilingual_names_get_editable_ukrainian_phonetics(self):
        english = automatic_pronunciations("The Offspring", "The Kids Aren't Alright")
        russian = automatic_pronunciations("Макс Корж", "Зелёный чемодан")
        self.assertNotRegex(english["artist_speech"] + english["title_speech"], r"[A-Za-z]")
        self.assertEqual(english["artist_speech"], "Зе Офспрінг")
        self.assertEqual(english["title_speech"], "Зе Кідз Арнт Олрайт")
        self.assertEqual(english["artist_language"], "en")
        self.assertNotRegex(russian["title_speech"], r"[ёыэъЁЫЭЪ]")
        self.assertEqual(russian["title_language"], "ru")

    def test_curated_pop_artist_pronunciations_are_canonical(self):
        expected = {
            "The Weeknd": "Зе Вікенд",
            "Billie Eilish": "Біллі Айліш",
            "Måneskin": "Манескін",
            "SZA": "Ес-Зі-Ей",
            "Dua Lipa": "Дуа Ліпа",
            "Linkin Park": "Лінкін Парк",
            "Twenty One Pilots": "Твенті Ван Пайлотс",
            "OneRepublic": "Ван Репаблік",
            "Imagine Dragons": "Імеджин Дреґонс",
            "AC/DC": "Ей-Сі Ді-Сі",
            "U2": "Ю Ту",
            "A$AP Rocky": "Ей-Сі-Ей-Пі Роккі",
        }
        for artist, spoken in expected.items():
            with self.subTest(artist=artist):
                self.assertEqual(automatic_pronunciations(artist, "Test")["artist_speech"], spoken)

    def test_tts_symbols_and_golden_names_are_normalized_before_synthesis(self):
        self.assertEqual(
            normalize_for_speech("David Guetta feat. Sia — Titanium!"),
            "Девід Гетта за участю Сіа, Тайтеніем",
        )
        self.assertEqual(
            normalize_for_speech("A$AP Rocky & U2?"),
            "Ей-Сі-Ей-Пі Роккі і Ю Ту",
        )
        self.assertEqual(
            normalize_for_speech("U2 — One"),
            "Ю Ту, Ван",
        )
        self.assertEqual(
            normalize_for_speech("The Ocean — Ocean Eyes"),
            "Зе Оушн, Оушн Айз",
        )
        self.assertEqual(
            normalize_for_speech(
                "A Thousand Suns, Living Things і Medal of Honor: Warfighter"
            ),
            "Ей Саузенд Санс, Лівінґ Сінґз і Медал Ов Онор, Ворфайтер",
        )

    def test_golden_titles_override_local_letter_by_letter_transliteration(self):
        expected = {
            "Grenade": "Гренейд",
            "Titanium": "Тайтеніем",
            "Faded": "Фейдед",
            "Rather Be": "Разер Бі",
            "High Hopes": "Хай Хоупс",
            "CASTLE OF GLASS": "Касл Ов Ґлас",
            "Thnks fr th Mmrs": "Сенкс Фор Зе Меморіз",
            "ZITTI E BUONI": "Цітті Е Буоні",
        }
        for title, spoken in expected.items():
            with self.subTest(title=title):
                self.assertEqual(automatic_pronunciations("Test", title)["title_speech"], spoken)

    def test_ai_pronunciation_is_audited_against_deterministic_rules(self):
        self.assertEqual(
            audit_ai_pronunciation(
                "The Kids Aren't Alright", "Ді кідз арнт олрайт", "title"
            ),
            "Зе Кідз Арнт Олрайт",
        )
        self.assertEqual(
            audit_ai_pronunciation("I Feel Fine", "і філ файн", "title"),
            "ай філ файн",
        )

    def test_neural_phonetics_must_be_plain_ukrainian_spelling(self):
        self.assertEqual(
            validate_phonetic_spelling("The Doors", "Зе Дорз", "artist"),
            "зе Дорз",
        )
        with self.assertRaisesRegex(ValueError, "неукраїнські"):
            validate_phonetic_spelling("Muse", "mjuːz", "artist")
        with self.assertRaisesRegex(ValueError, "неукраїнські"):
            validate_phonetic_spelling("Невідомий артист", "Кіно ё", "artist")

    def test_common_government_errors_are_corrected(self):
        self.assertEqual(
            normalize_linguistic("У ефірі можна приймати участь без пафосу"),
            "в ефірі можна брати участь без пафосу",
        )

    def test_stock_station_identification_is_rejected(self):
        self.assertTrue(_sounds_scripted("Зараз 13:07, ми у Києві. Це RADIO yulichka."))
        self.assertTrue(_sounds_scripted("На чотириста п'ятдесят восьмому місці — [[NEXT_TRACK]]."))
        self.assertTrue(_sounds_scripted("На одинадцятій позиції вмикаємо [[NEXT_TRACK]]."))
        self.assertTrue(_sounds_scripted("Далі — [[NEXT_TRACK]]."))
        self.assertTrue(_sounds_scripted("Іноді музика виростає з реального життя. [[NEXT_TRACK]]."))
        self.assertTrue(_sounds_scripted("А зараз в ефірі [[NEXT_TRACK]]."))
        self.assertTrue(_sounds_scripted("[[NEXT_TRACK]], йдемо далі."))
        self.assertFalse(
            _sounds_scripted(
                "Тоні Айоммі на місці придумав основний риф. [[NEXT_TRACK]]."
            )
        )

    def test_story_evidence_gate_rejects_invented_cinematic_sentence(self):
        verified = [
            "Декстер Голланд повернувся до Ґарден-Ґроув",
            "Історії його знайомих стали темним боком тексту пісні",
        ]
        self.assertIn(
            "порожні вулиці",
            _unsupported_story_sentence(
                "Декстер Голланд повернувся до Ґарден-Ґроув. "
                "Він побачив порожні вулиці, чужий біль і новини. "
                "Тепер [[NEXT_TRACK]].",
                verified,
            ),
        )
        self.assertEqual(
            _unsupported_story_sentence(
                "Декстер Голланд повернувся до Ґарден-Ґроув. "
                "Історії його знайомих стали темним боком тексту. "
                "Тепер [[NEXT_TRACK]].",
                verified,
            ),
            "",
        )

    def test_story_reveal_discards_model_claim_about_next_track(self):
        copy = _replace_story_reveal(
            "Перевірена історія лишається тут. "
            "[[NEXT_TRACK]] — ідеальний перехід, бо після бурі завжди нове.",
            {"story_id": 43, "story_variant": 0},
        )
        self.assertNotIn("ідеальний перехід", copy)
        self.assertNotIn("після бурі", copy)
        self.assertEqual(copy.count("[[NEXT_TRACK]]"), 1)

    def test_story_grounding_keeps_only_verified_wording(self):
        grounded = _ground_story_copy(
            "Декстер Голланд повернувся до Ґарден-Ґроув. "
            "Він побачив порожні вулиці, чужий біль і новини. "
            "[[NEXT_TRACK]].",
            {
                "story_id": 42,
                "story_hook": "Ця пісня почалася з поїздки старим районом",
                "story_data": [
                    "Декстер Голланд повернувся до Ґарден-Ґроув",
                    "Історії його знайомих стали темним боком тексту пісні",
                ],
            },
        )
        self.assertIn("Декстер Голланд повернувся до Ґарден-Ґроув", grounded)
        self.assertNotIn("порожні вулиці", grounded)
        self.assertNotIn("чужий біль", grounded)
        self.assertEqual(grounded.count("[[NEXT_TRACK]]"), 1)
        self.assertTrue(_sounds_scripted("[[NEXT_ARTIST]] — [[NEXT_TITLE]]."))
        self.assertTrue(_sounds_scripted("Чекай<unk><unk> [[NEXT_TRACK]]."))
        self.assertTrue(_sounds_scripted("[[NEXT_TRACK]]. Далі без зайвих слів."))
        self.assertFalse(_sounds_scripted(
            "Тиша нарешті здалася, і її місце займає звук. Далі [[NEXT_TRACK]]."
        ))
        self.assertFalse(_sounds_scripted("У Києві зараз 13:07. Далі — Scorpions."))

    def test_ukrainian_copy_warnings_catch_common_spelling_errors(self):
        warnings = _ukrainian_copy_warnings(
            "У ефірі 11 разів самий кращий трек трек!"
        )

        self.assertIn("евфонія: «у ефірі»", warnings)
        self.assertIn("числа записані цифрами", warnings)
        self.assertIn("повторене слово", warnings)
        broken = _ukrainian_copy_warnings(
            "Надворі тридцять дві градуси,тепер погода йде ровно."
        )
        self.assertIn("узгодження: «дві градуси»", broken)
        self.assertIn("немає пробілу після розділового знака", broken)
        self.assertIn("росіянізм: «ровно»", broken)
        model_errors = _ukrainian_copy_warnings(
            "Гар, коли жарко, музика стає тіном. Гарного слухати."
        )
        self.assertIn("обірване слово на початку репліки", model_errors)
        self.assertIn("росіянізм: «жарко»", model_errors)
        self.assertIn("помилка: «тіном»", model_errors)
        self.assertIn("помилка: «гарного слухати»", model_errors)
        mixed = _ukrainian_copy_warnings("Повітря дихає спокojно. СЛУХАЮ СЕБЕ.")
        self.assertIn("змішані латинські й кириличні літери в одному слові", mixed)
        self.assertIn("службовий текст великими літерами", mixed)
        self.assertIn(
            "калька: «включити радіо»",
            _ukrainian_copy_warnings("Не встиг включити радіо."),
        )
        self.assertFalse(_ukrainian_copy_warnings(
            "В ефірі спокійно світиться наступний трек."
        ))

    def test_copy_audit_rejects_service_commands_and_indirect_weather(self):
        self.assertIn(
            "службова команда моделі",
            _ukrainian_copy_warnings("Треки йдуть хвилями. PLAY."),
        )
        self.assertIn(
            "ненормативна або помилкова словоформа",
            _ukrainian_copy_warnings("Трекі звучать рушне, а дальше буде гучніше."),
        )
        for copy in (
            "Надворі вже темніє.",
            "Тепло й ясно — час для музики.",
            "У цій жарі хочеться тіні.",
        ):
            with self.subTest(copy=copy):
                self.assertTrue(_contains_weather_reference(copy))

    def test_long_collaboration_credit_is_compact_and_deduplicated(self):
        track = {
            "artist": "Witchz, Michael Lattino, Michael Lattino, LIONZDEN, kvltmvthr",
            "title": "The Magick",
        }
        display = _replace_track_markers("Далі — [[NEXT_TRACK]].", track)
        self.assertEqual(display, "Далі — Witchz, Michael Lattino — «The Magick».")

    def test_exact_ai_names_are_canonicalized_but_changed_names_are_not(self):
        track = {"artist": "Kiss", "title": "I Was Made For Lovin' You"}
        self.assertEqual(
            _canonicalize_verified_track_mentions(
                "Далі Kiss — I Was Made For Lovin' You.", track, "artist_and_title"
            ),
            "Далі [[NEXT_ARTIST]] — [[NEXT_TITLE]].",
        )
        self.assertEqual(
            _canonicalize_verified_track_mentions(
                "Далі KISSER — Made For You.", track, "artist_and_title"
            ),
            "Далі KISSER — Made For You.",
        )

    def test_only_literal_track_credit_is_treated_as_unverified(self):
        self.assertTrue(_contains_unmarked_track_credit(
            "Placebo — «This» нібито прогрів ефір. Далі [[NEXT_TRACK]]."
        ))
        self.assertFalse(_contains_unmarked_track_credit(
            "Далі [[NEXT_ARTIST]] — «[[NEXT_TITLE]]»."
        ))

    def test_time_numbers_and_curated_pronunciation(self):
        track = {
            "artist": "Scorpions",
            "title": "Living and Dying",
            "artist_speech": "Скорпіонс",
            "title_speech": "Лівін енд Дайін",
        }
        speech = normalize_for_speech(
            "Далі — Scorpions — «Living and Dying». Старт о 7:30.",
            [track],
        )
        self.assertIn("Скорпіонс", speech)
        self.assertIn("Лівін енд Дайін", speech)
        self.assertIn("сьома тридцять", speech)
        self.assertNotRegex(speech, r"\d")
        self.assertIn("й", speech)

    def test_time_reads_leading_zero_and_midnight_naturally(self):
        self.assertEqual(normalize_for_speech("08:03"), "восьма нуль три")
        self.assertEqual(normalize_for_speech("00:00"), "рівно опівночі")
        self.assertEqual(normalize_for_speech("12:00"), "дванадцята рівно")

    def test_cardinal_year_form_is_readable(self):
        self.assertEqual(number_to_words(2025), "дві тисячі двадцять п'ять")
        self.assertEqual(ordinal_locative(423), "чотириста двадцять третьому")

    def test_track_value_overrides_curated_default(self):
        speech = normalize_for_speech(
            "Scorpions",
            [{
                "artist": "Scorpions",
                "title": "Test",
                "artist_speech": "Скорпієнс",
                "title_speech": "",
            }],
        )
        self.assertEqual(speech, "Скорпієнс")


class RadioCopyTests(unittest.TestCase):
    def test_parallel_acceptance_has_ten_distinct_announce_fallbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            current, track = api.db.tracks()[:2]
            context = api.context_engine.snapshot(current, track)
            copies = []
            for variant in range(10):
                plan = {
                    "content_type": "talk",
                    "structure": "announce",
                    "mention_policy": "artist_and_title",
                    "fallback_variant": variant,
                }
                copy = persona_fallback_copy(track, current, context, plan)
                display = _replace_track_markers(copy, track, current)
                accepted, error = api.content_planner.quality_gate(
                    display,
                    track,
                    context,
                    mention_policy="artist_and_title",
                    structure="announce",
                )
                self.assertTrue(accepted, error)
                copies.append(display)

            self.assertEqual(len(set(copies)), 10)

    def setUp(self):
        self.track = {
            "rank": 11,
            "artist": "Scorpions",
            "title": "Living and Dying (Remastered 2023)",
        }
        self.current = {"artist": "Muse", "title": "Hysteria"}

    def test_all_fallback_styles_are_broadcast_length(self):
        for style in INTRO_STYLES:
            with self.subTest(style=style):
                copy = fallback_intro_copy(
                    self.track,
                    self.current,
                    style,
                    "Перевірений короткий факт",
                )
                display = _replace_track_markers(copy, self.track, self.current)
                self.assertGreaterEqual(spoken_word_count(display), 5)
                self.assertLessEqual(spoken_word_count(display), 35)
                self.assertIn(len(split_spoken_sentences(display)), (1, 2))
                self.assertNotIn("[[NEXT_TRACK]]", display)

    def test_api_returns_separate_display_and_speech_text(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            track = next(
                item for item in api.db.tracks()
                if item["artist"] == "Scorpions"
                and item["title"].startswith("Living and Dying")
            )
            result = api.make_intro(track["id"], style="straight_radio")
            self.assertTrue(result["ok"])
            self.assertEqual(result["provider"], "template")
            self.assertIn("Scorpions", result["display_text"])
            self.assertIn("Скорпіонс", result["speech_text"])
            self.assertIn("Лівін енд Дайін", result["speech_text"])
            self.assertNotEqual(result["display_text"], result["speech_text"])
            self.assertGreaterEqual(spoken_word_count(result["display_text"]), 5)

    def test_make_intro_with_story_content_plan_uses_story_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"host_ai_provider": "parallel"})
            track = next(item for item in api.db.tracks() if item["artist"] == "Scorpions")
            current = next(item for item in api.db.tracks() if item["artist"] == "Muse")
            result = api.make_intro(
                track["id"],
                current_track_id=current["id"],
                content_plan={
                    "content_type": "story",
                    "story_hook": "Це було несподівано",
                    "story_data": [
                        "Початок відбувся просто в студії",
                        "Композицію нарешті включили в ефір",
                    ],
                },
                duration_seconds=15,
                store_track=True,
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["fallback"])
            self.assertEqual(result["provider"], "template")
            self.assertIn("Це було несподівано", result["display_text"])
            self.assertRegex(
                result["display_text"],
                r"(мікрофон переходить|поворот ефіру|На цьому нерві|музичну лінію|простір забирає)",
            )
            self.assertNotIn("А зараз в ефірі", result["display_text"])
            saved_track = api.db.track(track["id"])
            self.assertEqual(saved_track["intro"], result["display_text"])
            self.assertEqual(saved_track["intro_speech"], result["speech_text"])
            self.assertGreaterEqual(spoken_word_count(result["display_text"]), 4)

    def test_two_ai_providers_are_used_for_pronunciation_consensus(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"host_ai_provider": "parallel"})
            track = next(item for item in api.db.tracks() if item["artist"] == "Scorpions")
            providers = [
                {"name": "nvidia", "url": "https://nvidia.invalid", "key": "one", "model": "a"},
                {"name": "secondary", "url": "https://second.invalid", "key": "two", "model": "b"},
            ]

            def fake_completion(spec, *_args):
                return {
                    "provider": spec["name"],
                    "candidate": json.dumps({
                        "artist_speech": "Скорпіонс",
                        "title_speech": "Лівін енд Дайін",
                        "artist_language": "en",
                        "title_language": "en",
                    }, ensure_ascii=False),
                    "error": "",
                }

            with patch.object(api, "_ai_providers", return_value=providers), \
                    patch("backend.api._chat_completion", side_effect=fake_completion) as mocked:
                result = api.generate_track_pronunciation(track["id"])

            self.assertTrue(result["ok"])
            self.assertEqual(mocked.call_count, 2)
            self.assertEqual(result["provider"], "nvidia+secondary")
            self.assertFalse(result["review"])
            self.assertEqual(
                result["track"]["pronunciation_source"],
                "ai:phonetic:nvidia+secondary",
            )

    def test_two_ai_providers_compete_for_a_valid_intro(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"host_ai_provider": "parallel"})
            track = next(item for item in api.db.tracks() if item["artist"] == "Scorpions")
            providers = [
                {"name": "nvidia", "url": "https://nvidia.invalid", "key": "one", "model": "a"},
                {"name": "secondary", "url": "https://second.invalid", "key": "two", "model": "b"},
            ]

            def fake_completion(spec, *_args):
                marker = (
                    "[[NEXT_TRACK]]" if spec["name"] == "nvidia"
                    else "[[NEXT_ARTIST]] — [[NEXT_TITLE]]"
                )
                lead = (
                    "Вечір тримає темп, тож трохи гучності не завадить "
                    if spec["name"] == "nvidia"
                    else "Менше шуму навколо, більше музики, "
                )
                return {
                    "provider": spec["name"],
                    "candidate": f"{lead}без зайвих церемоній, далі {marker}.",
                    "error": "",
                }

            with patch.object(api, "_ai_providers", return_value=providers), \
                    patch("backend.api._chat_completion", side_effect=fake_completion) as mocked, \
                    patch.object(api.content_planner, "quality_gate", return_value=(True, "")):
                result = api.make_intro(
                    track["id"],
                    style="straight_radio",
                    content_plan={"content_type": "talk", "target_seconds": 15},
                    duration_seconds=15,
                    store_track=False,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(mocked.call_count, 4)
            self.assertEqual(result["candidate_count"], 4)
            self.assertIn(result["selected_variant"], {1, 2})
            self.assertTrue(result["spelling_checked"])
            self.assertFalse(result["fallback"], result["provider_error"])
            self.assertIn(result["provider"], {"nvidia", "secondary"})

    def test_missing_track_marker_gets_one_ai_editor_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"intro_variants_per_provider": "1"})
            track = next(item for item in api.db.tracks() if item["artist"] == "Scorpions")
            provider = {
                "name": "secondary", "url": "https://second.invalid",
                "key": "two", "model": "deepseek/test",
            }
            candidates = iter([
                "Ти ще тут, і це вже хороший знак для вечірнього ефіру.",
                "Ти ще тут, і це вже хороший знак. Далі [[NEXT_TRACK]].",
            ])

            def fake_completion(spec, *_args):
                return {
                    "provider": spec["name"],
                    "candidate": next(candidates),
                    "error": "",
                }

            with patch.object(api, "_ai_providers", return_value=[provider]), \
                    patch("backend.api._chat_completion", side_effect=fake_completion) as mocked:
                result = api.make_intro(
                    track["id"],
                    style="straight_radio",
                    content_plan={
                        "content_type": "talk",
                        "style": "straight_radio",
                        "target_seconds": 15,
                        "mention_policy": "artist_and_title",
                    },
                    duration_seconds=15,
                    store_track=False,
                )

            self.assertEqual(mocked.call_count, 2)
            self.assertFalse(result["fallback"], result["provider_error"])
            self.assertEqual(result["provider"], "secondary")
            self.assertIn("Scorpions", result["display_text"])
            self.assertTrue(any(
                item.get("ok") and item.get("repaired")
                for item in result["provider_diagnostics"]
            ))

    def test_unplanned_weather_is_removed_by_ai_editor_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"intro_variants_per_provider": "1"})
            track = next(item for item in api.db.tracks() if item["artist"] == "Scorpions")
            provider = {
                "name": "secondary", "url": "https://second.invalid",
                "key": "two", "model": "deepseek/test",
            }
            candidates = iter([
                "Спека плавить асфальт, але [[NEXT_TRACK]] уже поруч.",
                "Вечір тримає рівний нерв, а далі [[NEXT_TRACK]].",
            ])

            def fake_completion(spec, *_args):
                return {
                    "provider": spec["name"],
                    "candidate": next(candidates),
                    "error": "",
                }

            with patch.object(api, "_ai_providers", return_value=[provider]), \
                    patch("backend.api._chat_completion", side_effect=fake_completion):
                result = api.make_intro(
                    track["id"],
                    style="straight_radio",
                    content_plan={
                        "content_type": "talk",
                        "style": "straight_radio",
                        "target_seconds": 12,
                        "mention_policy": "artist_and_title",
                        "may_say_weather": False,
                    },
                    duration_seconds=12,
                    store_track=False,
                )

            self.assertFalse(result["fallback"], result["provider_error"])
            self.assertNotIn("спека", result["display_text"].casefold())
            self.assertTrue(any(
                item.get("error") == "погода не запланована для цієї підводки"
                for item in result["provider_diagnostics"]
            ))

    def test_unplanned_exact_time_is_removed_by_ai_editor_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"intro_variants_per_provider": "1"})
            track = next(item for item in api.db.tracks() if item["artist"] == "Scorpions")
            context = api.context_engine.snapshot(None, track)
            context["time"]["time"] = "19:58"
            provider = {
                "name": "secondary", "url": "https://second.invalid",
                "key": "two", "model": "deepseek/test",
            }
            candidates = iter([
                "Уже дев'ятнадцята п'ятдесят вісім, а далі [[NEXT_TRACK]].",
                "Вечір не поспішає, а далі [[NEXT_TRACK]].",
            ])

            def fake_completion(spec, *_args):
                return {
                    "provider": spec["name"],
                    "candidate": next(candidates),
                    "error": "",
                }

            with patch.object(api, "_ai_providers", return_value=[provider]), \
                    patch("backend.api._chat_completion", side_effect=fake_completion):
                result = api.make_intro(
                    track["id"],
                    generation_context=context,
                    style="straight_radio",
                    content_plan={
                        "content_type": "talk",
                        "style": "straight_radio",
                        "target_seconds": 12,
                        "mention_policy": "artist_and_title",
                        "must_say_time": False,
                    },
                    duration_seconds=12,
                    store_track=False,
                )

            self.assertFalse(result["fallback"], result["provider_error"])
            self.assertNotIn("дев'ятнадцята п'ятдесят вісім", result["display_text"].casefold())
            self.assertTrue(any(
                item.get("error") == "час не запланований для цієї підводки"
                for item in result["provider_diagnostics"]
            ))

    def test_unmarked_hallucinated_track_is_removed_by_ai_editor_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"intro_variants_per_provider": "1"})
            track = next(item for item in api.db.tracks() if item["artist"] == "Scorpions")
            provider = {
                "name": "secondary", "url": "https://second.invalid",
                "key": "two", "model": "deepseek/test",
            }
            candidates = iter([
                "Placebo — «This» нібито вже прогрів ефір. Далі [[NEXT_TRACK]].",
                "Ефір уже прогрітий, а далі [[NEXT_TRACK]].",
            ])

            def fake_completion(spec, *_args):
                return {
                    "provider": spec["name"],
                    "candidate": next(candidates),
                    "error": "",
                }

            with patch.object(api, "_ai_providers", return_value=[provider]), \
                    patch("backend.api._chat_completion", side_effect=fake_completion):
                result = api.make_intro(
                    track["id"],
                    style="straight_radio",
                    content_plan={
                        "content_type": "talk",
                        "style": "straight_radio",
                        "target_seconds": 12,
                        "mention_policy": "artist_and_title",
                    },
                    duration_seconds=12,
                    store_track=False,
                )

            self.assertFalse(result["fallback"], result["provider_error"])
            self.assertNotIn("Placebo", result["display_text"])
            self.assertTrue(any(
                item.get("error") == "сторонній трек названо без перевіреного маркера"
                for item in result["provider_diagnostics"]
            ))

    def test_parallel_host_selects_quality_over_provider_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"host_ai_provider": "secondary"})
            track = next(item for item in api.db.tracks() if item["artist"] == "Scorpions")
            providers = [
                {"name": "nvidia", "url": "https://nvidia.invalid", "key": "one", "model": "nemotron"},
                {"name": "secondary", "url": "https://second.invalid", "key": "two", "model": "deepseek/test"},
            ]

            def fake_completion(spec, *_args):
                candidate = (
                    "Вечір тримає темп. Тепер без довгих пояснень звучить [[NEXT_TRACK]]."
                    if spec["name"] == "nvidia" else
                    "Спокійний вечірній нерв природно підхоплює [[NEXT_TRACK]]."
                )
                return {"provider": spec["name"], "candidate": candidate, "error": ""}

            with patch.object(api, "_ai_providers", return_value=providers), \
                    patch("backend.api._chat_completion", side_effect=fake_completion):
                result = api.make_intro(
                    track["id"],
                    style="straight_radio",
                    content_plan={
                        "content_type": "talk",
                        "style": "straight_radio",
                        "target_seconds": 15,
                        "mention_policy": "artist_and_title",
                    },
                    duration_seconds=15,
                    store_track=False,
                )

            self.assertFalse(result["fallback"], result["provider_error"])
            self.assertEqual(result["provider"], "nvidia")
            self.assertGreater(result["quality_score"], 0)

    def test_intro_competition_prefers_cleaner_ukrainian_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            api.db.save_settings({"host_ai_provider": "parallel"})
            track = next(item for item in api.db.tracks() if item["artist"] == "Scorpions")
            providers = [
                {"name": "nvidia", "url": "https://nvidia.invalid", "key": "one", "model": "a"},
                {"name": "secondary", "url": "https://second.invalid", "key": "two", "model": "b"},
            ]

            def fake_completion(spec, *_args):
                candidate = (
                    "У ефірі на 11 місці тихо світиться [[NEXT_TRACK]]."
                    if spec["name"] == "nvidia"
                    else "Тиша вечора спокійно підсвічує [[NEXT_TRACK]]."
                )
                return {
                    "provider": spec["name"],
                    "candidate": candidate,
                    "error": "",
                }

            with patch.object(api, "_ai_providers", return_value=providers), \
                    patch("backend.api._chat_completion", side_effect=fake_completion), \
                    patch.object(api.content_planner, "quality_gate", return_value=(True, "")):
                result = api.make_intro(
                    track["id"],
                    style="straight_radio",
                    content_plan={
                        "content_type": "talk",
                        "style": "straight_radio",
                        "target_seconds": 12,
                        "word_min": 5,
                        "word_max": 30,
                    },
                    duration_seconds=12,
                    store_track=False,
                )

            self.assertTrue(result["ok"])
            self.assertFalse(result["fallback"], result["provider_error"])
            self.assertEqual(result["provider"], "secondary")
            diagnostics = {
                item["provider"]: item for item in result["provider_diagnostics"]
            }
            self.assertFalse(diagnostics["nvidia"]["ok"])
            self.assertEqual(diagnostics["nvidia"]["error"], "заскриптована підводка")
            self.assertFalse(diagnostics["secondary"]["warnings"])

    def test_station_time_has_factual_offline_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            track = api.db.tracks()[0]
            context = {
                "time": {"time": "08:03", "daypart": "morning"},
                "weather": {"available": False},
                "station": {
                    "name": "LUMEN RADIO",
                    "city": "Київ",
                    "city_locative": "Києві",
                },
                "personality": {},
            }
            result = api.make_intro(
                track["id"],
                generation_context=context,
                content_plan={
                    "content_type": "top_of_hour",
                    "style": "straight_radio",
                    "target_seconds": 8,
                    "must_say_time": True,
                },
                duration_seconds=8,
                variant="short",
                store_track=False,
            )
            self.assertIn("08:03", result["display_text"])
            self.assertNotRegex(result["speech_text"], r"\d")
            self.assertLessEqual(result["display_text"].count("."), 2)
            self.assertNotIn("Продовжуємо без зайвої паузи", result["display_text"])

    def test_weather_fallback_uses_only_supplied_cache_values(self):
        copy = contextual_fallback_copy(
            self.track,
            self.current,
            "atmospheric",
            {
                "time": {"daypart": "day"},
                "weather": {
                    "available": True,
                    "temperature": 7.2,
                    "condition": "хмарно",
                    "rain_soon": True,
                },
                "station": {"city_locative": "Києві"},
            },
            {"content_type": "weather_change"},
        )
        self.assertIn("хмарно", copy)
        self.assertIn("+7°", copy)
        self.assertIn("можливий дощ", copy)
        self.assertLessEqual(copy.count("."), 2)

    def test_music_story_fallback_keeps_verified_hook_within_ten_seconds(self):
        copy = contextual_fallback_copy(
            self.track,
            self.current,
            "music_story",
            {"time": {"daypart": "day"}, "station": {}},
            {
                "content_type": "story",
                "story_hook": "Цю пісню могли не записати",
                "story_data": [
                    "Риф з'явився наприкінці сесії",
                    "Учасники спочатку відмовлялися його грати",
                    "Композицію все ж залишили",
                ],
                "story_duration_class": "normal",
            },
        )
        self.assertIn("Цю пісню могли не записати", copy)
        self.assertIn("[[NEXT_TRACK]]", copy)
        self.assertNotIn("студії", copy)

    def test_prompt_upgrade_removes_only_reusable_old_host_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = RadioAPI(root)
            track = first.db.tracks()[0]
            next_track = first.db.tracks()[1]
            first.db.update_track(
                track["id"],
                intro="Стара підводка",
                intro_speech="Стара підводка",
                intro_style="straight_radio",
            )
            first.db.add_history({
                "current_track_id": None,
                "next_track_id": track["id"],
                "content_type": "talk",
                "style": "straight_radio",
                "opening": "Стара підводка",
                "display_text": "Стара підводка",
            })
            first.db.save_transition({
                "current_track_id": track["id"],
                "next_track_id": next_track["id"],
                "status": "ready",
                "display_full": "Стара підготовлена підводка",
            })
            first.db.save_settings({"host_prompt_version": "legacy"})

            upgraded = RadioAPI(root)

            refreshed = upgraded.db.track(track["id"])
            self.assertEqual(refreshed["intro"], "")
            self.assertEqual(refreshed["intro_speech"], "")
            self.assertEqual(upgraded.db.recent_history(), [])
            self.assertIsNone(
                upgraded.db.transition(track["id"], next_track["id"])
            )
            self.assertEqual(
                upgraded.db.settings()["host_prompt_version"],
                HOST_PROMPT_VERSION,
            )

    def test_track_pronunciation_survives_chart_reimport(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            track = api.db.tracks()[0]
            result = api.set_track_pronunciation(
                track["id"], "Тестовий артист", "Тестова назва"
            )
            self.assertTrue(result["ok"])
            api.db.replace_tracks([
                {"rank": 7, "artist": track["artist"], "title": track["title"]}
            ])
            restored = api.db.tracks()[0]
            self.assertEqual(restored["artist_speech"], "Тестовий артист")
            self.assertEqual(restored["title_speech"], "Тестова назва")
            self.assertEqual(restored["previous_rank"], track["rank"])
            self.assertEqual(restored["rank"], 7)

    def test_merge_marks_only_unresolved_latin_names_for_review(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "radio.db")
            db.merge_tracks([
                {"rank": 1, "artist": "Scorpions", "title": "Українська назва"},
                {"rank": 2, "artist": "Unknown Artist", "title": "Unknown Song"},
            ])
            tracks = db.tracks()
            curated = next(item for item in tracks if item["artist"] == "Scorpions")
            unresolved = next(item for item in tracks if item["artist"] == "Unknown Artist")
            self.assertEqual(curated["pronunciation_review"], 0)
            self.assertEqual(unresolved["pronunciation_review"], 1)

    def test_explicit_original_spelling_counts_as_reviewed_pronunciation(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            track = next(item for item in api.db.tracks() if item["artist"] == "Scorpions")
            result = api.set_track_pronunciation(
                track["id"], track["artist"], track["title"]
            )
            self.assertEqual(result["track"]["pronunciation_review"], 0)
            self.assertEqual(result["track"]["artist_speech"], track["artist"])


if __name__ == "__main__":
    unittest.main()
