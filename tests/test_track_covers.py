import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api import RadioAPI


class _ImageResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.payload


class TrackCoverTests(unittest.TestCase):
    def test_youtube_cover_is_cached_locally_and_persisted(self):
        payload = b"\xff\xd8\xff" + b"cover" * 140
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            with patch(
                "backend.api.urllib.request.urlopen",
                return_value=_ImageResponse(payload),
            ) as urlopen:
                cover_path = api._cache_track_cover("uhG-vLZrb-g")
                self.assertEqual(
                    api._cache_track_cover("uhG-vLZrb-g"), cover_path,
                )

            self.assertEqual(urlopen.call_count, 1)
            self.assertEqual(cover_path, "cache/covers/uhG-vLZrb-g.jpg")
            self.assertEqual((Path(directory) / cover_path).read_bytes(), payload)

            track = api.db.add_local_track("Fall Out Boy", "Sugar", "song.mp3")
            api.db.update_track(track["id"], cover_path=cover_path)
            self.assertEqual(api.db.track(track["id"])["cover_path"], cover_path)
            api.shutdown()

    def test_invalid_video_id_is_rejected_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RadioAPI(Path(directory))
            with patch("backend.api.urllib.request.urlopen") as urlopen:
                self.assertEqual(api._cache_track_cover("not a video id"), "")
            urlopen.assert_not_called()
            api.shutdown()


if __name__ == "__main__":
    unittest.main()
