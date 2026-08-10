"""Run a real StyleTTS2 synthesis through the public LUMEN Radio API."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api import RadioAPI
from backend.tts_styletts import styletts_status


def main() -> int:
    api = RadioAPI(ROOT)
    api.db.save_settings({"use_styletts": "1"})
    text = " ".join(sys.argv[1:]).strip() or (
        "Вітаю. Локальний український голос ЛЮМЕН Радіо вже працює в ефірі."
    )
    started = time.perf_counter()
    result = api.synthesize_speech(text)
    printable = {key: value for key, value in result.items() if key != "audio"}
    printable["render_seconds"] = round(time.perf_counter() - started, 2)
    printable["audio_mime"] = (result.get("audio") or "").partition(",")[0]
    printable["styletts_status"] = styletts_status()
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") and result.get("provider") == "styletts2" else 1


if __name__ == "__main__":
    raise SystemExit(main())
