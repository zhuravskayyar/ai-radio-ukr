import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse


PROTOCOL_VERSION = "2026.08-safety-v1"


PROTOCOLS = {
    "dead_air": {
        "severity": "critical",
        "automatic": True,
        "status": "triggered",
        "steps": [
            "зафіксувати момент втрати виходу",
            "спробувати відновити поточний локальний файл",
            "перейти до наступного локального треку",
            "якщо музики немає — озвучити технічну паузу",
        ],
        "script": (
            "Маємо технічну паузу. Відновлюю музичний ефір із перевіреного резерву."
        ),
    },
    "technical_pause": {
        "severity": "high",
        "automatic": True,
        "status": "triggered",
        "steps": [
            "коротко назвати технічну паузу",
            "не обіцяти невідомий час відновлення",
            "запустити локальний резерв",
        ],
        "script": (
            "Коротка технічна пауза. Точний час відновлення не вигадую; "
            "перемикаюся на локальний музичний резерв."
        ),
    },
    "guest_drop": {
        "severity": "medium",
        "automatic": False,
        "status": "queued",
        "steps": [
            "не звинувачувати гостя",
            "один раз повідомити про втрату зв'язку",
            "перейти до резервного питання або музики",
        ],
        "script": (
            "Зв'язок із гостем перервався. Спробуємо повернути розмову, "
            "а зараз продовжуємо музикою."
        ),
    },
    "unconfirmed_fact": {
        "severity": "high",
        "automatic": False,
        "status": "blocked",
        "steps": [
            "не озвучувати твердження",
            "передати його редактору",
            "повернутися лише після підтвердження",
        ],
        "script": (
            "Цю інформацію поки не підтверджено. Не ставлю її в ефір "
            "до редакторської перевірки."
        ),
    },
    "abusive_listener": {
        "severity": "high",
        "automatic": False,
        "status": "rejected",
        "steps": [
            "не відтворювати повідомлення в ефірі",
            "не сперечатися з автором",
            "зберегти мінімальний журнал модерації без зайвих персональних даних",
        ],
        "script": "",
    },
    "safety_alert": {
        "severity": "critical",
        "automatic": False,
        "status": "human_review_required",
        "steps": [
            "перевірити офіційне першоджерело",
            "отримати схвалення відповідального редактора",
            "передати тільки практично необхідну дію",
            "прибрати гумор, музику й непідтверджені деталі",
        ],
        "script": "",
    },
    "correction": {
        "severity": "high",
        "automatic": False,
        "status": "queued",
        "steps": [
            "назвати, що саме було неточним",
            "одразу дати правильну інформацію",
            "зафіксувати джерело й редактора",
            "не виправдовуватися й не жартувати",
        ],
        "script": "",
    },
    "silence_warning": {
        "severity": "warning",
        "automatic": True,
        "status": "observed",
        "steps": ["зафіксувати попередження", "не втручатися до fallback-порога"],
        "script": "",
    },
    "silence_recovered": {
        "severity": "info",
        "automatic": True,
        "status": "resolved",
        "steps": ["зафіксувати спосіб відновлення"],
        "script": "",
    },
}


def _clean(value, limit=500):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _valid_source_url(value):
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class BroadcastSafety:
    """Deterministic safety and correction protocols; never calls an LLM."""

    def __init__(self, db):
        self.db = db

    def protocol(self, event_type, details=None, persist=True):
        event_type = str(event_type or "").strip().casefold()
        spec = PROTOCOLS.get(event_type)
        if not spec:
            return {"ok": False, "error": "Невідомий аварійний протокол"}
        details = dict(details or {})
        script = spec["script"]
        responsible_editor = _clean(details.get("responsible_editor"), 120)
        source_url = _clean(details.get("source_url"), 1000)

        if event_type == "safety_alert":
            official_text = _clean(details.get("official_text"), 700)
            if not official_text or not _valid_source_url(source_url) or not responsible_editor:
                return {
                    "ok": False,
                    "status": "human_review_required",
                    "error": (
                        "Safety alert потребує офіційного тексту, HTTP(S) джерела "
                        "і відповідального редактора"
                    ),
                    "protocol": self._payload(event_type, spec),
                }
            script = official_text

        payload = self._payload(event_type, spec)
        payload.update({
            "ok": True,
            "display_text": script,
            "responsible_editor": responsible_editor,
            "source_url": source_url,
        })
        if persist:
            event_id = self.db.add_broadcast_event({
                "event_type": event_type,
                "severity": spec["severity"],
                "status": spec["status"],
                "details_json": json.dumps(details, ensure_ascii=False),
                "script_text": script,
                "source_url": source_url,
                "responsible_editor": responsible_editor,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            payload["event_id"] = event_id
        return payload

    def correction(self, original, corrected, source_url, source_title, editor):
        original = _clean(original)
        corrected = _clean(corrected)
        source_url = _clean(source_url, 1000)
        source_title = _clean(source_title, 180)
        editor = _clean(editor, 120)
        missing = [
            label for label, value in (
                ("попередня неточність", original),
                ("правильна інформація", corrected),
                ("назва джерела", source_title),
                ("відповідальний редактор", editor),
            ) if not value
        ]
        if missing or not _valid_source_url(source_url):
            return {
                "ok": False,
                "status": "human_review_required",
                "error": (
                    "Для виправлення потрібні: " + ", ".join(missing or ["повний HTTP(S) URL джерела"])
                ),
            }
        script = (
            f"Виправлення. Раніше в ефірі прозвучало: {original}. "
            f"Правильно: {corrected}. Джерело перевірки — {source_title}. "
            "Перепрошую за неточність."
        )
        event_id = self.db.add_broadcast_event({
            "event_type": "correction",
            "severity": "high",
            "status": "queued",
            "details_json": json.dumps({
                "original": original,
                "corrected": corrected,
                "source_title": source_title,
            }, ensure_ascii=False),
            "script_text": script,
            "source_url": source_url,
            "responsible_editor": editor,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return {
            "ok": True,
            "event_id": event_id,
            "status": "queued",
            "display_text": script,
            "source_url": source_url,
            "responsible_editor": editor,
            "protocol": self._payload("correction", PROTOCOLS["correction"]),
        }

    @staticmethod
    def _payload(event_type, spec):
        return {
            "version": PROTOCOL_VERSION,
            "event_type": event_type,
            "severity": spec["severity"],
            "status": spec["status"],
            "automatic": spec["automatic"],
            "steps": list(spec["steps"]),
        }

    def status(self, limit=20):
        events = self.db.broadcast_events(limit)
        return {
            "ok": True,
            "version": PROTOCOL_VERSION,
            "recent": events,
            "open_corrections": sum(
                event["event_type"] == "correction"
                and event["status"] not in {"aired", "resolved"}
                for event in events
            ),
            "critical_events": sum(
                event["severity"] == "critical" for event in events
            ),
        }
