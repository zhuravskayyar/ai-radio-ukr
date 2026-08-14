"""Launch the real WebView2 UI and verify the local downloads playlist."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import base64
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import wave

os.environ.setdefault(
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
    "--autoplay-policy=no-user-gesture-required",
)
import webview

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from backend.api import RadioAPI


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return


def silent_wav_data_uri(seconds=2.0, sample_rate=8000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * int(seconds * sample_rate))
    return "data:audio/wav;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class SmokeAPI(RadioAPI):
    def provider_health(self):
        return [
            {
                "name": "secondary", "label": "OpenRouter",
                "state": "disabled", "message": "немає кредитів",
                "retry_in_seconds": 0,
            },
            {
                "name": "nvidia", "label": "NVIDIA",
                "state": "cooldown", "message": "таймаут",
                "retry_in_seconds": 300,
            },
        ]

    def bootstrap(self):
        result = super().bootstrap()
        result["settings"].update({
            "rotation": "random",
            "host_every": "1",
            "talk_probability": "100",
            "story_probability": "60",
            "pregen_depth": "4",
            "host_sentences": "4",
            "host_length": "15",
            "intro_bed_volume": "10",
            "program_volume": "75",
            "autostart_radio": "1",
        })
        return result

    def make_intro(self, track_id, current_track_id=None, verified_fact="", style=""):
        track = next(track for track in self.db.tracks() if track["id"] == int(track_id))
        intro = (
            "Перевіряємо автоматичний ефір без довгих промов і зайвих церемоній. "
            f'Далі {track["artist"]} — «{track["title"]}». '
            "Музика вже тихо звучить під голосом, тож техніка цього разу не сперечається. "
            "Додаємо гучності й продовжуємо."
        )
        return {
            "ok": True,
            "intro": intro,
            "display_text": intro,
            "speech_text": intro,
            "style": "straight_radio",
            "provider": "smoke",
            "provider_error": "",
        }

    def synthesize_speech(self, text, voice="uk-UA-OstapNeural", rate="-2%"):
        return {"ok": True, "audio": silent_wav_data_uri(), "voice": voice, "cached": True, "speech_text": text}

    def warm_tts(self):
        return {"ok": True, "ready": True, "provider": "smoke"}

    def prepare_transition_queue(self, track_ids, eta_seconds=0):
        pairs = list(zip(track_ids, track_ids[1:]))
        return {
            "ok": True,
            "busy": False,
            "prepared": [
                {
                    "current_track_id": current,
                    "next_track_id": next_track,
                    "status": "ready",
                    "transition_type": "talk_up",
                    "content_type": "talk",
                    "reused": False,
                    "error": "",
                }
                for current, next_track in pairs
            ],
        }

    def get_prepared_transition(self, current_track_id, next_track_id, available_intro_ms=0):
        return {
            "ok": True,
            "status": "ready",
            "id": 1,
            "current_track_id": int(current_track_id),
            "next_track_id": int(next_track_id),
            "transition_type": "talk_up",
            "content_type": "talk",
            "style": "straight_radio",
            "variant": "short",
            "display_text": "Готовий перехід уже був на диску. Додаємо гучності.",
            "speech_text": "Готовий перехід уже був на диску. Додаємо гучності.",
            "audio": silent_wav_data_uri(),
            "voice_duration_ms": 2000,
            "plan": {"duck_percent": 10, "events": []},
            "provider": "smoke",
            "provider_error": "",
        }

    def mark_transition_aired(self, current_track_id, next_track_id):
        return {"ok": True}

    def mark_played(self, track_id):
        return {"ok": True}


smoke_workspace = tempfile.TemporaryDirectory(prefix="lumen-radio-smoke-")
runtime_root = Path(smoke_workspace.name)
shutil.copytree(root / "ui", runtime_root / "ui")
for number in range(12):
    path = runtime_root / "downloads" / f"Smoke Artist {number:02d} - Smoke Track {number:02d}.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * (31 * 8000))

api = SmokeAPI(runtime_root)
api.db.save_settings({"queue_min_duration": "30"})
api.scan_music()
for smoke_track in api.db.tracks():
    if smoke_track.get("local_path"):
        api.db.update_track(
            smoke_track["id"], library_source="ai", match_score=1,
        )
story_tracks = [track for track in api.db.tracks() if track.get("local_path")][-2:]
for story_track in story_tracks:
    story_result = api.add_music_story(story_track["id"], {
        "category": "SONG_ORIGIN",
        "story_data": ["Два незалежні джерела підтвердили тестову історію"],
        "sources": [
            {"id": "first", "url": "https://example.com/first", "tier": "B"},
            {"id": "second", "url": "https://example.org/second", "tier": "B"},
        ],
        "claims": [{
            "text": "Два незалежні джерела підтвердили тестову історію",
            "source_ids": ["first", "second"],
        }],
        "confidence": "verified",
    })
    assert story_result["ok"] is True
server = ThreadingHTTPServer(
    ("127.0.0.1", 0),
    partial(QuietHandler, directory=str(runtime_root)),
)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_port}/ui/index.html"
window = webview.create_window("Vector Radio smoke test", url, js_api=api)
finished = threading.Event()
failures = []


def wait_for_diagnostics(predicate, timeout=10):
    deadline = time.time() + timeout
    latest = None
    while time.time() < deadline:
        try:
            latest = window.evaluate_js("window.radioDiagnostics()")
        except webview.errors.JavascriptException:
            # webview.start callback can run before app.js defines diagnostics.
            time.sleep(0.25)
            continue
        if predicate(latest):
            return latest
        time.sleep(0.25)
    raise AssertionError(f"Timed out waiting for diagnostics: {latest}")


def verify():
    try:
        intro_diagnostics = wait_for_diagnostics(
            lambda value: value["localPlaying"] is True
            and value["ducked"] is True
            and value["currentOutputVolume"] == 10
            and value["introSentences"] == 4
            and value["hasSeparateSpeechText"] is True,
        )
        during_intro = window.evaluate_js("""(() => ({
          title: document.querySelector('#nowTitle').textContent,
          artist: document.querySelector('#nowArtist').textContent,
          queue: document.querySelectorAll('#queue .queueItem').length,
          rundown: document.querySelectorAll('#rundown .rundown-item').length,
          hardPoints: document.querySelectorAll('#rundown .rundown-item.hard').length,
          currentClockSlots: document.querySelectorAll('#rundown .rundown-item.current').length,
          rundownMeta: document.querySelector('#rundownMeta').textContent,
          safetyStatus: document.querySelector('#safetyStatus').textContent,
          vectorLogoVisible: document.querySelector('.vector-logo').getBoundingClientRect().width > 100,
          centralPlayVisible: document.querySelector('#play').getBoundingClientRect().width > 60,
          settingsGearVisible: document.querySelector('.settings-gear').getBoundingClientRect().width > 20,
          libraryButtonVisible: document.querySelector('.top-library-button').getBoundingClientRect().width > 80,
          mainLibraryStatusVisible: document.querySelector('.main-library-status').getBoundingClientRect().height > 40,
          mainLibraryText: document.querySelector('#mainDownloadTitle').textContent,
          diagnostics: window.radioDiagnostics()
        }))()""")
        print("during_intro", json.dumps(during_intro, ensure_ascii=True), flush=True)
        after_intro = wait_for_diagnostics(
            lambda value: value["automationBusy"] is False
            and value["ducked"] is False
            and value["currentOutputVolume"] == 75,
        )
        print("after_intro", json.dumps(after_intro, ensure_ascii=True), flush=True)
        first_track_id = after_intro["currentTrackId"]
        window.evaluate_js("void handleTrackEnded(); true")
        after_transition = wait_for_diagnostics(
            lambda value: value["currentTrackId"] != first_track_id
            and value["localPlaying"] is True,
        )
        print("after_transition", json.dumps(after_transition, ensure_ascii=True), flush=True)
        wait_for_diagnostics(
            lambda value: value["automationBusy"] is False
            and value["localPlaying"] is True,
        )
        window.evaluate_js("""(() => {
          state.localAudio.pause();
          state.manualPause = false;
          state.broadcastStarted = true;
          state.lastAudibleAt = Date.now() - 8000;
          return true;
        })()""")
        after_watchdog = wait_for_diagnostics(
            lambda value: value["silenceWarnings"] >= 1
            and value["silenceFallbacks"] >= 1
            and value["emergencyRecoveryBusy"] is False
            and value["localPlaying"] is True,
        )
        print("after_watchdog", json.dumps(after_watchdog, ensure_ascii=True), flush=True)
        watchdog_counts = (
            after_watchdog["silenceWarnings"], after_watchdog["silenceFallbacks"],
        )
        manual_pause = window.evaluate_js("""(() => {
          pauseBroadcast();
          state.lastAudibleAt = Date.now() - 30000;
          checkSilenceWatchdog();
          return window.radioDiagnostics();
        })()""")
        window.evaluate_js("document.querySelector('.top-library-button').click()")
        time.sleep(1)
        library = window.evaluate_js("""(() => ({
          active: document.querySelector('#library').classList.contains('active'),
          count: document.querySelector('#libraryCount').textContent,
          localRows: [...document.querySelectorAll('#trackTable .badge')]
            .filter(node => node.textContent.includes('ЗАВАНТАЖЕНО')).length,
          downloadMonitor: !!document.querySelector('#downloadProgressBar'),
          providerChips: document.querySelectorAll('#providerStatusList .providerStatus').length,
          providerText: document.querySelector('#providerStatusList').textContent,
          updateMonitor: !!document.querySelector('#updateProgressBar'),
          sizeText: document.querySelector('#librarySizeText').textContent,
          corroboratedStoryBadges: [...document.querySelectorAll('#trackTable .badge')]
            .filter(node => node.textContent.includes('STORY · 1×2')).length,
          refreshLabel: document.querySelector('#refreshAiLibrary').textContent
        }))()""")
        window.evaluate_js("document.querySelector('.settings-gear').click()")
        time.sleep(0.5)
        simple_settings = window.evaluate_js("""(() => ({
          active: document.querySelector('#settings').classList.contains('active'),
          apiInputVisible: document.querySelector('#apiTextInput').getBoundingClientRect().height > 40,
          apiFileButtonVisible: document.querySelector('.file-button').getBoundingClientRect().height > 20,
          genreVisible: document.querySelector('#simpleStationPrompt').getBoundingClientRect().height > 40,
          editorModeOption: [...document.querySelectorAll('[data-setting="host_every"] option')]
            .some(option => option.value === '0' && option.textContent.includes('Редактор вирішує')),
          listenerProfileReady: document.querySelector('#listenerProfileSummary').textContent
            .includes('історія: 50%'),
          visibleCards: [...document.querySelectorAll('#settings .settingsGrid > article')]
            .filter(node => getComputedStyle(node).display !== 'none').length
        }))()""")
        result = {
            "during_intro": during_intro,
            "after_intro": after_intro,
            "after_transition": after_transition,
            "after_watchdog": after_watchdog,
            "manual_pause": manual_pause,
            "library": library,
            "simple_settings": simple_settings,
        }
        print(json.dumps(result, ensure_ascii=True), flush=True)
        assert intro_diagnostics["localTracks"] == 12
        assert during_intro["queue"] == 9
        assert during_intro["rundown"] == 12
        assert during_intro["hardPoints"] == 4
        assert during_intro["currentClockSlots"] == 1
        assert "2026.08-pilot-v1" in during_intro["rundownMeta"]
        assert "Watchdog 3/7" in during_intro["safetyStatus"]
        assert during_intro["vectorLogoVisible"] is True
        assert during_intro["centralPlayVisible"] is True
        assert during_intro["settingsGearVisible"] is True
        assert during_intro["libraryButtonVisible"] is True
        assert during_intro["mainLibraryStatusVisible"] is True
        assert "Бібліотека" in during_intro["mainLibraryText"]
        assert intro_diagnostics["radioBufferSize"] == 10
        assert intro_diagnostics["radioBufferTarget"] == 10
        assert intro_diagnostics["localPlaying"] is True
        assert intro_diagnostics["ducked"] is True
        assert intro_diagnostics["currentOutputVolume"] == 10
        assert intro_diagnostics["introSentences"] == 4
        assert intro_diagnostics["hasSeparateSpeechText"] is True
        assert after_intro["localPlaying"] is True
        assert after_intro["ducked"] is False
        assert after_intro["currentOutputVolume"] == 75
        assert after_intro["watchdogState"] == "armed"
        assert after_intro["silenceWarnings"] == 0
        assert after_intro["silenceFallbacks"] == 0
        assert after_intro["rotation"] == "random"
        assert len(after_intro["scheduledTrackIds"]) == len(set(after_intro["scheduledTrackIds"]))
        assert first_track_id not in after_intro["scheduledTrackIds"]
        assert after_transition["currentTrackId"] != first_track_id
        assert after_transition["localPlaying"] is True
        assert after_transition["tracksSinceHost"] == 0
        assert after_transition["sessionPlayedTracks"] == 2
        assert len(after_transition["scheduledTrackIds"]) == len(set(after_transition["scheduledTrackIds"]))
        assert after_transition["currentTrackId"] not in after_transition["scheduledTrackIds"]
        assert after_transition["radioBufferSize"] == 10
        assert len(after_transition["radioBufferTrackIds"]) == 10
        assert len(after_transition["radioBufferTrackIds"]) == len(
            set(after_transition["radioBufferTrackIds"])
        )
        assert after_watchdog["watchdogState"] == "armed"
        assert after_watchdog["silenceWarnings"] >= 1
        assert after_watchdog["silenceFallbacks"] >= 1
        assert after_watchdog["localPlaying"] is True
        assert manual_pause["watchdogState"] == "paused"
        assert manual_pause["manualPause"] is True
        assert manual_pause["silenceWarnings"] == watchdog_counts[0]
        assert manual_pause["silenceFallbacks"] == watchdog_counts[1]
        assert library["active"] is True
        assert library["localRows"] >= 1
        assert library["downloadMonitor"] is True
        assert library["providerChips"] == 2
        assert "OpenRouter" in library["providerText"]
        assert "NVIDIA" in library["providerText"]
        assert library["updateMonitor"] is True
        assert "трек" in library["sizeText"]
        assert library["refreshLabel"] == "↻ Оновити AI-бібліотеку"
        assert library["corroboratedStoryBadges"] >= 1
        assert simple_settings["active"] is True
        assert simple_settings["apiInputVisible"] is True
        assert simple_settings["apiFileButtonVisible"] is True
        assert simple_settings["genreVisible"] is True
        assert simple_settings["editorModeOption"] is True
        assert simple_settings["listenerProfileReady"] is True
        assert simple_settings["visibleCards"] == 1
    except BaseException as exc:
        failures.append(exc)
        raise
    finally:
        finished.set()
        window.destroy()


def watchdog():
    if not finished.wait(40):
        print("Smoke test timeout", flush=True)
        window.destroy()


threading.Thread(target=watchdog, daemon=True).start()
try:
    webview.start(
        verify,
        gui="edgechromium",
        private_mode=False,
        storage_path=tempfile.mkdtemp(prefix="lumen-webview-smoke-"),
    )
finally:
    server.shutdown()
    smoke_workspace.cleanup()
if failures:
    raise failures[0]
