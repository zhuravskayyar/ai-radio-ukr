import logging
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


LOGGER = logging.getLogger(__name__)


class RadioQueueManager:
    """Persistent self-refilling music buffer, separate from TTS pre-generation."""

    def __init__(self, db, root: Path, discoverer=None, random_source=None):
        self.db = db
        self.root = Path(root)
        self.discoverer = discoverer
        self.random = random_source or random.Random()
        self._lock = threading.RLock()
        self._refill_thread = None
        self._last_error = ""
        self._phase = "idle"
        self._retry_after = 0.0
        self._stopping = False

    def _int(self, key, default, minimum=0, maximum=10_000):
        try:
            value = int(float(self.db.settings().get(key, default)))
        except (TypeError, ValueError):
            value = int(default)
        return max(minimum, min(maximum, value))

    def _enabled_discovery(self):
        state = self._discovery_state()
        return state["requested"] and state["rights_confirmed"]

    def _discovery_state(self):
        settings = self.db.settings()
        truthy = {"1", "true", "yes", "on"}
        requested = str(
            settings.get("dynamic_discovery_enabled", "0")
        ).casefold() in truthy
        rights_confirmed = str(
            settings.get("licensed_sources_confirmed", "0")
        ).casefold() in truthy
        if not requested:
            blocked_reason = "AI-пошук треків вимкнений у налаштуваннях"
        elif not rights_confirmed:
            blocked_reason = (
                "Пошук готовий, але потрібне підтвердження права "
                "завантажувати й відтворювати вибрані джерела"
            )
        else:
            blocked_reason = ""
        return {
            "requested": requested,
            "rights_confirmed": rights_confirmed,
            "blocked_reason": blocked_reason,
        }

    def _target(self):
        return self._int("queue_size", 10, 3, 20)

    def _thresholds(self):
        target = self._target()
        refill = self._int("queue_refill_threshold", 7, 1, target - 1)
        critical = self._int("queue_critical_threshold", 2, 1, refill)
        return target, refill, critical

    def _path_exists(self, track):
        local_path = str(track.get("local_path") or "").strip()
        return bool(local_path and (self.root / local_path).is_file())

    def _duration_allowed(self, track):
        duration_ms = int(track.get("duration_ms") or 0)
        if duration_ms <= 0:
            return True
        minimum = self._int("queue_min_duration", 120, 30, 3600) * 1000
        maximum = self._int("queue_max_duration", 480, 60, 7200) * 1000
        return minimum <= duration_ms <= maximum

    @staticmethod
    def _artist_key(track):
        return " ".join(str((track or {}).get("artist") or "").casefold().split())

    def _playable_tracks(self):
        return [
            track for track in self.db.tracks()
            if self._path_exists(track)
            and self._duration_allowed(track)
            and str(track.get("library_source") or "") == "ai"
            and float(track.get("match_score") or 0) >= 0.75
        ]

    def _current_entries(self):
        entries = []
        seen = set()
        tracks = {track["id"]: track for track in self._playable_tracks()}
        previous_artist = ""
        for row in self.db.radio_queue():
            track_id = int(row["track_id"])
            if track_id in seen or track_id not in tracks:
                continue
            artist = self._artist_key(tracks[track_id])
            if artist and artist == previous_artist:
                continue
            entries.append({
                "track_id": track_id,
                "source_query": row.get("source_query", ""),
                "added_at": row.get("added_at", ""),
            })
            seen.add(track_id)
            previous_artist = artist
        return entries

    def _sanitize_entries(self, entries):
        tracks = {int(track["id"]): track for track in self._playable_tracks()}
        sanitized = []
        seen = set()
        previous_artist = ""
        for entry in entries:
            track_id = int(entry["track_id"])
            if track_id in seen or track_id not in tracks:
                continue
            artist = self._artist_key(tracks[track_id])
            if artist and artist == previous_artist:
                continue
            sanitized.append(entry)
            seen.add(track_id)
            previous_artist = artist
        return sanitized

    def _save(self, entries):
        self.db.replace_radio_queue(self._sanitize_entries(entries)[: self._target()])

    def _candidate(self, entries):
        queued_ids = {int(entry["track_id"]) for entry in entries}
        playable = self._playable_tracks()
        tracks_by_id = {int(track["id"]): track for track in playable}
        previous_artist = ""
        if entries:
            previous_artist = self._artist_key(tracks_by_id.get(int(entries[-1]["track_id"])))
        history = self.db.recent_radio_history(
            max(
                self._int("track_cooldown_tracks", 200, 1, 2000),
                self._int("artist_cooldown_tracks", 15, 1, 200),
            )
        )
        track_cooldown = self._int("track_cooldown_tracks", 200, 1, 2000)
        artist_cooldown = self._int("artist_cooldown_tracks", 15, 1, 200)
        recent_track_ids = {
            int(item["track_id"]) for item in history[:track_cooldown]
            if item.get("track_id") is not None
        }
        recent_artists = {
            str(item.get("artist") or "").casefold()
            for item in history[:artist_cooldown]
        }
        pool = [
            track for track in playable
            if track["id"] not in queued_ids
            and self._artist_key(track) != previous_artist
        ]
        strict = [
            track for track in pool
            if track["id"] not in recent_track_ids
            and track.get("artist", "").casefold() not in recent_artists
        ]
        if strict:
            pool = strict
        else:
            without_artist_repeat = [
                track for track in pool
                if track.get("artist", "").casefold() not in recent_artists
            ]
            pool = without_artist_repeat or pool
        if not pool:
            return None

        flow = (3.5, 4.8, 6.2, 7.8, 5.5, 3.0)
        target_energy = flow[len(entries) % len(flow)]
        prompt_words = {
            word for word in self.db.settings().get("station_prompt", "").casefold().split()
            if len(word) >= 4
        }

        def score(track):
            energy = float(track.get("energy") or 5)
            energy_score = 1 - min(1, abs(energy - target_energy) / 7)
            style_text = f'{track.get("genre", "")} {track.get("mood", "")}'.casefold()
            style_score = min(1, sum(word in style_text for word in prompt_words) / 3)
            freshness = 1 / (1 + float(track.get("play_count") or 0) * 0.25)
            story_bonus = 0.08 if int(track.get("story_count") or 0) else 0
            return (
                style_score * 0.35 + energy_score * 0.35
                + freshness * 0.22 + story_bonus + self.random.random() * 0.08
            )

        return max(pool, key=score)

    def _consume_ai_track(self, track):
        if not track or str(track.get("library_source") or "") != "ai":
            return False
        local_path = str(track.get("local_path") or "").strip()
        if local_path:
            path = (self.root / local_path).resolve()
            downloads_dir = (self.root / "downloads").resolve()
            try:
                if downloads_dir in path.parents and path.is_file():
                    path.unlink()
            except OSError as exc:
                LOGGER.warning("Could not delete consumed AI track file %s: %s", path, exc)
        return bool(self.db.consume_ai_track(track["id"]))

    def _fill_local(self, entries, target=None):
        target = target or self._target()
        while len(entries) < target:
            track = self._candidate(entries)
            if not track:
                break
            entries.append({
                "track_id": track["id"],
                "source_query": "local-library",
                "added_at": datetime.now(timezone.utc).isoformat(),
            })
        return entries

    def _snapshot(self, entries=None):
        entries = entries if entries is not None else self._current_entries()
        entries = self._sanitize_entries(entries)
        tracks = {track["id"]: track for track in self.db.tracks()}
        items = [tracks[entry["track_id"]] for entry in entries if entry["track_id"] in tracks]
        target, refill, critical = self._thresholds()
        discovery = self._discovery_state()
        thread_running = bool(self._refill_thread and self._refill_thread.is_alive())
        phase = self._phase
        if not thread_running and discovery["blocked_reason"]:
            phase = "blocked" if discovery["requested"] else "disabled"
        return {
            "ok": True,
            "items": items,
            "size": len(items),
            "target": target,
            "refill_threshold": refill,
            "critical_threshold": critical,
            "refilling": thread_running,
            "discovery_enabled": (
                discovery["requested"] and discovery["rights_confirmed"]
            ),
            "discovery_requested": discovery["requested"],
            "rights_confirmed": discovery["rights_confirmed"],
            "blocked_reason": discovery["blocked_reason"],
            "last_error": self._last_error,
            "phase": phase,
            "retry_in_seconds": max(0, round(self._retry_after - time.monotonic())),
        }

    def bootstrap(self, preferred_track_id=None):
        with self._lock:
            entries = self._current_entries()
            preferred = int(preferred_track_id) if preferred_track_id else None
            if preferred and any(entry["track_id"] == preferred for entry in entries):
                entries.sort(key=lambda entry: entry["track_id"] != preferred)
            elif preferred and any(track["id"] == preferred for track in self._playable_tracks()):
                entries.insert(0, {
                    "track_id": preferred,
                    "source_query": "manual",
                    "added_at": datetime.now(timezone.utc).isoformat(),
                })
            entries = self._fill_local(entries)
            self._save(entries)
            snapshot = self._snapshot(entries)
        if self._enabled_discovery() and snapshot["size"] <= snapshot["refill_threshold"]:
            return self.request_refill()
        return snapshot

    def reseed(self, preferred_track_id):
        with self._lock:
            preferred = self.db.track(int(preferred_track_id))
            entries = []
            if preferred and self._path_exists(preferred):
                entries.append({
                    "track_id": preferred["id"],
                    "source_query": "manual",
                    "added_at": datetime.now(timezone.utc).isoformat(),
                })
            entries = self._fill_local(entries)
            self._save(entries)
            return self._snapshot(entries)

    def advance(self, finished_track_id):
        now = datetime.now(timezone.utc).isoformat()
        consumed = False
        with self._lock:
            track = self.db.track(int(finished_track_id))
            if track:
                self.db.add_radio_history(track, now)
            entries = [
                entry for entry in self._current_entries()
                if int(entry["track_id"]) != int(finished_track_id)
            ]
            consumed = self._consume_ai_track(track)
            target, refill, critical = self._thresholds()
            if not self._enabled_discovery() or len(entries) <= critical:
                entries = self._fill_local(entries, target if not self._enabled_discovery() else refill)
            self._save(entries)
            snapshot = self._snapshot(entries)
        if self._enabled_discovery() and snapshot["size"] <= refill:
            self.request_refill()
        self.clean_cache()
        result = self.status()
        result["consumed_track_id"] = int(finished_track_id) if consumed else None
        result["consumed_ai_track"] = consumed
        return result

    def status(self):
        with self._lock:
            return self._snapshot()

    def request_refill(self, force=False):
        with self._lock:
            if self._stopping:
                return self._snapshot()
            if not self._enabled_discovery():
                # The UI polls this method frequently. Do not create a short-lived
                # worker every five seconds while the explicit rights gate is off.
                self._last_error = ""
                self._phase = "blocked"
                return self._snapshot()
            if self._refill_thread and self._refill_thread.is_alive():
                return self._snapshot()
            if not force and self._retry_after > time.monotonic():
                return self._snapshot()
            self._phase = "starting"
            self._refill_thread = threading.Thread(
                target=self._refill_worker,
                name="lumen-radio-refill",
                daemon=True,
            )
            self._refill_thread.start()
            return self._snapshot()

    def refresh(self):
        """Replace the active buffer with newly AI-selected downloads."""
        with self._lock:
            self._save([])
            self._last_error = ""
            self._retry_after = 0.0
        return self.request_refill(force=True)

    def _refill_worker(self):
        self._last_error = ""
        self._phase = "searching"
        LOGGER.info("AI library refill started")
        try:
            while True:
                with self._lock:
                    if self._stopping:
                        break
                    entries = self._current_entries()
                    if len(entries) >= self._target():
                        break
                    excluded = [entry["track_id"] for entry in entries]
                discovered = None
                if self._enabled_discovery() and self.discoverer:
                    try:
                        discovered = self.discoverer(excluded)
                    except Exception as exc:
                        self._last_error = str(exc)
                        self._phase = "error"
                        self._retry_after = time.monotonic() + 5
                        LOGGER.exception("AI library refill failed")
                with self._lock:
                    if self._stopping:
                        break
                    entries = self._current_entries()
                    if discovered and discovered.get("id") not in {
                        entry["track_id"] for entry in entries
                    }:
                        entries.append({
                            "track_id": discovered["id"],
                            "source_query": discovered.get("source_query", "ai-discovery"),
                            "added_at": datetime.now(timezone.utc).isoformat(),
                        })
                        self._last_error = ""
                        self._retry_after = 0.0
                        self._phase = "searching"
                        LOGGER.info(
                            "AI library added track: %s - %s",
                            discovered.get("artist", ""), discovered.get("title", ""),
                        )
                    else:
                        entries = self._fill_local(entries)
                    self._save(entries)
                    if not discovered:
                        break
        finally:
            if not self._last_error:
                self._phase = "idle"
            LOGGER.info(
                "AI library refill finished%s",
                f": {self._last_error}" if self._last_error else "",
            )
            self.clean_cache()

    def stop(self, timeout=2):
        with self._lock:
            self._stopping = True
            self._phase = "stopping"
            thread = self._refill_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(max(0, float(timeout)))
        return not bool(thread and thread.is_alive())

    def clean_cache(self):
        cache_dir = (self.root / "downloads" / "queue").resolve()
        if not cache_dir.is_dir():
            return
        try:
            max_bytes = int(float(self.db.settings().get("queue_cache_max_gb", 3)) * 1024 ** 3)
        except (TypeError, ValueError):
            max_bytes = 3 * 1024 ** 3
        files = [path for path in cache_dir.iterdir() if path.is_file()]
        total = sum(path.stat().st_size for path in files)
        if total <= max_bytes:
            return
        queued_paths = {
            (self.root / track.get("local_path", "")).resolve()
            for track in self._snapshot()["items"] if track.get("local_path")
        }
        tracks_by_path = {
            (self.root / track.get("local_path", "")).resolve(): track
            for track in self.db.tracks() if track.get("local_path")
        }
        for path in sorted(files, key=lambda item: item.stat().st_mtime):
            if total <= max_bytes:
                break
            resolved = path.resolve()
            if cache_dir not in resolved.parents or resolved in queued_paths:
                continue
            size = resolved.stat().st_size
            resolved.unlink(missing_ok=True)
            total -= size
            track = tracks_by_path.get(resolved)
            if track:
                self.db.update_track(track["id"], local_path="", status="pending")
