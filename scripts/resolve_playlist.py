"""Resolve a quota-safe batch of pending playlist tracks through YouTube API."""
from pathlib import Path
import argparse
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from backend.api import RadioAPI


parser = argparse.ArgumentParser()
parser.add_argument("--limit", type=int, default=80)
args = parser.parse_args()

api = RadioAPI(root)
pending = [track for track in api.bootstrap()["tracks"] if not track["youtube_id"] and track["status"] == "pending"]
ready = failed = 0
for track in pending[: args.limit]:
    result = api.resolve_track(track["id"])
    if result.get("ok"):
        ready += 1
        print(f'OK #{track["rank"]}: {track["artist"]} - {track["title"]}', flush=True)
    else:
        failed += 1
        error = result.get("error", "unknown error")
        print(f'FAIL #{track["rank"]}: {error}', flush=True)
        if "quota" in error.casefold() or "403" in error:
            print("STOP: YouTube daily quota reached", flush=True)
            break

total_ready = len([track for track in api.bootstrap()["tracks"] if track["youtube_id"]])
print(f"SUMMARY checked={ready + failed} resolved={ready} failed={failed} total_ready={total_ready}", flush=True)
