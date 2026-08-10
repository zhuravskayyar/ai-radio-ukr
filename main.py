from pathlib import Path
import ctypes
import faulthandler
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import logging
import os
import re
import sys
import threading
import time

from backend.api import RadioAPI

_CACHEBUST_RE = re.compile(r'(style\.css|library\.css|radio-copy\.css|vector\.css|app\.js)\?v=auto')
_SINGLE_INSTANCE_MUTEX_NAME = "Global\\VectorRadioSingleInstance"
_SINGLE_INSTANCE_HANDLE = None


def _acquire_single_instance_lock(timeout=15.0, poll=0.25):
    """Return True if this is the only running instance (Windows named mutex).

    Two windows editing the same SQLite settings independently is what makes a
    saved station style look like it "reverts" - each window keeps its own
    stale in-memory copy and overwrites the other's view when you switch tabs.
    Retries for a few seconds so a style-change auto-restart (old process
    still shutting down) does not get mistaken for a real second instance.
    """
    if sys.platform != "win32":
        return True
    global _SINGLE_INSTANCE_HANDLE
    kernel32 = ctypes.windll.kernel32
    if hasattr(kernel32.CreateMutexW, "argtypes"):
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
    ERROR_ALREADY_EXISTS = 183
    deadline = time.monotonic() + timeout
    while True:
        handle = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
        if kernel32.GetLastError() != ERROR_ALREADY_EXISTS:
            _SINGLE_INSTANCE_HANDLE = handle
            return True
        # A failed contender also receives a handle. It must be closed before
        # waiting, otherwise this new process keeps the old named mutex alive
        # itself and can never acquire it after the previous app exits.
        if handle:
            kernel32.CloseHandle(handle)
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)


def _release_single_instance_lock():
    global _SINGLE_INSTANCE_HANDLE
    if sys.platform == "win32" and _SINGLE_INSTANCE_HANDLE:
        try:
            ctypes.windll.kernel32.CloseHandle(_SINGLE_INSTANCE_HANDLE)
        finally:
            _SINGLE_INSTANCE_HANDLE = None


def main():
    if not _acquire_single_instance_lock():
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                "Vector Radio вже запущено в іншому вікні. Закрийте його перед повторним запуском.",
                "Vector Radio",
                0x40,
            )
        except Exception:
            pass
        return
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "VectorRadio.Desktop.1"
            )
        except Exception:
            pass
    # Desktop radio should be allowed to start its local audio automation
    # without requiring an extra click in WebView2.
    os.environ.setdefault(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--autoplay-policy=no-user-gesture-required",
    )
    log_path = Path(__file__).resolve().parent / "vector-radio.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
        force=True,
    )
    fault_log = open(log_path, "a", encoding="utf-8")
    faulthandler.enable(fault_log)
    logging.info("Vector Radio startup: importing pywebview")
    try:
        import webview
    except ImportError:
        print("pywebview is not installed. Run: pip install -r requirements.txt")
        raise SystemExit(1)
    logging.info("Vector Radio startup: pywebview imported")

    root = Path(__file__).resolve().parent
    api = RadioAPI(root, enable_auto_restart=True)
    logging.info("Vector Radio startup: backend ready")
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def end_headers(self):
            # The desktop UI is developed in-place. WebView2 otherwise keeps
            # stale CSS/JS between launches and can make a successful redesign
            # look as if it was never applied.
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            super().end_headers()

        def do_GET(self):
            if self.path.split("?", 1)[0].split("#", 1)[0] == "/ui/index.html":
                return self._serve_index_with_cachebust()
            return super().do_GET()

        def _serve_index_with_cachebust(self):
            # ?v=auto is rewritten to each asset's own mtime, so every edit to
            # style.css/app.js is picked up on the next reload with no manual
            # version bump in index.html.
            index_path = root / "ui" / "index.html"
            html = index_path.read_text(encoding="utf-8")

            def bump(match):
                filename = match.group(1)
                try:
                    version = int((root / "ui" / filename).stat().st_mtime)
                except OSError:
                    version = 0
                return f"{filename}?v={version}"

            html = _CACHEBUST_RE.sub(bump, html)
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/ui/index.html"
    logging.info("Vector Radio startup: UI server %s", url)
    window = webview.create_window(
        "Vector Radio",
        url,
        js_api=api,
        width=1040,
        height=800,
        min_size=(760, 620),
        background_color="#050607",
    )
    logging.info("Vector Radio startup: entering WebView event loop")
    try:
        # Auto GUI selection occasionally stalls before WebView2 is created on
        # Windows. Vector Radio ships for the installed Edge runtime, so select it
        # explicitly and fail visibly in the log if the runtime is unavailable.
        icon_path = next(
            (
                candidate
                for candidate in (
                    root / "assets" / "vector-radio.ico",
                    root / "packaging" / "assets" / "vector-radio.ico",
                )
                if candidate.is_file()
            ),
            None,
        )
        webview.start(
            gui="edgechromium",
            debug="--debug" in sys.argv,
            private_mode=False,
            icon=str(icon_path) if icon_path else None,
        )
    except Exception:
        logging.exception("Vector Radio startup: WebView failed")
        raise
    finally:
        try:
            api.shutdown()
        except Exception:
            logging.exception("Vector Radio shutdown failed")
        server.shutdown()
        _release_single_instance_lock()


if __name__ == "__main__":
    main()
