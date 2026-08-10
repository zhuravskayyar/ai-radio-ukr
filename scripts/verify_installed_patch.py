"""Print non-secret health data for an installed Vector Radio patch."""

import sqlite3
import sys
from pathlib import Path


def main(root_value):
    root = Path(root_value).resolve()
    sys.path.insert(0, str(root))
    from backend.updater import APP_VERSION

    with sqlite3.connect(root / "data" / "radio.db") as connection:
        schema = connection.execute("PRAGMA user_version").fetchone()[0]
        ai_tracks = connection.execute(
            "SELECT COUNT(*) FROM tracks WHERE library_source='ai'"
        ).fetchone()[0]
        settings = dict(connection.execute("SELECT key, value FROM settings"))
    checks = {
        "APP_VERSION": APP_VERSION,
        "SCHEMA": schema,
        "AI_TRACKS": ai_tracks,
        "DISCOVERY": settings.get("dynamic_discovery_enabled", ""),
        "TECH_GATE": settings.get("licensed_sources_confirmed", ""),
        "API_PRESENT": bool(
            settings.get("nvidia_api_key") or settings.get("secondary_api_key")
        ),
        "DOWNLOADER_MODULE": (
            root / "Qwen_python_20260804_4sskbslqs.py"
        ).is_file(),
        "UPDATER_MODULE": (root / "backend" / "updater.py").is_file(),
    }
    for key, value in checks.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main(sys.argv[1])
