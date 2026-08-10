import json
import random
from datetime import datetime, timezone
from typing import List


HOST_PERSONA = {
    "name": "Люмен",
    "identity": "нічний цифровий ведучий LUMEN RADIO",
    "tone": ["спокійний", "розумний", "іронічний", "атмосферний"],
    "traits": [
        "не перебільшує",
        "говорить коротко",
        "пам'ятає попередні треки",
        "реагує на зміну настрою й енергії",
        "не намагається жартувати в кожній репліці",
    ],
    "forbidden": [
        "шановні слухачі",
        "наступна композиція",
        "залишайтеся з нами",
        "справжній хіт",
        "підкорив чарти",
        "музична подорож",
        "неймовірний трек",
    ],
}


class HostBrain:
    """Orchestrates host decisions: memory, intent, mention policy, timing.

    This is a lightweight coordinator — heavy policies remain in
    `ContentPlanner`, `VoiceDirector` and `MusicKnowledgeBase`.
    """

    def __init__(self, db, planner=None, voice_director=None, knowledge=None):
        self.db = db
        self.planner = planner
        self.voice_director = voice_director
        self.knowledge = knowledge

    def record_opening(self, opening: dict) -> None:
        """Persist a host opening into DB memory buffer.

        opening: {opening, content_type, artist, topic, entities, ending_type, energy}
        """
        entry = {
            "opening": opening.get("opening", ""),
            "content_type": opening.get("content_type", ""),
            "artist": opening.get("artist", ""),
            "topic": opening.get("topic", ""),
            "entities": opening.get("entities", []),
            "ending_type": opening.get("ending_type", "track_launch"),
            "energy": opening.get("energy", None),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.db.add_host_opening(entry)

    def recent_openings(self, limit: int = 50) -> List[dict]:
        return self.db.recent_host_openings(limit)

    def avoid_phrases_instruction(self, limit: int = 20) -> str:
        """Return a short instruction text listing recent opening starts to avoid."""
        recent = self.recent_openings(limit)
        starts = []
        for r in recent:
            opening = (r.get("opening") or "").strip()
            if not opening:
                continue
            first = " ".join(opening.split()[:6])
            if first and first not in starts:
                starts.append(first)
        if not starts:
            return ""
        lines = [f"НЕ повторюй конструкції останніх підводок:"]
        for i, s in enumerate(starts[:10], 1):
            lines.append(f"{i}. \"{s}\"")
        return "\n".join(lines)

    def choose_mention_policy(self) -> str:
        policies = [
            "artist_and_title",
            "artist_only",
            "title_only",
            "implicit",
        ]
        settings = getattr(self.db, "settings", lambda: {})()
        preferred = settings.get("mention_policy")
        if preferred in policies:
            return preferred
        return random.choice(policies)

    def recent_structures(self, limit: int = 3) -> list[str]:
        return [
            item.get("topic") or item.get("content_type", "")
            for item in self.recent_openings(limit)
            if item.get("topic") or item.get("content_type")
        ]

    @staticmethod
    def persona_prompt() -> str:
        return (
            "Ти — Люмен, живий ведучий LUMEN RADIO і програми Play Together. "
            "Орієнтир — дорослий рок-ефір: впевнено, музично, з характером, без читання папірця. "
            "Ти вмієш коротко підвести трек, а коли є перевірені дані — зробити мініісторію або факт "
            "так, щоб це звучало як радіоведучий, а не енциклопедія. "
            "Не будь надто стерильним: природна розмовність, легка іронія й живий темп важливіші за ідеальну симетрію фраз. "
            "Не вигадуй біографію, дати, релізи й цитати без переданих перевірених даних."
        )

    def choose_transition_intent(self, current_track: dict, next_track: dict) -> str:
        """Heuristic intent chooser — can be replaced by ML later."""
        if not current_track or not next_track:
            return "introduce"
        energy_delta = (float(next_track.get("energy") or 5) - float(current_track.get("energy") or 5))
        if abs(energy_delta) >= 3:
            return "contrast" if energy_delta < 0 else "introduce"
        if current_track.get("artist") != next_track.get("artist"):
            return "introduce"
        return "recall"

    def timing_directive(self, next_track: dict) -> dict:
        """Return talk window guidance computed from track metadata."""
        vocal_start = int(next_track.get("vocal_start_ms") or 0)
        intro_ms = int(next_track.get("intro_ms") or 0)
        available = max(0, (vocal_start - 800) / 1000.0) if vocal_start else float(self.db.settings().get("host_length", 15))
        return {
            "talk_start_offset": -(available),
            "talk_end_offset": available,
            "must_finish_before_vocal": bool(vocal_start),
            "target_seconds": min(max(3.0, available), 40.0),
        }
