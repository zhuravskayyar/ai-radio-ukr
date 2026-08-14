import json


INTRO_TYPES = (
    "historical_fact",
    "strange_fact",
    "artist_story",
    "song_origin",
    "lyrics_meaning",
    "recording_story",
    "era_context",
    "music_fact",
    "mystery",
    "comparison",
    "listener_context",
)

PROFILE_DIMENSIONS = (
    "history",
    "artist_drama",
    "music_theory",
    "strange_facts",
    "nostalgia",
    "lyrics",
)

DEFAULT_LISTENER_PROFILE = {dimension: 0.50 for dimension in PROFILE_DIMENSIONS}

INTRO_TYPE_DIMENSIONS = {
    "historical_fact": "history",
    "strange_fact": "strange_facts",
    "artist_story": "artist_drama",
    "song_origin": "history",
    "lyrics_meaning": "lyrics",
    "recording_story": "music_theory",
    "era_context": "nostalgia",
    "music_fact": "music_theory",
    "mystery": "strange_facts",
    "comparison": "music_theory",
    "listener_context": "nostalgia",
}

STORY_CATEGORY_INTRO_TYPES = {
    "SONG_ORIGIN": "song_origin",
    "STUDIO_STORY": "recording_story",
    "BAND_ARGUMENT": "artist_story",
    "VOCALIST_STORY": "artist_story",
    "LYRICS_ORIGIN": "lyrics_meaning",
    "ACCIDENTAL_HIT": "strange_fact",
    "REJECTED_SONG": "mystery",
    "RECORDING_TRICK": "recording_story",
    "LIVE_STORY": "historical_fact",
    "NAME_STORY": "song_origin",
    "CHART_STORY": "historical_fact",
    "COLLABORATION": "artist_story",
    "BEFORE_FAME": "artist_story",
    "ALBUM_CONTEXT": "era_context",
    "FAN_STORY": "era_context",
}


def normalize_intro_type(value, fallback="music_fact"):
    candidate = str(value or "").strip().casefold()
    return candidate if candidate in INTRO_TYPES else fallback


def intro_type_for_story_category(category):
    return STORY_CATEGORY_INTRO_TYPES.get(
        str(category or "").strip().upper(),
        "music_fact",
    )


def normalize_listener_profile(value):
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            value = {}
    source = value if isinstance(value, dict) else {}
    profile = {}
    for dimension, default in DEFAULT_LISTENER_PROFILE.items():
        try:
            score = float(source.get(dimension, default))
        except (TypeError, ValueError):
            score = default
        profile[dimension] = round(max(0.10, min(0.90, score)), 4)
    return profile


class PersonalizationEngine:
    """Learns which sourced story angles hold this listener's attention."""

    def __init__(self, db):
        self.db = db

    def profile(self):
        settings = self.db.settings()
        return normalize_listener_profile(settings.get("listener_profile", "{}"))

    def preference(self, intro_type, profile=None):
        profile = profile or self.profile()
        dimension = INTRO_TYPE_DIMENSIONS.get(
            normalize_intro_type(intro_type),
            "music_theory",
        )
        return float(profile.get(dimension, DEFAULT_LISTENER_PROFILE[dimension]))

    def update(self, intro_type, action, completion_ratio=0.0):
        intro_type = normalize_intro_type(intro_type, fallback="")
        dimension = INTRO_TYPE_DIMENSIONS.get(intro_type)
        profile = self.profile()
        if not dimension:
            return profile

        try:
            ratio = max(0.0, min(1.0, float(completion_ratio or 0)))
        except (TypeError, ValueError):
            ratio = 0.0
        action = str(action or "").strip().casefold()
        current = profile[dimension]
        if action == "complete" or ratio >= 0.85:
            delta = 0.045 * (1.0 - current)
        elif action == "listened" and ratio >= 0.60:
            delta = 0.025 * (1.0 - current)
        elif action == "skip" and ratio < 0.35:
            delta = -0.075 * current
        else:
            delta = 0.0
        profile[dimension] = round(max(0.10, min(0.90, current + delta)), 4)
        self.db.save_settings({
            "listener_profile": json.dumps(profile, ensure_ascii=False),
        })
        return profile
