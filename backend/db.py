import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .speech_normalizer import automatic_pronunciations, suggested_pronunciations


def _needs_pronunciation_review(artist, title, artist_speech, title_speech):
    artist_latin = any("a" <= char.casefold() <= "z" for char in artist)
    title_latin = any("a" <= char.casefold() <= "z" for char in title)
    return int(
        (artist_latin and not artist_speech) or
        (title_latin and not title_speech)
    )


def _auto_pronunciation_values(artist, title):
    values = automatic_pronunciations(artist, title)
    values["pronunciation_review"] = int(
        float(values["artist_speech_confidence"]) < 0.9
        or float(values["title_speech_confidence"]) < 0.9
    )
    return values


DEFAULTS = {
    "station_name": "LUMEN RADIO",
    "host_name": "Люмен",
    "station_city": "Київ",
    "station_timezone": "Europe/Kyiv",
    "chart_name": "Play Together",
    "rotation": "random",
    "host_every": "1",
    "host_humor": "42",
    "host_sarcasm": "28",
    "host_energy": "72",
    "host_conversational": "92",
    "host_facts": "80",
    "host_length": "16",
    "host_sentences": "2",
    "intro_bed_volume": "10",
    "transition_duck_volume": "27",
    "program_volume": "75",
    "pregen_depth": "2",
    "queue_size": "10",
    "queue_refill_threshold": "7",
    "queue_critical_threshold": "2",
    "artist_cooldown_tracks": "15",
    "track_cooldown_tracks": "200",
    "queue_min_duration": "120",
    "queue_max_duration": "480",
    "queue_cache_max_gb": "3",
    "dynamic_discovery_enabled": "1",
    "licensed_sources_confirmed": "0",
    "station_prompt": "Сучасний альт рок, український і російськомовний alternative/indie rock; без музейного старого росроку типу Цоя, Кино, ДДТ, Би-2, Алисы чи Аквариума; чергуй впізнавані та свіжі треки, без попси й каверів.",
    "station_prompt_en": "",
    "station_prompt_en_source": "",
    "ai_playlist_prompt": "",
    "ai_previous_playlist": "[]",
    "talk_probability": "100",
    "silence_probability": "0",
    "rubric_probability": "12",
    "story_probability": "100",
    "story_every": "2",
    "fact_probability": "70",
    "program_name": "Play Together",
    "language_style": "casual_uk",
    "colloquiality": "0.30",
    "surzhyk": "0.08",
    "slang": "0.15",
    "weather_enabled": "0",
    "weather_latitude": "50.4501",
    "weather_longitude": "30.5234",
    "weather_refresh_minutes": "30",
    "autostart_radio": "1",
    "use_styletts": "1",
    "ai_temperature": "0.78",
    "ai_top_p": "0.90",
    "ai_max_tokens": "1000",
    "youtube_api_key": "",
    "nvidia_api_key": "",
    "nvidia_model": "nvidia/nemotron-3-super-120b-a12b",
    "primary_ai_provider": "nvidia",
    "dj_ai_provider": "parallel",
    "host_ai_provider": "secondary",
    "pronunciation_ai_provider": "auto",
    "strict_live_ai_host": "0",
    "intro_variants_per_provider": "2",
    # RadioAPI owns the active prompt revision. A neutral persisted default
    # lets it detect both existing installations and brand-new databases.
    "host_prompt_version": "0",
    "secondary_api_enabled": "0",
    "secondary_api_url": "https://openrouter.ai/api/v1/chat/completions",
    "secondary_api_key": "",
    "secondary_model": "deepseek/deepseek-v4-flash",
}


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.setup()

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def setup(self):
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rank INTEGER NOT NULL,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    artist_speech TEXT DEFAULT '',
                    title_speech TEXT DEFAULT '',
                    youtube_id TEXT DEFAULT '',
                    youtube_title TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    intro TEXT DEFAULT '',
                    intro_speech TEXT DEFAULT '',
                    intro_style TEXT DEFAULT '',
                    previous_rank INTEGER,
                    chart_weeks INTEGER DEFAULT 1,
                    duration_ms INTEGER DEFAULT 0,
                    bpm REAL DEFAULT 0,
                    energy REAL DEFAULT 5,
                    mood TEXT DEFAULT '',
                    genre TEXT DEFAULT '',
                    intro_end_ms INTEGER DEFAULT 0,
                    vocal_start_ms INTEGER DEFAULT 0,
                    outro_start_ms INTEGER DEFAULT 0,
                    hard_end_ms INTEGER DEFAULT 0,
                    end_type TEXT DEFAULT 'unknown',
                    artist_speech_confidence REAL DEFAULT 0,
                    title_speech_confidence REAL DEFAULT 0,
                    pronunciation_review INTEGER DEFAULT 0,
                    artist_language TEXT DEFAULT '',
                    title_language TEXT DEFAULT '',
                    pronunciation_source TEXT DEFAULT '',
                    match_score REAL DEFAULT 0,
                    local_path TEXT DEFAULT '',
                    local_only INTEGER DEFAULT 0,
                    library_source TEXT DEFAULT '',
                    play_count INTEGER DEFAULT 0,
                    last_played TEXT,
                    UNIQUE(artist, title)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    current_track_id INTEGER NOT NULL,
                    next_track_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'preparing',
                    transition_type TEXT NOT NULL DEFAULT 'clean_segue',
                    content_type TEXT NOT NULL DEFAULT 'none',
                    style TEXT DEFAULT '',
                    context_json TEXT DEFAULT '{}',
                    plan_json TEXT DEFAULT '{}',
                    display_short TEXT DEFAULT '',
                    linguistic_short TEXT DEFAULT '',
                    speech_short TEXT DEFAULT '',
                    audio_short_path TEXT DEFAULT '',
                    duration_short_ms INTEGER DEFAULT 0,
                    display_full TEXT DEFAULT '',
                    linguistic_full TEXT DEFAULT '',
                    speech_full TEXT DEFAULT '',
                    audio_full_path TEXT DEFAULT '',
                    duration_full_ms INTEGER DEFAULT 0,
                    voice_rate TEXT DEFAULT '+0%',
                    provider TEXT DEFAULT '',
                    provider_error TEXT DEFAULT '',
                    scheduled_for TEXT,
                    prepared_at TEXT,
                    expires_at TEXT,
                    UNIQUE(current_track_id, next_track_id)
                );
                CREATE TABLE IF NOT EXISTS host_memory (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    last_used_at TEXT,
                    cooldown_until TEXT,
                    use_count INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS intro_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    current_track_id INTEGER,
                    next_track_id INTEGER,
                    content_type TEXT DEFAULT '',
                    style TEXT DEFAULT '',
                    structure TEXT DEFAULT '',
                    mention_policy TEXT DEFAULT '',
                    length_class TEXT DEFAULT '',
                    rubric TEXT DEFAULT '',
                    opening TEXT DEFAULT '',
                    display_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS track_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER NOT NULL,
                    fact TEXT NOT NULL,
                    verified INTEGER DEFAULT 0,
                    last_used_at TEXT,
                    use_count INTEGER DEFAULT 0,
                    UNIQUE(track_id, fact)
                );
                CREATE TABLE IF NOT EXISTS music_stories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER NOT NULL,
                    story_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    hook TEXT NOT NULL DEFAULT '',
                    story_data_json TEXT NOT NULL DEFAULT '[]',
                    verified_quote TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',
                    source_title TEXT NOT NULL DEFAULT '',
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    claims_json TEXT NOT NULL DEFAULT '[]',
                    verification_status TEXT NOT NULL DEFAULT 'draft',
                    broadcast_ready INTEGER NOT NULL DEFAULT 0,
                    reviewed_by TEXT NOT NULL DEFAULT '',
                    reviewed_at TEXT NOT NULL DEFAULT '',
                    sensitive INTEGER NOT NULL DEFAULT 0,
                    confidence TEXT NOT NULL DEFAULT 'draft',
                    duration_class TEXT NOT NULL DEFAULT 'normal',
                    series_key TEXT NOT NULL DEFAULT '',
                    episode INTEGER NOT NULL DEFAULT 0,
                    tease_next TEXT NOT NULL DEFAULT '',
                    last_used_at TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(track_id, story_key)
                );
                CREATE TABLE IF NOT EXISTS weather_cache (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    observed_at TEXT,
                    fetched_at TEXT
                );
                CREATE TABLE IF NOT EXISTS host_openings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    opening TEXT NOT NULL,
                    content_type TEXT DEFAULT '',
                    artist TEXT DEFAULT '',
                    topic TEXT DEFAULT '',
                    entities_json TEXT DEFAULT '[]',
                    ending_type TEXT DEFAULT 'track_launch',
                    energy REAL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS radio_queue (
                    position INTEGER PRIMARY KEY,
                    track_id INTEGER NOT NULL UNIQUE,
                    source_query TEXT DEFAULT '',
                    added_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS radio_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER,
                    source_id TEXT DEFAULT '',
                    artist TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    played_at TEXT NOT NULL
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(tracks)")}
            if "match_score" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN match_score REAL DEFAULT 0")
            if "local_path" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN local_path TEXT DEFAULT ''")
            if "local_only" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN local_only INTEGER DEFAULT 0")
            if "artist_speech" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN artist_speech TEXT DEFAULT ''")
            if "title_speech" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN title_speech TEXT DEFAULT ''")
            if "intro_speech" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN intro_speech TEXT DEFAULT ''")
            if "intro_style" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN intro_style TEXT DEFAULT ''")
            track_columns = {
                "previous_rank": "INTEGER",
                "chart_weeks": "INTEGER DEFAULT 1",
                "duration_ms": "INTEGER DEFAULT 0",
                "bpm": "REAL DEFAULT 0",
                "energy": "REAL DEFAULT 5",
                "mood": "TEXT DEFAULT ''",
                "genre": "TEXT DEFAULT ''",
                "intro_end_ms": "INTEGER DEFAULT 0",
                "vocal_start_ms": "INTEGER DEFAULT 0",
                "outro_start_ms": "INTEGER DEFAULT 0",
                "hard_end_ms": "INTEGER DEFAULT 0",
                "end_type": "TEXT DEFAULT 'unknown'",
                "artist_speech_confidence": "REAL DEFAULT 0",
                "title_speech_confidence": "REAL DEFAULT 0",
                "pronunciation_review": "INTEGER DEFAULT 0",
                "artist_language": "TEXT DEFAULT ''",
                "title_language": "TEXT DEFAULT ''",
                "pronunciation_source": "TEXT DEFAULT ''",
                "library_source": "TEXT DEFAULT ''",
            }
            for name, declaration in track_columns.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE tracks ADD COLUMN {name} {declaration}")
            story_columns = {
                row[1] for row in db.execute("PRAGMA table_info(music_stories)")
            }
            for name, declaration in {
                "sources_json": "TEXT NOT NULL DEFAULT '[]'",
                "claims_json": "TEXT NOT NULL DEFAULT '[]'",
                "verification_status": "TEXT NOT NULL DEFAULT 'draft'",
                "broadcast_ready": "INTEGER NOT NULL DEFAULT 0",
                "reviewed_by": "TEXT NOT NULL DEFAULT ''",
                "reviewed_at": "TEXT NOT NULL DEFAULT ''",
                "sensitive": "INTEGER NOT NULL DEFAULT 0",
            }.items():
                if name not in story_columns:
                    db.execute(f"ALTER TABLE music_stories ADD COLUMN {name} {declaration}")
            history_columns = {
                row[1] for row in db.execute("PRAGMA table_info(intro_history)")
            }
            for name in ("structure", "mention_policy", "length_class", "rubric"):
                if name not in history_columns:
                    db.execute(
                        f"ALTER TABLE intro_history ADD COLUMN {name} TEXT DEFAULT ''"
                    )
            for key, value in DEFAULTS.items():
                db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
            db.execute(
                "UPDATE settings SET value='15' WHERE key='host_length' AND value='25'"
            )
            db.execute("UPDATE settings SET value='4' WHERE key='host_sentences' AND value='5'")
            db.execute("UPDATE settings SET value='random' WHERE key='rotation' AND value='chart'")
            db.execute("UPDATE settings SET value='Play Together' WHERE key='chart_name' AND value='LUMEN TOP 100'")
            db.execute("UPDATE settings SET value='1' WHERE key='host_every' AND value='3'")
            db.execute("UPDATE settings SET value='100' WHERE key='talk_probability' AND value='35'")
            db.execute(
                "UPDATE settings SET value='1000' WHERE key='ai_max_tokens' AND value='360'"
            )
            for artist, old_speech, new_speech in (
                ("The Offspring", "Ді Офспрінг", "Зе Офспрінг"),
                ("Three Days Grace", "срі дейс ґрасе", "Трі Дейз Ґрейс"),
                ("Black Sabbath", "блак саббас", "Блек Саббат"),
            ):
                db.execute(
                    "UPDATE tracks SET artist_speech=?, pronunciation_source='curated', "
                    "artist_speech_confidence=1 WHERE artist=? AND artist_speech=?",
                    (new_speech, artist, old_speech),
                )
            for artist, speech in (
                ("The Offspring", "Зе Офспрінг"),
                ("Three Days Grace", "Трі Дейз Ґрейс"),
                ("Black Sabbath", "Блек Саббат"),
            ):
                db.execute(
                    "UPDATE tracks SET artist_speech=?, pronunciation_source='curated', "
                    "artist_speech_confidence=1 WHERE artist=?",
                    (speech, artist),
                )
            db.execute(
                "UPDATE tracks SET title_speech='Х''юмен Рейс', pronunciation_source='curated', "
                "title_speech_confidence=1 WHERE title='Human Race' AND title_speech='гуман расе'"
            )
            schema_version = db.execute("PRAGMA user_version").fetchone()[0]
            if schema_version < 2:
                # One-time migration to the new default broadcast policy. User
                # choices made after this migration remain persistent.
                db.execute("UPDATE settings SET value='random' WHERE key='rotation'")
                db.execute("UPDATE settings SET value='1' WHERE key='host_every'")
                db.execute("UPDATE settings SET value='100' WHERE key='talk_probability'")
                db.execute("PRAGMA user_version=2")
            if schema_version < 3:
                # Shorter, less scripted links and one CPU TTS job at a time.
                db.execute("UPDATE settings SET value='7' WHERE key='host_length'")
                db.execute("UPDATE settings SET value='2' WHERE key='host_sentences'")
                db.execute("UPDATE settings SET value='35' WHERE key='host_humor'")
                db.execute("UPDATE settings SET value='30' WHERE key='story_probability'")
                db.execute("UPDATE settings SET value='1' WHERE key='pregen_depth'")
                db.execute("UPDATE settings SET value='0.78' WHERE key='ai_temperature'")
                db.execute("DELETE FROM transitions")
                db.execute("PRAGMA user_version=3")
            if schema_version < 4:
                # Drop prepared links made with the older long fallback copy.
                db.execute("DELETE FROM transitions")
                db.execute("PRAGMA user_version=4")
            if schema_version < 5:
                # Rebuild links after tightening the anti-cliche quality gate.
                db.execute("DELETE FROM transitions")
                db.execute("PRAGMA user_version=5")
            if schema_version < 6:
                # Rebuild links with compact, de-duplicated artist credits.
                db.execute("DELETE FROM transitions")
                db.execute("PRAGMA user_version=6")
            if schema_version < 7:
                # Prefer regular stories and rebuild speech with multilingual
                # editable phonetic spellings.
                db.execute("UPDATE settings SET value='70' WHERE key='story_probability'")
                db.execute("UPDATE settings SET value='4' WHERE key='story_every'")
                db.execute("DELETE FROM transitions")
                db.execute("PRAGMA user_version=7")
            if schema_version < 8:
                # Rebuild automatic values with the audited multilingual
                # dictionary. Manual edits always remain authoritative.
                for row in db.execute("SELECT * FROM tracks").fetchall():
                    automatic = _auto_pronunciation_values(row["artist"], row["title"])
                    source = row["pronunciation_source"] or ""
                    if source in ("", "auto_local") or automatic["pronunciation_source"] == "curated":
                        db.execute(
                            """UPDATE tracks SET
                            artist_speech=?,title_speech=?,
                            artist_speech_confidence=?,title_speech_confidence=?,
                            pronunciation_review=?,artist_language=?,title_language=?,
                            pronunciation_source=? WHERE id=?""",
                            (
                                automatic["artist_speech"], automatic["title_speech"],
                                automatic["artist_speech_confidence"],
                                automatic["title_speech_confidence"],
                                automatic["pronunciation_review"],
                                automatic["artist_language"], automatic["title_language"],
                                automatic["pronunciation_source"], row["id"],
                            ),
                        )
                db.execute("DELETE FROM transitions")
                db.execute("PRAGMA user_version=8")
            if schema_version < 9:
                # Introduce the Lumen persona and rebuild prepared copy with
                # structure memory, varied lengths, rare silence and rubrics.
                db.execute("UPDATE settings SET value='Люмен' WHERE key='host_name'")
                db.execute("DELETE FROM transitions")
                db.execute("PRAGMA user_version=9")
            if schema_version < 10:
                # Persistent ten-track music buffer. Voice transition depth is
                # intentionally separate and remains small for CPU StyleTTS.
                db.execute("DELETE FROM radio_queue")
                db.execute("PRAGMA user_version=10")
            if schema_version < 11:
                # The on-air library is now curated by AI and populated only
                # after LUMEN Downloader has produced a verified local file.
                db.execute(
                    """UPDATE tracks SET library_source='ai'
                    WHERE local_path LIKE 'downloads/queue/%'
                    AND match_score>=0.75"""
                )
                db.execute(
                    "UPDATE settings SET value='1' WHERE key='dynamic_discovery_enabled'"
                )
                db.execute("DELETE FROM radio_queue")
                db.execute("PRAGMA user_version=11")
            if schema_version < 12:
                # Real provider verification showed that DeepSeek currently
                # returns substantially more exact artist/title pairs than
                # NVIDIA. Keep both available and let plan quality choose.
                db.execute(
                    "UPDATE settings SET value='parallel' "
                    "WHERE key='dj_ai_provider' AND value='nvidia'"
                )
                db.execute("PRAGMA user_version=12")
            if schema_version < 13:
                # Reliability-first live policy: keep an emergency spoken link
                # when an AI provider fails, prepare the next two links, and
                # make sourced music stories the preferred recurring feature.
                db.execute(
                    "UPDATE settings SET value='0' "
                    "WHERE key='strict_live_ai_host' AND value='1'"
                )
                db.execute(
                    "UPDATE settings SET value='0' "
                    "WHERE key='silence_probability' AND value IN ('2','7','10')"
                )
                db.execute(
                    "UPDATE settings SET value='100' "
                    "WHERE key='story_probability' AND value IN ('30','60','70','85')"
                )
                db.execute(
                    "UPDATE settings SET value='2' "
                    "WHERE key='pregen_depth' AND value='1'"
                )
                db.execute("DELETE FROM transitions")
                db.execute("PRAGMA user_version=13")
            if schema_version < 14:
                # Preserve legacy single-source cards while making their audit
                # trail explicit. New cards can carry independent corroboration
                # and claim-level source references.
                for row in db.execute("SELECT * FROM music_stories").fetchall():
                    source_url = row["source_url"] or ""
                    sources = []
                    if source_url:
                        sources = [{
                            "id": "source-1",
                            "url": source_url,
                            "title": row["source_title"] or "",
                            "tier": "B",
                            "primary": False,
                            "independent": True,
                        }]
                    try:
                        story_data = json.loads(row["story_data_json"] or "[]")
                    except (TypeError, json.JSONDecodeError):
                        story_data = []
                    claims = [
                        {"text": part, "source_ids": ["source-1"]}
                        for part in story_data if sources and isinstance(part, str)
                    ]
                    verified = row["confidence"] == "verified" and bool(sources)
                    db.execute(
                        """UPDATE music_stories SET sources_json=?,claims_json=?,
                        verification_status=?,broadcast_ready=? WHERE id=?""",
                        (
                            json.dumps(sources, ensure_ascii=False),
                            json.dumps(claims, ensure_ascii=False),
                            "single_source" if verified else "draft",
                            int(verified),
                            row["id"],
                        ),
                    )
                db.execute("DELETE FROM transitions")
                db.execute("PRAGMA user_version=14")
            db.execute(
                "UPDATE settings SET value=? "
                "WHERE key='ai_max_tokens' AND value IN ('160','220','320','360')",
                (DEFAULTS["ai_max_tokens"],),
            )
            db.execute(
                "UPDATE settings SET value='16' WHERE key='host_length' AND value IN ('7','12','15')"
            )
            db.execute(
                "UPDATE settings SET value='85' WHERE key='story_probability' AND value IN ('30','60','70')"
            )
            db.execute(
                "UPDATE settings SET value='2' WHERE key='story_every' AND value IN ('4','5','6','7','8')"
            )
            db.execute(
                "UPDATE settings SET value='2' WHERE key='silence_probability' AND value IN ('7','10')"
            )
            db.execute(
                "UPDATE settings SET value='12' WHERE key='rubric_probability' AND value IN ('6')"
            )
            db.execute("UPDATE settings SET value='Остап' WHERE key='host_name' AND value='Марта'")
            db.execute(
                "UPDATE settings SET value=? WHERE key='nvidia_model' AND value=?",
                (DEFAULTS["nvidia_model"], "qwen/qwen3-next-80b-a3b-instruct"),
            )
            db.execute(
                "UPDATE settings SET value=? WHERE key='nvidia_model' AND value=?",
                (DEFAULTS["nvidia_model"], "qwen/qwen3.5-397b-a17b"),
            )
            db.execute(
                "UPDATE settings SET value=? WHERE key='nvidia_model' AND value=?",
                (DEFAULTS["nvidia_model"], "nvidia/nemotron-3-nano-30b-a3b"),
            )
            db.execute(
                "UPDATE settings SET value=? WHERE key='secondary_model' AND value=?",
                (DEFAULTS["secondary_model"], "deepseek/deepseek-v4-flash-latest"),
            )
            db.execute(
                "UPDATE settings SET value=? WHERE key='station_prompt' AND value=?",
                (
                    DEFAULTS["station_prompt"],
                    "Темна нічна електроніка, alternative, darkwave, atmospheric rock; чергуй відомі та маловідомі треки без веселого поп-звучання.",
                ),
            )
            for row in db.execute("SELECT * FROM tracks"):
                automatic = _auto_pronunciation_values(row["artist"], row["title"])
                updates = {}
                for field in (
                    "artist_speech", "title_speech", "artist_speech_confidence",
                    "title_speech_confidence", "artist_language", "title_language",
                    "pronunciation_source",
                ):
                    if not row[field]:
                        updates[field] = automatic[field]
                if updates:
                    assignments = ",".join(f"{field}=?" for field in updates)
                    db.execute(
                        f"UPDATE tracks SET {assignments},pronunciation_review=? WHERE id=?",
                        (*updates.values(), automatic["pronunciation_review"], row["id"]),
                    )
            db.execute(
                """UPDATE tracks SET pronunciation_review=1
                WHERE pronunciation_review=0 AND (
                    (artist GLOB '*[A-Za-z]*' AND artist_speech='') OR
                    (title GLOB '*[A-Za-z]*' AND title_speech='')
                )"""
            )

    def tracks(self):
        with closing(self.connect()) as db, db:
            return [
                dict(row) for row in db.execute(
                    """SELECT tracks.*,
                    (SELECT COUNT(*) FROM music_stories
                     WHERE music_stories.track_id=tracks.id
                     AND music_stories.confidence='verified'
                     AND music_stories.broadcast_ready=1) AS story_count,
                    (SELECT COUNT(*) FROM music_stories
                     WHERE music_stories.track_id=tracks.id
                     AND music_stories.confidence='verified'
                     AND music_stories.broadcast_ready=1
                     AND music_stories.verification_status='corroborated') AS story_corroborated_count
                    FROM tracks ORDER BY rank"""
                )
            ]

    def replace_tracks(self, tracks):
        with closing(self.connect()) as db, db:
            existing = {
                (row["artist"].casefold(), row["title"].casefold()): dict(row)
                for row in db.execute("SELECT * FROM tracks")
            }
            incoming = set()
            for track in tracks:
                key = (track["artist"].casefold(), track["title"].casefold())
                incoming.add(key)
                suggested = _auto_pronunciation_values(track["artist"], track["title"])
                old = existing.get(key)
                if old:
                    previous_rank = old["rank"] if old["rank"] != track["rank"] else old["previous_rank"]
                    db.execute(
                        """UPDATE tracks SET previous_rank=?,rank=?,local_only=0,
                        chart_weeks=COALESCE(chart_weeks,0)+1 WHERE id=?""",
                        (previous_rank, track["rank"], old["id"]),
                    )
                else:
                    db.execute(
                        """INSERT INTO tracks(
                            rank,artist,title,artist_speech,title_speech,
                            artist_speech_confidence,title_speech_confidence,
                            pronunciation_review,artist_language,title_language,
                            pronunciation_source
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            track["rank"], track["artist"], track["title"],
                            suggested["artist_speech"], suggested["title_speech"],
                            suggested["artist_speech_confidence"],
                            suggested["title_speech_confidence"],
                            suggested["pronunciation_review"],
                            suggested["artist_language"], suggested["title_language"],
                            suggested["pronunciation_source"],
                        ),
                    )
            for key, old in existing.items():
                if key not in incoming and not old["local_only"]:
                    db.execute("DELETE FROM tracks WHERE id=?", (old["id"],))

    def merge_tracks(self, tracks):
        with closing(self.connect()) as db, db:
            prepared = []
            for track in tracks:
                pronunciation = _auto_pronunciation_values(track["artist"], track["title"])
                prepared.append({
                    **track,
                    **pronunciation,
                })
            db.executemany(
                """INSERT INTO tracks(
                    rank,artist,title,artist_speech,title_speech,
                    artist_speech_confidence,title_speech_confidence,
                    pronunciation_review,artist_language,title_language,
                    pronunciation_source
                ) VALUES(
                    :rank,:artist,:title,:artist_speech,:title_speech,
                    :artist_speech_confidence,:title_speech_confidence,
                    :pronunciation_review,:artist_language,:title_language,
                    :pronunciation_source
                )
                ON CONFLICT(artist,title) DO UPDATE SET
                    previous_rank=tracks.rank,
                    rank=excluded.rank,
                    chart_weeks=COALESCE(tracks.chart_weeks,0)+1,
                    local_only=0""",
                prepared,
            )

    def add_host_opening(self, entry: dict):
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO host_openings(opening,content_type,artist,topic,entities_json,ending_type,energy,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    entry.get("opening", ""),
                    entry.get("content_type", ""),
                    entry.get("artist", ""),
                    entry.get("topic", ""),
                    json.dumps(entry.get("entities", []), ensure_ascii=False),
                    entry.get("ending_type", "track_launch"),
                    entry.get("energy", None),
                    entry.get("created_at") or datetime.now(timezone.utc).isoformat(),
                ),
            )

    def recent_host_openings(self, limit: int = 50):
        with closing(self.connect()) as db, db:
            rows = db.execute(
                "SELECT * FROM host_openings ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            result = []
            for r in rows:
                result.append({
                    "id": r["id"],
                    "opening": r["opening"],
                    "content_type": r["content_type"],
                    "artist": r["artist"],
                    "topic": r["topic"],
                    "entities": json.loads(r["entities_json"] or "[]"),
                    "ending_type": r["ending_type"],
                    "energy": r["energy"],
                    "created_at": r["created_at"],
                })
            return result

    def add_local_track(self, artist, title, local_path):
        """Create a temporary library row for an audio file outside the chart."""
        with closing(self.connect()) as db, db:
            row = db.execute(
                "SELECT id FROM tracks WHERE artist=? AND title=?",
                (artist, title),
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE tracks SET local_path=? WHERE id=?",
                    (local_path, row["id"]),
                )
                track_id = row["id"]
            else:
                rank = db.execute("SELECT COALESCE(MAX(rank), 0) + 1 FROM tracks").fetchone()[0]
                pronunciation = _auto_pronunciation_values(artist, title)
                cursor = db.execute(
                    """INSERT INTO tracks(
                        rank,artist,title,artist_speech,title_speech,
                        artist_speech_confidence,title_speech_confidence,
                        pronunciation_review,artist_language,title_language,
                        pronunciation_source,local_path,local_only
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (
                        rank, artist, title,
                        pronunciation["artist_speech"], pronunciation["title_speech"],
                        pronunciation["artist_speech_confidence"],
                        pronunciation["title_speech_confidence"],
                        pronunciation["pronunciation_review"],
                        pronunciation["artist_language"], pronunciation["title_language"],
                        pronunciation["pronunciation_source"],
                        local_path,
                    ),
                )
                track_id = cursor.lastrowid
            return dict(db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone())

    def update_track(self, track_id, **values):
        allowed = {
            "youtube_id", "youtube_title", "status", "intro", "intro_speech",
            "intro_style", "artist_speech", "title_speech", "match_score",
            "local_path", "play_count", "last_played", "previous_rank",
            "chart_weeks", "duration_ms", "bpm", "energy", "mood", "genre",
            "intro_end_ms", "vocal_start_ms", "outro_start_ms",
            "hard_end_ms", "end_type", "artist_speech_confidence",
            "title_speech_confidence", "pronunciation_review",
            "artist_language", "title_language", "pronunciation_source",
            "library_source",
        }
        values = {k: v for k, v in values.items() if k in allowed}
        if not values:
            return
        assignments = ",".join(f"{key}=?" for key in values)
        with closing(self.connect()) as db, db:
            db.execute(f"UPDATE tracks SET {assignments} WHERE id=?", (*values.values(), track_id))

    def clear_local_paths(self):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE tracks SET local_path='' WHERE local_path<>''")

    def purge_ai_library(self):
        """Remove generated-library state while preserving non-AI catalog rows."""
        with closing(self.connect()) as db, db:
            rows = db.execute(
                "SELECT id FROM tracks WHERE library_source='ai'"
            ).fetchall()
            track_ids = [int(row["id"]) for row in rows]
            db.execute("DELETE FROM radio_queue")
            if not track_ids:
                return 0

            placeholders = ",".join("?" for _ in track_ids)
            db.execute(
                f"DELETE FROM transitions WHERE current_track_id IN ({placeholders}) "
                f"OR next_track_id IN ({placeholders})",
                (*track_ids, *track_ids),
            )
            db.execute(
                f"DELETE FROM track_facts WHERE track_id IN ({placeholders})",
                track_ids,
            )
            db.execute(
                f"DELETE FROM music_stories WHERE track_id IN ({placeholders})",
                track_ids,
            )
            db.execute(
                f"DELETE FROM intro_history WHERE current_track_id IN ({placeholders}) "
                f"OR next_track_id IN ({placeholders})",
                (*track_ids, *track_ids),
            )
            db.execute(
                f"DELETE FROM tracks WHERE id IN ({placeholders}) AND local_only=1",
                track_ids,
            )
            db.execute(
                f"""UPDATE tracks SET
                youtube_id='',youtube_title='',status='pending',local_path='',
                library_source='',match_score=0
                WHERE id IN ({placeholders})""",
                track_ids,
            )
            return len(track_ids)

    def consume_ai_track(self, track_id):
        """Remove one generated AI-library track after it aired once."""
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT * FROM tracks WHERE id=?", (int(track_id),)).fetchone()
            if not row or row["library_source"] != "ai":
                return False
            db.execute("DELETE FROM radio_queue WHERE track_id=?", (int(track_id),))
            db.execute(
                "DELETE FROM transitions WHERE current_track_id=? OR next_track_id=?",
                (int(track_id), int(track_id)),
            )
            db.execute("DELETE FROM track_facts WHERE track_id=?", (int(track_id),))
            db.execute("DELETE FROM music_stories WHERE track_id=?", (int(track_id),))
            db.execute(
                "DELETE FROM intro_history WHERE current_track_id=? OR next_track_id=?",
                (int(track_id), int(track_id)),
            )
            if int(row["local_only"] or 0):
                db.execute("DELETE FROM tracks WHERE id=?", (int(track_id),))
            else:
                db.execute(
                    """UPDATE tracks SET
                    youtube_id='',youtube_title='',status='pending',local_path='',
                    library_source='',match_score=0,intro='',intro_speech='',
                    intro_style='' WHERE id=?""",
                    (int(track_id),),
                )
            return True

    def settings(self):
        with closing(self.connect()) as db, db:
            return {row["key"]: row["value"] for row in db.execute("SELECT * FROM settings")}

    def save_settings(self, values):
        with closing(self.connect()) as db, db:
            db.executemany(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(key, str(value)) for key, value in values.items() if key in DEFAULTS],
            )

    def track(self, track_id):
        with closing(self.connect()) as db, db:
            row = db.execute(
                """SELECT tracks.*,
                (SELECT COUNT(*) FROM music_stories
                 WHERE music_stories.track_id=tracks.id
                 AND music_stories.confidence='verified'
                 AND music_stories.broadcast_ready=1) AS story_count,
                (SELECT COUNT(*) FROM music_stories
                 WHERE music_stories.track_id=tracks.id
                 AND music_stories.confidence='verified'
                 AND music_stories.broadcast_ready=1
                 AND music_stories.verification_status='corroborated') AS story_corroborated_count
                FROM tracks WHERE tracks.id=?""",
                (track_id,),
            ).fetchone()
            return dict(row) if row else None

    def transition(self, current_track_id, next_track_id):
        with closing(self.connect()) as db, db:
            row = db.execute(
                """SELECT * FROM transitions
                WHERE current_track_id=? AND next_track_id=?""",
                (current_track_id, next_track_id),
            ).fetchone()
            return dict(row) if row else None

    def save_transition(self, values):
        allowed = {
            "current_track_id", "next_track_id", "status", "transition_type",
            "content_type", "style", "context_json", "plan_json",
            "display_short", "linguistic_short", "speech_short",
            "audio_short_path", "duration_short_ms", "display_full",
            "linguistic_full", "speech_full", "audio_full_path",
            "duration_full_ms", "voice_rate", "provider", "provider_error",
            "scheduled_for", "prepared_at", "expires_at",
        }
        payload = {key: value for key, value in values.items() if key in allowed}
        if not payload.get("current_track_id") or not payload.get("next_track_id"):
            raise ValueError("Transition requires current_track_id and next_track_id")
        columns = list(payload)
        update_columns = [
            column for column in columns
            if column not in {"current_track_id", "next_track_id"}
        ]
        placeholders = ",".join("?" for _ in columns)
        updates = ",".join(f"{column}=excluded.{column}" for column in update_columns)
        with closing(self.connect()) as db, db:
            db.execute(
                f"""INSERT INTO transitions({','.join(columns)})
                VALUES({placeholders})
                ON CONFLICT(current_track_id,next_track_id) DO UPDATE SET {updates}""",
                [payload[column] for column in columns],
            )
        return self.transition(payload["current_track_id"], payload["next_track_id"])

    def invalidate_transitions_for_track(self, track_id):
        with closing(self.connect()) as db, db:
            db.execute(
                "DELETE FROM transitions WHERE next_track_id=?",
                (int(track_id),),
            )

    def invalidate_all_transitions(self):
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM transitions")

    def clear_generated_host_copy(self):
        """Drop host text generated by an older prompt revision.

        Music, pronunciation, verified stories and aired-track history stay
        intact. Only reusable host copy and its anti-repetition history are
        reset so an old link can never be aired after a prompt upgrade.
        """
        with closing(self.connect()) as db, db:
            track_count = db.execute(
                """SELECT COUNT(*) FROM tracks
                WHERE intro<>'' OR intro_speech<>'' OR intro_style<>''"""
            ).fetchone()[0]
            transition_count = db.execute(
                "SELECT COUNT(*) FROM transitions"
            ).fetchone()[0]
            history_count = db.execute(
                "SELECT COUNT(*) FROM intro_history"
            ).fetchone()[0]
            opening_count = db.execute(
                "SELECT COUNT(*) FROM host_openings"
            ).fetchone()[0]
            db.execute(
                "UPDATE tracks SET intro='',intro_speech='',intro_style=''"
            )
            db.execute("DELETE FROM transitions")
            db.execute("DELETE FROM intro_history")
            db.execute("DELETE FROM host_openings")
        return {
            "tracks": int(track_count),
            "transitions": int(transition_count),
            "history": int(history_count),
            "openings": int(opening_count),
        }

    def recent_history(self, limit=50):
        with closing(self.connect()) as db, db:
            return [
                dict(row) for row in db.execute(
                    "SELECT * FROM intro_history ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                )
            ]

    def radio_queue(self):
        with closing(self.connect()) as db, db:
            return [
                dict(row) for row in db.execute(
                    """SELECT radio_queue.*,tracks.artist,tracks.title,
                    tracks.local_path,tracks.youtube_id
                    FROM radio_queue
                    LEFT JOIN tracks ON tracks.id=radio_queue.track_id
                    ORDER BY radio_queue.position"""
                )
            ]

    def replace_radio_queue(self, entries):
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM radio_queue")
            db.executemany(
                """INSERT INTO radio_queue(position,track_id,source_query,added_at)
                VALUES(?,?,?,?)""",
                [
                    (
                        position,
                        int(entry["track_id"]),
                        entry.get("source_query", ""),
                        entry.get("added_at", ""),
                    )
                    for position, entry in enumerate(entries)
                ],
            )

    def add_radio_history(self, track, played_at):
        with closing(self.connect()) as db, db:
            db.execute(
                """INSERT INTO radio_history(
                    track_id,source_id,artist,title,played_at
                ) VALUES(?,?,?,?,?)""",
                (
                    track.get("id"), track.get("youtube_id", ""),
                    track.get("artist", ""), track.get("title", ""), played_at,
                ),
            )

    def recent_radio_history(self, limit=200):
        with closing(self.connect()) as db, db:
            return [
                dict(row) for row in db.execute(
                    "SELECT * FROM radio_history ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                )
            ]

    def add_history(self, values):
        with closing(self.connect()) as db, db:
            db.execute(
                """INSERT INTO intro_history(
                    current_track_id,next_track_id,content_type,style,structure,
                    mention_policy,length_class,rubric,opening,display_text,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    values.get("current_track_id"), values.get("next_track_id"),
                    values.get("content_type", ""), values.get("style", ""),
                    values.get("structure", ""),
                    values.get("mention_policy", ""),
                    values.get("length_class", ""), values.get("rubric", ""),
                    values.get("opening", ""), values.get("display_text", ""),
                    values.get("created_at", ""),
                ),
            )

    def memory(self, key):
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT * FROM host_memory WHERE key=?", (key,)).fetchone()
            return dict(row) if row else None

    def memory_items(self, limit=20):
        with closing(self.connect()) as db, db:
            return [
                dict(row) for row in db.execute(
                    """SELECT * FROM host_memory
                    ORDER BY COALESCE(last_used_at,'') DESC LIMIT ?""",
                    (int(limit),),
                )
            ]

    def remember(self, key, value_json="{}", last_used_at=None, cooldown_until=None):
        with closing(self.connect()) as db, db:
            db.execute(
                """INSERT INTO host_memory(
                    key,value_json,last_used_at,cooldown_until,use_count
                ) VALUES(?,?,?,?,1)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    last_used_at=excluded.last_used_at,
                    cooldown_until=excluded.cooldown_until,
                    use_count=host_memory.use_count+1""",
                (key, value_json, last_used_at, cooldown_until),
            )

    def facts_for_track(self, track_id, verified_only=True):
        query = "SELECT * FROM track_facts WHERE track_id=?"
        params = [track_id]
        if verified_only:
            query += " AND verified=1"
        query += " ORDER BY COALESCE(last_used_at,'') ASC,use_count ASC,id ASC"
        with closing(self.connect()) as db, db:
            return [dict(row) for row in db.execute(query, params)]

    def add_fact(self, track_id, fact, verified=False):
        with closing(self.connect()) as db, db:
            db.execute(
                """INSERT INTO track_facts(track_id,fact,verified)
                VALUES(?,?,?) ON CONFLICT(track_id,fact) DO UPDATE SET
                verified=MAX(track_facts.verified,excluded.verified)""",
                (track_id, fact.strip(), int(bool(verified))),
            )

    def mark_fact_used(self, fact_id, used_at):
        with closing(self.connect()) as db, db:
            db.execute(
                """UPDATE track_facts SET last_used_at=?,use_count=use_count+1
                WHERE id=?""",
                (used_at, fact_id),
            )

    def stories_for_track(self, track_id, verified_only=True):
        query = "SELECT * FROM music_stories WHERE track_id=?"
        params = [track_id]
        if verified_only:
            query += " AND confidence='verified' AND broadcast_ready=1"
        query += """ ORDER BY
            CASE verification_status
                WHEN 'corroborated' THEN 0
                WHEN 'primary_source' THEN 1
                WHEN 'single_source' THEN 2
                ELSE 9
            END,
            use_count ASC,COALESCE(last_used_at,'') ASC,episode ASC,id ASC"""
        with closing(self.connect()) as db, db:
            return [dict(row) for row in db.execute(query, params)]

    def add_story(self, values):
        columns = (
            "track_id", "story_key", "category", "hook", "story_data_json",
            "verified_quote", "source_url", "source_title", "sources_json",
            "claims_json", "verification_status", "broadcast_ready", "reviewed_by",
            "reviewed_at", "sensitive", "confidence",
            "duration_class", "series_key", "episode", "tease_next",
            "created_at",
        )
        payload = {column: values.get(column, "") for column in columns}
        if not payload["sources_json"] and payload["source_url"]:
            payload["sources_json"] = json.dumps([{
                "id": "source-1",
                "url": payload["source_url"],
                "title": payload["source_title"],
                "tier": "B",
                "primary": False,
                "independent": True,
            }], ensure_ascii=False)
        if not payload["claims_json"] and payload["sources_json"]:
            try:
                story_data = json.loads(payload["story_data_json"] or "[]")
            except (TypeError, json.JSONDecodeError):
                story_data = []
            payload["claims_json"] = json.dumps([
                {"text": part, "source_ids": ["source-1"]}
                for part in story_data if isinstance(part, str)
            ], ensure_ascii=False)
        if not payload["verification_status"]:
            payload["verification_status"] = (
                "single_source"
                if payload["confidence"] == "verified" and payload["source_url"]
                else "draft"
            )
        payload["episode"] = int(payload.get("episode") or 0)
        payload["broadcast_ready"] = int(
            bool(payload.get("broadcast_ready"))
            or (
                "broadcast_ready" not in values
                and payload["confidence"] == "verified"
                and bool(payload["source_url"])
            )
        )
        payload["sensitive"] = int(bool(payload.get("sensitive")))
        with closing(self.connect()) as db, db:
            db.execute(
                f"""INSERT INTO music_stories({','.join(columns)})
                VALUES({','.join('?' for _ in columns)})
                ON CONFLICT(track_id,story_key) DO UPDATE SET
                    category=excluded.category,
                    hook=excluded.hook,
                    story_data_json=excluded.story_data_json,
                    verified_quote=excluded.verified_quote,
                    source_url=excluded.source_url,
                    source_title=excluded.source_title,
                    sources_json=excluded.sources_json,
                    claims_json=excluded.claims_json,
                    verification_status=excluded.verification_status,
                    broadcast_ready=excluded.broadcast_ready,
                    reviewed_by=excluded.reviewed_by,
                    reviewed_at=excluded.reviewed_at,
                    sensitive=excluded.sensitive,
                    confidence=excluded.confidence,
                    duration_class=excluded.duration_class,
                    series_key=excluded.series_key,
                    episode=excluded.episode,
                    tease_next=excluded.tease_next""",
                [payload[column] for column in columns],
            )
            row = db.execute(
                "SELECT * FROM music_stories WHERE track_id=? AND story_key=?",
                (payload["track_id"], payload["story_key"]),
            ).fetchone()
            return dict(row)

    def mark_story_used(self, story_id, used_at):
        with closing(self.connect()) as db, db:
            db.execute(
                """UPDATE music_stories SET last_used_at=?,use_count=use_count+1
                WHERE id=?""",
                (used_at, story_id),
            )

    def weather(self):
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT * FROM weather_cache WHERE id=1").fetchone()
            return dict(row) if row else None

    def save_weather(self, payload_json, observed_at, fetched_at):
        with closing(self.connect()) as db, db:
            db.execute(
                """INSERT INTO weather_cache(id,payload_json,observed_at,fetched_at)
                VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET
                payload_json=excluded.payload_json,
                observed_at=excluded.observed_at,
                fetched_at=excluded.fetched_at""",
                (payload_json, observed_at, fetched_at),
            )
