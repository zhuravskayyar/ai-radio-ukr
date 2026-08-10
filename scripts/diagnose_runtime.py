"""Print a secret-safe Vector Radio runtime diagnostic summary."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path


KEY_PATTERNS = {
    "NVIDIA": re.compile(r"nvapi-[A-Za-z0-9_-]{16,}"),
    "OpenRouter": re.compile(r"sk-or-(?:v1-)?[A-Za-z0-9_-]{16,}"),
    "YouTube": re.compile(r"AIza[A-Za-z0-9_-]{20,}"),
}

SAFE_SETTINGS = {
    "station_prompt",
    "dynamic_discovery_enabled",
    "licensed_sources_confirmed",
    "queue_size",
    "queue_refill_threshold",
    "queue_critical_threshold",
    "queue_min_duration",
    "queue_max_duration",
    "primary_ai_provider",
    "secondary_api_enabled",
    "secondary_api_url",
    "secondary_model",
}


def providers_in_file(path: Path) -> list[str]:
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    return [name for name, pattern in KEY_PATTERNS.items() if pattern.search(raw)]


def diagnose(root: Path) -> dict:
    root = root.resolve()
    db_path = root / "data" / "radio.db"
    result = {
        "root": str(root),
        "database_exists": db_path.is_file(),
        "key_files": {},
    }
    for name in ("api.txt", "apitest.txt"):
        path = root / name
        result["key_files"][name] = {
            "exists": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else 0,
            "providers": providers_in_file(path),
        }
    if not db_path.is_file():
        return result

    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        settings = {
            row["key"]: row["value"]
            for row in db.execute("SELECT key, value FROM settings")
        }
        result["settings"] = {
            key: value for key, value in settings.items() if key in SAFE_SETTINGS
        }
        result["configured_keys"] = {
            key: bool(str(settings.get(key, "")).strip())
            for key in ("nvidia_api_key", "secondary_api_key", "youtube_api_key")
        }
        result["counts"] = {
            "tracks": db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],
            "ready_tracks": db.execute(
                "SELECT COUNT(*) FROM tracks "
                "WHERE status = 'ready' AND length(coalesce(local_path, '')) > 0"
            ).fetchone()[0],
            "ai_tracks": db.execute(
                "SELECT COUNT(*) FROM tracks WHERE library_source = 'ai'"
            ).fetchone()[0],
            "queue": db.execute("SELECT COUNT(*) FROM radio_queue").fetchone()[0],
        }
        result["playable_files"] = sum(
            1
            for row in db.execute(
                "SELECT local_path FROM tracks WHERE length(coalesce(local_path, '')) > 0"
            )
            if (root / str(row["local_path"])).is_file()
        )

    result["discovery_gate"] = {
        "enabled": str(result["settings"].get("dynamic_discovery_enabled", "0")).casefold()
        in {"1", "true", "yes", "on"},
        "rights_confirmed": str(
            result["settings"].get("licensed_sources_confirmed", "0")
        ).casefold()
        in {"1", "true", "yes", "on"},
        "completion_api_available": bool(
            result["configured_keys"]["nvidia_api_key"]
            or result["configured_keys"]["secondary_api_key"]
            or any(
                set(item["providers"]) & {"NVIDIA", "OpenRouter"}
                for item in result["key_files"].values()
            )
        ),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--ascii", action="store_true",
        help="Escape non-ASCII characters for code-page-safe diagnostics.",
    )
    args = parser.parse_args()
    print(json.dumps(diagnose(args.root), ensure_ascii=args.ascii, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
