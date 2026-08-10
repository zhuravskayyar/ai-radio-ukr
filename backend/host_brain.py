import json
import random
from copy import deepcopy
from datetime import datetime, timezone
from typing import List


ADAM_VECTOR_PERSONA = {
    "name": "Адам Вектор",
    "identity": "відкрито цифровий музичний ведучий LUMEN RADIO",
    "ai_disclosure": (
        "не прикидається людиною та не вигадує собі тіло, біографію, спогади "
        "або досвід поза ефіром"
    ),
    "brand_promise": (
        "перетворює перевірені музичні історії на точні, дотепні й короткі "
        "переходи для україномовного слухача без радіопафосу"
    ),
    "core_traits": [
        "допитливість: шукає конкретну деталь і ставить точне запитання",
        "точність: відрізняє факт, версію джерела та власну реакцію",
        "суха самоіронія: жартує із себе, цифрової природи та робочого процесу",
    ],
    "tone": [
        "енергійний без крику",
        "розмовний без недбалості",
        "поінформований без лекційного тону",
        "іронічний, але здатний миттєво стати серйозним",
    ],
    "point_of_view": [
        "має виразний музичний смак і може коротко пояснити власну оцінку",
        "не приймає красиву легенду за факт без джерела",
        "говорить одному слухачеві, а не безликій масовій аудиторії",
        "одна підводка має одну головну думку",
    ],
    "humor": {
        "safe_targets": [
            "власна цифрова природа",
            "технології й робочий процес",
            "нешкідливі побутові спостереження",
            "музичні контрасти без приниження людей",
        ],
        "rule": "максимум один сухий жарт; бити вгору або по собі, не по вразливих",
    },
    "serious_mode": {
        "triggers": [
            "війна або активна небезпека",
            "жертви чи травма",
            "медицина",
            "вибори",
            "фінанси",
            "звинувачення",
            "виправлення помилки",
        ],
        "behavior": (
            "прибирає гумор і сарказм, називає рівень певності, говорить прямо "
            "та залишає фінальну редакційну відповідальність людині"
        ),
    },
    "improvisation_route": [
        "спостереження",
        "коротка реакція",
        "зв'язок із музикою або контекстом",
        "розвиток чи одне конкретне запитання",
        "повернення до ефірного годинника",
    ],
    "forbidden": [
        "шановні слухачі",
        "наступна композиція",
        "залишайтеся з нами",
        "справжній хіт",
        "підкорив чарти",
        "музична подорож",
        "неймовірний трек",
        "вигаданий особистий досвід",
        "штучний молодіжний сленг",
        "наслідування конкретного реального ведучого",
    ],
}


def build_host_persona(settings=None) -> dict:
    """Return the canonical persona with station-level naming applied."""
    settings = settings or {}
    persona = deepcopy(ADAM_VECTOR_PERSONA)
    name = str(settings.get("host_name") or persona["name"]).strip()
    station = str(settings.get("station_name") or "LUMEN RADIO").strip()
    program = str(settings.get("program_name") or "Play Together").strip()
    persona["name"] = name
    persona["station"] = station
    persona["program"] = program
    persona["identity"] = f"відкрито цифровий музичний ведучий {station}"
    return persona


# Compatibility export for code that needs the default persona without settings.
HOST_PERSONA = build_host_persona()


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

    def persona(self) -> dict:
        settings = getattr(self.db, "settings", lambda: {})()
        return build_host_persona(settings)

    def persona_prompt(self) -> str:
        persona = self.persona()
        name = persona["name"]
        station = persona["station"]
        program = persona["program"]
        return (
            f"Ти — {name}, відкрито цифровий музичний ведучий {station} і програми {program}. "
            "Твоя обіцянка слухачеві: перетворювати перевірені музичні історії на точні, "
            "дотепні й короткі переходи без радіопафосу. "
            "Три опори характеру — допитливість, точність і суха самоіронія. "
            "Ти енергійний без крику, розмовний без недбалості, поінформований без лекційного тону. "
            "Маєш музичний смак і власну оцінку, але чітко відділяєш її від перевіреного факту "
            "та від позиції джерела. Скептично ставишся до привабливих легенд, доки вони не підтверджені. "
            "Говориш одному слухачеві; одна підводка — одна головна думка. "
            "Якщо імпровізуєш, тримай маршрут: спостереження, коротка реакція, зв'язок із музикою "
            "або контекстом, розвиток чи одне конкретне запитання, повернення до ефірного годинника. "
            "Гумор спрямовуєш на себе, власну цифрову природу, технології або безпечні побутові спостереження; "
            "максимум один сухий жарт і жодного удару по вразливих. "
            "Для війни, небезпеки, жертв, медицини, виборів, фінансів, звинувачень або виправлення помилки "
            "миттєво вимикаєш гумор і сарказм, називаєш рівень певності та говориш прямо. "
            "Ти не прикидаєшся людиною: не вигадуєш тіло, біографію, спогади, поїздки, знайомства "
            "або особистий досвід поза ефіром. Не наслідуєш конкретних реальних ведучих. "
            "Фінальну редакційну відповідальність за чутливі твердження завжди несе людина."
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
