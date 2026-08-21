import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from online import create_server


ADMIN_TOKEN = "test-admin-token-with-safe-length"


class FakeDatabase:
    def __init__(self, tracks):
        self._tracks = {track["id"]: dict(track) for track in tracks}

    def track(self, track_id):
        track = self._tracks.get(int(track_id))
        return dict(track) if track else None


class FakeRadioAPI:
    def __init__(self, tracks):
        self.db = FakeDatabase(tracks)

    def bootstrap(self):
        return {
            "ok": True,
            "tracks": [self.db.track(1)],
            "settings": {"station_name": "Vector Radio", "nvidia_api_key": ""},
            "radio_queue": {"ok": True, "items": [self.db.track(1)]},
        }

    def update_status(self):
        return {"ok": True, "available": False}

    def radio_queue_status(self):
        return {"ok": True, "items": [self.db.track(1)], "size": 1, "target": 1}

    def save_settings(self, values):
        return {"ok": True, "settings": values}

    def shutdown(self):
        return {"ok": True}


class OnlineServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "ui").mkdir()
        (self.root / "downloads").mkdir()
        (self.root / "cache" / "covers").mkdir(parents=True)
        (self.root / "ui" / "index.html").write_text(
            "<!doctype html><script src=\"online-bridge.js?v=auto\"></script>",
            encoding="utf-8",
        )
        (self.root / "ui" / "online-bridge.js").write_text("// bridge", encoding="utf-8")
        (self.root / "downloads" / "sample.mp3").write_bytes(b"0123456789")
        (self.root / "cache" / "covers" / "sample.jpg").write_bytes(b"jpeg-data")
        tracks = [{
            "id": 1,
            "artist": "Artist",
            "title": "Title",
            "local_path": "downloads/sample.mp3",
            "cover_path": "cache/covers/sample.jpg",
        }]
        self.api = FakeRadioAPI(tracks)
        self.server = None
        self.thread = None

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2)
        self.temp.cleanup()

    def start_server(self, public_listen=False, allowed_origins=()):
        self.server = create_server(
            self.root,
            "127.0.0.1",
            0,
            admin_token=ADMIN_TOKEN,
            public_listen=public_listen,
            allowed_origins=allowed_origins,
            api=self.api,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5,
        )
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), payload
        connection.close()
        return result

    def rpc(self, method, args=None, headers=None):
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        return self.request(
            "POST",
            f"/api/rpc/{method}",
            json.dumps({"args": args or []}).encode("utf-8"),
            request_headers,
        )

    def test_private_mode_authenticates_and_streams_ranges_with_session_cookie(self):
        self.start_server(public_listen=False)

        status, headers, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["online"])
        self.assertIn("Content-Security-Policy", headers)

        status, _headers, _body = self.rpc("bootstrap")
        self.assertEqual(status, 401)

        status, headers, body = self.rpc(
            "bootstrap", headers={"X-Vector-Radio-Token": ADMIN_TOKEN},
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["online"]["role"], "admin")
        self.assertEqual(payload["tracks"][0]["local_path"], "media/1")
        self.assertEqual(payload["tracks"][0]["cover_path"], "cover/1")
        cookie = headers["Set-Cookie"].split(";", 1)[0]

        status, headers, body = self.request(
            "GET", "/media/1", headers={"Cookie": cookie, "Range": "bytes=2-5"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], "bytes 2-5/10")
        self.assertEqual(body, b"2345")

        status, _headers, body = self.request("GET", "/api.txt")
        self.assertEqual(status, 404)
        self.assertNotIn(ADMIN_TOKEN.encode("utf-8"), body)

    def test_public_listener_can_play_but_cannot_change_settings(self):
        self.start_server(public_listen=True)

        status, _headers, body = self.rpc("bootstrap")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["online"]["role"], "listener")
        self.assertEqual(payload["tracks"][0]["local_path"], "media/1")

        status, _headers, _body = self.rpc("save_settings", [{"station_name": "X"}])
        self.assertEqual(status, 403)

        status, headers, body = self.request(
            "GET", "/media/1", headers={"Range": "bytes=-3"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], "bytes 7-9/10")
        self.assertEqual(body, b"789")

    def test_cross_origin_rpc_is_rejected(self):
        self.start_server(public_listen=True)
        status, _headers, _body = self.rpc(
            "bootstrap", headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(status, 403)

    def test_explicit_github_pages_origin_gets_cors_for_api_and_media(self):
        pages_origin = "https://zhuravskayyar.github.io"
        self.start_server(public_listen=True, allowed_origins=[pages_origin])

        status, headers, body = self.rpc(
            "bootstrap", headers={"Origin": pages_origin},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], pages_origin)
        self.assertEqual(json.loads(body)["online"]["role"], "listener")

        status, headers, _body = self.request(
            "OPTIONS",
            "/api/rpc/bootstrap",
            headers={
                "Origin": pages_origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], pages_origin)
        self.assertIn("POST", headers["Access-Control-Allow-Methods"])

        status, headers, body = self.request(
            "GET",
            "/media/1",
            headers={"Origin": pages_origin, "Range": "bytes=0-1"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(headers["Access-Control-Allow-Origin"], pages_origin)
        self.assertEqual(body, b"01")


if __name__ == "__main__":
    unittest.main()
