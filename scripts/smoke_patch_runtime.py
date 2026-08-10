"""Exercise the patched backend and verify that migrated audio is decodable."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    sys.path.insert(0, str(root))

    from backend.api import RadioAPI
    from mutagen import File as MutagenFile

    api = RadioAPI(root)
    try:
        state = api.bootstrap()
        tracks = state.get("tracks") or []
        queue = state.get("radio_queue") or {}
        audio_checks = []
        for track in tracks:
            path = root / str(track.get("local_path") or "")
            audio = MutagenFile(path)
            duration = float(getattr(getattr(audio, "info", None), "length", 0) or 0)
            audio_checks.append({
                "file": path.name,
                "bytes": path.stat().st_size,
                "duration_seconds": round(duration, 1),
                "decodable": duration > 0,
            })
        result = {
            "ok": bool(state.get("ok")),
            "library_tracks": len(tracks),
            "queue_size": queue.get("size", 0),
            "queue_phase": queue.get("phase", ""),
            "blocked_reason": queue.get("blocked_reason", ""),
            "all_audio_decodable": bool(audio_checks) and all(
                item["decodable"] for item in audio_checks
            ),
            "audio": audio_checks,
        }
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result["ok"] and result["all_audio_decodable"] else 1
    finally:
        api.radio_queue.stop()


if __name__ == "__main__":
    raise SystemExit(main())
