from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODELS_URL = "https://integrate.api.nvidia.com/v1/models"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
KEY_PATTERN = re.compile(r"nvapi-[A-Za-z0-9_-]+")


def load_keys(path: Path) -> tuple[list[str], int]:
    raw = path.read_text(encoding="utf-8-sig")
    matches = KEY_PATTERN.findall(raw)
    return list(dict.fromkeys(matches)), len(matches)


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
            )[:500]
        return str(payload.get("detail") or payload.get("message") or error or raw)[:500]
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(raw or "Unknown error")[:500]


def check_key(index: int, key: str, model: str, timeout: float) -> dict:
    request_body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an API connectivity checker. Return only valid JSON "
                    'with this shape: {"ok":true,"message":"API працює"}.'
                ),
            },
            {"role": "user", "content": "Перевір з'єднання."},
        ],
        "temperature": 0,
        "top_p": 1,
        "max_tokens": 64,
        "stream": False,
    }
    if model.startswith("nvidia/nemotron-3"):
        request_body["chat_template_kwargs"] = {"enable_thinking": False}
    request = urllib.request.Request(
        NVIDIA_URL,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        elapsed = time.perf_counter() - started
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or choice.get("text") or ""
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text") or part.get("content") or part)
                for part in content
            )
        content = str(content).strip()
        return {
            "index": index,
            "ok": bool(content),
            "status": 200,
            "seconds": elapsed,
            "result": content or "Порожня відповідь",
        }
    except urllib.error.HTTPError as exc:
        elapsed = time.perf_counter() - started
        details = exc.read().decode("utf-8", errors="replace")
        return {
            "index": index,
            "ok": False,
            "status": exc.code,
            "seconds": elapsed,
            "result": compact_error(details),
        }
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {
            "index": index,
            "ok": False,
            "status": 0,
            "seconds": elapsed,
            "result": f"{type(exc).__name__}: {exc}",
        }


def check_key_auth(index: int, key: str, timeout: float) -> dict:
    request = urllib.request.Request(
        NVIDIA_MODELS_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        elapsed = time.perf_counter() - started
        models = payload.get("data") if isinstance(payload, dict) else None
        count = len(models) if isinstance(models, list) else 0
        return {
            "index": index,
            "ok": True,
            "status": 200,
            "seconds": elapsed,
            "result": f"Авторизація успішна; доступних моделей: {count}",
        }
    except urllib.error.HTTPError as exc:
        elapsed = time.perf_counter() - started
        details = exc.read().decode("utf-8", errors="replace")
        return {
            "index": index,
            "ok": False,
            "status": exc.code,
            "seconds": elapsed,
            "result": compact_error(details),
        }
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {
            "index": index,
            "ok": False,
            "status": 0,
            "seconds": elapsed,
            "result": f"{type(exc).__name__}: {exc}",
        }


def format_report(
    input_path: Path,
    model: str,
    total_matches: int,
    results: list[dict],
    mode: str,
) -> str:
    passed = sum(1 for item in results if item["ok"])
    lines = [
        "Vector Radio — перевірка NVIDIA API",
        f"Дата: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Вхідний TXT: {input_path}",
        f"Тест: {'авторизація та список моделей' if mode == 'models' else 'генерація відповіді'}",
        f"Модель: {model if mode == 'inference' else 'не застосовується'}",
        f"Знайдено записів ключів: {total_matches}",
        f"Унікальних ключів: {len(results)}",
        f"Працює: {passed}/{len(results)}",
        "Секретні ключі у звіт не записуються.",
        "",
    ]
    for item in sorted(results, key=lambda value: value["index"]):
        state = "ПРАЦЮЄ" if item["ok"] else "ПОМИЛКА"
        lines.extend(
            [
                f"API {item['index']}: {state}",
                f"HTTP: {item['status'] or 'немає відповіді'}",
                f"Час: {item['seconds']:.2f} с",
                f"Результат: {item['result']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely test NVIDIA API keys from a TXT file."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("api_test_results.txt"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--mode", choices=("inference", "models"), default="inference")
    args = parser.parse_args()

    keys, total_matches = load_keys(args.input)
    if not keys:
        raise SystemExit(f"NVIDIA API keys not found in {args.input}")

    results = []
    with ThreadPoolExecutor(max_workers=len(keys)) as executor:
        futures = {
            executor.submit(
                check_key_auth if args.mode == "models" else check_key,
                *(
                    (index, key, args.timeout)
                    if args.mode == "models"
                    else (index, key, args.model, args.timeout)
                ),
            ): index
            for index, key in enumerate(keys, start=1)
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                f"API {result['index']}: "
                f"{'OK' if result['ok'] else 'ERROR'} "
                f"({result['seconds']:.2f} s)",
                flush=True,
            )

    report = format_report(
        args.input, args.model, total_matches, results, args.mode
    )
    args.output.write_text(report, encoding="utf-8-sig")
    print(f"Report: {args.output.resolve()}")
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
