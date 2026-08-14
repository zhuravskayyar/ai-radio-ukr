import hashlib
import json
import re
from datetime import datetime, timezone

from .listener_personalization import intro_type_for_story_category


STORY_CATEGORIES = (
    "SONG_ORIGIN",
    "STUDIO_STORY",
    "BAND_ARGUMENT",
    "VOCALIST_STORY",
    "LYRICS_ORIGIN",
    "ACCIDENTAL_HIT",
    "REJECTED_SONG",
    "RECORDING_TRICK",
    "LIVE_STORY",
    "NAME_STORY",
    "CHART_STORY",
    "COLLABORATION",
    "BEFORE_FAME",
    "ALBUM_CONTEXT",
    "FAN_STORY",
)

DURATION_SECONDS = {
    "short": 10.0,
    "normal": 20.0,
    "feature": 37.5,
}

STORY_MODES = {
    "track_story": {"SONG_ORIGIN", "STUDIO_STORY", "LYRICS_ORIGIN", "REJECTED_SONG", "RECORDING_TRICK", "NAME_STORY"},
    "artist_story": {"VOCALIST_STORY", "BAND_ARGUMENT", "COLLABORATION", "BEFORE_FAME"},
    "interesting_fact": {"ACCIDENTAL_HIT", "CHART_STORY"},
    "nostalgia_era": {"LIVE_STORY", "ALBUM_CONTEXT", "FAN_STORY"},
}

SOURCE_TIERS = ("A", "A-", "B", "B-", "C", "D", "E")
EDITORIALLY_RELIABLE_TIERS = {"A", "A-", "B", "B-", "C"}


def _source_id(value, offset):
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").casefold()).strip("-")
    return cleaned or f"source-{offset}"


def _normalize_source(source, offset):
    if not isinstance(source, dict):
        raise ValueError(f"Джерело {offset} має бути об'єктом")
    url = _clean_sentence(source.get("url") or source.get("source_url"))
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise ValueError(f"Джерело {offset} має містити повний HTTP(S) URL")
    tier = str(source.get("tier") or "B").strip().upper()
    if tier not in SOURCE_TIERS:
        raise ValueError(
            f"Рівень джерела {offset} має бути одним із: {', '.join(SOURCE_TIERS)}"
        )
    return {
        "id": _source_id(source.get("id"), offset),
        "url": url,
        "title": _clean_sentence(source.get("title") or source.get("source_title")),
        "tier": tier,
        "primary": bool(source.get("primary", False)),
        "independent": bool(source.get("independent", True)),
    }


def _verification_summary(confidence, sources, claims, sensitive, reviewed_by, reviewed_at):
    source_ids = {source["id"] for source in sources}
    reliable = [source for source in sources if source["tier"] in EDITORIALLY_RELIABLE_TIERS]
    claims_are_sourced = bool(claims) and all(
        claim.get("source_ids")
        and set(claim["source_ids"]).issubset(source_ids)
        for claim in claims
    )
    independent_urls = {
        source["url"].casefold()
        for source in reliable
        if source.get("independent")
    }
    primary_count = sum(bool(source.get("primary")) for source in reliable)
    if confidence != "verified":
        status = "draft"
    elif len(reliable) != len(sources) or not claims_are_sourced:
        status = "needs_review"
    elif sensitive and (
        len(independent_urls) < 2 or not reviewed_by or not reviewed_at
    ):
        status = "human_review_required"
    elif len(independent_urls) >= 2:
        status = "corroborated"
    elif primary_count:
        status = "primary_source"
    else:
        status = "single_source"
    return {
        "status": status,
        "broadcast_ready": status in {
            "corroborated", "primary_source", "single_source"
        },
        "source_count": len(sources),
        "independent_source_count": len(independent_urls),
        "primary_source_count": primary_count,
        "claims_are_sourced": claims_are_sourced,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "sensitive": bool(sensitive),
    }


def _clean_sentence(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


class MusicKnowledgeBase:
    """Stores and selects source-backed story cards; it never researches live."""

    def __init__(self, db):
        self.db = db

    def add_card(self, track_id, card):
        track = self.db.track(int(track_id))
        if not track:
            return {"ok": False, "error": "Трек не знайдено"}
        category = str(card.get("category") or "").strip().upper()
        if category not in STORY_CATEGORIES:
            return {"ok": False, "error": "Невідома категорія музичної історії"}
        story_data = card.get("story_data") or []
        if isinstance(story_data, str):
            story_data = [part.strip() for part in story_data.split("|") if part.strip()]
        story_data = [_clean_sentence(part) for part in story_data if _clean_sentence(part)]
        if not story_data:
            return {"ok": False, "error": "Додайте хоча б один перевірений фрагмент історії"}
        confidence = str(card.get("confidence") or "draft").strip().casefold()
        if confidence not in {"draft", "verified"}:
            return {"ok": False, "error": "Confidence має бути draft або verified"}
        source_url = _clean_sentence(card.get("source_url"))
        source_title = _clean_sentence(card.get("source_title"))
        source_items = card.get("sources") or []
        if source_items and not isinstance(source_items, list):
            return {"ok": False, "error": "sources має бути масивом джерел"}
        if not source_items and source_url:
            source_items = [{
                "id": "source-1",
                "url": source_url,
                "title": source_title,
                "tier": str(card.get("source_tier") or "B"),
                "primary": bool(card.get("source_is_primary", False)),
                "independent": True,
            }]
        try:
            sources = [
                _normalize_source(source, offset)
                for offset, source in enumerate(source_items, 1)
            ]
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        source_ids = [source["id"] for source in sources]
        if len(source_ids) != len(set(source_ids)):
            return {"ok": False, "error": "Ідентифікатори джерел мають бути унікальними"}
        if confidence == "verified" and not sources:
            return {"ok": False, "error": "Для перевіреної історії потрібне джерело"}
        if confidence == "verified" and any(
            source["tier"] not in EDITORIALLY_RELIABLE_TIERS for source in sources
        ):
            return {
                "ok": False,
                "error": "Рівні D та E можна зберігати лише в чернетці, а не як VERIFIED",
            }
        raw_claims = card.get("claims") or []
        if raw_claims and not isinstance(raw_claims, list):
            return {"ok": False, "error": "claims має бути масивом тверджень"}
        claims = []
        if raw_claims:
            for offset, claim in enumerate(raw_claims, 1):
                if not isinstance(claim, dict):
                    return {"ok": False, "error": f"Твердження {offset} має бути об'єктом"}
                text = _clean_sentence(claim.get("text"))
                claim_sources = [
                    _source_id(source_id, index)
                    for index, source_id in enumerate(claim.get("source_ids") or [], 1)
                ]
                if not text or not claim_sources:
                    return {
                        "ok": False,
                        "error": f"Твердження {offset} потребує тексту й source_ids",
                    }
                if not set(claim_sources).issubset(set(source_ids)):
                    return {
                        "ok": False,
                        "error": f"Твердження {offset} посилається на невідоме джерело",
                    }
                claims.append({"text": text, "source_ids": list(dict.fromkeys(claim_sources))})
            claim_texts = {_clean_sentence(claim["text"]).casefold() for claim in claims}
            missing_claims = [
                part for part in story_data if part.casefold() not in claim_texts
            ]
            if missing_claims:
                return {
                    "ok": False,
                    "error": "Кожен фрагмент story_data має мати окремий запис у claims",
                }
        else:
            claims = [
                {"text": part, "source_ids": list(source_ids)} for part in story_data
            ]
        reviewed_by = _clean_sentence(card.get("reviewed_by"))
        reviewed_at = _clean_sentence(card.get("reviewed_at"))
        sensitive = bool(card.get("sensitive", False))
        verification = _verification_summary(
            confidence, sources, claims, sensitive, reviewed_by, reviewed_at
        )
        if confidence == "verified" and not verification["broadcast_ready"]:
            if verification["status"] == "human_review_required":
                return {
                    "ok": False,
                    "error": (
                        "Чутлива історія потребує двох незалежних надійних джерел, "
                        "імені редактора й часу перевірки"
                    ),
                }
            return {"ok": False, "error": "Картка не пройшла редакційну перевірку"}
        duration_class = str(card.get("duration_class") or "normal").strip().casefold()
        if duration_class not in DURATION_SECONDS:
            return {"ok": False, "error": "Тривалість має бути short, normal або feature"}
        hook = _clean_sentence(card.get("hook"))
        series_key = re.sub(r"[^a-z0-9_-]+", "-", str(card.get("series_key") or "").casefold()).strip("-")
        legacy_source = sources[0] if sources else {}
        source_url = legacy_source.get("url", "")
        source_title = legacy_source.get("title", "")
        identity = "\0".join((
            category,
            hook,
            "\0".join(story_data),
            json.dumps(sources, ensure_ascii=False, sort_keys=True),
        ))
        story_key = str(card.get("story_key") or "").strip() or hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()[:16]
        try:
            episode = max(0, int(card.get("episode") or 0))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Номер епізоду має бути цілим числом"}
        row = self.db.add_story({
            "track_id": int(track_id),
            "story_key": story_key,
            "category": category,
            "hook": hook,
            "story_data_json": json.dumps(story_data, ensure_ascii=False),
            "verified_quote": _clean_sentence(card.get("verified_quote")),
            "source_url": source_url,
            "source_title": source_title,
            "sources_json": json.dumps(sources, ensure_ascii=False),
            "claims_json": json.dumps(claims, ensure_ascii=False),
            "verification_status": verification["status"],
            "broadcast_ready": int(verification["broadcast_ready"]),
            "reviewed_by": reviewed_by,
            "reviewed_at": reviewed_at,
            "sensitive": int(sensitive),
            "confidence": confidence,
            "duration_class": duration_class,
            "series_key": series_key,
            "episode": episode,
            "tease_next": _clean_sentence(card.get("tease_next")),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return {"ok": True, "story": self.payload(row), "track": self.db.track(int(track_id))}

    def cards_for_track(self, track_id, verified_only=True):
        return [
            self.payload(row)
            for row in self.db.stories_for_track(int(track_id), verified_only)
        ]

    def select(self, track_id, excluded_intro_types=(), intro_type_scores=None):
        cards = self.cards_for_track(track_id, verified_only=True)
        if not cards:
            return None
        excluded = {str(value or "").casefold() for value in excluded_intro_types}
        scores = intro_type_scores or {}
        for card in cards:
            card["intro_type"] = intro_type_for_story_category(card.get("category"))
        cards = [
            card for card in cards
            if card.get("intro_type") not in excluded
        ]
        if not cards:
            return None
        verification_priority = {
            "corroborated": 0,
            "primary_source": 1,
            "single_source": 2,
        }
        cards.sort(key=lambda card: (
            verification_priority.get(card.get("verification_status"), 9),
            -float(scores.get(card.get("intro_type"), 0.5)),
            int(card.get("use_count") or 0),
            str(card.get("last_used_at") or ""),
            int(card.get("episode") or 0),
            int(card.get("id") or 0),
        ))
        episodes = {
            (card.get("series_key"), int(card.get("episode") or 0))
            for card in cards
        }
        for card in cards:
            if card.get("tease_next") and (
                card.get("series_key"), int(card.get("episode") or 0) + 1
            ) not in episodes:
                card["tease_next"] = ""
        # If a serial story has started, continue with the next verified episode.
        for card in cards:
            series_key = card.get("series_key")
            if not series_key or not card.get("episode"):
                continue
            memory = self.db.memory(f"story_series:{series_key}")
            if not memory:
                continue
            try:
                previous = json.loads(memory.get("value_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                previous = {}
            if int(card["episode"]) == int(previous.get("episode") or 0) + 1:
                card["callback"] = previous.get("tease_next") or ""
                return card

        fresh_cards = [card for card in cards if int(card.get("use_count") or 0) == 0]
        if fresh_cards:
            return fresh_cards[0]
        return None

    def mark_used(self, story_id, used_at=None):
        used = used_at or datetime.now(timezone.utc).isoformat()
        self.db.mark_story_used(int(story_id), used)

    @staticmethod
    def payload(row):
        if not row:
            return None
        try:
            story_data = json.loads(row.get("story_data_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            story_data = []
        try:
            sources = json.loads(row.get("sources_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            sources = []
        if not sources and row.get("source_url"):
            sources = [{
                "id": "source-1",
                "url": row.get("source_url", ""),
                "title": row.get("source_title", ""),
                "tier": "B",
                "primary": False,
                "independent": True,
            }]
        try:
            claims = json.loads(row.get("claims_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            claims = []
        if not claims:
            claims = [
                {"text": part, "source_ids": [source["id"] for source in sources]}
                for part in story_data if isinstance(part, str)
            ]
        verification = _verification_summary(
            row.get("confidence", "draft"),
            sources,
            claims,
            bool(row.get("sensitive", 0)),
            row.get("reviewed_by", ""),
            row.get("reviewed_at", ""),
        )
        category = row.get("category", "")
        story_mode = next(
            (mode for mode, categories in STORY_MODES.items() if category in categories),
            "track_story",
        )
        return {
            **row,
            "story_data": story_data if isinstance(story_data, list) else [],
            "sources": sources if isinstance(sources, list) else [],
            "claims": claims if isinstance(claims, list) else [],
            "verification": verification,
            "target_seconds": DURATION_SECONDS.get(row.get("duration_class"), 20.0),
            "story_mode": story_mode,
        }
