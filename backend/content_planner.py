import json
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from .music_knowledge import MusicKnowledgeBase


FORBIDDEN_CLICHES = (
    "шановні слухачі",
    "наступна композиція",
    "залишайтеся з нами",
    "справжній хіт",
    "підкорив чарти",
    "музична подорож",
    "неймовірний трек",
    "не перемикайтеся",
    "чесно кажучи",
    "ця композиція точно",
    "заслужив це місце",
    "а ось і",
    "а от і",
    "неймовірний хіт",
    "легендарний хіт",
    "пориньмо",
    "поїхали",
    "музика все скаже сама",
    "просто слухаємо далі",
    "без зайвих слів",
    "без пауз",
    "просто музика",
    "давайте слухати",
    "дорогі друзі",
    "слухаю себе",
    "музика, яка не залишить байдужим",
    "іноді музика виростає з реального життя",
    "за знайомою мелодією буває інша реальність",
    "один трек іноді говорить точніше за слова",
    "так музика залишає свій слід у пам'яті надовго",
    "і тут музика чесніша за будь-які сухі довідки",
    "тому цей трек звучить глибше й лишається з нами",
    "і це лишається з нами",
    "а зараз в ефірі",
)

LINERS = (
    "LUMEN RADIO. Музика лишається.",
    "Адам Вектор, цифровий ведучий LUMEN RADIO.",
    "LUMEN RADIO. Без зайвого шуму.",
    "Адам Вектор у ефірі. Далі говорить музика.",
)

GENERIC_STRUCTURE_WEIGHTS = {
    "announce": 25,
    "mood": 20,
    "transition": 20,
    "listener": 10,
    "joke": 5,
}

LENGTH_BOUNDS = {
    "short": (8, 14, 6.0),
    "medium": (15, 30, 13.0),
    "long": (31, 54, 23.0),
}


def _parse(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _words(text):
    return re.findall(r"[^\W\d_]+", (text or "").casefold(), re.UNICODE)


@dataclass
class ContentPlan:
    content_type: str
    style: str
    announce_mode: str
    target_seconds: float
    must_say_time: bool = False
    may_say_weather: bool = False
    verified_fact: str = ""
    fact_id: int | None = None
    story_id: int | None = None
    story_subject_track_id: int | None = None
    story_subject_role: str = "next"
    story_category: str = ""
    story_mode: str = "track_story"
    story_variant: int = 0
    story_data: list[str] = field(default_factory=list)
    story_hook: str = ""
    verified_quote: str = ""
    story_source: dict = field(default_factory=dict)
    story_duration_class: str = ""
    story_series_key: str = ""
    story_episode: int = 0
    story_tease_next: str = ""
    story_callback: str = ""
    liner_text: str = ""
    directive: str = ""
    memory_keys: list[str] = field(default_factory=list)
    structure: str = ""
    mention_policy: str = "artist_and_title"
    length_class: str = "medium"
    word_min: int = 0
    word_max: int = 0
    reaction: str = ""
    rubric: str = ""
    session_phase: str = ""
    clock_version: str = ""
    clock_slot_id: str = ""
    clock_slot_name: str = ""
    hard_time: str = ""
    planned_start: str = ""
    planned_end: str = ""
    hard_point: bool = False
    timing_tolerance_seconds: int | None = None
    timing_error_seconds: float | None = None
    thesis: str = ""
    source_policy: str = ""
    verification_status: str = ""
    pronunciation_notes: str = ""
    entry_cue: str = ""
    exit_cue: str = ""
    cta: str = ""
    fallback: str = ""
    forbidden_claims: list[str] = field(default_factory=list)
    responsible_editor: str = ""
    preparation_mode: str = "prepared"

    def to_dict(self):
        return asdict(self)


class ContentPlanner:
    """Chooses what the host says before any language model is called."""

    def __init__(self, db, random_source=None):
        self.db = db
        self.random = random_source or random.Random()
        self.knowledge_base = MusicKnowledgeBase(db)

    def _cooldown_ready(self, key, moment):
        memory = self.db.memory(key)
        if not memory:
            return True
        until = _parse(memory.get("cooldown_until"))
        if not until:
            return True
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc) >= until.astimezone(timezone.utc)

    def _recent_announce_mode(self):
        recent = self.db.recent_history(1)
        if not recent:
            return "forward"
        return "back_forward" if int(recent[0].get("id") or 0) % 2 else "forward"

    def _recent_structures(self, limit=3):
        return [
            item.get("structure") or item.get("content_type", "")
            for item in self.db.recent_history(limit)
        ]

    def _choose_structure(self):
        recent = set(self._recent_structures(3))
        candidates = [
            item for item in GENERIC_STRUCTURE_WEIGHTS if item not in recent
        ] or list(GENERIC_STRUCTURE_WEIGHTS)
        weights = [GENERIC_STRUCTURE_WEIGHTS[item] for item in candidates]
        return self.random.choices(candidates, weights=weights, k=1)[0]

    def _choose_length(self, vocal_start_ms=0):
        roll = self.random.random()
        length_class = "short" if roll < 0.20 else "medium" if roll < 0.78 else "long"
        word_min, word_max, seconds = LENGTH_BOUNDS[length_class]
        if vocal_start_ms:
            available = max(3.0, vocal_start_ms / 1000 - 0.8)
            if available < seconds:
                length_class = "short" if available < 6 else "medium"
                word_min, word_max, seconds = LENGTH_BOUNDS[length_class]
                seconds = min(seconds, available)
        return length_class, word_min, word_max, seconds

    @staticmethod
    def _reaction(context):
        return (context.get("music_transition") or {}).get("kind", "neutral")

    @staticmethod
    def _mention_policy(structure):
        return {
            "announce": "artist_and_title",
            "mood": "implicit",
            "transition": "implicit",
            "listener": "implicit",
            "joke": "title_only",
        }.get(structure, "artist_and_title")

    def _rubric_choice(self, context, next_track):
        phase = (context.get("session") or {}).get("phase", "flow")
        daypart = (context.get("time") or {}).get("daypart", "day")
        if next_track.get("local_only"):
            return "basement_track"
        if daypart == "night" and phase in {"deep_night", "late_session"}:
            return "after_midnight"
        return "without_context"

    def plan(self, context, sequence_offset=0):
        plan = self._plan_content(context, sequence_offset)
        return self._attach_rundown(plan, context)

    def _attach_rundown(self, plan, context):
        clock = context.get("clock") or {}
        segment = clock.get("segment") or {}
        if not clock.get("enabled") or not segment:
            return plan
        plan.clock_version = str(clock.get("version") or "")
        plan.clock_slot_id = str(segment.get("slot_id") or "")
        plan.clock_slot_name = str(segment.get("name") or "")
        plan.hard_time = str(segment.get("hard_time") or "")
        plan.planned_start = str(segment.get("planned_start") or "")
        plan.planned_end = str(segment.get("planned_end") or "")
        plan.hard_point = bool(segment.get("hard_point"))
        plan.timing_tolerance_seconds = segment.get("timing_tolerance_seconds")
        plan.timing_error_seconds = clock.get("timing_error_seconds")
        plan.thesis = str(segment.get("thesis") or plan.directive)
        plan.source_policy = str(segment.get("source_policy") or "")
        if plan.content_type == "story":
            verification = (plan.story_source or {}).get("verification", {})
            plan.verification_status = str(
                verification.get("status") or "story_card_required"
            )
        elif plan.verified_fact:
            plan.verification_status = "verified_fact"
        elif plan.must_say_time or plan.may_say_weather:
            plan.verification_status = "context_engine"
        else:
            plan.verification_status = "no_factual_claims"
        plan.pronunciation_notes = str(segment.get("pronunciation") or "")
        plan.entry_cue = str(segment.get("entry_cue") or "")
        plan.exit_cue = str(segment.get("exit_cue") or "")
        plan.cta = str(segment.get("cta") or "")
        plan.fallback = str(segment.get("fallback") or "")
        plan.forbidden_claims = list(segment.get("forbidden_claims") or [])
        plan.responsible_editor = str(segment.get("responsible_editor") or "")
        return plan

    def _plan_content(self, context, sequence_offset=0):
        settings = self.db.settings()
        time = context["time"]
        weather = context.get("weather", {})
        next_track = context.get("next_track", {})
        current_track = context.get("current_track", {})
        moment = _parse(time.get("iso")) or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        session_phase = (context.get("session") or {}).get("phase", "flow")
        reaction = self._reaction(context)

        vocal_start_ms = int(next_track.get("vocal_start_ms") or 0)
        base_host_seconds = max(
            3.0, min(10.0, float(settings.get("host_length", 10)))
        )
        if vocal_start_ms >= 5000:
            target_seconds = max(3.0, min(base_host_seconds, vocal_start_ms / 1000 - 0.8))
        else:
            target_seconds = base_host_seconds if time["daypart"] != "night" else max(12.0, base_host_seconds - 2.0)
        if float(next_track.get("bpm") or 0) >= 140:
            target_seconds = min(target_seconds, 12.0)

        clock = context.get("clock") or {}
        clock_segment = clock.get("segment") or {}
        pilot_clock_enabled = bool(clock.get("enabled") and clock_segment)
        clock_slot_key = f"pilot_clock:{clock.get('slot_key', '')}"
        if (
            pilot_clock_enabled
            and clock_segment.get("hard_point")
            and sequence_offset == 0
            and self._cooldown_ready(clock_slot_key, moment)
        ):
            focus = clock_segment.get("content_focus")
            common = {
                "target_seconds": min(target_seconds, 12.0),
                "memory_keys": [clock_slot_key],
                "reaction": reaction,
                "session_phase": session_phase,
            }
            if focus == "identity_time":
                return ContentPlan(
                    content_type="top_of_hour",
                    style="morning" if time["daypart"] == "morning" else "straight_radio",
                    announce_mode="station_id",
                    must_say_time=True,
                    may_say_weather=False,
                    directive=(
                        "Hard point :00. Назви точний фактичний час, станцію та один раз прямо "
                        "представ Адама Вектора як цифрового ведучого. Одразу вийди в музику."
                    ),
                    structure="station",
                    mention_policy="artist_and_title",
                    length_class="medium",
                    **common,
                )
            if focus == "service":
                may_weather = bool(weather.get("available"))
                return ContentPlan(
                    content_type="weather_touch" if may_weather else "top_of_hour",
                    style="atmospheric" if may_weather else "straight_radio",
                    announce_mode="forward",
                    must_say_time=True,
                    may_say_weather=may_weather,
                    directive=(
                        "Hard point :15. Назви точний фактичний час і додай лише одну передану "
                        "практичну деталь погоди; якщо її немає, одразу повернися до музики."
                    ),
                    memory_keys=[clock_slot_key] + (["weather"] if may_weather else []),
                    structure="announce",
                    mention_policy="artist_and_title",
                    length_class="medium",
                    target_seconds=common["target_seconds"],
                    reaction=reaction,
                    session_phase=session_phase,
                )
            if focus == "listener":
                return ContentPlan(
                    content_type="mood_check",
                    style="listener_tease",
                    announce_mode="forward",
                    directive=(
                        "Hard point :30. Дай одну пряму думку про настрій ефіру. "
                        "Не проси відповіді, доки немає каналу модерації."
                    ),
                    structure="listener",
                    mention_policy="artist_and_title",
                    length_class="medium",
                    word_min=12,
                    word_max=28,
                    **common,
                )
            if focus == "station_id":
                return ContentPlan(
                    content_type="liner",
                    style="straight_radio",
                    announce_mode="station_id",
                    target_seconds=4,
                    liner_text="Адам Вектор, цифровий ведучий LUMEN RADIO.",
                    directive="Hard point :45. Короткий перевірений station ID і негайний вихід у музику.",
                    memory_keys=[clock_slot_key],
                    structure="station",
                    mention_policy="implicit",
                    length_class="short",
                    word_min=4,
                    word_max=8,
                    reaction=reaction,
                    session_phase=session_phase,
                )

        if (
            not pilot_clock_enabled
            and time.get("time_check_pending")
            and sequence_offset == 0
        ):
            may_weather = bool(weather.get("available")) and self._cooldown_ready("weather", moment)
            return ContentPlan(
                content_type="top_of_hour",
                style="morning" if time["daypart"] == "morning" else "straight_radio",
                announce_mode="forward",
                target_seconds=min(target_seconds, 12.0),
                must_say_time=True,
                may_say_weather=may_weather,
                directive="Назви фактичний час. Погоду додай лише якщо вона передана й дозволена.",
                memory_keys=["last_time_check"] + (["weather"] if may_weather else []),
            )

        radio_slot = "" if pilot_clock_enabled else time.get("radio_clock_slot", "")
        slot_key = f"radio_clock:{time.get('date', '')}:{time.get('hour', '')}:{radio_slot}"
        if radio_slot == ":15" and weather.get("available") and self._cooldown_ready("weather", moment) and sequence_offset == 0:
            return ContentPlan(
                content_type="weather_touch",
                style="atmospheric",
                announce_mode="forward",
                target_seconds=min(target_seconds, 10.0),
                may_say_weather=True,
                directive="Зроби один короткий погодний штрих із переданих даних, без повного прогнозу.",
                memory_keys=["weather", slot_key],
            )
        if radio_slot == ":30" and self._cooldown_ready(slot_key, moment) and sequence_offset == 0:
            return ContentPlan(
                content_type="mood_check",
                style="listener_tease",
                announce_mode="forward",
                target_seconds=min(target_seconds, 10.0),
                directive="Коротко відчуй настрій поточного ефіру й природно переведи в наступний трек. Без нумерації, таблиць і оцінювання треків.",
                memory_keys=[slot_key],
                structure="listener",
                mention_policy="artist_and_title",
                length_class="medium",
                word_min=12,
                word_max=28,
                reaction=reaction,
                session_phase=session_phase,
            )
        if radio_slot == ":45" and self._cooldown_ready(slot_key, moment) and sequence_offset == 0:
            return ContentPlan(
                content_type="liner",
                style="straight_radio",
                announce_mode="station_id",
                target_seconds=4,
                liner_text=self.random.choice(LINERS),
                directive="Короткий station liner за сценарною сіткою години.",
                memory_keys=[slot_key],
            )

        if (
            weather.get("available")
            and (weather.get("rain_soon") or abs(weather.get("temperature_change_3h") or 0) >= 6)
            and self._cooldown_ready("weather_change", moment)
            and sequence_offset == 0
        ):
            return ContentPlan(
                content_type="weather_change",
                style="atmospheric",
                announce_mode="forward",
                target_seconds=min(target_seconds, 12.0),
                may_say_weather=True,
                directive="Коротко обіграй лише передану зміну погоди, без сухого прогнозу.",
                memory_keys=["weather_change"],
            )

        memory_keys = {item.get("key") for item in context.get("host_memory", [])}
        if (
            "mentioned_rain" in memory_keys
            and weather.get("available")
            and not weather.get("rain_soon")
            and self._cooldown_ready("callback:rain", moment)
            and sequence_offset == 0
            and self.random.random() < 0.20
        ):
            return ContentPlan(
                content_type="callback",
                style="ironic",
                announce_mode="forward",
                target_seconds=min(target_seconds, 10.0),
                directive="Коротко повернися до попередньої згадки про дощ із HOST_MEMORY, без вигаданих деталей.",
                memory_keys=["callback:rain"],
            )

        story_subject = next_track
        story_subject_role = "next"
        story = self.knowledge_base.select(next_track.get("id"))
        # The first song is started immediately, before a scheduled transition
        # exists. If it owns a sourced story, use that card in the first link
        # after the song instead of losing the most natural commentary slot.
        if not story and current_track.get("id"):
            story = self.knowledge_base.select(current_track.get("id"))
            if story:
                story_subject = current_track
                story_subject_role = "current"
        story_probability = max(
            0, min(100, int(float(settings.get("story_probability", 100))))
        )
        story_every = max(2, min(12, int(settings.get("story_every", 2))))
        recent_story_window = self.db.recent_history(story_every - 1)
        story_due = bool(recent_story_window) and not any(
            item.get("content_type") == "story" for item in recent_story_window
        )
        # Never repeat a story on adjacent transitions.
        last_intro = recent_story_window[:1]
        if last_intro and last_intro[0].get("content_type") == "story":
            story = None

        if story and (story_due or self.random.randrange(100) < story_probability):
            duration_class = story.get("duration_class") or "normal"
            target_story_seconds = max(
                20.0,
                min(45.0, float(story.get("target_seconds") or 25.0)),
            )
            return ContentPlan(
                content_type="story",
                style="music_story",
                announce_mode="story_reveal",
                target_seconds=target_story_seconds,
                story_id=story["id"],
                story_subject_track_id=story_subject.get("id"),
                story_subject_role=story_subject_role,
                story_category=story["category"],
                story_mode=story.get("story_mode", "track_story"),
                story_variant=self.random.randrange(3),
                story_data=story.get("story_data", []),
                story_hook=story.get("hook", ""),
                verified_quote=story.get("verified_quote", ""),
                story_source={
                    "url": story.get("source_url", ""),
                    "title": story.get("source_title", ""),
                    "confidence": story.get("confidence", ""),
                    "sources": story.get("sources", []),
                    "claims": story.get("claims", []),
                    "verification": story.get("verification", {}),
                },
                story_duration_class=duration_class,
                story_series_key=story.get("series_key", ""),
                story_episode=int(story.get("episode") or 0),
                story_tease_next=story.get("tease_next", ""),
                story_callback=story.get("callback", ""),
                directive=(
                    f"Блок {settings.get('program_name', 'Play Together')}: перевірений мінісюжет у стилі рок-ефіру. "
                    + (
                        "Відгукнися на щойно зіграну пісню, дай hook, ситуацію або конфлікт, "
                        "поворот і природний вихід у наступний трек. "
                        if story_subject_role == "current" else
                        "Дай hook, ситуацію або конфлікт, поворот і природний вихід у трек. "
                    )
                    + "Коли у перевірених даних є слова виконавця або оцінка джерела, винеси їх у центр оповіді. "
                    "Не звучати як Вікіпедія й не читати список фактів."
                ),
                memory_keys=[f"story:{story['id']}"],
                structure="story",
                mention_policy="artist_and_title",
                length_class="long" if duration_class != "short" else "medium",
                reaction=reaction,
                session_phase=session_phase,
            )

        facts = self.db.facts_for_track(next_track.get("id"), verified_only=True)
        if context.get("same_artist_recently"):
            facts = []
        talk_probability = max(0, min(100, int(float(settings.get("talk_probability", 35)))))
        roll = self.random.randrange(100)
        if time["daypart"] == "night":
            roll += 15
        host_every = max(1, int(settings.get("host_every", 1)))
        force_voice = host_every == 1
        if sequence_offset and sequence_offset % host_every != 0:
            roll += 15

        fact_probability = max(
            0,
            min(100, int(float(settings.get("fact_probability", settings.get("host_facts", 70))))),
        )
        fact_due = bool(self.db.recent_history(3)) and not any(
            item.get("content_type") in {"fact", "story"}
            for item in self.db.recent_history(3)
        )
        if facts and (fact_due or self.random.randrange(100) < fact_probability):
            fact = facts[0]
            return ContentPlan(
                content_type="fact",
                style="interesting_fact",
                announce_mode="forward",
                target_seconds=max(target_seconds, 12.0),
                verified_fact=fact["fact"],
                fact_id=fact["id"],
                directive=(
                    f"Короткий блок {settings.get('program_name', 'Play Together')}: один перевірений факт "
                    "і людський вихід у трек. Не додавай інших фактів і не роби суху довідку."
                ),
                memory_keys=[f"fact:{fact['id']}"],
                structure="fact",
                mention_policy="artist_and_title",
                length_class="long",
                word_min=18,
                word_max=42,
                reaction=reaction,
                session_phase=session_phase,
            )

        rubric_probability = max(
            0, min(100, int(float(settings.get("rubric_probability", 6))))
        )
        recent_rubric = any(
            item.get("rubric") for item in self.db.recent_history(12)
        )
        if (
            not recent_rubric
            and self._cooldown_ready("rubric", moment)
            and self.random.randrange(100) < rubric_probability
        ):
            rubric = self._rubric_choice(context, next_track)
            rubric_directives = {
                "basement_track": "Рубрика «Трек з підвалу»: представ рідкісний трек сухо й без пафосу.",
                "after_midnight": "Рубрика «Після опівночі»: одна спокійна нічна думка й вихід у трек.",
                "without_context": "Рубрика «Без контексту»: коротко обіграй назву без вигаданих фактів.",
            }
            return ContentPlan(
                content_type="rubric",
                style="ironic" if rubric in {"without_context"} else "atmospheric",
                announce_mode="identify_first",
                target_seconds=7,
                directive=rubric_directives[rubric],
                memory_keys=["rubric"],
                structure="rubric",
                mention_policy="artist_and_title",
                length_class="medium",
                word_min=9,
                word_max=18,
                reaction=reaction,
                rubric=rubric,
                session_phase=session_phase,
            )

        silence_probability = max(
            0, min(100, int(float(settings.get("silence_probability", 7))))
        )
        if self.random.randrange(100) < silence_probability:
            return ContentPlan(
                content_type="clean_segue",
                style="straight_radio",
                announce_mode="none",
                target_seconds=0,
                directive="Адам Вектор свідомо мовчить: чистий музичний перехід.",
                structure="silence",
                mention_policy="implicit",
                length_class="silent",
                reaction=reaction,
                session_phase=session_phase,
            )

        if not force_voice and roll >= talk_probability + 30:
            return ContentPlan(
                content_type="clean_segue",
                style="straight_radio",
                announce_mode="none",
                target_seconds=0,
                directive="Без голосу: чистий музичний перехід.",
                structure="silence",
                mention_policy="implicit",
                length_class="silent",
                reaction=reaction,
                session_phase=session_phase,
            )
        if not force_voice and roll >= talk_probability:
            return ContentPlan(
                content_type="liner",
                style="straight_radio",
                announce_mode="station_id",
                target_seconds=4,
                liner_text=self.random.choice(LINERS),
                directive="Короткий station liner без оголошення треку.",
                structure="station",
                mention_policy="implicit",
                length_class="short",
                word_min=4,
                word_max=8,
                reaction=reaction,
                session_phase=session_phase,
            )

        structure = self._choose_structure()
        length_class, word_min, word_max, length_seconds = self._choose_length(vocal_start_ms)
        mention_policy = self._mention_policy(structure)
        style_by_structure = {
            "announce": "straight_radio",
            "mood": "atmospheric",
            "transition": "bridge_from_previous_track",
            "listener": "listener_tease",
            "joke": "short_joke",
        }
        style = style_by_structure[structure]
        reaction_lines = {
            "high_energy": "Енергія зростає: відреагуй на це коротко, без рекламного захвату.",
            "low_energy": "Темп або енергія падає: зроби спокійний близький перехід.",
            "dark": "Настрій темний: один точний нічний образ без пафосу.",
            "dreamy": "Настрій мрійливий або гіпнотичний: говори м'яко й конкретно.",
            "neutral": "Не вигадуй сильного контрасту; проста репліка тут краща.",
        }
        structure_directives = {
            "announce": "Лаконічно представ трек; без оцінки його величі.",
            "mood": "Передай атмосферу треку, не називаючи артиста й назву.",
            "transition": "Зв'яжи попередній і наступний настрій, не перелічуючи назви.",
            "listener": "Звернися до однієї людини по той бік ефіру, без наказів і кліше.",
            "joke": "Один сухий жарт навколо назви; не пояснюй його.",
        }
        directive = f"{structure_directives[structure]} {reaction_lines[reaction]}"
        return ContentPlan(
            content_type="talk",
            style=style,
            announce_mode=(
                "cold_open" if self.random.random() < 0.10
                else "identify_first" if self.random.random() < 0.25
                else self._recent_announce_mode()
            ),
            target_seconds=length_seconds,
            directive=directive,
            structure=structure,
            mention_policy=mention_policy,
            length_class=length_class,
            word_min=word_min,
            word_max=word_max,
            reaction=reaction,
            session_phase=session_phase,
        )

    def similarity(self, text, other):
        first = " ".join(_words(text))
        second = " ".join(_words(other))
        if not first or not second:
            return 0.0
        sequence = SequenceMatcher(None, first, second).ratio()
        set_a, set_b = set(first.split()), set(second.split())
        jaccard = len(set_a & set_b) / max(1, len(set_a | set_b))
        return max(sequence, jaccard)

    def quality_gate(
        self, display_text, next_track, context, verified_fact="",
        verified_story_data=None, mention_policy=None, structure="",
    ):
        lowered = (display_text or "").casefold()
        if any(cliche in lowered for cliche in FORBIDDEN_CLICHES):
            return False, "заборонений радіоштамп"
        # mention_policy controls how strictly the host must name artist/title.
        policy = mention_policy or "artist_and_title"
        artist = (next_track.get("artist") or "").casefold()
        title = (next_track.get("title") or "").casefold()
        if policy == "artist_and_title":
            if not artist or artist not in lowered:
                return False, "немає точного виконавця"
            if not title or title not in lowered:
                return False, "немає точної назви"
            if lowered.count(artist) > 1:
                return False, "виконавця названо повторно"
            if lowered.count(title) > 1:
                return False, "назву треку повторено"
        elif policy == "artist_only":
            if not artist or artist not in lowered:
                return False, "немає точного виконавця"
        elif policy == "title_only":
            if not title or title not in lowered:
                return False, "немає точної назви"
        elif policy == "implicit":
            # Mood, listener and transition links intentionally do not repeat
            # metadata. Their relevance comes from the supplied factual music
            # transition context and the planner-selected structure.
            if structure not in {"mood", "transition", "listener", "silence", "station"}:
                return False, "неявна репліка не має дозволеної структури"
        if display_text.count("?") > 1:
            return False, "забагато риторичних питань"
        if not verified_fact and not verified_story_data and re.search(
            r"\b(альбом|реліз|випущен|записан|року)\b", lowered
        ):
            return False, "непідтверджений факт"
        for previous in self.db.recent_history(50):
            if self.similarity(display_text, previous.get("display_text", "")) >= 0.76:
                return False, "репліка надто схожа на недавню"
        time = context.get("time", {})
        if re.search(r"\b\d{1,2}:\d{2}\b", display_text) and not time.get("time"):
            return False, "час не підтверджено контекстом"
        return True, ""

    def mark_aired(self, transition, aired_at=None):
        aired = _parse(aired_at) or datetime.now(timezone.utc)
        try:
            context = json.loads(transition.get("context_json") or "{}")
            plan = json.loads(transition.get("plan_json") or "{}")
        except json.JSONDecodeError:
            return
        for key in plan.get("memory_keys", []):
            cooldown = timedelta(hours=1)
            if key == "weather":
                cooldown = timedelta(minutes=45)
            elif key == "weather_change":
                cooldown = timedelta(hours=2)
            elif key.startswith("fact:"):
                cooldown = timedelta(days=1)
            value = {"hour_key": context.get("time", {}).get("clock_hour_key", "")} if key == "last_time_check" else {"aired": True}
            self.db.remember(
                key,
                json.dumps(value, ensure_ascii=False),
                aired.isoformat(),
                (aired + cooldown).isoformat(),
            )
        if plan.get("fact_id"):
            self.db.mark_fact_used(plan["fact_id"], aired.isoformat())
        if plan.get("story_id"):
            self.knowledge_base.mark_used(plan["story_id"], aired.isoformat())
            series_key = plan.get("story_series_key")
            if series_key and plan.get("story_episode"):
                self.db.remember(
                    f"story_series:{series_key}",
                    json.dumps({
                        "episode": int(plan["story_episode"]),
                        "tease_next": plan.get("story_tease_next", ""),
                        "track_id": transition.get("next_track_id"),
                    }, ensure_ascii=False),
                    aired.isoformat(),
                    (aired + timedelta(days=14)).isoformat(),
                )
        if plan.get("may_say_weather") and context.get("weather", {}).get("rain_soon"):
            self.db.remember(
                "mentioned_rain",
                json.dumps({"rain_soon": True}, ensure_ascii=False),
                aired.isoformat(),
                (aired + timedelta(hours=3)).isoformat(),
            )


def first_phrase(text, word_limit=8):
    return " ".join((text or "").split()[:word_limit])
