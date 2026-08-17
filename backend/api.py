import asyncio
import ast
import base64
import hashlib
import html
import json
import logging
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import DEFAULTS, Database
from .content_planner import ContentPlanner, LINERS, first_phrase
from .context_engine import ContextEngine
from .demo_chart import DEMO_CHART
from .matcher import match_score
from .parser import parse_chart
from .speech_normalizer import (
    audit_ai_pronunciation,
    compact_artist_credit,
    detect_text_language,
    normalize_for_speech,
    normalize_linguistic,
    polish_ukrainian_grammar,
    validate_phonetic_spelling,
)
from .transition_director import TransitionDirector
from .voice_director import VoiceDirector
from .host_brain import HostBrain
from .listener_personalization import INTRO_TYPES, normalize_intro_type
from .radio_queue import RadioQueueManager
from .broadcast_safety import BroadcastSafety
from .updater import APP_VERSION, UpdateManager


DEFAULT_TTS_VOICE = "uk-UA-OstapNeural"
LOGGER = logging.getLogger(__name__)
DEFAULT_AI_MAX_TOKENS = int(DEFAULTS["ai_max_tokens"])
# Bump this whenever the editorial contract for generated host copy changes.
# RadioAPI will then discard text and prepared transitions from older prompts.
HOST_PROMPT_VERSION = "2026-08-14-fact-editor-personalization-v1"

# Canonical genre families used to validate the model's structured genre tag.
# The station prompt remains free-form; only explicit genre words participate
# in this gate, while era, language and mood stay in the AI selection prompt.
GENRE_ALIASES = {
    "alternative_rock": ("alternative rock", "alt rock", "альтернативний рок", "альт рок"),
    "pop_rock": ("pop rock", "pop-rock", "поп рок", "поп-рок"),
    "emo_rock": ("emo rock", "emo-rock", "емо рок", "емо-рок"),
    "pop_punk": ("pop punk", "pop-punk", "поп панк", "поп-панк"),
    "indie_rock": ("indie rock", "indie-rock", "інді рок", "інді-рок"),
    "melodic_rock": ("melodic rock", "melodic guitar rock", "мелодійний рок"),
    "festival_rock": ("festival rock", "festival-rock", "фестивальний рок"),
    "art_rock": ("art rock", "art-rock", "арт рок", "арт-рок"),
    "dream_pop": ("dream pop", "dream-pop", "дрім поп", "дрим поп"),
    "post_punk": ("post punk", "post-punk", "постпанк", "пост панк"),
    "darkwave": ("darkwave", "dark wave", "дарквейв", "дарк вейв"),
    "new_wave": ("new wave", "нова хвиля", "новая волна"),
    "drum_and_bass": ("drum and bass", "drum & bass", "dnb", "драм енд бейс"),
    "hip_hop": ("hip hop", "hip-hop", "хіп хоп", "хип хоп", "реп", "rap"),
    "rnb": ("r&b", "rnb", "rhythm and blues", "ритм енд блюз"),
    "shoegaze": ("shoegaze", "шугейз"),
    "chanson": ("russian chanson", "chanson", "шансон"),
    "electronic": ("electronic", "electronica", "електроніка", "электроника"),
    "classical": ("classical", "класична музика", "классическая музыка"),
    "reggae": ("reggae", "регі", "регги"),
    "country": ("country", "кантрі", "кантри"),
    "techno": ("techno", "техно"),
    "house": ("house", "хаус"),
    "metal": ("metal", "метал"),
    "punk": ("punk", "панк"),
    "indie": ("indie", "інді", "инди"),
    "rock": ("rock", "рок"),
    "pop": ("pop", "поп", "попса", "попси"),
    "jazz": ("jazz", "джаз"),
    "blues": ("blues", "блюз"),
    "folk": ("folk", "фолк"),
    "soul": ("soul", "соул"),
    "disco": ("disco", "диско"),
    "funk": ("funk", "фанк"),
}

GENRE_PARENTS = {
    "alternative_rock": {"rock"},
    "pop_rock": {"pop", "rock"},
    "emo_rock": {"rock"},
    "pop_punk": {"pop", "punk", "rock"},
    "indie_rock": {"indie", "rock"},
    "melodic_rock": {"rock"},
    "festival_rock": {"rock"},
    "art_rock": {"rock"},
    "post_punk": {"punk", "rock"},
    "dream_pop": {"pop"},
    "darkwave": {"electronic"},
    "new_wave": {"rock", "electronic"},
}

GENERIC_GENRES = {
    "rock", "pop", "electronic", "metal", "punk", "indie", "folk",
}


ROMANTIC_EVENING_MIN_SCORE = 72.0
ROMANTIC_EVENING_SEED_TRACKS = (
    {"artist": "Animal ДжаZ", "title": "Три полоски", "year": 2007},
    {"artist": "Валентин Стрыкало", "title": "Наше лето"},
    {"artist": "Валентин Стрыкало", "title": "Кладбище самолётов"},
    {"artist": "Нервы", "title": "Кофе мой друг", "year": 2012},
    {"artist": "Нервы", "title": "Слишком влюблён"},
    {"artist": "Бумбокс", "title": "Вахтерам", "year": 2006},
    {"artist": "Бумбокс", "title": "Та4то", "year": 2007},
    {"artist": "Бумбокс", "title": "Квіти в волоссі"},
    {"artist": "Фіолет", "title": "Кохана"},
    {"artist": "Фіолет", "title": "Романтика"},
)
ROMANTIC_EVENING_BLOCKED_ARTISTS = (
    "Земфира", "Земфіра", "ДДТ", "Сплин", "Би-2", "Бі-2", "Кино",
    "Кіно", "Виктор Цой", "Віктор Цой", "Аквариум", "Акваріум",
    "Чайф", "Машина времени", "Машина часу", "Алиса", "Аліса",
    "Крематорий", "Крематорій", "Наутилус Помпилиус",
)
ROMANTIC_EVENING_GENRES = {
    "alternative_rock", "pop_rock", "emo_rock", "pop_punk",
    "indie_rock", "melodic_rock", "festival_rock",
}
ROMANTIC_EVENING_MOOD_WORDS = {
    "romantic", "romance", "nostalgic", "nostalgia", "warm", "bittersweet",
    "youthful", "evening", "intimate", "longing", "tender", "love",
    "романтичний", "романтична", "романтика", "ностальгія", "теплий",
    "тепла", "вечірній", "вечір", "ніжний", "ніжність", "кохання",
    "молодіжний", "молодість", "меланхолійний", "романтичный", "романтика",
    "ностальгия", "тёплый", "теплый", "вечерний", "вечер", "нежный",
    "нежность", "любовь", "молодёжный", "молодежный", "меланхоличный",
}
ROMANTIC_EVENING_FORBIDDEN_GENRE_PHRASES = (
    "classic russian rock", "soviet rock", "post soviet rock", "bard rock",
    "chanson", "estrada", "traditional rock", "post punk", "darkwave",
    "art rock", "singer songwriter", "heavy metal", "metalcore", "hardcore",
    "rap only", "dance pop only", "slow acoustic", "классический русский рок",
    "советский рок", "бард рок", "шансон", "эстрада", "пост панк",
    "дарквейв", "арт рок", "тяжёлый метал", "тяжелый метал", "металкор",
    "хардкор", "акустическая баллада", "класичний російський рок",
    "радянський рок", "бард рок", "естрада", "постпанк", "арт рок",
    "важкий метал", "акустична балада",
)


def _normalized_ai_max_tokens(settings=None):
    """Return the bounded standard completion-token cap for AI requests."""
    try:
        value = int((settings or {}).get("ai_max_tokens", DEFAULT_AI_MAX_TOKENS))
    except (TypeError, ValueError):
        value = DEFAULT_AI_MAX_TOKENS
    return max(96, min(DEFAULT_AI_MAX_TOKENS, value))


def _openrouter_affordable_tokens(message):
    """Return the completion-token cap included in an OpenRouter credit error."""
    match = re.search(
        r"can only afford\s+(\d+)(?:\s+tokens)?",
        str(message or ""),
        re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


def _chat_completion(spec, system_prompt, request_text, temperature, top_p, max_tokens):
    """Call one OpenAI-compatible chat endpoint without leaking its key."""
    is_openrouter = "openrouter.ai" in str(spec.get("url") or "")
    openrouter_reasoning_required = False
    for attempt in range(2):
        payload = {
            "model": spec["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request_text},
            ],
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if spec.get("provider_type", spec.get("name")) == "nvidia":
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if is_openrouter:
            payload["reasoning"] = {
                "enabled": openrouter_reasoning_required,
                "exclude": True,
            }
        request = urllib.request.Request(
            spec["url"],
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {spec['key']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=float(spec.get("timeout_seconds") or 25),
            ) as response:
                payload = json.load(response)
                choice = (payload.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                content = message.get("content")
                if isinstance(content, list):
                    content = " ".join(
                        str(part.get("text") or part.get("content") or part)
                        for part in content
                    )
                if content is None:
                    content = choice.get("text") or ""
                candidate = str(content or "").strip()
                if not candidate:
                    return {
                        "provider": spec["name"],
                        "candidate": "",
                        "error": f"{spec['name']}: empty content in chat response",
                        "error_kind": "invalid_response",
                        "status_code": 200,
                    }
            return {
                "provider": spec["name"], "candidate": candidate, "error": "",
                "error_kind": "", "status_code": 200,
            }
        except urllib.error.HTTPError as exc:
            details = ""
            try:
                details = exc.read().decode("utf-8", errors="replace")[:1500]
                decoded = json.loads(details)
                error_payload = decoded.get("error")
                if isinstance(error_payload, dict):
                    message = (
                        error_payload.get("message")
                        or error_payload.get("code")
                        or error_payload.get("type")
                        or ""
                    )
                else:
                    message = str(error_payload or "")
                message = decoded.get("detail") or decoded.get("message") or message
            except Exception:
                message = ""
            affordable_tokens = _openrouter_affordable_tokens(message or details)
            if (
                attempt == 0
                and is_openrouter
                and exc.code == 400
                and "reasoning is mandatory" in str(message or details).casefold()
            ):
                openrouter_reasoning_required = True
                continue
            if (
                attempt == 0
                and is_openrouter
                and exc.code == 402
                and affordable_tokens
                and affordable_tokens < max_tokens
            ):
                max_tokens = affordable_tokens
                continue
            if not message and details:
                message = details[:300]
            suffix = f": {message}" if message else ""
            return {
                "provider": spec["name"],
                "candidate": "",
                "error": f"{spec['name']} HTTP {exc.code}{suffix}",
                "error_kind": (
                    "auth" if exc.code in {401, 403}
                    else "credit" if exc.code == 402
                    else "rate_limit" if exc.code == 429
                    else "server" if exc.code >= 500
                    else "request"
                ),
                "status_code": int(exc.code),
            }
        except Exception as exc:
            detail = str(exc)
            error_kind = (
                "timeout" if "timed out" in detail.casefold()
                else "network"
            )
            return {
                "provider": spec["name"],
                "candidate": "",
                "error": f"{spec['name']}: {detail}",
                "error_kind": error_kind,
                "status_code": 0,
            }


def _json_object(text):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text or "", flags=re.IGNORECASE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI не повернув JSON")
    return json.loads(cleaned[start:end + 1])


def _music_plan_object(text):
    """Recover a music plan even when a model wraps or truncates its JSON."""
    cleaned = re.sub(
        r"<think>.*?</think>", "", text or "", flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    try:
        payload = _json_object(cleaned)
        if isinstance(payload, dict):
            return payload
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = ast.literal_eval(cleaned[start:end + 1])
            if isinstance(payload, dict):
                return payload
        except (SyntaxError, ValueError):
            pass

    decoder = json.JSONDecoder()
    tracks = []
    seen = set()
    for offset, character in enumerate(cleaned):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[offset:])
        except json.JSONDecodeError:
            continue
        values = value if isinstance(value, list) else [value]
        if isinstance(value, dict) and isinstance(value.get("tracks"), list):
            return value
        for item in values:
            if not isinstance(item, dict):
                continue
            artist = str(item.get("artist") or "").strip()
            title = str(item.get("title") or "").strip()
            key = (artist.casefold(), title.casefold())
            if artist and title and key not in seen:
                seen.add(key)
                tracks.append(item)
    if tracks:
        return {"tracks": tracks, "targetMood": [], "avoid": []}
    raise ValueError("AI не повернув список треків")


def split_spoken_sentences(text):
    """Split short radio copy while preserving its sentence punctuation."""
    return [
        match.strip()
        for match in re.findall(r'[^.!?]+(?:[.!?]+(?:[»”"\']+)?)|[^.!?]+$', text or "")
        if match.strip()
    ]


def _sounds_scripted(text):
    """Catch stock announcer copy that still passes the factuality checks."""
    lowered = re.sub(r"\s+", " ", text or "").strip().casefold()
    without_markers = re.sub(
        r"\[\[(?:current|next)_(?:track|artist|title)\]\]",
        " ",
        lowered,
    )
    remaining_words = re.findall(r"[^\W\d_]+", without_markers, re.UNICODE)
    stock_phrases = (
        "продовжуємо без зайвої паузи",
        "залишаємо прогноз коротким",
        "музику вмикаємо",
        "нашого хіт-параду",
        "рейтинг рухається до вершини",
        "музика все скаже сама",
        "просто слухаємо далі",
        "переходимо плавно",
        "доводити нічого не треба",
        "нічого не треба доводити",
        "ти — люмен",
        "content director",
        "voice director",
        "mention_policy",
        "verified_story_data",
        "<unk>",
        "люмен у ефірі",
        "люмен в ефірі",
        "без зайвих слів",
        "без пауз",
        "просто музика",
        "давайте слухати",
        "дорогі друзі",
        "слухаю себе",
        "іноді музика виростає з реального життя",
        "за знайомою мелодією буває інша реальність",
        "один трек іноді говорить точніше за слова",
        "так музика залишає свій слід у пам'яті надовго",
        "і тут музика чесніша за будь-які сухі довідки",
        "тому цей трек звучить глибше й лишається з нами",
        "і це лишається з нами",
        "а зараз в ефірі",
        "тепер слухаємо",
        "зараз слухаємо",
        "йдемо далі",
        "рухаємося далі",
    )
    return (
        any(phrase in lowered for phrase in stock_phrases)
        or len(remaining_words) <= 3
        or re.search(r"\bзараз\b[^.!?]{0,40}\bми у\b", lowered) is not None
        or re.search(r"(?:^|[.!?]\s*)це\s+(?:radio|радіо)\b", lowered) is not None
        or re.search(r"\b(?:рейтинг|чарт|хіт-?парад|топ|countdown)\b", lowered) is not None
        or re.search(r"\bна\s+\d{1,4}(?:[-\s]?(?:му|ому|ьому|й|ій))?\s+(?:місці|місце|позиції|позиція|рядку|рядок)\b", lowered, re.UNICODE) is not None
        or re.search(
            r"\bна\s+(?:(?:(?:нуль\w*|одн\w*|дв\w*|тр\w*|чотир\w*|"
            r"п['’]?ят\w*|шіст\w*|шост\w*|сім\w*|сьом\w*|вісім\w*|"
            r"восьм\w*|дев['’]?ят\w*|десят\w*|сот\w*|тисяч\w*)"
            r"|(?:одинадц\w*|дванадц\w*|тринадц\w*|чотирнадц\w*|"
            r"п['’]?ятнадц\w*|шістнадц\w*|сімнадц\w*|вісімнадц\w*|"
            r"дев['’]?ятнадц\w*|двадц\w*|тридц\w*|сорок\w*|"
            r"п['’]?ятдесят\w*|шістдесят\w*|сімдесят\w*|"
            r"вісімдесят\w*|дев['’]?яност\w*))"
            r"[\s'’,-]+){1,6}(?:місці|місце|позиції|позиція|рядку|рядок)\b",
            lowered,
            re.UNICODE,
        ) is not None
        or re.search(
            r"\b(?:місце|позиція|рядок)\s+(?:номер\s+)?(?:\d+|"
            r"перш\w*|друг\w*|трет\w*|четверт\w*|п['’]?ят\w*|"
            r"шост\w*|сьом\w*|восьм\w*|дев['’]?ят\w*|десят\w*)\b",
            lowered,
            re.UNICODE,
        ) is not None
    )


_STORY_EVIDENCE_STOPWORDS = {
    "адже", "але", "або", "вона", "вони", "воно", "його", "йому", "їхній",
    "коли", "після", "перед", "просто", "саме", "свою", "свої", "став", "стала",
    "стали", "також", "тому", "увесь", "через", "щоби", "який", "яка", "яке",
    "було", "була", "були", "бути", "цей", "цією", "того", "тоді", "тепер",
}


def _story_evidence_stems(text):
    words = re.findall(
        r"[A-Za-zА-Яа-яІіЇїЄєҐґ]+(?:['’ʼ-][A-Za-zА-Яа-яІіЇїЄєҐґ]+)*",
        re.sub(r"\[\[[^\]]+\]\]", " ", text or ""),
    )
    return {
        word.casefold().replace("’", "'").replace("ʼ", "'")[:6]
        for word in words
        if len(word) >= 4 and word.casefold() not in _STORY_EVIDENCE_STOPWORDS
    }


def _unsupported_story_sentence(copy, verified_parts):
    """Return a story sentence with too little lexical support, if any.

    This does not try to prove facts. It catches the common failure mode where
    a model adds a fresh cinematic sentence (streets, pain, news, reactions)
    that has almost no language in common with the verified card.
    """
    evidence = _story_evidence_stems(" ".join(str(item) for item in verified_parts))
    if not evidence:
        return ""
    for sentence in split_spoken_sentences(copy):
        if "[[NEXT_" in sentence:
            continue
        stems = _story_evidence_stems(sentence)
        if len(stems) < 3:
            continue
        supported = len(stems & evidence) / len(stems)
        if supported < 0.28:
            return sentence
    return ""


INTRO_STYLES = (
    "ironic",
    "short_joke",
    "interesting_fact",
    "morning",
    "atmospheric",
    "bridge_from_previous_track",
    "listener_tease",
    "straight_radio",
    "music_story",
)

STYLE_GUIDANCE = {
    "ironic": "суха іронія про звичайну побутову ситуацію",
    "short_joke": "один короткий жарт і швидкий вихід у пісню",
    "interesting_fact": "один перевірений факт як гачок, без інших фактів",
    "morning": "жива ранкова репліка без обов'язкового жарту про каву",
    "atmospheric": "один виразний образ і спокійний музичний настрій",
    "bridge_from_previous_track": "природний місток від попередньої пісні",
    "listener_tease": "легкий доброзичливий підкол слухача",
    "straight_radio": "пряме лаконічне оголошення без жарту",
    "music_story": "короткий перевірений музичний сюжет із поворотом перед треком",
}


LEGACY_REGIONAL_ROCK_ARTISTS = {
    "кино", "кіно", "виктор цой", "віктор цой", "цой", "viktor tsoi",
    "tsoi", "kino", "ддт", "ddt", "алиса", "аліса", "aquarium",
    "аквариум", "акваріум", "наутилус помпилиус", "nautilus pompilius",
    "гражданская оборона", "гражданська оборона", "grajdanskaya oborona",
    "сектор газа", "сектор газу", "ария", "арія", "машина времени",
    "машина часу", "би 2", "бі 2", "b 2", "б 2", "король и шут",
    "король і шут", "korol i shut", "агата кристи", "агата крісті",
    "agata kristi", "чайф", "chaif", "крематорий", "крематорій",
    "krematoriy", "пикник", "пікнік", "piknik",
}


def _modern_regional_alt_rock_prompt(station_prompt):
    normalized = (station_prompt or "").casefold()
    wants_regional = re.search(
        r"(?:рос|рус|russian|ru\b|укр|україн|ukrainian|ua\b)",
        normalized,
    )
    wants_rock = "рок" in normalized or "rock" in normalized
    wants_alt = "альт" in normalized or "alternative" in normalized or "alt" in normalized
    return bool(wants_regional and wants_rock and wants_alt)


def spoken_word_count(text):
    return len(re.findall(r"[^\W\d_]+(?:['’ʼ-][^\W\d_]+)*", text or "", re.UNICODE))


def _ukrainian_copy_warnings(text, allow_time_digits=False):
    """Return deterministic language/spelling warnings for generated radio copy.

    This is intentionally lightweight, not a full grammar checker. It catches
    mistakes that are objective enough for automatic provider scoring: digits
    in copy, common Ukrainian government errors, Russian-only letters, repeated
    words and broken punctuation.
    """
    copy = text or ""
    warnings = []
    digit_scope = copy
    if allow_time_digits:
        digit_scope = re.sub(r"\b\d{1,2}:\d{2}\b", " ", digit_scope)
    if re.search(r"\d", digit_scope):
        warnings.append("числа записані цифрами")
    checks = (
        (r"\bу ефірі\b", "евфонія: «у ефірі»"),
        (r"\bпо рейтингу\b", "керування: «по рейтингу»"),
        (r"\bслідуюч(?:ий|а|е|і|ого|ої|ому|ій|им|у)\b", "калька: «слідуючий»"),
        (r"\bприймати участь\b", "калька: «приймати участь»"),
        (r"\bна протязі\b", "калька: «на протязі»"),
        (r"\bсамий\s+(?:кращ|гірш|цікав|сильн|тих|гучн)", "калька: «самий»"),
        (r"\bтільки що\b", "калька: «тільки що»"),
        (r"\bровно\b", "росіянізм: «ровно»"),
        (r"\bпогодя\b", "помилка: «погодя»"),
        (r"\bдві\s+градус(?:и|ів)\b", "узгодження: «дві градуси»"),
        (r"\bна диван\b", "керування: «на диван»"),
        (r"\bжарк(?:о|ий|а|е|і)\b", "росіянізм: «жарко»"),
        (r"\bтіном\b", "помилка: «тіном»"),
        (r"\bрозтопе\b", "помилка: «розтопе»"),
        (r"\bгарного\s+слухати\b", "помилка: «гарного слухати»"),
        (r"\bу\s+пляжі\b", "керування: «у пляжі»"),
        (r"\bзавести\s+пляшок\b", "керування: «завести пляшок»"),
        (r"^\s*гар\s*,", "обірване слово на початку репліки"),
        (r"\bветер\b", "росіянізм: «ветер»"),
        (r"\bвключити\s+радіо\b", "калька: «включити радіо»"),
        (r"\bось\s+й\b", "помилка: «ось й»"),
        (r"\bвирос\b", "нормативна форма: «виріс»"),
        (r"\bнездійснен\w*\s+сн\w*\b", "калька: «нездійснені сни»"),
        (r"\bриф\s+здався\s+(?:занадто|надто)\s+схожий\b", "керування: «риф здався надто схожим»"),
        (r"\bвідмовились\b", "літературна форма: «відмовилися»"),
        (r"\bоднією\s+з\s+найвідоміших\s+(?:\[\[|(?-i:[A-ZА-ЯІЇЄҐ]))", "пропущено іменник після «однією з найвідоміших»"),
    )
    for pattern, warning in checks:
        if re.search(pattern, copy, flags=re.IGNORECASE):
            warnings.append(warning)
    if re.search(
        r"\b(?:трекі|тіни|рушне|дальше|обі|підігрівані)\b",
        copy,
        flags=re.IGNORECASE | re.UNICODE,
    ):
        warnings.append("ненормативна або помилкова словоформа")
    if re.search(r"\b(?:play|stop|next\s+track)\b", copy, flags=re.IGNORECASE):
        warnings.append("службова команда моделі")
    if re.search(r"[ёыэъЁЫЭЪ]", copy):
        warnings.append("російські літери в українській репліці")
    if re.search(
        r"(?=[^\s]*[A-Za-z])(?=[^\s]*[А-Яа-яІіЇїЄєҐґ])"
        r"[A-Za-zА-Яа-яІіЇїЄєҐґ'’ʼ-]+",
        copy,
    ):
        warnings.append("змішані латинські й кириличні літери в одному слові")
    if re.search(r"\b[А-ЯІЇЄҐ]{3,}(?:\s+[А-ЯІЇЄҐ]{3,})+\b", copy):
        warnings.append("службовий текст великими літерами")
    if re.search(
        r"\b([А-Яа-яІіЇїЄєҐґA-Za-z'’]{3,})\s+\1\b",
        copy,
        flags=re.IGNORECASE,
    ):
        warnings.append("повторене слово")
    if re.search(r"\s+[,.!?;:]", copy):
        warnings.append("пробіл перед розділовим знаком")
    if re.search(r"[,;:](?=[^\s\d/])|\.(?=[А-ЯІЇЄҐA-Z])", copy):
        warnings.append("немає пробілу після розділового знака")
    if re.search(
        r"<unk>|\b(?:content director|voice director|mention_policy|verified_story_data)\b",
        copy,
        flags=re.IGNORECASE,
    ):
        warnings.append("службовий текст моделі")
    if re.search(r"[!?]{2,}|\.{3,}", copy):
        warnings.append("надмірна пунктуація")
    if copy.count("«") != copy.count("»"):
        warnings.append("незбалансовані лапки-ялинки")
    if copy.count("(") != copy.count(")"):
        warnings.append("незбалансовані дужки")
    if copy.count('"') % 2:
        warnings.append("незбалансовані лапки")
    return warnings


def _contains_weather_reference(text):
    """Detect weather talk that must be explicitly scheduled by the planner."""
    if re.search(
        r"\b(?:надвор\w*|тепл\w*|ясн\w*|похмар\w*|хмар\w*|вітр\w*|жар\w*|темні(?:є|шає|ти))\b",
        text or "",
        flags=re.IGNORECASE | re.UNICODE,
    ):
        return True
    return re.search(
        r"\b(?:погод\w*|температур\w*|градус\w*|спек\w*|спекот\w*|"
        r"жарк\w*|дощ\w*|злив\w*|гроз\w*|сніг\w*|мороз\w*|"
        r"кондиціонер\w*|пустел\w*|піт\w*|сонце\s+(?:пече|гріє|настирливе))\b",
        text or "",
        flags=re.IGNORECASE | re.UNICODE,
    ) is not None


def _contains_unmarked_track_credit(text):
    """Reject artist/title literals that bypass DB-backed track markers."""
    return re.search(
        r"(?:^|[.!?]\s+)(?:(?!\[\[)[^.!?\n]){1,70}\s+—\s+"
        r"«(?!\[\[)[^»\n]{1,120}»",
        text or "",
        flags=re.UNICODE,
    ) is not None


def _replace_track_markers(copy, track, current=None):
    current_label = (
        f'{compact_artist_credit(current["artist"])} — «{current["title"]}»'
        if current else "початок ефіру"
    )
    next_label = f'{compact_artist_credit(track["artist"])} — «{track["title"]}»'
    replacements = {
        "[[CURRENT_TRACK]]": current_label,
        "[[CURRENT_ARTIST]]": compact_artist_credit((current or {}).get("artist", "")),
        "[[CURRENT_TITLE]]": (current or {}).get("title", ""),
        "[[NEXT_TRACK]]": next_label,
        "[[NEXT_ARTIST]]": compact_artist_credit(track["artist"]),
        "[[NEXT_TITLE]]": track["title"],
    }
    result = copy
    for marker, value in replacements.items():
        result = result.replace(marker, value)
    return result


def _story_reveal_copy(plan):
    reveals = (
        "Після цієї деталі мікрофон переходить до [[NEXT_TRACK]].",
        "Наступний поворот ефіру — [[NEXT_TRACK]].",
        "На цьому нерві починається [[NEXT_TRACK]].",
        "Цю музичну лінію продовжує [[NEXT_TRACK]].",
        "Тепер простір забирає [[NEXT_TRACK]].",
    )
    variant = int((plan or {}).get("story_variant") or 0)
    index = (int((plan or {}).get("story_id") or 0) + variant) % len(reveals)
    return reveals[index]


def _replace_story_reveal(copy, plan):
    """Replace model-written NEXT claims with a safe station-owned reveal."""
    sentences = split_spoken_sentences(copy)
    if not sentences:
        return copy
    replaced = False
    result = []
    for sentence in sentences:
        if "[[NEXT_" in sentence:
            if not replaced:
                result.append(_story_reveal_copy(plan))
                replaced = True
            continue
        result.append(sentence)
    return " ".join(result) if replaced else copy


def _ground_story_copy(copy, plan):
    """Lock AI story sentences to the closest verified source wording.

    The model still chooses emphasis and order. The aired factual clauses come
    only from the verified card, so a plausible paraphrase cannot smuggle in a
    new place, reaction, metaphor or outcome.
    """
    verified = []
    for value in [
        (plan or {}).get("story_hook", ""),
        *((plan or {}).get("story_data", []) or []),
    ]:
        part = normalize_linguistic(str(value)).strip(" .!?")
        if part and part.casefold() not in {item.casefold() for item in verified}:
            verified.append(part)
    if not verified:
        return ""

    selected = []
    for sentence in split_spoken_sentences(copy):
        if "[[NEXT_" in sentence:
            continue
        stems = _story_evidence_stems(sentence)
        if not stems:
            continue
        matches = []
        for index, part in enumerate(verified):
            if part in selected:
                continue
            source_stems = _story_evidence_stems(part)
            intersection = len(stems & source_stems)
            score = (
                intersection / max(1, len(stems))
                + intersection / max(1, len(source_stems))
            )
            matches.append((score, intersection, -index, part))
        if matches:
            score, intersection, _order, part = max(matches)
            if intersection >= 2 and score >= 0.45:
                selected.append(part)

    reveal = _story_reveal_copy(plan)
    if not selected:
        variant = int((plan or {}).get("story_variant") or 0)
        selected = [verified[variant % len(verified)]]

    # Apply the word budget after AI ordering as well; several individually
    # valid facts can overflow the TTS window when combined.
    ordered = [*selected, *(part for part in verified if part not in selected)]
    budgeted = []
    for part in ordered:
        projected = spoken_word_count(". ".join([*budgeted, part, reveal]))
        if projected <= 48:
            budgeted.append(part)
    if not budgeted:
        budgeted = selected[:1]
    return ". ".join([*budgeted[:4], reveal])


def _canonicalize_verified_track_mentions(copy, track, mention_policy):
    """Turn exact DB-backed literal names from AI back into safe markers."""
    result = copy or ""
    artist = compact_artist_credit(track.get("artist", ""))
    title = track.get("title", "")
    replacements = []
    if mention_policy in {"artist_and_title", "title_only"} and title:
        replacements.append((title, "[[NEXT_TITLE]]"))
    if mention_policy in {"artist_and_title", "artist_only"} and artist:
        replacements.append((artist, "[[NEXT_ARTIST]]"))
    for literal, marker in replacements:
        if marker not in result:
            result = re.sub(
                rf"(?<![\w]){re.escape(literal)}(?![\w])",
                marker, result, count=1, flags=re.IGNORECASE,
            )
    return result


def persona_fallback_copy(track, current, context, plan):
    """A short Adam Vector fallback that respects the selected structure."""
    structure = (plan or {}).get("structure") or "announce"
    reaction = (plan or {}).get("reaction") or "neutral"
    length_class = (plan or {}).get("length_class") or "short"
    rubric = (plan or {}).get("rubric") or ""
    if rubric:
        copies = {
            "basement_track": "Трек з підвалу: поза звичним маршрутом, але саме тому тут [[NEXT_TRACK]].",
            "after_midnight": "Після опівночі музика перестає прикидатися нормальною, і далі [[NEXT_TRACK]].",
            "without_context": "Без контексту: сама назва вже зробила половину роботи, [[NEXT_TRACK]].",
        }
        return copies.get(rubric, copies["without_context"])

    if structure == "announce":
        copies = (
            "У центрі цього переходу — [[NEXT_TRACK]].",
            "Коротко й без зайвих пояснень: [[NEXT_TRACK]].",
            "Слова відступають. У центрі — [[NEXT_TRACK]].",
            "Один точний анонс перед стартом: [[NEXT_TRACK]].",
            "Пауза закінчується там, де починається [[NEXT_TRACK]].",
            "Мікрофон замовкає. Далі — [[NEXT_TRACK]].",
            "Кілька секунд тиші — і далі [[NEXT_TRACK]].",
            "Без передмови, але з точним ім'ям: [[NEXT_TRACK]].",
            "Новий рух цієї музичної лінії — [[NEXT_TRACK]].",
            "Наступний відрізок ефіру: [[NEXT_TRACK]].",
        )
        variant = int((plan or {}).get("fallback_variant") or 0)
        return copies[variant % len(copies)]
    if structure == "joke":
        return "[[NEXT_TITLE]] — назва, після якої регулятор гучності виглядає підозріло."
    if structure == "listener":
        return "Якщо ти досі тут, наступні кілька хвилин знайшли правильну людину."

    reactions = {
        "high_energy": "Темп піднявся, але світліше від цього не стало.",
        "low_energy": "Трохи повільніше, трохи ближче, без потреби заповнювати кожну паузу.",
        "dark": "Світло можна не вмикати, далі буде темно, але не тихо.",
        "dreamy": "Ритм відступає на крок, і думкам стає трохи просторіше.",
        "neutral": "Цей перехід не потребує пояснень; важливіше, куди він веде.",
    }
    if structure == "transition" and current:
        if length_class == "long":
            return "Попередній трек поступово розчинився, але тиші не буде, наступний рух уже починається."
        return reactions.get(reaction, reactions["neutral"])
    if structure == "mood":
        return reactions.get(reaction, reactions["neutral"])
    return "Мікрофон поступається місцем наступному звуку."


def fallback_intro_copy(track, current, style, verified_fact=""):
    copies = {
        "ironic": (
            "[[NEXT_TRACK]]. Тепер у планах є хоча б один пункт, який справді хочеться виконати.",
            "[[NEXT_TRACK]]. Звучить як достатня причина відкласти серйозне обличчя.",
            "У навушниках уже звільнено місце для [[NEXT_TRACK]].",
        ),
        "short_joke": (
            "[[NEXT_TRACK]]. Регулятор гучності щойно втратив право голосу.",
            "[[NEXT_TRACK]]. Сусідам залишимо право на рецензію.",
            "[[NEXT_TRACK]]. Побут офіційно бере музичну паузу.",
        ),
        "morning": (
            "Ранок отримує чіткіший контур із [[NEXT_TRACK]].",
            "Першу нормальну думку сьогодні довіримо [[NEXT_TRACK]].",
            "Світло вже ввімкнуло день; звук додає [[NEXT_TRACK]].",
        ),
        "atmospheric": (
            "У цьому повітрі бракувало саме [[NEXT_TRACK]].",
            "Тиша відступає рівно настільки, щоб увійшов [[NEXT_TRACK]].",
            "Мить змінює форму, щойно починається [[NEXT_TRACK]].",
        ),
        "bridge_from_previous_track": (
            "Після [[CURRENT_TRACK]] напрям змінюється: [[NEXT_TRACK]].",
            "Відлуння [[CURRENT_TRACK]] ще тут, а наступний крок робить [[NEXT_TRACK]].",
            "Між [[CURRENT_TRACK]] і [[NEXT_TRACK]] вистачить одного вдиху.",
        ),
        "listener_tease": (
            "Ти дочекався моменту, коли гучність має сенс: [[NEXT_TRACK]].",
            "Здається, навушники теж були готові до [[NEXT_TRACK]].",
            "Цей трек перевірить, чи ти справді не відволікався: [[NEXT_TRACK]].",
        ),
        "straight_radio": (
            "Мікрофон закривається на [[NEXT_TRACK]].",
            "Наступні хвилини належать [[NEXT_TRACK]].",
            "У центрі цього переходу — [[NEXT_TRACK]].",
        ),
    }
    fact = re.sub(r"\s+", " ", (verified_fact or "").strip()).strip(" .!?")
    if fact:
        copies["interesting_fact"] = (
            f"{fact}. Ця деталь веде просто до [[NEXT_TRACK]].",
            f"Коротка деталь: {fact}. Її підхоплює [[NEXT_TRACK]].",
        )
    choices = copies.get(style) or copies["straight_radio"]
    return random.choice(choices)


def fallback_short_copy(track, current=None, style="straight_radio"):
    return fallback_intro_copy(track, current, style)


def contextual_fallback_copy(track, current, style, context, plan, short=False):
    """Create factual copy for scheduled content even when the LLM is offline."""
    content_type = (plan or {}).get("content_type", "")
    time_context = (context or {}).get("time", {})
    weather = (context or {}).get("weather", {})
    station = (context or {}).get("station", {})
    city = station.get("city_locative") or station.get("city") or "місті"

    if content_type == "story" and (plan or {}).get("story_data"):
        story_data = [
            re.sub(r"\s+", " ", str(part)).strip(" .")
            for part in plan.get("story_data", [])
            if str(part).strip()
        ]
        hook = re.sub(
            r"\s+", " ", str(plan.get("story_hook") or "")
        ).strip(" .")
        variant = int((plan or {}).get("story_variant") or 0)
        offset = variant % len(story_data)
        ordered_data = story_data[offset:] + story_data[:offset]
        # The fallback follows the same new editorial contract as the AI:
        # concrete verified material first, with no reusable philosophical
        # opener or empty conclusion. Keep unique facts in source order.
        candidates = []
        for piece in [hook, *ordered_data]:
            if piece and piece.casefold() not in {
                item.casefold() for item in candidates
            }:
                candidates.append(piece)
        reveal = _story_reveal_copy(plan)
        if short or float((plan or {}).get("target_seconds") or 10) <= 10:
            pieces = candidates[:1]
        else:
            pieces = []
            # Prefer factual density over padding. Three strong sentences are
            # valid when a fourth would either repeat the hook or overflow the
            # 55-word TTS window.
            for piece in candidates:
                projected = spoken_word_count(
                    ". ".join([*pieces, piece, reveal])
                )
                # Leave headroom for the speech normalizer, which expands
                # numerals and foreign names into several spoken words.
                if projected <= 48:
                    pieces.append(piece)
            if not pieces and candidates:
                pieces = candidates[:1]
        if plan.get("story_callback"):
            pieces.insert(
                0,
                "Пам'ятаєте, залишилася ще одна деталь — "
                + str(plan["story_callback"]).strip(" ."),
            )
        pieces = pieces[:4]
        copy = ". ".join(piece for piece in pieces if piece)
        return f"{copy}. {reveal}" if copy else reveal

    if content_type == "top_of_hour" and time_context.get("time"):
        exact_time = time_context["time"]
        if (plan or {}).get("clock_slot_id") == "hour_open":
            persona = ((context or {}).get("personality") or {}).get("persona") or {}
            host_name = persona.get("name") or "Адам Вектор"
            station_name = station.get("name") or "LUMEN RADIO"
            return (
                f"Я — {host_name}, цифровий ведучий {station_name}. "
                f"У {city} зараз {exact_time}. Цю годину відкриває [[NEXT_TRACK]]."
            )
        return f"У {city} зараз {exact_time}. Цю годину продовжує [[NEXT_TRACK]]."

    if content_type in {"weather_touch", "weather_change"} and weather.get("available"):
        temperature = weather.get("temperature")
        condition = weather.get("condition") or "погода без різких сюрпризів"
        weather_line = f"У {city} зараз {condition}"
        if temperature is not None:
            weather_line += f", {round(float(temperature)):+d}°"
        if content_type == "weather_change":
            if weather.get("rain_soon"):
                weather_line += ", а в найближчі години можливий дощ"
            elif abs(float(weather.get("temperature_change_3h") or 0)) >= 6:
                direction = "потеплішає" if float(weather["temperature_change_3h"]) > 0 else "похолодає"
                weather_line += f", і протягом трьох годин помітно {direction}"
        time_prefix = (
            f"Зараз {time_context.get('time')}. "
            if (plan or {}).get("must_say_time") and time_context.get("time")
            else ""
        )
        return f"{time_prefix}{weather_line}. На цьому тлі починається [[NEXT_TRACK]]."

    if content_type == "mood_check":
        return "Тримаємо ефір по настрою, рівно в цей нерв вечора. Його підхоплює [[NEXT_TRACK]]."

    if content_type == "callback":
        return (
            "До погоди ще повернемося. "
            "Музичну лінію продовжує [[NEXT_TRACK]]."
        )

    if content_type == "fact" and (plan or {}).get("verified_fact"):
        fact = re.sub(r"\s+", " ", plan["verified_fact"]).strip(" .!?")
        return f"Одна деталь: {fact}. Її підхоплює [[NEXT_TRACK]]."

    if content_type == "rubric":
        return persona_fallback_copy(track, current, context, plan)

    if short:
        return persona_fallback_copy(track, current, context, plan)
    if (plan or {}).get("structure"):
        return persona_fallback_copy(track, current, context, plan)
    return fallback_intro_copy(track, current, style, (plan or {}).get("verified_fact", ""))


def story_quality_score(display_text, speech_text, plan, next_track=None):
    """Score whether a sourced story is ready for air on a 0-10 scale."""
    sentences = split_spoken_sentences(display_text)
    sentence_count = len(sentences)
    word_count = spoken_word_count(speech_text)
    lowered = (display_text or "").casefold()
    fact_score = 10 if plan.get("story_data") and plan.get("story_source", {}).get("url") else 4
    legacy_phrases = (
        "іноді музика виростає з реального життя",
        "за знайомою мелодією буває інша реальність",
        "так музика залишає свій слід",
        "і це лишається з нами",
        "а зараз в ефірі",
    )
    # This score keeps its public key for compatibility, but now rewards a
    # concrete, non-template story instead of abstract emotional vocabulary.
    emotion_score = 9 if not any(
        phrase in lowered for phrase in legacy_phrases
    ) else 3
    length_class = str(plan.get("length_class") or "normal")
    sentence_bounds = {
        "short": (1, 2),
        "normal": (2, 4),
        "feature": (3, 4),
    }.get(length_class, (2, 4))
    planned_word_min = int(plan.get("word_min") or 12)
    planned_word_max = int(plan.get("word_max") or 80)
    # Grounded cards may legitimately be shorter than the editorial target;
    # factual density is preferable to invented padding.
    safe_word_min = max(8, round(planned_word_min * 0.65))
    radio_score = 10 if sentence_bounds[0] <= sentence_count <= sentence_bounds[1] else 4
    tts_score = 10 if (
        safe_word_min <= word_count <= planned_word_max
        and not re.search(r"[A-Za-z]", speech_text or "")
    ) else 5
    final_sentence = sentences[-1].casefold() if sentences else ""
    artist = compact_artist_credit((next_track or {}).get("artist", "")).casefold()
    title = str((next_track or {}).get("title", "")).casefold()
    transition_score = 10 if (
        artist and title and artist in final_sentence and title in final_sentence
    ) else 4
    scores = {
        "facts": fact_score,
        "emotion": emotion_score,
        "radio": radio_score,
        "tts": tts_score,
        "transition": transition_score,
    }
    scores["final"] = round(sum(scores.values()) / len(scores), 1)
    return scores


class RadioAPI:
    def __init__(self, root: Path, enable_auto_restart: bool = False):
        self.root = root
        self.db = Database(root / "data" / "radio.db")
        reset = self.db.reset_runtime_session()
        LOGGER.info("Runtime session memory reset: %s", reset)
        self._sync_host_prompt_version()
        self._shutdown_event = threading.Event()
        # Off by default: scripts/tests construct RadioAPI too, and must never
        # trigger a real os._exit()/process relaunch as a side effect.
        self._enable_auto_restart = enable_auto_restart
        self._provider_health_lock = threading.RLock()
        self._provider_health = self._load_provider_health()
        self.updater = UpdateManager(root)
        self._prepare_ai_session()
        self._last_intro_style = ""
        self._discovery_plan_lock = threading.RLock()
        self._discovery_plan_prompt = ""
        self._discovery_plan_pool = []
        self._discovery_plan_context = {"target_mood": [], "avoid": []}
        self.context_engine = ContextEngine(self.db)
        self.content_planner = ContentPlanner(self.db)
        self.personalization = self.content_planner.personalization
        self.music_knowledge = self.content_planner.knowledge_base
        self.voice_director = VoiceDirector(DEFAULT_TTS_VOICE)
        self.host_brain = HostBrain(self.db, self.content_planner, self.voice_director, self.music_knowledge)
        self.broadcast_safety = BroadcastSafety(self.db)
        self._prepare_lock = threading.Lock()
        # Keep the static demo chart only as an offline/dev bootstrap. In a
        # configured live station the library must come from imported files or
        # AI discovery, not from placeholder data that can leak into playback.
        if not self.db.tracks() and not self._ai_providers(self.db.settings()):
            seed = parse_chart(DEMO_CHART)
            self.db.replace_tracks(seed)
        story_file = self.root / "data" / "music-stories.json"
        if story_file.exists():
            try:
                self.import_music_stories(
                    json.loads(story_file.read_text(encoding="utf-8-sig"))
                )
            except (OSError, ValueError, json.JSONDecodeError):
                # A broken research file must not prevent the radio from booting.
                pass
        self.radio_queue = RadioQueueManager(
            self.db,
            self.root,
            discoverer=self._discover_queue_track,
            discovery_available=lambda: bool(self._ai_providers(self.db.settings())),
            provider_status=self.provider_health,
        )

    def _sync_host_prompt_version(self):
        """Invalidate reusable copy when the editorial host prompt changes."""
        stored_version = str(
            self.db.settings().get("host_prompt_version", "0")
        ).strip()
        if stored_version == HOST_PROMPT_VERSION:
            return None
        removed = self.db.clear_generated_host_copy()
        self.db.save_settings({"host_prompt_version": HOST_PROMPT_VERSION})
        LOGGER.info(
            "Host prompt upgraded %s -> %s; stale copy removed: %s",
            stored_version or "0",
            HOST_PROMPT_VERSION,
            removed,
        )
        return removed

    @staticmethod
    def _normalized_station_prompt(value):
        return " ".join(str(value or "").casefold().split())

    @classmethod
    def _is_romantic_evening_profile(cls, station_prompt):
        text = cls._normalized_station_prompt(station_prompt)
        romantic_mode = any(
            phrase in text
            for phrase in (
                "romantic evening", "романтичний вечір", "романтический вечер",
            )
        )
        seed_artists = {
            cls._normalize_music_text(seed["artist"])
            for seed in ROMANTIC_EVENING_SEED_TRACKS
        }
        prompt_key = cls._normalize_music_text(text)
        mentioned_seeds = sum(artist in prompt_key for artist in seed_artists)
        return romantic_mode or mentioned_seeds >= 2

    @classmethod
    def _romantic_evening_queries(cls, lane_index=0):
        seeds = list(ROMANTIC_EVENING_SEED_TRACKS)
        start = (max(0, int(lane_index)) * 3) % len(seeds)
        selected = [seeds[(start + offset) % len(seeds)] for offset in range(3)]
        queries = []
        for seed in selected:
            label = f'{seed["artist"]} {seed["title"]}'
            queries.extend((
                f"songs similar to {label}",
                f"песни похожие на {label}",
            ))
        queries.extend((
            "romantic alternative pop rock 2005 2018 russian ukrainian",
            "молодёжный emo pop rock любовь 2010",
        ))
        return queries

    @staticmethod
    def _bounded_score(value, default=0.0):
        try:
            return max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            return float(default)

    @classmethod
    def _romantic_evening_genre_score(cls, value):
        genre = cls._recommendation_genre(value)
        normalized = cls._normalize_music_text(genre)
        if any(
            cls._normalize_music_text(phrase) in normalized
            for phrase in ROMANTIC_EVENING_FORBIDDEN_GENRE_PHRASES
        ):
            return 0.0
        _text, mentions = cls._genre_mentions(genre)
        families = {item[2] for item in mentions}
        if families & {"chanson", "post_punk", "darkwave", "art_rock", "metal"}:
            return 0.0
        desired = families & ROMANTIC_EVENING_GENRES
        if desired:
            return 100.0
        if "rock" in families and any(
            word in normalized
            for word in ("melod", "guitar", "festival", "youth", "мелод", "гітар", "гитар")
        ):
            return 85.0
        if "rock" in families:
            return 65.0
        return 0.0

    @classmethod
    def _romantic_evening_suitability(cls, value):
        artist = str((value or {}).get("artist") or "").strip()
        title = str((value or {}).get("title") or "").strip()
        artist_key = cls._normalize_music_text(artist)
        key = (artist_key, cls._normalize_music_text(title))
        seeds = {
            (
                cls._normalize_music_text(seed["artist"]),
                cls._normalize_music_text(seed["title"]),
            ): seed
            for seed in ROMANTIC_EVENING_SEED_TRACKS
        }
        seed_artists = {seed_key[0] for seed_key in seeds}
        blocked = {
            cls._normalize_music_text(name)
            for name in ROMANTIC_EVENING_BLOCKED_ARTISTS
        }
        if not all(key) or artist_key in blocked:
            return {
                "score": 0.0, "era": 0.0, "artist": 0.0,
                "genre": 0.0, "mood": 0.0, "popularity": 0.0,
                "blocked": artist_key in blocked,
            }

        seed = seeds.get(key)

        def year_score(raw, default=60.0):
            try:
                year = int(float(raw))
            except (TypeError, ValueError):
                return default
            if 2005 <= year <= 2018:
                return 100.0
            distance = 2005 - year if year < 2005 else year - 2018
            return max(0.0, 100.0 - distance * 6.0)

        release_year = (
            (value or {}).get("year")
            or (value or {}).get("releaseYear")
            or (seed or {}).get("year")
        )
        era_score = year_score(release_year)
        artist_score = cls._bounded_score(
            (value or {}).get("artistGenerationScore"),
            year_score((value or {}).get("artistBreakthroughYear")),
        )
        if artist_key in seed_artists:
            artist_score = 100.0

        genre_score = cls._romantic_evening_genre_score(value)
        if seed:
            genre_score = max(genre_score, 100.0)

        moods = (value or {}).get("moods") or (value or {}).get("mood") or []
        if isinstance(moods, str):
            moods = [moods]
        mood_text = cls._normalize_music_text(
            " ".join(str(item) for item in moods)
            + " " + str((value or {}).get("reason") or "")
        )
        mood_hits = sum(
            cls._normalize_music_text(word) in mood_text
            for word in ROMANTIC_EVENING_MOOD_WORDS
        )
        inferred_mood = min(100.0, 55.0 + mood_hits * 12.0) if mood_hits else 55.0
        mood_score = cls._bounded_score(
            (value or {}).get("moodScore"), inferred_mood,
        )
        popularity_score = cls._bounded_score(
            (value or {}).get("popularityScore"), 65.0,
        )
        if seed:
            era_score = max(era_score, 100.0)
            artist_score = 100.0
            mood_score = max(mood_score, 100.0)
            popularity_score = max(popularity_score, 90.0)

        score = (
            era_score * 0.20
            + artist_score * 0.15
            + genre_score * 0.25
            + mood_score * 0.25
            + popularity_score * 0.15
        )
        return {
            "score": round(score, 2),
            "era": round(era_score, 2),
            "artist": round(artist_score, 2),
            "genre": round(genre_score, 2),
            "mood": round(mood_score, 2),
            "popularity": round(popularity_score, 2),
            "blocked": False,
        }

    @staticmethod
    def _recommendation_genre(value):
        genres = (
            value.get("genre") or value.get("genres")
            if isinstance(value, dict) else None
        )
        if isinstance(genres, list):
            genres = ", ".join(str(item).strip() for item in genres if str(item).strip())
        return str(genres or (value or {}).get("reason") or "").strip()

    @staticmethod
    def _genre_mentions(value):
        text = " ".join(str(value or "").casefold().replace("_", " ").split())
        matches = []
        occupied = []
        aliases = sorted(
            (
                (alias.casefold(), genre)
                for genre, values in GENRE_ALIASES.items()
                for alias in values
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for alias, genre in aliases:
            pattern = rf"(?<!\w){re.escape(alias)}(?!\w)"
            for match in re.finditer(pattern, text, flags=re.UNICODE):
                span = match.span()
                if any(span[0] < end and start < span[1] for start, end in occupied):
                    continue
                occupied.append(span)
                matches.append((span[0], span[1], genre))
        return text, sorted(matches)

    @classmethod
    def _station_genre_policy(cls, station_prompt):
        text, mentions = cls._genre_mentions(station_prompt)
        positive = set()
        negative = set()
        negative_marker = re.compile(
            r"(?<!\w)(?:без|крім|окрім|не|уникай|уникайте|exclude|avoid|"
            r"without|except|no|not|rather\s+than|instead\s+of|over)(?!\w)",
            flags=re.IGNORECASE | re.UNICODE,
        )
        positive_marker = re.compile(
            r"(?<!\w)(?:prioriti[sz]e|prefer|include|focus(?:ed)?|aim|want|"
            r"choose|keep|пріоритет\w*|переваг\w*|віддавай\w*|віддавайте\w*|"
            r"включ\w*|додавай\w*|додавайте\w*|обирай\w*|обирайте\w*)(?!\w)",
            flags=re.IGNORECASE | re.UNICODE,
        )
        for start, _end, genre in mentions:
            # Negation often introduces a comma-separated list ("Avoid old
            # rock, chanson, post-punk...").  Looking only a few words back
            # loses that scope and turns later exclusions into requirements.
            # Keep polarity through the current sentence/paragraph, while a
            # later explicit positive marker can start a positive list again.
            clause_start = max(
                (text.rfind(separator, 0, start) for separator in ".!?;\n"),
                default=-1,
            ) + 1
            prefix = text[clause_start:start]
            last_negative = max(
                (match.start() for match in negative_marker.finditer(prefix)),
                default=-1,
            )
            last_positive = max(
                (match.start() for match in positive_marker.finditer(prefix)),
                default=-1,
            )
            (negative if last_negative > last_positive else positive).add(genre)
        # A broad word can legitimately occur in both contexts, for example
        # "modern alternative rock; avoid classic Russian rock".  Our compact
        # taxonomy cannot encode the era/adjective qualifier, so retaining the
        # positive family is safer than forbidding all of its subgenres.
        positive_parents = {
            parent
            for genre in positive
            for parent in GENRE_PARENTS.get(genre, set())
        }
        negative.difference_update(positive | positive_parents)
        # A specific subgenre in the prompt is a real constraint. Generic
        # parent tags are used only when the user did not name a narrower one.
        specific = positive - GENERIC_GENRES
        return {
            "required": specific or positive,
            "forbidden": negative,
        }

    @classmethod
    def _recommendation_style_issue(cls, station_prompt, genre):
        policy = cls._station_genre_policy(station_prompt)
        required = policy["required"]
        forbidden = policy["forbidden"]
        if not required and not forbidden:
            return ""
        _text, mentions = cls._genre_mentions(genre)
        candidate = {item[2] for item in mentions}
        expanded = set(candidate)
        for item in candidate:
            expanded.update(GENRE_PARENTS.get(item, set()))
        if not candidate:
            return "genre-missing"
        if forbidden and expanded & forbidden:
            return "genre-conflict"
        if required and not expanded & required:
            return "genre-conflict"
        return ""

    def _translated_station_prompt(self, station_prompt, settings=None):
        """Translate a Ukrainian/Russian station-style prompt to English for AI search.

        Keyword heuristics elsewhere (legacy-artist filters, "без поп"/"без
        кавер" checks) keep reading the original-language text; only the copy
        handed to the AI music search benefits from the English translation.
        """
        text = str(station_prompt or "").strip()
        if not text or detect_text_language(text) in ("en", "other"):
            return text
        settings = settings or self.db.settings()
        normalized = self._normalized_station_prompt(text)
        cached = str(settings.get("station_prompt_en") or "").strip()
        cached_source = self._normalized_station_prompt(settings.get("station_prompt_en_source", ""))
        if cached and cached_source == normalized:
            return cached
        system_prompt = (
            "Translate the user's text to natural, concise English for a music "
            "search query. Preserve every artist name, band name and genre term "
            "exactly. Do not add comments, quotes or explanations - return only "
            "the translated text."
        )
        for spec in self._ai_providers(settings):
            response = self._provider_chat_completion(
                spec, system_prompt, text, 0.0, 1.0, 200,
            )
            candidate = response.get("candidate", "").strip().strip('"').strip()
            if candidate:
                self.db.save_settings({
                    "station_prompt_en": candidate,
                    "station_prompt_en_source": text,
                })
                return candidate
        LOGGER.warning("Station prompt translation failed; using original text for AI search")
        return text

    @staticmethod
    def _playlist_track_refs(value):
        try:
            payload = json.loads(value or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return [
            {
                "artist": str(item.get("artist") or "").strip(),
                "title": str(item.get("title") or "").strip(),
            }
            for item in payload
            if isinstance(item, dict)
            and str(item.get("artist") or "").strip()
            and str(item.get("title") or "").strip()
        ][:200]

    def _merge_playlist_refs(self, *collections, limit=200):
        merged = []
        seen = set()
        for collection in collections:
            for item in collection or []:
                artist = str(item.get("artist") or "").strip()
                title = str(item.get("title") or "").strip()
                key = (
                    self._normalize_music_text(artist),
                    self._normalize_music_text(title),
                )
                if not all(key) or key in seen:
                    continue
                seen.add(key)
                merged.append({"artist": artist, "title": title})
                if len(merged) >= limit:
                    return merged
        return merged

    def _remember_ai_tracks(self, tracks):
        settings = self.db.settings()
        remembered = self._merge_playlist_refs(
            tracks,
            self._playlist_track_refs(settings.get("ai_previous_playlist", "[]")),
        )
        self.db.save_settings({
            "ai_previous_playlist": json.dumps(remembered, ensure_ascii=False),
        })
        return remembered

    def _clear_ai_cache_files(self):
        cache_dir = (self.root / "downloads" / "queue").resolve()
        expected = self.root.resolve() / "downloads" / "queue"
        if cache_dir != expected:
            raise RuntimeError("Некоректний шлях AI-кешу")
        removed = 0
        if cache_dir.exists():
            # Delete depth-first so a locked file (e.g. the track currently
            # playing) is skipped instead of aborting the whole purge and
            # leaving the newly saved settings looking like they never took.
            for path in sorted(cache_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                try:
                    if path.is_dir() and not path.is_symlink():
                        path.rmdir()
                    else:
                        path.unlink()
                        removed += 1
                except OSError:
                    LOGGER.warning("Could not remove AI cache path (in use?): %s", path)
        cache_dir.mkdir(parents=True, exist_ok=True)
        return removed

    def _prepare_ai_session(self):
        settings = self.db.settings()
        current_prompt = self._normalized_station_prompt(
            settings.get("station_prompt", DEFAULTS["station_prompt"])
        )
        previous_prompt = self._normalized_station_prompt(
            settings.get("ai_playlist_prompt", "")
        )
        cached_tracks = [
            {"artist": track["artist"], "title": track["title"]}
            for track in self.db.tracks()
            if str(track.get("library_source") or "") == "ai"
        ]
        prompt_changed = bool(previous_prompt and previous_prompt != current_prompt)
        if prompt_changed:
            previous_tracks = []
            LOGGER.info("Station genre prompt changed; starting an unrelated AI playlist")
        else:
            previous_tracks = self._merge_playlist_refs(
                cached_tracks,
                self._playlist_track_refs(settings.get("ai_previous_playlist", "[]")),
            )
        self.db.save_settings({
            "ai_playlist_prompt": current_prompt,
            "ai_previous_playlist": json.dumps(previous_tracks, ensure_ascii=False),
        })
        removed_rows = self.db.purge_ai_library()
        removed_files = self._clear_ai_cache_files()
        if prompt_changed:
            LOGGER.info(
                "AI session reset after prompt change: removed_tracks=%s, removed_files=%s",
                removed_rows,
                removed_files,
            )
        else:
            LOGGER.info(
                "Fresh AI session started: excluded_previous=%s, removed_tracks=%s, "
                "removed_files=%s",
                len(previous_tracks), removed_rows, removed_files,
            )

    def shutdown(self):
        """Stop background work and remember the active tracks as exclusions."""
        if self._shutdown_event.is_set():
            return {"ok": True, "already_closed": True}
        self._shutdown_event.set()
        if hasattr(self, "radio_queue"):
            self.radio_queue.stop(timeout=2)
        tracks = [
            {"artist": track["artist"], "title": track["title"]}
            for track in self.db.tracks()
            if str(track.get("library_source") or "") == "ai"
        ]
        remembered = self._remember_ai_tracks(tracks)
        LOGGER.info(
            "AI tracks remembered for anti-repeat on shutdown: active=%s, remembered=%s",
            len(tracks), len(remembered),
        )
        return {
            "ok": True,
            "removed_tracks": 0,
            "removed_files": 0,
            "preserved_tracks": len(tracks),
            "remembered_tracks": len(remembered),
        }

    def bootstrap(self):
        try:
            self.scan_music()
            queue = self.radio_queue.bootstrap()
            settings = self._settings_payload()
            if (
                self._enable_auto_restart
                and str(settings.get("auto_update_enabled", "1")) == "1"
            ):
                self.updater.check()
            return {
                "ok": True,
                "app_version": APP_VERSION,
                "update_status": self.updater.status(),
                "tracks": self._ai_library_tracks(),
                "settings": settings,
                "radio_queue": queue,
                "pilot_clock": self.pilot_hour(),
                "broadcast_safety": self.broadcast_safety.status(),
            }
        except Exception as exc:
            # pywebview promise rejections otherwise lose the Python traceback
            # and the UI can only show a generic "data loading" message.
            LOGGER.exception("LUMEN bootstrap failed")
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "tracks": [],
                "settings": {},
                "radio_queue": None,
                "app_version": APP_VERSION,
                "update_status": self.updater.status(),
            }

    def update_status(self):
        return self.updater.status()

    def check_for_updates(self):
        if not self._enable_auto_restart:
            return {"ok": False, "error": "Оновлення доступні лише у Windows-програмі"}
        self.updater.check(force=True)
        return self.updater.status()

    def apply_update(self):
        if not self._enable_auto_restart:
            return {"ok": False, "error": "Автооновлення недоступне в цьому режимі"}
        patch = self.updater.patch_path()
        if not patch:
            return {"ok": False, "error": "Перевірений патч ще не завантажено"}
        pythonw = self.root / "runtime" / "pythonw.exe"
        helper = self.root / "backend" / "update_helper.py"
        if not pythonw.is_file() or not helper.is_file():
            return {"ok": False, "error": "Не знайдено локальний модуль оновлення"}
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            [
                str(pythonw), str(helper),
                "--pid", str(os.getpid()),
                "--root", str(self.root),
                "--patch", str(patch),
            ],
            cwd=str(self.root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
        threading.Timer(0.8, lambda: os._exit(0)).start()
        return {"ok": True, "message": "Vector Radio закриється, встановить патч і запуститься знову"}

    def pilot_hour(self, reference_iso=None):
        settings = self.db.settings()
        if str(settings.get("pilot_clock_enabled", "1")) != "1":
            return {"ok": True, "enabled": False, "segments": []}
        time_context = self.context_engine.time_context(reference_iso, settings)
        rundown = self.context_engine.pilot_clock.rundown(
            time_context.iso,
            settings.get("responsible_editor", "").strip(),
        )
        events = self.db.rundown_events(rundown["hour_key"])
        by_slot = {segment["slot_id"]: segment for segment in rundown["segments"]}
        for segment in rundown["segments"]:
            segment["items"] = []
        for event in events:
            try:
                plan = json.loads(event.get("plan_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                plan = {}
            item = {
                "id": event.get("id"),
                "slot_id": event.get("slot_id", ""),
                "planned_for": event.get("planned_for", ""),
                "aired_at": event.get("aired_at", ""),
                "timing_error_seconds": event.get("timing_error_seconds"),
                "timing_status": event.get("timing_status", "planned"),
                "content_type": event.get("content_type", ""),
                "current_track_id": event.get("current_track_id"),
                "next_track_id": event.get("next_track_id"),
                "verification_status": plan.get("verification_status", ""),
                "thesis": plan.get("thesis", ""),
                "fallback": plan.get("fallback", ""),
            }
            segment = by_slot.get(event.get("slot_id"))
            if segment is not None:
                segment["items"].append(item)
        hard_events = [event for event in events if event.get("hard_time")]
        aired_hard_events = [event for event in hard_events if event.get("aired_at")]
        on_time = [
            event for event in aired_hard_events
            if event.get("timing_status") == "on_time"
        ]
        return {
            "ok": True,
            **rundown,
            "events": events,
            "metrics": {
                "planned_events": len(events),
                "aired_events": sum(bool(event.get("aired_at")) for event in events),
                "hard_points_aired": len(aired_hard_events),
                "hard_points_on_time": len(on_time),
                "hard_point_accuracy_percent": (
                    round(len(on_time) / len(aired_hard_events) * 100, 1)
                    if aired_hard_events else None
                ),
            },
        }

    def emergency_protocol(self, event_type, details=None):
        details = dict(details or {})
        if not details.get("responsible_editor"):
            details["responsible_editor"] = self.db.settings().get(
                "responsible_editor", ""
            )
        return self.broadcast_safety.protocol(event_type, details)

    def record_broadcast_event(self, event_type, details=None):
        return self.emergency_protocol(event_type, details)

    def queue_correction(
        self, original, corrected, source_url, source_title, editor="",
    ):
        responsible_editor = str(editor or "").strip() or self.db.settings().get(
            "responsible_editor", ""
        )
        return self.broadcast_safety.correction(
            original, corrected, source_url, source_title, responsible_editor,
        )

    def resolve_broadcast_event(self, event_id, status="resolved"):
        status = str(status or "resolved").strip().casefold()
        if status not in {"resolved", "aired", "rejected"}:
            return {"ok": False, "error": "Недозволений статус події"}
        self.db.resolve_broadcast_event(
            int(event_id), status, datetime.now(timezone.utc).isoformat()
        )
        return {"ok": True, "status": status}

    def broadcast_safety_status(self):
        return self.broadcast_safety.status()

    def _ai_library_tracks(self):
        tracks = []
        for track in self.db.tracks():
            if str(track.get("library_source") or "") != "ai":
                continue
            local_path = str(track.get("local_path") or "").strip()
            if not local_path or not (self.root / local_path).is_file():
                continue
            if float(track.get("match_score") or 0) < 0.75:
                continue
            enriched = dict(track)
            try:
                enriched["file_size_bytes"] = (self.root / local_path).stat().st_size
            except OSError:
                enriched["file_size_bytes"] = 0
            tracks.append(enriched)
        tracks.sort(key=lambda track: int(track.get("id") or 0), reverse=True)
        return [
            {**track, "rank": position}
            for position, track in enumerate(tracks, start=1)
        ]

    def _settings_payload(self):
        settings = self.db.settings()
        nvidia_key_count = len(self._nvidia_keys(settings))
        settings["nvidia_key_count"] = nvidia_key_count
        settings["nvidia_key_detected"] = nvidia_key_count > 0
        settings["secondary_key_detected"] = bool(self._secondary_key(settings))
        settings["youtube_key_detected"] = bool(self._youtube_key(settings))
        # The WebView only needs presence flags. Never send stored secrets back
        # to JavaScript or make them visible again after the initial import.
        settings["nvidia_api_key"] = ""
        settings["nvidia_api_keys"] = ""
        settings["secondary_api_key"] = ""
        settings["youtube_api_key"] = ""
        try:
            from .tts_styletts import styletts_status

            settings["styletts_status"] = styletts_status()
        except Exception as exc:
            settings["styletts_status"] = {
                "available": False,
                "model_cached": False,
                "ready": False,
                "cuda": False,
                "device": "cpu",
                "error": str(exc),
            }
        return settings

    def scan_music(self):
        # downloads/ is the primary test library; music/ remains supported for
        # files the user adds manually. Source order controls duplicate priority.
        source_dirs = [self.root / "downloads", self.root / "music"]
        for directory in source_dirs:
            directory.mkdir(parents=True, exist_ok=True)
        extensions = {".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".webm"}
        files = [
            path
            for directory in source_dirs
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.suffix.casefold() in extensions
        ]
        tracks = self.db.tracks()
        known_by_path = {
            str(track.get("local_path") or "").replace("\\", "/"): track
            for track in tracks if track.get("local_path")
        }
        self.db.clear_local_paths()
        assigned = set()
        matched = []
        imported = []
        unmatched = []
        try:
            from mutagen import File as MutagenFile
        except ImportError:
            MutagenFile = None
        for path in files:
            artist = title = ""
            duration_ms = 0
            bpm = 0
            if MutagenFile:
                try:
                    audio_file = MutagenFile(path, easy=True)
                    if audio_file:
                        artist = (audio_file.get("artist") or [""])[0]
                        title = (audio_file.get("title") or [""])[0]
                        duration_ms = round(float(getattr(audio_file.info, "length", 0)) * 1000)
                        bpm_value = (audio_file.get("bpm") or audio_file.get("tbpm") or [0])[0]
                        try:
                            bpm = float(bpm_value or 0)
                        except (TypeError, ValueError):
                            bpm = 0
                except Exception:
                    pass
            clean_stem = re.sub(r"\s*\[[A-Za-z0-9_-]{6,}\]\s*$", "", path.stem).strip()
            title = (title or clean_stem).strip()
            artist = (artist or "").strip()
            if not artist and " - " in title:
                artist, title = (part.strip() for part in title.split(" - ", 1))
            display = f"{artist} {title}".strip() or clean_stem
            best = None
            for track in tracks:
                if artist and title:
                    score, _, _ = match_score(track["artist"], track["title"], title, artist)
                else:
                    score, _, _ = match_score(track["artist"], track["title"], display, "")
                if best is None or score > best[0]:
                    best = (score, track)
            relative = path.relative_to(self.root).as_posix()
            known = known_by_path.get(relative)
            if known and known["id"] not in assigned:
                self.db.update_track(
                    known["id"], local_path=relative,
                    duration_ms=duration_ms or known.get("duration_ms", 0),
                    bpm=bpm or known.get("bpm", 0),
                )
                assigned.add(known["id"])
                matched.append({
                    "file": relative,
                    "track_id": known["id"],
                    "artist": known["artist"],
                    "title": known["title"],
                    "score": 1.0,
                })
            elif best and best[0] >= 0.72 and best[1]["id"] not in assigned:
                self.db.update_track(
                    best[1]["id"], local_path=relative,
                    duration_ms=duration_ms, bpm=bpm,
                )
                assigned.add(best[1]["id"])
                matched.append({"file": relative, "track_id": best[1]["id"], "artist": best[1]["artist"], "title": best[1]["title"], "score": best[0]})
            elif best and best[0] >= 0.72:
                # The same track exists in both source folders. Keep the first
                # one (downloads/ has priority) instead of matching a wrong row.
                unmatched.append(relative)
            else:
                local_track = self.db.add_local_track(
                    artist or "Невідомий виконавець",
                    title or clean_stem or path.name,
                    relative,
                )
                self.db.update_track(
                    local_track["id"], duration_ms=duration_ms, bpm=bpm
                )
                local_track["duration_ms"] = duration_ms
                local_track["bpm"] = bpm
                tracks.append(local_track)
                assigned.add(local_track["id"])
                imported.append({
                    "file": relative,
                    "track_id": local_track["id"],
                    "artist": local_track["artist"],
                    "title": local_track["title"],
                })
        source_counts = {
            directory.name: sum(1 for path in files if directory in path.parents)
            for directory in source_dirs
        }
        return {
            "ok": True,
            "files": len(files),
            "matched": len(matched),
            "imported": len(imported),
            "playable": len(matched) + len(imported),
            "sources": source_counts,
            "unmatched": unmatched,
            "matches": matched,
            "imports": imported,
            "tracks": self._ai_library_tracks(),
            "all_tracks": self.db.tracks(),
        }

    def _file_keys(self):
        """Read supported credentials from raw key files or pasted SDK examples.

        ``apitest.txt`` may contain complete Python examples instead of one key
        per line. Extracting only recognised token shapes keeps code, comments,
        and model names out of the provider list. Values are never logged.
        """
        values = []
        patterns = (
            r"nvapi-[A-Za-z0-9_-]+",
            r"sk-or-[A-Za-z0-9_-]+",
            r"gsk_[A-Za-z0-9_-]+",
            r"AIza[A-Za-z0-9_-]+",
        )
        for name in ("api.txt", "apitest.txt"):
            key_file = self.root / name
            if not key_file.exists():
                continue
            try:
                raw = key_file.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            for pattern in patterns:
                values.extend(re.findall(pattern, raw))
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    def _nvidia_keys(self, settings=None):
        settings = settings or self.db.settings()
        values = []
        configured = settings.get("nvidia_api_key", "").strip()
        if configured:
            values.append(configured)
        stored_pool = settings.get("nvidia_api_keys", "[]")
        try:
            parsed_pool = json.loads(stored_pool or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed_pool = str(stored_pool or "").splitlines()
        if isinstance(parsed_pool, list):
            values.extend(
                str(value).strip() for value in parsed_pool
                if str(value).strip().startswith("nvapi-")
            )
        values.extend(key for key in self._file_keys() if key.startswith("nvapi-"))
        return list(dict.fromkeys(values))

    def _nvidia_key(self, settings=None):
        return next(iter(self._nvidia_keys(settings)), "")

    def _secondary_key(self, settings=None):
        settings = settings or self.db.settings()
        configured = settings.get("secondary_api_key", "").strip()
        if configured:
            return configured
        return next((key for key in self._file_keys() if key.startswith("sk-or-")), "")

    def _load_provider_health(self):
        """Load persisted provider circuit breakers without storing secrets."""
        try:
            payload = json.loads(self.db.settings().get("provider_health", "{}") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(key): dict(value)
            for key, value in payload.items()
            if isinstance(value, dict)
        }

    def _save_provider_health(self):
        with self._provider_health_lock:
            payload = json.dumps(self._provider_health, ensure_ascii=False)
        self.db.save_settings({"provider_health": payload})

    @staticmethod
    def _provider_health_key(spec):
        raw = "\0".join((
            str(spec.get("provider_type") or spec.get("name") or "ai"),
            str(spec.get("url") or ""),
            str(spec.get("model") or ""),
            str(spec.get("key") or ""),
        ))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _provider_label(spec):
        name = str(spec.get("name") or "AI")
        url = str(spec.get("url") or "").casefold()
        provider_type = str(spec.get("provider_type") or name).casefold()
        if provider_type == "nvidia" or name.casefold().startswith("nvidia"):
            suffix = name.removeprefix("nvidia")
            return f"NVIDIA{suffix}"
        if "openrouter.ai" in url:
            return "OpenRouter"
        return name

    @staticmethod
    def _provider_failure_policy(response):
        kind = str(response.get("error_kind") or "").casefold()
        raw = str(response.get("error") or "").casefold()
        if not kind:
            if "http 401" in raw or "http 403" in raw:
                kind = "auth"
            elif "http 402" in raw or "credit" in raw:
                kind = "credit"
            elif "http 429" in raw or "rate limit" in raw:
                kind = "rate_limit"
            elif "timed out" in raw or "timeout" in raw:
                kind = "timeout"
            elif "empty content" in raw or "invalid" in raw:
                kind = "invalid_response"
            else:
                kind = "network"
        policies = {
            "auth": ("disabled", 0, "ключ відхилено; імпортуйте новий ключ"),
            "credit": ("disabled", 0, "немає доступного ліміту або кредитів"),
            "rate_limit": ("cooldown", 15 * 60, "досягнуто ліміт запитів"),
            "timeout": ("cooldown", 5 * 60, "сервіс не відповів вчасно"),
            "server": ("cooldown", 5 * 60, "сервіс тимчасово недоступний"),
            "invalid_response": ("cooldown", 10 * 60, "отримано некоректну відповідь"),
            "request": ("cooldown", 30 * 60, "запит відхилено сервісом"),
            "network": ("cooldown", 5 * 60, "немає стабільного з’єднання із сервісом"),
        }
        return policies.get(kind, policies["network"])

    def _provider_record(self, spec):
        key = self._provider_health_key(spec)
        expired = False
        with self._provider_health_lock:
            record = dict(self._provider_health.get(key) or {})
            if (
                record.get("state") == "cooldown"
                and float(record.get("retry_at") or 0) <= time.time()
            ):
                self._provider_health.pop(key, None)
                record = {}
                expired = True
        if expired:
            self._save_provider_health()
        return key, record

    def _provider_available(self, spec):
        _key, record = self._provider_record(spec)
        return not record or record.get("state") not in {"disabled", "cooldown"}

    def _record_provider_failure(self, spec, response):
        state, delay, reason = self._provider_failure_policy(response)
        key = self._provider_health_key(spec)
        with self._provider_health_lock:
            previous = self._provider_health.get(key) or {}
            failures = int(previous.get("failures") or 0) + 1
            self._provider_health[key] = {
                "provider": self._provider_label(spec),
                "state": state,
                "reason": reason,
                "retry_at": time.time() + delay if delay else 0,
                "failures": failures,
            }
        self._save_provider_health()
        LOGGER.warning(
            "AI provider circuit breaker: provider=%s state=%s reason=%s",
            self._provider_label(spec), state, reason,
        )
        return reason

    def _record_provider_success(self, spec):
        key = self._provider_health_key(spec)
        changed = False
        with self._provider_health_lock:
            if key in self._provider_health:
                self._provider_health.pop(key, None)
                changed = True
        if changed:
            self._save_provider_health()

    def _reset_provider_health(self, provider_names=None):
        names = {str(value).casefold() for value in (provider_names or [])}
        with self._provider_health_lock:
            if not names:
                self._provider_health.clear()
            else:
                self._provider_health = {
                    key: value for key, value in self._provider_health.items()
                    if not any(
                        str(value.get("provider") or "").casefold().startswith(name)
                        for name in names
                    )
                }
        self._save_provider_health()

    def _provider_chat_completion(
        self, spec, system_prompt, request_text, temperature, top_p, max_tokens,
    ):
        label = self._provider_label(spec)
        _key, record = self._provider_record(spec)
        if record and record.get("state") in {"disabled", "cooldown"}:
            return {
                "provider": spec.get("name", label),
                "candidate": "",
                "error": f"{label}: {record.get('reason') or 'тимчасово недоступний'}",
                "public_error": f"{label}: {record.get('reason') or 'тимчасово недоступний'}",
                "error_kind": "circuit_open",
                "skipped": True,
            }
        response = _chat_completion(
            spec, system_prompt, request_text, temperature, top_p, max_tokens,
        )
        if response.get("error"):
            technical_error = str(response.get("error") or "")
            reason = self._record_provider_failure(spec, response)
            response["technical_error"] = technical_error
            response["public_error"] = f"{label}: {reason}"
            response["error"] = response["public_error"]
        else:
            self._record_provider_success(spec)
        return response

    def provider_health(self):
        providers = self._ai_providers(self.db.settings())
        snapshots = []
        persist_expired = False
        for spec in providers:
            key = self._provider_health_key(spec)
            with self._provider_health_lock:
                before = key in self._provider_health
            _key, record = self._provider_record(spec)
            if before and not record:
                persist_expired = True
            retry = max(0, round(float(record.get("retry_at") or 0) - time.time()))
            snapshots.append({
                "name": str(spec.get("name") or "AI"),
                "label": self._provider_label(spec),
                "state": str(record.get("state") or "ready"),
                "message": str(record.get("reason") or "доступний"),
                "retry_in_seconds": retry,
            })
        if persist_expired:
            self._save_provider_health()
        return snapshots

    def _ai_providers(self, settings=None):
        settings = settings or self.db.settings()
        providers = []
        primary = str(settings.get("primary_ai_provider", "nvidia")).strip().casefold()
        nvidia_keys = self._nvidia_keys(settings)
        secondary_enabled = str(
            settings.get("secondary_api_enabled", "0")
        ).strip().casefold() in {"1", "true", "yes", "on"}
        secondary_url = settings.get("secondary_api_url", "").strip()
        secondary_key = self._secondary_key(settings)
        secondary_model = settings.get("secondary_model", "").strip()

        nvidia_providers = []
        for index, nvidia_key in enumerate(nvidia_keys, start=1):
            nvidia_providers.append({
                "name": "nvidia" if index == 1 else f"nvidia-{index}",
                "provider_type": "nvidia",
                "url": "https://integrate.api.nvidia.com/v1/chat/completions",
                "key": nvidia_key,
                "model": settings.get("nvidia_model") or DEFAULTS["nvidia_model"],
                # Hosted NIM can spend significant time warming/queuing a large
                # model and completing the full 20-track JSON plan. All
                # credentials run in parallel, so a 200-second per-key
                # limit improves reliability without multiplying total wait.
                "timeout_seconds": 200,
            })

        secondary_provider = None
        if secondary_enabled and secondary_key:
            if not secondary_url and secondary_key.startswith("sk-or-"):
                secondary_url = "https://openrouter.ai/api/v1/chat/completions"
            if not secondary_model and secondary_key.startswith("sk-or-"):
                secondary_model = DEFAULTS["secondary_model"]
            if secondary_url and secondary_model:
                secondary_provider = {
                    "name": "secondary",
                    "provider_type": "secondary",
                    "url": secondary_url,
                    "key": secondary_key,
                    "model": secondary_model,
                    "timeout_seconds": 25,
                }

        if secondary_provider and nvidia_providers and "deepseek" in secondary_model.casefold():
            providers.append(secondary_provider)
            providers.extend(nvidia_providers)
        elif primary == "secondary":
            if secondary_provider:
                providers.append(secondary_provider)
            providers.extend(nvidia_providers)
        else:
            providers.extend(nvidia_providers)
            if secondary_provider:
                providers.append(secondary_provider)

        return providers

    @staticmethod
    def _role_ordered_providers(providers, preferred_name):
        preferred = str(preferred_name or "").strip().casefold()
        if not preferred or preferred in {"auto", "parallel", "all"}:
            return providers
        selected = [
            item for item in providers
            if str(item.get("name") or "").casefold() == preferred
            or str(item.get("provider_type") or "").casefold() == preferred
        ]
        if not selected:
            return providers
        return selected + [item for item in providers if item not in selected]

    def _ai_providers_for_tracks(self, settings=None):
        settings = settings or self.db.settings()
        providers = self._ai_providers(settings)
        return self._role_ordered_providers(
            providers, settings.get("dj_ai_provider", "parallel")
        )

    def _ai_providers_for_pronunciation(self, settings=None):
        """Select a provider for the isolated phonetic spelling pass."""
        settings = settings or self.db.settings()
        providers = self._ai_providers(settings)
        return self._role_ordered_providers(
            providers, settings.get("pronunciation_ai_provider", "auto")
        )

    def _ai_providers_for_intro(self, settings=None):
        settings = settings or self.db.settings()
        providers = self._role_ordered_providers(
            self._ai_providers(settings), settings.get("host_ai_provider", "secondary")
        )
        deepseek = [
            item for item in providers
            if "deepseek" in str(item.get("model") or "").casefold()
        ]
        if deepseek:
            others = [item for item in providers if item not in deepseek]
            providers = deepseek + others

        # Extra credentials for the same endpoint/model improve DJ throughput,
        # but asking every key for the same spoken intro only wastes time. Keep
        # one credential per model here; DeepSeek remains first for hosting.
        unique = []
        seen_models = set()
        for item in providers:
            model_key = (
                str(item.get("url") or "").casefold(),
                str(item.get("model") or "").casefold(),
            )
            if model_key in seen_models:
                continue
            seen_models.add(model_key)
            unique.append(item)
        return unique

    def verify_deepseek_response(self):
        """Check whether a configured Deepseek-style secondary provider responds."""
        settings = self.db.settings()
        providers = self._ai_providers(settings)
        for spec in providers:
            if "deepseek" in str(spec.get("model") or "").casefold():
                system_prompt = (
                    "Ти музичний помічник. Поверни тільки JSON:"
                    " {\"ok\": true}"
                )
                request_text = json.dumps(
                    {"purpose": "deepseek connectivity test"},
                    ensure_ascii=False,
                )
                response = self._provider_chat_completion(
                    spec, system_prompt, request_text, 0.0, 0.0, 60,
                )
                if response.get("error"):
                    return {
                        "ok": False,
                        "provider": spec["name"],
                        "model": spec["model"],
                        "error": response["error"],
                    }
                try:
                    payload = _json_object(response.get("candidate", ""))
                    if payload.get("ok") is True:
                        return {
                            "ok": True,
                            "provider": spec["name"],
                            "model": spec["model"],
                        }
                    return {
                        "ok": False,
                        "provider": spec["name"],
                        "model": spec["model"],
                        "error": "Unexpected response payload",
                        "candidate": response.get("candidate", ""),
                    }
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    return {
                        "ok": False,
                        "provider": spec["name"],
                        "model": spec["model"],
                        "error": f"Invalid JSON response: {exc}",
                        "candidate": response.get("candidate", ""),
                    }
        return {"ok": False, "error": "Deepseek provider not configured"}

    def benchmark_ai_providers(
        self,
        station_style="",
        intro_style="straight_radio",
        intro_seconds=12,
    ):
        """Compare configured AI providers on DJ search and host copy quality.

        The benchmark intentionally does not download audio. It only evaluates
        provider outputs using the same validators used by the live radio flow.
        """
        settings = dict(self.db.settings())
        if (station_style or "").strip():
            settings["station_prompt"] = station_style.strip()
        providers = self._ai_providers(settings)
        if not providers:
            return {"ok": False, "error": "Не налаштовано AI-провайдерів для тесту"}

        tracks = self.db.tracks()
        if not tracks:
            return {"ok": False, "error": "Немає треків для тесту підводки"}
        current = tracks[0] if len(tracks) > 1 else None
        next_track = tracks[1] if len(tracks) > 1 else tracks[0]
        results = []

        for spec in providers:
            entry = {
                "provider": spec.get("name", ""),
                "model": spec.get("model", ""),
                "music_search": {"ok": False, "score": 0, "error": ""},
                "radio_host": {"ok": False, "score": 0, "spelling_score": 0, "error": ""},
                "total_score": 0,
            }
            try:
                plan = self._queue_search_plan(settings, providers=[spec])
                entry["music_search"] = {
                    "ok": True,
                    "score": float(plan.get("quality_score") or 0),
                    "provider": plan.get("provider", spec.get("name", "")),
                    "tracks": plan.get("tracks", [])[:5],
                    "track_count": len(plan.get("tracks", [])),
                    "similar_count": len(plan.get("similar_tracks", [])),
                    "error": "",
                }
            except Exception as exc:
                entry["music_search"]["error"] = str(exc)

            try:
                intro = self.make_intro(
                    next_track["id"],
                    current["id"] if current else None,
                    style=intro_style,
                    content_plan={
                        "content_type": "talk",
                        "style": intro_style,
                        "target_seconds": intro_seconds,
                        "word_min": 6,
                        "word_max": 24,
                        "mention_policy": "artist_and_title",
                    },
                    duration_seconds=intro_seconds,
                    store_track=False,
                    providers_override=[spec],
                )
                diagnostics = intro.get("provider_diagnostics", [])
                diagnostic_scores = [
                    float(item.get("score") or 0)
                    for item in diagnostics if item.get("ok")
                ]
                warnings = _ukrainian_copy_warnings(
                    intro.get("display_text", ""),
                    allow_time_digits=False,
                )
                spelling_score = max(0, 100 - len(warnings) * 10)
                host_score = (
                    max(diagnostic_scores) if diagnostic_scores and not intro.get("fallback")
                    else 0
                )
                entry["radio_host"] = {
                    "ok": bool(intro.get("ok")) and not intro.get("fallback"),
                    "score": host_score,
                    "spelling_score": spelling_score,
                    "provider": intro.get("provider", ""),
                    "intro": intro.get("display_text", ""),
                    "warnings": warnings,
                    "diagnostics": diagnostics,
                    "error": intro.get("provider_error", ""),
                }
            except Exception as exc:
                entry["radio_host"]["error"] = str(exc)

            entry["total_score"] = round(
                float(entry["music_search"].get("score") or 0) * 0.55
                + float(entry["radio_host"].get("score") or 0) * 0.35
                + float(entry["radio_host"].get("spelling_score") or 0) * 0.10,
                2,
            )
            results.append(entry)

        ranked = sorted(results, key=lambda item: item["total_score"], reverse=True)
        winner = ranked[0]["provider"] if ranked and ranked[0]["total_score"] > 0 else ""
        return {
            "ok": True,
            "station_style": settings.get("station_prompt", ""),
            "winner": winner,
            "results": ranked,
        }

    def _youtube_key(self, settings=None):
        settings = settings or self.db.settings()
        configured = settings.get("youtube_api_key", "").strip()
        if configured:
            return configured
        return next((key for key in self._file_keys() if key.startswith("AIza")), "")

    def import_chart(self, text):
        tracks = parse_chart(text)
        if not tracks:
            return {"ok": False, "error": "Не знайдено рядків у форматі: 1. Виконавець - Назва"}
        self.db.replace_tracks(tracks)
        return {"ok": True, "count": len(tracks), "tracks": self.db.tracks()}

    def import_api_text(self, text):
        """Validate a pasted TXT and store recognised provider keys locally.

        Labels are optional, so both ``NVIDIA_API_KEY=nvapi-...`` and a raw
        token work. Secrets are never returned to the WebView or written to a
        log. At least one supported completion provider is required.
        """
        raw = str(text or "")
        if len(raw.encode("utf-8", errors="ignore")) > 65536:
            return {"ok": False, "error": "API TXT завеликий. Максимум — 64 КБ."}
        patterns = {
            "NVIDIA": r"nvapi-[A-Za-z0-9_-]{16,}",
            "OpenRouter": r"sk-or-(?:v1-)?[A-Za-z0-9_-]{16,}",
            "YouTube": r"AIza[A-Za-z0-9_-]{20,}",
        }
        found = {
            provider: list(dict.fromkeys(re.findall(pattern, raw)))
            for provider, pattern in patterns.items()
        }
        completion_providers = [
            provider for provider in ("NVIDIA", "OpenRouter") if found[provider]
        ]
        if not completion_providers:
            return {
                "ok": False,
                "error": (
                    "Не знайдено ключ NVIDIA (nvapi-…) або OpenRouter "
                    "(sk-or-v1-…). Вставте повний ключ без лапок."
                ),
            }
        values = {}
        if found["NVIDIA"]:
            values["nvidia_api_key"] = found["NVIDIA"][0]
            values["nvidia_api_keys"] = json.dumps(found["NVIDIA"])
        if found["OpenRouter"]:
            values.update({
                "secondary_api_key": found["OpenRouter"][0],
                "secondary_api_enabled": "1",
                "secondary_api_url": "https://openrouter.ai/api/v1/chat/completions",
                "secondary_model": DEFAULTS["secondary_model"],
            })
        if found["YouTube"]:
            values["youtube_api_key"] = found["YouTube"][0]
        self.db.save_settings(values)
        # Re-importing a credential is the explicit user action that closes a
        # permanent auth/credit circuit and allows that provider to be tested.
        self._reset_provider_health(completion_providers)
        providers = completion_providers + (["YouTube"] if found["YouTube"] else [])
        provider_counts = {
            provider: len(found[provider]) for provider in providers
        }
        return {
            "ok": True,
            "providers": providers,
            "provider_counts": provider_counts,
            "settings": self._settings_payload(),
        }

    def import_library_file(self):
        path = self.root / "library-import.txt"
        if not path.exists():
            return {"ok": False, "error": "Файл library-import.txt не знайдено"}
        try:
            return self.import_chart(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            return {"ok": False, "error": f"Не вдалося прочитати файл: {exc}"}

    def save_settings(self, values):
        values = dict(values or {})
        if "youtube_auth_browser" in values:
            browser = str(
                values.get("youtube_auth_browser") or "off"
            ).strip().casefold()
            values["youtube_auth_browser"] = (
                browser if browser in {"chrome", "edge", "firefox"} else "off"
            )
        # The WebView never receives the credential pool and therefore cannot
        # replace or erase it. Only the validated TXT importer may update it.
        values.pop("nvidia_api_keys", None)
        # Empty password fields mean "keep the stored key", not "erase it".
        # Credential replacement happens through import_api_text or by entering
        # a non-empty value in the legacy advanced form.
        for key in ("nvidia_api_key", "secondary_api_key", "youtube_api_key"):
            if key in values and not str(values[key] or "").strip():
                values.pop(key)
        previous = self.db.settings()
        self.db.save_settings(values)
        reset_providers = []
        if str(values.get("nvidia_api_key") or "").strip() and (
            str(values.get("nvidia_api_key")) != str(previous.get("nvidia_api_key"))
        ):
            reset_providers.append("nvidia")
        if str(values.get("secondary_api_key") or "").strip() and (
            str(values.get("secondary_api_key")) != str(previous.get("secondary_api_key"))
        ):
            reset_providers.append("openrouter")
        if reset_providers:
            self._reset_provider_health(reset_providers)
        if (
            "use_styletts" in values
            and str(values["use_styletts"]) != str(previous.get("use_styletts", "1"))
        ):
            # Prepared transitions contain engine-specific audio paths. Rebuild
            # them after an engine switch instead of airing stale cached audio.
            self.db.invalidate_all_transitions()
        previous_prompt_norm = self._normalized_station_prompt(previous.get("station_prompt"))
        new_prompt_norm = self._normalized_station_prompt(values.get("station_prompt"))
        prompt_changed = "station_prompt" in values and new_prompt_norm != previous_prompt_norm
        result = {"ok": True, "prompt_changed": prompt_changed}
        log_lines = [
            f"station_prompt в payload: {'так' if 'station_prompt' in values else 'ні'}",
            f"попередній (норм.): {previous_prompt_norm[:80] or '(порожньо)'}",
            f"новий (норм.): {new_prompt_norm[:80] or '(порожньо)'}",
            f"prompt_changed: {prompt_changed}",
        ]
        if prompt_changed:
            try:
                self._clear_discovery_plan_cache()
                current_prompt = new_prompt_norm
                ai_rows_before = sum(
                    1 for t in self.db.tracks() if str(t.get("library_source") or "") == "ai"
                )
                removed_rows = self.db.purge_ai_library()
                removed_files = self._clear_ai_cache_files()
                # Mark the new prompt as already handled so the next launch does not
                # purge the freshly-built library again as if the style just changed.
                self.db.save_settings({
                    "ai_playlist_prompt": current_prompt,
                    "ai_previous_playlist": "[]",
                })
                ai_rows_after = sum(
                    1 for t in self.db.tracks() if str(t.get("library_source") or "") == "ai"
                )
                LOGGER.info(
                    "AI library purged after station style change: removed_tracks=%s, removed_files=%s",
                    removed_rows, removed_files,
                )
                log_lines.append(
                    f"purge OK: ai_rows {ai_rows_before}->{ai_rows_after}, "
                    f"removed_rows={removed_rows}, removed_files={removed_files}"
                )
                result["tracks"] = self._ai_library_tracks()
                result["radio_queue"] = self.radio_queue.refresh()
            except Exception as exc:
                # The new station_prompt is already persisted above; a failed
                # purge must not make save_settings raise and look like the
                # style change itself never happened.
                LOGGER.exception("AI library purge after station style change failed")
                log_lines.append(f"purge FAILED: {exc}")
        ai_rows_now = sum(
            1 for t in self.db.tracks() if str(t.get("library_source") or "") == "ai"
        )
        log_lines.append(
            f"ai_rows зараз: {ai_rows_now}, валідних треків: {len(self._ai_library_tracks())}, "
            f"буфер (queue_size): {self.db.settings().get('queue_size', DEFAULTS['queue_size'])}"
        )
        if prompt_changed:
            if self._enable_auto_restart:
                log_lines.append("Перезапускаю LUMEN Radio, щоб застосувати новий стиль…")
                result["restarting"] = True
                self._schedule_restart()
            else:
                log_lines.append("Автоперезапуск вимкнено (не desktop-режим) — застосовано без рестарту.")
        result["log"] = "\n".join(log_lines)
        result["settings"] = self._settings_payload()
        return result

    def _schedule_restart(self, delay=1.2):
        """Relaunch the whole app after a station-style change instead of
        trying to reconcile in-process caches (queue threads, AI provider
        state, any stray duplicate window) with the new style."""
        def worker():
            time.sleep(delay)
            try:
                self.shutdown()
            except Exception:
                LOGGER.exception("Failed to stop Vector Radio cleanly before restart")
            try:
                self._launch_restart_helper()
            except Exception:
                LOGGER.exception("Failed to start Vector Radio restart helper")
            os._exit(0)

        threading.Thread(target=worker, daemon=True, name="lumen-restart").start()

    def _launch_restart_helper(self):
        helper = self.root / "backend" / "restart_helper.py"
        if not helper.is_file():
            raise FileNotFoundError(helper)
        interpreter = Path(sys.executable)
        console_python = interpreter.with_name("python.exe")
        if console_python.is_file():
            interpreter = console_python
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(
            [
                str(interpreter), str(helper),
                "--pid", str(os.getpid()),
                "--root", str(self.root),
            ],
            cwd=str(self.root),
            creationflags=creationflags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def radio_queue_status(self):
        return self.radio_queue.status()

    def request_radio_queue_refill(self):
        return self.radio_queue.request_refill()

    def refresh_ai_library(self):
        self._clear_discovery_plan_cache()
        queue = self.radio_queue.refresh()
        return {
            "ok": True,
            "tracks": self._ai_library_tracks(),
            "radio_queue": queue,
        }

    def generate_top_tracks_with_intros(
        self,
        station_style,
        limit=10,
        intro_style="straight_radio",
        intro_seconds=15,
    ):
        settings = dict(self.db.settings())
        settings["station_prompt"] = station_style
        search_plan = self._queue_search_plan(settings)
        jobs = []
        for index, recommendation in enumerate(search_plan["tracks"][:limit], start=1):
            track = self.db.add_local_track(
                recommendation["artist"], recommendation["title"],
                "",
            )
            self.db.update_track(
                track["id"], library_source="ai-candidate", match_score=0.0,
            )
            jobs.append((index, track))

        def generate(job):
            index, track = job
            try:
                intro = self.make_intro(
                    track["id"],
                    style=intro_style,
                    content_plan={
                        "content_type": "talk",
                        "style": intro_style,
                        "target_seconds": intro_seconds,
                        "word_min": max(10, round(float(intro_seconds) * 1.8)),
                        "word_max": max(18, round(float(intro_seconds) * 3.0)),
                    },
                    duration_seconds=intro_seconds,
                    store_track=False,
                )
                display_text = str(intro.get("display_text") or "").strip()
                fallback = bool(intro.get("fallback"))
                intro_ok = bool(intro.get("ok", True)) and bool(display_text)
                return index, {
                    "artist": track["artist"],
                    "title": track["title"],
                    "intro": display_text,
                    "provider": intro.get("provider", ""),
                    "ok": intro_ok,
                    "fallback": fallback,
                    "error": intro.get("provider_error") or intro.get("error") or "",
                }
            except Exception as exc:
                return index, {
                    "artist": track["artist"],
                    "title": track["title"],
                    "intro": "",
                    "provider": "",
                    "ok": False,
                    "fallback": False,
                    "error": str(exc),
                }

        indexed_results = {}
        if jobs:
            with ThreadPoolExecutor(max_workers=min(10, len(jobs))) as executor:
                futures = {
                    executor.submit(generate, job): job[0]
                    for job in jobs
                }
                for future in as_completed(futures):
                    index, result = future.result()
                    indexed_results[index] = result
        results = [indexed_results[index] for index, _track in jobs]
        passed = sum(bool(item.get("ok")) for item in results)
        return {
            "ok": len(results) == int(limit) and passed == int(limit),
            "station_style": station_style,
            "tracks": results,
            "passed": passed,
            "requested": int(limit),
            "source_provider": search_plan.get("provider", ""),
        }

    def advance_radio_queue(self, finished_track_id):
        return self.radio_queue.advance(int(finished_track_id))

    def reseed_radio_queue(self, preferred_track_id):
        return self.radio_queue.reseed(int(preferred_track_id))

    def _queue_search_plan(self, settings, excluded_tracks=None, providers=None):
        station_prompt = settings.get("station_prompt", DEFAULTS["station_prompt"]).strip()
        station_prompt_search = self._translated_station_prompt(station_prompt, settings)
        romantic_evening = self._is_romantic_evening_profile(station_prompt)
        providers = list(providers) if providers is not None else self._ai_providers_for_tracks(settings)
        search_max_tokens = _normalized_ai_max_tokens(settings)
        system_prompt = """Ти музичний директор радіо. Поверни тільки JSON:
{"tracks":[{"artist":"...","title":"...","genre":"..."}],
 "similarTracks":[{"artist":"...","title":"...","genre":"..."}],
 "targetMood":["..."],"avoid":["..."]}

Для кожного треку поле genre обов'язкове: вкажи 1-3 точні канонічні жанри.
Не називай жанр зі стилю станції автоматично — вкажи реальний жанр саме треку.

Спочатку самостійно добери рівно 10 конкретних треків, які відповідають стилю станції.
Потім додай до них 10 схожих пісень, які підходять до першого списку або до загального настрою.
Називай лише реальні офіційно видані пісні з точною канонічною назвою та виконавцем.
Перед додаванням кожної пари мовчки перевір, що саме цей виконавець справді випускав
саме цю пісню. Не перенось відому назву до схожого артиста й не вгадуй пісню за жанром.
Якщо пам’ятаєш артиста, але не впевнений у точній назві його композиції — обери іншу пару.
Пріоритет — відомі треки й виконавці зі сформованою аудиторією: хіти, класика жанру
або впізнавані культові композиції. Не пропонуй анонімні royalty-free записи,
AI-generated music, мікси, плейлисти, кавери чи вигадані назви. Якщо не впевнений,
що трек існує, не додавай його. Не створюй пошукові фрази й не вигадуй URL.
Якщо стиль просить російський або український alternative/alt rock, добирай переважно
сучаснішу хвилю дві тисячі десятих — дві тисячі двадцятих і не перетворюй ефір
на музейний росрок: не став Кино/Цоя, ДДТ, Алису, Аквариум, Наутилус, Сектор Газа,
Би-2 та подібний старий канон, якщо користувач прямо цього не попросив.
Для такого регіонального запиту не підмінюй основний список західним англомовним
darkwave чи post-punk: українська та російськомовна сцена мають становити основу.
Не став одного й того самого виконавця поруч у списку.
Не додавай поле reason: воно витрачає токени й не допомагає пошуку. Не повторюй
композиції з excludeTracks.
Не додавай пояснень поза JSON."""
        if romantic_evening:
            seed_lines = "\n".join(
                f'- {seed["artist"]} — {seed["title"]}'
                for seed in ROMANTIC_EVENING_SEED_TRACKS
            )
            system_prompt += f"""

Активний профіль: Romantic Evening.
Еталонний музичний простір:
{seed_lines}

Сама тема кохання НЕ робить пісню придатною. Вона також має бути стилістично
і поколіннєво близькою до еталонів. Для кожного треку додай компактні поля:
year (рік релізу), artistBreakthroughYear, mood (1-3 слова), popularityScore
(0-100) і retrievalClass (seed, discovery або experimental). Не вигадуй рік,
якщо не впевнений — поверни null. Розподіл основного списку: приблизно 70%
сусідів конкретних seed-треків, 20% genre+mood+era discovery і 10% обережних
експериментів. Заборонено більше одного треку одного виконавця в основному
списку. Не пропонуй Земфіру, ДДТ, Сплін, Бі-2/Би-2, Кино, Аквариум, Чайф,
Машину времени, Алису, Крематорий або Наутилус Помпилиус."""
        excluded_tracks = excluded_tracks or []
        excluded_keys = {
            (
                self._normalize_music_text(track.get("artist")),
                self._normalize_music_text(track.get("title")),
            )
            for track in excluded_tracks
            if str(track.get("artist") or "").strip()
            and str(track.get("title") or "").strip()
        }
        request_payload = {
            "stationPrompt": station_prompt_search,
            "excludeTracks": [
                {
                    "artist": str(track.get("artist") or "").strip(),
                    "title": str(track.get("title") or "").strip(),
                }
                for track in excluded_tracks[:60]
                if str(track.get("artist") or "").strip()
                and str(track.get("title") or "").strip()
            ],
        }
        if romantic_evening:
            request_payload["selectionProfile"] = {
                "name": "Romantic Evening",
                "minimumScore": ROMANTIC_EVENING_MIN_SCORE,
                "weights": {
                    "mood": 25, "genre": 25, "era": 20,
                    "artistGeneration": 15, "popularity": 15,
                },
                "seedTracks": list(ROMANTIC_EVENING_SEED_TRACKS),
                "retrievalMix": {"seed": 70, "discovery": 20, "experimental": 10},
            }
        request_text = json.dumps(request_payload, ensure_ascii=False)
        LOGGER.info(
            "Requesting AI music plan for station style: %s (search text: %s)",
            station_prompt, station_prompt_search,
        )
        avoid_legacy_regional_rock = _modern_regional_alt_rock_prompt(station_prompt)
        legacy_artist_keys = {
            self._normalize_music_text(artist)
            for artist in LEGACY_REGIONAL_ROCK_ARTISTS
        }
        romantic_blocked_artist_keys = {
            self._normalize_music_text(artist)
            for artist in ROMANTIC_EVENING_BLOCKED_ARTISTS
        }
        def blocked_artist_reason(artist):
            artist_key = self._normalize_music_text(artist)
            if avoid_legacy_regional_rock and artist_key in legacy_artist_keys:
                return "legacy-regional-rock"
            if romantic_evening and artist_key in romantic_blocked_artist_keys:
                return "romantic-evening-blacklist"
            return ""

        def normalized_plan(response):
            payload = _music_plan_object(response.get("candidate", ""))
            reject_pop = re.search(
                r"\bбез\s+(?:поп|попс)", station_prompt.casefold()
            ) is not None
            reject_covers = re.search(
                r"\bбез\s+кавер", station_prompt.casefold()
            ) is not None

            def reason_conflicts_with_style(reason):
                words = set(self._normalize_music_text(reason).split())
                return (
                    reject_pop
                    and any(word.startswith("поп") or word == "pop" for word in words)
                ) or (
                    reject_covers
                    and any(word.startswith("кавер") or word == "cover" for word in words)
                )

            seed_keys = {
                (
                    self._normalize_music_text(seed["artist"]),
                    self._normalize_music_text(seed["title"]),
                )
                for seed in ROMANTIC_EVENING_SEED_TRACKS
            }

            def scored_recommendation(value, artist, title, reason, genre):
                recommendation = {
                    "artist": artist,
                    "title": title,
                    "reason": reason,
                    "genre": genre,
                }
                if not romantic_evening:
                    return recommendation, None
                scoring_value = {**value, "artist": artist, "title": title}
                suitability = self._romantic_evening_suitability(scoring_value)
                retrieval_class = str(
                    value.get("retrievalClass") or value.get("retrieval_class") or ""
                ).strip().casefold()
                if retrieval_class not in {"seed", "discovery", "experimental"}:
                    candidate_key = (
                        self._normalize_music_text(artist),
                        self._normalize_music_text(title),
                    )
                    retrieval_class = "seed" if candidate_key in seed_keys else "discovery"
                recommendation.update({
                    "year": value.get("year") or value.get("releaseYear"),
                    "artist_breakthrough_year": value.get("artistBreakthroughYear"),
                    "mood": value.get("mood") or value.get("moods") or [],
                    "popularity_score": suitability["popularity"],
                    "suitability_score": suitability["score"],
                    "suitability_components": {
                        key: suitability[key]
                        for key in ("era", "artist", "genre", "mood", "popularity")
                    },
                    "retrieval_class": retrieval_class,
                })
                return recommendation, suitability

            recommendations = []
            similar_recommendations = []
            skipped = []
            seen = set()
            seen_titles = set()
            previous_artist_key = ""
            for value in payload.get("tracks", []) or payload.get("topSongs", []):
                if not isinstance(value, dict):
                    continue
                artist = str(value.get("artist") or "").strip()
                title = str(value.get("title") or "").strip()
                key = (
                    self._normalize_music_text(artist),
                    self._normalize_music_text(title),
                )
                reason = str(value.get("reason") or "").strip()
                genre = self._recommendation_genre(value)
                if (
                    not artist or not title or not all(key)
                    or key in seen or key in excluded_keys
                ):
                    continue
                if key[1] in seen_titles:
                    skipped.append({
                        "artist": artist,
                        "title": title,
                        "reason": "duplicate-title-other-artist",
                    })
                    continue
                if reason_conflicts_with_style(reason):
                    skipped.append({
                        "artist": artist,
                        "title": title,
                        "reason": "style-conflict",
                    })
                    continue
                block_reason = blocked_artist_reason(artist)
                if block_reason:
                    skipped.append({
                        "artist": artist,
                        "title": title,
                        "reason": block_reason,
                    })
                    continue
                recommendation, suitability = scored_recommendation(
                    value, artist, title, reason, genre,
                )
                genre_issue = (
                    "genre-conflict"
                    if romantic_evening and suitability["genre"] <= 0
                    else self._recommendation_style_issue(station_prompt, genre)
                    if not romantic_evening else ""
                )
                if genre_issue:
                    skipped.append({
                        "artist": artist,
                        "title": title,
                        "reason": genre_issue,
                        "genre": genre,
                    })
                    continue
                if (
                    romantic_evening
                    and suitability["score"] < ROMANTIC_EVENING_MIN_SCORE
                ):
                    skipped.append({
                        "artist": artist,
                        "title": title,
                        "reason": "below-romantic-evening-threshold",
                        "score": suitability["score"],
                    })
                    continue
                if key[0] == previous_artist_key:
                    skipped.append({
                        "artist": artist,
                        "title": title,
                        "reason": "adjacent-artist-repeat",
                    })
                    continue
                seen.add(key)
                seen_titles.add(key[1])
                recommendations.append(recommendation)
                previous_artist_key = key[0]
            similar_seen = set(seen)
            similar_seen_titles = set(seen_titles)
            previous_similar_artist_key = previous_artist_key
            for value in payload.get("similarTracks", []) or payload.get("similarSongs", []):
                if not isinstance(value, dict):
                    continue
                artist = str(value.get("artist") or "").strip()
                title = str(value.get("title") or "").strip()
                key = (
                    self._normalize_music_text(artist),
                    self._normalize_music_text(title),
                )
                reason = str(value.get("reason") or "").strip()
                genre = self._recommendation_genre(value)
                if (
                    not artist or not title or not all(key)
                    or key in similar_seen or key in excluded_keys
                ):
                    continue
                if key[1] in similar_seen_titles:
                    skipped.append({
                        "artist": artist,
                        "title": title,
                        "reason": "duplicate-title-other-artist",
                    })
                    continue
                if reason_conflicts_with_style(reason):
                    skipped.append({
                        "artist": artist,
                        "title": title,
                        "reason": "style-conflict",
                    })
                    continue
                block_reason = blocked_artist_reason(artist)
                if block_reason:
                    skipped.append({
                        "artist": artist,
                        "title": title,
                        "reason": block_reason,
                    })
                    continue
                recommendation, suitability = scored_recommendation(
                    value, artist, title, reason, genre,
                )
                genre_issue = (
                    "genre-conflict"
                    if romantic_evening and suitability["genre"] <= 0
                    else self._recommendation_style_issue(station_prompt, genre)
                    if not romantic_evening else ""
                )
                if genre_issue:
                    skipped.append({
                        "artist": artist,
                        "title": title,
                        "reason": genre_issue,
                        "genre": genre,
                    })
                    continue
                if (
                    romantic_evening
                    and suitability["score"] < ROMANTIC_EVENING_MIN_SCORE
                ):
                    skipped.append({
                        "artist": artist,
                        "title": title,
                        "reason": "below-romantic-evening-threshold",
                        "score": suitability["score"],
                    })
                    continue
                if key[0] == previous_similar_artist_key:
                    skipped.append({
                        "artist": artist,
                        "title": title,
                        "reason": "adjacent-artist-repeat",
                    })
                    continue
                similar_seen.add(key)
                similar_seen_titles.add(key[1])
                similar_recommendations.append(recommendation)
                previous_similar_artist_key = key[0]
            if not recommendations:
                raise ValueError("AI не повернув нових виконавців і назв")
            return {
                "tracks": recommendations[:10],
                "similar_tracks": similar_recommendations[:10],
                "target_mood": [
                    str(value).strip() for value in payload.get("targetMood", [])
                    if str(value).strip()
                ][:5],
                "avoid": [
                    str(value).strip().casefold() for value in payload.get("avoid", [])
                    if str(value).strip()
                ][:12],
                "provider": response.get("provider", ""),
                "skipped": skipped,
            }

        def plan_quality_score(plan):
            track_count = len(plan.get("tracks", []))
            similar_count = len(plan.get("similar_tracks", []))
            reason_penalty = sum(
                max(0, len(str(item.get("reason") or "").split()) - 3)
                for item in plan.get("tracks", [])
            )
            score = 0.0
            score += min(track_count, 10) * 12
            score += 14 if track_count >= 10 else -max(0, 5 - track_count) * 6
            score += min(similar_count, 10) * 2
            score += min(len(plan.get("target_mood", [])), 5) * 1.2
            score += min(len(plan.get("avoid", [])), 12) * 0.4
            if romantic_evening:
                suitability_scores = [
                    float(item.get("suitability_score") or 0)
                    for item in plan.get("tracks", [])
                ]
                if suitability_scores:
                    score += sum(suitability_scores) / len(suitability_scores)
            score -= reason_penalty * 0.75
            score -= len(plan.get("skipped", [])) * 3
            return round(max(0.0, score), 2)

        errors = []
        diagnostics = []
        candidates = []
        provider_priority = {
            spec.get("name", ""): index for index, spec in enumerate(providers)
        }
        selection_lanes = (
            f"Strictly follow the station style: {station_prompt_search}. Do not substitute a different era, region, language, or genre.",
            f"Choose mainstream, widely recognized songs that strictly fit: {station_prompt_search}.",
            f"Keep the artist mix varied while strictly following: {station_prompt_search}.",
            f"Prioritize canonical hits that strictly match: {station_prompt_search}.",
            f"Use only well-known, official releases within this exact scope: {station_prompt_search}.",
        )
        if romantic_evening:
            selection_lanes = (
                "Use the supplied retrieval queries. Return roughly 7 seed-neighbor, "
                "2 genre+mood+era discovery and 1 experimental candidate.",
                "Expand from three rotating seed tracks while keeping one track per artist.",
                "Find close 2005-2018 Ukrainian/Russian-language emo and pop-rock neighbors.",
                "Prioritize youthful romantic city-evening songs near the supplied seeds.",
                "Use recognizable official releases; love lyrics alone are not sufficient.",
            )

        def provider_request_text(spec, index):
            payload = json.loads(request_text)
            payload["selectionLane"] = index + 1
            payload["selectionFocus"] = selection_lanes[index % len(selection_lanes)]
            payload["providerLabel"] = str(spec.get("name") or "AI")
            if romantic_evening:
                payload["retrievalQueries"] = self._romantic_evening_queries(index)
            return json.dumps(payload, ensure_ascii=False)

        if providers:
            with ThreadPoolExecutor(max_workers=len(providers)) as executor:
                futures = {
                    executor.submit(
                        self._provider_chat_completion, spec, system_prompt,
                        provider_request_text(spec, index),
                        0.15, 0.75, search_max_tokens,
                    ): spec
                    for index, spec in enumerate(providers)
                }
                for future in as_completed(futures):
                    spec = futures[future]
                    response = future.result()
                    provider_name = response.get("provider") or spec.get("name", "AI")
                    if response.get("error"):
                        errors.append(response["error"])
                        diagnostics.append({
                            "provider": provider_name,
                            "ok": False,
                            "score": 0,
                            "error": response["error"],
                        })
                        continue
                    try:
                        plan = normalized_plan(response)
                        plan["quality_score"] = plan_quality_score(plan)
                        diagnostics.append({
                            "provider": provider_name,
                            "ok": True,
                            "score": plan["quality_score"],
                            "tracks": len(plan.get("tracks", [])),
                            "similar_tracks": len(plan.get("similar_tracks", [])),
                            "error": "",
                        })
                        candidates.append(plan)
                        LOGGER.info(
                            "AI music plan from %s scored %.2f: %s",
                            plan.get("provider") or "AI",
                            plan["quality_score"],
                            "; ".join(
                                f'{item["artist"]} - {item["title"]}'
                                for item in plan["tracks"]
                            ),
                        )
                        continue
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        LOGGER.warning(
                            "AI music plan rejected from %s: %s; response=%r",
                            provider_name, exc,
                            response.get("candidate", "")[:700],
                        )

                    repair_prompt = """Ти музичний директор. Поверни тільки короткий валідний JSON
формату {"tracks":[{"artist":"...","title":"...","genre":"..."}]}. Рівно 5 реальних,
відомих, офіційно виданих треків під заданий стиль. Genre обов'язковий і має
описувати реальний жанр треку. Без reason, Markdown і тексту
поза JSON. Не повторюй excludeTracks."""
                    if romantic_evening:
                        repair_prompt += """
Для Romantic Evening у кожному елементі також поверни year,
artistBreakthroughYear, mood, popularityScore і retrievalClass. Лише один трек
на виконавця; не повертай артистів із blacklist у selectionProfile."""
                    repaired = self._provider_chat_completion(
                        spec, repair_prompt, request_text, 0.1, 0.7, search_max_tokens,
                    )
                    if repaired.get("error"):
                        errors.append(repaired["error"])
                        diagnostics.append({
                            "provider": provider_name,
                            "ok": False,
                            "score": 0,
                            "error": repaired["error"],
                        })
                        continue
                    try:
                        plan = normalized_plan(repaired)
                        plan["quality_score"] = max(0, plan_quality_score(plan) - 5)
                        plan["repaired"] = True
                        diagnostics.append({
                            "provider": provider_name,
                            "ok": True,
                            "score": plan["quality_score"],
                            "tracks": len(plan.get("tracks", [])),
                            "similar_tracks": len(plan.get("similar_tracks", [])),
                            "repaired": True,
                            "error": "",
                        })
                        candidates.append(plan)
                        LOGGER.info(
                            "Repaired AI music plan from %s scored %.2f: %s",
                            plan.get("provider") or "AI",
                            plan["quality_score"],
                            "; ".join(
                                f'{item["artist"]} - {item["title"]}'
                                for item in plan["tracks"]
                            ),
                        )
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        LOGGER.warning(
                            "Repaired AI music plan rejected from %s: %s; response=%r",
                            repaired.get("provider", "AI"), exc,
                            repaired.get("candidate", "")[:700],
                        )
                        error = f'{provider_name}: некоректний список треків'
                        errors.append(error)
                        diagnostics.append({
                            "provider": provider_name,
                            "ok": False,
                            "score": 0,
                            "error": error,
                        })
        if candidates:
            chosen = max(
                candidates,
                key=lambda plan: (
                    float(plan.get("quality_score") or 0),
                    -provider_priority.get(plan.get("provider", ""), len(providers)),
                ),
            )
            if romantic_evening:
                ranked_items = []
                for plan in candidates:
                    for item in plan.get("tracks", []) + plan.get("similar_tracks", []):
                        ranked_items.append({
                            **item,
                            "source_provider": plan.get("provider", ""),
                        })
                ranked_items.sort(
                    key=lambda item: (
                        -float(item.get("suitability_score") or 0),
                        provider_priority.get(item.get("source_provider", ""), len(providers)),
                    )
                )
                unique_items = []
                seen_keys = set()
                seen_titles = set()
                seen_artists = set()
                for item in ranked_items:
                    artist_key = self._normalize_music_text(item.get("artist"))
                    title_key = self._normalize_music_text(item.get("title"))
                    key = (artist_key, title_key)
                    if (
                        not all(key) or key in seen_keys or title_key in seen_titles
                        or artist_key in seen_artists
                    ):
                        continue
                    seen_keys.add(key)
                    seen_titles.add(title_key)
                    seen_artists.add(artist_key)
                    unique_items.append(item)

                buckets = {
                    lane: [
                        item for item in unique_items
                        if item.get("retrieval_class") == lane
                    ]
                    for lane in ("seed", "discovery", "experimental")
                }
                primary = []
                for lane, limit in (("seed", 7), ("discovery", 2), ("experimental", 1)):
                    primary.extend(buckets[lane][:limit])
                primary_keys = {
                    (
                        self._normalize_music_text(item.get("artist")),
                        self._normalize_music_text(item.get("title")),
                    )
                    for item in primary
                }
                primary.extend(
                    item for item in unique_items
                    if (
                        self._normalize_music_text(item.get("artist")),
                        self._normalize_music_text(item.get("title")),
                    ) not in primary_keys
                )
                primary = primary[:10]
                primary_keys = {
                    (
                        self._normalize_music_text(item.get("artist")),
                        self._normalize_music_text(item.get("title")),
                    )
                    for item in primary
                }
                remaining = [
                    item for item in unique_items
                    if (
                        self._normalize_music_text(item.get("artist")),
                        self._normalize_music_text(item.get("title")),
                    ) not in primary_keys
                ]
                if not primary:
                    raise RuntimeError(
                        "Romantic Evening: жоден кандидат не набрав мінімум 72 бали"
                    )
                chosen = {
                    **chosen,
                    "tracks": primary,
                    "similar_tracks": remaining[:10],
                    "backup_tracks": remaining[10:60],
                    "target_mood": [
                        "romantic", "nostalgic", "warm", "bittersweet", "evening",
                    ],
                    "avoid": list(ROMANTIC_EVENING_FORBIDDEN_GENRE_PHRASES),
                    "quality_score": round(
                        sum(float(item.get("suitability_score") or 0) for item in primary)
                        / len(primary),
                        2,
                    ),
                    "candidate_count": len(ranked_items),
                    "qualified_candidate_count": len(unique_items),
                    "provider_diagnostics": diagnostics,
                }
                LOGGER.info(
                    "Romantic Evening pool: candidates=%s qualified=%s selected=%s score=%.2f",
                    len(ranked_items), len(unique_items), len(primary),
                    float(chosen["quality_score"]),
                )
                return chosen
            chosen_keys = {
                (
                    self._normalize_music_text(item.get("artist")),
                    self._normalize_music_text(item.get("title")),
                )
                for item in chosen.get("tracks", []) + chosen.get("similar_tracks", [])
            }
            chosen_titles = {key[1] for key in chosen_keys}
            backup_tracks = []
            backup_limit = min(60, max(20, len(providers) * 12))
            previous_backup_artist = self._normalize_music_text(
                (chosen.get("similar_tracks") or chosen.get("tracks") or [{}])[-1].get("artist")
            )
            other_plans = sorted(
                (plan for plan in candidates if plan is not chosen),
                key=lambda plan: (
                    -float(plan.get("quality_score") or 0),
                    provider_priority.get(plan.get("provider", ""), len(providers)),
                ),
            )
            for plan in other_plans:
                for item in plan.get("tracks", []) + plan.get("similar_tracks", []):
                    artist_key = self._normalize_music_text(item.get("artist"))
                    key = (artist_key, self._normalize_music_text(item.get("title")))
                    if (
                        not all(key) or key in chosen_keys or key[1] in chosen_titles
                        or artist_key == previous_backup_artist
                    ):
                        continue
                    chosen_keys.add(key)
                    chosen_titles.add(key[1])
                    backup_tracks.append({
                        **item,
                        "source_provider": plan.get("provider", ""),
                    })
                    previous_backup_artist = artist_key
                    if len(backup_tracks) >= backup_limit:
                        break
                if len(backup_tracks) >= backup_limit:
                    break
            chosen["backup_tracks"] = backup_tracks
            chosen["provider_diagnostics"] = diagnostics
            LOGGER.info(
                "Chosen AI music plan from %s with score %.2f",
                chosen.get("provider") or "AI",
                float(chosen.get("quality_score") or 0),
            )
            return chosen
        if not providers:
            raise RuntimeError("Для добору музики не налаштовано AI-провайдера")
        friendly_errors = list(dict.fromkeys(
            str(error)[:180] for error in errors if error
        ))
        raise RuntimeError(
            "Жоден доступний AI-провайдер не зміг підібрати музику. "
            + (
                "; ".join(friendly_errors)
                if friendly_errors else "Спробую знову пізніше."
            )
        )

    @staticmethod
    def _normalize_music_text(value):
        value = html.unescape(str(value or "")).casefold()
        value = value.replace("&", " and ")
        value = re.sub(
            r"\b(?:official|audio|video|lyrics?|lyric|hd|4k|remaster(?:ed)?|topic|vevo)\b",
            " ", value,
        )
        return " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))

    @classmethod
    def _recommendation_match_score(cls, candidate, recommendation):
        def source_title_words(value):
            value = re.sub(
                r"[\[(][^\])]*(?:official|audio|video|lyrics?|remaster(?:ed)?|visuali[sz]er)"
                r"[^\])]*[\])]",
                " ",
                str(value or ""),
                flags=re.IGNORECASE,
            )
            return set(cls._normalize_music_text(value).split())

        artist_words = set(cls._normalize_music_text(recommendation.get("artist")).split())
        title_words = set(cls._normalize_music_text(recommendation.get("title")).split())
        if not artist_words or not title_words:
            return 0.0
        raw_candidate_title = str(candidate.get("title") or "")
        candidate_title_words = source_title_words(raw_candidate_title)
        title_coverage = len(title_words & candidate_title_words) / len(title_words)
        if title_coverage < 0.75:
            return 0.0

        # Artist words occurring anywhere in a video title are not sufficient:
        # "Альбіна — Вогонь" used to match a child performance whose long
        # title happened to contain both words, while "Птаха — Сонце" could
        # match the reversed pair "КОЛІР СОНЦЕ — Птаха". Require either an
        # exact artist/uploader/channel identity or the normal Artist — Title
        # ordering in the source title.
        artist_key = cls._normalize_music_text(recommendation.get("artist"))
        metadata_artist_match = any(
            cls._normalize_music_text(value) == artist_key
            for value in (
                candidate.get("artist"),
                candidate.get("uploader"),
                candidate.get("channel"),
            )
            if str(value or "").strip()
        )

        title_parts = re.split(
            r"\s+(?:-|–|—|\|)\s+|\s*:\s+",
            raw_candidate_title,
            maxsplit=1,
        )
        ordered_title_match = False
        title_core_words = set(candidate_title_words - artist_words)
        if len(title_parts) == 2:
            left_words = set(cls._normalize_music_text(title_parts[0]).split())
            right_words = source_title_words(title_parts[1])
            title_core_words = right_words
            artist_recall = len(artist_words & left_words) / len(artist_words)
            artist_precision = len(artist_words & left_words) / max(1, len(left_words))
            right_title_coverage = len(title_words & right_words) / len(title_words)
            ordered_title_match = (
                artist_recall >= 0.80
                and artist_precision >= 0.50
                and right_title_coverage >= 0.75
            )

        # A one-word request such as "Black" must not validate the different
        # song "Black Sabbath" merely because the channel artist is exact.
        # For longer titles, allow transliterations/subtitles after the name.
        title_precision = len(title_words & title_core_words) / max(1, len(title_core_words))
        minimum_title_precision = 0.75 if len(title_words) == 1 else 0.45
        if title_precision < minimum_title_precision:
            return 0.0

        if not metadata_artist_match and not ordered_title_match:
            return 0.0
        artist_confidence = 1.0 if metadata_artist_match else 0.9
        return round(title_coverage * 0.60 + artist_confidence * 0.40, 4)

    @staticmethod
    def _exact_track_query(recommendation):
        artist = str(recommendation.get("artist") or "").replace('"', " ").strip()
        title = str(recommendation.get("title") or "").replace('"', " ").strip()
        return f'ytsearch5:"{artist}" "{title}" official audio'

    @classmethod
    def _queue_candidate_allowed(
        cls, candidate, settings, blocked_words, source_ids, recommendation=None,
    ):
        title = str(candidate.get("title") or "").casefold()
        duration = candidate.get("duration")
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            return False
        minimum = float(settings.get("queue_min_duration", 120))
        maximum = float(settings.get("queue_max_duration", 480))
        allowed = bool(
            candidate.get("id")
            and candidate.get("url")
            and minimum <= duration <= maximum
            and candidate.get("id") not in source_ids
            and not any(word in title for word in blocked_words)
        )
        if not allowed:
            return False
        if recommendation is None:
            return True
        match_score = cls._recommendation_match_score(candidate, recommendation)
        candidate["match_score"] = match_score
        return match_score >= 0.75

    @classmethod
    def _candidate_matches_artist(cls, candidate, artist):
        artist_key = cls._normalize_music_text(artist)
        if not artist_key:
            return False
        if any(
            cls._normalize_music_text(value) == artist_key
            for value in (
                candidate.get("artist"),
                candidate.get("uploader"),
                candidate.get("channel"),
            )
            if str(value or "").strip()
        ):
            return True
        title_parts = re.split(
            r"\s+(?:-|–|—|\|)\s+|\s*:\s+",
            str(candidate.get("title") or ""),
            maxsplit=1,
        )
        return bool(
            len(title_parts) == 2
            and cls._normalize_music_text(title_parts[0]) == artist_key
        )

    @classmethod
    def _canonical_candidate_title(cls, candidate, artist):
        metadata_title = str(candidate.get("track") or "").strip()
        if metadata_title:
            return metadata_title
        raw_title = html.unescape(str(candidate.get("title") or "")).strip()
        title_parts = re.split(
            r"\s+(?:-|–|—|\|)\s+|\s*:\s+",
            raw_title,
            maxsplit=1,
        )
        if (
            len(title_parts) == 2
            and cls._normalize_music_text(title_parts[0])
            == cls._normalize_music_text(artist)
        ):
            raw_title = title_parts[1].strip()
        raw_title = re.sub(
            r"\s*[\[(][^\])]*(?:official|audio|video|lyrics?|visuali[sz]er|"
            r"clip|клип|прем(?:'|’)єра|премьера)[^\])]*[\])]\s*",
            " ",
            raw_title,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\s+", " ", raw_title).strip(" -–—|:")

    def _analyze_discovered_audio(self, path, search_plan):
        # A downloaded track must become playable immediately. Importing
        # librosa/numba here can compile DSP kernels for minutes and consume
        # gigabytes before the first DB row is saved. Timing analysis remains
        # an explicit library action; discovery stores safe neutral defaults.
        return {
            "bpm": 0,
            "energy": 5,
            "mood": ", ".join(search_plan.get("target_mood", [])[:3]),
        }

    @staticmethod
    def _download_info_candidate(info):
        video_id = str(info.get("id") or "")
        url = info.get("webpage_url") or info.get("original_url") or info.get("url")
        if url and not str(url).startswith("http") and video_id:
            url = f"https://www.youtube.com/watch?v={video_id}"
        return {
            "id": video_id,
            "url": url,
            "title": html.unescape(str(info.get("title") or "")),
            "artist": html.unescape(str(info.get("artist") or info.get("uploader") or "")),
            "uploader": html.unescape(str(info.get("uploader") or "")),
            "channel": html.unescape(str(info.get("channel") or "")),
            "track": html.unescape(str(info.get("track") or info.get("alt_title") or "")),
            "duration": info.get("duration"),
        }

    def _download_audio_with_lumen(
        self, item, output_dir, *, search=False, music_search=False,
        validator=None, progress_callback=None,
    ):
        try:
            from Qwen_python_20260804_4sskbslqs import download_audio_item
        except (ImportError, SystemExit) as exc:
            raise RuntimeError("LUMEN Downloader недоступний") from exc
        settings = self.db.settings()
        return download_audio_item(
            item,
            output_dir,
            search=search,
            music_search=music_search,
            candidates=5,
            retries=2,
            youtube_auth_browser=settings.get("youtube_auth_browser", "off"),
            youtube_auth_profile=settings.get("youtube_auth_profile", ""),
            validator=validator,
            progress_callback=progress_callback,
        )

    def _set_queue_progress(
        self, stage, percent=0, message="", track="", **details,
    ):
        if not hasattr(self, "radio_queue"):
            return None
        return self.radio_queue.update_progress(
            stage, percent, message, track, **details,
        )

    def _clear_discovery_plan_cache(self):
        if not hasattr(self, "_discovery_plan_lock"):
            return
        with self._discovery_plan_lock:
            self._discovery_plan_prompt = ""
            self._discovery_plan_pool = []
            self._discovery_plan_context = {"target_mood": [], "avoid": []}

    def _refill_discovery_plan_cache(self, settings, excluded_tracks):
        plan = self._queue_search_plan(settings, excluded_tracks)
        romantic_evening = self._is_romantic_evening_profile(
            settings.get("station_prompt", DEFAULTS["station_prompt"])
        )
        pool = []
        seen = set()
        seen_artists = set()
        previous_artist = ""
        for item in (
            list(plan.get("tracks") or [])
            + list(plan.get("similar_tracks") or [])
            + list(plan.get("backup_tracks") or [])
        ):
            artist_key = self._normalize_music_text(item.get("artist"))
            title_key = self._normalize_music_text(item.get("title"))
            key = (artist_key, title_key)
            if (
                not all(key) or key in seen or artist_key == previous_artist
                or (romantic_evening and artist_key in seen_artists)
            ):
                continue
            seen.add(key)
            seen_artists.add(artist_key)
            pool.append({
                **item,
                "source_provider": (
                    item.get("source_provider") or plan.get("provider", "")
                ),
            })
            previous_artist = artist_key
        with self._discovery_plan_lock:
            self._discovery_plan_prompt = self._normalized_station_prompt(
                settings.get("station_prompt", DEFAULTS["station_prompt"])
            )
            self._discovery_plan_pool = pool
            self._discovery_plan_context = {
                "target_mood": list(plan.get("target_mood") or []),
                "avoid": list(plan.get("avoid") or []),
                "provider": plan.get("provider", ""),
                "provider_diagnostics": list(plan.get("provider_diagnostics") or []),
            }
        return len(pool)

    def _pop_discovery_recommendation(self, settings, excluded_tracks):
        prompt = self._normalized_station_prompt(
            settings.get("station_prompt", DEFAULTS["station_prompt"])
        )
        excluded_keys = {
            (
                self._normalize_music_text(track.get("artist")),
                self._normalize_music_text(track.get("title")),
            )
            for track in excluded_tracks
            if str(track.get("artist") or "").strip()
            and str(track.get("title") or "").strip()
        }
        with self._discovery_plan_lock:
            if self._discovery_plan_prompt and self._discovery_plan_prompt != prompt:
                self._discovery_plan_pool = []
                self._discovery_plan_context = {"target_mood": [], "avoid": []}
            self._discovery_plan_prompt = prompt
            while self._discovery_plan_pool:
                item = self._discovery_plan_pool.pop(0)
                key = (
                    self._normalize_music_text(item.get("artist")),
                    self._normalize_music_text(item.get("title")),
                )
                if all(key) and key not in excluded_keys:
                    return item, dict(self._discovery_plan_context)
        return None, dict(self._discovery_plan_context)

    def _discover_queue_track(self, excluded_track_ids):
        if self._shutdown_event.is_set():
            return None
        self._set_queue_progress(
            "planning", 6, "AI добирає відомі треки під стиль станції",
        )
        settings = self.db.settings()
        enabled = str(settings.get("dynamic_discovery_enabled", "0")).casefold()
        licensed = str(settings.get("licensed_sources_confirmed", "0")).casefold()
        if enabled not in {"1", "true", "yes", "on"}:
            return None
        if licensed not in {"1", "true", "yes", "on"}:
            raise RuntimeError("Підтвердьте права на джерела перед автозавантаженням")

        romantic_evening = self._is_romantic_evening_profile(
            settings.get("station_prompt", DEFAULTS["station_prompt"])
        )
        history_limit = int(float(settings.get("track_cooldown_tracks", 200)))
        history = self.db.recent_radio_history(history_limit)
        if romantic_evening:
            seven_days_ago = (
                datetime.now(timezone.utc) - timedelta(days=7)
            ).isoformat()
            weekly_history = self.db.radio_history_since(seven_days_ago)
            history_by_id = {
                int(item.get("id") or 0): item
                for item in [*history, *weekly_history]
            }
            history = sorted(
                history_by_id.values(),
                key=lambda item: int(item.get("id") or 0),
                reverse=True,
            )
        excluded_ids = {int(value) for value in excluded_track_ids}
        queue_tracks = [
            track for track in self.db.tracks() if track["id"] in excluded_ids
        ]
        last_queue_artist = (
            self._normalize_music_text(queue_tracks[-1].get("artist"))
            if queue_tracks else ""
        )
        excluded_tracks = list(queue_tracks)
        excluded_tracks.extend(history)
        excluded_tracks.extend(self._ai_library_tracks())
        excluded_tracks.extend(
            self._playlist_track_refs(settings.get("ai_previous_playlist", "[]"))
        )
        excluded_track_keys = {
            (
                self._normalize_music_text(track.get("artist")),
                self._normalize_music_text(track.get("title")),
            )
            for track in excluded_tracks
            if str(track.get("artist") or "").strip()
            and str(track.get("title") or "").strip()
        }
        source_ids = {
            str(item.get("source_id") or "") for item in history
            if item.get("source_id")
        }
        source_ids.update(
            str(track.get("youtube_id") or "")
            for track in self.db.tracks()
            if track["id"] in excluded_ids
            and track.get("youtube_id")
        )
        recent_artists = {
            self._normalize_music_text(item.get("artist"))
            for item in history[: max(
                8 if romantic_evening else 1,
                int(float(settings.get("artist_cooldown_tracks", 15))),
            )]
        }
        queued_artists = {
            self._normalize_music_text(item.get("artist"))
            for item in queue_tracks
            if str(item.get("artist") or "").strip()
        }
        base_blocked_words = {
            "reaction", "tutorial", "review", "interview", "live concert",
            "live video", "live session", "concert",
            "full concert", "1 hour", "10 hours", "sped up", "nightcore",
            "slowed + reverb", "slowed and reverb", "shorts", "playlist", "mix",
            "cover", "karaoke", "tribute", "fan made", "ai generated",
            "royalty free", "type beat",
        }
        output_dir = self.root / "downloads" / "queue"
        output_dir.mkdir(parents=True, exist_ok=True)
        candidate = None
        downloaded = None
        download_errors = []
        attempted_artist_fallbacks = set()
        search_plan = {"target_mood": [], "avoid": []}
        refreshed_plan = False
        max_attempts = 16
        for _attempt in range(max_attempts):
            recommendation, cached_context = self._pop_discovery_recommendation(
                settings, excluded_tracks,
            )
            if not recommendation:
                if refreshed_plan:
                    break
                self._refill_discovery_plan_cache(settings, excluded_tracks)
                refreshed_plan = True
                recommendation, cached_context = self._pop_discovery_recommendation(
                    settings, excluded_tracks,
                )
                if not recommendation:
                    break
            search_plan = cached_context
            recommendation_artist = self._normalize_music_text(recommendation["artist"])
            if (
                recommendation_artist == last_queue_artist
                or recommendation_artist in recent_artists
                or (romantic_evening and recommendation_artist in queued_artists)
            ):
                continue
            blocked_words = {
                *base_blocked_words,
                *(str(value).casefold() for value in search_plan.get("avoid", [])),
            }
            accepted = {}

            def validator(info, selected=recommendation):
                checked = self._download_info_candidate(info)
                if not self._queue_candidate_allowed(
                    checked, settings, blocked_words, source_ids, selected,
                ):
                    return False
                accepted.clear()
                accepted.update(checked)
                return True

            query = f'{recommendation["artist"]} - {recommendation["title"]}'
            self._set_queue_progress(
                "searching", 18,
                "Шукаю точний офіційний аудіозапис",
                track=query,
            )

            def download_progress(progress, selected=query):
                raw_percent = float(progress.get("percent") or 0)
                self._set_queue_progress(
                    "downloading",
                    20 + raw_percent * 0.75,
                    "Завантажую аудіо у локальну бібліотеку",
                    track=selected,
                    downloaded_bytes=progress.get("downloaded_bytes", 0),
                    total_bytes=progress.get("total_bytes", 0),
                    speed=progress.get("speed", 0),
                    eta=progress.get("eta", 0),
                )
            try:
                LOGGER.info("LUMEN Downloader searching audio: %s", query)
                downloaded = self._download_audio_with_lumen(
                    query,
                    output_dir,
                    search=True,
                    music_search=True,
                    validator=validator,
                    progress_callback=download_progress,
                )
                if not accepted:
                    raise RuntimeError(
                        "LUMEN Downloader повернув аудіо, яке не пройшло "
                        "перевірку виконавця та назви"
                    )
                info = downloaded.get("info") or {}
                candidate = dict(accepted)
                candidate.update({
                    "query": query,
                    "recommendation": recommendation,
                })
                LOGGER.info(
                    "LUMEN Downloader completed: %s -> %s",
                    query, downloaded.get("path"),
                )
                break
            except Exception as exc:
                LOGGER.warning("LUMEN Downloader rejected %s: %s", query, exc)
                download_errors.append(f"{query}: {exc}")
                if recommendation_artist in attempted_artist_fallbacks:
                    continue
                attempted_artist_fallbacks.add(recommendation_artist)

                artist_accepted = {}

                def artist_validator(info, selected_artist=recommendation["artist"]):
                    checked = self._download_info_candidate(info)
                    if not self._queue_candidate_allowed(
                        checked, settings, blocked_words, source_ids,
                    ):
                        return False
                    if not self._candidate_matches_artist(checked, selected_artist):
                        return False
                    canonical_title = self._canonical_candidate_title(
                        checked, selected_artist,
                    )
                    corrected_key = (
                        self._normalize_music_text(selected_artist),
                        self._normalize_music_text(canonical_title),
                    )
                    if not all(corrected_key) or corrected_key in excluded_track_keys:
                        return False
                    checked["canonical_title"] = canonical_title
                    checked["match_score"] = 0.85
                    artist_accepted.clear()
                    artist_accepted.update(checked)
                    return True

                artist_query = f'{recommendation["artist"]} official audio'
                self._set_queue_progress(
                    "searching", 18,
                    "Уточнюю реальну назву треку цього виконавця",
                    track=artist_query,
                )
                try:
                    downloaded = self._download_audio_with_lumen(
                        artist_query,
                        output_dir,
                        search=True,
                        music_search=False,
                        validator=artist_validator,
                        progress_callback=lambda progress, selected=artist_query: (
                            download_progress(progress, selected)
                        ),
                    )
                    if not artist_accepted:
                        raise RuntimeError(
                            "резервний результат не пройшов перевірку виконавця"
                        )
                    corrected_title = artist_accepted["canonical_title"]
                    corrected_recommendation = {
                        **recommendation,
                        "title": corrected_title,
                    }
                    candidate = dict(artist_accepted)
                    candidate.update({
                        "query": f'{recommendation["artist"]} - {corrected_title}',
                        "recommendation": corrected_recommendation,
                    })
                    LOGGER.info(
                        "LUMEN Downloader corrected AI title: %s -> %s - %s",
                        query, recommendation["artist"], corrected_title,
                    )
                    break
                except Exception as fallback_exc:
                    LOGGER.warning(
                        "LUMEN Downloader artist fallback rejected %s: %s",
                        recommendation["artist"], fallback_exc,
                    )
                    download_errors.append(
                        f'{recommendation["artist"]}: {fallback_exc}'
                    )
        if not downloaded or not candidate:
            detail = download_errors[-1] if download_errors else "немає нових рекомендацій"
            raise RuntimeError(f"LUMEN Downloader не знайшов відповідний аудіотрек. {detail}")

        prepared = Path(downloaded["path"]).resolve()
        info = downloaded.get("info") or {}
        self._set_queue_progress(
            "verifying", 95, "Перевіряю завантажений аудіофайл",
            track=candidate.get("query", ""),
        )
        try:
            relative = prepared.relative_to(self.root.resolve()).as_posix()
        except ValueError as exc:
            raise RuntimeError("LUMEN Downloader повернув файл поза папкою радіо") from exc
        if not prepared.is_file():
            raise RuntimeError("LUMEN Downloader не повернув готовий локальний аудіофайл")
        if self._shutdown_event.is_set():
            try:
                prepared.unlink()
            except OSError:
                pass
            return None

        artist = candidate["recommendation"]["artist"]
        title = candidate["recommendation"]["title"]
        track = self.db.add_local_track(artist, title, relative)
        analysis = self._analyze_discovered_audio(prepared, search_plan)
        self._set_queue_progress(
            "saving", 97, "Зберігаю метадані треку", track=candidate["query"],
        )
        duration = float(info.get("duration") or candidate.get("duration") or 0)
        source_id = str(info.get("id") or candidate["id"])
        source_title = str(info.get("title") or candidate["title"])
        self.db.update_track(
            track["id"],
            youtube_id=source_id, youtube_title=source_title,
            status="ready", duration_ms=round(duration * 1000),
            bpm=analysis["bpm"], energy=analysis["energy"], mood=analysis["mood"],
            genre=str(candidate["recommendation"].get("genre") or "").strip(),
            match_score=candidate["match_score"], library_source="ai",
        )
        result = self.db.track(track["id"])
        self._remember_ai_tracks([result])
        result["source_query"] = candidate["query"]
        return result

    def resolve_track(self, track_id):
        track = next((x for x in self.db.tracks() if x["id"] == int(track_id)), None)
        if not track:
            return {"ok": False, "error": "Трек не знайдено"}
        local_path = str(track.get("local_path") or "").strip()
        if local_path and (self.root / local_path).is_file():
            return {"ok": True, "track": track, "local_path": local_path, "cached": True}

        recommendation = {"artist": track["artist"], "title": track["title"]}
        settings = dict(self.db.settings())
        settings.update({"queue_min_duration": "60", "queue_max_duration": "600"})
        blocked_words = {
            "reaction", "tutorial", "review", "interview", "full concert",
            "sped up", "nightcore", "slowed + reverb", "slowed and reverb",
            "shorts", "playlist", "mix", "cover", "karaoke", "tribute",
            "fan made", "ai generated", "royalty free", "type beat",
        }
        accepted = {}

        def validator(info):
            candidate = self._download_info_candidate(info)
            if not self._queue_candidate_allowed(
                candidate, settings, blocked_words, set(), recommendation,
            ):
                return False
            accepted.clear()
            accepted.update(candidate)
            return True

        try:
            downloaded = self._download_audio_with_lumen(
                f'{track["artist"]} - {track["title"]}',
                self.root / "downloads",
                search=True,
                music_search=True,
                validator=validator,
            )
            prepared = Path(downloaded["path"]).resolve()
            relative = prepared.relative_to(self.root.resolve()).as_posix()
            info = downloaded.get("info") or {}
            downloaded_candidate = self._download_info_candidate(info)
            candidate = accepted or downloaded_candidate
            duration = float(info.get("duration") or candidate.get("duration") or 0)
            self.db.update_track(
                track["id"],
                local_path=relative,
                youtube_id=str(info.get("id") or candidate.get("id") or ""),
                youtube_title=str(info.get("title") or candidate.get("title") or ""),
                match_score=float(candidate.get("match_score") or 1),
                duration_ms=round(duration * 1000),
                status="ready",
            )
            updated = self.db.track(track["id"])
            return {
                "ok": True,
                "track": updated,
                "local_path": relative,
                "cached": False,
            }
        except Exception as exc:
            self.db.update_track(track["id"], status="unavailable")
            return {"ok": False, "error": f"LUMEN Downloader: {exc}"}

    def _speech_asset(self, text, voice=DEFAULT_TTS_VOICE, rate="-2%"):
        speech_text = normalize_for_speech(text, self.db.tracks())
        if not speech_text:
            return {"ok": False, "error": "Немає тексту для озвучення"}
        cache = self.root / "cache" / "tts"
        cache.mkdir(parents=True, exist_ok=True)
        settings = self.db.settings()
        use_styletts = str(settings.get("use_styletts", "1")).strip().casefold() in {
            "1", "true", "yes", "on",
        }
        path = None
        cached = False
        provider = ""
        styletts_error = ""
        try:
            if use_styletts:
                local_key = "styletts2-uk-filatov-v1\0" + rate + "\0" + speech_text
                local_path = cache / (
                    hashlib.sha256(local_key.encode("utf-8")).hexdigest() + ".wav"
                )
                cached = local_path.is_file() and local_path.stat().st_size > 44
                if cached:
                    path = local_path
                    provider = "styletts2"
                else:
                    from .tts_styletts import styletts_last_error, synthesize_styletts

                    if synthesize_styletts(speech_text, voice, rate, local_path):
                        path = local_path
                        provider = "styletts2"
                    else:
                        styletts_error = styletts_last_error()

            if path is None:
                edge_key = "edge-tts-v1\0" + voice + "\0" + rate + "\0" + speech_text
                edge_path = cache / (
                    hashlib.sha256(edge_key.encode("utf-8")).hexdigest() + ".mp3"
                )
                cached = edge_path.is_file() and edge_path.stat().st_size > 0
                if not cached:
                    import edge_tts

                    asyncio.run(
                        edge_tts.Communicate(
                            speech_text, voice=voice, rate=rate
                        ).save(str(edge_path))
                    )
                path = edge_path
                provider = "edge_tts"
            duration_ms = 0
            try:
                from mutagen import File as MutagenFile
                audio_file = MutagenFile(path)
                duration_ms = (
                    round(float(audio_file.info.length) * 1000)
                    if audio_file is not None else 0
                )
            except Exception:
                duration_ms = 0
            if not duration_ms and path.suffix.casefold() == ".wav":
                try:
                    import wave

                    with wave.open(str(path), "rb") as audio_file:
                        duration_ms = round(
                            audio_file.getnframes()
                            / max(1, audio_file.getframerate())
                            * 1000
                        )
                except (OSError, EOFError, wave.Error):
                    duration_ms = 0
            if not duration_ms:
                duration_ms = round(max(1, spoken_word_count(speech_text)) / 2.4 * 1000)
            return {
                "ok": True,
                "voice": voice,
                "rate": rate,
                "cached": cached,
                "provider": provider,
                "fallback_reason": styletts_error,
                "speech_text": speech_text,
                "path": path.relative_to(self.root).as_posix(),
                "duration_ms": duration_ms,
            }
        except Exception as exc:
            details = f"StyleTTS2: {styletts_error}; " if styletts_error else ""
            return {"ok": False, "error": f"{details}Edge TTS: {exc}"}

    # StyleTTS integration moved to backend/tts_styletts.py

    def _audio_data(self, relative_path):
        if not relative_path:
            return ""
        path = (self.root / relative_path).resolve()
        cache_root = (self.root / "cache" / "tts").resolve()
        if cache_root not in path.parents or not path.is_file():
            return ""
        audio = base64.b64encode(path.read_bytes()).decode("ascii")
        mime = "audio/wav" if path.suffix.casefold() == ".wav" else "audio/mpeg"
        return f"data:{mime};base64,{audio}"

    def synthesize_speech(self, text, voice=DEFAULT_TTS_VOICE, rate="-2%"):
        result = self._speech_asset(text, voice=voice, rate=rate)
        if result.get("ok"):
            result["audio"] = self._audio_data(result["path"])
        return result

    def warm_tts(self):
        settings = self.db.settings()
        enabled = str(settings.get("use_styletts", "1")).strip().casefold() in {
            "1", "true", "yes", "on",
        }
        if not enabled:
            return {"ok": True, "ready": False, "provider": "edge_tts"}
        try:
            from .tts_styletts import styletts_last_error, warm_styletts

            ready = warm_styletts()
            return {
                "ok": ready,
                "ready": ready,
                "provider": "styletts2",
                "error": "" if ready else styletts_last_error(),
            }
        except Exception as exc:
            return {"ok": False, "ready": False, "provider": "styletts2", "error": str(exc)}

    def set_track_pronunciation(self, track_id, artist_speech="", title_speech=""):
        track = next(
            (item for item in self.db.tracks() if item["id"] == int(track_id)),
            None,
        )
        if not track:
            return {"ok": False, "error": "Трек не знайдено"}
        artist_speech = (artist_speech or "").strip()
        title_speech = (title_speech or "").strip()
        artist_latin = bool(re.search(r"[A-Za-z]", track.get("artist", "")))
        title_latin = bool(re.search(r"[A-Za-z]", track.get("title", "")))
        self.db.update_track(
            track["id"],
            artist_speech=artist_speech,
            title_speech=title_speech,
            artist_speech_confidence=1.0 if artist_speech or not artist_latin else 0.0,
            title_speech_confidence=1.0 if title_speech or not title_latin else 0.0,
            artist_language=detect_text_language(track.get("artist", "")),
            title_language=detect_text_language(track.get("title", "")),
            pronunciation_source="manual",
            pronunciation_review=int(
                (artist_latin and not artist_speech) or
                (title_latin and not title_speech)
            ),
        )
        tracks = self.db.tracks()
        updated = next(item for item in tracks if item["id"] == track["id"])
        intro_speech = normalize_for_speech(updated.get("intro", ""), tracks)
        self.db.update_track(updated["id"], intro_speech=intro_speech)
        updated["intro_speech"] = intro_speech
        return {"ok": True, "track": updated}

    def generate_track_pronunciation(self, track_id, invalidate=True):
        track = self.db.track(int(track_id))
        if not track:
            return {"ok": False, "error": "Трек не знайдено"}
        settings = self.db.settings()
        providers = self._ai_providers_for_pronunciation(settings)
        if not providers:
            return {
                "ok": True,
                "track": track,
                "provider": "auto_local",
                "review": bool(track.get("pronunciation_review")),
            }

        system_prompt = """Ти фонетичний редактор українського радіо. Це окремий
    нейронний етап літерації перед TTS, а не переклад. Спочатку внутрішньо визнач
    реальну вимову назви мовою оригіналу, потім запиши її українськими літерами.
    Поверни тільки JSON:
    {"artist_speech":"...","title_speech":"..."}

artist_speech і title_speech мають бути записані українськими літерами так, як
оригінал реально вимовляється. Не перекладай назву й не відмінюй її. Англійські
    слова передавай за звучанням, а не за написанням; російські — українською
    фонетичною орфографією, але не українізуй ім'я. Абревіатури записуй назвами
    літер. Не додавай наголосів, IPA, пояснень, цифр, лапок поза JSON чи латинських
    і російських літер у *_speech."""
        request_text = json.dumps(
            {"artist": track["artist"], "title": track["title"]},
            ensure_ascii=False,
        )
        with ThreadPoolExecutor(max_workers=len(providers)) as executor:
            futures = [
                executor.submit(
                    _chat_completion, spec, system_prompt, request_text,
                    0.15, 0.8, 120,
                )
                for spec in providers
            ]
            responses = [future.result() for future in as_completed(futures)]

        valid = []
        errors = []
        for response in responses:
            if response.get("error"):
                errors.append(response["error"])
                continue
            try:
                payload = _json_object(response.get("candidate", ""))
                artist_speech = validate_phonetic_spelling(
                    track["artist"], str(payload.get("artist_speech", "")), "artist"
                )
                title_speech = validate_phonetic_spelling(
                    track["title"], str(payload.get("title_speech", "")), "title"
                )
                valid.append({
                    "provider": response["provider"],
                    "artist_speech": artist_speech,
                    "title_speech": title_speech,
                    "artist_language": detect_text_language(track["artist"]),
                    "title_language": detect_text_language(track["title"]),
                })
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{response['provider']}: {exc}")

        if not valid:
            return {"ok": False, "error": "; ".join(errors) or "AI не створив транскрипцію"}
        provider_priority = {
            item["name"]: index for index, item in enumerate(providers)
        }
        pronunciation_groups = {}
        for item in valid:
            key = (
                item["artist_speech"].casefold(),
                item["title_speech"].casefold(),
            )
            pronunciation_groups.setdefault(key, []).append(item)
        winning_group = max(
            pronunciation_groups.values(),
            key=lambda group: (
                len(group),
                -min(provider_priority.get(item["provider"], len(providers)) for item in group),
            ),
        )
        winning_group.sort(
            key=lambda item: provider_priority.get(item["provider"], len(providers))
        )
        chosen = winning_group[0]
        consensus = len(valid) > 1 and all(
            item["artist_speech"].casefold() == chosen["artist_speech"].casefold()
            and item["title_speech"].casefold() == chosen["title_speech"].casefold()
            for item in valid[1:]
        )
        disagreement = len(valid) > 1 and not consensus
        confidence = 0.97 if consensus else (0.92 if len(valid) == 1 else 0.84)
        source = "+".join(dict.fromkeys(item["provider"] for item in valid))
        self.db.update_track(
            track["id"],
            artist_speech=chosen["artist_speech"],
            title_speech=chosen["title_speech"],
            artist_speech_confidence=confidence,
            title_speech_confidence=confidence,
            artist_language=chosen["artist_language"],
            title_language=chosen["title_language"],
            pronunciation_source=f"ai:phonetic:{source}",
            pronunciation_review=int(disagreement),
        )
        if invalidate:
            self.db.invalidate_transitions_for_track(track["id"])
        updated = self.db.track(track["id"])
        return {
            "ok": True,
            "track": updated,
            "provider": source,
            "review": disagreement,
            "errors": errors,
        }

    def set_youtube_id(self, track_id, video_id):
        video_id = (video_id or "").strip()
        if not video_id:
            return {"ok": False, "error": "Порожній video ID"}
        self.db.update_track(int(track_id), youtube_id=video_id, youtube_title="Вказано вручну", status="ready")
        return {"ok": True}

    def _choose_intro_style(self, current, verified_fact, requested=""):
        hour = datetime.now().astimezone().hour
        eligible = [
            "ironic", "short_joke", "atmospheric", "listener_tease",
            "straight_radio",
        ]
        if 5 <= hour < 12:
            eligible.append("morning")
        if current:
            eligible.append("bridge_from_previous_track")
        if (verified_fact or "").strip():
            eligible.append("interesting_fact")
        if requested in eligible:
            chosen = requested
        else:
            choices = [item for item in eligible if item != self._last_intro_style]
            chosen = random.choice(choices or eligible)
        self._last_intro_style = chosen
        return chosen

    def make_intro(
        self,
        track_id,
        current_track_id=None,
        verified_fact="",
        style="",
        generation_context=None,
        content_plan=None,
        duration_seconds=None,
        variant="full",
        store_track=True,
        providers_override=None,
    ):
        tracks = self.db.tracks()
        track = next((x for x in tracks if x["id"] == int(track_id)), None)
        if not track:
            return {"ok": False, "error": "Трек не знайдено"}
        current = next((x for x in tracks if current_track_id and x["id"] == int(current_track_id)), None)
        settings = self.db.settings()
        context = generation_context or self.context_engine.snapshot(current, track)
        plan = content_plan or {}
        verified_fact = plan.get("verified_fact") or verified_fact
        requested_style = plan.get("style") or style
        structure = plan.get("structure") or plan.get("content_type") or "announce"
        mention_policy = plan.get("mention_policy") or "artist_and_title"
        length_class = plan.get("length_class") or "medium"
        content_type = plan.get("content_type") or "talk"
        requested_duration = float(
            duration_seconds or plan.get("target_seconds") or settings.get("host_length", 10)
        )
        duration_seconds = (
            max(7.0, min(45.0, requested_duration))
            if content_type in {"story", "fact"}
            else max(3.0, min(12.0, requested_duration))
        )
        if plan.get("target_seconds") != duration_seconds:
            plan = {**plan, "target_seconds": duration_seconds}
        voice_profile = self.voice_director.profile(
            context, track, duration_seconds, content_type
        )
        short_variant = variant == "short"
        story_mode = content_type == "story"
        if story_mode:
            if length_class == "short":
                sentence_target, allowed_sentences = 2, (1, 2)
            elif length_class == "feature":
                sentence_target, allowed_sentences = 4, (3, 4)
            else:
                sentence_target, allowed_sentences = 3, (2, 3, 4)
        elif plan.get("content_type") in {
            "top_of_hour", "weather_touch", "weather_change", "fact"
        }:
            sentence_target = 1 if length_class == "short" else 2 if length_class == "normal" else 4
            allowed_sentences = (1, 2) if length_class == "short" else (2, 3) if length_class == "normal" else (3, 4)
        else:
            sentence_target = 2 if duration_seconds >= 9 and not short_variant else 1
            allowed_sentences = (1, 2, 3) if not short_variant else (1,)
        style = self._choose_intro_style(current, verified_fact, style)
        if requested_style in INTRO_STYLES:
            style = requested_style
            self._last_intro_style = style
        generated = contextual_fallback_copy(
            track, current, style, context, plan, short_variant
        )
        generated_from_ai = False
        selected_story_grounded = False
        ai_provider = ""
        selected_quality_score = 0.0
        selected_variant = 0
        providers = (
            list(providers_override)
            if providers_override is not None else self._ai_providers_for_intro(settings)
        )
        provider_error = ""
        provider_diagnostics = []
        if providers:
            humor = int(settings.get("host_humor", 70))
            sarcasm = int(settings.get("host_sarcasm", 35))
            energy = int(settings.get("host_energy", 65))
            conversational = int(settings.get("host_conversational", 85))
            facts = int(settings.get("host_facts", 60))
            length = max(6, min(55 if story_mode else 32, duration_seconds))
            temperature = max(0.1, min(1.0, float(settings.get("ai_temperature", 0.65))))
            top_p = max(0.1, min(1.0, float(settings.get("ai_top_p", 0.90))))
            max_tokens = _normalized_ai_max_tokens(settings)
            effective_facts = facts if (verified_fact or "").strip() else 0
            marker_by_policy = {
                "artist_and_title": "[[NEXT_TRACK]]",
                "artist_only": "[[NEXT_ARTIST]]",
                "title_only": "[[NEXT_TITLE]]",
                "implicit": "",
            }
            required_marker = marker_by_policy.get(mention_policy, "[[NEXT_TRACK]]")
            if story_mode:
                mention_policy = "artist_and_title"
                required_marker = "[[NEXT_TRACK]]"
            if required_marker:
                naming_rule = (
                    f"Використай {required_marker} рівно один раз. Інші NEXT-маркери не використовуй."
                )
            else:
                naming_rule = (
                    "Не називай наступного артиста або трек і не використовуй жодного NEXT-маркера; "
                    "репліка тримається на настрої, переході або зверненні до слухача."
                )
            story_subject_role = str(plan.get("story_subject_role") or "next")
            story_subject_rule = (
                "Історія стосується щойно зіграного [[CURRENT_TRACK]]. Можеш назвати його цим "
                "маркером один раз на початку або не повторювати назву. Завершення все одно веде "
                "до наступної пісні через обов'язковий NEXT-маркер."
                if story_subject_role == "current" else
                "Історія стосується наступного треку. Не називай його до фінального NEXT-маркера."
            )
            story_mode_rule = {
                "track_story": "Режим «Історія треку»: поясни, як народилася або змінилася саме ця пісня.",
                "artist_story": "Режим «Історія артиста»: покажи рису або подію виконавця лише через цей трек.",
                "interesting_fact": "Режим «Цікавий факт»: побудуй підводку навколо одного неочевидного перевіреного факту.",
                "nostalgia_era": "Режим «Ностальгія/епоха»: пов'яжи трек з його перевіреним контекстом часу або сцени.",
            }.get(plan.get("story_mode"), "Розкажи перевірену історію треку.")
            story_verification = (
                plan.get("story_source", {}).get("verification", {})
                if isinstance(plan.get("story_source"), dict) else {}
            )
            story_verification_status = str(
                story_verification.get("status") or "single_source"
            )
            serious_mode = bool(story_verification.get("sensitive"))
            story_evidence_rule = {
                "corroborated": (
                    "Картка має незалежне підтвердження. Не перебільшуй рівень певності "
                    "і не додавай фактів поза картою тверджень."
                ),
                "primary_source": (
                    "Картка спирається на першоджерело. Пояснення, мотив або оцінку "
                    "подавай як позицію автора чи виконавця, а не як всезагальний факт."
                ),
                "single_source": (
                    "Картка має одне надійне джерело. Не створюй враження незалежного "
                    "підтвердження і не посилюй категоричність формулювання."
                ),
            }.get(story_verification_status, "Не посилюй рівень певності картки.")
            story_detail_rule = {
                "short": "гачок і одна конкретна деталь",
                "feature": "гачок і до трьох конкретних деталей",
            }.get(length_class, "гачок і одна або дві конкретні деталі")
            story_rules = f"""
MUSIC STORY MODE:
Це блок Play Together: живий мінісюжет для рок-ефіру, не лекція й не суха довідка.
{story_mode_rule} Побудуй усну мініісторію тільки з VERIFIED_STORY_DATA: {story_detail_rule} й природний вихід у пісню. {story_subject_rule}
{story_evidence_rule}

Почни з унікального VERIFIED_STORY_HOOK або найсильнішої конкретної деталі. Не починай із загальної фрази про музику, життя, пам'ять, реальність чи «знайому мелодію». Кожне речення має додавати нову перевірену деталь. Не додавай порожнього морального висновку. Заверши фактичну історію до NEXT-маркера, а потім постав потрібний NEXT-маркер окремим останнім реченням без будь-яких слів поруч: система сама перетворить його на ефірний вихід. Якщо перевірених даних вистачає лише на три сильні речення, не розтягуй їх порожнім четвертим.

Не додавай жодного факту поза VERIFIED_STORY_DATA. Не вигадуй діалоги, цитати, місця, дати, реакції, причини чи емоції. Пряму мову використовуй тільки з VERIFIED_QUOTE й не видавай переказ за дослівну цитату. Якщо дані передають пояснення виконавця або висновок указаного джерела, чітко подай це як їхню позицію, а не як думку ведучого. Одна історія — одна головна думка.
""" if story_mode else ""
            program_rules = ""
            if plan.get("content_type") in {"fact", "story"}:
                program_rules = (
                    f"\nФОРМАТ: {settings.get('program_name', 'Play Together')}. "
                    "Це коротка телевізійна фактова міні-документалка, а не FM-оголошення. "
                    "Побудуй її як HOOK → VERIFIED FACT → TWIST → TRACK. Якщо перевірені дані "
                    "не містять окремого повороту, не вигадуй його: нехай поворотом стане сам контекст. "
                    "Одна підводка — одна історія, від одного до чотирьох речень.\n"
                )
            serious_mode_rule = (
                "\nСЕРЙОЗНИЙ РЕЖИМ АКТИВНИЙ: тема чутлива. Повністю вимкни гумор, "
                "сарказм, гру слів і легковажні образи. Назви джерело або рівень певності, "
                "не драматизуй і не роби власних висновків поза перевіреною карткою.\n"
                if serious_mode else ""
            )
            system_prompt = f"""{self.host_brain.persona_prompt()}
Ти не пишеш «красиві тексти», а говориш природно й розмовно як професійний ведучий у реальному ефірі та одразу для українського TTS.
{story_rules}
{program_rules}
{serious_mode_rule}

Говори одному слухачеві, ніби мікрофон щойно відкрився. Не будуй вступ,
основну частину й фінал. Почни одразу з живої думки та дотримайся
MENTION_POLICY нижче. Не нумеруй треки й не подавай їх як сходинки в музичному списку: це радіо по настрою.
Не завершуй автоматично словами «слухаємо», «додаємо гучності», «поїхали» або
«без зайвих слів». Не описуй власну роботу ведучого.
Не представляй станцію окремим реченням і не кажи «зараз ми у Києві».

НОВА РЕДАКЦІЙНА МОДЕЛЬ ({HOST_PROMPT_VERSION}): не наслідуй старі підводки й не збирай репліку з готових радіоформул. Починай із конкретної думки, образу або переданого факту саме цього переходу. Фрази з RECENT_OPENINGS і RECENT_STRUCTURES — негативні приклади, а не матеріал для перефразування. Не використовуй абстрактний вступ чи порожній підсумок. Окремий технічний NEXT-маркер дозволений лише в MUSIC STORY MODE і не є текстом для ефіру.

ДОВЖИНА: орієнтуйся на приблизно {length:.1f} секунди, але не рахуй слова механічно. Для звичайної підводки — одне або два живі речення; бажана ціль — {sentence_target}. Для факту або музичної історії — від одного до чотирьох змістовних речень відповідно до класу довжини. Не стискай думку до телеграфного оголошення, якщо є місце для нормальної людської фрази.

ГУМОР: максимум один основний жарт. Гумор сухий, природний, іноді самоіронічний. Не пояснюй жарт і не намагайся бути смішним у кожному реченні. Не вигадуй особистого досвіду ведучого.

НЕ ПОЧИНАЙ словами «А зараз», «Наступна композиція», «Цікаво знати», «Друзі» або «В ефірі». НЕ ВЖИВАЙ: «чесно кажучи», «ця композиція точно», «він заслужив увагу», «а ось і», «неймовірний хіт», «легендарний хіт», «пориньмо», «іноді музика виростає з реального життя», «за знайомою мелодією буває інша реальність», «так музика залишає свій слід», «і це лишається з нами», «а зараз в ефірі», банальні мотиваційні фрази, пафос і довгі вступи. Не став більше одного риторичного питання. Не повторюй автоматично жарти про каву.

ФАКТИ: про пісню або виконавця фактичне твердження дозволене лише з VERIFIED_FACT або VERIFIED_STORY_DATA. Час і погоду можна брати тільки з CONTEXT_JSON і лише коли цього просить CONTENT DIRECTOR. Якщо даних немає, не здогадуйся. Не говори про релізи, альбоми, жанр, популярність, музичні списки чи біографію без перевірених даних. Не приписуй виконавцю думок або дій.

КРИТИЧНЕ ПРАВИЛО НАЗВ: не пиши ім’я виконавця або назву пісні самостійно. Для поточного треку дозволені лише [[CURRENT_TRACK]], [[CURRENT_ARTIST]], [[CURRENT_TITLE]]. {naming_rule} Не відмінюй і не змінюй маркери. У режимі opening не використовуй CURRENT-маркери.

ПРАВИЛА TTS: усі числа пиши словами й граматично узгоджуй; не пиши цифрами час, температуру або кількість. Речення роби короткими. Не перевантажуй текст тире, дужками, двокрапками й лапками. Не додавай символів наголосу. Іноземні назви залишаються маркерами: їхню стабільну вимову підставить система.

ГРАМАТИКА: перед відповіддю мовчки перевір закінчення прикметників і числівників,
відмінок після «у», «в», «на», «до», «після» та узгодження роду. Не відмінюй
Система вставить назви замість дозволених маркерів після перевірки.

STYLE цього запиту: {style}. Напрям: {STYLE_GUIDANCE[style]}.
СТРУКТУРА: {structure}. Не повторюй структури з RECENT_STRUCTURES.
ТИП ПІДВОДКИ: {plan.get('intro_type') or 'listener_context'}. Це редакторське рішення; не змінюй тип і не змішуй кілька сюжетів.
MENTION_POLICY: {mention_policy}.
КЛАС ДОВЖИНИ: {length_class}. Підлаштуй природний темп репліки під доступний час, без підрахунку слів у тексті.
ФАЗА ЕФІРУ: {plan.get('session_phase') or context.get('session', {}).get('phase', 'flow')}.
РЕАКЦІЯ НА МУЗИКУ: {plan.get('reaction') or context.get('music_transition', {}).get('kind', 'neutral')}.

CONTENT DIRECTOR: {plan.get("directive", "Один гачок, максимум один жарт і природний вихід у пісню.")}
ANNOUNCE MODE: {plan.get("announce_mode", "forward")}. Режим задає порядок думки, але не скасовує MENTION_POLICY; `cold_open` починає без привітання, `station_id` не вигадує назв.

RUNDOWN: версія {plan.get('clock_version', 'без clock')}; слот {plan.get('clock_slot_id', '')} — {plan.get('clock_slot_name', '')}.
HARD TIME: {plan.get('hard_time', '') or 'немає'}; допустиме відхилення {plan.get('timing_tolerance_seconds', '') or 'не задано'} секунд.
ТЕЗА СЛОТА: {plan.get('thesis', '')}
ПОЛІТИКА ДЖЕРЕЛ: {plan.get('source_policy', '')}
ЗАБОРОНЕНІ ТВЕРДЖЕННЯ: {json.dumps(plan.get('forbidden_claims', []), ensure_ascii=False)}
ВІДПОВІДАЛЬНИЙ РЕДАКТОР: {plan.get('responsible_editor', 'НЕ ПРИЗНАЧЕНО')}.
ВХІД: {plan.get('entry_cue', '')} ВИХІД: {plan.get('exit_cue', '')} FALLBACK: {plan.get('fallback', '')}

VOICE DIRECTOR: {self.voice_director.prompt_directive(voice_profile)}

Налаштування характеру: гумор {humor}%, сарказм {sarcasm}%, енергійність {energy}%, розмовність {conversational}%, дозволений рівень фактів {effective_facts}%. Цільова тривалість {length:.1f} секунди.

Виводь тільки слова ведучого. Без зовнішніх лапок, пояснень, заголовків і позначок."""
            recent_openings = [
                item.get("opening", "") for item in self.db.recent_history(20)
                if item.get("opening")
            ]
            factual_context = {
                "time": context.get("time", {}),
                "weather": context.get("weather", {}),
                "station": context.get("station", {}),
                "same_artist_recently": context.get("same_artist_recently", False),
                "host_memory": context.get("host_memory", []),
                "session": context.get("session", {}),
                "music_transition": context.get("music_transition", {}),
                "track_character": {
                    "current_energy": (current or {}).get("energy"),
                    "next_energy": track.get("energy"),
                    "next_mood": track.get("mood"),
                    "next_genre": track.get("genre"),
                    "bpm": track.get("bpm"),
                },
                "announce_mode": plan.get("announce_mode", "forward"),
                "recent_openings_do_not_repeat": recent_openings,
                "recent_structures_do_not_repeat": context.get("session", {}).get("recent_structures", []),
            }
            request_text = f"""CURRENT_TRACK:
[[CURRENT_TRACK]]

NEXT_TRACK:
[[NEXT_TRACK]]

NEXT_ARTIST:
[[NEXT_ARTIST]]

NEXT_TITLE:
[[NEXT_TITLE]]

VERIFIED_FACT:
{(verified_fact or '').strip()}

VERIFIED_FACT_SOURCE:
{json.dumps(plan.get('fact_source', {}), ensure_ascii=False)}

VERIFIED_STORY_DATA:
{json.dumps([normalize_linguistic(str(item)) for item in plan.get('story_data', [])], ensure_ascii=False)}

VERIFIED_STORY_HOOK:
{normalize_linguistic(plan.get('story_hook', ''))}

VERIFIED_QUOTE:
{plan.get('verified_quote', '')}

STORY_SERIES_CALLBACK:
{plan.get('story_callback', '')}

STORY_SERIES_TEASE_NEXT:
{plan.get('story_tease_next', '')}

STORY_SOURCE_FOR_AUDIT_ONLY:
{json.dumps(plan.get('story_source', {}), ensure_ascii=False)}

CLAIM_EVIDENCE_MAP_FOR_AUDIT_ONLY:
{json.dumps(plan.get('story_source', {}).get('claims', []) if isinstance(plan.get('story_source'), dict) else [], ensure_ascii=False)}

STORY_SUBJECT_ROLE:
{plan.get('story_subject_role', 'next')}

TIME:
{context.get('time', {}).get('time', '')}

TIME_SPOKEN_EXACTLY:
{normalize_linguistic(context.get('time', {}).get('time', ''))}

MODE:
{"opening" if not current else "between_tracks"}

STYLE:
{style}

CONTEXT_JSON:
{json.dumps(factual_context, ensure_ascii=False)}"""
            try:
                variants_per_provider = max(
                    1, min(3, int(settings.get("intro_variants_per_provider", 2)))
                )
            except (TypeError, ValueError):
                variants_per_provider = 2
            generation_jobs = [
                (spec, variant_index)
                for spec in providers
                for variant_index in range(variants_per_provider)
            ]
            with ThreadPoolExecutor(
                max_workers=min(8, len(generation_jobs))
            ) as executor:
                futures = {
                    executor.submit(
                        _chat_completion,
                        spec,
                        system_prompt,
                        request_text + (
                            "\n\nVARIANT_ID:\n"
                            f"{variant_index + 1}\n"
                            "Створи самостійний варіант, не копіюй найбільш очевидний початок."
                        ),
                        temperature,
                        top_p,
                        max_tokens,
                    ): (spec, variant_index)
                    for spec, variant_index in generation_jobs
                }
                responses = []
                for future in as_completed(futures):
                    spec, variant_index = futures[future]
                    response = future.result()
                    response["variant"] = variant_index + 1
                    response.setdefault("provider", spec["name"])
                    responses.append(response)

            provider_priority = {
                spec["name"]: index for index, spec in enumerate(providers)
            }
            expected_time = normalize_linguistic(
                context.get("time", {}).get("time", "")
            ).casefold()
            weather_allowed = bool(plan.get("may_say_weather")) or _contains_weather_reference(
                " ".join([
                    str(verified_fact or ""),
                    *(str(item) for item in plan.get("story_data", []) or []),
                ])
            )
            banned = (
                "чесно кажучи", "ця композиція точно", "заслужив це місце", "заслужив увагу",
                "а ось і", "а от і", "неймовірний хіт", "легендарний хіт", "пориньмо",
                "поїхали", "музика все скаже сама", "просто слухаємо далі",
                "тепер слухаємо", "зараз слухаємо", "йдемо далі", "рухаємося далі",
            )
            forbidden_openings = (
                "а зараз", "наступна композиція", "цікаво знати", "друзі", "в ефірі",
            )
            valid_candidates = []
            errors = []
            next_markers = ("[[NEXT_TRACK]]", "[[NEXT_ARTIST]]", "[[NEXT_TITLE]]")
            provider_specs = {spec["name"]: spec for spec in providers}

            def inspect_host_response(response):
                provider_name = response.get("provider", "")
                variant_id = int(response.get("variant") or 1)
                candidate = _canonicalize_verified_track_mentions(
                    response.get("candidate", ""), track, mention_policy
                )
                if story_mode:
                    candidate = _ground_story_copy(candidate, plan)
                candidate_error = response.get("error", "")
                candidate_sentences = split_spoken_sentences(candidate)
                candidate_display = _replace_track_markers(candidate, track, current)
                candidate_linguistic = normalize_linguistic(candidate).casefold()
                candidate_word_count = spoken_word_count(candidate_display)
                language_warnings = _ukrainian_copy_warnings(
                    candidate,
                    allow_time_digits=bool(plan.get("must_say_time")),
                )
                lowered = candidate.casefold()
                if candidate_error:
                    return None, {
                        "provider": provider_name,
                        "variant": variant_id,
                        "ok": False,
                        "score": 0,
                        "error": candidate_error,
                        "candidate": candidate_display[:500],
                        "words": candidate_word_count,
                        "sentences": len(candidate_sentences),
                    }
                marker_counts = {
                    marker: candidate.count(marker) for marker in next_markers
                }
                if mention_policy == "artist_and_title":
                    marker_contract_ok = (
                        marker_counts["[[NEXT_TRACK]]"] == 1
                        and marker_counts["[[NEXT_ARTIST]]"] == 0
                        and marker_counts["[[NEXT_TITLE]]"] == 0
                    ) or (
                        marker_counts["[[NEXT_TRACK]]"] == 0
                        and marker_counts["[[NEXT_ARTIST]]"] == 1
                        and marker_counts["[[NEXT_TITLE]]"] == 1
                    )
                elif mention_policy == "artist_only":
                    marker_contract_ok = marker_counts == {
                        "[[NEXT_TRACK]]": 0,
                        "[[NEXT_ARTIST]]": 1,
                        "[[NEXT_TITLE]]": 0,
                    }
                elif mention_policy == "title_only":
                    marker_contract_ok = marker_counts == {
                        "[[NEXT_TRACK]]": 0,
                        "[[NEXT_ARTIST]]": 0,
                        "[[NEXT_TITLE]]": 1,
                    }
                else:
                    marker_contract_ok = not any(marker_counts.values())
                literal_contract_ok = False
                if not marker_contract_ok:
                    literal_contract_ok, _literal_contract_error = self.content_planner.quality_gate(
                        candidate_display, track, context, verified_fact,
                        plan.get("story_data", []), mention_policy=mention_policy,
                        structure=structure,
                    )
                if not marker_contract_ok and not literal_contract_ok:
                    candidate_error = "не дотримано контракту безпечних маркерів назви"
                elif _contains_unmarked_track_credit(candidate):
                    candidate_error = "сторонній трек названо без перевіреного маркера"
                elif plan.get("must_say_time") and expected_time not in candidate_linguistic:
                    candidate_error = "не названо точний станційний час"
                elif (
                    not plan.get("must_say_time")
                    and expected_time
                    and expected_time in candidate_linguistic
                ):
                    candidate_error = "час не запланований для цієї підводки"
                elif not weather_allowed and _contains_weather_reference(candidate):
                    candidate_error = "погода не запланована для цієї підводки"
                elif len(candidate_sentences) not in allowed_sentences:
                    candidate_error = "неправильна кількість речень"
                elif candidate_word_count < int(plan.get("word_min") or (14 if story_mode else 6)):
                    candidate_error = "репліка надто коротка для запланованого ефірного часу"
                elif candidate_word_count > voice_profile.target_words_max:
                    candidate_error = "репліка не вміщується у доступний ефірний час"
                elif candidate.count("?") > 1:
                    candidate_error = "забагато риторичних питань"
                elif candidate.casefold().lstrip(" \t\r\n«\"'—–-.,:;!?").startswith(forbidden_openings):
                    candidate_error = "заборонений початок підводки"
                elif any(phrase in lowered for phrase in banned):
                    candidate_error = "заборонений радіоштамп"
                elif _sounds_scripted(candidate):
                    candidate_error = "заскриптована підводка"
                elif story_mode and _unsupported_story_sentence(
                    candidate,
                    [plan.get("story_hook", ""), *(plan.get("story_data", []) or [])],
                ):
                    candidate_error = "додано деталь поза VERIFIED_STORY_DATA"
                elif language_warnings:
                    candidate_error = f"мовна помилка: {language_warnings[0]}"
                if candidate_error:
                    return None, {
                        "provider": provider_name,
                        "variant": variant_id,
                        "ok": False,
                        "score": 0,
                        "error": candidate_error,
                        "candidate": candidate_display[:500],
                        "words": candidate_word_count,
                        "sentences": len(candidate_sentences),
                    }
                word_count = candidate_word_count
                comfortable_words = max(18, duration_seconds * 2.6)
                pace_overflow = max(0.0, word_count - comfortable_words)
                ideal_words = min(
                    comfortable_words,
                    max(float(plan.get("word_min") or 0), duration_seconds * 2.0),
                )
                quality_score = 100.0
                quality_score -= abs(len(candidate_sentences) - sentence_target) * 6
                quality_score -= pace_overflow
                quality_score -= abs(word_count - ideal_words) * 0.35
                quality_score -= max(0, candidate.count("—") - 2) * 1.5
                quality_score += 3
                quality_score = round(max(0.0, quality_score), 2)
                item = {
                    "provider": provider_name,
                    "variant": variant_id,
                    "candidate": candidate,
                    "score": quality_score,
                    "word_count": word_count,
                    "warnings": language_warnings,
                    "grounded_story": story_mode,
                    "priority": provider_priority.get(response["provider"], len(providers)),
                }
                return item, {
                    "provider": provider_name,
                    "variant": variant_id,
                    "ok": True,
                    "score": quality_score,
                    "words": word_count,
                    "warnings": language_warnings,
                    "error": "",
                }

            repairable_errors = {
                "не дотримано контракту безпечних маркерів назви",
                "сторонній трек названо без перевіреного маркера",
                "погода не запланована для цієї підводки",
                "час не запланований для цієї підводки",
                "заборонений радіоштамп",
                "заборонений початок підводки",
                "заскриптована підводка",
                "додано деталь поза VERIFIED_STORY_DATA",
                "неправильна кількість речень",
                "репліка не вміщується у доступний ефірний час",
                "репліка надто коротка для запланованого ефірного часу",
            }
            # Spelling, punctuation, marker and factual gates are independent
            # for every candidate, so audit them concurrently as well.
            with ThreadPoolExecutor(
                max_workers=min(8, len(responses))
            ) as executor:
                inspected = list(executor.map(inspect_host_response, responses))

            repair_queue = []
            for response, (item, diagnostic) in zip(responses, inspected):
                diagnostic["stage"] = "initial"
                provider_diagnostics.append(diagnostic)
                if item is not None:
                    valid_candidates.append(item)
                    continue
                provider_name = response.get("provider", "")
                errors.append(
                    f"{provider_name}#{response.get('variant', 1)}: "
                    f"{diagnostic.get('error', '')}"
                )
                repair_allowed = bool(response.get("candidate")) and (
                    diagnostic.get("error") in repairable_errors
                    or str(diagnostic.get("error") or "").startswith("мовна помилка:")
                )
                if repair_allowed and provider_specs.get(provider_name):
                    repair_queue.append((response, diagnostic))

            # A second network round is only needed when every original failed.
            # All language-editor repairs run together instead of serially.
            if not valid_candidates and repair_queue:
                weather_rule = (
                    "Погоду можна залишити лише якщо вона вже є в оригіналі."
                    if weather_allowed else
                    "Повністю прибери погоду, температуру, спеку, дощ і прогноз."
                )
                time_rule = (
                    "Збережи точний час із початкової репліки."
                    if plan.get("must_say_time") else
                    "Повністю прибери точний час і будь-яке озвучення годин чи хвилин."
                )
                repair_marker_rule = (
                    f"Встав {required_marker} рівно один раз і назви трек тільки цим маркером."
                    if required_marker else
                    "Не називай трек і не додавай жодного NEXT-маркера."
                )
                repair_sentence_rule = (
                    "Три-п'ять природних змістовних речень."
                    if story_mode else "Одне-два природні речення."
                )
                editor_prompt = (
                    "Ти ефірний редактор і коректор української мови. Поверни тільки "
                    "виправлену репліку ведучого, без пояснень і зовнішніх лапок. "
                    "Виправ правопис, пунктуацію, граматичне керування й причину відхилення, "
                    "але не додавай нових фактів. "
                    f"{repair_marker_rule} {weather_rule} {time_rule} "
                    f"{repair_sentence_rule} Без радіоштампів і службового тексту."
                )
                with ThreadPoolExecutor(
                    max_workers=min(8, len(repair_queue))
                ) as executor:
                    repair_futures = {
                        executor.submit(
                            _chat_completion,
                            provider_specs[response.get("provider", "")],
                            editor_prompt,
                            json.dumps({
                                "rejectionReason": diagnostic.get("error", ""),
                                "original": response.get("candidate", ""),
                                "contentDirective": plan.get("directive", ""),
                                "verifiedStoryHook": normalize_linguistic(
                                    plan.get("story_hook", "")
                                ),
                                "verifiedStoryData": [
                                    normalize_linguistic(str(item))
                                    for item in plan.get("story_data", []) or []
                                ],
                            }, ensure_ascii=False),
                            min(temperature, 0.35),
                            min(top_p, 0.8),
                            min(max_tokens, 260),
                        ): (response, diagnostic)
                        for response, diagnostic in repair_queue
                    }
                    for future in as_completed(repair_futures):
                        original, initial_diagnostic = repair_futures[future]
                        repaired = future.result()
                        repaired["variant"] = original.get("variant", 1)
                        item, diagnostic = inspect_host_response(repaired)
                        diagnostic.update({"stage": "proofread", "repaired": True})
                        if repaired.get("error"):
                            diagnostic["repair_error"] = repaired.get("error", "")
                        provider_diagnostics.append(diagnostic)
                        if item is not None:
                            valid_candidates.append(item)
                        else:
                            errors.append(
                                f"{repaired.get('provider', '')}#{repaired.get('variant', 1)} "
                                f"proofread: {diagnostic.get('error', '')}"
                            )

            if valid_candidates:
                valid_candidates.sort(
                    key=lambda item: (
                        -item["score"],
                        item["priority"],
                        item["word_count"],
                    )
                )
                for item in valid_candidates:
                    candidate_display = polish_ukrainian_grammar(
                        _replace_track_markers(item["candidate"], track, current)
                    )
                    accepted, gate_error = self.content_planner.quality_gate(
                        candidate_display, track, context, verified_fact,
                        plan.get("story_data", []), mention_policy=mention_policy,
                        structure=structure,
                    )
                    if accepted:
                        ai_provider = item["provider"]
                        generated = item["candidate"]
                        generated_from_ai = True
                        selected_quality_score = item["score"]
                        selected_variant = item["variant"]
                        selected_story_grounded = bool(item.get("grounded_story"))
                        provider_error = ""
                        break
                    errors.append(f'{item["provider"]}: Quality gate: {gate_error}')
                    for diagnostic in provider_diagnostics:
                        if (
                            diagnostic.get("provider") == item["provider"]
                            and diagnostic.get("variant") == item["variant"]
                        ):
                            diagnostic["ok"] = False
                            diagnostic["error"] = f"Quality gate: {gate_error}"
                            break
                if not generated_from_ai:
                    provider_error = "; ".join(errors) or "AI не повернув придатного тексту"
            else:
                provider_error = "; ".join(errors) or "AI не повернув придатного тексту"
        display_text = polish_ukrainian_grammar(
            _replace_track_markers(generated, track, current)
        )
        if generated_from_ai:
            accepted, gate_error = self.content_planner.quality_gate(
                display_text, track, context, verified_fact,
                plan.get("story_data", []), mention_policy=mention_policy,
                structure=structure,
            )
            if not accepted:
                provider_error = f"Quality gate: {gate_error}"
                generated = contextual_fallback_copy(
                    track, current, style, context, plan, short_variant
                )
                display_text = polish_ukrainian_grammar(
                    _replace_track_markers(generated, track, current)
                )
        directed = self.voice_director.direct(
            display_text, tracks, context, track, duration_seconds, content_type
        )
        speech_text = directed["tts_text"]
        story_quality = (
            story_quality_score(display_text, speech_text, plan, track)
            if story_mode else {}
        )
        if store_track:
            self.db.update_track(
                track["id"],
                intro=display_text,
                intro_speech=speech_text,
                intro_style=style,
            )
        fallback = not bool(providers) or bool(provider_error)
        return {
            "ok": True,
            "intro": display_text,
            "display_text": display_text,
            "linguistic_text": directed["linguistic_text"],
            "speech_text": speech_text,
            "voice_profile": directed["profile"],
            "story_quality": story_quality,
            "needs_pronunciation_review": directed["needs_pronunciation_review"],
            "style": style,
            "fallback": fallback,
            "provider": "template" if fallback else ai_provider,
            "provider_error": provider_error,
            "provider_diagnostics": provider_diagnostics,
            "candidate_count": len(provider_diagnostics),
            "selected_variant": selected_variant,
            "grounded_story": selected_story_grounded,
            "quality_score": selected_quality_score,
            "spelling_checked": True,
        }

    def _record_rundown_transition(self, transition):
        try:
            plan = json.loads(transition.get("plan_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            return
        if not plan.get("clock_slot_id") or not transition.get("scheduled_for"):
            return
        planned_start = str(plan.get("planned_start") or "")
        try:
            hour_key = datetime.fromisoformat(planned_start).strftime("%Y-%m-%dT%H")
        except ValueError:
            hour_key = ""
        self.db.save_rundown_event({
            "clock_version": plan.get("clock_version", ""),
            "hour_key": hour_key,
            "slot_id": plan.get("clock_slot_id", ""),
            "hard_time": plan.get("hard_time", ""),
            "planned_for": transition.get("scheduled_for", ""),
            "timing_error_seconds": plan.get("timing_error_seconds"),
            "timing_status": "planned",
            "current_track_id": transition.get("current_track_id"),
            "next_track_id": transition.get("next_track_id"),
            "content_type": transition.get("content_type", ""),
            "responsible_editor": plan.get("responsible_editor", ""),
            "plan_json": json.dumps(plan, ensure_ascii=False),
        })

    def prepare_transition(
        self, current_track_id, next_track_id, scheduled_for=None,
        sequence_offset=0, force=False,
    ):
        current = self.db.track(int(current_track_id))
        next_track = self.db.track(int(next_track_id))
        if not current or not next_track:
            return {"ok": False, "error": "Трек переходу не знайдено"}
        existing = self.db.transition(current["id"], next_track["id"])
        now = datetime.now(timezone.utc)
        if existing and existing.get("status") == "ready" and not force:
            expires = existing.get("expires_at")
            try:
                old_schedule = datetime.fromisoformat(existing.get("scheduled_for") or "")
                new_schedule = datetime.fromisoformat(scheduled_for) if scheduled_for else now
                schedule_close = abs((old_schedule - new_schedule).total_seconds()) <= 600
                if expires and schedule_close and datetime.fromisoformat(expires).astimezone(timezone.utc) > now:
                    return {"ok": True, "transition": existing, "reused": True}
            except ValueError:
                pass

        context = self.context_engine.snapshot(current, next_track, scheduled_for)
        content_plan = self.content_planner.plan(context, int(sequence_offset))

        scheduled = scheduled_for or now.isoformat()
        self.db.save_transition({
            "current_track_id": current["id"],
            "next_track_id": next_track["id"],
            "status": "preparing",
            "content_type": content_plan.content_type,
            "style": content_plan.style,
            "context_json": json.dumps(context, ensure_ascii=False),
            "plan_json": json.dumps(content_plan.to_dict(), ensure_ascii=False),
            "scheduled_for": scheduled,
            "prepared_at": now.isoformat(),
        })

        settings = self.db.settings()
        director = TransitionDirector(settings.get("transition_duck_volume", 27))

        def save_clean_transition(provider="planner", provider_errors=None):
            mixer = director.plan(current, next_track, "clean_segue")
            plan_payload = {
                **content_plan.to_dict(),
                "mixer": mixer.to_dict(),
                "fallback_suppressed": provider == "ai-fallback-suppressed",
            }
            transition = self.db.save_transition({
                "current_track_id": current["id"],
                "next_track_id": next_track["id"],
                "status": "ready",
                "transition_type": mixer.transition_type,
                "content_type": "clean_segue",
                "style": content_plan.style,
                "context_json": json.dumps(context, ensure_ascii=False),
                "plan_json": json.dumps(plan_payload, ensure_ascii=False),
                "provider": provider,
                "provider_error": " | ".join(
                    item for item in (provider_errors or []) if item
                ),
                "scheduled_for": scheduled,
                "prepared_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=6)).isoformat(),
            })
            self.db.add_history({
                "current_track_id": current["id"],
                "next_track_id": next_track["id"],
                "content_type": "clean_segue",
                "style": content_plan.style,
                "structure": content_plan.structure or "silence",
                "mention_policy": content_plan.mention_policy,
                "length_class": content_plan.length_class,
                "rubric": content_plan.rubric,
                "intro_type": content_plan.intro_type,
                "opening": "",
                "display_text": "",
                "created_at": now.isoformat(),
            })
            self._record_rundown_transition(transition)
            return {"ok": True, "transition": transition, "reused": False}

        if content_plan.content_type == "clean_segue":
            return save_clean_transition("planner")

        provider_errors = []
        if content_plan.content_type == "liner":
            display_short = display_full = content_plan.liner_text or random.choice(LINERS)
            directed_short = self.voice_director.direct(
                display_short, self.db.tracks(), context, next_track, 4
            )
            directed_full = directed_short
            provider = "liner"
        else:
            pronunciation_source = str(
                next_track.get("pronunciation_source", "")
            ).casefold()
            should_refine_pronunciation = bool(
                pronunciation_source not in {"manual", "curated"}
                and not pronunciation_source.startswith("ai:")
                and (
                    next_track.get("pronunciation_review")
                    or float(next_track.get("artist_speech_confidence") or 0) < 0.95
                    or float(next_track.get("title_speech_confidence") or 0) < 0.95
                )
                and self._ai_providers_for_intro(settings)
            )
            with ThreadPoolExecutor(max_workers=2 if should_refine_pronunciation else 1) as executor:
                intro_future = executor.submit(
                    self.make_intro,
                    next_track["id"], current["id"], content_plan.verified_fact,
                    content_plan.style, context, content_plan.to_dict(),
                    content_plan.target_seconds, "full", False,
                )
                pronunciation_future = (
                    executor.submit(
                        self.generate_track_pronunciation,
                        next_track["id"], False,
                    )
                    if should_refine_pronunciation else None
                )
                try:
                    intro_result = intro_future.result()
                except Exception as exc:
                    LOGGER.exception("Parallel intro generation failed; using local copy")
                    provider_errors.append(f"AI intro: {exc}")
                    intro_result = self.make_intro(
                        next_track["id"], current["id"], content_plan.verified_fact,
                        content_plan.style, context, content_plan.to_dict(),
                        content_plan.target_seconds, "full", False,
                        providers_override=[],
                    )
                if pronunciation_future:
                    try:
                        pronunciation_result = pronunciation_future.result()
                    except Exception as exc:
                        LOGGER.exception("Parallel pronunciation generation failed")
                        pronunciation_result = {
                            "ok": False,
                            "error": f"Транскрипція: {exc}",
                        }
                else:
                    pronunciation_result = None
            strict_live_ai_host = str(
                settings.get("strict_live_ai_host", "0")
            ).strip().casefold() in {"1", "true", "yes", "on"}
            if strict_live_ai_host and intro_result.get("fallback"):
                return save_clean_transition(
                    "ai-fallback-suppressed",
                    [intro_result.get("provider_error", "")],
                )
            if (
                content_plan.content_type == "story"
                and float((intro_result.get("story_quality") or {}).get("final") or 0) < 8
            ):
                return save_clean_transition(
                    "story-quality-suppressed",
                    ["Story quality нижче 8/10"],
                )
            display_short = display_full = intro_result["display_text"]
            if pronunciation_result and pronunciation_result.get("ok"):
                refreshed_track = self.db.track(next_track["id"]) or next_track
                refreshed_tracks = self.db.tracks()
                directed_short = directed_full = self.voice_director.direct(
                    display_full, refreshed_tracks, context, refreshed_track,
                    content_plan.target_seconds, content_plan.content_type,
                )
            else:
                directed_short = directed_full = {
                    "linguistic_text": intro_result["linguistic_text"],
                    "tts_text": intro_result["speech_text"],
                    "profile": intro_result["voice_profile"],
                }
                if pronunciation_result and pronunciation_result.get("error"):
                    provider_errors.append(pronunciation_result["error"])
            provider = intro_result["provider"]
            if intro_result.get("provider_error"):
                provider_errors.append(intro_result["provider_error"])

        voice_asset = self._speech_asset(
            directed_short["tts_text"],
            rate=directed_short["profile"]["rate"],
        )
        if not voice_asset.get("ok"):
            provider_errors.append(
                voice_asset.get("error") or "TTS не підготував аудіо"
            )
            voice_asset.update({
                "path": "",
                "duration_ms": round(
                    max(1, spoken_word_count(directed_short["tts_text"])) / 2.4 * 1000
                ),
            })
        short_asset = full_asset = voice_asset

        mixer = director.plan(
            current, next_track, content_plan.content_type,
            short_asset["duration_ms"], full_asset["duration_ms"],
        )
        plan_payload = {**content_plan.to_dict(), "mixer": mixer.to_dict()}
        prepared_at = datetime.now(timezone.utc)
        transition = self.db.save_transition({
            "current_track_id": current["id"],
            "next_track_id": next_track["id"],
            "status": "ready",
            "transition_type": mixer.transition_type,
            "content_type": content_plan.content_type,
            "style": content_plan.style,
            "context_json": json.dumps(context, ensure_ascii=False),
            "plan_json": json.dumps(plan_payload, ensure_ascii=False),
            "display_short": display_short,
            "linguistic_short": directed_short["linguistic_text"],
            "speech_short": directed_short["tts_text"],
            "audio_short_path": short_asset["path"],
            "duration_short_ms": short_asset["duration_ms"],
            "display_full": display_full,
            "linguistic_full": directed_full["linguistic_text"],
            "speech_full": directed_full["tts_text"],
            "audio_full_path": full_asset["path"],
            "duration_full_ms": full_asset["duration_ms"],
            "voice_rate": directed_full["profile"]["rate"],
            "provider": provider,
            "provider_error": " | ".join(dict.fromkeys(provider_errors)),
            "scheduled_for": scheduled,
            "prepared_at": prepared_at.isoformat(),
            "expires_at": (prepared_at + timedelta(hours=6)).isoformat(),
        })
        self.db.add_history({
            "current_track_id": current["id"],
            "next_track_id": next_track["id"],
            "content_type": content_plan.content_type,
            "style": content_plan.style,
            "structure": content_plan.structure or content_plan.content_type,
            "mention_policy": content_plan.mention_policy,
            "length_class": content_plan.length_class,
            "rubric": content_plan.rubric,
            "intro_type": content_plan.intro_type,
            "opening": first_phrase(display_full),
            "display_text": display_full,
            "created_at": prepared_at.isoformat(),
        })
        self.host_brain.record_opening({
            "opening": first_phrase(display_full),
            "content_type": content_plan.content_type,
            "artist": next_track.get("artist", ""),
            "topic": content_plan.structure or content_plan.content_type,
            "entities": [
                value for value in (next_track.get("artist"), next_track.get("title"))
                if value
            ],
            "ending_type": content_plan.mention_policy,
            "energy": next_track.get("energy"),
        })
        self._record_rundown_transition(transition)
        return {"ok": True, "transition": transition, "reused": False}

    def prepare_transition_queue(self, track_ids, eta_seconds=0):
        ids = [int(value) for value in (track_ids or [])]
        if len(ids) < 2:
            return {"ok": False, "error": "Черга має містити щонайменше два треки"}
        if not self._prepare_lock.acquire(blocking=False):
            return {"ok": True, "busy": True, "prepared": []}
        prepared = []
        try:
            scheduled = datetime.now(timezone.utc) + timedelta(seconds=max(0, float(eta_seconds or 0)))
            depth = max(1, min(5, int(self.db.settings().get("pregen_depth", 4))))
            for offset, (current_id, next_id) in enumerate(zip(ids, ids[1:])):
                if offset >= depth:
                    break
                try:
                    result = self.prepare_transition(
                        current_id, next_id, scheduled.isoformat(), offset
                    )
                except Exception as exc:
                    LOGGER.exception(
                        "Transition preparation failed for %s -> %s",
                        current_id,
                        next_id,
                    )
                    result = {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "transition": {"status": "emergency"},
                    }
                transition = result.get("transition") or {}
                try:
                    rundown_plan = json.loads(transition.get("plan_json") or "{}")
                except (TypeError, json.JSONDecodeError):
                    rundown_plan = {}
                prepared.append({
                    "current_track_id": current_id,
                    "next_track_id": next_id,
                    "status": transition.get("status", "failed"),
                    "transition_type": transition.get("transition_type", "clean_segue"),
                    "content_type": transition.get("content_type", "none"),
                    "scheduled_for": transition.get("scheduled_for", ""),
                    "clock_version": rundown_plan.get("clock_version", ""),
                    "clock_slot_id": rundown_plan.get("clock_slot_id", ""),
                    "clock_slot_name": rundown_plan.get("clock_slot_name", ""),
                    "hard_time": rundown_plan.get("hard_time", ""),
                    "timing_error_seconds": rundown_plan.get("timing_error_seconds"),
                    "responsible_editor": rundown_plan.get("responsible_editor", ""),
                    "reused": bool(result.get("reused")),
                    "error": result.get("error", ""),
                })
                next_track = self.db.track(next_id) or {}
                duration_ms = int(next_track.get("duration_ms") or 210_000)
                scheduled += timedelta(milliseconds=duration_ms)
            return {"ok": True, "busy": False, "prepared": prepared}
        finally:
            self._prepare_lock.release()

    def get_prepared_transition(self, current_track_id, next_track_id, available_intro_ms=0):
        current = self.db.track(int(current_track_id)) or {}
        next_track = self.db.track(int(next_track_id)) or {}
        transition = self.db.transition(int(current_track_id), int(next_track_id))
        expired = False
        if transition and transition.get("expires_at"):
            try:
                expires_at = datetime.fromisoformat(transition["expires_at"])
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                expired = expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc)
            except ValueError:
                expired = True
        if not transition or transition.get("status") != "ready" or expired:
            if current and next_track:
                context = self.context_engine.snapshot(current, next_track)
                emergency_plan = {
                    "content_type": "talk",
                    "style": "straight_radio",
                    "structure": "announce",
                    "mention_policy": "artist_and_title",
                    "target_seconds": 8,
                    "directive": "Коротка аварійна підводка без зовнішнього API.",
                }
                display_text = polish_ukrainian_grammar(
                    _replace_track_markers(
                        contextual_fallback_copy(
                            next_track,
                            current,
                            "straight_radio",
                            context,
                            emergency_plan,
                            short=True,
                        ),
                        next_track,
                        current,
                    )
                )
                directed = self.voice_director.direct(
                    display_text,
                    self.db.tracks(),
                    context,
                    next_track,
                    8,
                )
                return {
                    "ok": True,
                    "status": "emergency",
                    "current_track_id": int(current_track_id),
                    "next_track_id": int(next_track_id),
                    "transition_type": "between",
                    "content_type": "emergency_talk",
                    "style": "straight_radio",
                    "display_text": display_text,
                    "speech_text": directed["tts_text"],
                    "audio": "",
                    "provider": "local-emergency",
                    "provider_error": (
                        "Підготовлена підводка застаріла"
                        if expired else "Підготовка не завершилася вчасно"
                    ),
                    "reason": (
                        "Підготовлений матеріал застарів; використано локальну підводку"
                        if expired else
                        "Готового TTS ще немає; використано локальну підводку без очікування API"
                    ),
                }
            return {
                "ok": True,
                "status": "emergency",
                "current_track_id": int(current_track_id),
                "next_track_id": int(next_track_id),
                "transition_type": "clean_segue",
                "content_type": "clean_segue",
                "reason": (
                    "Підготовлений матеріал застарів; запускаємо чистий segue без очікування API"
                    if expired else
                    "Готового TTS немає; запускаємо чистий segue без очікування API"
                ),
            }
        if available_intro_ms:
            next_track["vocal_start_ms"] = int(available_intro_ms)
        director = TransitionDirector(
            self.db.settings().get("transition_duck_volume", 27)
        )
        mixer = director.plan(
            current, next_track, transition.get("content_type", "none"),
            transition.get("duration_short_ms", 0),
            transition.get("duration_full_ms", 0),
        )
        variant = mixer.variant
        prefix = "short" if variant == "short" else "full"
        audio_path = transition.get(f"audio_{prefix}_path", "") if variant != "none" else ""
        return {
            "ok": True,
            "status": "ready",
            "id": transition["id"],
            "current_track_id": int(current_track_id),
            "next_track_id": int(next_track_id),
            "transition_type": mixer.transition_type,
            "content_type": transition.get("content_type", "none"),
            "style": transition.get("style", ""),
            "variant": variant,
            "display_text": transition.get(f"display_{prefix}", "") if variant != "none" else "",
            "speech_text": transition.get(f"speech_{prefix}", "") if variant != "none" else "",
            "audio": self._audio_data(audio_path),
            "voice_duration_ms": mixer.voice_duration_ms,
            "plan": mixer.to_dict(),
            "provider": transition.get("provider", ""),
            "provider_error": transition.get("provider_error", ""),
        }

    def mark_transition_aired(self, current_track_id, next_track_id):
        transition = self.db.transition(int(current_track_id), int(next_track_id))
        if transition:
            aired = datetime.now(timezone.utc)
            self.content_planner.mark_aired(transition, aired.isoformat())
            try:
                plan = json.loads(transition.get("plan_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                plan = {}
            self.db.add_listener_exposure(transition, plan, aired.isoformat())
            comparison_time = plan.get("hard_time") or transition.get("scheduled_for")
            timing_error = None
            timing_status = "aired"
            if comparison_time:
                try:
                    target = datetime.fromisoformat(comparison_time)
                    if target.tzinfo is None:
                        target = target.replace(tzinfo=timezone.utc)
                    timing_error = round(
                        (aired - target.astimezone(timezone.utc)).total_seconds(), 3
                    )
                    if plan.get("hard_time"):
                        tolerance = int(plan.get("timing_tolerance_seconds") or 5)
                        timing_status = (
                            "on_time" if abs(timing_error) <= tolerance
                            else "early" if timing_error < 0 else "late"
                        )
                except (TypeError, ValueError):
                    pass
            self.db.mark_rundown_aired(
                current_track_id,
                next_track_id,
                transition.get("scheduled_for", ""),
                aired.isoformat(),
                timing_error,
                timing_status,
            )
        return {"ok": True}

    def record_listener_feedback(
        self, track_id, action, listened_seconds=0, duration_seconds=0,
    ):
        action = str(action or "").strip().casefold()
        if action not in {"skip", "listened", "complete"}:
            return {"ok": False, "error": "Невідомий тип реакції слухача"}
        try:
            listened = max(0.0, float(listened_seconds or 0))
            duration = max(0.0, float(duration_seconds or 0))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Некоректна тривалість прослуховування"}
        ratio = min(1.0, listened / duration) if duration > 0 else (
            1.0 if action == "complete" else 0.0
        )
        exposure = self.db.resolve_listener_exposure(
            int(track_id), action, listened, ratio,
            datetime.now(timezone.utc).isoformat(),
        )
        if not exposure:
            return {
                "ok": True,
                "recorded": False,
                "listener_profile": self.personalization.profile(),
            }
        profile = self.personalization.update(
            exposure.get("intro_type", ""), action, ratio,
        )
        return {
            "ok": True,
            "recorded": True,
            "intro_type": exposure.get("intro_type", ""),
            "completion_ratio": round(ratio, 4),
            "listener_profile": profile,
        }

    def set_track_analysis(self, track_id, values):
        allowed = {
            "duration_ms", "bpm", "energy", "mood", "genre", "intro_end_ms",
            "vocal_start_ms", "outro_start_ms", "hard_end_ms", "end_type",
        }
        payload = {key: value for key, value in (values or {}).items() if key in allowed}
        if not self.db.track(int(track_id)):
            return {"ok": False, "error": "Трек не знайдено"}
        self.db.update_track(int(track_id), **payload)
        return {"ok": True, "track": self.db.track(int(track_id))}

    def add_track_fact(
        self, track_id, fact, verified=False, intro_type="music_fact",
        source_url="", source_title="",
    ):
        if not self.db.track(int(track_id)):
            return {"ok": False, "error": "Трек не знайдено"}
        fact = (fact or "").strip()
        if not fact:
            return {"ok": False, "error": "Факт порожній"}
        if len(split_spoken_sentences(fact)) > 1:
            return {"ok": False, "error": "Факт має бути одним реченням"}
        intro_type = normalize_intro_type(intro_type, "")
        if intro_type not in INTRO_TYPES:
            return {"ok": False, "error": "Невідомий тип факту"}
        source_url = str(source_url or "").strip()
        if bool(verified) and not source_url:
            return {"ok": False, "error": "Перевірений факт потребує HTTP(S) джерела"}
        if source_url and not re.match(r"^https?://", source_url, re.IGNORECASE):
            return {"ok": False, "error": "Джерело факту має бути повним HTTP(S) URL"}
        self.db.add_fact(
            int(track_id), fact, bool(verified), intro_type,
            source_url, str(source_title or "").strip(),
        )
        self.db.invalidate_transitions_for_track(int(track_id))
        return {"ok": True, "facts": self.db.facts_for_track(int(track_id), False)}

    def add_music_story(self, track_id, card):
        result = self.music_knowledge.add_card(int(track_id), card or {})
        if result.get("ok"):
            self.db.invalidate_transitions_for_track(int(track_id))
        return result

    def import_music_stories(self, payload):
        if not isinstance(payload, list):
            return {"ok": False, "error": "Корінь JSON має бути масивом треків"}
        tracks = {
            (track["artist"].casefold(), track["title"].casefold()): track
            for track in self.db.tracks()
        }
        imported = []
        errors = []
        for item in payload:
            if not isinstance(item, dict):
                errors.append("Запис треку в story JSON має бути об'єктом")
                continue
            artist = str(item.get("artist") or "").strip()
            title = str(item.get("title") or "").strip()
            track = tracks.get((artist.casefold(), title.casefold()))
            if not track:
                errors.append(f"Не знайдено трек: {artist} — {title}")
                continue
            stories = item.get("stories") or []
            if not isinstance(stories, list):
                errors.append(f"stories не є масивом: {artist} — {title}")
                continue
            for offset, card in enumerate(stories, 1):
                if not isinstance(card, dict):
                    errors.append(
                        f"{artist} — {title}, story {offset}: картка має бути об'єктом"
                    )
                    continue
                result = self.add_music_story(track["id"], card)
                if result.get("ok"):
                    imported.append(result["story"])
                else:
                    errors.append(
                        f"{artist} — {title}, story {offset}: {result.get('error')}"
                    )
        return {"ok": not errors, "imported": imported, "errors": errors}

    def music_stories(self, track_id, verified_only=True):
        if not self.db.track(int(track_id)):
            return {"ok": False, "error": "Трек не знайдено"}
        return {
            "ok": True,
            "stories": self.music_knowledge.cards_for_track(
                int(track_id), bool(verified_only)
            ),
        }

    def mark_played(self, track_id):
        track = next((x for x in self.db.tracks() if x["id"] == int(track_id)), None)
        if track:
            self.db.update_track(track["id"], play_count=track["play_count"] + 1, last_played=datetime.now(timezone.utc).isoformat())
        return {"ok": True}
