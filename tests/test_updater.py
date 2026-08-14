import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from backend.updater import LATEST_RELEASE_URL, UpdateManager, _version_tuple


class FakeResponse:
    def __init__(self, data):
        self._stream = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data))}

    def read(self, size=-1):
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, responses):
        self.responses = responses

    def __call__(self, request, timeout=0):
        url = getattr(request, "full_url", str(request))
        return FakeResponse(self.responses[url])


def release_payload(version, patch_url, checksum_url):
    patch_name = f"Vector_Radio_Patch_{version}.exe"
    return json.dumps({
        "tag_name": f"v{version}",
        "html_url": f"https://github.com/example/releases/tag/v{version}",
        "assets": [
            {"name": patch_name, "browser_download_url": patch_url},
            {
                "name": f"{patch_name}.sha256",
                "browser_download_url": checksum_url,
            },
        ],
    }).encode("utf-8")


class UpdateManagerTests(unittest.TestCase):
    def test_four_component_release_continues_legacy_patch_sequence(self):
        self.assertGreater(_version_tuple("1.0.0.6"), _version_tuple("1.0.5"))
        self.assertEqual(_version_tuple("1.0.0.6"), _version_tuple("1.0.6"))

    def test_new_patch_is_downloaded_and_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            patch_data = b"verified-vector-radio-patch"
            patch_url = "https://github.com/example/download/patch.exe"
            checksum_url = "https://github.com/example/download/patch.exe.sha256"
            expected = hashlib.sha256(patch_data).hexdigest().encode("ascii")
            opener = FakeOpener({
                LATEST_RELEASE_URL: release_payload("1.0.4", patch_url, checksum_url),
                patch_url: patch_data,
                checksum_url: expected + b"  Vector_Radio_Patch_1.0.4.exe\n",
            })
            manager = UpdateManager(Path(directory), "1.0.3", opener=opener)

            manager._check_worker()

            status = manager.status()
            self.assertTrue(status["ready"])
            self.assertEqual(status["stage"], "ready")
            self.assertEqual(status["latest_version"], "1.0.4")
            self.assertEqual(manager.patch_path().read_bytes(), patch_data)

    def test_invalid_checksum_is_rejected_and_partial_file_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            patch_url = "https://github.com/example/download/patch.exe"
            checksum_url = "https://github.com/example/download/patch.exe.sha256"
            opener = FakeOpener({
                LATEST_RELEASE_URL: release_payload("1.0.4", patch_url, checksum_url),
                patch_url: b"tampered",
                checksum_url: b"0" * 64,
            })
            manager = UpdateManager(Path(directory), "1.0.3", opener=opener)

            manager._check_worker()

            status = manager.status()
            self.assertFalse(status["ready"])
            self.assertEqual(status["stage"], "error")
            self.assertIn("SHA-256", status["error"])
            self.assertEqual(list((Path(directory) / "updates").glob("*.part")), [])

    def test_current_version_does_not_download_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            opener = FakeOpener({
                LATEST_RELEASE_URL: release_payload(
                    "1.0.4",
                    "https://github.com/example/download/patch.exe",
                    "https://github.com/example/download/patch.exe.sha256",
                ),
            })
            manager = UpdateManager(Path(directory), "1.0.4", opener=opener)

            manager._check_worker()

            status = manager.status()
            self.assertEqual(status["stage"], "current")
            self.assertFalse(status["update_available"])
            self.assertFalse(status["ready"])


if __name__ == "__main__":
    unittest.main()
