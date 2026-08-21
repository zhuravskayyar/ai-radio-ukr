"""Local Ukrainian StyleTTS2 synthesis used by Vector Radio.

The model is loaded lazily and kept in memory.  Text follows the reference
Ukrainian StyleTTS2 pipeline: stressification, IPA conversion, tokenization,
and inference with the Filatov single-speaker style prompt.
"""
from __future__ import annotations

import gc
import re
import os
import hashlib
import importlib.util
import threading
import wave
from pathlib import Path
from unicodedata import normalize


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_REPO = "patriotyk/styletts2_ukrainian_single"
STYLE_REPO = "patriotyk/styletts2-ukrainian"
# Pin the exact model snapshots used by the installer.  This prevents a future
# upstream update from silently changing Adam Vector's voice.
MODEL_REVISION = "2646553e1f9a8c832480e3ad5ccb6839245af584"
STYLE_REVISION = "b02909e4c9f001865bf71633d76fee7110f657a3"
SAMPLE_RATE = 24_000
HF_HOME = PROJECT_ROOT / "cache" / "huggingface"
STANZA_RESOURCES_DIR = PROJECT_ROOT / "cache" / "stanza"
# ukrainian_word_stress imports stanza at module import time, so the writable
# project-local resource directory must be selected before either is imported.
os.environ.setdefault("HF_HOME", str(HF_HOME))
os.environ.setdefault("STANZA_RESOURCES_DIR", str(STANZA_RESOURCES_DIR))

_model = None
_style_prompt = None
_stressifier = None
_device = None
_last_error = ""
_load_lock = threading.Lock()
_stress_lock = threading.Lock()
_inference_lock = threading.Lock()


def _set_error(error: object) -> None:
    global _last_error
    _last_error = str(error).strip()


def styletts_last_error() -> str:
    return _last_error


def _get_device():
    global _device
    if _device is not None:
        return _device
    try:
        import torch

        _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if _device.type == "cpu":
            # Leave enough CPU time for WebView audio/UI threads while the
            # model renders speech in the background.
            thread_count = max(1, min(2, int(os.getenv("LUMEN_TTS_THREADS", "2"))))
            torch.set_num_threads(thread_count)
            try:
                torch.set_num_interop_threads(1)
            except RuntimeError:
                pass
    except Exception as exc:
        _set_error(exc)
        _device = None
    return _device


def _cached_asset(repo_id: str, filename: str, repo_type: str = "model") -> str | None:
    """Return a cached Hub file without making a network request."""
    try:
        from huggingface_hub import try_to_load_from_cache

        path = try_to_load_from_cache(
            repo_id=repo_id,
            filename=filename,
            revision=MODEL_REVISION if repo_type == "model" else STYLE_REVISION,
            repo_type=repo_type,
        )
        return path if isinstance(path, str) and Path(path).is_file() else None
    except Exception:
        return None


def _resolve_asset(repo_id: str, filename: str, repo_type: str = "model") -> str:
    cached = _cached_asset(repo_id, filename, repo_type)
    if cached:
        return cached

    from huggingface_hub import hf_hub_download

    return hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        revision=MODEL_REVISION if repo_type == "model" else STYLE_REVISION,
        repo_type=repo_type,
    )


def _windows_safe_config(config_path: str) -> str:
    """Create an ASCII YAML copy for a dependency that calls open() without UTF-8.

    styletts2-inference currently relies on the Windows locale when reading the
    config. Escaped YAML keeps the IPA vocabulary intact on cp1251 systems.
    """
    source = Path(config_path).read_bytes()
    digest = hashlib.sha256(source).hexdigest()[:12]
    destination = (
        Path(__file__).resolve().parents[1]
        / "cache"
        / "tts"
        / f"styletts_config_{digest}.yml"
    )
    if destination.is_file():
        return str(destination)

    import yaml

    payload = yaml.safe_load(source.decode("utf-8"))
    serialized = yaml.safe_dump(
        payload,
        allow_unicode=False,
        sort_keys=False,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".yml.part")
    try:
        temporary.write_text(serialized, encoding="ascii", newline="\n")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return str(destination)


def _prune_single_speaker_model(model):
    """Release training and style-sampling modules unused by fixed-voice inference."""
    unused = (
        "weights",
        "diffusion",
        "predictor_encoder",
        "style_encoder",
        "sampler",
        "to_mel",
        "noise",
    )
    for attribute in unused:
        if not hasattr(model, attribute):
            continue
        try:
            delattr(model, attribute)
        except (AttributeError, TypeError):
            try:
                setattr(model, attribute, None)
            except (AttributeError, TypeError):
                pass
    gc.collect()
    return model


def _get_model_and_style():
    global _model, _style_prompt
    if _model is not None and _style_prompt is not None:
        return _model, _style_prompt

    with _load_lock:
        if _model is not None and _style_prompt is not None:
            return _model, _style_prompt
        device = _get_device()
        if device is None:
            return None, None
        try:
            import torch
            from styletts2_inference.models import StyleTTS2

            weights_path = _resolve_asset(MODEL_REPO, "pytorch_model.bin")
            config_path = _windows_safe_config(
                _resolve_asset(MODEL_REPO, "config.yml")
            )
            style_path = _resolve_asset(STYLE_REPO, "filatov.pt", "space")
            model = StyleTTS2(
                config_path=config_path,
                weights_path=weights_path,
                device=device,
            )
            model.eval()
            style_prompt = torch.load(
                style_path,
                map_location=device,
                weights_only=True,
            ).to(device)
            if style_prompt.ndim == 1:
                style_prompt = style_prompt.unsqueeze(0)
            if tuple(style_prompt.shape) != (1, 256):
                raise ValueError(
                    f"Unexpected StyleTTS voice prompt shape: {tuple(style_prompt.shape)}"
                )
            # The single-speaker forward path uses a fixed 256-value prompt and
            # never touches the diffusion/style encoders or retained state dict.
            # Releasing them avoids keeping well over a gigabyte of dead memory.
            _prune_single_speaker_model(model)
            _model = model
            _style_prompt = style_prompt
            _set_error("")
            return _model, _style_prompt
        except Exception as exc:
            _set_error(exc)
            return None, None


def _get_stressifier():
    global _stressifier
    if _stressifier is not None:
        return _stressifier
    with _stress_lock:
        if _stressifier is not None:
            return _stressifier
        try:
            from ukrainian_word_stress import Stressifier

            _stressifier = Stressifier()
            return _stressifier
        except Exception as exc:
            _set_error(exc)
            return None


def _split_to_parts(text: str) -> list[str]:
    text = re.sub(r"(\w+[^.,!:?\-])\n", r"\1. ", text)
    text = text.replace("\n", " ")
    parts = [""]
    index = 0
    last = len(text) - 1
    for position, char in enumerate(text):
        parts[index] += char
        if char in ".?!:" and position < last and text[position + 1] == " ":
            # The reference demo keeps very short clauses with the next part.
            if len(parts[index].strip()) <= 20:
                continue
            index += 1
            parts.append("")
    return [part.strip() for part in parts if part.strip()]


def _normalize_text(text: str) -> str:
    text = normalize("NFKC", text).replace('"', "")
    text = re.sub(r"[᠆‐‑‒–—―⁻₋−⸺⸻]", "-", text)
    text = re.sub(r"\s+-\s+", ": ", text)
    if text and text[-1] not in ".?!:-":
        text += "."
    return text


def _to_speed(rate: str) -> float:
    try:
        if rate.endswith("%"):
            percent = float(rate[:-1])
            return max(0.7, min(1.3, 1.0 + percent / 100.0))
    except (AttributeError, TypeError, ValueError):
        pass
    return 1.0


def _write_wav(path: Path, audio) -> None:
    import numpy as np

    if not isinstance(audio, np.ndarray):
        audio = audio.detach().cpu().numpy()
    audio = np.asarray(audio).squeeze().astype("float32")
    audio = np.nan_to_num(audio, nan=0.0, posinf=1.0, neginf=-1.0)
    audio = np.clip(audio, -1.0, 1.0)
    int_audio = (audio * 32767.0).astype("int16")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        with wave.open(str(temporary), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(SAMPLE_RATE)
            writer.writeframes(int_audio.tobytes())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def synthesize_styletts(text: str, voice: str, rate: str, out_path: Path) -> bool:
    """Synthesize Ukrainian speech as a 24 kHz mono WAV file."""
    del voice  # The local single-speaker checkpoint uses its Filatov prompt.
    if not isinstance(text, str) or not text.strip():
        _set_error("No text supplied for StyleTTS2")
        return False
    if out_path.suffix.casefold() != ".wav":
        _set_error("StyleTTS2 output path must use the .wav extension")
        return False

    model, style_prompt = _get_model_and_style()
    stressifier = _get_stressifier()
    if model is None or style_prompt is None or stressifier is None:
        return False

    try:
        import torch
        from ipa_uk import ipa
        from ukrainian_word_stress import StressSymbol

        result_wavs = []
        with _inference_lock, torch.inference_mode():
            for part in _split_to_parts(text):
                prepared = part.replace("+", StressSymbol.CombiningAcuteAccent)
                prepared = stressifier(_normalize_text(prepared))
                phonetic = ipa(prepared)
                if not phonetic:
                    continue
                tokens = model.tokenizer.encode(phonetic)
                if tokens is None or len(tokens) == 0:
                    continue
                wav = model(tokens, speed=_to_speed(rate), s_prev=style_prompt)
                result_wavs.append(wav.detach().cpu())
        if not result_wavs:
            raise RuntimeError("StyleTTS2 produced no audio")
        _write_wav(out_path, torch.concatenate(result_wavs))
        _set_error("")
        return out_path.is_file() and out_path.stat().st_size > 44
    except Exception as exc:
        out_path.unlink(missing_ok=True)
        _set_error(exc)
        return False


def warm_styletts() -> bool:
    """Load the local model, voice prompt, and stress pipeline without synthesis."""
    model, style_prompt = _get_model_and_style()
    stressifier = _get_stressifier()
    return model is not None and style_prompt is not None and stressifier is not None


def styletts_status() -> dict[str, object]:
    dependencies = has_styletts()
    model_cached = bool(
        _cached_asset(MODEL_REPO, "pytorch_model.bin")
        and _cached_asset(MODEL_REPO, "config.yml")
        and _cached_asset(STYLE_REPO, "filatov.pt", "space")
    )
    stanza_files = (
        "resources.json",
        "uk/tokenize/iu.pt",
        "uk/mwt/iu.pt",
        "uk/pos/iu_charlm.pt",
        "uk/backward_charlm/conll17.pt",
        "uk/pretrain/conll17.pt",
        "uk/forward_charlm/conll17.pt",
    )
    stanza_ready = all((STANZA_RESOURCES_DIR / name).is_file() for name in stanza_files)
    # Do not import torch/stanza during radio bootstrap. Those imports are
    # intentionally deferred until the first uncached synthesis.
    gpu = getattr(_device, "type", "") == "cuda"
    device_name = "auto"
    if _device is not None:
        device_name = str(_device)
        if gpu:
            try:
                import torch

                device_name = torch.cuda.get_device_name(0)
            except Exception:
                device_name = "cuda"
    return {
        "available": dependencies,
        "model_cached": model_cached,
        "stanza_ready": stanza_ready,
        "ready": dependencies and model_cached and stanza_ready,
        "model_loaded": _model is not None,
        "cuda": gpu,
        "device": device_name,
        "model": MODEL_REPO,
        "error": _last_error,
    }


def has_styletts() -> bool:
    required = ("torch", "ipa_uk", "styletts2_inference", "ukrainian_word_stress")
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if missing:
        _set_error("Missing StyleTTS2 dependencies: " + ", ".join(missing))
        return False
    return True
