"""Bounded web and music research tools for the radio director.

The AI never receives a browser object.  It may request one of the allowlisted
tools below; this module validates the arguments, performs the network work and
returns small, structured metadata records.  Audio downloads remain the
responsibility of the existing LUMEN Downloader validation pipeline.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from html.parser import HTMLParser


MAX_QUERY_CHARS = 240
MAX_RESULTS = 20
MAX_PAGE_CHARS = 12_000
MAX_HTTP_BYTES = 256_000
DEFAULT_TIMEOUT_SECONDS = 12

BLOCKED_RESULT_TERMS = (
    "reaction", "tutorial", "review", "interview", "live concert",
    "live session", "full concert", "sped up", "nightcore", "slowed",
    "playlist", "mix", "cover", "karaoke", "tribute", "fan made",
    "ai generated", "royalty free", "type beat",
)
BLOCKED_LYRICS_HOSTS = (
    "azlyrics.com", "genius.com", "lyrics.com", "musixmatch.com",
)
YOUTUBE_VIDEO_HOSTS = (
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
)


class ResearchToolError(ValueError):
    """A safe, user-facing research-tool validation error."""


class _VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self._title_depth = 0
        self.title_parts = []
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self._hidden_depth += 1
        if tag == "title":
            self._title_depth += 1

    def handle_endtag(self, tag):
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        if tag == "title":
            self._title_depth = max(0, self._title_depth - 1)

    def handle_data(self, data):
        value = " ".join(str(data or "").split())
        if not value:
            return
        if self._title_depth:
            self.title_parts.append(value)
        if not self._hidden_depth:
            self.text_parts.append(value)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, validator):
        super().__init__()
        self._validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._validator(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _DuckDuckGoResultParser(HTMLParser):
    """Read the small public HTML result page without executing scripts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self._title_depth = 0
        self._snippet_depth = 0
        self._href = ""
        self._title_parts = []
        self._snippet_parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = set(str(attributes.get("class") or "").split())
        if tag.casefold() == "a" and "result__a" in classes:
            self._title_depth += 1
            self._href = str(attributes.get("href") or "")
            self._title_parts = []
        if "result__snippet" in classes:
            self._snippet_depth += 1
            self._snippet_parts = []

    def handle_endtag(self, tag):
        if tag.casefold() == "a" and self._title_depth:
            self._title_depth = max(0, self._title_depth - 1)
            title = " ".join(self._title_parts).strip()
            if title and self._href:
                self.results.append({
                    "title": title,
                    "url": self._href,
                    "snippet": "",
                })
            self._href = ""
            self._title_parts = []
        if self._snippet_depth and tag.casefold() in {"a", "div", "span"}:
            self._snippet_depth = max(0, self._snippet_depth - 1)
            snippet = " ".join(self._snippet_parts).strip()
            if snippet and self.results and not self.results[-1].get("snippet"):
                self.results[-1]["snippet"] = snippet
            self._snippet_parts = []

    def handle_data(self, data):
        value = " ".join(str(data or "").split())
        if not value:
            return
        if self._title_depth:
            self._title_parts.append(value)
        if self._snippet_depth:
            self._snippet_parts.append(value)


class MusicResearchTools:
    """Execute a small allowlist of metadata-oriented research commands."""

    TOOL_DEFINITIONS = (
        {
            "name": "search_music",
            "description": "Search public music metadata and official-looking audio results.",
            "arguments": {"query": "string", "limit": "integer 1..20"},
        },
        {
            "name": "search_artist",
            "description": "Look up artist identities in MusicBrainz.",
            "arguments": {"artist": "string", "limit": "integer 1..20"},
        },
        {
            "name": "verify_track",
            "description": "Verify that an artist/title pair exists in public metadata.",
            "arguments": {"artist": "string", "title": "string"},
        },
        {
            "name": "open_webpage",
            "description": "Read bounded visible text from a public HTTP(S) page.",
            "arguments": {"url": "string", "max_chars": "integer 200..12000"},
        },
        {
            "name": "search_web",
            "description": "Search public pages for source-backed facts about a track.",
            "arguments": {"query": "string", "limit": "integer 1..10"},
        },
    )

    def __init__(self, *, timeout=DEFAULT_TIMEOUT_SECONDS, resolver=None):
        self.timeout = max(3, min(30, int(timeout)))
        self._resolver = resolver or socket.getaddrinfo

    @classmethod
    def tool_definitions(cls):
        return [dict(item) for item in cls.TOOL_DEFINITIONS]

    @staticmethod
    def _bounded_limit(value, *, default=10):
        try:
            limit = int(value)
        except (TypeError, ValueError):
            limit = default
        return max(1, min(MAX_RESULTS, limit))

    @staticmethod
    def _bounded_text(value, label):
        text = " ".join(str(value or "").split())
        if not text:
            raise ResearchToolError(f"{label} is required")
        if len(text) > MAX_QUERY_CHARS:
            raise ResearchToolError(f"{label} exceeds {MAX_QUERY_CHARS} characters")
        return text

    @staticmethod
    def _truthy(value):
        return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}

    @staticmethod
    def _host_matches(host, suffix):
        return host == suffix or host.endswith(f".{suffix}")

    def validate_public_url(self, value):
        """Reject local/private destinations before any HTTP or browser access."""
        raw = str(value or "").strip()
        if len(raw) > 2048:
            raise ResearchToolError("URL is too long")
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme.casefold() not in {"http", "https"}:
            raise ResearchToolError("Only HTTP(S) pages are allowed")
        if parsed.username or parsed.password:
            raise ResearchToolError("Credentials in URLs are not allowed")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ResearchToolError("Invalid URL port") from exc
        if port not in {None, 80, 443}:
            raise ResearchToolError("Only standard HTTP(S) ports are allowed")
        host = (parsed.hostname or "").rstrip(".").casefold()
        if not host or host in {"localhost", "localhost.localdomain"}:
            raise ResearchToolError("A public hostname is required")
        if any(self._host_matches(host, item) for item in BLOCKED_LYRICS_HOSTS):
            raise ResearchToolError("Full-lyrics providers are not opened by this tool")
        if re.search(r"(?:^|[/_-])lyrics?(?:[/_.-]|$)", parsed.path, re.IGNORECASE):
            raise ResearchToolError("Full song-text pages are outside this tool's scope")

        try:
            literal = ipaddress.ip_address(host.strip("[]"))
            addresses = [literal]
        except ValueError:
            try:
                records = self._resolver(host, port or 443, type=socket.SOCK_STREAM)
            except OSError as exc:
                raise ResearchToolError("The hostname could not be resolved") from exc
            addresses = []
            for record in records:
                try:
                    addresses.append(ipaddress.ip_address(record[4][0]))
                except (IndexError, TypeError, ValueError):
                    continue
        if not addresses or any(not address.is_global for address in addresses):
            raise ResearchToolError("Private or non-routable addresses are not allowed")
        return raw

    def execute(self, command, *, youtube_api_key="", allow_browser=False):
        """Validate and execute a JSON-like AI tool command."""
        if not isinstance(command, dict):
            return {"ok": False, "tool": "", "error": "Tool command must be an object"}
        tool = str(command.get("tool") or "").strip()
        if set(command) - {"tool", "arguments"}:
            return {"ok": False, "tool": tool, "error": "Unsupported tool command fields"}
        arguments = command.get("arguments") or {}
        if not isinstance(arguments, dict):
            return {"ok": False, "tool": tool, "error": "Tool arguments must be an object"}
        allowed = {item["name"] for item in self.TOOL_DEFINITIONS}
        if tool not in allowed:
            return {"ok": False, "tool": tool, "error": "Tool is not allowlisted"}
        try:
            if tool == "search_music":
                unexpected = set(arguments) - {"query", "limit"}
                if unexpected:
                    raise ResearchToolError("Unsupported search_music arguments")
                return self.search_music(
                    arguments.get("query"),
                    limit=arguments.get("limit", 10),
                    youtube_api_key=youtube_api_key,
                    allow_browser=allow_browser,
                )
            if tool == "search_artist":
                unexpected = set(arguments) - {"artist", "limit"}
                if unexpected:
                    raise ResearchToolError("Unsupported search_artist arguments")
                return self.search_artist(
                    arguments.get("artist"), limit=arguments.get("limit", 5),
                )
            if tool == "verify_track":
                unexpected = set(arguments) - {"artist", "title"}
                if unexpected:
                    raise ResearchToolError("Unsupported verify_track arguments")
                return self.verify_track(
                    arguments.get("artist"), arguments.get("title"),
                    youtube_api_key=youtube_api_key,
                    allow_browser=allow_browser,
                )
            if tool == "search_web":
                unexpected = set(arguments) - {"query", "limit"}
                if unexpected:
                    raise ResearchToolError("Unsupported search_web arguments")
                return self.search_web(
                    arguments.get("query"),
                    limit=arguments.get("limit", 6),
                    allow_browser=allow_browser,
                )
            unexpected = set(arguments) - {"url", "max_chars"}
            if unexpected:
                raise ResearchToolError("Unsupported open_webpage arguments")
            return self.open_webpage(
                arguments.get("url"),
                max_chars=arguments.get("max_chars", 4000),
                allow_browser=allow_browser,
            )
        except ResearchToolError as exc:
            return {"ok": False, "tool": tool, "error": str(exc)}
        except Exception as exc:  # A tool failure must not stop radio playback.
            return {
                "ok": False,
                "tool": tool,
                "error": f"{type(exc).__name__}: research provider unavailable",
            }

    @staticmethod
    def _clean_result_title(value):
        return html.unescape(" ".join(str(value or "").split())).strip()

    @classmethod
    def _is_usable_music_result(cls, result):
        title = cls._clean_result_title(result.get("title")).casefold()
        return bool(title) and not any(term in title for term in BLOCKED_RESULT_TERMS)

    @staticmethod
    def _split_artist_title(raw_title, fallback_artist=""):
        title = html.unescape(str(raw_title or "")).strip()
        parts = re.split(r"\s+(?:-|–|—|\|)\s+", title, maxsplit=1)
        if len(parts) == 2 and all(part.strip() for part in parts):
            left, right = (part.strip() for part in parts)
            fallback = str(fallback_artist or "").strip()
            normalize = lambda value: re.sub(
                r"[^\w]+", " ", str(value or "").casefold(), flags=re.UNICODE,
            ).strip()
            fallback_key = normalize(fallback)
            if fallback_key:
                left_score = SequenceMatcher(
                    None, normalize(left), fallback_key,
                ).ratio()
                right_score = SequenceMatcher(
                    None, normalize(right), fallback_key,
                ).ratio()
                # Official uploads commonly use "Title - Artist".  The
                # verified channel name tells us when the pair must be flipped.
                if right_score >= 0.78 and right_score > left_score:
                    return right, left
            return left, right
        return str(fallback_artist or "").strip(), title

    @classmethod
    def _dedupe_results(cls, results, limit):
        output = []
        seen = set()
        for result in results:
            if not isinstance(result, dict) or not cls._is_usable_music_result(result):
                continue
            key = (
                re.sub(r"\W+", " ", str(result.get("artist") or "").casefold()).strip(),
                re.sub(r"\W+", " ", str(result.get("title") or "").casefold()).strip(),
            )
            if not key[1] or key in seen:
                continue
            seen.add(key)
            output.append(result)
            if len(output) >= limit:
                break
        return output

    def search_music(self, query, *, limit=10, youtube_api_key="", allow_browser=False):
        query = self._bounded_text(query, "query")
        limit = self._bounded_limit(limit)
        attempts = []
        results = []
        if str(youtube_api_key or "").strip():
            try:
                results = self._dedupe_results(
                    self._youtube_api_search(query, limit, youtube_api_key), limit,
                )
                attempts.append({"provider": "youtube-api", "ok": bool(results)})
            except Exception as exc:
                attempts.append({
                    "provider": "youtube-api", "ok": False,
                    "error": self._safe_provider_error(exc),
                })
        if not results:
            try:
                results = self._dedupe_results(
                    self._yt_dlp_search(query, limit), limit,
                )
                attempts.append({"provider": "yt-dlp", "ok": bool(results)})
            except Exception as exc:
                attempts.append({
                    "provider": "yt-dlp", "ok": False,
                    "error": self._safe_provider_error(exc),
                })
        if not results and self._truthy(allow_browser):
            try:
                results = self._dedupe_results(
                    self._playwright_youtube_search(query, limit), limit,
                )
                attempts.append({"provider": "playwright", "ok": bool(results)})
            except Exception as exc:
                attempts.append({
                    "provider": "playwright", "ok": False,
                    "error": self._safe_provider_error(exc),
                })
        results = self._dedupe_results(results, limit)
        source = str(results[0].get("source") or "") if results else ""
        return {
            "ok": bool(results), "tool": "search_music", "query": query,
            "source": source, "results": results, "attempts": attempts,
            "browser_used": source == "youtube:playwright",
        }

    @staticmethod
    def _safe_provider_error(exc):
        if isinstance(exc, urllib.error.HTTPError):
            return f"HTTP {exc.code}"
        if isinstance(exc, urllib.error.URLError):
            return "network error"
        return f"{type(exc).__name__}: provider unavailable"

    @staticmethod
    def _unwrapped_search_url(value):
        raw = html.unescape(str(value or "").strip())
        parsed = urllib.parse.urlsplit(raw)
        if "duckduckgo.com" in (parsed.hostname or "").casefold():
            target = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
            if target:
                return urllib.parse.unquote(target)
        return raw

    def _clean_web_results(self, results, limit):
        output = []
        seen = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            title = self._clean_result_title(item.get("title"))
            url = self._unwrapped_search_url(item.get("url"))
            snippet = self._clean_result_title(item.get("snippet"))
            if not title or not url:
                continue
            try:
                parsed = urllib.parse.urlsplit(url)
                host = (parsed.hostname or "").casefold()
                if any(
                    self._host_matches(host, domain)
                    for domain in ("google.com", "bing.com", "duckduckgo.com")
                ):
                    continue
                url = self.validate_public_url(url)
            except ResearchToolError:
                continue
            key = url.casefold().rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            output.append({
                "title": title[:300],
                "url": url,
                "snippet": snippet[:700],
                "source": str(item.get("source") or "web"),
            })
            if len(output) >= limit:
                break
        return output

    def _duckduckgo_web_search(self, query, limit):
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({
            "q": query,
        })
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                ),
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read(MAX_HTTP_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
        parser = _DuckDuckGoResultParser()
        parser.feed(raw.decode(charset, errors="replace"))
        return [
            {**item, "source": "duckduckgo:http"}
            for item in parser.results[:limit * 2]
        ]

    def _playwright_web_search(self, query, limit):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ResearchToolError("Playwright is not installed") from exc
        google_url = "https://www.google.com/search?" + urllib.parse.urlencode({
            "q": query,
            "num": min(10, limit * 2),
            "hl": "en",
        })
        results = []
        with sync_playwright() as playwright:
            browser = None
            last_error = None
            for channel in ("msedge", "chrome", None):
                try:
                    kwargs = {"headless": True}
                    if channel:
                        kwargs["channel"] = channel
                    browser = playwright.chromium.launch(**kwargs)
                    break
                except Exception as exc:
                    last_error = exc
            if browser is None:
                raise ResearchToolError(
                    "No Playwright Chromium/Edge/Chrome browser is available"
                ) from last_error
            try:
                page = browser.new_page()
                page.goto(
                    google_url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout * 1000,
                )
                cards = page.locator("a:has(h3)")
                for index in range(min(cards.count(), limit * 3)):
                    link = cards.nth(index)
                    href = str(link.get_attribute("href") or "")
                    heading = link.locator("h3")
                    title = self._clean_result_title(
                        heading.first.inner_text() if heading.count() else ""
                    )
                    if title and href.startswith("http"):
                        results.append({
                            "title": title,
                            "url": href,
                            "snippet": "",
                            "source": "google:playwright",
                        })
                if not results:
                    # Google can present a consent or bot-check page to an
                    # unattended browser. Bing is the bounded second engine,
                    # not a change in research scope.
                    bing_url = "https://www.bing.com/search?" + urllib.parse.urlencode({
                        "q": query,
                        "count": min(10, limit * 2),
                    })
                    page.goto(
                        bing_url,
                        wait_until="domcontentloaded",
                        timeout=self.timeout * 1000,
                    )
                    bing_cards = page.locator("li.b_algo")
                    for index in range(min(bing_cards.count(), limit * 3)):
                        card = bing_cards.nth(index)
                        links = card.locator("h2 a")
                        if not links.count():
                            continue
                        link = links.first
                        href = str(link.get_attribute("href") or "")
                        title = self._clean_result_title(link.inner_text())
                        snippets = card.locator(".b_caption p")
                        snippet = self._clean_result_title(
                            snippets.first.inner_text() if snippets.count() else ""
                        )
                        if title and href.startswith("http"):
                            results.append({
                                "title": title,
                                "url": href,
                                "snippet": snippet,
                                "source": "bing:playwright",
                            })
            finally:
                browser.close()
        return results

    def search_web(self, query, *, limit=6, allow_browser=False):
        query = self._bounded_text(query, "query")
        limit = max(1, min(10, self._bounded_limit(limit, default=6)))
        attempts = []
        results = []
        if self._truthy(allow_browser):
            try:
                results = self._clean_web_results(
                    self._playwright_web_search(query, limit), limit,
                )
                attempts.append({"provider": "playwright", "ok": bool(results)})
            except Exception as exc:
                attempts.append({
                    "provider": "playwright", "ok": False,
                    "error": self._safe_provider_error(exc),
                })
        if not results:
            try:
                results = self._clean_web_results(
                    self._duckduckgo_web_search(query, limit), limit,
                )
                attempts.append({"provider": "duckduckgo", "ok": bool(results)})
            except Exception as exc:
                attempts.append({
                    "provider": "duckduckgo", "ok": False,
                    "error": self._safe_provider_error(exc),
                })
        source = str(results[0].get("source") or "") if results else ""
        return {
            "ok": bool(results),
            "tool": "search_web",
            "query": query,
            "source": source,
            "results": results,
            "attempts": attempts,
            "browser_used": source.endswith(":playwright"),
        }

    @staticmethod
    def youtube_video_id(value):
        """Return a canonical YouTube video id from a real video URL."""
        raw = html.unescape(str(value or "").strip())
        try:
            parsed = urllib.parse.urlsplit(raw)
        except ValueError:
            return ""
        host = (parsed.hostname or "").casefold().rstrip(".")
        video_id = ""
        if host == "youtu.be":
            video_id = parsed.path.strip("/").split("/", 1)[0]
        elif host in YOUTUBE_VIDEO_HOSTS:
            if parsed.path.rstrip("/") == "/watch":
                video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
            elif parsed.path.startswith("/embed/"):
                video_id = parsed.path.split("/", 3)[2]
        video_id = str(video_id or "").strip()
        return video_id if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id) else ""

    def search_youtube_on_google(self, artist, title, *, limit=5, allow_browser=True):
        """Resolve an exact playlist entry to ranked YouTube links via Google.

        This method never downloads media and never lets an AI invent a URL.
        Google (with the bounded Bing fallback already used by search_web) only
        supplies candidate links; yt-dlp metadata validation remains mandatory
        before the downloader writes an audio file.
        """
        artist = self._bounded_text(artist, "artist")
        title = self._bounded_text(title, "title")
        limit = max(1, min(10, self._bounded_limit(limit, default=5)))
        query = (
            f'site:youtube.com/watch "{artist[:80]}" "{title[:100]}" '
            "official audio"
        )
        web_result = self.search_web(
            query[:MAX_QUERY_CHARS], limit=min(10, limit * 2),
            allow_browser=allow_browser,
        )
        candidates = []
        seen = set()
        for item in web_result.get("results", []):
            video_id = self.youtube_video_id(item.get("url"))
            label = self._clean_result_title(item.get("title"))
            if not video_id or video_id in seen or not self._is_usable_music_result(item):
                continue
            seen.add(video_id)
            artist_score = self._match_ratio(artist, label)
            title_score = self._match_ratio(title, label)
            candidates.append({
                "id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": label,
                "source": str(item.get("source") or "google:web"),
                "resolver_score": round(artist_score * 0.45 + title_score * 0.55, 4),
            })
        candidates.sort(
            key=lambda item: float(item.get("resolver_score") or 0), reverse=True,
        )
        candidates = candidates[:limit]
        return {
            "ok": bool(candidates),
            "tool": "search_youtube_on_google",
            "artist": artist,
            "title": title,
            "query": query[:MAX_QUERY_CHARS],
            "source": str(candidates[0].get("source") or "") if candidates else "",
            "results": candidates,
            "attempts": list(web_result.get("attempts") or []),
            "browser_used": bool(web_result.get("browser_used")),
            "error": str(web_result.get("error") or "") if not candidates else "",
        }

    def _json_request(self, url, *, headers=None):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "VectorRadio/1.0 (+https://github.com/zhuravskayyar/ai-radio-ukr)",
                **(headers or {}),
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = response.read(MAX_HTTP_BYTES)
        return json.loads(payload.decode("utf-8"))

    def _youtube_api_search(self, query, limit, api_key):
        params = urllib.parse.urlencode({
            "part": "snippet", "type": "video", "maxResults": limit,
            "q": query, "videoEmbeddable": "true", "key": str(api_key).strip(),
        })
        payload = self._json_request(
            f"https://www.googleapis.com/youtube/v3/search?{params}"
        )
        results = []
        for item in payload.get("items", []):
            video_id = str((item.get("id") or {}).get("videoId") or "").strip()
            snippet = item.get("snippet") or {}
            raw_title = self._clean_result_title(snippet.get("title"))
            channel = self._clean_result_title(snippet.get("channelTitle"))
            artist, title = self._split_artist_title(raw_title, channel)
            if not video_id:
                continue
            results.append({
                "id": video_id, "artist": artist, "title": title,
                "display_title": raw_title, "channel": channel,
                "published_at": str(snippet.get("publishedAt") or ""),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "source": "youtube:api",
            })
        return results

    def _yt_dlp_search(self, query, limit):
        try:
            import yt_dlp
        except ImportError as exc:
            raise ResearchToolError("yt-dlp is not installed") from exc
        options = {
            "quiet": True, "no_warnings": True, "extract_flat": True,
            "skip_download": True, "noplaylist": True,
            "playlistend": min(MAX_RESULTS * 2, limit * 2),
            "socket_timeout": self.timeout,
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            payload = downloader.extract_info(
                f"ytsearch{min(MAX_RESULTS * 2, limit * 2)}:{query}", download=False,
            ) or {}
        results = []
        for item in payload.get("entries", []) or []:
            if not isinstance(item, dict):
                continue
            video_id = str(item.get("id") or "").strip()
            raw_title = self._clean_result_title(item.get("title"))
            channel = self._clean_result_title(
                item.get("channel") or item.get("uploader")
            )
            artist, title = self._split_artist_title(
                raw_title, item.get("artist") or channel,
            )
            url = item.get("webpage_url") or item.get("original_url")
            if not str(url or "").startswith("http") and video_id:
                url = f"https://www.youtube.com/watch?v={video_id}"
            results.append({
                "id": video_id, "artist": artist, "title": title,
                "display_title": raw_title, "channel": channel,
                "duration": item.get("duration"), "url": str(url or ""),
                "source": "youtube:yt-dlp",
            })
        return results

    def _playwright_youtube_search(self, query, limit):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ResearchToolError("Playwright is not installed") from exc
        search_url = (
            "https://www.youtube.com/results?search_query="
            + urllib.parse.quote_plus(query)
        )
        results = []
        with sync_playwright() as playwright:
            browser = None
            last_error = None
            for channel in ("msedge", "chrome", None):
                try:
                    kwargs = {"headless": True}
                    if channel:
                        kwargs["channel"] = channel
                    browser = playwright.chromium.launch(**kwargs)
                    break
                except Exception as exc:
                    last_error = exc
            if browser is None:
                raise ResearchToolError("No Playwright Chromium/Edge/Chrome browser is available") from last_error
            try:
                page = browser.new_page()
                page.goto(
                    search_url, wait_until="domcontentloaded",
                    timeout=self.timeout * 1000,
                )
                page.wait_for_selector("ytd-video-renderer", timeout=min(7000, self.timeout * 1000))
                cards = page.locator("ytd-video-renderer")
                for index in range(min(cards.count(), limit * 2)):
                    card = cards.nth(index)
                    title_link = card.locator("#video-title").first
                    raw_title = self._clean_result_title(title_link.get_attribute("title"))
                    href = str(title_link.get_attribute("href") or "")
                    channel = self._clean_result_title(
                        card.locator("#channel-name a").first.inner_text()
                        if card.locator("#channel-name a").count() else ""
                    )
                    if not raw_title or not href.startswith("/watch"):
                        continue
                    artist, title = self._split_artist_title(raw_title, channel)
                    url = urllib.parse.urljoin("https://www.youtube.com", href)
                    video_id = urllib.parse.parse_qs(
                        urllib.parse.urlsplit(url).query
                    ).get("v", [""])[0]
                    url = (
                        f"https://www.youtube.com/watch?v={video_id}"
                        if video_id else url
                    )
                    results.append({
                        "id": video_id, "artist": artist, "title": title,
                        "display_title": raw_title, "channel": channel,
                        "url": url, "source": "youtube:playwright",
                    })
            finally:
                browser.close()
        return results

    def search_artist(self, artist, *, limit=5):
        artist = self._bounded_text(artist, "artist")
        limit = self._bounded_limit(limit, default=5)
        params = urllib.parse.urlencode({
            "query": f'artist:"{artist}"', "fmt": "json", "limit": limit,
        })
        try:
            payload = self._json_request(
                f"https://musicbrainz.org/ws/2/artist/?{params}"
            )
        except Exception as exc:
            return {
                "ok": False, "tool": "search_artist", "artist": artist,
                "source": "musicbrainz", "results": [],
                "error": self._safe_provider_error(exc),
            }
        results = []
        for item in payload.get("artists", [])[:limit]:
            identifier = str(item.get("id") or "")
            results.append({
                "id": identifier, "name": str(item.get("name") or ""),
                "sort_name": str(item.get("sort-name") or ""),
                "country": str(item.get("country") or ""),
                "disambiguation": str(item.get("disambiguation") or ""),
                "score": int(item.get("score") or 0),
                "url": f"https://musicbrainz.org/artist/{identifier}",
                "source": "musicbrainz",
            })
        return {
            "ok": bool(results), "tool": "search_artist", "artist": artist,
            "source": "musicbrainz", "results": results,
        }

    @staticmethod
    def _match_ratio(left, right):
        normalize = lambda value: re.sub(
            r"[^\w]+", " ", str(value or "").casefold(), flags=re.UNICODE,
        ).strip()
        return SequenceMatcher(None, normalize(left), normalize(right)).ratio()

    def verify_track(self, artist, title, *, youtube_api_key="", allow_browser=False):
        artist = self._bounded_text(artist, "artist")
        title = self._bounded_text(title, "title")
        params = urllib.parse.urlencode({
            "query": f'recording:"{title}" AND artist:"{artist}"',
            "fmt": "json", "limit": 8,
        })
        candidates = []
        provider_error = ""
        try:
            payload = self._json_request(
                f"https://musicbrainz.org/ws/2/recording/?{params}"
            )
            for item in payload.get("recordings", []):
                credit = "".join(
                    str(part.get("name") or (part.get("artist") or {}).get("name") or "")
                    + str(part.get("joinphrase") or "")
                    for part in item.get("artist-credit", [])
                    if isinstance(part, dict)
                ).strip()
                candidates.append({
                    "id": str(item.get("id") or ""),
                    "artist": credit, "title": str(item.get("title") or ""),
                    "url": f"https://musicbrainz.org/recording/{item.get('id', '')}",
                    "source": "musicbrainz",
                })
        except Exception as exc:
            provider_error = self._safe_provider_error(exc)

        if not candidates:
            fallback = self.search_music(
                f"{artist} {title} official audio", limit=5,
                youtube_api_key=youtube_api_key, allow_browser=allow_browser,
            )
            candidates = list(fallback.get("results") or [])
            if not candidates and fallback.get("attempts"):
                provider_error = provider_error or "all metadata providers unavailable"

        ranked = []
        for item in candidates:
            artist_score = self._match_ratio(artist, item.get("artist"))
            title_score = self._match_ratio(title, item.get("title"))
            score = round(artist_score * 0.45 + title_score * 0.55, 4)
            ranked.append((score, artist_score, title_score, item))
        ranked.sort(key=lambda value: value[0], reverse=True)
        best = ranked[0] if ranked else (0.0, 0.0, 0.0, {})
        verified = bool(best[1] >= 0.72 and best[2] >= 0.82 and best[0] >= 0.80)
        return {
            "ok": bool(ranked), "tool": "verify_track", "verified": verified,
            "artist": artist, "title": title, "score": best[0],
            "artist_score": round(best[1], 4), "title_score": round(best[2], 4),
            "match": best[3], "source": str(best[3].get("source") or ""),
            "error": provider_error if not ranked else "",
        }

    def _http_page(self, url, max_chars):
        redirect_handler = _SafeRedirectHandler(self.validate_public_url)
        opener = urllib.request.build_opener(redirect_handler)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "VectorRadio/1.0 (+https://github.com/zhuravskayyar/ai-radio-ukr)",
            },
        )
        with opener.open(request, timeout=self.timeout) as response:
            final_url = self.validate_public_url(response.geturl())
            content_type = str(response.headers.get("Content-Type") or "").casefold()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                raise ResearchToolError("Only HTML pages can be read")
            charset = response.headers.get_content_charset() or "utf-8"
            raw = response.read(MAX_HTTP_BYTES)
        parser = _VisibleTextParser()
        parser.feed(raw.decode(charset, errors="replace"))
        text = " ".join(parser.text_parts)
        return {
            "title": " ".join(parser.title_parts)[:300],
            "text": text[:max_chars], "final_url": final_url,
            "truncated": len(text) > max_chars,
        }

    def _playwright_page(self, url, max_chars):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ResearchToolError("Playwright is not installed") from exc
        with sync_playwright() as playwright:
            browser = None
            last_error = None
            for channel in ("msedge", "chrome", None):
                try:
                    kwargs = {"headless": True}
                    if channel:
                        kwargs["channel"] = channel
                    browser = playwright.chromium.launch(**kwargs)
                    break
                except Exception as exc:
                    last_error = exc
            if browser is None:
                raise ResearchToolError("No Playwright browser is available") from last_error
            try:
                context = browser.new_context(java_script_enabled=True)

                def guard(route, request):
                    if request.resource_type in {"image", "media", "font"}:
                        route.abort()
                        return
                    try:
                        self.validate_public_url(request.url)
                    except ResearchToolError:
                        route.abort()
                        return
                    route.continue_()

                context.route("**/*", guard)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                final_url = self.validate_public_url(page.url)
                text = " ".join(page.locator("body").inner_text().split())
                return {
                    "title": str(page.title() or "")[:300],
                    "text": text[:max_chars], "final_url": final_url,
                    "truncated": len(text) > max_chars,
                }
            finally:
                browser.close()

    def open_webpage(self, url, *, max_chars=4000, allow_browser=False):
        url = self.validate_public_url(url)
        try:
            max_chars = int(max_chars)
        except (TypeError, ValueError):
            max_chars = 4000
        max_chars = max(200, min(MAX_PAGE_CHARS, max_chars))
        attempts = []
        try:
            page = self._http_page(url, max_chars)
            attempts.append({"provider": "http", "ok": True})
        except Exception as exc:
            attempts.append({
                "provider": "http", "ok": False,
                "error": self._safe_provider_error(exc),
            })
            page = None
        if (
            self._truthy(allow_browser)
            and (not page or len(str(page.get("text") or "")) < 200)
        ):
            try:
                page = self._playwright_page(url, max_chars)
                attempts.append({"provider": "playwright", "ok": True})
            except Exception as exc:
                attempts.append({
                    "provider": "playwright", "ok": False,
                    "error": self._safe_provider_error(exc),
                })
        return {
            "ok": bool(page and page.get("text")), "tool": "open_webpage",
            "source": "playwright" if attempts and attempts[-1].get("provider") == "playwright" and attempts[-1].get("ok") else "http",
            "url": url, **(page or {}), "attempts": attempts,
        }
