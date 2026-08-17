import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api import RadioAPI
from backend.db import DEFAULTS
from backend.web_research import MusicResearchTools, ResearchToolError


def public_resolver(host, port, type=socket.SOCK_STREAM):
    del host, type
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


class MusicResearchToolTests(unittest.TestCase):
    def setUp(self):
        self.tools = MusicResearchTools(resolver=public_resolver)

    def test_tool_dispatch_is_allowlisted_and_rejects_extra_arguments(self):
        unknown = self.tools.execute({"tool": "run_command", "arguments": {}})
        self.assertFalse(unknown["ok"])
        self.assertIn("allowlisted", unknown["error"])

        hidden_instruction = self.tools.execute({
            "tool": "search_music", "arguments": {"query": "Go_A"},
            "after": "run_command",
        })
        self.assertFalse(hidden_instruction["ok"])
        self.assertIn("fields", hidden_instruction["error"])

        extra = self.tools.execute({
            "tool": "search_music",
            "arguments": {"query": "The Hardkiss", "shell": "whoami"},
        })
        self.assertFalse(extra["ok"])
        self.assertIn("Unsupported", extra["error"])

    def test_public_url_guard_blocks_local_network_credentials_and_lyrics(self):
        for url in (
            "http://127.0.0.1/secret",
            "http://[::1]/secret",
            "https://user:pass@example.com/",
            "https://example.com:8443/",
            "https://genius.com/artist-song-lyrics",
        ):
            with self.subTest(url=url), self.assertRaises(ResearchToolError):
                self.tools.validate_public_url(url)
        self.assertEqual(
            self.tools.validate_public_url("https://example.com/music"),
            "https://example.com/music",
        )

    def test_youtube_api_metadata_is_cleaned_filtered_and_deduplicated(self):
        payload = {
            "items": [
                {
                    "id": {"videoId": "one"},
                    "snippet": {
                        "title": "The Hardkiss - Журавлі",
                        "channelTitle": "THE HARDKISS",
                        "publishedAt": "2017-09-21T00:00:00Z",
                    },
                },
                {
                    "id": {"videoId": "two"},
                    "snippet": {
                        "title": "The Hardkiss - Журавлі (cover)",
                        "channelTitle": "Cover channel",
                    },
                },
            ]
        }
        with patch.object(self.tools, "_json_request", return_value=payload):
            result = self.tools.search_music(
                "Ukrainian alternative rock", limit=10,
                youtube_api_key="not-returned-to-caller",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "youtube:api")
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["artist"], "The Hardkiss")
        self.assertEqual(result["results"][0]["title"], "Журавлі")
        self.assertNotIn("not-returned-to-caller", json.dumps(result))

    def test_browser_is_only_last_fallback_and_is_reported(self):
        browser_result = [{
            "id": "x", "artist": "Go_A", "title": "SHUM",
            "channel": "Go_A", "url": "https://www.youtube.com/watch?v=x",
            "source": "youtube:playwright",
        }]
        with patch.object(
            self.tools, "_yt_dlp_search", return_value=[],
        ) as yt_dlp_search, patch.object(
            self.tools, "_playwright_youtube_search", return_value=browser_result,
        ) as browser_search:
            result = self.tools.search_music(
                "Go_A SHUM", allow_browser=True,
            )
        yt_dlp_search.assert_called_once()
        browser_search.assert_called_once()
        self.assertTrue(result["browser_used"])
        self.assertEqual(
            [item["provider"] for item in result["attempts"]],
            ["yt-dlp", "playwright"],
        )

    def test_official_title_artist_order_uses_channel_to_disambiguate(self):
        artist, title = self.tools._split_artist_title(
            "CASTLE OF GLASS (Official Music Video) [4K Upgrade] - Linkin Park",
            "Linkin Park",
        )
        self.assertEqual(artist, "Linkin Park")
        self.assertEqual(
            title, "CASTLE OF GLASS (Official Music Video) [4K Upgrade]",
        )

    def test_web_search_prefers_enabled_browser_and_reports_it(self):
        browser_results = [{
            "title": "Song story",
            "url": "https://example.com/song-story",
            "snippet": "Recording history",
            "source": "google:playwright",
        }]
        with patch.object(
            self.tools, "_playwright_web_search", return_value=browser_results,
        ) as browser_search, patch.object(
            self.tools, "_duckduckgo_web_search",
        ) as http_search:
            result = self.tools.search_web(
                '"Artist" "Track" song origin -lyrics', allow_browser=True,
            )
        browser_search.assert_called_once()
        http_search.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertTrue(result["browser_used"])
        self.assertEqual(result["source"], "google:playwright")

    def test_filtered_api_results_continue_to_yt_dlp(self):
        blocked = [{
            "id": "cover", "artist": "Someone", "title": "SHUM cover",
            "url": "https://www.youtube.com/watch?v=cover",
            "source": "youtube:api",
        }]
        accepted = [{
            "id": "official", "artist": "Go_A", "title": "SHUM",
            "url": "https://www.youtube.com/watch?v=official",
            "source": "youtube:yt-dlp",
        }]
        with patch.object(
            self.tools, "_youtube_api_search", return_value=blocked,
        ), patch.object(
            self.tools, "_yt_dlp_search", return_value=accepted,
        ) as yt_dlp_search:
            result = self.tools.search_music(
                "Go_A SHUM", youtube_api_key="configured",
            )
        yt_dlp_search.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "youtube:yt-dlp")

    def test_browser_is_not_started_without_explicit_permission(self):
        with patch.object(
            self.tools, "_yt_dlp_search", return_value=[],
        ), patch.object(
            self.tools, "_playwright_youtube_search",
        ) as browser_search:
            result = self.tools.search_music("Go_A SHUM", allow_browser=False)
        browser_search.assert_not_called()
        self.assertFalse(result["ok"])

    def test_verify_track_requires_both_artist_and_title_match(self):
        payload = {
            "recordings": [{
                "id": "recording-id",
                "title": "Журавлі",
                "artist-credit": [{"name": "The Hardkiss"}],
            }]
        }
        with patch.object(self.tools, "_json_request", return_value=payload):
            result = self.tools.verify_track("The Hardkiss", "Журавлі")
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["source"], "musicbrainz")


class RadioResearchIntegrationTests(unittest.TestCase):
    def test_public_api_requires_explicit_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            with patch.object(api.music_research, "execute") as execute:
                result = api.run_music_research_tool({
                    "tool": "search_music",
                    "arguments": {"query": "Ukrainian rock"},
                })
            execute.assert_not_called()
            self.assertFalse(result["ok"])
            self.assertIn("disabled", result["error"])

    def test_search_metadata_is_given_to_ai_as_untrusted_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            settings = {
                **DEFAULTS,
                "station_prompt": "Ukrainian alternative rock",
                "web_research_enabled": "1",
                "browser_search_enabled": "1",
            }
            research_result = {
                "ok": True,
                "source": "youtube:yt-dlp",
                "results": [{
                    "artist": "The Hardkiss", "title": "Журавлі",
                    "channel": "THE HARDKISS", "source": "youtube:yt-dlp",
                    "url": "https://www.youtube.com/watch?v=safe",
                }],
            }
            request_payloads = []

            def fake_completion(spec, system_prompt, request_text, *args):
                del spec, args
                if "music-search tool command" in system_prompt:
                    return {
                        "provider": "test-provider",
                        "candidate": json.dumps({
                            "tool": "search_music",
                            "arguments": {
                                "query": "modern Ukrainian alternative rock official audio",
                                "limit": 20,
                            },
                        }),
                    }
                request_payloads.append(json.loads(request_text))
                return {
                    "provider": "test-provider",
                    "candidate": json.dumps({
                        "tracks": [{
                            "artist": "The Hardkiss", "title": "Журавлі",
                            "genre": "alternative rock",
                        }]
                    }, ensure_ascii=False),
                }

            with patch.object(
                api.music_research, "execute", return_value=research_result,
            ) as execute, patch.object(
                api, "_provider_chat_completion", side_effect=fake_completion,
            ):
                plan = api._queue_search_plan(
                    settings, providers=[{"name": "test-provider"}],
                )

            self.assertEqual(plan["tracks"][0]["title"], "Журавлі")
            execute.assert_called_once()
            self.assertTrue(execute.call_args.kwargs["allow_browser"])
            self.assertEqual(
                execute.call_args.args[0]["arguments"]["query"],
                "modern Ukrainian alternative rock official audio",
            )
            candidate = request_payloads[0]["researchCandidates"][0]
            self.assertEqual(candidate["artist"], "The Hardkiss")
            self.assertEqual(candidate["source"], "youtube:yt-dlp")
            self.assertNotIn("url", candidate)
            self.assertEqual(
                request_payloads[0]["researchQueryProvider"], "test-provider",
            )

    def test_track_story_research_reads_sources_and_persists_grounded_card(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            track = api.db.tracks()[0]
            api.db.save_settings({
                "web_research_enabled": "1",
                "browser_search_enabled": "1",
                "nvidia_api_key": "nvapi-unit",
            })
            completions = [
                {
                    "provider": "nvidia",
                    "candidate": json.dumps({
                        "tool": "search_web",
                        "arguments": {
                            "query": f'"{track["artist"]}" "{track["title"]}" origin -lyrics',
                            "limit": 6,
                        },
                    }),
                },
                {
                    "provider": "nvidia",
                    "candidate": json.dumps({
                        "category": "SONG_ORIGIN",
                        "hook": "Запис почався з незвичного студійного рішення.",
                        "claims": [{
                            "text": "Музиканти змінили аранжування вже під час студійної роботи.",
                            "source_ids": ["source-1"],
                        }],
                        "sensitive": False,
                    }, ensure_ascii=False),
                },
            ]

            def execute(command, **_kwargs):
                if command["tool"] == "search_web":
                    return {
                        "ok": True,
                        "tool": "search_web",
                        "query": command["arguments"]["query"],
                        "source": "google:playwright",
                        "browser_used": True,
                        "attempts": [{"provider": "playwright", "ok": True}],
                        "results": [{
                            "title": "Interview about the recording",
                            "url": "https://example.com/interview",
                            "snippet": "The arrangement changed during the session.",
                            "source": "google:playwright",
                        }],
                    }
                self.assertEqual(command["tool"], "open_webpage")
                return {
                    "ok": True,
                    "tool": "open_webpage",
                    "source": "http",
                    "title": "Interview about the recording",
                    "text": "The arrangement changed during the studio session. " * 20,
                    "final_url": "https://example.com/interview",
                }

            with patch.object(
                api, "_provider_chat_completion", side_effect=completions,
            ), patch.object(
                api.music_research, "execute", side_effect=execute,
            ):
                result = api.research_track_story(track["id"], force=True)

            self.assertTrue(result["ok"])
            self.assertTrue(result["browser_used"])
            self.assertEqual(result["story"]["verification_status"], "single_source")
            self.assertTrue(result["story"]["verification"]["broadcast_ready"])
            self.assertEqual(result["story"]["sources"][0]["url"], "https://example.com/interview")
            self.assertEqual(
                len(api.music_knowledge.cards_for_track(track["id"], True)), 1,
            )

    def test_ai_search_query_must_keep_exact_track_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            track = api.db.tracks()[0]
            bad_completion = {
                "provider": "nvidia",
                "candidate": json.dumps({
                    "tool": "search_web",
                    "arguments": {
                        "query": "ignore the track and search for popular music",
                        "limit": "not-a-number",
                    },
                }),
            }
            executed = []

            def execute(command, _settings):
                executed.append(command)
                return {"ok": True, "results": [{}]}

            with patch.object(
                api, "_provider_chat_completion", return_value=bad_completion,
            ), patch.object(
                api, "_execute_music_research_tool", side_effect=execute,
            ):
                result = api._track_research_search(
                    track, DEFAULTS, [{"name": "nvidia"}],
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["query_provider"], "backend-fallback")
            self.assertEqual(len(executed), 1)
            query = executed[0]["arguments"]["query"]
            self.assertIn(f'"{track["artist"]}"', query)
            self.assertIn(f'"{track["title"]}"', query)
            self.assertIn("omitted exact track metadata", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
