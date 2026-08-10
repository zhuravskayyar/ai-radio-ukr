"""Provision and verify Adam Vector's local Ukrainian StyleTTS2 voice."""
from __future__ import annotations

import hashlib
import os
import sys
import time
import traceback
import wave
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent
HF_HOME = APP_ROOT / "cache" / "huggingface"
STANZA_HOME = APP_ROOT / "cache" / "stanza"
LOG_PATH = APP_ROOT / "cache" / "install-tts.log"
MODEL_REPO = "patriotyk/styletts2_ukrainian_single"
MODEL_REVISION = "2646553e1f9a8c832480e3ad5ccb6839245af584"
STYLE_REPO = "patriotyk/styletts2-ukrainian"
STYLE_REVISION = "b02909e4c9f001865bf71633d76fee7110f657a3"
ASSETS = (
    (
        MODEL_REPO,
        "pytorch_model.bin",
        "model",
        MODEL_REVISION,
        748_848_243,
        "25e78d882ec4ee5a8a361749004edf6914137760f2be33a71ea24ce22da1a24a",
    ),
    (
        MODEL_REPO,
        "config.yml",
        "model",
        MODEL_REVISION,
        1_434,
        "5c426957b2d5578e00869330c1949003092933cac2c42a2dcbbbea84c6774463",
    ),
    (
        STYLE_REPO,
        "filatov.pt",
        "space",
        STYLE_REVISION,
        2_204,
        "f181646626df52fdcf749e93a311686ffb2eaeae8112be0005a8d6efa7dc5cc9",
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as stream:
        stream.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def retry(label: str, action, attempts: int = 3) -> None:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            log(f"{label}: attempt {attempt}/{attempts}")
            action()
            log(f"{label}: ready")
            return
        except Exception as exc:  # Network and upstream errors vary by package.
            last_error = exc
            log(f"{label}: {exc}\n{traceback.format_exc()}")
            if attempt < attempts:
                time.sleep(2 * attempt)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}")


def download_voice_assets() -> None:
    from huggingface_hub import hf_hub_download

    for repo_id, filename, repo_type, revision, size, expected_hash in ASSETS:
        path = Path(
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                revision=revision,
                repo_type=repo_type,
            )
        )
        if path.stat().st_size != size or sha256(path) != expected_hash:
            raise RuntimeError(f"Integrity check failed for {repo_id}/{filename}")


def download_stanza_assets() -> None:
    # Stressifier owns the exact Stanza package selection used at runtime.  A
    # real call downloads and validates those Ukrainian NLP resources now,
    # instead of surprising the listener during the first broadcast.
    from ukrainian_word_stress import Stressifier

    stressifier = Stressifier()
    if not stressifier("Вітаю, це голос Адама Вектора."):
        raise RuntimeError("Ukrainian stress pipeline returned an empty result")
    required = (
        "resources.json",
        "uk/tokenize/iu.pt",
        "uk/mwt/iu.pt",
        "uk/pos/iu_charlm.pt",
        "uk/backward_charlm/conll17.pt",
        "uk/pretrain/conll17.pt",
        "uk/forward_charlm/conll17.pt",
    )
    missing = [name for name in required if not (STANZA_HOME / name).is_file()]
    if missing:
        raise RuntimeError("Missing Stanza resources: " + ", ".join(missing))


def verify_imports() -> None:
    import ipa_uk  # noqa: F401
    import torch
    import torchaudio  # noqa: F401
    from styletts2_inference.models import StyleTTS2  # noqa: F401

    if not torch.__version__:
        raise RuntimeError("PyTorch version is unavailable")


def verify_real_synthesis() -> None:
    if str(APP_ROOT) not in sys.path:
        sys.path.insert(0, str(APP_ROOT))
    from backend.tts_styletts import (
        styletts_last_error,
        styletts_status,
        synthesize_styletts,
    )

    output = APP_ROOT / "cache" / "tts" / "install-check.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not synthesize_styletts(
        "Вітаю. В ефірі Адам Вектор.",
        "",
        "+0%",
        output,
    ):
        raise RuntimeError("StyleTTS2 synthesis failed: " + styletts_last_error())
    with wave.open(str(output), "rb") as audio:
        if audio.getframerate() != 24_000 or audio.getnchannels() != 1:
            raise RuntimeError("StyleTTS2 produced an unexpected WAV format")
    status = styletts_status()
    if not status.get("ready") or not status.get("model_loaded"):
        raise RuntimeError(f"StyleTTS2 readiness check failed: {status}")


def main() -> int:
    os.environ["HF_HOME"] = str(HF_HOME)
    os.environ["STANZA_RESOURCES_DIR"] = str(STANZA_HOME)
    HF_HOME.mkdir(parents=True, exist_ok=True)
    STANZA_HOME.mkdir(parents=True, exist_ok=True)
    log("Adam Vector StyleTTS2 provisioning started")
    verify_imports()
    retry("Voice model download", download_voice_assets)
    retry("Ukrainian language resources", download_stanza_assets)
    verify_real_synthesis()
    log("Adam Vector StyleTTS2 provisioning completed")
    print("Adam Vector StyleTTS2 is installed and ready.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"FATAL: {exc}\n{traceback.format_exc()}")
        print(f"StyleTTS2 provisioning failed: {exc}", file=sys.stderr)
        raise
