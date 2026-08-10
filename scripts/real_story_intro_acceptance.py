"""Generate a source-backed music-story transition from the local radio DB."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.api import RadioAPI, split_spoken_sentences, spoken_word_count


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


def _story_candidates(api: RadioAPI, requested_track_id: int | None) -> list[tuple[dict, dict, dict]]:
    all_tracks = api.db.tracks()
    tracks = all_tracks
    if requested_track_id is not None:
        tracks = [track for track in tracks if track["id"] == requested_track_id]
        if not tracks:
            raise ValueError(f"Трек із id={requested_track_id} відсутній у локальній базі")
    candidates = []
    for current in tracks:
        next_track = next(
            (track for track in all_tracks if track["id"] != current["id"]),
            None,
        )
        for story in api.db.stories_for_track(current["id"], verified_only=True):
            if next_track:
                candidates.append((current, next_track, story))
    if not candidates:
        raise ValueError("У локальній базі немає верифікованих історій для тесту")
    return candidates


def _story_plan(story: dict, duration_seconds: float, variant: int) -> dict:
    try:
        story_data = json.loads(story.get("story_data_json") or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Некоректні дані story card #{story.get('id')}: {exc}") from exc
    if not isinstance(story_data, list) or not story_data:
        raise ValueError(f"Story card #{story.get('id')} не містить фактів")
    return {
        "content_type": "story",
        "style": "straight_radio",
        "announce_mode": "back_forward",
        "target_seconds": duration_seconds,
        "mention_policy": "artist_and_title",
        "story_id": story["id"],
        "story_subject_track_id": story["track_id"],
        "story_subject_role": "current",
        "story_category": story.get("category", ""),
        "story_variant": variant,
        "story_hook": story.get("hook", ""),
        "story_data": story_data,
        "verified_quote": story.get("verified_quote", ""),
        "story_source": {
            "url": story.get("source_url", ""),
            "title": story.get("source_title", ""),
        },
        "story_duration_class": story.get("duration_class", ""),
        "directive": (
            "Побудуй від трьох до п'яти речень: факт, контекст, цікава деталь, "
            "значення та природний вихід у наступну композицію."
        ),
    }


def _compact_error(value: str, limit: int = 650) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _check_intro(story: dict, intro: dict, require_ai: bool = False) -> list[str]:
    errors = []
    profile = intro.get("voice_profile") or {}
    target_seconds = float(profile.get("target_seconds") or 0)
    if not intro.get("ok") or not intro.get("speech_text"):
        errors.append(intro.get("error") or intro.get("provider_error") or "Підводка не створена")
    if not 20 <= target_seconds <= 40:
        errors.append("TTS-профіль історії має бути в межах 20-40 секунд")
    word_count = spoken_word_count(intro.get("speech_text", ""))
    if not int(profile.get("target_words_min") or 0) <= word_count <= int(profile.get("target_words_max") or 0):
        errors.append("Текст не відповідає словниковій цілі TTS-профілю")
    sentence_count = len(split_spoken_sentences(intro.get("display_text", "")))
    if not 3 <= sentence_count <= 5:
        errors.append("Історична підводка має містити 3-5 змістовних речень")
    if not story.get("source_url"):
        errors.append("Story card не містить URL джерела")
    if float((intro.get("story_quality") or {}).get("final") or 0) < 8:
        errors.append("Story quality нижче 8/10")
    if require_ai and intro.get("fallback"):
        errors.append("Live AI не створив прийнятну підводку; див. помилку генератора")
    return errors


def _report(result: dict) -> str:
    lines = [
        "LUMEN Radio - local music-story batch acceptance",
        f"Режим: {'LIVE AI' if result.get('live_ai') else 'LOCAL FALLBACK'}",
        f"Фільтр провайдера: {result.get('provider_filter') or 'усі доступні'}",
        f"Запитано підводок: {result['requested']}",
        f"Доступних верифікованих карток: {result['available_cards']}",
        f"РЕЗУЛЬТАТ: {'PASS' if result['ok'] else 'FAIL'} - {result['passed']}/{result['requested']}",
    ]
    for item in result["items"]:
        intro = item["intro"]
        profile = intro.get("voice_profile") or {}
        lines.extend([
            "",
            f"{item['index']:02d}. {'PASS' if item['ok'] else 'FAIL'} | card #{item['story']['id']} | повтор: {item['repeat']}",
            f"Поточний: {item['current']['artist']} - {item['current']['title']}",
            f"Наступний: {item['next_track']['artist']} - {item['next_track']['title']}",
            f"TTS: {profile.get('target_seconds', 0)} с | слів: {spoken_word_count(intro.get('speech_text', ''))}",
            f"Генератор: {intro.get('provider', '')} | fallback: {'так' if intro.get('fallback') else 'ні'}",
            f"Фактичний режим: {'source-locked' if intro.get('grounded_story') else 'template' if intro.get('fallback') else 'generative'}",
            "Quality: " + ", ".join(
                f"{name}={score}/10" for name, score in (intro.get("story_quality") or {}).items()
            ),
            f"Джерело: {item['story'].get('source_title', '')} | {item['story'].get('source_url', '')}",
            "Текст: " + intro.get("speech_text", ""),
        ])
        if intro.get("provider_error"):
            lines.append("Помилка генератора: " + _compact_error(intro["provider_error"]))
        lines.extend(f"Помилка: {error}" for error in item["errors"])
    return "\n".join(lines).rstrip() + "\n"


def run(
    root: Path,
    count: int,
    track_id: int | None,
    duration_seconds: float,
    live_ai: bool,
    provider_name: str = "",
) -> dict:
    with tempfile.TemporaryDirectory(prefix="lumen-story-intro-") as directory:
        temp_root = Path(directory)
        _copy_runtime_root(root, temp_root)
        api = RadioAPI(temp_root)
        try:
            candidates = _story_candidates(api, track_id)
            providers = api._ai_providers_for_intro() if live_ai else []
            if provider_name:
                providers = [
                    item for item in providers
                    if item.get("name") == provider_name
                    or item.get("provider_type") == provider_name
                ]
                if not providers:
                    raise ValueError(
                        f"Провайдер {provider_name!r} не налаштований для підводок"
                    )
            # Acceptance favors a conclusive verdict over the shorter
            # live-playback timeout; this affects only the temporary test copy.
            providers = [
                {
                    **item,
                    "timeout_seconds": max(
                        40, int(item.get("timeout_seconds") or 0)
                    ),
                }
                for item in providers
            ]
            items = []
            for index in range(count):
                current, next_track, story = candidates[index % len(candidates)]
                variant = index // len(candidates)
                intro = api.make_intro(
                    next_track["id"],
                    current_track_id=current["id"],
                    content_plan=_story_plan(story, duration_seconds, variant),
                    duration_seconds=duration_seconds,
                    store_track=False,
                    providers_override=providers,
                )
                errors = _check_intro(story, intro, require_ai=live_ai)
                items.append({
                    "index": index + 1,
                    "ok": not errors,
                    "errors": errors,
                    "repeat": variant + 1,
                    "current": current,
                    "next_track": next_track,
                    "story": story,
                    "intro": intro,
                })
            passed = sum(item["ok"] for item in items)
            return {
                "ok": passed == count,
                "requested": count,
                "available_cards": len(candidates),
                "passed": passed,
                "live_ai": live_ai,
                "provider_filter": provider_name,
                "items": items,
            }
        finally:
            api.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test 3-5 sentence music-story transitions using the local radio database"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--track-id", type=int)
    parser.add_argument("--duration", type=float, default=22)
    parser.add_argument("--live-ai", action="store_true")
    parser.add_argument("--provider", default="")
    parser.add_argument("--output", default="story_intro_acceptance_real.txt")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    count = max(1, min(50, args.count))
    duration_seconds = max(20.0, min(40.0, args.duration))
    result = run(
        root, count, args.track_id, duration_seconds, args.live_ai, args.provider
    )
    output = (root / args.output).resolve()
    output.write_text(_report(result), encoding="utf-8")
    print(output)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
