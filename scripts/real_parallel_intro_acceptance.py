"""Generate and strictly validate ten real AI song intros in parallel."""

from __future__ import annotations

import argparse
import copy
import re
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.api import (
    RadioAPI,
    _contains_weather_reference,
    _ukrainian_copy_warnings,
    spoken_word_count,
)


STYLES = ("straight_radio",)
INTRO_TYPES = ("listener_context", "comparison")


def _copy_runtime_root(real_root: Path, temp_root: Path) -> None:
    source = real_root / "data" / "radio.db"
    if not source.exists():
        raise FileNotFoundError(f"Локальну базу не знайдено: {source}")
    destination = temp_root / "data" / "radio.db"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    for name in ("api.txt", "apitest.txt"):
        key_file = real_root / name
        if key_file.exists():
            shutil.copy2(key_file, temp_root / name)


def _select_tracks(api: RadioAPI, count: int) -> list[dict]:
    selected = []
    seen_tracks = set()
    previous_artist = ""
    deferred = []
    for track in api.db.tracks():
        artist = str(track.get("artist") or "").strip()
        title = str(track.get("title") or "").strip()
        key = (artist.casefold(), title.casefold())
        if not artist or not title or key in seen_tracks:
            continue
        seen_tracks.add(key)
        if artist.casefold() == previous_artist:
            deferred.append(track)
            continue
        selected.append(track)
        previous_artist = artist.casefold()
        if len(selected) == count:
            return selected
    for track in deferred:
        selected.append(track)
        if len(selected) == count:
            return selected
    raise ValueError(
        f"У локальній базі лише {len(selected)} придатних треків; потрібно {count}"
    )


def _plan(index: int, duration_seconds: float) -> dict:
    intro_type = INTRO_TYPES[index % len(INTRO_TYPES)]
    return {
        "content_type": "talk",
        "style": STYLES[index % len(STYLES)],
        "target_seconds": duration_seconds,
        "word_min": 8,
        "word_max": max(22, round(duration_seconds * 3.0)),
        "mention_policy": "artist_and_title",
        "structure": "announce",
        "announce_mode": "identify_last",
        "length_class": "medium",
        "intro_type": intro_type,
        "fallback_variant": index,
        "must_say_time": False,
        "may_say_weather": False,
        "directive": (
            "Коротка жива підводка з точним оголошенням переданого треку. "
            "Не вигадуй біографічних, історичних або студійних фактів: тут "
            "дозволені лише слухацький контекст і природний вихід у музику. "
            "Не згадуй погоду, місто, річку, станцію або службові команди."
        ),
    }


def _redact(value: object, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(
        r"\b(?:nvapi-|sk-or-(?:v1-)?)[A-Za-z0-9_.-]+",
        "[REDACTED_API_KEY]",
        text,
        flags=re.IGNORECASE,
    )
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _check_intro(
    api: RadioAPI,
    track: dict,
    context: dict,
    plan: dict,
    intro: dict,
    require_ai: bool,
) -> list[str]:
    errors = []
    display_text = str(intro.get("display_text") or "").strip()
    speech_text = str(intro.get("speech_text") or "").strip()
    if not intro.get("ok"):
        errors.append(intro.get("error") or "генератор повернув ok=false")
    if not display_text:
        errors.append("порожній display_text")
    if not speech_text:
        errors.append("порожній speech_text")
    if require_ai and intro.get("fallback"):
        errors.append("AI не пройшов перевірки; отримано template fallback")
        if intro.get("provider_error"):
            errors.append("провайдер: " + _redact(intro["provider_error"]))
    if require_ai and not float(intro.get("quality_score") or 0):
        errors.append("немає позитивного AI quality score")
    if display_text:
        accepted, gate_error = api.content_planner.quality_gate(
            display_text,
            track,
            context,
            verified_fact="",
            verified_story_data=[],
            mention_policy=plan["mention_policy"],
            structure=plan["structure"],
        )
        if not accepted:
            errors.append(f"повторний ефірний quality gate: {gate_error}")
        if not plan.get("may_say_weather") and _contains_weather_reference(display_text):
            errors.append("незапланована згадка погоди")
        residual = display_text
        for value in (track.get("artist", ""), track.get("title", "")):
            if value:
                residual = re.sub(re.escape(str(value)), "", residual, flags=re.IGNORECASE)
        warnings = _ukrainian_copy_warnings(residual, allow_time_digits=False)
        errors.extend(f"мовний аудит: {warning}" for warning in warnings)
        if re.search(r"\b[A-Za-z]{2,}\b", residual):
            errors.append("сторонній латинський або службовий текст")
        if re.search(
            r"\b(?:увімкн\w*|включ\w*|слухайте|радіостанці\w*)\b",
            residual,
            flags=re.IGNORECASE | re.UNICODE,
        ):
            errors.append("наказова або службова фраза")
    return [_redact(error) for error in errors if error]


class _ParallelMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def enter(self) -> None:
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)

    def leave(self) -> None:
        with self._lock:
            self.active -= 1


def _generate_one(
    api: RadioAPI,
    job: dict,
    providers: list[dict],
    barrier: threading.Barrier | None,
    meter: _ParallelMeter,
    require_ai: bool,
) -> dict:
    started = time.monotonic()
    meter.enter()
    try:
        if barrier is not None:
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
        provider_index = (
            job["index"] + job["attempt"] - 2
        ) % max(1, len(providers))
        job_providers = [providers[provider_index]] if providers else []
        intro = api.make_intro(
            job["track"]["id"],
            current_track_id=job["current"]["id"],
            style=job["plan"]["style"],
            generation_context=copy.deepcopy(job["context"]),
            content_plan=copy.deepcopy(job["plan"]),
            duration_seconds=job["plan"]["target_seconds"],
            store_track=False,
            providers_override=job_providers,
        )
        errors = _check_intro(
            api,
            job["track"],
            job["context"],
            job["plan"],
            intro,
            require_ai,
        )
    except Exception as exc:
        intro = {
            "ok": False,
            "display_text": "",
            "speech_text": "",
            "provider": "",
            "fallback": False,
            "provider_error": str(exc),
        }
        errors = [_redact(exc)]
    finally:
        meter.leave()
    return {
        **job,
        "ok": not errors,
        "errors": errors,
        "intro": intro,
        "seconds": round(time.monotonic() - started, 2),
    }


def run(
    api: RadioAPI,
    tracks: list[dict],
    providers: list[dict],
    *,
    workers: int,
    attempts: int,
    duration_seconds: float,
    require_ai: bool,
) -> dict:
    jobs = []
    for index, track in enumerate(tracks):
        current = tracks[index - 1]
        context = api.context_engine.snapshot(current, track)
        jobs.append({
            "index": index + 1,
            "track": track,
            "current": current,
            "context": context,
            "plan": _plan(index, duration_seconds),
            "attempt": 0,
        })

    pending = {job["index"]: job for job in jobs}
    final = {}
    attempt_history = {job["index"]: [] for job in jobs}
    meter = _ParallelMeter()
    started = time.monotonic()

    for attempt in range(1, attempts + 1):
        if not pending:
            break
        batch = [{**job, "attempt": attempt} for job in pending.values()]
        round_workers = min(workers, len(batch))
        barrier = threading.Barrier(round_workers) if round_workers > 1 else None
        results = {}
        with ThreadPoolExecutor(max_workers=round_workers) as executor:
            futures = {
                executor.submit(
                    _generate_one,
                    api,
                    job,
                    providers,
                    barrier,
                    meter,
                    require_ai,
                ): job["index"]
                for job in batch
            }
            for future in as_completed(futures):
                result = future.result()
                results[result["index"]] = result

        for index, result in results.items():
            attempt_history[index].append({
                "attempt": attempt,
                "ok": result["ok"],
                "provider": result["intro"].get("provider", ""),
                "seconds": result["seconds"],
                "errors": result["errors"],
                "provider_error": _redact(result["intro"].get("provider_error", "")),
            })
            final[index] = result
            if result["ok"]:
                pending.pop(index, None)

    items = []
    for job in jobs:
        result = final.get(job["index"], {**job, "ok": False, "errors": ["не виконано"], "intro": {}})
        result["attempt_history"] = attempt_history[job["index"]]
        items.append(result)
    passed = sum(item["ok"] for item in items)
    return {
        "ok": passed == len(tracks) and meter.peak > 1,
        "requested": len(tracks),
        "passed": passed,
        "workers": workers,
        "parallel_peak": meter.peak,
        "attempts": attempts,
        "duration_seconds": duration_seconds,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "items": items,
    }


def _report(root: Path, safe_providers: list[dict], result: dict, require_ai: bool) -> str:
    provider_summary = ", ".join(
        f"{item.get('name', '')}/{item.get('model', '')}"
        for item in safe_providers
    ) or "локальний редакційний fallback"
    lines = [
        "LUMEN Radio — паралельний acceptance-тест підводок",
        f"Дата: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Проєкт: {root}",
        f"Режим: {'LIVE AI, fallback заборонено' if require_ai else 'fallback дозволено'}",
        "Провайдери: " + provider_summary,
        f"Паралельні workers: {result['workers']}",
        f"Фактичний пік одночасних задач: {result['parallel_peak']}",
        f"Максимум спроб на трек: {result['attempts']}",
        f"AI-варіантів на одну спробу: {result.get('variants', 1)}",
        f"Загальний час: {result['elapsed_seconds']} с",
        "",
        f"РЕЗУЛЬТАТ: {'PASS' if result['ok'] else 'FAIL'} — {result['passed']}/{result['requested']}",
    ]
    for item in result["items"]:
        intro = item.get("intro") or {}
        quality = (
            "local-gate"
            if intro.get("fallback")
            else f"{float(intro.get('quality_score') or 0):.2f}"
        )
        lines.extend([
            "",
            (
                f"{item['index']:02d}. {'PASS' if item['ok'] else 'FAIL'} | "
                f"{item['track']['artist']} — {item['track']['title']}"
            ),
            (
                f"Тип: {item['plan']['intro_type']} | стиль: {item['plan']['style']} | "
                f"спроб: {len(item['attempt_history'])}"
            ),
            (
                f"Генератор: {intro.get('provider', '')} | fallback: "
                f"{'так' if intro.get('fallback') else 'ні'} | "
                f"quality={quality} | "
                f"слів={spoken_word_count(intro.get('speech_text', ''))}"
            ),
            "Текст: " + str(intro.get("display_text") or ""),
        ])
        for error in item.get("errors") or []:
            lines.append("Помилка: " + _redact(error))
        for attempt in item.get("attempt_history") or []:
            if not attempt["ok"]:
                details = list(attempt["errors"])
                if attempt["provider_error"] and not any(
                    attempt["provider_error"] in value for value in details
                ):
                    details.append("провайдер: " + attempt["provider_error"])
                detail = "; ".join(details)
                lines.append(
                    f"Спроба {attempt['attempt']} ({attempt['seconds']} с): {_redact(detail)}"
                )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate ten real song intros concurrently and require an exact 10/10"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--variants", type=int, default=1)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--provider", default="")
    parser.add_argument(
        "--api-file",
        default="",
        help="Optional extra key file; OpenRouter credentials are rotated without being logged",
    )
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--allow-fallback", action="store_true")
    parser.add_argument("--output", default="parallel_intro_10_tracks_results.txt")
    args = parser.parse_args()

    real_root = Path(args.root).resolve()
    output = (real_root / args.output).resolve()
    count = max(2, min(20, int(args.count)))
    workers = max(2, min(10, int(args.workers), count))
    attempts = max(1, min(5, int(args.attempts)))
    variants = max(1, min(3, int(args.variants)))
    duration_seconds = max(7.0, min(12.0, float(args.duration)))
    timeout_seconds = max(10, min(240, int(args.timeout)))
    require_ai = not args.allow_fallback

    with tempfile.TemporaryDirectory(prefix="lumen-parallel-intro-") as directory:
        temp_root = Path(directory)
        _copy_runtime_root(real_root, temp_root)
        api = RadioAPI(temp_root)
        try:
            api.db.save_settings({
                "intro_variants_per_provider": str(variants),
                "provider_health": "{}",
            })
            api._reset_provider_health()
            tracks = _select_tracks(api, count)
            # Keep every configured credential available to the acceptance
            # harness. One credential is assigned per track and retries rotate
            # through the pool, avoiding a request burst against one key.
            providers = api._ai_providers()
            if args.api_file:
                api_file = Path(args.api_file).expanduser().resolve()
                raw = api_file.read_text(encoding="utf-8-sig")
                extra_keys = list(dict.fromkeys(re.findall(
                    r"sk-or-[A-Za-z0-9_-]+", raw
                )))
                secondary = next(
                    (
                        item for item in providers
                        if item.get("provider_type") == "secondary"
                    ),
                    {},
                )
                for index, key in enumerate(extra_keys, start=1):
                    providers.append({
                        "name": f"openrouter-file-{index}",
                        "provider_type": "openrouter-file",
                        "url": "https://openrouter.ai/api/v1/chat/completions",
                        "key": key,
                        "model": secondary.get("model") or "openrouter/free",
                        "timeout_seconds": timeout_seconds,
                    })
            deduplicated = []
            seen_credentials = set()
            for item in providers:
                credential = str(item.get("key") or "")
                if not credential or credential in seen_credentials:
                    continue
                seen_credentials.add(credential)
                deduplicated.append(item)
            providers = deduplicated
            if args.provider:
                provider_filter = args.provider.casefold()
                providers = [
                    item for item in providers
                    if str(item.get("name") or "").casefold() == provider_filter
                    or str(item.get("provider_type") or "").casefold() == provider_filter
                ]
            providers = [
                {**item, "timeout_seconds": timeout_seconds}
                for item in providers
            ]
            if require_ai and not providers:
                result = {
                    "ok": False,
                    "requested": count,
                    "passed": 0,
                    "workers": workers,
                    "parallel_peak": 0,
                    "attempts": attempts,
                    "elapsed_seconds": 0,
                    "items": [],
                }
                safe_providers = []
            else:
                safe_providers = [
                    {"name": item.get("name", ""), "model": item.get("model", "")}
                    for item in providers
                ]
                result = run(
                    api,
                    tracks,
                    providers,
                    workers=workers,
                    attempts=attempts,
                    duration_seconds=duration_seconds,
                    require_ai=require_ai,
                )
                result["variants"] = variants
        finally:
            api.shutdown()

    output.write_text(
        _report(real_root, safe_providers, result, require_ai),
        encoding="utf-8",
    )
    print(output)
    print(f"RESULT={'PASS' if result['ok'] else 'FAIL'} {result['passed']}/{result['requested']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
