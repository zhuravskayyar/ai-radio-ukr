from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

try:
    import pronouncing
except ImportError:
    pronouncing = None

DEFAULT_PHRASE_OVERRIDES = {
    # Артисти
    "the weeknd": "Зе Вікенд",
    "imagine dragons": "Імеджин Дреґонс",
    "linkin park": "Лінкін Парк",
    "lady gaga": "Лейді Ґаґа",
    "lana del rey": "Лана Дел Рей",
    "billie eilish": "Біллі Айліш",
    "arctic monkeys": "Арктік Манкіз",
    "twenty one pilots": "Твенті Ван Пайлотс",
    "red hot chili peppers": "Ред Хот Чілі Пепперз",
    "bring me the horizon": "Брінґ Мі Зе Горайзон",
    "system of a down": "Сістем Ов Е Даун",
    "three days grace": "Срі Дейз Ґрейс",
    "fall out boy": "Фол Аут Бой",
    "my chemical romance": "Май Кемікал Роменс",
    "avril lavigne": "Евріл Лавін",
    "rihanna": "Ріанна",
    "beyonce": "Біонсе",
    "beyoncé": "Біонсе",
    "sia": "Сіа",
    "adele": "Адель",
    "eminem": "Емінем",
    "50 cent": "Фіфті Сент",
    "jay-z": "Джей Зі",
    "jay z": "Джей Зі",
    "ac/dc": "Ей Сі Ді Сі",
    "abba": "АББА",
    "u2": "Ю Ту",
    "maroon 5": "Марун Файв",
    "blink-182": "Блінк Ван Ейті Ту",
    "би-2": "Бі два",

    # Часті назви
    "blinding lights": "Блайндінґ Лайтс",
    "shape of you": "Шейп Ов Ю",
    "bad guy": "Бед Ґай",
    "believer": "Беливер",
    "numb": "Нам",
    "in the end": "Ін Зі Енд",
    "bring me to life": "Брінґ Мі Ту Лайф",
    "wake me up": "Вейк Мі Ап",
    "somebody that i used to know": "Самбаді Зет Ай Юзд Ту Ноу",
}

WORD_OVERRIDES = {
    "the": "зе",
    "a": "е",
    "an": "ен",
    "of": "ов",
    "and": "енд",
    "to": "ту",
    "you": "ю",
    "your": "йор",
    "my": "май",
    "me": "мі",
    "i": "ай",
    "we": "ві",
    "are": "ар",
    "is": "із",
    "love": "лав",
    "live": "лів",
    "life": "лайф",
    "night": "найт",
    "light": "лайт",
    "lights": "лайтс",
    "heart": "харт",
    "fire": "файр",
    "girl": "ґьорл",
    "girls": "ґьорлз",
    "boy": "бой",
    "boys": "бойз",
    "baby": "бейбі",
    "dance": "денс",
    "dream": "дрім",
    "dreams": "дрімз",
    "world": "ворлд",
    "never": "невер",
    "forever": "форевер",
    "without": "відаут",
    "beautiful": "б’ютіфул",
    "crazy": "крейзі",
    "radio": "рейдіоу",
    "feat": "фіт",
    "featuring": "фічерінґ",
    "remix": "рі́мікс",
    "version": "ве́ршн",
    "official": "офі́шл",
}

ABBREVIATIONS = {
    "DJ": "ді джей",
    "MC": "ем сі",
    "TV": "ті ві",
    "FM": "еф ем",
    "AM": "ей ем",
    "USA": "ю ес ей",
    "UK": "ю кей",
    "US": "ю ес",
    "EP": "і пі",
    "LP": "ел пі",
    "VIP": "ві ай пі",
    "R&B": "ар енд бі",
    "OST": "оу ес ті",
    "BTS": "бі ті ес",
}

NUMBER_WORDS = {
    "0": "зеро",
    "1": "ван",
    "2": "ту",
    "3": "срі",
    "4": "фор",
    "5": "файв",
    "6": "сікс",
    "7": "севен",
    "8": "ейт",
    "9": "найн",
    "10": "тен",
    "11": "ілевен",
    "12": "твелв",
    "13": "серті́н",
    "14": "форті́н",
    "15": "фіфті́н",
    "16": "сіксті́н",
    "17": "севенті́н",
    "18": "ейті́н",
    "19": "найнті́н",
    "20": "твенті",
    "21": "твенті ван",
    "22": "твенті ту",
    "24": "твенті фор",
    "30": "серті",
    "40": "фо́рті",
    "50": "фі́фті",
    "60": "сі́ксті",
    "70": "се́венті",
    "80": "е́йті",
    "90": "на́йнті",
    "100": "ван гандред",
    "101": "ван оу ван",
    "182": "ван е́йті ту",
    "1975": "найнті́н севенті файв",
    "2000": "ту саузенд",
    "2001": "ту саузенд ван",
}

ARPABET_MAP = {
    "AA": "а",
    "AE": "е",
    "AH": "а",
    "AO": "о",
    "AW": "ау",
    "AY": "ай",
    "B": "б",
    "CH": "ч",
    "D": "д",
    "DH": "з",
    "EH": "е",
    "ER": "ер",
    "EY": "ей",
    "F": "ф",
    "G": "ґ",
    "HH": "х",
    "IH": "і",
    "IY": "і",
    "JH": "дж",
    "K": "к",
    "L": "л",
    "M": "м",
    "N": "н",
    "NG": "нґ",
    "OW": "оу",
    "OY": "ой",
    "P": "п",
    "R": "р",
    "S": "с",
    "SH": "ш",
    "T": "т",
    "TH": "с",
    "UH": "у",
    "UW": "у",
    "V": "в",
    "W": "в",
    "Y": "й",
    "Z": "з",
    "ZH": "ж",
}

UKRAINIAN_CHARS = set("іїєґІЇЄҐ")
RUSSIAN_CHARS = set("ыэъёЫЭЪЁ")


def normalize_spaces(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.:;!?])", r"\1", text)
    text = re.sub(r"([\(\[]) ", r"\1", text)
    text = re.sub(r" ([\)\]])", r"\1", text)
    return text.strip()


def normalize_quotes(text: str) -> str:
    return (
        text.replace("“", '"')
        .replace("”", '"')
        .replace("„", '"')
        .replace("«", '"')
        .replace("»", '"')
        .replace("’", "'")
        .replace("`", "'")
    )


def detect_script(text: str) -> str:
    has_latin = bool(re.search(r"[A-Za-z]", text))
    has_cyrillic = bool(re.search(r"[А-Яа-яЁёІіЇїЄєҐґ]", text))

    if has_latin and has_cyrillic:
        return "mixed"
    if has_latin:
        return "en"
    if has_cyrillic:
        if any(char in text for char in UKRAINIAN_CHARS):
            return "uk"
        if any(char in text for char in RUSSIAN_CHARS):
            return "ru"
        if re.search(
            r"\b(?:никто|ничто|пишет|полковнику|что|это|его|тебя|меня|себя|мой|твой)\b",
            text.casefold(),
        ):
            return "ru"
        return "uk"
    return "unknown"


def preserve_case(source: str, result: str) -> str:
    if not result:
        return result
    if source.isupper() and len(source) > 1:
        return result.upper()
    if source[:1].isupper():
        return result[:1].upper() + result[1:]
    return result


def strip_arpabet_stress(phoneme: str) -> str:
    return re.sub(r"\d", "", phoneme)


def russian_to_ukrainian_tts(text: str) -> str:
    word_replacements = [
        ("полковнику", "полковніку"),
        ("никто", "нікто"),
        ("ничто", "нічто"),
    ]
    replacements = [
        ("сч", "щ"),
        ("зч", "щ"),
        ("тся", "ца"),
        ("ться", "ца"),
        ("ого", "ово"),
        ("его", "єво"),
        ("ё", "йо"),
        ("э", "е"),
        ("ы", "и"),
        ("ъ", ""),
    ]

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
        r"(?i)\bе",
        lambda match: preserve_case(match.group(0), "є"),
        result,
    )
    return normalize_spaces(result)


def arpabet_to_ukrainian(phones: str) -> str:
    result_parts: list[str] = []
    for raw_phoneme in phones.split():
        phoneme = strip_arpabet_stress(raw_phoneme)
        mapped = ARPABET_MAP.get(phoneme)
        if mapped:
            result_parts.append(mapped)
    result = "".join(result_parts)
    result = re.sub(r"іі+", "і", result)
    result = re.sub(r"аа+", "а", result)
    result = re.sub(r"оуоу", "оу", result)
    return result


def fallback_english_transliteration(word: str) -> str:
    lower = word.lower()
    rules = [
        ("tion", "шн"),
        ("sion", "жн"),
        ("ture", "чер"),
        ("ough", "оу"),
        ("eigh", "ей"),
        ("igh", "ай"),
        ("air", "ер"),
        ("ear", "ір"),
        ("eer", "ір"),
        ("ph", "ф"),
        ("sh", "ш"),
        ("ch", "ч"),
        ("tch", "ч"),
        ("th", "с"),
        ("wh", "в"),
        ("ck", "к"),
        ("qu", "кв"),
        ("ng", "нґ"),
        ("ee", "і"),
        ("ea", "і"),
        ("oo", "у"),
        ("ou", "ау"),
        ("ow", "оу"),
        ("oa", "оу"),
        ("ai", "ей"),
        ("ay", "ей"),
        ("oi", "ой"),
        ("oy", "ой"),
        ("au", "о"),
        ("aw", "о"),
    ]
    result = lower
    for source, target in rules:
        result = result.replace(source, target)

    char_map = {
        "a": "а",
        "b": "б",
        "c": "к",
        "d": "д",
        "e": "е",
        "f": "ф",
        "g": "ґ",
        "h": "х",
        "i": "і",
        "j": "дж",
        "k": "к",
        "l": "л",
        "m": "м",
        "n": "н",
        "o": "о",
        "p": "п",
        "q": "к",
        "r": "р",
        "s": "с",
        "t": "т",
        "u": "у",
        "v": "в",
        "w": "в",
        "x": "кс",
        "y": "і",
        "z": "з",
        "'": "’",
        "-": "-",
    }
    output: list[str] = []
    for char in result:
        if re.match(r"[А-Яа-яІіЇїЄєҐґ]", char):
            output.append(char)
        else:
            output.append(char_map.get(char, char))
    transliterated = "".join(output)
    if lower.endswith("e") and len(lower) > 3:
        transliterated = re.sub(r"е$", "", transliterated)
    return preserve_case(word, transliterated)


def english_word_to_ukrainian(word: str) -> str:
    clean_word = word.strip()
    lower = clean_word.lower()
    if not clean_word:
        return clean_word
    if lower in WORD_OVERRIDES:
        return preserve_case(clean_word, WORD_OVERRIDES[lower])
    if clean_word.upper() in ABBREVIATIONS:
        return ABBREVIATIONS[clean_word.upper()]
    if clean_word.isdigit():
        return NUMBER_WORDS.get(clean_word, " ".join(
            NUMBER_WORDS.get(char, char) for char in clean_word
        ))
    if "-" in clean_word:
        return "-".join(
            english_word_to_ukrainian(part)
            for part in clean_word.split("-")
        )
    dictionary_word = re.sub(r"[^A-Za-z']", "", clean_word)
    if pronouncing is not None and dictionary_word:
        pronunciations = pronouncing.phones_for_word(dictionary_word.lower())
        if pronunciations:
            result = arpabet_to_ukrainian(pronunciations[0])
            return preserve_case(clean_word, result)
    return fallback_english_transliteration(clean_word)


def english_text_to_ukrainian(text: str) -> str:
    token_pattern = re.compile(
        r"[A-Za-z]+(?:['-][A-Za-z]+)*|\d+|&|[^A-Za-z\d&]+"
    )
    tokens = token_pattern.findall(text)
    output: list[str] = []
    for token in tokens:
        if re.fullmatch(r"[A-Za-z]+(?:['-][A-Za-z]+)*", token):
            output.append(english_word_to_ukrainian(token))
        elif token == "&":
            output.append(" енд ")
        elif token.isdigit():
            output.append(NUMBER_WORDS.get(
                token,
                " ".join(NUMBER_WORDS.get(char, char) for char in token),
            ))
        else:
            output.append(token)
    return normalize_spaces("".join(output))


def replace_phrase_overrides(
    text: str,
    overrides: dict[str, str],
) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}
    result = text
    sorted_overrides = sorted(
        overrides.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for index, (phrase, pronunciation) in enumerate(sorted_overrides):
        # Keep protected phrases out of transliteration. ASCII/digit markers
        # are unsafe here because the converter treats them as pronounceable
        # English words and numbers before restore_phrase_overrides() runs.
        marker = f"\ue000{chr(0xE100 + index)}\ue001"
        pattern = re.compile(
            rf"(?<![A-Za-zА-Яа-яІіЇїЄєҐґ])"
            rf"{re.escape(phrase)}"
            rf"(?![A-Za-zА-Яа-яІіЇїЄєҐґ])",
            flags=re.IGNORECASE,
        )
        if pattern.search(result):
            result = pattern.sub(marker, result)
            protected[marker] = pronunciation
    return result, protected


def restore_phrase_overrides(
    text: str,
    protected: dict[str, str],
) -> str:
    result = text
    for marker, pronunciation in protected.items():
        result = result.replace(marker, pronunciation)
    return result


def process_mixed_text(text: str) -> str:
    pattern = re.compile(
        r"[A-Za-z]+(?:['-][A-Za-z]+)*"
        r"|\d+"
        r"|[А-Яа-яЁёІіЇїЄєҐґ]+(?:[-'][А-Яа-яЁёІіЇїЄєҐґ]+)*"
        r"|[^A-Za-zА-Яа-яЁёІіЇїЄєҐґ\d]+"
    )
    tokens = pattern.findall(text)
    output: list[str] = []
    for token in tokens:
        language = detect_script(token)
        if language == "en":
            output.append(english_text_to_ukrainian(token))
        elif language == "ru":
            output.append(russian_to_ukrainian_tts(token))
        else:
            output.append(token)
    return normalize_spaces("".join(output))


@dataclass
class RadioPronunciation:
    phrase_overrides: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_PHRASE_OVERRIDES)
    )
    pronounce_parentheses: bool = True
    remove_file_extensions: bool = True
    remove_track_number: bool = True

    def add_override(self, original: str, pronunciation: str) -> None:
        self.phrase_overrides[original.lower().strip()] = pronunciation.strip()

    def clean_metadata(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        text = normalize_quotes(text)
        if self.remove_file_extensions:
            text = re.sub(
                r"\.(mp3|flac|wav|ogg|m4a|aac|opus)$",
                "",
                text,
                flags=re.IGNORECASE,
            )
        if self.remove_track_number:
            text = re.sub(
                r"^\s*\d{1,3}\s*[-._]\s*",
                "",
                text,
            )
        text = re.sub(
            r"\b(?:official\s+video|official\s+audio|lyrics?\s+video)\b",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\[(?:official|audio|video|lyrics?|hd|hq|4k)[^\]]*\]",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\b(?:feat\.?|ft\.?|featuring)\b",
            " feat ",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\s*[–—]\s*", " — ", text)
        return normalize_spaces(text)

    def convert(self, text: str) -> str:
        if not isinstance(text, str):
            raise TypeError("text має бути рядком")
        text = self.clean_metadata(text)
        if not text:
            return ""
        protected_text, protected = replace_phrase_overrides(
            text,
            self.phrase_overrides,
        )
        language = detect_script(protected_text)
        if language == "en":
            result = english_text_to_ukrainian(protected_text)
        elif language == "ru":
            result = russian_to_ukrainian_tts(protected_text)
        elif language == "mixed":
            result = process_mixed_text(protected_text)
        else:
            result = protected_text
        result = restore_phrase_overrides(result, protected)
        result = re.sub(
            r"\bfeat\b",
            "фіт",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"\bremix\b",
            "рімікс",
            result,
            flags=re.IGNORECASE,
        )
        return normalize_spaces(result)

    def convert_track(
        self,
        artist: str,
        title: str,
        template: str = "{artist}. Пісня {title}.",
    ) -> str:
        converted_artist = self.convert(artist)
        converted_title = self.convert(title)
        return template.format(
            artist=converted_artist,
            title=converted_title,
        )


def main() -> None:
    converter = RadioPronunciation()
    examples = [
        "The Weeknd — Blinding Lights",
        "Imagine Dragons — Believer",
        "Linkin Park — In the End",
        "Billie Eilish — Bad Guy",
        "AC/DC — Highway to Hell",
        "Maroon 5 — Memories",
        "Би-2 — Полковнику никто не пишет",
        "Кино — Группа крови",
        "Океан Ельзи — Без бою",
        "Антитіла feat. Ed Sheeran — 2step",
    ]
    for example in examples:
        print(f"Оригінал: {example}")
        print(f"Для TTS: {converter.convert(example)}")
        print("-" * 60)
    print("\nІнтерактивний режим. Для виходу введіть exit.")
    while True:
        try:
            text = input("\nНазва треку: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.lower() in {"exit", "quit", "вихід"}:
            break
        print("Вимова:", converter.convert(text))


if __name__ == "__main__":
    main()
