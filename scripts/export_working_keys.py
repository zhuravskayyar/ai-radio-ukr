from __future__ import annotations

from pathlib import Path

from test_all_track_keys import parse_specs


SOURCE = Path(r"C:\Users\yarik\Desktop\api.txt")
OUTPUT = Path(__file__).resolve().parents[1] / "working_api_keys.txt"

WORKING = {
    "490996984b": {
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "result": "10/10",
        "seconds": "100.96",
    },
    "c195123a37": {
        "model": "openrouter/free",
        "result": "10/10",
        "seconds": "30.51",
    },
    "22846b79cd": {
        "model": "openrouter/free",
        "result": "10/10",
        "seconds": "23.52",
    },
}


def main() -> int:
    specs, _unresolved = parse_specs(SOURCE)
    selected = [spec for spec in specs if spec["fingerprint"] in WORKING]
    selected.sort(key=lambda spec: spec["label"])

    lines = [
        "VECTOR RADIO — ПРАЦЮЮЧІ API-КЛЮЧІ",
        "Джерело: C:\\Users\\yarik\\Desktop\\api.txt",
        "Критерій: API повернув рівно 10 позицій під час перевірки.",
        "УВАГА: файл містить секретні ключі у відкритому вигляді.",
        "",
    ]
    for spec in selected:
        result = WORKING[spec["fingerprint"]]
        lines.extend(
            [
                f"Провайдер: {spec['provider']}",
                f"Назва тесту: {spec['label']}",
                f"Модель: {result['model']}",
                f"Ключ: {spec['key']}",
                f"Результат: {result['result']}",
                f"Час відповіді: {result['seconds']} с",
                "",
            ]
        )

    if len(selected) != len(WORKING):
        missing = sorted(set(WORKING) - {spec["fingerprint"] for spec in selected})
        raise SystemExit(f"Working key fingerprints not found: {', '.join(missing)}")

    OUTPUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8-sig")
    print(f"Exported {len(selected)} working keys to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
