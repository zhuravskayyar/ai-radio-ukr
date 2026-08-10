from __future__ import annotations

import argparse
import copy
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.api import DEFAULTS, RadioAPI


DEFAULT_STYLE = (
    "Сучасний альт рок, український і російськомовний alternative/indie rock; "
    "без музейного старого росроку типу Цоя, Кино, ДДТ, Би-2, Алисы чи Аквариума; "
    "чергуй впізнавані та свіжі треки, без попси й каверів."
)

BLOCKED_WORDS = {
    "reaction", "tutorial", "review", "interview", "live concert",
    "live video", "live session", "concert",
    "full concert", "1 hour", "10 hours", "sped up", "nightcore",
    "slowed + reverb", "slowed and reverb", "shorts", "playlist", "mix",
    "cover", "karaoke", "tribute", "fan made", "ai generated",
    "royalty free", "type beat",
}


def _copy_runtime_root(real_root: Path, temp_root: Path) -> None:
    data_dir = temp_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = real_root / "data" / "radio.db"
    if db_path.exists():
        shutil.copy2(db_path, data_dir / "radio.db")
    for name in ("api.txt", "apitest.txt"):
        source = real_root / name
        if source.exists():
            shutil.copy2(source, temp_root / name)


def _candidate_pool(api: RadioAPI, plan: dict, attempted: set[tuple[str, str]]) -> list[dict]:
    pool = []
    seen = set(attempted)
    previous_artist = ""
    for item in (
        list(plan.get("tracks") or [])
        + list(plan.get("similar_tracks") or [])
        + list(plan.get("backup_tracks") or [])
    ):
        artist = str(item.get("artist") or "").strip()
        title = str(item.get("title") or "").strip()
        artist_key = api._normalize_music_text(artist)
        title_key = api._normalize_music_text(title)
        key = (artist_key, title_key)
        if not all(key) or key in seen or artist_key == previous_artist:
            continue
        seen.add(key)
        pool.append({
            "artist": artist,
            "title": title,
            "reason": str(item.get("reason") or "").strip(),
            "source_provider": (
                item.get("source_provider") or plan.get("provider") or ""
            ),
        })
        previous_artist = artist_key
    return pool


def _verify_one(api: RadioAPI, settings: dict, recommendation: dict) -> dict:
    try:
        import yt_dlp
    except ImportError as exc:
        return {**recommendation, "ok": False, "error": f"yt-dlp unavailable: {exc}"}

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": False,
        "playlistend": 5,
        "socket_timeout": 12,
        "retries": 1,
        "extractor_retries": 1,
    }
    search_settings = dict(settings)
    search_settings.update({"queue_min_duration": "60", "queue_max_duration": "600"})
    started = time.monotonic()
    query = api._exact_track_query(recommendation)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            payload = ydl.extract_info(query, download=False) or {}
        for info in payload.get("entries") or []:
            if not isinstance(info, dict):
                continue
            candidate = api._download_info_candidate(info)
            if api._queue_candidate_allowed(
                candidate,
                search_settings,
                BLOCKED_WORDS,
                set(),
                recommendation,
            ):
                return {
                    **recommendation,
                    "ok": True,
                    "source_id": candidate.get("id", ""),
                    "source_title": candidate.get("title", ""),
                    "source_channel": (
                        candidate.get("channel") or candidate.get("uploader") or ""
                    ),
                    "match_score": candidate.get("match_score", 0),
                    "seconds": round(time.monotonic() - started, 2),
                    "error": "",
                }
        error = "немає точної пари виконавець/назва серед перших п’яти результатів"
    except Exception as exc:
        error = str(exc)[:300]
    return {
        **recommendation,
        "ok": False,
        "seconds": round(time.monotonic() - started, 2),
        "error": error,
    }


def _append_without_adjacent_repeat(
    api: RadioAPI,
    accepted: list[dict],
    results: list[dict],
    target: int,
) -> None:
    accepted_keys = {
        (api._normalize_music_text(item["artist"]), api._normalize_music_text(item["title"]))
        for item in accepted
    }
    previous_artist = api._normalize_music_text(accepted[-1]["artist"]) if accepted else ""
    for item in results:
        if not item.get("ok"):
            continue
        artist_key = api._normalize_music_text(item.get("artist"))
        key = (artist_key, api._normalize_music_text(item.get("title")))
        if not all(key) or key in accepted_keys or artist_key == previous_artist:
            continue
        accepted.append(item)
        accepted_keys.add(key)
        previous_artist = artist_key
        if len(accepted) >= target:
            break


def run_acceptance(
    api: RadioAPI,
    settings: dict,
    *,
    target: int,
    max_rounds: int,
    workers: int,
) -> dict:
    accepted: list[dict] = []
    checked: list[dict] = []
    attempted: set[tuple[str, str]] = set()
    diagnostics: list[dict] = []
    started = time.monotonic()

    for round_number in range(1, max_rounds + 1):
        excluded = [
            {"artist": artist, "title": title}
            for artist, title in attempted
        ]
        plan_started = time.monotonic()
        plan = api._queue_search_plan(settings, excluded_tracks=excluded)
        pool = _candidate_pool(api, plan, attempted)
        diagnostics.append({
            "round": round_number,
            "provider": plan.get("provider", ""),
            "candidates": len(pool),
            "plan_seconds": round(time.monotonic() - plan_started, 2),
            "providers": list(plan.get("provider_diagnostics") or []),
        })

        batch_size = max(workers * 3, target * 2)
        for start in range(0, len(pool), batch_size):
            batch = pool[start:start + batch_size]
            for item in batch:
                attempted.add((
                    api._normalize_music_text(item["artist"]),
                    api._normalize_music_text(item["title"]),
                ))
            indexed_results = {}
            with ThreadPoolExecutor(max_workers=min(workers, len(batch))) as executor:
                futures = {
                    executor.submit(_verify_one, api, settings, item): index
                    for index, item in enumerate(batch)
                }
                for future in as_completed(futures):
                    indexed_results[futures[future]] = future.result()
            ordered = [indexed_results[index] for index in range(len(batch))]
            checked.extend(ordered)
            _append_without_adjacent_repeat(api, accepted, ordered, target)
            if len(accepted) >= target:
                break
        if len(accepted) >= target:
            break

    return {
        "ok": len(accepted) == target,
        "target": target,
        "verified": accepted[:target],
        "checked": checked,
        "diagnostics": diagnostics,
        "seconds": round(time.monotonic() - started, 2),
    }


def _format_report(root: Path, style: str, providers: list[dict], result: dict) -> str:
    verified = result["verified"]
    failed = [item for item in result["checked"] if not item.get("ok")]
    lines = [
        "LUMEN Radio — real DJ acceptance test",
        f"Дата: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Проєкт: {root}",
        "Дані: тільки реальні відповіді AI та реальний пошук метаданих; моків немає.",
        f"Стиль: {style}",
        "Провайдери: " + ", ".join(
            f"{item.get('name', '')}/{item.get('model', '')}" for item in providers
        ),
        "",
        f"РЕЗУЛЬТАТ: {'PASS' if result['ok'] else 'FAIL'} — {len(verified)}/{result['target']}",
        f"Перевірено кандидатів: {len(result['checked'])}",
        f"Відхилено неточних кандидатів: {len(failed)}",
        f"Загальний час: {result['seconds']} с",
        "Повторів виконавця підряд: 0",
        "",
        "Підтверджені треки:",
    ]
    for index, item in enumerate(verified, start=1):
        lines.append(
            f"{index:02d}. {item['artist']} — {item['title']} | "
            f"match={item.get('match_score', 0):.2f} | "
            f"source={item.get('source_title', '')} | {item.get('seconds', 0)} с"
        )
    lines.extend(["", "Раунди добору:"])
    for item in result["diagnostics"]:
        lines.append(
            f"- round={item['round']}, candidates={item['candidates']}, "
            f"AI={item['plan_seconds']} с, selected_provider={item['provider']}"
        )
        for provider in item["providers"]:
            lines.append(
                f"  {provider.get('provider', '')}: ok={provider.get('ok')}, "
                f"tracks={provider.get('tracks', 0)}, "
                f"similar={provider.get('similar_tracks', 0)}, "
                f"error={provider.get('error', '')}"
            )
    if failed:
        lines.extend(["", "Приклади відхилених кандидатів:"])
        for item in failed[:10]:
            lines.append(
                f"- {item.get('artist', '')} — {item.get('title', '')}: "
                f"{item.get('error', '')}"
            )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", default="dj_acceptance_real.txt")
    parser.add_argument("--target", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--style", default=DEFAULT_STYLE)
    args = parser.parse_args()

    real_root = Path(args.root).resolve()
    output = (real_root / args.output).resolve()
    target = max(1, min(20, int(args.target)))
    workers = max(1, min(10, int(args.workers)))
    max_rounds = max(1, min(5, int(args.max_rounds)))

    with tempfile.TemporaryDirectory(prefix="lumen-dj-acceptance-") as directory:
        temp_root = Path(directory)
        _copy_runtime_root(real_root, temp_root)
        api = RadioAPI(temp_root)
        settings = copy.deepcopy(api.db.settings())
        settings["station_prompt"] = args.style or DEFAULTS["station_prompt"]
        providers = api._ai_providers(settings)
        if not providers:
            output.write_text("Помилка: AI-провайдери не налаштовані.\n", encoding="utf-8")
            print(output)
            return 1
        safe_providers = [
            {"name": item.get("name", ""), "model": item.get("model", "")}
            for item in providers
        ]
        print(
            f"Providers={len(providers)}; target={target}; metadata_workers={workers}",
            flush=True,
        )
        result = run_acceptance(
            api,
            settings,
            target=target,
            max_rounds=max_rounds,
            workers=workers,
        )
        api.shutdown()

    output.write_text(
        _format_report(real_root, args.style, safe_providers, result),
        encoding="utf-8",
    )
    print(output)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
