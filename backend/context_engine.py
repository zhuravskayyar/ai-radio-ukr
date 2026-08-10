import json
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .host_brain import build_host_persona
from .pilot_clock import PilotClock


WEATHER_LABELS = {
    0: "ясно",
    1: "переважно ясно",
    2: "мінлива хмарність",
    3: "хмарно",
    45: "туман",
    48: "паморозь і туман",
    51: "слабка мряка",
    53: "мряка",
    55: "сильна мряка",
    61: "слабкий дощ",
    63: "дощ",
    65: "сильний дощ",
    71: "слабкий сніг",
    73: "сніг",
    75: "сильний сніг",
    80: "короткочасний дощ",
    81: "дощові заряди",
    82: "сильна злива",
    85: "снігові заряди",
    86: "сильні снігові заряди",
    95: "гроза",
    96: "гроза з градом",
    99: "сильна гроза з градом",
}

WEEKDAYS_UK = (
    "понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота",
    "неділя",
)

CITY_LOCATIVE = {
    "Київ": "Києві",
    "Львів": "Львові",
    "Одеса": "Одесі",
    "Харків": "Харкові",
    "Дніпро": "Дніпрі",
    "Запоріжжя": "Запоріжжі",
    "Вінниця": "Вінниці",
    "Полтава": "Полтаві",
    "Чернігів": "Чернігові",
    "Черкаси": "Черкасах",
}


def _float(settings, key, default):
    try:
        return float(settings.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _int(settings, key, default):
    try:
        return int(float(settings.get(key, default)))
    except (TypeError, ValueError):
        return int(default)


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            pass
    return None


def daypart_for(hour):
    if 6 <= hour < 10:
        return "morning"
    if 10 <= hour < 16:
        return "day"
    if 16 <= hour < 20:
        return "drive"
    if 20 <= hour < 24:
        return "evening"
    return "night"


@dataclass
class TimeContext:
    iso: str
    date: str
    time: str
    hour: int
    minute: int
    weekday: str
    daypart: str
    weekend: bool
    first_day_of_month: bool
    holiday: str
    clock_hour_key: str
    time_check_pending: bool
    radio_clock_slot: str


class ContextEngine:
    """Builds factual, cached context for content planning and generation."""

    def __init__(self, db):
        self.db = db
        self.started_at = datetime.now(timezone.utc)
        self.pilot_clock = PilotClock()

    @staticmethod
    def _session_phase(minutes_on_air):
        if minutes_on_air < 15:
            return "opening"
        if minutes_on_air < 60:
            return "flow"
        if minutes_on_air < 120:
            return "deep_night"
        return "late_session"

    @staticmethod
    def _track_reaction(current_track, next_track):
        current_energy = float((current_track or {}).get("energy") or 5)
        next_energy = float((next_track or {}).get("energy") or 5)
        delta = next_energy - current_energy
        mood = str((next_track or {}).get("mood") or "").casefold()
        if delta >= 3 or next_energy >= 8:
            kind = "high_energy"
        elif delta <= -3 or next_energy <= 3:
            kind = "low_energy"
        elif any(word in mood for word in ("dark", "темн", "gloom", "witch")):
            kind = "dark"
        elif any(word in mood for word in ("dream", "мрій", "ambient", "hypnot")):
            kind = "dreamy"
        else:
            kind = "neutral"
        return {
            "kind": kind,
            "energy_delta": round(delta, 2),
            "current_energy": current_energy,
            "next_energy": next_energy,
            "current_mood": str((current_track or {}).get("mood") or ""),
            "next_mood": str((next_track or {}).get("mood") or ""),
            "next_genre": str((next_track or {}).get("genre") or ""),
            "next_bpm": float((next_track or {}).get("bpm") or 0),
        }

    def _timezone(self, settings, reference=None):
        try:
            return ZoneInfo(settings.get("station_timezone", "Europe/Kyiv"))
        except ZoneInfoNotFoundError:
            if settings.get("station_timezone", "Europe/Kyiv") == "Europe/Kyiv":
                moment = reference or datetime.now(timezone.utc)
                year = moment.year
                march_end = datetime(year, 3, 31)
                october_end = datetime(year, 10, 31)
                summer_start = march_end - timedelta(days=(march_end.weekday() + 1) % 7)
                summer_end = october_end - timedelta(days=(october_end.weekday() + 1) % 7)
                is_summer = summer_start.date() <= moment.date() < summer_end.date()
                return timezone(timedelta(hours=3 if is_summer else 2), "Europe/Kyiv")
            return timezone.utc

    def time_context(self, scheduled_for=None, settings=None):
        settings = settings or self.db.settings()
        moment = _parse_datetime(scheduled_for) or datetime.now(timezone.utc)
        zone = self._timezone(settings, moment)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=zone)
        moment = moment.astimezone(zone)
        holidays = {
            (1, 1): "Новий рік",
            (8, 24): "День Незалежності України",
            (12, 25): "Різдво",
            (12, 31): "переддень Нового року",
        }
        hour_key = moment.strftime("%Y-%m-%dT%H")
        memory = self.db.memory("last_time_check")
        last_hour = ""
        if memory:
            try:
                last_hour = json.loads(memory["value_json"]).get("hour_key", "")
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return TimeContext(
            iso=moment.isoformat(),
            date=moment.strftime("%Y-%m-%d"),
            time=moment.strftime("%H:%M"),
            hour=moment.hour,
            minute=moment.minute,
            weekday=WEEKDAYS_UK[moment.weekday()],
            daypart=daypart_for(moment.hour),
            weekend=moment.weekday() >= 5,
            first_day_of_month=moment.day == 1,
            holiday=holidays.get((moment.month, moment.day), ""),
            clock_hour_key=hour_key,
            time_check_pending=last_hour != hour_key,
            radio_clock_slot=(
                f":{(moment.minute // 15) * 15:02d}"
                if moment.minute % 15 <= 5 else ""
            ),
        )

    def refresh_weather(self, settings=None, force=False):
        settings = settings or self.db.settings()
        if str(settings.get("weather_enabled", "0")) != "1":
            return self.weather_context(settings)
        cached = self.db.weather()
        refresh_minutes = max(15, _int(settings, "weather_refresh_minutes", 30))
        if cached and not force:
            fetched = _parse_datetime(cached.get("fetched_at"))
            if fetched and datetime.now(timezone.utc) - fetched.astimezone(timezone.utc) < timedelta(minutes=refresh_minutes):
                return self.weather_context(settings)

        params = {
            "latitude": _float(settings, "weather_latitude", 50.4501),
            "longitude": _float(settings, "weather_longitude", 30.5234),
            "current": ",".join((
                "temperature_2m", "apparent_temperature", "is_day",
                "precipitation", "rain", "snowfall", "weather_code",
                "cloud_cover", "wind_speed_10m",
            )),
            "hourly": "temperature_2m,precipitation_probability,weather_code",
            "daily": "sunrise,sunset",
            "forecast_days": 1,
            "timezone": settings.get("station_timezone", "Europe/Kyiv"),
        }
        url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                raw = json.load(response)
            current = raw.get("current", {})
            hourly = raw.get("hourly", {})
            current_time = current.get("time")
            times = hourly.get("time", [])
            at = times.index(current_time) if current_time in times else 0
            next_probabilities = hourly.get("precipitation_probability", [])[at:at + 3]
            temperatures = hourly.get("temperature_2m", [])[at:at + 4]
            payload = {
                "temperature": current.get("temperature_2m"),
                "feels_like": current.get("apparent_temperature"),
                "condition_code": current.get("weather_code"),
                "condition": WEATHER_LABELS.get(current.get("weather_code"), ""),
                "precipitation": current.get("precipitation", 0),
                "rain": current.get("rain", 0),
                "snowfall": current.get("snowfall", 0),
                "wind_speed": current.get("wind_speed_10m"),
                "cloud_cover": current.get("cloud_cover"),
                "is_day": bool(current.get("is_day", 1)),
                "rain_soon": any((value or 0) >= 50 for value in next_probabilities),
                "temperature_change_3h": (
                    round(temperatures[-1] - temperatures[0], 1)
                    if len(temperatures) >= 2 else 0
                ),
                "sunrise": (raw.get("daily", {}).get("sunrise") or [""])[0],
                "sunset": (raw.get("daily", {}).get("sunset") or [""])[0],
                "source": "open-meteo",
            }
            fetched_at = datetime.now(timezone.utc).isoformat()
            self.db.save_weather(
                json.dumps(payload, ensure_ascii=False), current_time, fetched_at
            )
            return {**payload, "available": True, "fetched_at": fetched_at}
        except Exception as exc:
            weather = self.weather_context(settings)
            weather["refresh_error"] = str(exc)
            return weather

    def weather_context(self, settings=None):
        settings = settings or self.db.settings()
        cached = self.db.weather()
        if not cached:
            return {"available": False}
        try:
            payload = json.loads(cached["payload_json"])
        except (TypeError, json.JSONDecodeError):
            return {"available": False}
        return {
            **payload,
            "available": True,
            "observed_at": cached.get("observed_at"),
            "fetched_at": cached.get("fetched_at"),
        }

    def snapshot(self, current_track, next_track, scheduled_for=None):
        settings = self.db.settings()
        time_context = self.time_context(scheduled_for, settings)
        clock = (
            self.pilot_clock.snapshot(
                time_context.iso,
                settings.get("responsible_editor", "").strip(),
            )
            if str(settings.get("pilot_clock_enabled", "1")) == "1"
            else {"enabled": False}
        )
        weather = self.refresh_weather(settings)
        if weather.get("available"):
            local_clock = time_context.iso[:16]
            sunrise = str(weather.get("sunrise") or "")[:16]
            sunset = str(weather.get("sunset") or "")[:16]
            if sunrise and local_clock < sunrise:
                weather["solar_phase"] = "before_sunrise"
            elif sunset and local_clock >= sunset:
                weather["solar_phase"] = "after_sunset"
            else:
                weather["solar_phase"] = "daylight"
        same_artist_recently = False
        next_artist = (next_track or {}).get("artist", "").casefold()
        if next_artist:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=60)
            for track in self.db.tracks():
                if track.get("artist", "").casefold() != next_artist or not track.get("last_played"):
                    continue
                played = _parse_datetime(track["last_played"])
                if played and played.astimezone(timezone.utc) >= cutoff:
                    same_artist_recently = True
                    break
        city = settings.get("station_city", "Київ")
        scheduled_moment = _parse_datetime(time_context.iso) or datetime.now(timezone.utc)
        minutes_on_air = max(
            0, int((scheduled_moment.astimezone(timezone.utc) - self.started_at).total_seconds() / 60)
        )
        recent_history = self.db.recent_history(12)
        return {
            "time": asdict(time_context),
            "clock": clock,
            "weather": weather,
            "station": {
                "name": settings.get("station_name", "LUMEN RADIO"),
                "city": city,
                "city_locative": CITY_LOCATIVE.get(city, f"місті {city}"),
            },
            "personality": {
                "persona": build_host_persona(settings),
                "language_style": settings.get("language_style", "casual_uk"),
                "colloquiality": _float(settings, "colloquiality", 0.30),
                "surzhyk": _float(settings, "surzhyk", 0.08),
                "slang": _float(settings, "slang", 0.15),
            },
            "current_track": current_track or {},
            "next_track": next_track or {},
            "music_transition": self._track_reaction(current_track, next_track),
            "session": {
                "minutes_on_air": minutes_on_air,
                "phase": self._session_phase(minutes_on_air),
                "recent_lines": [
                    item.get("display_text", "") for item in recent_history[:8]
                    if item.get("display_text")
                ],
                "recent_structures": [
                    item.get("structure") or item.get("content_type", "")
                    for item in recent_history[:8]
                ],
            },
            "same_artist_recently": same_artist_recently,
            "host_memory": self._memory_context(),
        }

    def _memory_context(self):
        memory = []
        for item in self.db.memory_items(12):
            try:
                value = json.loads(item["value_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                value = {}
            memory.append({
                "key": item["key"],
                "value": value,
                "last_used_at": item.get("last_used_at"),
                "cooldown_until": item.get("cooldown_until"),
            })
        return memory
