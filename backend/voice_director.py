import re
from dataclasses import asdict, dataclass

from .speech_normalizer import normalize_for_speech, normalize_linguistic


@dataclass
class VoiceProfile:
    voice: str
    rate: str
    pitch: str
    language_style: str
    colloquiality: float
    surzhyk: float
    slang: float
    target_seconds: float
    target_words_min: int
    target_words_max: int

    def to_dict(self):
        return asdict(self)


class VoiceDirector:
    """Turns final copy into linguistic and TTS layers with a stable voice profile."""

    def __init__(self, default_voice="uk-UA-OstapNeural"):
        self.default_voice = default_voice

    def profile(self, context, next_track, target_seconds, content_type=""):
        daypart = context.get("time", {}).get("daypart", "day")
        energy = float((next_track or {}).get("energy") or 5)
        rate = {"morning": 2, "day": 0, "drive": 3, "evening": -2, "night": -5}.get(daypart, 0)
        if energy >= 8:
            rate += 2
        elif energy <= 3:
            rate -= 2
        rate = max(-7, min(5, rate))
        personality = context.get("personality", {})
        is_story = content_type == "story"
        seconds = (
            max(20.0, min(40.0, float(target_seconds or 22)))
            if is_story else max(2.5, min(10.0, float(target_seconds or 10)))
        )
        # Spoken stories need room for a narrative; other links stay compact.
        target = seconds * (2.15 + rate / 100)
        target_words_min = 35 if is_story else max(5, round(target * 0.85))
        target_words_max = 55 if is_story else max(7, round(target * 1.10))
        return VoiceProfile(
            voice=self.default_voice,
            rate=f"{rate:+d}%",
            pitch="-2Hz",
            language_style=personality.get("language_style", "casual_uk"),
            colloquiality=float(personality.get("colloquiality", 0.30)),
            surzhyk=min(0.08, float(personality.get("surzhyk", 0.08))),
            slang=float(personality.get("slang", 0.15)),
            target_seconds=seconds,
            target_words_min=target_words_min,
            target_words_max=target_words_max,
        )

    def prompt_directive(self, profile):
        styles = {
            "standard": "літературна природна українська без розмовних вставок",
            "casual_uk": "жива розмовна українська без граматичних помилок",
            "local_uk": "локальна розмовна українська; суржик лише зрідка й без карикатури",
        }
        return (
            f"VOICE_STYLE: {styles.get(profile.language_style, styles['casual_uk'])}. "
            f"COLLOQUIALITY={profile.colloquiality:.2f}; "
            f"SURZHYK не більше {profile.surzhyk:.2f}; SLANG={profile.slang:.2f}. "
            f"Ціль: {profile.target_words_min}–{profile.target_words_max} слів і "
            f"приблизно {profile.target_seconds:.1f} секунди. Не вставляй «ну» чи "
            "«коротше» автоматично: людська недосконалість має бути рідкісною."
        )

    def _punctuation_director(self, text):
        sentences = []
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            words = sentence.split()
            if len(words) > 24 and ", " in sentence:
                left, right = sentence.split(", ", 1)
                if len(left.split()) >= 7 and len(right.split()) >= 5:
                    sentence = left.rstrip(" ,") + ". " + right[:1].upper() + right[1:]
            sentences.append(sentence)
        directed = " ".join(sentences)
        directed = re.sub(r"\s*;\s*", ". ", directed)
        directed = re.sub(r"\.{2,}", ".", directed)
        return re.sub(r"\s+", " ", directed).strip()

    def direct(self, qwen_text, tracks, context, next_track, target_seconds, content_type=""):
        profile = self.profile(context, next_track, target_seconds, content_type)
        linguistic = normalize_linguistic(qwen_text)
        tts_text = normalize_for_speech(linguistic, tracks)
        tts_text = self._punctuation_director(tts_text)
        return {
            "qwen_text": qwen_text,
            "linguistic_text": linguistic,
            "tts_text": tts_text,
            "profile": profile.to_dict(),
            "needs_pronunciation_review": bool((next_track or {}).get("pronunciation_review")),
        }
