"""Run the 100-case pronunciation corpus plus live parallel AI/TTS checks."""

from __future__ import annotations

import argparse
import copy
import difflib
import re
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.api import RadioAPI, spoken_word_count
from radio_pronunciation import RadioPronunciation
from scripts.real_parallel_intro_acceptance import _check_intro, _plan, _redact


DEFAULT_HARD_CASES = (7, 12, 23, 28, 33, 42, 55, 59, 71, 91)
TABLE_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$")


def _without_stress(value: str) -> str:
    return unicodedata.normalize(
        "NFC",
        "".join(
            char
            for char in unicodedata.normalize("NFD", value or "")
            if char != "\u0301"
        ),
    )


def _comparison_key(value: str) -> str:
    value = _without_stress(value).casefold()
    value = value.translate(str.maketrans({
        "’": "'", "`": "'", "-": " ", "—": " ", "–": " ",
        ".": " ", ",": " ", "!": " ", "?": " ", "/": " ",
    }))
    value = re.sub(r"[^а-яіїєґёыэъa-z0-9']+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _split_track(value: str) -> tuple[str, str]:
    parts = re.split(r"\s+[—–]\s+", value.strip(), maxsplit=1)
    if len(parts) != 2:
        raise ValueError(f"Немає розділювача виконавець — назва: {value}")
    return parts[0].strip(), parts[1].strip()


def _split_expected(value: str) -> tuple[str, str]:
    parts = re.split(r"\.\s+", value.strip(), maxsplit=1)
    if len(parts) != 2:
        raise ValueError(f"Немає паузи між виконавцем і назвою: {value}")
    return parts[0].strip(), parts[1].strip().rstrip(".")


def parse_cases(path: Path) -> list[dict]:
    cases = []
    category = ""
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        heading = re.match(r"^##\s+([A-G])\.\s+(.*)$", line.strip())
        if heading:
            category = f"{heading.group(1)}. {heading.group(2)}"
            continue
        match = TABLE_ROW.match(line.strip())
        if not match:
            continue
        number = int(match.group(1))
        original = match.group(2).strip()
        expected = match.group(3).strip()
        artist, title = _split_track(original)
        artist_expected, title_expected = _split_expected(expected)
        cases.append({
            "number": number,
            "category": category,
            "original": original,
            "expected": expected,
            "artist": artist,
            "title": title,
            "artist_expected": artist_expected,
            "title_expected": title_expected,
        })
    numbers = [item["number"] for item in cases]
    if numbers != list(range(1, 101)):
        raise ValueError(
            f"Очікували позиції 1..100, отримано {len(cases)}: {numbers[:5]}…{numbers[-5:]}"
        )
    return cases


def _structural_errors(original: str, spoken: str) -> list[str]:
    errors = []
    if re.search(r"[A-Za-z]", spoken):
        errors.append("залишилися латинські літери")
    if re.search(r"\d", spoken):
        errors.append("залишилися цифри")
    if re.search(r"\b(?:feat|ft)\.?\b|&|https?://|www\.", spoken, re.IGNORECASE):
        errors.append("залишився службовий символ/маркер")
    if re.search(r"\.(?:mp3|flac|wav|ogg|m4a|aac|opus)\b", spoken, re.IGNORECASE):
        errors.append("залишилося розширення файла")
    if "." not in spoken:
        errors.append("немає паузи між виконавцем і назвою")
    if len(spoken) > max(1, len(original) * 3):
        errors.append("транскрипція довша за оригінал більш ніж утричі")
    return errors


def run_local(cases: list[dict]) -> dict:
    converter = RadioPronunciation()
    items = []
    by_category: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "exact": 0, "structural": 0}
    )
    for case in cases:
        artist = converter.convert(case["artist"], kind="artist")
        title = converter.convert(case["title"], kind="title")
        spoken = f"{artist}. {title}"
        repeated = (
            f"{converter.convert(case['artist'], kind='artist')}. "
            f"{converter.convert(case['title'], kind='title')}"
        )
        errors = _structural_errors(case["original"], spoken)
        if repeated != spoken:
            errors.append("повторний запуск дав інший результат")
        actual_key = _comparison_key(spoken)
        expected_key = _comparison_key(case["expected"])
        exact = actual_key == expected_key
        similarity = difflib.SequenceMatcher(None, expected_key, actual_key).ratio()
        category = by_category[case["category"]]
        category["total"] += 1
        category["exact"] += int(exact)
        category["structural"] += int(not errors)
        items.append({
            **case,
            "spoken": spoken,
            "exact": exact,
            "similarity": similarity,
            "errors": errors,
            "ok": exact and not errors,
        })
    return {
        "total": len(items),
        "passed": sum(item["ok"] for item in items),
        "exact": sum(item["exact"] for item in items),
        "structural": sum(not item["errors"] for item in items),
        "average_similarity": sum(item["similarity"] for item in items) / len(items),
        "by_category": dict(by_category),
        "items": items,
    }


def _prepare_isolated_root(real_root: Path, temp_root: Path) -> None:
    """Copy credentials only; never expose the station DB to external APIs."""
    for name in ("api.txt", "apitest.txt"):
        key_file = real_root / name
        if key_file.exists():
            shutil.copy2(key_file, temp_root / name)


class _Meter:
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


def _run_branch(
    name: str,
    operation,
    barrier: threading.Barrier,
    meter: _Meter,
) -> tuple[object, dict]:
    meter.enter()
    started = time.monotonic()
    try:
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            pass
        value = operation()
        error = ""
    except Exception as exc:
        value = None
        error = _redact(exc)
    finally:
        finished = time.monotonic()
        meter.leave()
    return value, {
        "name": name,
        "started": started,
        "finished": finished,
        "seconds": round(finished - started, 2),
        "error": error,
    }


def _live_case(
    api: RadioAPI,
    case: dict,
    track: dict,
    current: dict,
    provider: dict,
    pronunciation_provider: threading.local,
    meter: _Meter,
) -> dict:
    started = time.monotonic()
    context = api.context_engine.snapshot(current, track)
    plan = _plan(case["number"], 10.0)
    barrier = threading.Barrier(2)

    def generate_intro():
        return api.make_intro(
            track["id"],
            current_track_id=current["id"],
            style=plan["style"],
            generation_context=copy.deepcopy(context),
            content_plan=copy.deepcopy(plan),
            duration_seconds=plan["target_seconds"],
            store_track=False,
            providers_override=[provider],
        )

    def generate_pronunciation():
        pronunciation_provider.spec = provider
        return api.generate_track_pronunciation(track["id"], invalidate=False)

    with ThreadPoolExecutor(max_workers=2) as executor:
        intro_future = executor.submit(
            _run_branch, "text", generate_intro, barrier, meter
        )
        pronunciation_future = executor.submit(
            _run_branch, "pronunciation", generate_pronunciation, barrier, meter
        )
        intro, intro_timing = intro_future.result()
        pronunciation, pronunciation_timing = pronunciation_future.result()

    intro = intro or {"ok": False, "error": intro_timing["error"]}
    pronunciation = pronunciation or {
        "ok": False,
        "error": pronunciation_timing["error"],
    }
    refreshed = api.db.track(track["id"]) or track
    display = str(intro.get("display_text") or "")
    if display:
        directed = api.voice_director.direct(
            display,
            api.db.tracks(),
            context,
            refreshed,
            plan["target_seconds"],
            plan["content_type"],
        )
        speech = str(directed.get("tts_text") or "")
    else:
        speech = ""
    audited_intro = {**intro, "speech_text": speech}
    errors = _check_intro(api, refreshed, context, plan, audited_intro, True)
    if not pronunciation.get("ok"):
        errors.append("AI-вимова: " + _redact(pronunciation.get("error") or "ok=false"))
    errors.extend(_structural_errors(display or case["original"], speech))

    artist_actual = str(refreshed.get("artist_speech") or "")
    title_actual = str(refreshed.get("title_speech") or "")
    artist_match = _comparison_key(artist_actual) == _comparison_key(case["artist_expected"])
    title_match = _comparison_key(title_actual) == _comparison_key(case["title_expected"])
    if not artist_match:
        errors.append("AI-вимова артиста не збігається з acceptance-еталоном")
    if not title_match:
        errors.append("AI-вимова назви не збігається з acceptance-еталоном")
    overlap = (
        min(intro_timing["finished"], pronunciation_timing["finished"])
        - max(intro_timing["started"], pronunciation_timing["started"])
    )
    if overlap <= 0:
        errors.append("гілки тексту та вимови фактично не перекривалися")
    return {
        **case,
        "ok": not errors,
        "errors": list(dict.fromkeys(_redact(error) for error in errors if error)),
        "provider": str(provider.get("name") or ""),
        "model": str(provider.get("model") or ""),
        "intro": intro,
        "pronunciation": pronunciation,
        "artist_actual": artist_actual,
        "title_actual": title_actual,
        "artist_match": artist_match,
        "title_match": title_match,
        "display_text": display,
        "speech_text": speech,
        "text_seconds": intro_timing["seconds"],
        "pronunciation_seconds": pronunciation_timing["seconds"],
        "overlap_seconds": round(max(0.0, overlap), 2),
        "seconds": round(time.monotonic() - started, 2),
    }


def run_live(
    root: Path,
    cases: list[dict],
    selected_numbers: tuple[int, ...],
    workers: int,
    timeout: int,
) -> dict:
    selected = [item for item in cases if item["number"] in selected_numbers]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="lumen-pronunciation-live-") as directory:
        temp_root = Path(directory)
        _prepare_isolated_root(root, temp_root)
        api = RadioAPI(temp_root)
        try:
            api.db.replace_tracks([
                {"rank": index, "artist": item["artist"], "title": item["title"]}
                for index, item in enumerate(selected, start=1)
            ])
            api.db.save_settings({
                "intro_variants_per_provider": "1",
                "provider_health": "{}",
                "primary_ai_provider": "secondary",
                "secondary_api_enabled": "1",
                "secondary_api_url": "https://openrouter.ai/api/v1/chat/completions",
                "secondary_model": "openrouter/free",
            })
            api._reset_provider_health()
            providers = []
            seen_credentials = set()
            for item in api._ai_providers():
                credential = str(item.get("key") or "")
                if not credential or credential in seen_credentials:
                    continue
                seen_credentials.add(credential)
                providers.append({**item, "timeout_seconds": timeout})
            safe_providers = [
                {"name": item.get("name", ""), "model": item.get("model", "")}
                for item in providers
            ]
            if not providers:
                return {
                    "ok": False,
                    "requested": len(selected),
                    "passed": 0,
                    "parallel_peak": 0,
                    "elapsed_seconds": 0,
                    "providers": [],
                    "items": [],
                    "error": "Не знайдено налаштованих AI-провайдерів",
                }

            provider_context = threading.local()
            default_pronunciation_providers = api._ai_providers_for_pronunciation

            def selected_pronunciation_provider(settings=None):
                spec = getattr(provider_context, "spec", None)
                return [spec] if spec else default_pronunciation_providers(settings)

            api._ai_providers_for_pronunciation = selected_pronunciation_provider
            tracks = api.db.tracks()
            track_by_key = {
                (item["artist"].casefold(), item["title"].casefold()): item
                for item in tracks
            }
            meter = _Meter()
            jobs = []
            for index, case in enumerate(selected):
                track = track_by_key[(case["artist"].casefold(), case["title"].casefold())]
                current_case = selected[index - 1]
                current = track_by_key[
                    (current_case["artist"].casefold(), current_case["title"].casefold())
                ]
                jobs.append((case, track, current, providers[index % len(providers)]))

            indexed = {}
            with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as executor:
                futures = {
                    executor.submit(
                        _live_case,
                        api,
                        case,
                        track,
                        current,
                        provider,
                        provider_context,
                        meter,
                    ): case["number"]
                    for case, track, current, provider in jobs
                }
                for future in as_completed(futures):
                    number = futures[future]
                    try:
                        indexed[number] = future.result()
                    except Exception as exc:
                        case = next(item for item in selected if item["number"] == number)
                        indexed[number] = {
                            **case,
                            "ok": False,
                            "errors": [_redact(exc)],
                            "provider": "",
                            "model": "",
                            "intro": {},
                            "pronunciation": {},
                            "artist_actual": "",
                            "title_actual": "",
                            "artist_match": False,
                            "title_match": False,
                            "display_text": "",
                            "speech_text": "",
                            "text_seconds": 0,
                            "pronunciation_seconds": 0,
                            "overlap_seconds": 0,
                            "seconds": 0,
                        }
            items = [indexed[number] for number in selected_numbers if number in indexed]
            passed = sum(item["ok"] for item in items)
            return {
                "ok": passed == len(selected) and meter.peak > 1,
                "requested": len(selected),
                "passed": passed,
                "parallel_peak": meter.peak,
                "workers": workers,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "providers": safe_providers,
                "items": items,
                "error": "",
            }
        finally:
            api.shutdown()


def report_text(root: Path, corpus: Path, local: dict, live: dict) -> str:
    lines = [
        "LUMEN Radio — acceptance-звіт паралельної генерації та вимови",
        f"Дата: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Проєкт: {root}",
        f"Корпус: {corpus}",
        "Ключі: використано локальну конфігурацію; секрети вилучено зі звіту",
        "",
        "1. ЛОКАЛЬНИЙ PRONUNCIATION ENGINE — 100 ПОЗИЦІЙ",
        f"Повний PASS (еталон + інваріанти): {local['passed']}/{local['total']}",
        f"Точний фонетичний збіг без урахування наголосів: {local['exact']}/{local['total']}",
        f"Машинні інваріанти: {local['structural']}/{local['total']}",
        f"Середня схожість з еталоном: {local['average_similarity'] * 100:.1f}%",
        "",
        "За категоріями:",
    ]
    for category, values in local["by_category"].items():
        lines.append(
            f"- {category}: exact {values['exact']}/{values['total']}; "
            f"інваріанти {values['structural']}/{values['total']}"
        )

    lines.extend([
        "",
        "Деталі 100 позицій:",
    ])
    for item in local["items"]:
        lines.extend([
            "",
            (
                f"{item['number']:03d}. {'PASS' if item['ok'] else 'FAIL'} | "
                f"exact={'так' if item['exact'] else 'ні'} | "
                f"similarity={item['similarity'] * 100:.1f}%"
            ),
            "Оригінал: " + item["original"],
            "Очікується: " + item["expected"],
            "Фактично: " + item["spoken"],
        ])
        lines.extend("Помилка: " + error for error in item["errors"])

    provider_summary = ", ".join(
        f"{item.get('name')}/{item.get('model')}" for item in live.get("providers", [])
    ) or "немає"
    lines.extend([
        "",
        "2. LIVE AI — ПАРАЛЕЛЬНІ ГІЛКИ ТЕКСТ + ВИМОВА",
        "Провайдери: " + provider_summary,
        f"Робітників верхнього рівня: {live.get('workers', 0)}",
        f"Фактичний пік AI-гілок: {live.get('parallel_peak', 0)}",
        f"Загальний час: {live.get('elapsed_seconds', 0)} с",
        (
            f"РЕЗУЛЬТАТ: {'PASS' if live.get('ok') else 'FAIL'} — "
            f"{live.get('passed', 0)}/{live.get('requested', 0)}"
        ),
    ])
    if live.get("error"):
        lines.append("Помилка запуску: " + _redact(live["error"]))
    for item in live.get("items", []):
        intro = item.get("intro") or {}
        pronunciation = item.get("pronunciation") or {}
        lines.extend([
            "",
            (
                f"{item['number']:03d}. {'PASS' if item['ok'] else 'FAIL'} | "
                f"{item['original']}"
            ),
            f"Провайдер: {item.get('provider', '')}/{item.get('model', '')}",
            (
                f"Паралельність: overlap={item.get('overlap_seconds', 0)} с | "
                f"текст={item.get('text_seconds', 0)} с | "
                f"вимова={item.get('pronunciation_seconds', 0)} с"
            ),
            (
                f"AI text: ok={bool(intro.get('ok'))}; "
                f"fallback={bool(intro.get('fallback'))}; "
                f"quality={float(intro.get('quality_score') or 0):.2f}; "
                f"слів={spoken_word_count(item.get('speech_text', ''))}"
            ),
            (
                f"AI pronunciation: ok={bool(pronunciation.get('ok'))}; "
                f"source={str((pronunciation.get('track') or {}).get('pronunciation_source') or '')}; "
                f"review={bool(pronunciation.get('review'))}"
            ),
            "Еталон артиста: " + item["artist_expected"],
            (
                "Фактична вимова артиста: " + item.get("artist_actual", "")
                + f" | match={'так' if item.get('artist_match') else 'ні'}"
            ),
            "Еталон назви: " + item["title_expected"],
            (
                "Фактична вимова назви: " + item.get("title_actual", "")
                + f" | match={'так' if item.get('title_match') else 'ні'}"
            ),
            "Display text: " + item.get("display_text", ""),
            "Speech text: " + item.get("speech_text", ""),
        ])
        lines.extend("Помилка: " + error for error in item.get("errors", []))

    lines.extend([
        "",
        "3. ВЕРДИКТ",
        (
            "Паралельний механізм підтверджено."
            if live.get("parallel_peak", 0) > 1
            else "Паралельний механізм не підтверджено."
        ),
        (
            "Корпус готовий до ефіру без ручної корекції."
            if local["passed"] == local["total"] and live.get("ok")
            else "Потрібна ручна/словникова корекція позицій FAIL; див. деталі вище."
        ),
    ])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--cases-file", required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=75)
    parser.add_argument(
        "--hard-cases",
        default=",".join(str(value) for value in DEFAULT_HARD_CASES),
    )
    parser.add_argument(
        "--output",
        default="parallel_pronunciation_acceptance_report.txt",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    corpus = Path(args.cases_file).resolve()
    output = (root / args.output).resolve()
    selected = tuple(
        dict.fromkeys(
            int(value.strip())
            for value in args.hard_cases.split(",")
            if value.strip()
        )
    )
    workers = max(1, min(6, int(args.workers)))
    timeout = max(15, min(180, int(args.timeout)))
    cases = parse_cases(corpus)
    local = run_local(cases)
    live = run_live(root, cases, selected, workers, timeout)
    output.write_text(report_text(root, corpus, local, live), encoding="utf-8")
    print(output)
    print(
        f"LOCAL={local['passed']}/{local['total']} "
        f"LIVE={live.get('passed', 0)}/{live.get('requested', 0)} "
        f"PARALLEL_PEAK={live.get('parallel_peak', 0)}"
    )
    return 0 if live.get("parallel_peak", 0) > 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
