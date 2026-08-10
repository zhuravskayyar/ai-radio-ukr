import re


LINE = re.compile(r"^\s*(?:(\d{1,3})\s*[.)-]\s*)?(.+?)\s+[-–—]\s+(.+?)\s*$")


def parse_chart(text: str):
    tracks = []
    seen = set()
    for line in text.splitlines():
        match = LINE.match(line)
        if not match:
            continue
        rank, artist, title = match.groups()
        key = (artist.casefold(), title.casefold())
        if key in seen:
            continue
        seen.add(key)
        tracks.append({"rank": int(rank or len(tracks) + 1), "artist": artist.strip(), "title": title.strip()})
    return sorted(tracks, key=lambda item: item["rank"])
