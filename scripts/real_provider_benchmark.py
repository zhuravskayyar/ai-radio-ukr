from __future__ import annotations

import argparse
import copy
import shutil
import statistics
import tempfile
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.api import (
    LEGACY_REGIONAL_ROCK_ARTISTS,
    RadioAPI,
    _sounds_scripted,
    _ukrainian_copy_warnings,
    spoken_word_count,
)


DEFAULT_STYLE = (
    "Сучасний альт рок, український і російськомовний alternative/indie rock; "
    "без музейного старого росроку типу Цоя, Кино, ДДТ, Би-2, Алисы чи Аквариума; "
    "чергуй впізнавані та свіжі треки, без попси й каверів."
)


def _copy_runtime_root(real_root: Path, temp_root: Path) -> None:
    data_dir = temp_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = real_root / "data" / "radio.db"
    if db_path.exists():
        shutil.copy2(db_path, data_dir / "radio.db")
    for name in ("api.txt", "apitest.txt"):
        api_file = real_root / name
        if api_file.exists():
            shutil.copy2(api_file, temp_root / name)


def _average(values: list[float]) -> float:
    return round(statistics.mean(values), 2) if values else 0.0


def _artist_key(api: RadioAPI, value: str) -> str:
    return api._normalize_music_text(value)


def _adjacent_artist_repeats(api: RadioAPI, tracks: list[dict]) -> list[str]:
    repeats = []
    previous = ""
    for item in tracks:
        current = _artist_key(api, item.get("artist", ""))
        if current and current == previous:
            repeats.append(str(item.get("artist", "")))
        previous = current
    return repeats


def _legacy_tracks(api: RadioAPI, tracks: list[dict]) -> list[str]:
    legacy_keys = {_artist_key(api, artist) for artist in LEGACY_REGIONAL_ROCK_ARTISTS}
    found = []
    for item in tracks:
        if _artist_key(api, item.get("artist", "")) in legacy_keys:
            found.append(f'{item.get("artist", "")} - {item.get("title", "")}')
    return found


def _verify_tracks_with_real_search(
    api: RadioAPI,
    settings: dict,
    tracks: list[dict],
) -> dict:
    """Verify AI recommendations through real YouTube metadata search.

    No media is downloaded. The same artist/title/duration checks used by the
    live queue decide whether a search result actually matches a recommendation.
    """
    try:
        import yt_dlp
    except ImportError as exc:
        return {
            "enabled": True,
            "checked": 0,
            "verified": 0,
            "matches": [],
            "failed": [],
            "error": f"yt-dlp unavailable: {exc}",
        }

    search_settings = dict(settings)
    search_settings.update({
        "queue_min_duration": "60",
        "queue_max_duration": "600",
    })
    blocked_words = {
        "reaction", "tutorial", "review", "interview", "live concert",
        "full concert", "1 hour", "10 hours", "sped up", "nightcore",
        "slowed + reverb", "slowed and reverb", "shorts", "playlist", "mix",
        "cover", "karaoke", "tribute", "fan made", "ai generated",
        "royalty free", "type beat",
    }
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": False,
        "playlistend": 5,
        "socket_timeout": 15,
        "retries": 1,
        "extractor_retries": 1,
    }
    matches = []
    failed = []
    for track in tracks:
        artist = str(track.get("artist") or "").strip()
        title = str(track.get("title") or "").strip()
        query = f'ytsearch5:"{artist}" "{title}" official audio'
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                payload = ydl.extract_info(query, download=False) or {}
            entries = payload.get("entries") or []
            accepted = None
            for info in entries:
                if not isinstance(info, dict):
                    continue
                candidate = api._download_info_candidate(info)
                if api._queue_candidate_allowed(
                    candidate,
                    search_settings,
                    blocked_words,
                    set(),
                    {"artist": artist, "title": title},
                ):
                    accepted = candidate
                    break
            if accepted:
                matches.append({
                    "artist": artist,
                    "title": title,
                    "source_title": accepted.get("title", ""),
                    "source_channel": accepted.get("channel") or accepted.get("uploader", ""),
                    "match_score": accepted.get("match_score", 0),
                })
            else:
                failed.append({
                    "artist": artist,
                    "title": title,
                    "error": "немає точного результату серед перших п’яти",
                })
        except Exception as exc:
            failed.append({
                "artist": artist,
                "title": title,
                "error": str(exc)[:300],
            })
    return {
        "enabled": True,
        "checked": len(tracks),
        "verified": len(matches),
        "matches": matches,
        "failed": failed,
        "error": "",
    }


def _intro_score(intro: dict) -> float:
    diagnostics = intro.get("provider_diagnostics") or []
    scores = [
        float(item.get("score") or 0)
        for item in diagnostics
        if item.get("ok")
    ]
    return max(scores) if scores else 0.0


def _run_provider(
    api: RadioAPI,
    settings: dict,
    provider: dict,
    limit: int,
    intro_seconds: int,
    verify_tracks: bool = False,
) -> dict:
    safe_provider = {
        "name": provider.get("name", ""),
        "model": provider.get("model", ""),
        "url": provider.get("url", ""),
    }
    result = {
        "provider": safe_provider,
        "music_search": {
            "ok": False,
            "score": 0.0,
            "error": "",
            "tracks": [],
            "similar_tracks": [],
            "skipped": [],
            "adjacent_repeats": [],
            "legacy_tracks": [],
        },
        "intros": [],
        "summary": {},
    }
    try:
        plan = api._queue_search_plan(settings, providers=[provider])
        tracks = list(plan.get("tracks") or [])[:limit]
        result["music_search"] = {
            "ok": True,
            "score": float(plan.get("quality_score") or 0),
            "error": "",
            "tracks": tracks,
            "similar_tracks": list(plan.get("similar_tracks") or [])[:limit],
            "target_mood": list(plan.get("target_mood") or []),
            "avoid": list(plan.get("avoid") or []),
            "skipped": list(plan.get("skipped") or []),
            "provider_diagnostics": list(plan.get("provider_diagnostics") or []),
            "adjacent_repeats": _adjacent_artist_repeats(api, tracks),
            "legacy_tracks": _legacy_tracks(api, tracks),
            "verification": (
                _verify_tracks_with_real_search(api, settings, tracks)
                if verify_tracks else {"enabled": False, "checked": 0, "verified": 0}
            ),
        }
    except Exception as exc:
        result["music_search"]["error"] = str(exc)
        result["summary"] = {
            "total_score": 0.0,
            "intro_ok": 0,
            "fallbacks": 0,
            "scripted": 0,
            "avg_intro_score": 0.0,
            "avg_spelling_score": 0.0,
        }
        return result

    existing_tracks = api.db.tracks()
    current_id = existing_tracks[0]["id"] if existing_tracks else None
    intro_scores: list[float] = []
    spelling_scores: list[float] = []
    ok_count = 0
    fallback_count = 0
    scripted_count = 0
    styles = [
        "ironic",
        "atmospheric",
        "listener_tease",
        "straight_radio",
        "short_joke",
    ]

    for index, recommendation in enumerate(result["music_search"]["tracks"], start=1):
        intro_style = styles[(index - 1) % len(styles)]
        track = api.db.add_local_track(
            str(recommendation.get("artist") or "").strip(),
            str(recommendation.get("title") or "").strip(),
            f"benchmark/{provider.get('name', 'provider')}-{index}.mp3",
        )
        api.db.update_track(
            track["id"],
            library_source="ai-benchmark",
            match_score=1.0,
        )
        intro = api.make_intro(
            track["id"],
            current_track_id=current_id,
            style=intro_style,
            content_plan={
                "content_type": "talk",
                "style": intro_style,
                "target_seconds": intro_seconds,
                "mention_policy": "artist_and_title",
                "directive": (
                    "Жива підводка для сучасного укр/рос альт-рок ефіру: без штампів, "
                    "без довгого вступу, без фраз про власну роботу ведучого. "
                    "Не нумеруй трек і не подавай його як сходинку в музичному списку. "
                    "Можна говорити одним або двома короткими реченнями."
                ),
            },
            duration_seconds=intro_seconds,
            store_track=False,
            providers_override=[provider],
        )
        text = intro.get("display_text", "")
        accepted_diagnostics = [
            item for item in intro.get("provider_diagnostics") or []
            if item.get("ok")
        ]
        warnings = list(
            max(
                accepted_diagnostics,
                key=lambda item: float(item.get("score") or 0),
                default={},
            ).get("warnings") or []
        )
        spelling_score = max(0, 100 - len(warnings) * 10)
        scripted = _sounds_scripted(text)
        score = _intro_score(intro)
        is_fallback = bool(intro.get("fallback"))
        suppressed = (
            is_fallback
            and str(settings.get("strict_live_ai_host", "1")).strip().casefold()
            in {"1", "true", "yes", "on"}
        )
        is_ok = bool(intro.get("ok")) and not is_fallback and score > 0

        ok_count += int(is_ok)
        fallback_count += int(is_fallback)
        scripted_count += int(scripted)
        intro_scores.append(score)
        spelling_scores.append(float(spelling_score))
        result["intros"].append({
            "index": index,
            "artist": track["artist"],
            "title": track["title"],
            "ok": is_ok,
            "fallback": is_fallback,
            "suppressed": suppressed,
            "provider": intro.get("provider", ""),
            "style": intro_style,
            "score": score,
            "spelling_score": spelling_score,
            "words": 0 if suppressed else spoken_word_count(text),
            "scripted": scripted,
            "warnings": warnings,
            "error": intro.get("provider_error", ""),
            "text": "" if suppressed else text,
            "diagnostics": intro.get("provider_diagnostics") or [],
        })
        current_id = track["id"]

    avg_intro = _average(intro_scores)
    avg_spelling = _average(spelling_scores)
    raw_music_score = float(result["music_search"]["score"])
    plan_music_score = min(100.0, raw_music_score / 1.34)
    verification = result["music_search"].get("verification") or {}
    verification_checked = int(verification.get("checked") or 0)
    verified_tracks = int(verification.get("verified") or 0)
    if verification_checked:
        verified_music_score = 100.0 * verified_tracks / verification_checked
        effective_music_score = round(
            plan_music_score * 0.35 + verified_music_score * 0.65,
            2,
        )
    else:
        effective_music_score = round(plan_music_score, 2)
    total_score = round(
        effective_music_score * 0.45
        + avg_intro * 0.35
        + avg_spelling * 0.20
        - fallback_count * 2
        - scripted_count * 3
        - len(result["music_search"]["adjacent_repeats"]) * 5
        - len(result["music_search"]["legacy_tracks"]) * 4,
        2,
    )
    result["summary"] = {
        "total_score": max(0.0, total_score),
        "intro_ok": ok_count,
        "fallbacks": fallback_count,
        "scripted": scripted_count,
        "avg_intro_score": avg_intro,
        "avg_spelling_score": avg_spelling,
        "music_score": effective_music_score,
        "music_plan_score": raw_music_score,
        "track_count": len(result["music_search"]["tracks"]),
        "adjacent_repeats": len(result["music_search"]["adjacent_repeats"]),
        "legacy_tracks": len(result["music_search"]["legacy_tracks"]),
        "verified_tracks": verified_tracks,
        "verification_checked": verification_checked,
    }
    return result


def _run_provider_isolated(
    real_root: Path,
    settings: dict,
    provider: dict,
    limit: int,
    intro_seconds: int,
    verify_tracks: bool = False,
) -> dict:
    """Run one real provider against its own temporary database copy."""
    with tempfile.TemporaryDirectory(
        prefix=f"lumen-{provider.get('name', 'provider')}-benchmark-"
    ) as directory:
        temp_root = Path(directory)
        _copy_runtime_root(real_root, temp_root)
        api = RadioAPI(temp_root)
        return _run_provider(
            api,
            copy.deepcopy(settings),
            provider,
            limit,
            intro_seconds,
            verify_tracks,
        )


def _format_report(root: Path, style: str, limit: int, results: list[dict]) -> str:
    ranked = sorted(
        results,
        key=lambda item: float(item.get("summary", {}).get("total_score") or 0),
        reverse=True,
    )
    winner = ranked[0]["provider"]["name"] if ranked and ranked[0].get("summary", {}).get("total_score") else ""
    verification_used = any(
        (item.get("music_search", {}).get("verification") or {}).get("enabled")
        for item in results
    )
    lines = [
        "LUMEN Radio — real AI provider benchmark",
        f"Дата: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Проєкт: {root}",
        "Mock-дані: ні",
        "Аудіо/YouTube download: ні",
        (
            "Перевірка існування треків: так, реальні метадані без завантаження аудіо"
            if verification_used else "Перевірка існування треків: ні"
        ),
        f"Стиль: {style}",
        f"Ліміт: {limit} треків і {limit} підводок на кожного провайдера",
        f"Переможець: {winner or 'немає'}",
        "",
        "Зведення",
    ]
    for item in ranked:
        provider = item["provider"]
        summary = item.get("summary", {})
        music = item.get("music_search", {})
        lines.extend([
            (
                f"- {provider.get('name') or 'unknown'} / {provider.get('model') or 'unknown'}: "
                f"total={summary.get('total_score', 0)}, "
                f"music={summary.get('music_score', 0)}, "
                f"plan_raw={summary.get('music_plan_score', music.get('score', 0))}, "
                f"tracks={summary.get('track_count', 0)}, "
                f"intro_ok={summary.get('intro_ok', 0)}/{summary.get('track_count', 0)}, "
                f"intro_avg={summary.get('avg_intro_score', 0)}, "
                f"spelling_avg={summary.get('avg_spelling_score', 0)}, "
                f"fallbacks={summary.get('fallbacks', 0)}, "
                f"scripted={summary.get('scripted', 0)}, "
                f"adjacent_repeats={summary.get('adjacent_repeats', 0)}, "
                f"legacy_tracks={summary.get('legacy_tracks', 0)}"
                f", verified={summary.get('verified_tracks', 0)}/{summary.get('verification_checked', 0)}"
            )
        ])
    lines.append("")

    for item in ranked:
        provider = item["provider"]
        music = item["music_search"]
        summary = item.get("summary", {})
        lines.extend([
            "=" * 80,
            f"Провайдер: {provider.get('name') or 'unknown'}",
            f"Модель: {provider.get('model') or 'unknown'}",
            f"URL: {provider.get('url') or 'unknown'}",
            f"Total score: {summary.get('total_score', 0)}",
            "",
            "Пошук треків",
            f"OK: {music.get('ok')}",
            f"Score: {music.get('score', 0)}",
        ])
        if music.get("error"):
            lines.append(f"Error: {music.get('error')}")
        if music.get("target_mood"):
            lines.append(f"Target mood: {', '.join(music.get('target_mood') or [])}")
        if music.get("avoid"):
            lines.append(f"Avoid: {', '.join(music.get('avoid') or [])}")
        if music.get("adjacent_repeats"):
            lines.append(f"Повтори виконавця підряд: {', '.join(music.get('adjacent_repeats') or [])}")
        if music.get("legacy_tracks"):
            lines.append(f"Старий канон, що проскочив: {', '.join(music.get('legacy_tracks') or [])}")
        verification = music.get("verification") or {}
        if verification.get("enabled"):
            lines.append(
                f"Перевірено реальним пошуком: {verification.get('verified', 0)}/"
                f"{verification.get('checked', 0)}"
            )
            if verification.get("error"):
                lines.append(f"Помилка перевірки: {verification.get('error')}")
            if verification.get("failed"):
                lines.append("Не підтверджено пошуком:")
                for failed in verification.get("failed") or []:
                    lines.append(
                        f"  - {failed.get('artist', '')} - {failed.get('title', '')}: "
                        f"{failed.get('error', '')}"
                    )
        if music.get("skipped"):
            lines.append("Відсіяно алгоритмом:")
            for skipped in music.get("skipped") or []:
                lines.append(
                    f"  - {skipped.get('artist', '')} - {skipped.get('title', '')}: "
                    f"{skipped.get('reason', '')}"
                )
        lines.append("Треки:")
        for index, track in enumerate(music.get("tracks") or [], start=1):
            reason = str(track.get("reason") or "").strip()
            suffix = f" — {reason}" if reason else ""
            lines.append(f"  {index:02d}. {track.get('artist', '')} - {track.get('title', '')}{suffix}")
        if music.get("similar_tracks"):
            lines.append("Схожі треки:")
            for index, track in enumerate(music.get("similar_tracks") or [], start=1):
                reason = str(track.get("reason") or "").strip()
                suffix = f" — {reason}" if reason else ""
                lines.append(f"  {index:02d}. {track.get('artist', '')} - {track.get('title', '')}{suffix}")
        lines.extend(["", "Підводки:"])
        for intro in item.get("intros") or []:
            flags = []
            if intro.get("fallback"):
                flags.append("fallback")
            if intro.get("suppressed"):
                flags.append("clean-segue")
            if intro.get("scripted"):
                flags.append("scripted")
            if intro.get("warnings"):
                flags.append("warnings")
            flags_text = f" [{', '.join(flags)}]" if flags else ""
            lines.extend([
                (
                    f"  {intro['index']:02d}. {intro['artist']} - {intro['title']}"
                    f"{flags_text}"
                ),
                (
                    f"      score={intro.get('score', 0)}, spelling={intro.get('spelling_score', 0)}, "
                    f"words={intro.get('words', 0)}, provider={intro.get('provider') or ''}"
                ),
            ])
            if intro.get("error"):
                lines.append(f"      error={intro.get('error')}")
            if intro.get("warnings"):
                lines.append(f"      warnings={'; '.join(intro.get('warnings') or [])}")
            rejected = [
                item for item in intro.get("diagnostics") or []
                if item.get("candidate") and not item.get("ok")
            ]
            for diagnostic in rejected[:2]:
                lines.append(
                    f"      rejected_ai={diagnostic.get('candidate', '')} "
                    f"(reason={diagnostic.get('error', '')}, "
                    f"words={diagnostic.get('words', 0)}, "
                    f"sentences={diagnostic.get('sentences', 0)})"
                )
            if intro.get("suppressed"):
                lines.append("      text=(clean segue: шаблонна підводка не озвучується в live-ефірі)")
            else:
                lines.append(f"      text={intro.get('text', '')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="Project root")
    parser.add_argument("--output", default="provider_benchmark_real.txt")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--intro-seconds", type=int, default=10)
    parser.add_argument(
        "--verify-tracks",
        action="store_true",
        help="Verify each recommendation through real metadata search without downloading audio",
    )
    parser.add_argument("--style", default=DEFAULT_STYLE)
    args = parser.parse_args()

    real_root = Path(args.root).resolve()
    output = (real_root / args.output).resolve()
    limit = max(1, min(10, int(args.limit)))

    with tempfile.TemporaryDirectory(prefix="lumen-provider-discovery-") as directory:
        temp_root = Path(directory)
        _copy_runtime_root(real_root, temp_root)
        api = RadioAPI(temp_root)
        settings = copy.deepcopy(api.db.settings())
        settings["station_prompt"] = args.style
        providers = api._ai_providers(settings)
        if not providers:
            report = _format_report(real_root, args.style, limit, [])
            report += "\nПомилка: AI-провайдери не налаштовані.\n"
            output.write_text(report, encoding="utf-8")
            print(output)
            return 1

    results = []
    safe_names = [
        f"{provider.get('name', 'unknown')}/{provider.get('model', 'unknown')}"
        for provider in providers
    ]
    print(f"Real providers: {', '.join(safe_names)}", flush=True)
    print(
        f"Running {limit} track searches and {limit} intros per provider...",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=min(2, len(providers))) as executor:
        futures = {
            executor.submit(
                _run_provider_isolated,
                real_root,
                settings,
                provider,
                limit,
                args.intro_seconds,
                args.verify_tracks,
            ): provider
            for provider in providers
        }
        for future in as_completed(futures):
            provider = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "provider": {
                        "name": provider.get("name", ""),
                        "model": provider.get("model", ""),
                        "url": provider.get("url", ""),
                    },
                    "music_search": {
                        "ok": False,
                        "score": 0.0,
                        "error": str(exc),
                        "tracks": [],
                    },
                    "intros": [],
                    "summary": {
                        "total_score": 0.0,
                        "intro_ok": 0,
                        "fallbacks": 0,
                        "scripted": 0,
                        "avg_intro_score": 0.0,
                        "avg_spelling_score": 0.0,
                        "track_count": 0,
                        "adjacent_repeats": 0,
                        "legacy_tracks": 0,
                    },
                }
            results.append(result)
            output.write_text(
                _format_report(real_root, args.style, limit, results),
                encoding="utf-8",
            )
            summary = result.get("summary", {})
            print(
                f"Finished {provider.get('name', 'unknown')}: "
                f"tracks={summary.get('track_count', 0)}, "
                f"intros={summary.get('intro_ok', 0)}; checkpoint={output}",
                flush=True,
            )

    output.write_text(
        _format_report(real_root, args.style, limit, results),
        encoding="utf-8",
    )
    print(output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
