import html
import re
import unicodedata


NOISE = {
    "official", "video", "audio", "lyrics", "lyric", "music", "hd", "hq",
    "remastered", "remaster", "visualizer", "clip", "version", "topic",
}


def tokens(value: str):
    value = html.unescape(value or "").casefold()
    value = unicodedata.normalize("NFKD", value)
    words = re.findall(r"[\w]+", value, flags=re.UNICODE)
    return [word for word in words if word not in NOISE and len(word) > 1]


def coverage(expected: str, actual: str):
    wanted = set(tokens(expected))
    present = set(tokens(actual))
    return len(wanted & present) / len(wanted) if wanted else 0.0


def match_score(artist: str, title: str, youtube_title: str, channel: str = ""):
    title_score = coverage(title, youtube_title)
    artist_score = max(coverage(artist, youtube_title), coverage(artist, channel))
    score = title_score * 0.68 + artist_score * 0.32
    return round(score, 4), round(title_score, 4), round(artist_score, 4)


def is_confident(artist: str, title: str, youtube_title: str, channel: str = ""):
    score, title_score, artist_score = match_score(artist, title, youtube_title, channel)
    # Artist verification is mandatory; this prevents generic titles such as
    # "You", "Home" or "Intro" from matching an unrelated upload.
    return score >= 0.72 and title_score >= 0.67 and artist_score >= 0.5
