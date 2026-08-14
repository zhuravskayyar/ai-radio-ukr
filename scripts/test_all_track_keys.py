from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


NVIDIA_MODEL = "nvidia/nemotron-3-super-120b-a12b"
OPENROUTER_DEFAULT_MODEL = "openrouter/free"
OPENAI_MODEL = "gpt-5.4-mini"
QWEN_MODEL = "qwen-plus"
GEMINI_MODEL = "gemini-3.6-flash"

NVIDIA_RE = re.compile(r"nvapi-[A-Za-z0-9_-]+")
OPENROUTER_RE = re.compile(r"sk-or-[A-Za-z0-9_-]+")
OPENAI_RE = re.compile(r"sk-proj-[A-Za-z0-9_-]+")
GENERIC_SK_RE = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
BARE_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{24,}")


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def next_nonempty(lines: list[str], index: int) -> str:
    for line in lines[index + 1 :]:
        if line.strip():
            return line.strip()
    return ""


def parse_specs(path: Path) -> tuple[list[dict], list[dict]]:
    raw = path.read_text(encoding="utf-8-sig")
    lines = raw.splitlines()
    specs: list[dict] = []
    unresolved: list[dict] = []

    for index, key in enumerate(unique(NVIDIA_RE.findall(raw)), start=1):
        specs.append(
            {
                "label": f"NVIDIA {index}",
                "provider": "NVIDIA",
                "key": key,
                "url": "https://integrate.api.nvidia.com/v1/chat/completions",
                "model": NVIDIA_MODEL,
                "kind": "openai-compatible",
                "extra": {"chat_template_kwargs": {"enable_thinking": False}},
            }
        )

    inline_openrouter_models: dict[str, str] = {}
    for line in lines:
        key_match = OPENROUTER_RE.search(line)
        model_match = re.search(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+)", line)
        if key_match and model_match:
            inline_openrouter_models[key_match.group(0)] = model_match.group(1)
    for index, key in enumerate(unique(OPENROUTER_RE.findall(raw)), start=1):
        specs.append(
            {
                "label": f"OpenRouter {index}",
                "provider": "OpenRouter",
                "key": key,
                "url": "https://openrouter.ai/api/v1/chat/completions",
                "model": inline_openrouter_models.get(
                    key, OPENROUTER_DEFAULT_MODEL
                ),
                "kind": "openai-compatible",
                "headers": {
                    "HTTP-Referer": "https://vector-radio.local",
                    "X-Title": "Vector Radio API test",
                },
            }
        )

    for index, key in enumerate(unique(OPENAI_RE.findall(raw)), start=1):
        specs.append(
            {
                "label": f"OpenAI {index}",
                "provider": "OpenAI",
                "key": key,
                "url": "https://api.openai.com/v1/chat/completions",
                "model": OPENAI_MODEL,
                "kind": "openai-compatible",
                "token_field": "max_completion_tokens",
            }
        )

    qwen_key = ""
    omni_keys: list[str] = []
    for index, line in enumerate(lines):
        label = line.strip().casefold()
        if label == "qwen":
            candidate = next_nonempty(lines, index)
            match = GENERIC_SK_RE.fullmatch(candidate)
            if match and not candidate.startswith(("sk-or-", "sk-proj-")):
                qwen_key = candidate
        elif label == "omni":
            for value in lines[index + 1 :]:
                candidate = value.strip()
                match = GENERIC_SK_RE.match(candidate)
                if match and not candidate.startswith(("sk-or-", "sk-proj-")):
                    omni_keys.append(match.group(0))

    if qwen_key:
        specs.append(
            {
                "label": "Qwen 1",
                "provider": "Qwen / Alibaba Cloud",
                "key": qwen_key,
                "url": (
                    "https://dashscope-intl.aliyuncs.com/compatible-mode/"
                    "v1/chat/completions"
                ),
                "model": QWEN_MODEL,
                "kind": "openai-compatible",
            }
        )

    recognised = set(
        NVIDIA_RE.findall(raw)
        + OPENROUTER_RE.findall(raw)
        + OPENAI_RE.findall(raw)
        + ([qwen_key] if qwen_key else [])
        + omni_keys
    )
    bare_tokens = []
    for line in lines:
        candidate = line.strip()
        if (
            BARE_TOKEN_RE.fullmatch(candidate)
            and candidate not in recognised
            and "/" not in candidate
        ):
            bare_tokens.append(candidate)
    for index, key in enumerate(unique(bare_tokens), start=1):
        specs.append(
            {
                "label": f"Gemini {index}",
                "provider": "Google Gemini",
                "key": key,
                "url": (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{GEMINI_MODEL}:generateContent"
                ),
                "model": GEMINI_MODEL,
                "kind": "gemini",
            }
        )

    for index, key in enumerate(unique(omni_keys), start=1):
        unresolved.append(
            {
                "label": f"Omni {index}",
                "fingerprint": fingerprint(key),
                "reason": "У TXT немає base URL та назви моделі Omni.",
            }
        )

    seen = set()
    deduped = []
    for spec in specs:
        marker = (spec["provider"], spec["key"])
        if marker in seen:
            continue
        seen.add(marker)
        spec["fingerprint"] = fingerprint(spec["key"])
        deduped.append(spec)
    return deduped, unresolved


def request_prompt(style: str) -> str:
    return (
        "Поверни ТІЛЬКИ валідний JSON без Markdown і пояснень. "
        "Потрібно рівно 10 реальних, офіційно виданих і широко відомих треків. "
        "Не вигадуй виконавців або назви, не повторюй виконавця двічі підряд. "
        f"Стиль радіостанції: {style}. "
        'Формат: {"tracks":[{"artist":"...","title":"..."}]}'
    )


def compact_error(raw: str) -> str:
    try:
        payload = json.loads(raw)
        error = payload.get("error")
        if isinstance(error, dict):
            return str(
                error.get("message")
                or error.get("code")
                or error.get("type")
                or error
            )[:800]
        return str(payload.get("detail") or payload.get("message") or error or raw)[:800]
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(raw or "Unknown error")[:800]


def extract_tracks(content: str) -> tuple[list[dict], str]:
    cleaned = re.sub(
        r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE
    ).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return [], "Відповідь не містить JSON-об'єкта"
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        return [], f"Некоректний JSON: {exc}"
    tracks = []
    seen = set()
    for item in payload.get("tracks", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        artist = str(item.get("artist") or "").strip()
        title = str(item.get("title") or "").strip()
        marker = (artist.casefold(), title.casefold())
        if artist and title and marker not in seen:
            seen.add(marker)
            tracks.append({"artist": artist, "title": title})
    if len(tracks) != 10:
        return tracks, f"Отримано {len(tracks)} унікальних треків замість 10"
    return tracks, ""


def call_openai_compatible(spec: dict, prompt: str, timeout: float) -> tuple[int, str]:
    token_field = spec.get("token_field", "max_tokens")
    payload = {
        "model": spec["model"],
        "messages": [
            {
                "role": "system",
                "content": "Ти музичний редактор. Виконуй JSON-контракт точно.",
            },
            {"role": "user", "content": prompt},
        ],
        token_field: 1200,
        "stream": False,
    }
    if spec.get("provider") != "OpenAI":
        payload.update({"temperature": 0.2, "top_p": 0.9})
    if spec.get("provider") == "OpenRouter":
        payload["reasoning"] = {"enabled": False, "exclude": True}
    payload.update(spec.get("extra") or {})
    headers = {
        "Authorization": f"Bearer {spec['key']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        **(spec.get("headers") or {}),
    }
    request = urllib.request.Request(
        spec["url"],
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.load(response)
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or choice.get("text") or ""
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text") or part.get("content") or part)
                for part in content
            )
        return response.status, str(content).strip()


def call_gemini(spec: dict, prompt: str, timeout: float) -> tuple[int, str]:
    payload = {
        "system_instruction": {
            "parts": [{"text": "Ти музичний редактор. Виконуй JSON-контракт точно."}]
        },
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 1200,
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }
    request = urllib.request.Request(
        spec["url"],
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "x-goog-api-key": spec["key"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.load(response)
        candidates = body.get("candidates") or []
        parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
        content = " ".join(str(part.get("text") or "") for part in parts).strip()
        return response.status, content


def test_spec(spec: dict, style: str, timeout: float) -> dict:
    started = time.perf_counter()
    try:
        if spec["kind"] == "gemini":
            status, content = call_gemini(spec, request_prompt(style), timeout)
        else:
            status, content = call_openai_compatible(
                spec, request_prompt(style), timeout
            )
        elapsed = time.perf_counter() - started
        tracks, error = extract_tracks(content)
        return {
            **{key: spec[key] for key in ("label", "provider", "model", "fingerprint")},
            "ok": status == 200 and len(tracks) == 10,
            "status": status,
            "seconds": elapsed,
            "tracks": tracks,
            "error": error,
            "raw": content[:2000] if error else "",
        }
    except urllib.error.HTTPError as exc:
        elapsed = time.perf_counter() - started
        details = exc.read().decode("utf-8", errors="replace")
        return {
            **{key: spec[key] for key in ("label", "provider", "model", "fingerprint")},
            "ok": False,
            "status": exc.code,
            "seconds": elapsed,
            "tracks": [],
            "error": compact_error(details),
            "raw": "",
        }
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {
            **{key: spec[key] for key in ("label", "provider", "model", "fingerprint")},
            "ok": False,
            "status": 0,
            "seconds": elapsed,
            "tracks": [],
            "error": f"{type(exc).__name__}: {exc}",
            "raw": "",
        }


def format_report(
    input_path: Path,
    style: str,
    results: list[dict],
    unresolved: list[dict],
) -> str:
    passed = sum(1 for item in results if item["ok"])
    lines = [
        "VECTOR RADIO — ПЕРЕВІРКА ВСІХ API НА 10 ТРЕКІВ",
        f"Дата: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Вхідний TXT: {input_path}",
        f"Стиль: {style}",
        f"Протестовано ключів: {len(results)}",
        f"Успішно повернули рівно 10 треків: {passed}/{len(results)}",
        f"Не протестовано безпечним способом: {len(unresolved)}",
        "Секретні ключі у звіт не записуються; fingerprint — SHA-256[:10].",
        "",
    ]
    for item in sorted(results, key=lambda value: value["label"]):
        lines.extend(
            [
                "=" * 72,
                f"{item['label']} | {item['provider']} | key={item['fingerprint']}",
                f"Модель: {item['model']}",
                f"Статус: {'ПРАЦЮЄ' if item['ok'] else 'ПОМИЛКА'}",
                f"HTTP: {item['status'] or 'немає відповіді'}",
                f"Час: {item['seconds']:.2f} с",
                f"Треків: {len(item['tracks'])}/10",
            ]
        )
        if item["error"]:
            lines.append(f"Помилка: {item['error']}")
        for index, track in enumerate(item["tracks"], start=1):
            lines.append(f"{index:02d}. {track['artist']} — {track['title']}")
        if item["raw"]:
            lines.extend(["Сира відповідь:", item["raw"]])
        lines.append("")

    if unresolved:
        lines.extend(["=" * 72, "НЕ ПРОТЕСТОВАНО", ""])
        for item in unresolved:
            lines.append(
                f"- {item['label']} | key={item['fingerprint']}: {item['reason']}"
            )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test recognized API keys by requesting exactly ten tracks."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("all_api_10_tracks_results.txt")
    )
    parser.add_argument("--style", default="шансон")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--only-provider",
        default="",
        help="Optional exact provider name, for example OpenRouter.",
    )
    args = parser.parse_args()

    specs, unresolved = parse_specs(args.input)
    if args.only_provider:
        specs = [
            spec for spec in specs
            if spec["provider"].casefold() == args.only_provider.casefold()
        ]
        unresolved = []
    if not specs:
        raise SystemExit(f"Recognized API keys not found in {args.input}")

    results = []
    with ThreadPoolExecutor(max_workers=min(6, len(specs))) as executor:
        futures = {
            executor.submit(test_spec, spec, args.style, args.timeout): spec
            for spec in specs
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                f"{result['label']}: {'OK' if result['ok'] else 'ERROR'} "
                f"tracks={len(result['tracks'])}/10 ({result['seconds']:.2f} s)",
                flush=True,
            )

    report = format_report(args.input, args.style, results, unresolved)
    args.output.write_text(report, encoding="utf-8-sig")
    print(f"Report: {args.output.resolve()}")
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
