import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure the repository root is on sys.path when running this test file directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api import (
    RadioAPI,
    _openrouter_affordable_tokens,
    _sounds_scripted,
    spoken_word_count,
)


class SecondaryApiTests(unittest.TestCase):
    def test_api_txt_import_validates_stores_and_masks_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            nvidia = "nvapi-1234567890abcdefghijkl"
            openrouter = "sk-or-v1-1234567890abcdefghijkl"
            youtube = "AIza1234567890abcdefghijklmnop"
            result = api.import_api_text(
                f"NVIDIA_API_KEY={nvidia}\n"
                f"OPENROUTER_API_KEY={openrouter}\n"
                f"YOUTUBE_API_KEY={youtube}\n"
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["providers"], ["NVIDIA", "OpenRouter", "YouTube"])
            stored = api.db.settings()
            self.assertEqual(stored["nvidia_api_key"], nvidia)
            self.assertEqual(stored["secondary_api_key"], openrouter)
            self.assertEqual(stored["youtube_api_key"], youtube)
            self.assertEqual(stored["secondary_api_enabled"], "1")
            self.assertEqual(result["settings"]["nvidia_api_key"], "")
            self.assertEqual(result["settings"]["secondary_api_key"], "")
            self.assertTrue(result["settings"]["nvidia_key_detected"])
            self.assertTrue(result["settings"]["secondary_key_detected"])

    def test_api_txt_import_requires_completion_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            result = api.import_api_text("YOUTUBE_API_KEY=AIza1234567890abcdefghijklmnop")
            self.assertFalse(result["ok"])
            self.assertIn("NVIDIA", result["error"])
            self.assertFalse(api.db.settings()["youtube_api_key"])

    def test_saving_style_with_blank_masked_key_preserves_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            try:
                api.db.save_settings({"nvidia_api_key": "nvapi-existing-secret"})
                result = api.save_settings({
                    "nvidia_api_key": "",
                    "station_prompt": "Сучасний український alternative rock",
                })
                self.assertTrue(result["ok"])
                self.assertEqual(
                    api.db.settings()["nvidia_api_key"], "nvapi-existing-secret",
                )
            finally:
                api.shutdown()

    def test_openrouter_credit_error_extracts_affordable_token_limit(self):
        self.assertEqual(
            _openrouter_affordable_tokens(
                "This request requires more credits. You requested up to 900 tokens, "
                "but can only afford 140."
            ),
            140,
        )
        self.assertIsNone(_openrouter_affordable_tokens("rate limit exceeded"))

    def test_credit_error_disables_only_that_provider_until_key_reimport(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            openrouter = {
                "name": "secondary",
                "provider_type": "secondary",
                "url": "https://openrouter.ai/api/v1/chat/completions",
                "key": "sk-or-v1-1234567890abcdefghijkl",
                "model": "deepseek/test",
            }
            failure = {
                "provider": "secondary",
                "candidate": "",
                "error": "secondary HTTP 402: technical credit payload",
                "error_kind": "credit",
                "status_code": 402,
            }
            with patch("backend.api._chat_completion", return_value=failure) as completion:
                first = api._provider_chat_completion(
                    openrouter, "system", "request", 0, 1, 100,
                )
                second = api._provider_chat_completion(
                    openrouter, "system", "request", 0, 1, 100,
                )

            self.assertEqual(completion.call_count, 1)
            self.assertNotIn("technical credit payload", first["error"])
            self.assertTrue(second["skipped"])
            self.assertEqual(api.provider_health(), [])

            imported = api.import_api_text(
                "OPENROUTER_API_KEY=sk-or-v1-1234567890abcdefghijkl"
            )
            self.assertTrue(imported["ok"])
            provider = next(
                item for item in api.provider_health()
                if item["label"] == "OpenRouter"
            )
            self.assertEqual(provider["state"], "ready")

    def test_failed_provider_does_not_block_a_working_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            providers = [
                {
                    "name": "secondary", "provider_type": "secondary",
                    "url": "https://openrouter.ai/api/v1/chat/completions",
                    "key": "openrouter", "model": "deepseek/test",
                },
                {
                    "name": "nvidia", "provider_type": "nvidia",
                    "url": "https://integrate.api.nvidia.com/v1/chat/completions",
                    "key": "nvidia", "model": "nvidia/test",
                },
            ]

            def completion(spec, *_args):
                if spec["name"] == "secondary":
                    return {
                        "provider": "secondary", "candidate": "",
                        "error": "secondary HTTP 402: long provider response",
                        "error_kind": "credit", "status_code": 402,
                    }
                return {
                    "provider": "nvidia",
                    "candidate": json.dumps({
                        "tracks": [
                            {"artist": "Working Artist", "title": "Working Track"},
                        ],
                        "similarTracks": [], "targetMood": [], "avoid": [],
                    }),
                    "error": "",
                }

            with patch("backend.api._chat_completion", side_effect=completion):
                plan = api._queue_search_plan(
                    {"station_prompt": "alternative rock"}, providers=providers,
                )

            self.assertEqual(plan["provider"], "nvidia")
            diagnostics = {
                item["provider"]: item for item in plan["provider_diagnostics"]
            }
            self.assertFalse(diagnostics["secondary"]["ok"])
            self.assertNotIn("long provider response", diagnostics["secondary"]["error"])
            self.assertTrue(diagnostics["nvidia"]["ok"])

    def test_pasted_nvidia_examples_become_parallel_dj_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api.txt").write_text(
                "nvapi-main-key\nsk-or-test-key\n",
                encoding="utf-8",
            )
            (root / "apitest.txt").write_text(
                '''client = OpenAI(base_url="https://integrate.api.nvidia.com/v1",
api_key="nvapi-extra-one")
client = OpenAI(base_url="https://integrate.api.nvidia.com/v1",
api_key="nvapi-extra-two")
client = OpenAI(base_url="https://integrate.api.nvidia.com/v1",
api_key="nvapi-extra-three")''',
                encoding="utf-8",
            )
            api = RadioAPI(root)
            api.db.save_settings({"secondary_api_enabled": "1"})

            providers = api._ai_providers()
            track_providers = api._ai_providers_for_tracks()
            intro_providers = api._ai_providers_for_intro()

            self.assertEqual(len(providers), 5)
            self.assertEqual(
                [item["name"] for item in providers if item["provider_type"] == "nvidia"],
                ["nvidia", "nvidia-2", "nvidia-3", "nvidia-4"],
            )
            self.assertEqual(len(track_providers), 5)
            self.assertEqual(len(intro_providers), 2)
            self.assertIn("deepseek", intro_providers[0]["model"].casefold())

    def test_secondary_provider_autodetects_openrouter_key_from_api_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api.txt").write_text("sk-or-test-key\n", encoding="utf-8")

            api = RadioAPI(root)
            api.db.save_settings({
                "secondary_api_enabled": "1",
                "secondary_api_url": "",
                "secondary_api_key": "",
                "secondary_model": "",
            })

            providers = api._ai_providers()

            self.assertTrue(any(
                provider["name"] == "secondary"
                and provider["url"] == "https://openrouter.ai/api/v1/chat/completions"
                and provider["key"] == "sk-or-test-key"
                and provider["model"] == "deepseek/deepseek-v4-flash"
                for provider in providers
            ))

    def test_primary_ai_provider_can_be_swapped_to_secondary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api.txt").write_text(
                "sk-or-test-key\n",
                encoding="utf-8",
            )

            api = RadioAPI(root)
            api.db.save_settings({
                "primary_ai_provider": "secondary",
                "secondary_api_enabled": "1",
                "secondary_api_url": "",
                "secondary_api_key": "",
                "secondary_model": "",
                "nvidia_api_key": "nvapi-fake-key",
                "nvidia_model": "nvidia/nemotron-3-super-120b-a12b",
            })

            providers = api._ai_providers()

            self.assertEqual(len(providers), 2)
            self.assertEqual(providers[0]["name"], "secondary")
            self.assertEqual(providers[1]["name"], "nvidia")

    def test_deepseek_secondary_provider_is_preferred_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api.txt").write_text("sk-or-test-key\n", encoding="utf-8")

            api = RadioAPI(root)
            api.db.save_settings({
                "secondary_api_enabled": "1",
                "secondary_api_url": "",
                "secondary_api_key": "",
                "secondary_model": "deepseek/deepseek-v4-flash-latest",
                "nvidia_api_key": "nvapi-fake-key",
                "nvidia_model": "nvidia/nemotron-3-super-120b-a12b",
            })

            providers = api._ai_providers()

            self.assertEqual(len(providers), 2)
            self.assertEqual(providers[0]["name"], "secondary")
            self.assertEqual(providers[1]["name"], "nvidia")
            self.assertIn("deepseek", providers[0]["model"].casefold())

    def test_verify_deepseek_response_returns_ok_for_configured_deepseek(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api.txt").write_text("sk-or-test-key\n", encoding="utf-8")

            api = RadioAPI(root)
            api.db.save_settings({
                "secondary_api_enabled": "1",
                "secondary_api_url": "",
                "secondary_api_key": "",
                "secondary_model": "deepseek/deepseek-v4-flash-latest",
            })

            with patch("backend.api._chat_completion", return_value={
                "provider": "secondary",
                "candidate": '{"ok": true}',
                "error": "",
            }):
                result = api.verify_deepseek_response()

            self.assertTrue(result["ok"])
            self.assertEqual(result["provider"], "secondary")
            self.assertIn("deepseek", result["model"].casefold())

    def test_verify_deepseek_response_reports_not_configured_without_deepseek(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api.txt").write_text("sk-or-test-key\n", encoding="utf-8")

            api = RadioAPI(root)
            api.db.save_settings({
                "secondary_api_enabled": "1",
                "secondary_api_url": "",
                "secondary_api_key": "",
                "secondary_model": "openai/gpt-4o-mini",
            })

            result = api.verify_deepseek_response()

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "Deepseek provider not configured")

    def test_generate_top_tracks_with_intros_returns_ten_tracks_and_intros_for_russian_rock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = RadioAPI(root)

            fake_plan = {
                "tracks": [
                    {"artist": f"Artist {i}", "title": f"Track {i}", "reason": "русский рок"}
                    for i in range(1, 11)
                ],
                "provider": "nvidia",
            }

            def fake_make_intro(track_id, *args, **kwargs):
                return {
                    "display_text": f"Intro for track {track_id}",
                    "provider": "deepseek",
                }

            with patch.object(api, "_queue_search_plan", return_value=fake_plan), \
                    patch.object(api, "make_intro", side_effect=fake_make_intro):
                result = api.generate_top_tracks_with_intros(
                    "русский рок",
                    limit=10,
                    intro_style="straight_radio",
                    intro_seconds=15,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["station_style"], "русский рок")
            self.assertEqual(len(result["tracks"]), 10)
            self.assertTrue(all(item["artist"] == f"Artist {index}" for index, item in enumerate(result["tracks"], start=1)))
            self.assertTrue(all(item["title"] == f"Track {index}" for index, item in enumerate(result["tracks"], start=1)))
            self.assertTrue(all(item["intro"].startswith("Intro for track ") for item in result["tracks"]))
            self.assertTrue(all(item["provider"] == "deepseek" for item in result["tracks"]))
            self.assertEqual(result["source_provider"], "nvidia")

    def test_modern_alt_rock_batch_returns_ten_tracks_and_non_scripted_intros(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = RadioAPI(root)
            providers = [{"name": "nvidia", "url": "https://nvidia.invalid", "key": "one", "model": "a"}]
            plan_tracks = [
                ("The Hardkiss", "Журавлі"),
                ("Один в каное", "У мене немає дому"),
                ("Latexfauna", "Bounty"),
                ("Vivienne Mort", "Готика"),
                ("SadSvit", "Касета"),
                ("паліндром", "Не дожив"),
                ("Дайте танк (!)", "Мы"),
                ("Буерак", "Спортивные очки"),
                ("Молчат Дома", "Судно"),
                ("Пошлая Молли", "Любимая песня твоей сестры"),
            ]
            intro_variants = [
                "Свіжий нерв сцени без музейного пилу тримає темп і лишає достатньо повітря між гітарами голосом та наступною паузою тому далі [[NEXT_TRACK]].",
                "Цей ефір краще працює без пилу старих плакатів і з живим рухом у колонках тож на черзі [[NEXT_TRACK]] для нормального темпу.",
                "Не копаємося в архіві заради архіву а тримаємо ближчий пульс сцени і даємо простір для [[NEXT_TRACK]] просто зараз.",
                "Коли гітари не про ностальгію а про сьогоднішній нерв ефіру логічно поставити [[NEXT_TRACK]] без довгого вступу.",
                "Тут потрібен не музейний експонат а трек який ще дихає в актуальному плейлисті тому рівно в цю щілину входить [[NEXT_TRACK]].",
                "Залишаємо старі канони на полиці а в ефірі тримаємо сучасніший тиск і акуратний перехід до [[NEXT_TRACK]].",
                "Ніч не просить класичної лекції про рок вона просить живого ходу вперед і зараз цю роботу бере [[NEXT_TRACK]].",
                "Якщо вже збирати альтернативний рок то без автоматичного поклоніння архівам і з нормальним фокусом на [[NEXT_TRACK]].",
                "Після кількох темних відтінків хочеться не пафосу а точного удару в настрій і цим ударом буде [[NEXT_TRACK]].",
                "Ефір рухається без музейної екскурсії та зайвого пилу на підсилювачах тож наступну лінію проводить [[NEXT_TRACK]].",
            ]
            intro_index = {"value": 0}

            def fake_completion(spec, system_prompt, *_args):
                if '"tracks"' in system_prompt:
                    return {
                        "provider": spec["name"],
                        "candidate": json.dumps({
                            "tracks": [
                                {"artist": artist, "title": title, "reason": "modern alt"}
                                for artist, title in plan_tracks
                            ],
                            "similarTracks": [],
                            "targetMood": ["modern", "alt rock"],
                            "avoid": ["legacy"],
                        }, ensure_ascii=False),
                        "error": "",
                    }
                candidate = intro_variants[intro_index["value"] % len(intro_variants)]
                intro_index["value"] += 1
                return {"provider": spec["name"], "candidate": candidate, "error": ""}

            with patch.object(api, "_ai_providers", return_value=providers), \
                    patch("backend.api._chat_completion", side_effect=fake_completion):
                result = api.generate_top_tracks_with_intros(
                    "сучасний альт рок рос і укр рок без старого канону",
                    limit=10,
                    intro_style="straight_radio",
                    intro_seconds=10,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["tracks"]), 10)
            self.assertEqual(
                [(item["artist"], item["title"]) for item in result["tracks"]],
                plan_tracks,
            )
            self.assertTrue(all(item["provider"] == "nvidia" for item in result["tracks"]))
            self.assertTrue(all(not _sounds_scripted(item["intro"]) for item in result["tracks"]))
            self.assertTrue(all(spoken_word_count(item["intro"]) >= 18 for item in result["tracks"]))

    def test_benchmark_ai_providers_reports_best_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = RadioAPI(root)
            providers = [
                {"name": "nvidia", "url": "https://nvidia.invalid", "key": "one", "model": "a"},
                {"name": "secondary", "url": "https://second.invalid", "key": "two", "model": "b"},
            ]

            def fake_completion(spec, system_prompt, *_args):
                if '"tracks"' in system_prompt:
                    tracks = (
                        [{"artist": "Short Artist", "title": "Short Song", "reason": "ok"}]
                        if spec["name"] == "nvidia"
                        else [
                            {"artist": f"Artist {index}", "title": f"Track {index}", "reason": "fits"}
                            for index in range(1, 11)
                        ]
                    )
                    return {
                        "provider": spec["name"],
                        "candidate": json.dumps({
                            "tracks": tracks,
                            "similarTracks": [],
                            "targetMood": ["night"],
                            "avoid": ["cover"],
                        }, ensure_ascii=False),
                        "error": "",
                    }
                return {
                    "provider": spec["name"],
                    "candidate": (
                        "У ефірі на 11 місці [[NEXT_TRACK]]."
                        if spec["name"] == "nvidia"
                        else "В ефірі спокійно світиться [[NEXT_TRACK]] для цього вечора."
                    ),
                    "error": "",
                }

            with patch.object(api, "_ai_providers", return_value=providers), \
                    patch("backend.api._chat_completion", side_effect=fake_completion), \
                    patch.object(api.content_planner, "quality_gate", return_value=(True, "")):
                result = api.benchmark_ai_providers("dark atmospheric rock")

            self.assertTrue(result["ok"])
            self.assertEqual(result["winner"], "secondary")
            self.assertEqual([item["provider"] for item in result["results"]][0], "secondary")
            secondary = result["results"][0]
            self.assertTrue(secondary["music_search"]["ok"])
            self.assertTrue(secondary["radio_host"]["ok"])
