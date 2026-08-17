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


if __name__ == "__main__":
    unittest.main()
