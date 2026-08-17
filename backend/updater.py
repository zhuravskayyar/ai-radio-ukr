"""Small, verified updater for installed Vector Radio builds."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.request
from pathlib import Path


APP_VERSION = "1.0.0.7"
REPOSITORY = "zhuravskayyar/ai-radio-ukr"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
PATCH_NAME_TEMPLATE = "Vector_Radio_Patch_{version}.exe"


def _version_tuple(value):
    numbers = [int(part) for part in re.findall(r"\d+", str(value or ""))[:4]]
    # Releases through 1.0.5 used three components. The Windows release line
    # continues as 1.0.0.x, where the fourth component is the patch counter.
    # Normalize both spellings so existing three-component installations see
    # a four-component release as the next update rather than a downgrade.
    if len(numbers) == 4 and numbers[2] == 0:
        numbers = [numbers[0], numbers[1], numbers[3]]
    return tuple((numbers + [0, 0, 0, 0])[:4])


class UpdateManager:
    """Check GitHub Releases and stage a patch only after SHA-256 verification."""

    def __init__(self, root: Path, current_version=APP_VERSION, opener=None):
        self.root = Path(root)
        self.current_version = str(current_version)
        self.opener = opener or urllib.request.urlopen
        self._lock = threading.RLock()
        self._thread = None
        self._state = {
            "ok": True,
            "current_version": self.current_version,
            "latest_version": self.current_version,
            "stage": "idle",
            "percent": 0.0,
            "message": "Перевірка оновлень ще не виконувалась",
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "ready": False,
            "update_available": False,
            "error": "",
            "release_url": "",
        }

    def status(self):
        with self._lock:
            result = dict(self._state)
            result["checking"] = bool(self._thread and self._thread.is_alive())
            return result

    def _set_state(self, **updates):
        with self._lock:
            self._state.update(updates)

    def check(self, force=False):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            if self._state.get("ready") and not force:
                return self.status()
            self._set_state(
                stage="checking",
                percent=0.0,
                message="Перевіряю нову версію…",
                error="",
            )
            self._thread = threading.Thread(
                target=self._check_worker,
                name="vector-radio-updater",
                daemon=True,
            )
            self._thread.start()
        return self.status()

    @staticmethod
    def _request(url):
        return urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"Vector-Radio/{APP_VERSION}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def _read_all(self, url, limit=2_000_000):
        with self.opener(self._request(url), timeout=25) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise RuntimeError("Відповідь сервера оновлень завелика")
        return data

    def _check_worker(self):
        try:
            payload = json.loads(self._read_all(LATEST_RELEASE_URL).decode("utf-8"))
            latest = str(payload.get("tag_name") or "").strip().lstrip("vV")
            if not latest:
                raise RuntimeError("GitHub Release не містить номера версії")
            release_url = str(payload.get("html_url") or "")
            self._set_state(latest_version=latest, release_url=release_url)
            if _version_tuple(latest) <= _version_tuple(self.current_version):
                self._set_state(
                    stage="current",
                    percent=100.0,
                    message=f"Встановлено актуальну версію {self.current_version}",
                    ready=False,
                    update_available=False,
                    error="",
                )
                return

            patch_name = PATCH_NAME_TEMPLATE.format(version=latest)
            checksum_name = f"{patch_name}.sha256"
            assets = {
                str(asset.get("name") or ""): str(asset.get("browser_download_url") or "")
                for asset in payload.get("assets", [])
                if isinstance(asset, dict)
            }
            patch_url = assets.get(patch_name, "")
            checksum_url = assets.get(checksum_name, "")
            if not patch_url or not checksum_url:
                raise RuntimeError(
                    f"Для версії {latest} ще немає перевіреного патча"
                )
            if not (
                patch_url.startswith("https://github.com/")
                and checksum_url.startswith("https://github.com/")
            ):
                raise RuntimeError("Неприпустиме джерело патча")

            expected_text = self._read_all(checksum_url, limit=4096).decode(
                "ascii", errors="ignore"
            )
            match = re.search(r"\b[0-9a-fA-F]{64}\b", expected_text)
            if not match:
                raise RuntimeError("Файл контрольної суми пошкоджений")
            expected_hash = match.group(0).lower()
            update_dir = self.root / "updates"
            update_dir.mkdir(parents=True, exist_ok=True)
            target = update_dir / patch_name
            temporary = target.with_suffix(target.suffix + ".part")
            digest = hashlib.sha256()
            downloaded = 0
            self._set_state(
                stage="downloading",
                percent=0.0,
                message=f"Завантажую Vector Radio {latest}…",
                update_available=True,
                ready=False,
                downloaded_bytes=0,
                total_bytes=0,
            )
            try:
                with self.opener(self._request(patch_url), timeout=60) as response:
                    total = int(response.headers.get("Content-Length") or 0)
                    self._set_state(total_bytes=total)
                    with temporary.open("wb") as output:
                        while True:
                            chunk = response.read(256 * 1024)
                            if not chunk:
                                break
                            output.write(chunk)
                            digest.update(chunk)
                            downloaded += len(chunk)
                            percent = (
                                min(99.0, downloaded / total * 100.0)
                                if total else 0.0
                            )
                            self._set_state(
                                percent=round(percent, 1),
                                downloaded_bytes=downloaded,
                            )
                if digest.hexdigest().lower() != expected_hash:
                    raise RuntimeError("SHA-256 патча не збігається")
                temporary.replace(target)
            finally:
                if temporary.exists():
                    temporary.unlink()

            self._set_state(
                stage="ready",
                percent=100.0,
                message=f"Оновлення {latest} готове до встановлення",
                downloaded_bytes=downloaded,
                total_bytes=downloaded,
                ready=True,
                update_available=True,
                error="",
                patch_path=str(target),
            )
        except Exception as exc:
            self._set_state(
                stage="error",
                message="Не вдалося підготувати оновлення",
                ready=False,
                error=str(exc),
            )

    def patch_path(self):
        with self._lock:
            if not self._state.get("ready"):
                return None
            path = Path(str(self._state.get("patch_path") or ""))
        return path if path.is_file() else None
