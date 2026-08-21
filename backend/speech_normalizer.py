import re
import unicodedata

from radio_pronunciation import DEFAULT_PRONUNCIATION_ENGINE, detect_script


# Compatibility mappings are views of the canonical exact dictionary. There
# are no pronunciation rules or curated names duplicated in this module.
PRONUNCIATIONS = DEFAULT_PRONUNCIATION_ENGINE.exact_mappings("artist")
TITLE_PRONUNCIATIONS = DEFAULT_PRONUNCIATION_ENGINE.exact_mappings("title")
SYMBOL_PRONUNCIATIONS = DEFAULT_PRONUNCIATION_ENGINE.exact_mappings("symbol")


def detect_text_language(value: str) -> str:
    language = detect_script(value or "")
    return "uk_or_ru" if language == "uk" else language


def phonetic_ukrainian(value: str) -> tuple[str, float, str]:
    """Return editable Ukrainian phonetic spelling for StyleTTS2."""
    result = DEFAULT_PRONUNCIATION_ENGINE.transcribe_with_meta(value)
    return result.spoken, result.confidence, detect_text_language(value)


def automatic_pronunciations(artist: str, title: str) -> dict:
    curated_artist, curated_title = suggested_pronunciations(artist, title)
    clean_title = _clean_track_title(title)
    artist_result = DEFAULT_PRONUNCIATION_ENGINE.transcribe_with_meta(
        artist, kind="artist"
    )
    title_result = DEFAULT_PRONUNCIATION_ENGINE.transcribe_with_meta(
        clean_title, kind="title"
    )
    return {
        "artist_speech": curated_artist or artist_result.spoken,
        "title_speech": curated_title or title_result.spoken,
        "artist_speech_confidence": 1.0 if curated_artist else artist_result.confidence,
        "title_speech_confidence": 1.0 if curated_title else title_result.confidence,
        "artist_language": detect_text_language(artist),
        "title_language": detect_text_language(clean_title),
        "pronunciation_source": "curated" if curated_artist or curated_title else "auto_local",
    }


_ONES = (
    "нуль", "один", "два", "три", "чотири", "п'ять", "шість", "сім",
    "вісім", "дев'ять", "десять", "одинадцять", "дванадцять",
    "тринадцять", "чотирнадцять", "п'ятнадцять", "шістнадцять",
    "сімнадцять", "вісімнадцять", "дев'ятнадцять",
)
_TENS = {
    20: "двадцять", 30: "тридцять", 40: "сорок", 50: "п'ятдесят",
    60: "шістдесят", 70: "сімдесят", 80: "вісімдесят", 90: "дев'яносто",
}
_HUNDREDS = {
    100: "сто", 200: "двісті", 300: "триста", 400: "чотириста",
    500: "п'ятсот", 600: "шістсот", 700: "сімсот", 800: "вісімсот",
    900: "дев'ятсот",
}
_ORDINAL_LOCATIVE = {
    1: "першому", 2: "другому", 3: "третьому", 4: "четвертому",
    5: "п'ятому", 6: "шостому", 7: "сьомому", 8: "восьмому",
    9: "дев'ятому", 10: "десятому", 11: "одинадцятому",
    12: "дванадцятому", 13: "тринадцятому", 14: "чотирнадцятому",
    15: "п'ятнадцятому", 16: "шістнадцятому", 17: "сімнадцятому",
    18: "вісімнадцятому", 19: "дев'ятнадцятому", 20: "двадцятому",
    30: "тридцятому", 40: "сороковому", 50: "п'ятдесятому",
    60: "шістдесятому", 70: "сімдесятому", 80: "вісімдесятому",
    90: "дев'яностому", 100: "сотому", 200: "двохсотому",
    300: "трьохсотому", 400: "чотирьохсотому", 500: "п'ятисотому",
    600: "шестисотому", 700: "семисотому", 800: "восьмисотому",
    900: "дев'ятисотому",
}
_ORDINAL_NEUTER = {
    1: "перше", 2: "друге", 3: "третє", 4: "четверте", 5: "п'яте",
    6: "шосте", 7: "сьоме", 8: "восьме", 9: "дев'яте", 10: "десяте",
    11: "одинадцяте", 12: "дванадцяте", 13: "тринадцяте",
    14: "чотирнадцяте", 15: "п'ятнадцяте", 16: "шістнадцяте",
    17: "сімнадцяте", 18: "вісімнадцяте", 19: "дев'ятнадцяте",
    20: "двадцяте", 30: "тридцяте", 40: "сорокове", 50: "п'ятдесяте",
    60: "шістдесяте", 70: "сімдесяте", 80: "вісімдесяте",
    90: "дев'яносте", 100: "соте", 200: "двохсоте", 300: "трьохсоте",
    400: "чотирьохсоте", 500: "п'ятисоте", 600: "шестисоте",
    700: "семисоте", 800: "восьмисоте", 900: "дев'ятисоте",
}
_ORDINAL_FEMININE = {
    1: "перша", 2: "друга", 3: "третя", 4: "четверта", 5: "п'ята",
    6: "шоста", 7: "сьома", 8: "восьма", 9: "дев'ята", 10: "десята",
    11: "одинадцята", 12: "дванадцята", 13: "тринадцята",
    14: "чотирнадцята", 15: "п'ятнадцята", 16: "шістнадцята",
    17: "сімнадцята", 18: "вісімнадцята", 19: "дев'ятнадцята",
    20: "двадцята", 30: "тридцята", 40: "сорокова", 50: "п'ятдесята",
    60: "шістдесята", 70: "сімдесята", 80: "вісімдесята",
    90: "дев'яноста", 100: "сота", 200: "двохсота", 300: "трьохсота",
    400: "чотирьохсота", 500: "п'ятисота", 600: "шестисота",
    700: "семисота", 800: "восьмисота", 900: "дев'ятисота",
}
_ORDINAL_FEMININE_LOCATIVE = {
    1: "першій", 2: "другій", 3: "третій", 4: "четвертій",
    5: "п'ятій", 6: "шостій", 7: "сьомій", 8: "восьмій",
    9: "дев'ятій", 10: "десятій", 11: "одинадцятій",
    12: "дванадцятій", 13: "тринадцятій", 14: "чотирнадцятій",
    15: "п'ятнадцятій", 16: "шістнадцятій", 17: "сімнадцятій",
    18: "вісімнадцятій", 19: "дев'ятнадцятій", 20: "двадцятій",
    30: "тридцятій", 40: "сороковій", 50: "п'ятдесятій",
    60: "шістдесятій", 70: "сімдесятій", 80: "вісімдесятій",
    90: "дев'яностій", 100: "сотій", 200: "двохсотій",
    300: "трьохсотій", 400: "чотирьохсотій", 500: "п'ятисотій",
    600: "шестисотій", 700: "семисотій", 800: "восьмисотій",
    900: "дев'ятисотій",
}
_ORDINAL_MASCULINE = {
    1: "перший", 2: "другий", 3: "третій", 4: "четвертий", 5: "п'ятий",
    6: "шостий", 7: "сьомий", 8: "восьмий", 9: "дев'ятий", 10: "десятий",
    11: "одинадцятий", 12: "дванадцятий", 13: "тринадцятий",
    14: "чотирнадцятий", 15: "п'ятнадцятий", 16: "шістнадцятий",
    17: "сімнадцятий", 18: "вісімнадцятий", 19: "дев'ятнадцятий",
    20: "двадцятий", 30: "тридцятий", 40: "сороковий", 50: "п'ятдесятий",
    60: "шістдесятий", 70: "сімдесятий", 80: "вісімдесятий",
    90: "дев'яностий", 100: "сотий", 200: "двохсотий",
    300: "трьохсотий", 400: "чотирьохсотий", 500: "п'ятисотий",
    600: "шестисотий", 700: "семисотий", 800: "восьмисотий",
    900: "дев'ятисотий",
}


def number_to_words(value: int) -> str:
    """Return a TTS-friendly Ukrainian cardinal for non-negative integers."""
    value = int(value)
    if value < 0:
        return "мінус " + number_to_words(-value)
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens, remainder = divmod(value, 10)
        return " ".join(part for part in (_TENS[tens * 10], number_to_words(remainder) if remainder else "") if part)
    if value < 1000:
        hundreds, remainder = divmod(value, 100)
        return " ".join(part for part in (_HUNDREDS[hundreds * 100], number_to_words(remainder) if remainder else "") if part)
    if value < 1_000_000:
        thousands, remainder = divmod(value, 1000)
        if thousands == 1:
            prefix = "одна тисяча"
        elif thousands == 2:
            prefix = "дві тисячі"
        else:
            ending = "тисячі" if thousands % 10 in (2, 3, 4) and thousands % 100 not in (12, 13, 14) else "тисяч"
            prefix = f"{number_to_words(thousands)} {ending}"
        return " ".join(part for part in (prefix, number_to_words(remainder) if remainder else "") if part)
    return str(value)


def _compound_ordinal(value: int, forms: dict[int, str]) -> str:
    if value in forms:
        return forms[value]
    if 20 < value < 100:
        tens, remainder = divmod(value, 10)
        return f"{_TENS[tens * 10]} {forms[remainder]}"
    if 100 < value < 1000:
        hundreds, remainder = divmod(value, 100)
        if remainder:
            return f"{_HUNDREDS[hundreds * 100]} {_compound_ordinal(remainder, forms)}"
    return number_to_words(value)


def ordinal_locative(value: int) -> str:
    return _large_ordinal(int(value), _ORDINAL_LOCATIVE)


def ordinal_neuter(value: int) -> str:
    return _large_ordinal(int(value), _ORDINAL_NEUTER)


def ordinal_feminine(value: int) -> str:
    return _large_ordinal(int(value), _ORDINAL_FEMININE)


def ordinal_feminine_locative(value: int) -> str:
    return _large_ordinal(int(value), _ORDINAL_FEMININE_LOCATIVE)


def ordinal_masculine(value: int) -> str:
    return _large_ordinal(int(value), _ORDINAL_MASCULINE)


def _large_ordinal(value: int, forms: dict[int, str]) -> str:
    if value < 1000:
        return _compound_ordinal(value, forms)
    thousands, remainder = divmod(value, 1000)
    if remainder:
        return f"{number_to_words(thousands * 1000)} {_compound_ordinal(remainder, forms)}"
    return number_to_words(value)


def rank_in_place(rank: int) -> str:
    return f"на {ordinal_locative(rank)} місці"


def suggested_pronunciations(artist: str, title: str) -> tuple[str, str]:
    artist_speech = DEFAULT_PRONUNCIATION_ENGINE.exact_lookup(
        (artist or "").strip(), kind="artist"
    ) or ""
    clean_title = (title or "").strip()
    title_speech = DEFAULT_PRONUNCIATION_ENGINE.exact_lookup(
        clean_title, kind="title"
    ) or ""
    if not title_speech:
        for known, spoken in TITLE_PRONUNCIATIONS.items():
            if clean_title.casefold().startswith(known.casefold()):
                title_speech = spoken
                break
    return artist_speech, title_speech


def audit_ai_pronunciation(original: str, candidate: str, kind: str) -> str:
    """Apply deterministic pronunciation rules after a probabilistic AI pass."""
    original = (original or "").strip()
    candidate = unicodedata.normalize("NFC", (candidate or "").strip())
    candidate = candidate.translate(str.maketrans({"’": "'", "`": "'", "´": "'"}))
    candidate = "".join(
        char for char in unicodedata.normalize("NFD", candidate)
        if char != "\u0301"
    )
    candidate = unicodedata.normalize("NFC", re.sub(r"\s+", " ", candidate))
    curated_artist, curated_title = suggested_pronunciations(
        original if kind == "artist" else "",
        original if kind == "title" else "",
    )
    curated = curated_artist if kind == "artist" else curated_title
    if curated:
        return curated

    # English "the" changes before a vowel sound. The AI may flatten both
    # forms to one spelling, so enforce the predictable initial case here.
    match = re.match(r"(?i)^the\s+([A-Za-z]+)", original)
    if match and candidate:
        next_word = match.group(1).casefold()
        article = "ді" if next_word[:1] in "aeiou" else "зе"
        candidate = re.sub(r"^\S+", article, candidate, count=1)
    if re.match(r"(?i)^i(?:\s|$)", original) and candidate:
        candidate = re.sub(r"^\S+", "ай", candidate, count=1)
    return candidate


def validate_phonetic_spelling(original: str, candidate: str, kind: str) -> str:
    """Return a safe Ukrainian spelling for TTS or raise ValueError.

    A neural model may choose pronunciation, but its result must remain a plain
    Ukrainian phonetic spelling rather than IPA, transliteration, or commentary.
    """
    candidate = audit_ai_pronunciation(original, candidate, kind)
    if not candidate:
        raise ValueError("порожня транскрипція")
    if re.search(r"[A-Za-zЁёЫыЭэЪъ0-9]", candidate):
        raise ValueError("транскрипція містить неукраїнські літери або цифри")
    if not re.fullmatch(r"[А-Яа-яІіЇїЄєҐґ'’\-.,:;!?()\s]+", candidate):
        raise ValueError("транскрипція містить IPA або сторонні символи")
    if not re.search(r"[А-Яа-яІіЇїЄєҐґ]", candidate):
        raise ValueError("транскрипція не містить українських літер")
    if len(candidate) > max(80 if kind == "artist" else 120, len(original) * 3):
        raise ValueError("надто довга транскрипція")
    return candidate


def compact_artist_credit(artist: str) -> str:
    """Keep unusually long collaboration credits speakable on air."""
    parts = [part.strip() for part in re.split(r"\s*,\s*", artist or "") if part.strip()]
    unique = []
    seen = set()
    for part in parts:
        key = part.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(part)
    if len(parts) != len(unique) or len(unique) >= 4:
        unique = unique[:2]
    return ", ".join(unique) if unique else (artist or "").strip()


def _replace_case_insensitive(text: str, original: str, spoken: str) -> str:
    if not original or not spoken or original == spoken:
        return text
    return re.sub(re.escape(original), lambda _match: spoken, text, flags=re.IGNORECASE)


def polish_ukrainian_grammar(text: str) -> str:
    """Fix common broadcast case/government errors without changing clock digits."""
    speech = unicodedata.normalize("NFC", (text or "").strip())
    speech = re.sub(
        r"\bна\s+(\d{1,3})(?:-?(?:ій|й))?\s+(позиції)\b",
        lambda match: (
            f"{'На' if match.group(0)[0].isupper() else 'на'} "
            f"{ordinal_feminine_locative(int(match.group(1)))} {match.group(2)}"
        ),
        speech,
        flags=re.IGNORECASE,
    )
    grammar_replacements = (
        (r"\bу ефірі\b", "в ефірі"),
        (r"\bпо рейтингу\b", "у рейтингу"),
        (r"\bслідуюч(?:ий|а|е)\b", "наступний"),
        (r"\bприймати участь\b", "брати участь"),
        (r"\bна протязі\b", "протягом"),
    )
    for pattern, replacement in grammar_replacements:
        speech = re.sub(pattern, replacement, speech, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", speech).strip()


def normalize_linguistic(text: str) -> str:
    """Normalize grammar and numbers while preserving original proper names."""
    speech = polish_ukrainian_grammar(text)
    # Deal with grammatical rank forms before the generic number pass.
    speech = re.sub(
        r"\bна\s+(\d{1,3})(?:-?(?:му|ому|ьому))?\s+(місці)\b",
        lambda match: f"на {ordinal_locative(int(match.group(1)))} {match.group(2)}",
        speech,
        flags=re.IGNORECASE,
    )
    speech = re.sub(
        r"\b(\d{1,3})(?:-?(?:те|е))\s+(місце)\b",
        lambda match: f"{ordinal_neuter(int(match.group(1)))} {match.group(2)}",
        speech,
        flags=re.IGNORECASE,
    )
    speech = re.sub(
        r"\b(\d{1,3})(?:-?(?:ша|га|тя|а))\s+(позиція)\b",
        lambda match: f"{ordinal_feminine(int(match.group(1)))} {match.group(2)}",
        speech,
        flags=re.IGNORECASE,
    )
    speech = re.sub(
        r"\bпозиція\s+(\d{1,3})\b",
        lambda match: f"{ordinal_feminine(int(match.group(1)))} позиція",
        speech,
        flags=re.IGNORECASE,
    )
    speech = re.sub(
        r"\bмісце\s+(\d{1,3})\b",
        lambda match: f"{ordinal_neuter(int(match.group(1)))} місце",
        speech,
        flags=re.IGNORECASE,
    )
    speech = re.sub(
        r"\b(\d{1,2}):(\d{2})\b",
        lambda match: _spoken_time(int(match.group(1)), int(match.group(2))),
        speech,
    )
    speech = re.sub(
        r"\b(\d{4})\s+(рік|року|році)\b",
        _spoken_year,
        speech,
        flags=re.IGNORECASE,
    )
    speech = re.sub(
        r"(?<!\w)([+-])\s*(\d+(?:[.,]\d+)?)\s*°(?:[CcСс])?",
        _spoken_temperature,
        speech,
    )
    speech = re.sub(r"#\s*(\d+)", lambda match: f"номер {number_to_words(int(match.group(1)))}", speech)
    speech = re.sub(r"\b\d+\b", lambda match: number_to_words(int(match.group(0))), speech)
    speech = re.sub(r"\b(?:feat|ft)\.\s*", "за участю ", speech, flags=re.IGNORECASE)
    speech = re.sub(r"\bvs\.\s*", "проти ", speech, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", speech).strip()


def _spoken_time(hour: int, minute: int) -> str:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Некоректний час: {hour:02d}:{minute:02d}")
    if hour == 0 and minute == 0:
        return "рівно опівночі"
    spoken_hour = "нульова" if hour == 0 else ordinal_feminine(hour)
    if minute == 0:
        return f"{spoken_hour} рівно"
    spoken_minute = number_to_words(minute)
    if minute < 10:
        spoken_minute = "нуль " + spoken_minute
    return f"{spoken_hour} {spoken_minute}"


def _spoken_year(match) -> str:
    value = int(match.group(1))
    form = match.group(2).casefold()
    if form == "рік":
        return f"{ordinal_masculine(value)} рік"
    locative = ordinal_locative(value)
    if form == "році":
        return f"{locative} році"
    words = locative.split()
    if words[-1].endswith("ьому"):
        words[-1] = words[-1][:-4] + "ього"
    elif words[-1].endswith("ому"):
        words[-1] = words[-1][:-3] + "ого"
    return f"{' '.join(words)} року"


def _spoken_temperature(match) -> str:
    sign = "плюс" if match.group(1) == "+" else "мінус"
    raw = match.group(2).replace(",", ".")
    value = float(raw)
    whole = int(value)
    if value != whole:
        spoken = f"{number_to_words(whole)} і {number_to_words(round((value - whole) * 10))} десятих"
    else:
        spoken = number_to_words(whole)
    last = whole % 10
    ending = "градус" if last == 1 and whole % 100 != 11 else "градуси" if last in (2, 3, 4) and whole % 100 not in (12, 13, 14) else "градусів"
    return f"{sign} {spoken} {ending}"


def normalize_for_speech(text: str, tracks=()) -> str:
    """Build a stable Edge/Piper-friendly copy without changing display text."""
    # Keep the product name pronounceable even in legacy/cached copy.  The
    # English spelling otherwise reaches some engines as separate letters.
    speech = re.sub(
        r"\b(?:LUMEN|Люмен)\s+(?:RADIO|Радіо)\b",
        "Вектор Радіо",
        text,
        flags=re.IGNORECASE,
    )
    speech = re.sub(
        r"\bVector\s+Radio\b",
        "Вектор Радіо",
        speech,
        flags=re.IGNORECASE,
    )
    speech = normalize_linguistic(speech)
    mappings = {}
    for track in tracks or ():
        artist = str(track.get("artist", "")).strip()
        title = str(track.get("title", "")).strip()
        artist_speech = str(track.get("artist_speech", "")).strip()
        title_speech = str(track.get("title_speech", "")).strip()
        compact_artist = compact_artist_credit(artist)
        # If the title contains noisy suffixes (official video, HD, lyrics, etc.)
        # prepare a cleaned short title to use only for speech replacement.
        clean_title = _clean_track_title(title)
        if artist_speech:
            mappings[artist.casefold()] = (artist, artist_speech)
            if compact_artist != artist:
                mappings[compact_artist.casefold()] = (
                    compact_artist,
                    compact_artist_credit(artist_speech),
                )
        if title_speech:
            mappings[title.casefold()] = (title, title_speech)
        # If cleaned title differs, prefer mapping noisy full title -> cleaned spoken
        if clean_title and clean_title != title:
            # If there is a verified spoken title, keep it; otherwise use the cleaned
            # title as the spoken replacement so TTS doesn't read extraneous tags.
            spoken_for_title = title_speech or clean_title
            mappings[title.casefold()] = (title, spoken_for_title)
    for original, spoken in sorted(
        mappings.values(), key=lambda item: len(item[0]), reverse=True
    ):
        speech = _replace_case_insensitive(speech, original, spoken)
    speech = DEFAULT_PRONUNCIATION_ENGINE.transcribe(speech)
    speech = speech.replace("&", " і ")
    speech = re.sub(r"\b(?:feat(?:uring)?|ft)\.?", "за участю", speech, flags=re.IGNORECASE)
    speech = re.sub(r"[!?]+", "", speech)
    speech = speech.translate(str.maketrans({"«": "", "»": "", "“": "", "”": "", '"': ""}))
    speech = re.sub(r"\s*[—–]\s*", ", ", speech)
    speech = re.sub(r"\s*:\s*", ", ", speech)
    # Edge and Piper disagree about explicit stress marks. Remove only the
    # combining acute accent; keeping every other mark is essential for й/ї.
    speech = "".join(
        char for char in unicodedata.normalize("NFD", speech)
        if char != "\u0301"
    )
    speech = unicodedata.normalize("NFC", speech)
    speech = re.sub(r"\s+([,.;!?])", r"\1", speech)
    speech = re.sub(r",(?:\s*,)+", ",", speech)
    speech = re.sub(r"\s+", " ", speech).strip()
    return speech


def _clean_track_title(title: str) -> str:
    """Return a shorter, speech-friendly title by removing common noisy suffixes.

    Examples removed: (Official Video), [Official Audio], - Official Video, HD, Remastered, Lyrics, 2020 Remaster
    This is conservative: it only strips obvious non-title metadata and bracketed extras.
    """
    if not title:
        return title
    t = title
    # Remove common bracketed extras: ( ... ) or [ ... ]
    t = re.sub(r"\s*[\(\[][^\)\]]{1,120}[\)\]]\s*$", "", t).strip()
    # Remove trailing descriptors after a dash or em-dash
    t = re.sub(r"\s*[–—-]\s*(?:official|official video|official audio|audio|video|hd|hd video|lyrics|remaster|remastered|version|live|promo|clip)\b.*$", "", t, flags=re.IGNORECASE)
    # Remove common keywords anywhere at end
    t = re.sub(r"\b(?:official video|official audio|official|video|audio|lyrics|remaster|remastered|hd|live|clip)\b\s*$", "", t, flags=re.IGNORECASE)
    # Trim stray punctuation and whitespace
    t = re.sub(r"[\s:,-]+$", "", t).strip()
    return t
