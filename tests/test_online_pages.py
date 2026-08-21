import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GitHubPagesClientTests(unittest.TestCase):
    def test_landing_page_links_to_online_player(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="ui/index.html"', html)
        self.assertIn("Слухати онлайн", html)

    def test_pwa_paths_are_relative_to_project_pages_scope(self):
        manifest = json.loads(
            (ROOT / "ui" / "manifest.webmanifest").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["start_url"], "./index.html")
        self.assertEqual(manifest["scope"], "./")
        self.assertFalse(manifest["icons"][0]["src"].startswith("/"))

        worker = (ROOT / "ui" / "service-worker.js").read_text(encoding="utf-8")
        self.assertIn("new URL(path, self.location.href)", worker)
        self.assertNotIn("'/ui/", worker)

    def test_pages_client_has_configurable_https_backend(self):
        html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        bridge = (ROOT / "ui" / "online-bridge.js").read_text(encoding="utf-8")
        config = (ROOT / "ui" / "online-config.js").read_text(encoding="utf-8")
        app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn('src="online-config.js?v=auto"', html)
        self.assertIn('id="onlineConnectionForm"', html)
        self.assertIn("location.hostname.endsWith('.github.io')", bridge)
        self.assertIn("url.protocol !== 'https:'", bridge)
        self.assertIn("apiBase: ''", config)
        self.assertIn("window.VECTOR_RADIO_API_BASE", app)


if __name__ == "__main__":
    unittest.main()
