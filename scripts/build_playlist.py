"""Resolve a quota-safe batch and export verified YouTube playlist files."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from backend.api import RadioAPI


def export(api):
    ready = [track for track in api.db.tracks() if track["status"] == "ready" and track["youtube_id"] and track["match_score"] >= 0.72]
    payload = [{"rank": t["rank"], "artist": t["artist"], "title": t["title"], "youtube_id": t["youtube_id"], "youtube_title": t["youtube_title"], "match_score": t["match_score"], "url": f'https://www.youtube.com/watch?v={t["youtube_id"]}'} for t in ready]
    (ROOT / "data" / "playlist.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["#EXTM3U"]
    for track in payload:
        lines.extend([f'#EXTINF:-1,{track["artist"]} - {track["title"]}', track["url"]])
    (ROOT / "playlist.m3u8").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=90, help="Maximum search.list calls this run")
    args = parser.parse_args()
    api = RadioAPI(ROOT)
    pending = [track for track in api.db.tracks() if track["status"] == "pending"][: max(0, args.limit)]
    found = unavailable = errors = 0
    for index, track in enumerate(pending, 1):
        result = api.resolve_track(track["id"])
        if result.get("ok"):
            found += 1
        elif any(code in result.get("error", "") for code in ("403", "429")) or "quota" in result.get("error", "").lower():
            print(f"YouTube limit stopped at {index}: {result['error']}")
            break
        elif result.get("error", "").startswith("YouTube:"):
            errors += 1
        else:
            unavailable += 1
        print(f"[{index}/{len(pending)}] found={found} unavailable={unavailable} errors={errors} | {track['artist']} - {track['title']}")
        time.sleep(1.2)
    total = export(api)
    print(json.dumps({"searched": found + unavailable + errors, "found": found, "unavailable": unavailable, "errors": errors, "playlist": total}, ensure_ascii=False))


if __name__ == "__main__":
    main()
