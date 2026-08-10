from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone


PILOT_CLOCK_VERSION = "2026.08-pilot-v1"
HARD_POINT_TOLERANCE_SECONDS = 5


@dataclass(frozen=True)
class ClockSegment:
    slot_id: str
    start_minute: int
    duration_seconds: int
    name: str
    content_focus: str
    thesis: str
    entry_cue: str
    exit_cue: str
    cta: str
    fallback: str
    source_policy: str
    forbidden_claims: tuple[str, ...]
    hard_point: bool = False


PILOT_CLOCK = (
    ClockSegment(
        "hour_open", 0, 300, "Відкриття години", "identity_time",
        "Хто веде ефір, котра година і який музичний напрям зараз починається.",
        "Точний початок години; station ID без джинглового перевантаження.",
        "Короткий вихід у перший трек музичного sweep.",
        "Без CTA.",
        "Локальний station ID, точний час із контексту, потім музика.",
        "Час тільки з ContextEngine; цифрову ідентичність назвати прямо.",
        ("непідтверджені новини", "вигаданий прогноз", "вигаданий людський досвід"),
        True,
    ),
    ClockSegment(
        "music_sweep_a", 5, 300, "Музичний sweep A", "music_sweep",
        "Два музичні кроки з мінімальним втручанням ведучого.",
        "Чистий стик або коротка реакція на попередній трек.",
        "Зберегти темп і підвести до музичної історії.",
        "Без CTA.",
        "Чистий музичний перехід.",
        "Жодних фактів без Story Card або verified fact.",
        ("чарти без джерела", "дати релізу без джерела", "оцінка популярності як факт"),
    ),
    ClockSegment(
        "story_a", 10, 300, "Play Together: історія A", "story",
        "Одна перевірена музична історія з конкретним поворотом.",
        "Hook або найсильніша деталь Story Card.",
        "Природний reveal наступного треку.",
        "Одне коротке запитання лише за наявності каналу відповіді.",
        "Скорочена версія перевіреної картки; без картки — музичний sweep.",
        "Claim/evidence map; A–C джерела; sensitive лише після людського review.",
        ("факти поза карткою", "переказ як пряма цитата", "непідтверджена причина події"),
    ),
    ClockSegment(
        "quarter_service", 15, 300, "Сервісна чверть", "service",
        "Точний час і одна практична контекстна деталь без повного бюлетеня.",
        "Hard point; коротке позначення чверті години.",
        "Повернення до музики не пізніше кінця сегмента.",
        "Без CTA.",
        "Якщо погоди немає — точний час і station ID.",
        "Час і погода лише з кешованого ContextEngine.",
        ("прогноз без даних", "порада з безпеки без офіційного джерела"),
        True,
    ),
    ClockSegment(
        "music_sweep_b", 20, 300, "Музичний sweep B", "music_sweep",
        "Контраст або продовження енергії без зайвих пояснень.",
        "Реакція на зміну енергії чи чистий стик.",
        "Підвести до одного перевіреного факту.",
        "Без CTA.",
        "Чистий музичний перехід.",
        "Тільки метадані треків і перевірений контекст.",
        ("вигаданий жанр", "вигадана реакція артиста"),
    ),
    ClockSegment(
        "fact_focus", 25, 300, "Один факт", "fact",
        "Один перевірений факт, одна цифра або один неочевидний контекст.",
        "Теза одразу, без енциклопедичного вступу.",
        "Музичний доказ або природний вихід у трек.",
        "Без CTA.",
        "Без verified fact — короткий mood transition.",
        "Verified fact або Story Card; чітко позначити позицію джерела.",
        ("друга неперевірена цифра", "біографічна деталь без джерела"),
    ),
    ClockSegment(
        "half_hour_reset", 30, 300, "Півгодинний reset", "listener",
        "Коротко повернути увагу слухача до поточного настрою ефіру.",
        "Hard point; одна пряма думка або конкретне запитання.",
        "Новий музичний напрям другої половини години.",
        "Одне питання, відповідь на яке займає 10–20 секунд; лише після запуску moderation.",
        "Без інтерактивного каналу — mood-check без заклику відповідати.",
        "Контекст сесії та музики; жодних персональних даних слухача.",
        ("імітація неіснуючих відповідей", "заклик надсилати приватні дані"),
        True,
    ),
    ClockSegment(
        "story_b", 35, 300, "Play Together: історія B", "story",
        "Друга перевірена історія або продовження серії без повтору першої.",
        "Новий hook чи callback із пам'яті серії.",
        "Закінчити фактичну частину до reveal треку.",
        "Можливий тизер наступного епізоду без фальшивої обіцянки.",
        "Скорочена картка; без нової картки — музичний sweep.",
        "Claim/evidence map; окрема перевірка кожного твердження.",
        ("повтор першої історії", "вигаданий cliffhanger", "непідтверджена цитата"),
    ),
    ClockSegment(
        "music_sweep_c", 40, 300, "Музичний sweep C", "music_sweep",
        "Дати музиці простір перед ідентифікацією станції.",
        "Чистий стик або одна коротка фраза.",
        "Підготувати точний station ID на :45.",
        "Без CTA.",
        "Чистий музичний перехід.",
        "Без нових фактів.",
        ("зайва історія", "довгий монолог"),
    ),
    ClockSegment(
        "three_quarter_id", 45, 300, "Ідентифікація :45", "station_id",
        "Назвати станцію й відкрито цифрового ведучого без рекламного пафосу.",
        "Hard point; короткий station ID.",
        "Негайний вихід у музику.",
        "Без CTA.",
        "Локальний заздалегідь перевірений liner.",
        "Тільки назва станції, ім'я/роль ведучого та музичний контекст.",
        ("непідтверджене охоплення", "рекламна перевага", "імітація людини"),
        True,
    ),
    ClockSegment(
        "discovery", 50, 300, "Відкриття/рубрика", "rubric",
        "Один свіжий музичний штрих без штучної сенсаційності.",
        "Назва рубрики або конкретне спостереження.",
        "Трек має залишитися головним результатом сегмента.",
        "Одна проста дія лише після визначення каналу аналітики.",
        "Без готової рубрики — звичайний короткий перехід.",
        "Факти лише з перевіреної картки; назва може бути обіграна без фактичних домислів.",
        ("слово «новий» без дати", "вигадана популярність", "порожня сенсація"),
    ),
    ClockSegment(
        "hour_close", 55, 300, "Закриття години", "hour_close",
        "Завершити дугу години й залишити чистий запас до наступного :00.",
        "Короткий callback лише якщо він є в пам'яті.",
        "Музика або резерв мають безпечно довести ефір до hard point.",
        "Без CTA, якщо він уже звучав у цій годині.",
        "Короткий liner або чиста музика до наступного :00.",
        "Тільки факти, що вже прозвучали й збережені в пам'яті ефіру.",
        ("новий довгий сюжет", "неперевірений тизер", "обіцянка точного часу без плану"),
    ),
)


def _parse_moment(value):
    if isinstance(value, datetime):
        moment = value
    elif value:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        moment = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


class PilotClock:
    """A versioned, deterministic 60-minute editorial clock."""

    def __init__(self, segments=PILOT_CLOCK):
        self.segments = tuple(segments)
        self.validate()

    def validate(self):
        cursor = 0
        hard_points = []
        for segment in self.segments:
            start_second = segment.start_minute * 60
            if start_second != cursor:
                raise ValueError(f"Clock gap or overlap before {segment.slot_id}")
            if not 180 <= segment.duration_seconds <= 420:
                raise ValueError(f"Segment {segment.slot_id} is outside 3–7 minutes")
            if segment.hard_point:
                hard_points.append(start_second)
            cursor += segment.duration_seconds
        if cursor != 3600:
            raise ValueError(f"Pilot clock must equal 3600 seconds, got {cursor}")
        if hard_points != [0, 900, 1800, 2700]:
            raise ValueError(f"Hard points must be :00/:15/:30/:45, got {hard_points}")
        return True

    @staticmethod
    def _hour_start(moment):
        return moment.replace(minute=0, second=0, microsecond=0)

    def _segment_payload(self, segment, hour_start, responsible_editor=""):
        planned_start = hour_start + timedelta(minutes=segment.start_minute)
        planned_end = planned_start + timedelta(seconds=segment.duration_seconds)
        payload = asdict(segment)
        payload.update({
            "forbidden_claims": list(segment.forbidden_claims),
            "planned_start": planned_start.isoformat(),
            "planned_end": planned_end.isoformat(),
            "hard_time": planned_start.isoformat() if segment.hard_point else "",
            "timing_tolerance_seconds": (
                HARD_POINT_TOLERANCE_SECONDS if segment.hard_point else None
            ),
            "responsible_editor": responsible_editor or "НЕ ПРИЗНАЧЕНО",
            "editor_status": "assigned" if responsible_editor else "required",
            "verification": "required_before_air",
            "pronunciation": "track phonetics must be reviewed before TTS",
        })
        return payload

    def snapshot(self, value=None, responsible_editor=""):
        moment = _parse_moment(value)
        hour_start = self._hour_start(moment)
        elapsed = (moment - hour_start).total_seconds()
        segment = next(
            item for item in self.segments
            if item.start_minute * 60 <= elapsed
            < item.start_minute * 60 + item.duration_seconds
        )
        payload = self._segment_payload(segment, hour_start, responsible_editor)
        planned_start = _parse_moment(payload["planned_start"])
        timing_error = (
            round((moment - planned_start).total_seconds(), 3)
            if segment.hard_point else None
        )
        hard_point_due = bool(
            segment.hard_point
            and abs(timing_error) <= HARD_POINT_TOLERANCE_SECONDS
        )
        hard_point_missed = bool(
            segment.hard_point
            and timing_error > HARD_POINT_TOLERANCE_SECONDS
        )
        later_hard_points = [
            hour_start + timedelta(minutes=item.start_minute)
            for item in self.segments if item.hard_point
            and hour_start + timedelta(minutes=item.start_minute) > moment
        ]
        next_hard_time = (
            later_hard_points[0]
            if later_hard_points else hour_start + timedelta(hours=1)
        )
        return {
            "enabled": True,
            "version": PILOT_CLOCK_VERSION,
            "hour_key": hour_start.strftime("%Y-%m-%dT%H"),
            "hour_start": hour_start.isoformat(),
            "hour_end": (hour_start + timedelta(hours=1)).isoformat(),
            "slot_key": f"{hour_start.strftime('%Y-%m-%dT%H')}:{segment.slot_id}",
            "segment_number": self.segments.index(segment) + 1,
            "segment": payload,
            "hard_point_due": hard_point_due,
            "hard_point_missed": hard_point_missed,
            "timing_error_seconds": timing_error,
            "next_hard_time": next_hard_time.isoformat(),
            "seconds_to_next_hard_point": max(
                0, round((next_hard_time - moment).total_seconds(), 3)
            ),
        }

    def rundown(self, value=None, responsible_editor=""):
        moment = _parse_moment(value)
        hour_start = self._hour_start(moment)
        current = self.snapshot(moment, responsible_editor)
        return {
            "enabled": True,
            "version": PILOT_CLOCK_VERSION,
            "hour_key": current["hour_key"],
            "hour_start": current["hour_start"],
            "hour_end": current["hour_end"],
            "total_seconds": sum(item.duration_seconds for item in self.segments),
            "segment_count": len(self.segments),
            "hard_points": [":00", ":15", ":30", ":45"],
            "hard_point_tolerance_seconds": HARD_POINT_TOLERANCE_SECONDS,
            "current_slot_id": current["segment"]["slot_id"],
            "responsible_editor": responsible_editor or "НЕ ПРИЗНАЧЕНО",
            "editor_status": "assigned" if responsible_editor else "required",
            "segments": [
                self._segment_payload(item, hour_start, responsible_editor)
                for item in self.segments
            ],
        }
