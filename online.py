"""Secure browser/LAN server for Vector Radio.

The desktop application talks to ``RadioAPI`` through pywebview.  This module
exposes the same UI through a deliberately small, authenticated JSON-RPC
surface and streams only media files that belong to a database track.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import signal
import threading
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from backend.api import RadioAPI


LOGGER = logging.getLogger("vector-radio-online")
MAX_RPC_BODY = 2 * 1024 * 1024
SESSION_COOKIE = "vector_radio_session"
SESSION_MAX_AGE = 24 * 60 * 60

RPC_METHODS = frozenset({
    "add_music_story",
    "add_track_fact",
    "advance_radio_queue",
    "apply_update",
    "benchmark_ai_providers",
    "bootstrap",
    "broadcast_safety_status",
    "emergency_protocol",
    "generate_track_pronunciation",
    "get_prepared_transition",
    "import_api_text",
    "make_intro",
    "mark_played",
    "mark_transition_aired",
    "pilot_hour",
    "prepare_transition_queue",
    "queue_correction",
    "radio_queue_status",
    "record_broadcast_event",
    "record_listener_feedback",
    "refresh_ai_library",
    "request_radio_queue_refill",
    "research_track_intro",
    "reseed_radio_queue",
    "resolve_broadcast_event",
    "resolve_track",
    "save_settings",
    "set_track_analysis",
    "set_track_pronunciation",
    "synthesize_speech",
    "update_status",
    "warm_tts",
})

PUBLIC_RPC_METHODS = frozenset({
    "bootstrap",
    "broadcast_safety_status",
    "get_prepared_transition",
    "pilot_hour",
    "radio_queue_status",
    "update_status",
})

PUBLIC_NOOP_METHODS = frozenset({
    "advance_radio_queue",
    "mark_played",
    "mark_transition_aired",
    "prepare_transition_queue",
    "record_listener_feedback",
    "reseed_radio_queue",
})

STATIC_EXTENSIONS = frozenset({
    ".css", ".html", ".js", ".json", ".png", ".jpg", ".jpeg",
    ".svg", ".webp", ".ico", ".webmanifest", ".woff", ".woff2",
})

_CACHEBUST_RE = re.compile(
    r"(style\.css|library\.css|radio-copy\.css|vector\.css|boombox\.css|"
    r"online-config\.js|online-bridge\.js|app\.js)\?v=auto"
)


def _safe_token(value: str) -> str:
    value = str(value or "").strip()
    if value and len(value) < 16:
        raise ValueError("Токен адміністратора має містити щонайменше 16 символів")
    if len(value) > 512:
        raise ValueError("Токен адміністратора завеликий")
    return value


def _session_value(token: str, nonce: str) -> str:
    return hmac.new(
        token.encode("utf-8"),
        ("vector-radio-online-session\0" + nonce).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _normalize_origin(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Некоректний allowed origin: {value}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"Некоректний allowed origin: {value}")
    return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}"


def _rewrite_media_paths(value):
    """Replace local filesystem paths with opaque, track-scoped URLs."""
    if isinstance(value, list):
        return [_rewrite_media_paths(item) for item in value]
    if not isinstance(value, dict):
        return value

    rewritten = {key: _rewrite_media_paths(item) for key, item in value.items()}
    track_id = value.get("id")
    try:
        track_id = int(track_id)
    except (TypeError, ValueError):
        track_id = None
    if track_id is not None:
        if value.get("local_path"):
            rewritten["local_path"] = f"media/{track_id}"
        if value.get("cover_path"):
            rewritten["cover_path"] = f"cover/{track_id}"

    nested_track = value.get("track")
    if isinstance(nested_track, dict) and nested_track.get("id"):
        nested_id = int(nested_track["id"])
        if value.get("local_path"):
            rewritten["local_path"] = f"media/{nested_id}"
        if value.get("cover_path"):
            rewritten["cover_path"] = f"cover/{nested_id}"
    return rewritten


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


class VectorRadioHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address,
        handler_class,
        *,
        root: Path,
        api,
        admin_token: str,
        public_listen: bool,
        secure_cookie: bool,
        allowed_origins,
    ):
        super().__init__(server_address, handler_class)
        self.root = Path(root).resolve()
        self.ui_root = (self.root / "ui").resolve()
        self.api = api
        self.admin_token = admin_token
        self.public_listen = bool(public_listen)
        self.secure_cookie = bool(secure_cookie)
        self.allowed_origins = frozenset(
            origin for origin in (_normalize_origin(value) for value in allowed_origins)
            if origin
        )
        self.session_nonce = secrets.token_urlsafe(24)
        self.session_value = _session_value(admin_token, self.session_nonce)


class VectorRadioRequestHandler(BaseHTTPRequestHandler):
    server_version = "VectorRadioOnline/1.0"

    @property
    def app(self) -> VectorRadioHTTPServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format, *args):
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: https://i.ytimg.com; "
            "media-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        origin = self.headers.get("Origin", "").strip()
        if origin and self._origin_permitted(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self):
        path = urlsplit(self.path).path
        origin = self.headers.get("Origin", "").strip()
        if not path.startswith("/api/") or not origin or not self._origin_permitted(origin):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Vector-Radio-Token, Authorization",
        )
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self._handle_get(head_only=True)

    def do_GET(self):
        self._handle_get(head_only=False)

    def do_POST(self):
        path = urlsplit(self.path).path
        if not path.startswith("/api/rpc/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self._origin_permitted():
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"ok": False, "error": "Запит має походити з цього самого сайту"},
            )
            return
        method = unquote(path.removeprefix("/api/rpc/")).strip()
        if method not in RPC_METHODS:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "error": "Невідомий API-метод"},
            )
            return

        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"ok": False, "error": "Потрібен Content-Type application/json"},
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_RPC_BODY:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": "Запит перевищує дозволений розмір"},
            )
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            args = payload.get("args", [])
            kwargs = payload.get("kwargs", {})
            if not isinstance(args, list) or not isinstance(kwargs, dict):
                raise ValueError("Некоректні аргументи")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": f"Некоректний JSON: {exc}"},
            )
            return

        admin_from_header = self._header_token_is_admin()
        role = "admin" if (admin_from_header or self._cookie_is_admin()) else (
            "listener" if self.app.public_listen else "none"
        )
        if role == "none":
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"ok": False, "error": "Потрібен токен адміністратора"},
            )
            return
        if role == "listener" and method not in PUBLIC_RPC_METHODS | PUBLIC_NOOP_METHODS:
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"ok": False, "error": "Ця дія доступна лише адміністратору"},
            )
            return

        try:
            result = self._dispatch_rpc(method, args, kwargs, role)
            result = _rewrite_media_paths(result)
        except Exception as exc:
            LOGGER.exception("Online RPC %s failed", method)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                set_session=admin_from_header,
            )
            return
        self._send_json(HTTPStatus.OK, result, set_session=admin_from_header)

    def _handle_get(self, head_only: bool):
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {
                "ok": True,
                "online": True,
                "public_listen": self.app.public_listen,
            }, head_only=head_only)
            return
        if path.startswith("/media/"):
            self._serve_track_asset(path, "local_path", head_only)
            return
        if path.startswith("/cover/"):
            self._serve_track_asset(path, "cover_path", head_only)
            return
        self._serve_static(path, head_only)

    def _dispatch_rpc(self, method: str, args: list, kwargs: dict, role: str):
        if role == "listener" and method in PUBLIC_NOOP_METHODS:
            if method in {"advance_radio_queue", "reseed_radio_queue"}:
                return {
                    "ok": True,
                    "items": [],
                    "size": 0,
                    "target": 0,
                    "refilling": False,
                    "online_listener": True,
                }
            if method == "prepare_transition_queue":
                return {
                    "ok": True,
                    "busy": False,
                    "prepared": [],
                    "online_listener": True,
                }
            return {"ok": True, "online_listener": True}

        target = getattr(self.app.api, method, None)
        if not callable(target):
            raise AttributeError(f"API-метод {method} недоступний")
        result = target(*args, **kwargs)
        if method == "bootstrap" and isinstance(result, dict):
            result = dict(result)
            result["online"] = {
                "enabled": True,
                "role": role,
                "public_listen": self.app.public_listen,
                "media_range": True,
                "pwa": True,
            }
        return result

    def _origin_permitted(self, origin=None) -> bool:
        origin = str(origin if origin is not None else self.headers.get("Origin", "")).strip()
        if not origin:
            return True
        try:
            normalized = _normalize_origin(origin)
            origin_host = urlsplit(normalized).netloc.casefold()
        except ValueError:
            return False
        request_host = self.headers.get("Host", "").casefold()
        return bool(
            origin_host
            and (
                hmac.compare_digest(origin_host, request_host)
                or normalized in self.app.allowed_origins
            )
        )

    def _presented_token(self) -> str:
        direct = self.headers.get("X-Vector-Radio-Token", "").strip()
        if direct:
            return direct
        authorization = self.headers.get("Authorization", "")
        if authorization.casefold().startswith("bearer "):
            return authorization[7:].strip()
        return ""

    def _header_token_is_admin(self) -> bool:
        presented = self._presented_token()
        return bool(presented) and hmac.compare_digest(presented, self.app.admin_token)

    def _cookie_is_admin(self) -> bool:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            value = cookie.get(SESSION_COOKIE)
            presented = value.value if value else ""
        except Exception:
            return False
        return bool(presented) and hmac.compare_digest(presented, self.app.session_value)

    def _authorized_for_media(self) -> bool:
        return self.app.public_listen or self._header_token_is_admin() or self._cookie_is_admin()

    def _serve_track_asset(self, request_path: str, field: str, head_only: bool):
        if not self._authorized_for_media():
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"ok": False, "error": "Потрібна авторизація"},
                head_only=head_only,
            )
            return
        try:
            track_id = int(request_path.rstrip("/").rsplit("/", 1)[-1])
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        track = self.app.api.db.track(track_id)
        relative = str((track or {}).get(field) or "").strip()
        if not relative:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        candidate = (self.app.root / relative).resolve()
        allowed = (
            ((self.app.root / "downloads").resolve(), (self.app.root / "music").resolve())
            if field == "local_path"
            else ((self.app.root / "cache" / "covers").resolve(),)
        )
        if not candidate.is_file() or not any(_is_within(candidate, directory) for directory in allowed):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._serve_file(candidate, head_only, allow_range=field == "local_path")

    def _serve_static(self, request_path: str, head_only: bool):
        if request_path in {"", "/"}:
            request_path = "/ui/index.html"
        if not request_path.startswith("/ui/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        relative = unquote(request_path.removeprefix("/ui/"))
        candidate = (self.app.ui_root / relative).resolve()
        if (
            not candidate.is_file()
            or not _is_within(candidate, self.app.ui_root)
            or candidate.suffix.casefold() not in STATIC_EXTENSIONS
        ):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if candidate.name == "index.html":
            self._serve_index(candidate, head_only)
            return
        self._serve_file(candidate, head_only, cache_static=True)

    def _serve_index(self, path: Path, head_only: bool):
        html = path.read_text(encoding="utf-8")

        def bump(match):
            filename = match.group(1)
            try:
                version = int((self.app.ui_root / filename).stat().st_mtime)
            except OSError:
                version = 0
            return f"{filename}?v={version}"

        body = _CACHEBUST_RE.sub(bump, html).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _serve_file(
        self,
        path: Path,
        head_only: bool,
        *,
        allow_range: bool = False,
        cache_static: bool = False,
    ):
        size = path.stat().st_size
        start, end = 0, max(0, size - 1)
        status = HTTPStatus.OK
        range_header = self.headers.get("Range", "") if allow_range else ""
        if range_header:
            parsed = self._parse_range(range_header, size)
            if parsed is None:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = parsed
            status = HTTPStatus.PARTIAL_CONTENT

        content_length = max(0, end - start + 1)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        if allow_range:
            self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if path.name == "service-worker.js":
            self.send_header("Service-Worker-Allowed", "/ui/")
            self.send_header("Cache-Control", "no-cache")
        elif cache_static:
            self.send_header("Cache-Control", "public, max-age=3600")
        else:
            self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        if head_only or content_length == 0:
            return
        with path.open("rb") as source:
            source.seek(start)
            remaining = content_length
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    @staticmethod
    def _parse_range(header: str, size: int):
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
        if not match or size <= 0:
            return None
        first, last = match.groups()
        if not first and not last:
            return None
        if not first:
            suffix = int(last)
            if suffix <= 0:
                return None
            return max(0, size - suffix), size - 1
        start = int(first)
        end = int(last) if last else size - 1
        if start >= size or start > end:
            return None
        return start, min(end, size - 1)

    def _send_json(
        self,
        status: HTTPStatus,
        payload,
        *,
        set_session: bool = False,
        head_only: bool = False,
    ):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if set_session:
            secure = "; Secure" if self.app.secure_cookie else ""
            same_site = "None" if self.app.secure_cookie else "Strict"
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}={self.app.session_value}; Path=/; HttpOnly; "
                f"SameSite={same_site}; Max-Age={SESSION_MAX_AGE}{secure}",
            )
        self.end_headers()
        if not head_only:
            self.wfile.write(body)


def create_server(
    root: Path,
    host: str,
    port: int,
    *,
    admin_token: str,
    public_listen: bool = False,
    secure_cookie: bool = False,
    allowed_origins=(),
    api=None,
):
    root = Path(root).resolve()
    if not (root / "ui" / "index.html").is_file():
        raise FileNotFoundError(root / "ui" / "index.html")
    token = _safe_token(admin_token)
    if not token:
        raise ValueError("Порожній токен адміністратора заборонено")
    radio_api = api or RadioAPI(root, enable_auto_restart=False)
    return VectorRadioHTTPServer(
        (host, int(port)),
        VectorRadioRequestHandler,
        root=root,
        api=radio_api,
        admin_token=token,
        public_listen=public_listen,
        secure_cookie=secure_cookie,
        allowed_origins=allowed_origins,
    )


def _display_host(host: str) -> str:
    return "127.0.0.1" if host in {"0.0.0.0", "::"} else host


def main(argv=None):
    parser = argparse.ArgumentParser(description="Vector Radio online/LAN server")
    parser.add_argument("--host", default=os.environ.get("VECTOR_RADIO_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VECTOR_RADIO_PORT", "8080")))
    parser.add_argument(
        "--token",
        default=os.environ.get("VECTOR_RADIO_ADMIN_TOKEN", ""),
        help="admin token (prefer VECTOR_RADIO_ADMIN_TOKEN in production)",
    )
    parser.add_argument(
        "--public-listen",
        action="store_true",
        default=os.environ.get("VECTOR_RADIO_PUBLIC_LISTEN", "0") == "1",
        help="allow playback without an admin token",
    )
    parser.add_argument(
        "--secure-cookie",
        action="store_true",
        default=os.environ.get("VECTOR_RADIO_SECURE_COOKIE", "0") == "1",
        help="mark the admin session cookie Secure (use behind HTTPS)",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        help="exact browser origin allowed to call the API (repeatable)",
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent
    token = _safe_token(args.token) or secrets.token_urlsafe(32)
    environment_origins = [
        value.strip()
        for value in os.environ.get("VECTOR_RADIO_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    ]
    log_path = root / "vector-radio-online.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
        force=True,
    )
    server = create_server(
        root,
        args.host,
        args.port,
        admin_token=token,
        public_listen=args.public_listen,
        secure_cookie=args.secure_cookie,
        allowed_origins=[*environment_origins, *args.allowed_origin],
    )
    host = _display_host(args.host)
    port = server.server_port
    base_url = f"http://{host}:{port}/ui/index.html"
    print(f"Vector Radio online: {base_url}")
    if args.public_listen:
        print("Публічне прослуховування: увімкнено")
    print(f"Адміністратор: {base_url}#token={token}")
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not args.secure_cookie:
        print("Увага: для доступу з інтернету використовуйте HTTPS reverse proxy.")
    print("Зупинка: Ctrl+C")

    stopped = threading.Event()

    def stop_server(*_args):
        if stopped.is_set():
            return
        stopped.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        for signal_name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, signal_name):
                signal.signal(getattr(signal, signal_name), stop_server)
    try:
        server.serve_forever(poll_interval=0.4)
    finally:
        server.server_close()
        try:
            server.api.shutdown()
        except Exception:
            LOGGER.exception("Online API shutdown failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
