from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

try:
    import cmudict
except ImportError:  # Keep the radio usable in a partially installed environment.
    cmudict = None

try:
    import pronouncing
except ImportError:  # The pattern fallback remains available during recovery.
    pronouncing = None


DEFAULT_DICTIONARY_PATH = (
    Path(__file__).resolve().parent / "data" / "tts_pronunciations.json"
)

# This is the only ARPABET mapping used by the application. Exact voice-tuned
# spellings in the JSON dictionary deliberately take priority over this map.
ARPABET_TO_UKRAINIAN = {
    "AA": "а", "AE": "е", "AH": "е", "AO": "о", "AW": "ау",
    "AY": "ай", "B": "б", "CH": "ч", "D": "д", "DH": "з",
    "EH": "е", "ER": "ер", "EY": "ей", "F": "ф", "G": "ґ",
    "HH": "х", "IH": "і", "IY": "і", "JH": "дж", "K": "к",
    "L": "л", "M": "м", "N": "н", "NG": "нґ", "OW": "оу",
    "OY": "ой", "P": "п", "R": "р", "S": "с", "SH": "ш",
    "T": "т", "TH": "с", "UH": "у", "UW": "у", "V": "в",
    "W": "в", "Y": "й", "Z": "з", "ZH": "ж",
}

ENGLISH_PATTERNS = (
    ("tion", "шн"), ("sion", "жн"), ("ture", "чер"),
    ("eigh", "ей"), ("ough", "оу"), ("igh", "ай"),
    ("tch", "ч"), ("sh", "ш"), ("ch", "ч"), ("ph", "ф"),
    ("th", "с"), ("wh", "в"), ("ck", "к"), ("qu", "кв"),
    ("ng", "нґ"), ("ee", "і"), ("ea", "і"), ("oo", "у"),
    ("ai", "ей"), ("ay", "ей"), ("oa", "оу"), ("ou", "ау"),
    ("ow", "оу"), ("oi", "ой"), ("oy", "ой"), ("au", "о"),
    ("aw", "о"),
)

_ENGLISH_CHARACTERS = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е",
    "f": "ф", "g": "ґ", "h": "х", "i": "і", "j": "дж",
    "k": "к", "l": "л", "m": "м", "n": "н", "o": "о",
    "p": "п", "q": "к", "r": "р", "s": "с", "t": "т",
    "u": "у", "v": "в", "w": "в", "x": "кс", "y": "і",
    "z": "з", "'": "’", "’": "’", "-": "-",
}

_LETTER_NAMES = {
    "a": "ей", "b": "бі", "c": "сі", "d": "ді", "e": "і",
    "f": "еф", "g": "джі", "h": "ейч", "i": "ай", "j": "джей",
    "k": "кей", "l": "ел", "m": "ем", "n": "ен", "o": "оу",
    "p": "пі", "q": "к'ю", "r": "ар", "s": "ес", "t": "ті",
    "u": "ю", "v": "ві", "w": "дабл-ю", "x": "екс",
    "y": "вай", "z": "зі",
}

_ENGLISH_NUMBERS = {
    "0": "зеро", "1": "ван", "2": "ту", "3": "срі", "4": "фор",
    "5": "файв", "6": "сікс", "7": "севен", "8": "ейт",
    "9": "найн", "10": "тен", "11": "ілевен", "12": "твелв",
    "13": "сертін", "14": "фортін", "15": "фіфтін",
    "16": "сікстін", "17": "севентін", "18": "ейтін",
    "19": "найнтін", "20": "твенті", "21": "твенті ван",
    "22": "твенті ту", "24": "твенті фор", "30": "серті",
    "40": "форті", "50": "фіфті", "60": "сіксті",
    "70": "севенті", "80": "ейті", "90": "найнті",
    "100": "ван гандред", "101": "ван оу ван",
    "182": "ван ейті ту", "1975": "найнтін севенті файв",
    "2000": "ту саузенд", "2001": "ту саузенд ван",
}

_ABBREVIATIONS = {
    "DJ": "ді джей", "MC": "ем сі", "TV": "ті ві", "FM": "еф ем",
    "AM": "ей ем", "USA": "ю ес ей", "UK": "ю кей", "US": "ю ес",
    "EP": "і пі", "LP": "ел пі", "VIP": "ві ай пі",
    "R&B": "ар енд бі", "OST": "оу ес ті", "BTS": "бі ті ес",
}

# Voice adaptations are applied only after a successful CMUdict lookup. They
# smooth the few regular ARPABET spellings that StyleTTS2 reads unnaturally.
_CMU_VOICE_ADAPTATIONS = {
    "you": "ю", "love": "лав", "fire": "файр", "world": "ворлд",
    "beautiful": "б’ютіфул", "radio": "рейдіо",
}

# These values only keep recovery mode intelligible when dependencies are
# missing. A complete installation reaches CMUdict before consulting them.
_MISSING_CMU_DEFAULTS = {
    "the": "зе", "a": "ей", "an": "ен", "of": "ов", "and": "енд",
    "to": "ту", "you": "ю", "your": "йор", "my": "май", "me": "мі",
    "i": "ай", "we": "ві", "are": "ар", "is": "із", "love": "лав",
    "life": "лайф", "night": "найт", "light": "лайт",
    "lights": "лайтс", "heart": "харт", "fire": "файр",
    "dream": "дрім", "dreams": "дрімз", "world": "ворлд",
    "beautiful": "б’ютіфул", "radio": "рейдіо",
}

_UKRAINIAN_CHARS = set("іїєґІЇЄҐ")
_RUSSIAN_CHARS = set("ыэъёЫЭЪЁ")
_RUSSIAN_HINTS = re.compile(
    r"\b(?:никто|ничто|пишет|полковнику|что|это|его|тебя|меня|себя|мой|твой)\b",
    flags=re.IGNORECASE,
)


def normalize_spaces(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.:;!?])", r"\1", text)
    text = re.sub(r"([\(\[]) ", r"\1", text)
    text = re.sub(r" ([\)\]])", r"\1", text)
    return text.strip()


def normalize_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.translate(str.maketrans({"’": "'", "`": "'", "–": "-", "—": "-"}))
    return normalize_spaces(value).casefold()


def normalize_quotes(text: str) -> str:
    return text.translate(str.maketrans({
        "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
        "’": "'", "`": "'",
    }))


def preserve_case(source: str, result: str) -> str:
    if not result:
        return result
    if source.isupper() and len(source) > 1:
        return result.upper()
    if source[:1].isupper():
        return result[:1].upper() + result[1:]
    return result


def detect_script(text: str) -> str:
    has_latin = bool(re.search(r"[A-Za-z]", text or ""))
    has_cyrillic = bool(re.search(r"[А-Яа-яЁёІіЇїЄєҐґ]", text or ""))
    if has_latin and has_cyrillic:
        return "mixed"
    if has_latin:
        return "en"
    if has_cyrillic:
        if any(char in text for char in _UKRAINIAN_CHARS):
            return "uk"
        if any(char in text for char in _RUSSIAN_CHARS) or _RUSSIAN_HINTS.search(text):
            return "ru"
        return "uk"
    return "other"


def strip_arpabet_stress(phoneme: str) -> str:
    return re.sub(r"\d", "", phoneme)


def arpabet_to_ukrainian(phones: str | Iterable[str]) -> str:
    raw_phones = phones.split() if isinstance(phones, str) else phones
    result = "".join(
        ARPABET_TO_UKRAINIAN.get(strip_arpabet_stress(raw), "")
        for raw in raw_phones
    )
    result = re.sub(r"іі+", "і", result)
    result = re.sub(r"аа+", "а", result)
    return result.replace("оуоу", "оу")


@lru_cache(maxsize=8192)
def cmudict_lookup(word: str) -> Optional[str]:
    """Return the first local CMU pronunciation without doing network I/O."""
    normalized = re.sub(r"[^A-Za-z']", "", word or "").casefold()
    if not normalized:
        return None
    if pronouncing is not None:
        pronunciations = pronouncing.phones_for_word(normalized)
        if pronunciations:
            return pronunciations[0]
    if cmudict is not None:
        pronunciations = cmudict.dict().get(normalized, ())
        if pronunciations:
            first = pronunciations[0]
            return " ".join(first) if not isinstance(first, str) else first
    return None


def fallback_english_transliteration(word: str) -> str:
    lower = (word or "").casefold()
    output: list[str] = []
    index = 0
    while index < len(lower):
        matched = False
        for source, target in ENGLISH_PATTERNS:
            if lower.startswith(source, index):
                output.append(target)
                index += len(source)
                matched = True
                break
        if matched:
            continue
        output.append(_ENGLISH_CHARACTERS.get(lower[index], lower[index]))
        index += 1
    result = "".join(output)
    if lower.endswith("e") and len(lower) > 3:
        result = re.sub(r"е$", "", result)
    return preserve_case(word, result)


def russian_to_ukrainian_tts(text: str) -> str:
    # Avoid a global Russian г -> ґ substitution: it sounds unnatural in the
    # configured StyleTTS2 voice. Proper names belong in the exact dictionary.
    word_replacements = (
        ("полковнику", "полковніку"), ("никто", "нікто"),
        ("ничто", "нічто"),
    )
    replacements = (
        ("сч", "щ"), ("зч", "щ"), ("ться", "ца"), ("тся", "ца"),
        ("ого", "ово"), ("его", "єво"), ("ё", "йо"), ("ы", "и"),
        ("э", "е"), ("ъ", ""),
    )
    result = text
    for old, new in word_replacements:
        result = re.sub(
            rf"\b{old}\b",
            lambda match: preserve_case(match.group(0), new),
            result,
            flags=re.IGNORECASE,
        )
    for old, new in replacements:
        result = re.sub(old, new, result, flags=re.IGNORECASE)
    result = re.sub(
        r"(?i)\bе", lambda match: preserve_case(match.group(0), "є"), result
    )
    return normalize_spaces(result)


@dataclass(frozen=True)
class DictionaryEntry:
    original: str
    spoken: str
    kind: str = "artist"
    language: str = ""
    variants: Mapping[str, str] | None = None
    verified: bool = True


@dataclass(frozen=True)
class PronunciationResult:
    spoken: str
    confidence: float
    language: str
    source: str


class PronunciationEngine:
    """Canonical exact -> CMUdict -> pattern pronunciation pipeline."""

    def __init__(
        self,
        dictionary_path: Path | str | None = DEFAULT_DICTIONARY_PATH,
        entries: Iterable[Mapping[str, object]] | None = None,
        cmu_lookup: Callable[[str], Optional[str]] | None = None,
    ) -> None:
        self.dictionary_path = Path(dictionary_path) if dictionary_path else None
        self._cmu_lookup = cmu_lookup or cmudict_lookup
        self._entries: list[DictionaryEntry] = []
        self._by_key: dict[str, list[DictionaryEntry]] = {}
        if entries is None:
            entries = self._load_entries()
        for raw in entries:
            self._add_mapping(raw)

    def _load_entries(self) -> list[Mapping[str, object]]:
        if self.dictionary_path is None:
            return []
        try:
            payload = json.loads(self.dictionary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        raw_entries = payload.get("entries", []) if isinstance(payload, dict) else []
        if isinstance(raw_entries, dict):
            return [
                {"original": original, **details}
                for original, details in raw_entries.items()
                if isinstance(details, dict)
            ]
        return [entry for entry in raw_entries if isinstance(entry, dict)]

    def _add_mapping(self, raw: Mapping[str, object]) -> None:
        original = str(raw.get("original") or "").strip()
        spoken = str(raw.get("spoken") or raw.get("tts") or "").strip()
        if not original or not spoken:
            return
        variants = raw.get("variants")
        clean_variants = {
            str(key): str(value).strip()
            for key, value in variants.items()
            if str(value).strip()
        } if isinstance(variants, Mapping) else None
        entry = DictionaryEntry(
            original=original,
            spoken=spoken,
            kind=str(raw.get("kind") or (
                "symbol" if raw.get("language") == "symbol" else "artist"
            )),
            language=str(raw.get("language") or ""),
            variants=clean_variants,
            verified=bool(raw.get("verified", True)),
        )
        self._entries.append(entry)
        self._by_key.setdefault(normalize_key(original), []).append(entry)

    def add_override(
        self,
        original: str,
        spoken: str,
        *,
        kind: str = "word",
        language: str = "",
        variants: Mapping[str, str] | None = None,
    ) -> None:
        """Add a verified in-memory override (persistent approval lives in SQLite)."""
        self._add_mapping({
            "original": original,
            "spoken": spoken,
            "kind": kind,
            "language": language,
            "variants": variants or {},
            "verified": True,
        })

    @property
    def entries(self) -> tuple[DictionaryEntry, ...]:
        return tuple(self._entries)

    def exact_mappings(self, kind: str) -> dict[str, str]:
        return {
            entry.original: entry.spoken
            for entry in self._entries
            if entry.kind == kind
        }

    def exact_lookup(
        self,
        value: str,
        kind: str | None = None,
        context: str | None = None,
    ) -> Optional[str]:
        candidates = self._by_key.get(normalize_key(value), ())
        if kind:
            candidates = sorted(
                candidates,
                key=lambda entry: entry.kind == kind,
                reverse=True,
            )
            candidates = [
                entry for entry in candidates if entry.kind in {kind, "word", "symbol"}
            ]
        for entry in candidates:
            if entry.variants:
                variant = entry.variants.get(context or "")
                if variant is None:
                    variant = entry.variants.get("default")
                if variant:
                    return variant
            return entry.spoken
        return None

    def _phrase_entries(self, kind: str | None) -> list[DictionaryEntry]:
        allowed = {kind, "word", "symbol"} if kind else None
        entries = [
            entry for entry in self._entries
            if allowed is None or entry.kind in allowed
        ]
        return sorted(entries, key=lambda entry: len(entry.original), reverse=True)

    @staticmethod
    def _entry_pattern(original: str) -> re.Pattern[str]:
        pieces = [re.escape(piece) for piece in re.split(r"\s+", original.strip())]
        phrase = r"\s+".join(pieces)
        return re.compile(rf"(?<!\w){phrase}(?!\w)", flags=re.IGNORECASE)

    def _protect_exact(
        self,
        text: str,
        kind: str | None,
        context: str | None,
    ) -> tuple[str, dict[str, str], bool]:
        protected: dict[str, str] = {}
        result = text
        for index, entry in enumerate(self._phrase_entries(kind)):
            pattern = self._entry_pattern(entry.original)
            if not pattern.search(result):
                continue
            marker = f"\ue000{chr(0xE100 + index)}\ue001"
            spoken = (
                (entry.variants or {}).get(context or "")
                or (entry.variants or {}).get("default")
                or entry.spoken
            )
            result = pattern.sub(marker, result)
            protected[marker] = spoken
        return result, protected, bool(protected)

    def _english_word(self, word: str) -> tuple[str, float, str]:
        lower = word.casefold()
        upper = word.upper()
        if upper in _ABBREVIATIONS:
            return _ABBREVIATIONS[upper], 0.96, "abbreviation"
        letters_only = re.sub(r"[^A-Za-z]", "", word)
        if word.isupper() and 1 < len(letters_only) <= 5:
            return "-".join(_LETTER_NAMES[char.casefold()] for char in letters_only), 0.9, "abbreviation"
        phones = self._cmu_lookup(word)
        if phones:
            spoken = _CMU_VOICE_ADAPTATIONS.get(lower)
            if spoken is None:
                spoken = arpabet_to_ukrainian(phones)
            if spoken:
                # CMUdict is a strong automatic suggestion, not a voice-tuned
                # approval. Keeping it below the review threshold lets the
                # existing UI play it back and persist a user's correction.
                return preserve_case(word, spoken), 0.88, "cmudict"
        if lower in _MISSING_CMU_DEFAULTS:
            return (
                preserve_case(word, _MISSING_CMU_DEFAULTS[lower]),
                0.55,
                "pattern",
            )
        return fallback_english_transliteration(word), 0.55, "pattern"

    def transcribe_with_meta(
        self,
        value: str,
        kind: str | None = None,
        context: str | None = None,
    ) -> PronunciationResult:
        value = unicodedata.normalize("NFKC", (value or "").strip())
        language = detect_script(value)
        if not value:
            return PronunciationResult("", 1.0, language, "unchanged")
        exact = self.exact_lookup(value, kind=kind, context=context)
        if exact is not None:
            return PronunciationResult(exact, 1.0, language, "exact")

        protected_text, protected, used_exact = self._protect_exact(
            value, kind, context
        )
        cyrillic_language = detect_script(
            re.sub(r"[A-Za-z]+", "", protected_text)
        )
        token_pattern = re.compile(
            r"[A-Za-z]+(?:['’\-][A-Za-z]+)*|\d+|[^A-Za-z\d]+"
        )
        tokens = token_pattern.findall(protected_text)
        output: list[str] = []
        confidence = 1.0
        sources: set[str] = {"exact"} if used_exact else set()
        for token in tokens:
            if re.fullmatch(r"[A-Za-z]+(?:['’\-][A-Za-z]+)*", token):
                spoken, token_confidence, source = self._english_word(token)
                output.append(spoken)
                confidence = min(confidence, token_confidence)
                sources.add(source)
            elif token.isdigit():
                output.append(_ENGLISH_NUMBERS.get(
                    token,
                    " ".join(_ENGLISH_NUMBERS.get(char, char) for char in token),
                ))
                confidence = min(confidence, 0.65)
                sources.add("number")
            elif cyrillic_language == "ru" and re.search(
                r"[А-Яа-яЁёЫыЭэЪъ]", token
            ):
                output.append(russian_to_ukrainian_tts(token))
                confidence = min(confidence, 0.82)
                sources.add("russian")
            else:
                output.append(token)

        spoken = "".join(output)
        for marker, replacement in protected.items():
            spoken = spoken.replace(marker, replacement)
        source = "+".join(sorted(sources)) if sources else "unchanged"
        return PronunciationResult(
            normalize_spaces(spoken), confidence, language, source
        )

    def transcribe(
        self,
        value: str,
        kind: str | None = None,
        context: str | None = None,
    ) -> str:
        return self.transcribe_with_meta(value, kind=kind, context=context).spoken


DEFAULT_PRONUNCIATION_ENGINE = PronunciationEngine()


# Backward-compatible function API; every route delegates to the same engine.
def english_word_to_ukrainian(word: str) -> str:
    return DEFAULT_PRONUNCIATION_ENGINE._english_word(word)[0]


def english_text_to_ukrainian(text: str) -> str:
    return DEFAULT_PRONUNCIATION_ENGINE.transcribe(text)


def process_mixed_text(text: str) -> str:
    return DEFAULT_PRONUNCIATION_ENGINE.transcribe(text)


class RadioPronunciation:
    def __init__(
        self,
        phrase_overrides: Mapping[str, str] | None = None,
        pronounce_parentheses: bool = True,
        remove_file_extensions: bool = True,
        remove_track_number: bool = True,
        engine: PronunciationEngine | None = None,
    ) -> None:
        self.engine = engine or PronunciationEngine()
        self.pronounce_parentheses = pronounce_parentheses
        self.remove_file_extensions = remove_file_extensions
        self.remove_track_number = remove_track_number
        for original, spoken in (phrase_overrides or {}).items():
            self.engine.add_override(original, spoken)

    @property
    def phrase_overrides(self) -> dict[str, str]:
        return {entry.original.casefold(): entry.spoken for entry in self.engine.entries}

    def add_override(
        self,
        original: str,
        pronunciation: str,
        kind: str = "word",
    ) -> None:
        self.engine.add_override(original, pronunciation, kind=kind)

    def clean_metadata(self, text: str) -> str:
        text = normalize_quotes(unicodedata.normalize("NFKC", text))
        if self.remove_file_extensions:
            text = re.sub(
                r"\.(mp3|flac|wav|ogg|m4a|aac|opus)$", "", text,
                flags=re.IGNORECASE,
            )
        if self.remove_track_number:
            text = re.sub(r"^\s*\d{1,3}\s*[-._]\s*", "", text)
        text = re.sub(
            r"\b(?:official\s+video|official\s+audio|lyrics?\s+video)\b",
            "", text, flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\[(?:official|audio|video|lyrics?|hd|hq|4k)[^]]*]", "", text,
            flags=re.IGNORECASE,
        )
        return normalize_spaces(re.sub(r"\s*[–—]\s*", " — ", text))

    def convert(
        self,
        text: str,
        kind: str | None = None,
        context: str | None = None,
    ) -> str:
        if not isinstance(text, str):
            raise TypeError("text має бути рядком")
        return self.engine.transcribe(
            self.clean_metadata(text), kind=kind, context=context
        )

    def convert_track(
        self,
        artist: str,
        title: str,
        template: str = "{artist}. Пісня {title}.",
    ) -> str:
        return template.format(
            artist=self.convert(artist, kind="artist"),
            title=self.convert(title, kind="title"),
        )


def main() -> None:
    converter = RadioPronunciation()
    examples = (
        "Ocean", "Би-2", "Twenty One Pilots", "The Ocean",
        "Ocean Eyes", "U2 — One", "Imagine Dragons — Believer",
    )
    for example in examples:
        print(f"{example} -> {converter.convert(example)}")


if __name__ == "__main__":
    main()
