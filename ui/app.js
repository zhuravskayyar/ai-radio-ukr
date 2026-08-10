const state = {
  tracks: [],
  settings: {},
  index: 0,
  playing: false,
  libraryVisible: 25,
  voiceAudio: null,
  voiceResolve: null,
  systemVoicePlaying: false,
  localAudio: null,
  audioTrackId: null,
  automationBusy: false,
  broadcastStarted: false,
  autoplayBlocked: false,
  pendingTrackEnd: false,
  tracksSinceHost: 0,
  sequenceId: 0,
  ducked: false,
  currentOutputVolume: 0,
  lastAutomationError: '',
  upcomingIndices: [],
  prefetching: false,
  prefetchSignature: '',
  preparedQueue: [],
  nextPreparedTransition: null,
  outroVoiceStarted: false,
  outroVoicePromise: null,
  lastTransitionType: '',
  emergencySegue: false,
  sessionPlayedTrackIds: new Set(),
  rotationCycle: 1,
  tracksSinceStory: 3,
  radioQueue: null,
  updateStatus: null,
  appVersion: '',
  pilotClock: null,
  broadcastSafety: null,
  manualPause: false,
  lastAudibleAt: Date.now(),
  silenceWarningActive: false,
  emergencyRecoveryBusy: false,
  silenceWarnings: 0,
  silenceFallbacks: 0,
  watchdogState: 'armed',
};

let booted = false;
let bootAttempts = 0;
let bootRetryTimer = null;
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

function settingNumber(key, fallback) {
  const value = Number(state.settings[key]);
  return Number.isFinite(value) ? value : fallback;
}

function programVolume() {
  return Math.max(0, Math.min(100, settingNumber('program_volume', 75)));
}

function introBedVolume() {
  return Math.max(0, Math.min(30, settingNumber('intro_bed_volume', 10)));
}

function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.add('show');
  setTimeout(() => element.classList.remove('show'), 3000);
}

async function boot() {
  if (booted || !window.pywebview?.api) return;
  booted = true;
  try {
    const data = await window.pywebview.api.bootstrap();
    if (!data?.ok) throw new Error(data?.error || 'Backend не відповів');
    state.tracks = data.tracks;
    state.settings = data.settings;
    state.pilotClock = data.pilot_clock || null;
    state.broadcastSafety = data.broadcast_safety || null;
    state.updateStatus = data.update_status || null;
    state.appVersion = data.app_version || '';
    ensureCharacterSettings();
    ensureLibraryActions();
    fillSettings();
    resetSessionRotation();
    if (!applyRadioQueue(data.radio_queue, true)) {
      select(randomFirstPlayableIndex());
    }
    renderRundown();
    renderSafetyStatus();
    if (String(state.settings.use_styletts ?? '1') === '1'
        && state.settings.styletts_status?.ready) {
      // Warm the CPU model in a pywebview worker. Do not await it: the library
      // and music controls must remain usable while the voice is loading.
      void window.pywebview.api.warm_tts().catch(error => {
        console.warn('StyleTTS warm-up failed', error);
      });
    }
    if (String(state.settings.autostart_radio ?? '1') === '1' && playableIndices().length) {
      setTimeout(() => startBroadcast(), 700);
    }
    bootAttempts = 0;
  } catch (error) {
    booted = false;
    bootAttempts += 1;
    console.error('Bootstrap failed', error);
    const reason = String(error?.message || error || 'невідома помилка');
    if (bootAttempts < 4) {
      toast(`Підключення до локальних даних… спроба ${bootAttempts + 1}/4`);
      clearTimeout(bootRetryTimer);
      bootRetryTimer = setTimeout(boot, 350 * (2 ** (bootAttempts - 1)));
    } else {
      toast(`Не вдалося завантажити дані: ${reason}`);
    }
  }
}

window.addEventListener('pywebviewready', boot);
if (window.pywebview?.api) boot();
else {
  const bridgeTimer = setInterval(() => {
    if (window.pywebview?.api) {
      clearInterval(bridgeTimer);
      boot();
    }
  }, 100);
  setTimeout(() => clearInterval(bridgeTimer), 15000);
}

$$('.nav').forEach(button => {
  button.onclick = () => {
    $$('.nav,.page').forEach(element => element.classList.remove('active'));
    button.classList.add('active');
    $('#' + button.dataset.page).classList.add('active');
    if (button.dataset.page === 'library') renderLibrary();
  };
});

function ensureCharacterSettings() {
  if (!$('#characterSettings')) {
    const controls = [
      ['host_humor', 'Гумор', 0, 100, 1],
      ['host_sarcasm', 'Сарказм', 0, 100, 1],
      ['host_energy', 'Енергійність', 0, 100, 1],
      ['host_conversational', 'Розмовність', 0, 100, 1],
      ['host_facts', 'Факти', 0, 100, 1],
      ['talk_probability', 'Голосових переходів', 0, 100, 1],
      ['silence_probability', 'Свідомих пауз', 0, 20, 1],
      ['rubric_probability', 'Рідкісних рубрик', 0, 20, 1],
      ['story_probability', 'Музичних історій', 0, 100, 1],
      ['story_every', 'STORY не рідше ніж', 2, 8, 1],
      ['fact_probability', 'Фактів', 0, 100, 1],
      ['host_length', 'Довжина', 8, 32, 1],
      ['host_sentences', 'Кількість речень', 1, 5, 1],
      ['colloquiality', 'Розмовність мови', 0, 1, 0.05],
      ['surzhyk', 'Легкий суржик', 0, 0.08, 0.01],
      ['slang', 'Сленг', 0, 0.5, 0.05],
    ];
    $('.settingsGrid').insertAdjacentHTML('beforeend', `
      <article id="characterSettings">
        <h3>Характер ведучого</h3>
        ${controls.map(([key, label, min, max, step]) => `
          <label>${label} <output data-output="${key}"></output>
            <input type="range" min="${min}" max="${max}" step="${step}" data-setting="${key}">
          </label>`).join('')}
        <label>Стиль мови
          <select data-setting="language_style">
            <option value="standard">Літературна</option>
            <option value="casual_uk">Жива українська</option>
            <option value="local_uk">Локальна розмовна</option>
          </select>
        </label>
        <p class="hint">Адам Вектор — відкрито цифровий ведучий: допитливий, точний і сухо самоіронічний. Він має музичний смак, не вигадує людського досвіду, а на чутливих темах одразу вимикає гумор.</p>
      </article>`);
  }

  if (!$('#broadcastSettings')) {
    $('.settingsGrid').insertAdjacentHTML('beforeend', `
      <article id="broadcastSettings">
        <h3>Автоматичний ефір</h3>
        <label>Музика під голосом <output data-output="intro_bed_volume"></output>
          <input type="range" min="0" max="30" data-setting="intro_bed_volume">
        </label>
        <label>Гучність після підводки <output data-output="program_volume"></output>
          <input type="range" min="10" max="100" data-setting="program_volume">
        </label>
        <label>Ducking під голосом <output data-output="transition_duck_volume"></output>
          <input type="range" min="10" max="40" data-setting="transition_duck_volume">
        </label>
        <label>Глибина підготовки <output data-output="pregen_depth"></output>
          <input type="range" min="1" max="5" data-setting="pregen_depth">
        </label>
        <p class="hint">Підводки й MP3 готуються на кілька переходів наперед. Під час зміни треку зовнішні API не викликаються.</p>
      </article>`);
  }

  if (!$('#ttsSettings')) {
    $('.settingsGrid').insertAdjacentHTML('beforeend', `
      <article id="ttsSettings">
        <h3>Український голос</h3>
        <label>Основний рушій
          <select data-setting="use_styletts">
            <option value="1">Локальний StyleTTS2</option>
            <option value="0">Edge TTS</option>
          </select>
        </label>
        <p id="ttsStatus" class="hint">Перевіряю локальний голос…</p>
        <p class="hint">StyleTTS2 працює локально. Edge TTS і системний голос залишаються автоматичним резервом.</p>
      </article>`);
  }

  if (!$('#dynamicQueueSettings')) {
    $('.settingsGrid').insertAdjacentHTML('beforeend', `
      <article id="dynamicQueueSettings">
        <h3>Динамічний музичний буфер</h3>
        <label>Розмір буфера <output data-output="queue_size"></output>
          <input type="range" min="5" max="15" step="1" data-setting="queue_size">
        </label>
        <label>Поповнювати від <output data-output="queue_refill_threshold"></output>
          <input type="range" min="2" max="12" step="1" data-setting="queue_refill_threshold">
        </label>
        <label>Cooldown артиста <output data-output="artist_cooldown_tracks"></output>
          <input type="range" min="2" max="30" step="1" data-setting="artist_cooldown_tracks">
        </label>
        <label>Cooldown треку <output data-output="track_cooldown_tracks"></output>
          <input type="range" min="20" max="500" step="10" data-setting="track_cooldown_tracks">
        </label>
        <label>Ліміт кешу <output data-output="queue_cache_max_gb"></output>
          <input type="range" min="1" max="20" step="1" data-setting="queue_cache_max_gb">
        </label>
        <label>Промпт стилю станції
          <textarea data-setting="station_prompt" rows="5"></textarea>
        </label>
        <label>AI-пошук нової музики
          <select data-setting="dynamic_discovery_enabled">
            <option value="0">Вимкнений · локальна бібліотека</option>
            <option value="1">Увімкнений · фоновий yt-dlp</option>
          </select>
        </label>
        <p class="hint"><b>Автоматичний режим активний.</b> Технічний дозвіл на пошук зберігається патчем. Користувач сам відповідає за право використовувати вибрані джерела й композиції.</p>
        <p id="queueStatus" class="hint">Буфер працює з локальної бібліотеки</p>
        <p class="hint">Онлайн-пошук працює у фоні. Файли кешуються до встановленого ліміту, а плеєр не блокується під час поповнення.</p>
      </article>`);
  }

  if (!$('#contextSettings')) {
    $('.settingsGrid').insertAdjacentHTML('beforeend', `
      <article id="contextSettings">
        <h3>Контекст ефіру</h3>
        <label>Місто<input data-setting="station_city"></label>
        <label>Часовий пояс<input data-setting="station_timezone"></label>
        <label>Широта для погоди<input type="number" step="0.0001" data-setting="weather_latitude"></label>
        <label>Довгота для погоди<input type="number" step="0.0001" data-setting="weather_longitude"></label>
        <label>Назва програми<input data-setting="program_name"></label>
        <label>Відповідальний редактор<input data-setting="responsible_editor" placeholder="Ім’я та прізвище"></label>
        <label>Пілотний clock
          <select data-setting="pilot_clock_enabled">
            <option value="1">Увімкнений · 60 хв</option>
            <option value="0">Вимкнений · legacy planning</option>
          </select>
        </label>
        <label>Watchdog тиші
          <select data-setting="silence_watchdog_enabled">
            <option value="1">Увімкнений</option>
            <option value="0">Вимкнений</option>
          </select>
        </label>
        <label>Попередження, сек<input type="number" min="2" max="3" step="1" data-setting="silence_warning_seconds"></label>
        <label>Аварійний резерв, сек<input type="number" min="5" max="8" step="1" data-setting="silence_fallback_seconds"></label>
        <label>Погода
          <select data-setting="weather_enabled">
            <option value="0">Вимкнена</option>
            <option value="1">Open-Meteo, кеш 30 хв</option>
          </select>
        </label>
        <p class="hint">Час береться для фактичного запланованого переходу. Погода використовується лише з кешованої відповіді API й ніколи не вигадується. Watchdog контролює стан локального плеєра й TTS: попереджає через 2–3 с, а через 5–8 с запускає перевірений резерв.</p>
      </article>`);
  }

  if (!$('#secondaryApiSettings')) {
    $('.settingsGrid').insertAdjacentHTML('beforeend', `
      <article id="secondaryApiSettings">
        <h3>Другий AI API</h3>
        <label>Перший AI API
          <select data-setting="primary_ai_provider">
            <option value="nvidia">NVIDIA Nemotron</option>
            <option value="secondary">OpenRouter / OpenAI-сумісний</option>
          </select>
        </label>
        <label>AI для DJ-пошуку
          <select data-setting="dj_ai_provider">
            <option value="parallel">Паралельно, кращий план</option>
            <option value="secondary">OpenRouter / DeepSeek</option>
            <option value="nvidia">NVIDIA Nemotron</option>
          </select>
        </label>
        <label>AI для ведучого / Play Together
          <select data-setting="host_ai_provider">
            <option value="secondary">OpenRouter / DeepSeek</option>
            <option value="nvidia">NVIDIA Nemotron</option>
            <option value="parallel">Паралельно всі</option>
          </select>
        </label>
        <label>Аварійна підводка при помилці AI
          <select data-setting="strict_live_ai_host">
            <option value="0">Завжди озвучувати локальний резерв</option>
            <option value="1">Вимкнути резерв і лишити музику</option>
          </select>
        </label>
        <label>Варіантів підводки від кожного AI
          <select data-setting="intro_variants_per_provider">
            <option value="1">Один — швидше</option>
            <option value="2">Два — баланс якості й швидкості</option>
            <option value="3">Три — максимальний вибір</option>
          </select>
        </label>
        <label>Паралельна генерація
          <select data-setting="secondary_api_enabled">
            <option value="0">Вимкнена</option>
            <option value="1">Увімкнена</option>
          </select>
        </label>
        <label>OpenRouter / OpenAI-сумісний URL<input data-setting="secondary_api_url" placeholder="https://openrouter.ai/api/v1/chat/completions"></label>
        <label>API key<input type="password" data-setting="secondary_api_key" placeholder="sk-or-..."></label>
        <label>Модель<input data-setting="secondary_model" placeholder="deepseek/deepseek-v4-flash"></label>
        <p id="secondaryStatus" class="hint">Другий API вимкнений</p>
        <button id="benchmarkProviders" type="button">Тест AI-провайдерів</button>
        <p id="providerBenchmarkResult" class="hint">Тест перевіряє DJ-пошук, підводку й правопис без завантаження аудіо.</p>
        <p class="hint">Обидва API можуть працювати паралельно. В ефір іде кандидат, який пройшов перевірку маркерів, фактів, граматики, природного темпу та радіоштампів.</p>
      </article>`);
  }

  $$('#characterSettings input, #broadcastSettings input, #dynamicQueueSettings input').forEach(input => {
    input.oninput = () => updateSettingOutput(input.dataset.setting, input.value);
  });
}

function ensureLibraryActions() {
  const button = $('#refreshAiLibrary');
  if (button && button.dataset.bound !== '1') {
    button.dataset.bound = '1';
    button.onclick = async () => {
      button.disabled = true;
      button.textContent = 'AI добирає треки…';
      toast('Очищаю буфер і запускаю новий AI-добір…');
      try {
        const result = await window.pywebview.api.refresh_ai_library();
        if (!result.ok) {
          toast(result.error);
          return;
        }
        state.tracks = result.tracks;
        resetSessionRotation();
        const selected = applyRadioQueue(result.radio_queue, true);
        render();
        if (
          selected && !state.playing && !state.broadcastStarted
          && !state.automationBusy && !state.autoplayBlocked
          && playableIndices().length
        ) {
          await startBroadcast();
        }
        toast('AI-бібліотека оновлюється у фоні');
      } catch (error) {
        toast(`Не вдалося оновити AI-бібліотеку: ${error}`);
      } finally {
        button.disabled = false;
        button.textContent = '↻ Оновити AI-бібліотеку';
      }
    };
  }

  const benchmarkButton = $('#benchmarkProviders');
  if (benchmarkButton && benchmarkButton.dataset.bound !== '1') {
    benchmarkButton.dataset.bound = '1';
    benchmarkButton.onclick = async () => {
      const output = $('#providerBenchmarkResult');
      benchmarkButton.disabled = true;
      benchmarkButton.textContent = 'Тестую AI…';
      if (output) output.textContent = 'Викликаю провайдерів: музичний план і тестова підводка…';
      try {
        const result = await window.pywebview.api.benchmark_ai_providers(
          state.settings.station_prompt || '',
          'straight_radio',
          Number(state.settings.host_length || 16),
        );
        if (!result.ok) {
          if (output) output.textContent = result.error;
          toast(result.error);
          return;
        }
        const summary = (result.results || []).map(item => {
          const music = item.music_search?.ok
            ? Math.round(Number(item.music_search.score || 0))
            : 'помилка';
          const host = item.radio_host?.ok
            ? Math.round(Number(item.radio_host.score || 0))
            : 'fallback';
          const spelling = Math.round(Number(item.radio_host?.spelling_score || 0));
          return `${item.provider}: DJ ${music}, ведучий ${host}, правопис ${spelling}, total ${item.total_score}`;
        }).join(' · ');
        const label = result.winner
          ? `Переможець: ${result.winner}. ${summary}`
          : `Без переможця. ${summary}`;
        if (output) output.textContent = label;
        toast(result.winner ? `Кращий AI: ${result.winner}` : 'Тест AI завершено без переможця');
      } catch (error) {
        if (output) output.textContent = `Помилка тесту: ${error}`;
        toast(`Помилка тесту AI: ${error}`);
      } finally {
        benchmarkButton.disabled = false;
        benchmarkButton.textContent = 'Тест AI-провайдерів';
      }
    };
  }
}

function updateSettingOutput(key, value) {
  const output = $(`[data-output="${key}"]`);
  if (!output) return;
  if (key === 'host_length') output.textContent = `${value} сек`;
  else if (key === 'host_sentences') output.textContent = `${value} реч.`;
  else if (key === 'story_every') output.textContent = `${value} трек.`;
  else if (key === 'pregen_depth') output.textContent = `${value} перех.`;
  else if (key === 'queue_size') output.textContent = `${value} треків`;
  else if (key === 'queue_refill_threshold') output.textContent = `${value} треків`;
  else if (key === 'artist_cooldown_tracks' || key === 'track_cooldown_tracks') output.textContent = `${value} треків`;
  else if (key === 'queue_cache_max_gb') output.textContent = `${value} ГБ`;
  else if (['colloquiality', 'surzhyk', 'slang'].includes(key)) output.textContent = `${Math.round(Number(value) * 100)}%`;
  else output.textContent = `${value}%`;
}

function fillSettings() {
  Object.entries(state.settings).forEach(([key, value]) => {
    $$(`[data-setting="${key}"]`).forEach(element => {
      element.value = value;
    });
    updateSettingOutput(key, value);
  });
  $('#volume').value = programVolume();
  const stationTitle = $('#stationTitle');
  if (stationTitle) stationTitle.textContent = state.settings.station_name;
  const hostName = $('#hostName');
  if (hostName) hostName.textContent = state.settings.host_name;
  const simpleStationPrompt = $('#simpleStationPrompt');
  if (simpleStationPrompt) {
    simpleStationPrompt.value = state.settings.station_prompt || '';
  }
  const nvidiaStatus = $('#nvidiaStatus');
  if (nvidiaStatus) {
    const nvidiaKeyCount = Number(state.settings.nvidia_key_count || 0);
    nvidiaStatus.textContent = state.settings.nvidia_key_detected
      ? `● NVIDIA API: знайдено ключів — ${nvidiaKeyCount}`
      : '○ NVIDIA API key не знайдено';
  }
  const youtubeStatus = $('#youtubeStatus');
  if (youtubeStatus) {
    youtubeStatus.textContent = state.settings.youtube_key_detected
      ? '● YouTube API key знайдено'
      : '○ YouTube API key не знайдено';
  }
  const secondaryStatus = $('#secondaryStatus');
  if (secondaryStatus) {
    secondaryStatus.textContent = String(state.settings.secondary_api_enabled || '0') === '1'
      ? (state.settings.secondary_key_detected
        ? '● Другий API налаштований'
        : '○ Додайте URL, модель і API key')
      : '○ Другий API вимкнений';
  }
  const styletts = state.settings.styletts_status || {};
  const localEnabled = String(state.settings.use_styletts ?? '1') === '1';
  if (!localEnabled) {
    $('#ttsStatus').textContent = '○ Основний рушій: Edge TTS';
  } else if (!styletts.available) {
    $('#ttsStatus').textContent = '○ StyleTTS2 не встановлено; активний Edge TTS';
  } else if (!styletts.model_cached || !styletts.stanza_ready) {
    $('#ttsStatus').textContent = '◌ StyleTTS2 встановлено; локальні ресурси ще не завантажені';
  } else {
    const acceleration = styletts.model_loaded
      ? (styletts.cuda ? `CUDA · ${styletts.device}` : 'CPU')
      : 'CPU/CUDA auto';
    $('#ttsStatus').textContent = `● Локальний StyleTTS2 готовий · ${acceleration}`;
  }
  const queueStatus = $('#queueStatus');
  if (queueStatus) {
    const discovery = String(state.settings.dynamic_discovery_enabled || '0') === '1';
    const licensed = String(state.settings.licensed_sources_confirmed || '0') === '1';
    queueStatus.textContent = discovery
      ? (licensed ? '● AI-пошук дозволений · refill у фоні' : '○ Потрібне підтвердження прав')
      : '● Буфер працює з локальної резервної бібліотеки';
  }
  renderSafetyStatus();
}

function isRejectedDiscoveryCache(track) {
  const path = String(track?.local_path || '').replaceAll('\\', '/');
  return path.startsWith('downloads/queue/') && Number(track?.match_score || 0) < 0.75;
}

function hasPlayable(track) {
  return !!track
    && !isRejectedDiscoveryCache(track)
    && !!track.local_path;
}

function playableIndices() {
  const local = state.tracks
    .map((track, index) => track.local_path && hasPlayable(track) ? index : -1)
    .filter(index => index >= 0);
  if (local.length) return local;
  return state.tracks
    .map((track, index) => hasPlayable(track) ? index : -1)
    .filter(index => index >= 0);
}

function mergeQueueTracks(items = []) {
  items.forEach(track => {
    const at = state.tracks.findIndex(existing => existing.id === track.id);
    if (at >= 0) state.tracks[at] = {...state.tracks[at], ...track};
    else state.tracks.push({...track, rank: state.tracks.length + 1});
  });
}

function removeTrackFromState(trackId, keepTrackId = null) {
  const removeIndex = state.tracks.findIndex(track => track.id === trackId);
  if (removeIndex < 0) return;
  const upcomingIds = state.upcomingIndices
    .map(index => state.tracks[index]?.id)
    .filter(id => id && id !== trackId);
  state.tracks.splice(removeIndex, 1);
  state.upcomingIndices = upcomingIds
    .map(id => state.tracks.findIndex(track => track.id === id))
    .filter(index => index >= 0);
  if (keepTrackId !== null) {
    const keepIndex = state.tracks.findIndex(track => track.id === keepTrackId);
    if (keepIndex >= 0) state.index = keepIndex;
  } else if (state.index > removeIndex) {
    state.index -= 1;
  } else if (state.index >= state.tracks.length) {
    state.index = Math.max(0, state.tracks.length - 1);
  }
}

function applyRadioQueue(snapshot, selectCurrent = false) {
  if (!snapshot?.ok) return false;
  state.radioQueue = snapshot;
  if (!snapshot.items?.length) {
    state.upcomingIndices = [];
    return false;
  }
  mergeQueueTracks(snapshot.items);
  const indices = snapshot.items
    .map(track => state.tracks.findIndex(item => item.id === track.id))
    .filter(index => index >= 0);
  if (!indices.length) return false;
  if (selectCurrent) {
    state.upcomingIndices = indices.slice(1);
    select(indices[0], {preserveSchedule: true});
  } else {
    state.upcomingIndices = indices.filter(index => index !== state.index);
  }
  const status = $('#bufferStatus');
  if (status) {
    status.textContent = `${snapshot.size}/${snapshot.target}${snapshot.refilling ? ' · REFILL' : ''}`;
  }
  return true;
}

async function rebuildRadioQueue(preferredIndex = state.index, selectCurrent = false) {
  const api = window.pywebview?.api;
  const preferred = state.tracks[preferredIndex];
  if (!preferred || typeof api?.reseed_radio_queue !== 'function') return false;
  try {
    const snapshot = await api.reseed_radio_queue(preferred.id);
    return applyRadioQueue(snapshot, selectCurrent);
  } catch (error) {
    console.warn('Radio queue reseed failed', error);
    return false;
  }
}

function resetSessionRotation() {
  state.sessionPlayedTrackIds = new Set();
  state.rotationCycle = 1;
  state.tracksSinceStory = Math.max(1, settingNumber('story_every', 4) - 1);
  state.upcomingIndices = [];
  state.preparedQueue = [];
  state.nextPreparedTransition = null;
  state.prefetchSignature = '';
  state.radioQueue = null;
}

function randomFirstPlayableIndex() {
  const playable = playableIndices();
  if (!playable.length) return 0;
  const latest = playable
    .filter(index => state.tracks[index].last_played)
    .sort((left, right) => String(state.tracks[right].last_played).localeCompare(
      String(state.tracks[left].last_played),
    ))[0];
  const candidates = playable.length > 1
    ? playable.filter(index => index !== latest)
    : playable;
  return candidates[Math.floor(Math.random() * candidates.length)];
}

function sequentialNext(direction = 1) {
  const playable = playableIndices();
  if (!playable.length) return state.index;
  const at = playable.indexOf(state.index);
  const safeAt = at >= 0 ? at : 0;
  return playable[(safeAt + direction + playable.length) % playable.length];
}

function nextPlayable(direction = 1) {
  if (direction < 0) return sequentialNext(-1);
  return chooseUpcoming(state.index, state.upcomingIndices);
}

function chooseUpcoming(fromIndex, excluded = []) {
  const playable = playableIndices();
  if (playable.length <= 1) return playable[0] ?? fromIndex;
  const blocked = new Set([fromIndex, ...excluded]);
  let candidates = playable.filter(index => !blocked.has(index));
  if (!candidates.length) candidates = playable.filter(index => index !== fromIndex);
  const fromArtist = String(state.tracks[fromIndex]?.artist || '').trim().toLocaleLowerCase();
  if (fromArtist) {
    const withoutSameArtist = candidates.filter(index =>
      String(state.tracks[index]?.artist || '').trim().toLocaleLowerCase() !== fromArtist,
    );
    if (withoutSameArtist.length) candidates = withoutSameArtist;
  }
  let unplayed = candidates.filter(index =>
    !state.sessionPlayedTrackIds.has(state.tracks[index].id),
  );
  if (!unplayed.length && candidates.length) {
    state.rotationCycle += 1;
    state.sessionPlayedTrackIds = new Set([
      state.tracks[fromIndex]?.id,
    ].filter(Boolean));
    unplayed = candidates;
  }
  candidates = unplayed.length ? unplayed : candidates;
  const storyEvery = Math.max(2, Math.min(8, settingNumber('story_every', 4)));
  if (state.tracksSinceStory >= storyEvery - 1) {
    const storyCandidates = candidates.filter(index => Number(state.tracks[index].story_count) > 0);
    if (storyCandidates.length) {
      return storyCandidates.reduce((best, index) =>
        Number(state.tracks[index].play_count || 0) < Number(state.tracks[best].play_count || 0)
          ? index : best,
      storyCandidates[0]);
    }
  }
  const rotation = state.settings.rotation || 'random';
  if (rotation === 'random') return candidates[Math.floor(Math.random() * candidates.length)];
  const weights = candidates.map(index => {
    const track = state.tracks[index];
    const fromTrack = state.tracks[fromIndex] || {};
    const currentEnergy = Number(fromTrack.energy || 5);
    const nextEnergy = Number(track.energy || 5);
    const energyFit = 1 / (1 + Math.abs(nextEnergy - currentEnergy) * 0.25);
    const storyBoost = Number(track.story_count || 0) ? 1.25 : 1;
    const repeatPenalty = 1 / (1 + (Number(track.play_count) || 0) * 0.35);
    return energyFit * storyBoost * repeatPenalty;
  });
  const total = weights.reduce((sum, value) => sum + value, 0);
  let pick = Math.random() * total;
  for (let offset = 0; offset < candidates.length; offset += 1) {
    pick -= weights[offset];
    if (pick <= 0) return candidates[offset];
  }
  return candidates[candidates.length - 1];
}

function pregenDepth() {
  return Math.max(1, Math.min(5, settingNumber('pregen_depth', 4)));
}

function ensureUpcomingQueue() {
  if (state.radioQueue?.items?.length) {
    const playable = playableIndices();
    state.upcomingIndices = state.upcomingIndices.filter(index =>
      index !== state.index && playable.includes(index),
    );
    return state.upcomingIndices;
  }
  const depth = pregenDepth();
  const playable = playableIndices();
  state.upcomingIndices = state.upcomingIndices.filter(index =>
    index !== state.index && playable.includes(index),
  );
  while (state.upcomingIndices.length < Math.min(depth, Math.max(0, playable.length - 1))) {
    const from = state.upcomingIndices.at(-1) ?? state.index;
    const next = chooseUpcoming(from, state.upcomingIndices);
    if (next === undefined || next === null || state.upcomingIndices.includes(next)) break;
    state.upcomingIndices.push(next);
  }
  return state.upcomingIndices;
}

function takeScheduledNext() {
  ensureUpcomingQueue();
  const next = state.upcomingIndices.shift();
  return next ?? nextPlayable(1);
}

function syncNowPlayingDisplay() {
  const track = state.tracks[state.index];
  if (!track) return;
  $('#nowTitle').textContent = track.title;
  $('#nowArtist').textContent = track.artist;
  $('#coverRank').textContent = 'LIVE';
}

function render() {
  syncNowPlayingDisplay();
  const indices = ensureUpcomingQueue().slice(0, 9);
  const bufferStatus = $('#bufferStatus');
  if (bufferStatus && state.radioQueue) {
    bufferStatus.textContent = `${state.radioQueue.size}/${state.radioQueue.target}${state.radioQueue.refilling ? ' · REFILL' : ''}`;
  }
  const queueCount = $('#statusQueueCount');
  if (queueCount) {
    queueCount.textContent = indices.length.toString();
  }
  $('#queue').innerHTML = indices.map((index, offset) => {
    const track = state.tracks[index];
    const fromTrack = state.tracks[offset === 0 ? state.index : indices[offset - 1]];
    const prepared = state.preparedQueue.find(item =>
      item.current_track_id === fromTrack?.id && item.next_track_id === track.id,
    );
    const readiness = prepared?.status === 'ready' ? ' · READY' : prepared ? ' · PREP' : '';
    const clockSlot = prepared?.clock_slot_id ? ` · ${prepared.clock_slot_id.replaceAll('_', ' ')}` : '';
    return `<div class="queueItem"><span class="num">0${offset + 1}</span><div><b>${esc(track.title)}</b><small>${esc(track.artist)}${readiness}${esc(clockSlot)}</small></div><button onclick="tune(${index})">▶</button></div>`;
  }).join('') || '<p class="hint">Ще немає перевірених треків</p>';
  renderRundown();
  renderLibrary();
}

function renderRundown() {
  const root = $('#rundown');
  if (!root) return;
  const clock = state.pilotClock;
  if (!clock?.enabled) {
    root.innerHTML = '<p class="hint">Пілотний clock вимкнений.</p>';
    $('#rundownMeta').textContent = 'Legacy planning без фіксованої 60-хвилинної сітки';
    $('#rundownAccuracy').textContent = 'HARD —';
    return;
  }
  const editor = clock.editor_status === 'assigned'
    ? clock.responsible_editor : 'редактор не призначений';
  $('#rundownMeta').textContent = `${clock.version} · 60:00 · ${clock.segment_count} сегментів · ${editor}`;
  const accuracy = clock.metrics?.hard_point_accuracy_percent;
  $('#rundownAccuracy').textContent = accuracy == null
    ? 'HARD —' : `HARD ${accuracy}%`;
  root.innerHTML = (clock.segments || []).map(segment => {
    const current = segment.slot_id === clock.current_slot_id ? ' current' : '';
    const hard = segment.hard_point ? ' hard' : '';
    const event = (segment.items || []).at(-1);
    const eventText = event
      ? `<small class="event">${esc(event.content_type)} · ${esc(event.timing_status)}</small>`
      : '';
    return `<article class="rundown-item${current}${hard}">
      <time>${esc(String(segment.planned_start || '').slice(11, 16))}</time>
      <b>${esc(segment.name)}</b>
      <small>${esc(segment.thesis)}</small>
      ${eventText}
    </article>`;
  }).join('');
}

async function refreshPilotClock() {
  const api = window.pywebview?.api;
  if (typeof api?.pilot_hour !== 'function') return;
  try {
    const clock = await api.pilot_hour();
    if (clock?.ok) {
      state.pilotClock = clock;
      renderRundown();
    }
  } catch (error) {
    console.warn('Pilot clock refresh failed', error);
  }
}

function watchdogThresholds() {
  const warning = Math.max(2, Math.min(3,
    Math.round(settingNumber('silence_warning_seconds', 3))));
  const fallback = Math.max(warning + 2, Math.min(8,
    Math.round(settingNumber('silence_fallback_seconds', 7))));
  return {warning, fallback};
}

function watchdogEnabled() {
  return String(state.settings.silence_watchdog_enabled ?? '1') === '1';
}

function hasAudibleOutput() {
  const localPlaying = !!state.localAudio
    && !state.localAudio.paused
    && !state.localAudio.ended;
  const voicePlaying = !!state.voiceAudio
    && !state.voiceAudio.paused
    && !state.voiceAudio.ended;
  return localPlaying || voicePlaying || state.systemVoicePlaying;
}

function renderSafetyStatus() {
  const element = $('#safetyStatus');
  if (!element) return;
  const {warning, fallback} = watchdogThresholds();
  const labels = {
    disabled: '○ Watchdog вимкнений',
    paused: '○ Watchdog · ручна пауза',
    warning: `⚠ Тиша понад ${warning} с · очікую резерв`,
    recovery: '⚠ Watchdog · аварійне відновлення',
    armed: `● Watchdog ${warning}/${fallback} с`,
  };
  element.textContent = labels[state.watchdogState] || labels.armed;
  element.dataset.state = state.watchdogState;
}

async function refreshBroadcastSafety() {
  const api = window.pywebview?.api;
  if (typeof api?.broadcast_safety_status !== 'function') return;
  try {
    const result = await api.broadcast_safety_status();
    if (result?.ok) state.broadcastSafety = result;
  } catch (error) {
    console.warn('Broadcast safety refresh failed', error);
  }
}

function logBroadcastEvent(eventType, details = {}) {
  const api = window.pywebview?.api;
  if (typeof api?.record_broadcast_event !== 'function') return;
  void api.record_broadcast_event(eventType, details)
    .then(() => refreshBroadcastSafety())
    .catch(error => console.warn('Broadcast event log failed', error));
}

function noteAudibleOutput(method = 'playback') {
  const recoveredAfterWarning = state.silenceWarningActive
    && !state.emergencyRecoveryBusy;
  state.lastAudibleAt = Date.now();
  state.silenceWarningActive = false;
  state.watchdogState = watchdogEnabled() ? 'armed' : 'disabled';
  renderSafetyStatus();
  if (recoveredAfterWarning) {
    logBroadcastEvent('silence_recovered', {
      method,
      current_track_id: state.tracks[state.index]?.id || null,
    });
  }
}

async function recoverFromDeadAir(silentSeconds) {
  if (state.emergencyRecoveryBusy || state.manualPause) return;
  state.emergencyRecoveryBusy = true;
  state.silenceFallbacks += 1;
  state.watchdogState = 'recovery';
  state.emergencySegue = true;
  renderSafetyStatus();
  let method = '';
  let protocol = null;
  try {
    const api = window.pywebview?.api;
    if (typeof api?.emergency_protocol === 'function') {
      protocol = await api.emergency_protocol('dead_air', {
        silent_seconds: silentSeconds,
        current_track_id: state.tracks[state.index]?.id || null,
        responsible_editor: state.settings.responsible_editor || '',
      });
    }

    const local = state.localAudio;
    const resumable = local && local.paused && !local.ended
      && (!Number.isFinite(local.duration) || local.currentTime < local.duration - 0.25);
    if (resumable) {
      try {
        await local.play();
        if (!local.paused) method = 'resume_current_track';
      } catch (error) {
        console.warn('Current track recovery failed', error);
      }
    }

    if (!method && !state.automationBusy && state.tracks[state.index]) {
      await handleTrackEnded();
      if (hasAudibleOutput()) method = 'next_local_track';
    }

    if (!method) {
      state.broadcastStarted = false;
      const text = protocol?.display_text
        || 'Маємо технічну паузу. Відновлюю музичний ефір із перевіреного резерву.';
      $('#intro').textContent = `«${text}»`;
      await speak(text, false);
      method = 'technical_pause_announcement';
    }

    logBroadcastEvent('silence_recovered', {
      method,
      silent_seconds: silentSeconds,
      current_track_id: state.tracks[state.index]?.id || null,
    });
    state.lastAudibleAt = Date.now();
    state.silenceWarningActive = false;
    toast(`Ефір відновлено: ${method.replaceAll('_', ' ')}`);
  } catch (error) {
    state.lastAutomationError = String(error?.message || error);
    console.error('Dead-air recovery failed', error);
    toast(`Аварійне відновлення не завершено: ${state.lastAutomationError}`);
  } finally {
    state.emergencyRecoveryBusy = false;
    state.watchdogState = watchdogEnabled() ? 'armed' : 'disabled';
    renderSafetyStatus();
    void refreshBroadcastSafety();
  }
}

function checkSilenceWatchdog() {
  if (!watchdogEnabled()) {
    state.silenceWarningActive = false;
    if (state.watchdogState !== 'disabled') {
      state.watchdogState = 'disabled';
      renderSafetyStatus();
    }
    return;
  }
  if (!state.broadcastStarted || state.manualPause || state.autoplayBlocked) {
    state.lastAudibleAt = Date.now();
    state.silenceWarningActive = false;
    const nextState = state.manualPause ? 'paused' : 'armed';
    if (state.watchdogState !== nextState) {
      state.watchdogState = nextState;
      renderSafetyStatus();
    }
    return;
  }
  if (hasAudibleOutput()) {
    noteAudibleOutput('playback_resumed');
    return;
  }

  const {warning, fallback} = watchdogThresholds();
  const silentSeconds = (Date.now() - state.lastAudibleAt) / 1000;
  if (silentSeconds >= warning && !state.silenceWarningActive) {
    state.silenceWarningActive = true;
    state.silenceWarnings += 1;
    state.watchdogState = 'warning';
    renderSafetyStatus();
    toast(`Watchdog: тиша ${silentSeconds.toFixed(1)} с`);
    logBroadcastEvent('silence_warning', {
      silent_seconds: Number(silentSeconds.toFixed(2)),
      current_track_id: state.tracks[state.index]?.id || null,
    });
  }
  if (silentSeconds >= fallback && !state.automationBusy
      && !state.emergencyRecoveryBusy) {
    void recoverFromDeadAir(Number(silentSeconds.toFixed(2)));
  }
}

function renderLibrary() {
  const visible = state.tracks.slice(0, state.libraryVisible);
  $('#libraryCount').textContent = state.tracks.length;
  const libraryStatus = $('#aiLibraryStatus');
  if (libraryStatus) {
    const snapshot = state.radioQueue || {};
    const retry = Number(snapshot.retry_in_seconds || 0);
    const error = String(snapshot.last_error || '').trim();
    const blocked = String(snapshot.blocked_reason || '').trim();
    libraryStatus.classList.toggle('busy', !!snapshot.refilling);
    libraryStatus.classList.toggle('error', !!error && !snapshot.refilling && !blocked);
    if (snapshot.refilling) {
      libraryStatus.textContent = 'AI добирає відомий трек і передає його в LUMEN Downloader…';
    } else if (blocked) {
      libraryStatus.textContent = blocked;
    } else if (error) {
      const shortError = error.length > 280 ? `${error.slice(0, 277)}…` : error;
      libraryStatus.textContent = `Помилка пошуку: ${shortError}${retry ? ` · повтор через ${retry} с` : ''}`;
    } else if (state.tracks.length) {
      libraryStatus.textContent = `${state.tracks.length} треків відібрано AI та завантажено локально`;
    } else {
      libraryStatus.textContent = 'Очікую на перший трек від AI та LUMEN Downloader';
    }
  }
  const progress = state.radioQueue?.progress || {};
  const progressPercent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
  const progressBar = $('#downloadProgressBar');
  const progressLabel = $('#downloadProgressPercent');
  const progressText = $('#downloadProgressText');
  const progressDetails = $('#downloadProgressDetails');
  if (progressBar) progressBar.style.width = `${progressPercent}%`;
  if (progressLabel) progressLabel.textContent = `${Math.round(progressPercent)}%`;
  if (progressText) {
    progressText.textContent = progress.message || (
      state.radioQueue?.refilling
        ? 'AI шукає наступний трек…'
        : 'Бібліотека готова до відтворення'
    );
  }
  if (progressDetails) {
    const details = [];
    if (progress.track) details.push(progress.track);
    if (Number(progress.downloaded_bytes || 0)) {
      const total = Number(progress.total_bytes || 0);
      details.push(`${formatBytes(progress.downloaded_bytes)}${total ? ` / ${formatBytes(total)}` : ''}`);
    }
    if (Number(progress.speed || 0)) details.push(`${formatBytes(progress.speed)}/с`);
    if (Number(progress.eta || 0)) details.push(`ще ≈ ${Math.ceil(progress.eta)} с`);
    progressDetails.textContent = details.join(' · ') || 'Автоматичне поповнення бібліотеки ввімкнено';
  }
  const providerStatusList = $('#providerStatusList');
  if (providerStatusList) {
    const providers = state.radioQueue?.providers || [];
    providerStatusList.innerHTML = providers.map(provider => {
      const status = String(provider.state || 'ready');
      const retry = Number(provider.retry_in_seconds || 0);
      const retryText = retry ? ` · повтор через ${Math.max(1, Math.ceil(retry / 60))} хв` : '';
      const icon = status === 'ready' ? '●' : status === 'disabled' ? '×' : '○';
      return `<span class="providerStatus ${esc(status)}" title="${esc(provider.message || '')}">${icon} ${esc(provider.label || provider.name || 'AI')}: ${esc(provider.message || 'доступний')}${retryText}</span>`;
    }).join('') || '<span class="providerStatus disabled">× Немає налаштованого AI-провайдера</span>';
  }
  const localTracks = state.tracks.filter(hasPlayable);
  const libraryBytes = localTracks.reduce(
    (total, track) => total + Number(track.file_size_bytes || 0), 0
  );
  if ($('#libraryReadyCount')) $('#libraryReadyCount').textContent = localTracks.length;
  if ($('#librarySizeText')) {
    $('#librarySizeText').textContent = `${localTracks.length} треків · ${formatBytes(libraryBytes)}`;
  }
  if ($('#topLibraryCount')) $('#topLibraryCount').textContent = localTracks.length;
  const mainStatus = $('#mainLibraryStatus');
  const mainProgress = state.radioQueue?.refilling
    ? progressPercent
    : (localTracks.length ? 100 : progressPercent);
  if ($('#mainDownloadProgressBar')) {
    $('#mainDownloadProgressBar').style.width = `${mainProgress}%`;
  }
  if ($('#mainDownloadPercent')) {
    $('#mainDownloadPercent').textContent = `${Math.round(mainProgress)}%`;
  }
  if ($('#mainDownloadTitle')) {
    if (state.radioQueue?.refilling) {
      $('#mainDownloadTitle').textContent = progress.message || 'Завантажую наступний трек…';
    } else if (localTracks.length) {
      $('#mainDownloadTitle').textContent = `Бібліотека готова · ${localTracks.length} треків`;
    } else if (state.radioQueue?.blocked_reason) {
      $('#mainDownloadTitle').textContent = state.radioQueue.blocked_reason;
    } else {
      $('#mainDownloadTitle').textContent = 'Очікую на перший трек';
    }
  }
  if ($('#mainDownloadDetails')) {
    const currentTrack = String(progress.track || '').trim();
    const transfer = Number(progress.downloaded_bytes || 0)
      ? `${formatBytes(progress.downloaded_bytes)}${Number(progress.total_bytes || 0) ? ` / ${formatBytes(progress.total_bytes)}` : ''}`
      : '';
    const serviceNotice = state.radioQueue?.blocked_reason || state.radioQueue?.last_error || '';
    const idleDetails = serviceNotice
      ? `${formatBytes(libraryBytes)} локально · ${serviceNotice}`
      : `${formatBytes(libraryBytes)} локально · натисніть, щоб відкрити список`;
    $('#mainDownloadDetails').textContent = [currentTrack, transfer]
      .filter(Boolean).join(' · ') || idleDetails;
  }
  if (mainStatus) mainStatus.classList.toggle('busy', !!state.radioQueue?.refilling);
  renderUpdateStatus();
  $('#trackTable').innerHTML = visible.map((track, index) => `
    <div class="tr">
      <span>•</span>
      <span class="track"><b>${esc(track.title)}</b><small>${esc(track.artist)}</small></span>
      <button class="badge ${hasPlayable(track) ? 'ready' : ''}" onclick="resolveTrack(${index})">${isRejectedDiscoveryCache(track) ? '× СТАРИЙ КЕШ' : track.local_path ? `● ЗАВАНТАЖЕНО${Number(track.file_size_bytes || 0) ? ` · ${formatBytes(track.file_size_bytes)}` : ''}` : track.status === 'unavailable' ? '↻ ПОВТОРИТИ' : '↓ ЗАВАНТАЖИТИ'}</button>
      <button class="badge ${track.vocal_start_ms || track.outro_start_ms ? 'ready' : ''}" onclick="editTrackAnalysis(${index})">${track.vocal_start_ms || track.outro_start_ms ? '● ЗАДАНО' : '○ ДОДАТИ'}</button>
      <button class="badge ${Number(track.story_count) ? 'ready' : ''}" onclick="addMusicStory(${index})">${Number(track.story_count) ? `● ${track.story_count} STORY${Number(track.story_corroborated_count) ? ` · ${track.story_corroborated_count}×2` : ''}` : '○ STORY'}</button>
      <button class="badge ${track.pronunciation_review ? '' : 'ready'}" onclick="${track.pronunciation_review ? 'autoPronunciation' : 'editPronunciation'}(${index})">${track.pronunciation_review ? '↻ АВТО ВИМОВА' : '● ВИМОВА'}</button>
      <button onclick="tune(${index});showPage('onair')">В ефір</button>
    </div>`).join('') || '<p class="hint">Перший трек з’явиться тут одразу після завантаження</p>';
  $('#loadMore').hidden = visible.length >= state.tracks.length;
}

function formatBytes(value) {
  const bytes = Math.max(0, Number(value || 0));
  if (bytes < 1024) return `${Math.round(bytes)} Б`;
  const units = ['КБ', 'МБ', 'ГБ'];
  let size = bytes / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`;
}

function renderUpdateStatus() {
  const status = state.updateStatus || {};
  const percent = Math.max(0, Math.min(100, Number(status.percent || 0)));
  const version = status.current_version || state.appVersion || '—';
  if ($('#appVersionLabel')) $('#appVersionLabel').textContent = `v${version}`;
  if ($('#updateProgressBar')) $('#updateProgressBar').style.width = `${percent}%`;
  if ($('#updateProgressText')) {
    const error = String(status.error || '').trim();
    $('#updateProgressText').textContent = error || status.message || 'Перевіряю версію…';
  }
  const button = $('#applyUpdate');
  if (button) {
    button.hidden = !status.ready;
    button.textContent = status.latest_version
      ? `Встановити ${status.latest_version}`
      : 'Встановити оновлення';
  }
}

function esc(value = '') {
  const div = document.createElement('div');
  div.textContent = value;
  return div.innerHTML;
}

function showPage(id) {
  document.querySelector(`[data-page="${id}"]`).click();
}

function stopVoice() {
  const resolve = state.voiceResolve;
  state.voiceResolve = null;
  if (state.voiceAudio) {
    state.voiceAudio.pause();
    state.voiceAudio = null;
  }
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  state.systemVoicePlaying = false;
  if (resolve) resolve();
}

function select(index, options = {}) {
  if (!state.tracks.length) return;
  state.sequenceId += 1;
  state.pendingTrackEnd = false;
  if (state.localAudio) {
    state.localAudio.pause();
    state.localAudio = null;
  }
  state.audioTrackId = null;
  if (!options.preserveVoice) {
    stopVoice();
    state.outroVoiceStarted = false;
    state.outroVoicePromise = null;
  }
  if (!options.preserveSchedule) {
    state.upcomingIndices = [];
    state.preparedQueue = [];
    state.nextPreparedTransition = null;
    state.prefetchSignature = '';
  }
  state.index = (index + state.tracks.length) % state.tracks.length;
  const track = state.tracks[state.index];
  state.sessionPlayedTrackIds.add(track.id);
  $('#nowTitle').textContent = track.title;
  $('#nowArtist').textContent = track.artist;
  $('#coverRank').textContent = 'LIVE';
  $('#intro').textContent = track.intro ? '«' + track.intro + '»' : 'Ефір по настрою: живий перехід до треку.';
  state.playing = false;
  state.ducked = false;
  state.currentOutputVolume = 0;
  $('#play').textContent = '▶';
  render();
}

window.select = select;
window.showPage = showPage;

window.autoPronunciation = async index => {
  const track = state.tracks[index];
  toast(`Створюю фонетику: ${track.artist} — ${track.title}`);
  const result = await window.pywebview.api.generate_track_pronunciation(track.id);
  if (!result.ok) {
    toast(result.error);
    return;
  }
  state.tracks[index] = result.track;
  render();
  toast(result.review
    ? 'Фонетику створено одним API — натисніть ще раз для ручної перевірки'
    : 'Два API підтвердили однакову вимову');
};

window.editPronunciation = async index => {
  const track = state.tracks[index];
  const artistSpeech = prompt(
    `Як ведучий має вимовляти виконавця «${track.artist}»?`,
    track.artist_speech || track.artist,
  );
  if (artistSpeech === null) return;
  const titleSpeech = prompt(
    `Як ведучий має вимовляти назву «${track.title}»?`,
    track.title_speech || track.title,
  );
  if (titleSpeech === null) return;
  const result = await window.pywebview.api.set_track_pronunciation(
    track.id,
    artistSpeech.trim(),
    titleSpeech.trim(),
  );
  if (!result.ok) {
    toast(result.error);
    return;
  }
  state.tracks[index] = result.track;
  render();
  toast('Вимову збережено для наступних ефірів');
};

window.addMusicStory = async index => {
  const track = state.tracks[index];
  const category = prompt(
    'Категорія STORY: SONG_ORIGIN, STUDIO_STORY, BAND_ARGUMENT, LYRICS_ORIGIN, ACCIDENTAL_HIT, RECORDING_TRICK, LIVE_STORY, NAME_STORY або інша підтримувана:',
    'SONG_ORIGIN',
  );
  if (category === null) return;
  const durationClass = prompt('Тривалість: short (10–15 с), normal (15–25 с), feature (25–40 с):', 'normal');
  if (durationClass === null) return;
  const hook = prompt('Перевірений hook — найцікавіша перша деталь:', '');
  if (hook === null) return;
  const storyData = prompt(
    'Перевірені фрагменти сюжету в логічному порядку. Розділяйте символом |',
    '',
  );
  if (storyData === null) return;
  const verifiedQuote = prompt('Перевірена дослівна цитата, якщо є. Інакше залиште порожньою:', '');
  if (verifiedQuote === null) return;
  const sourceUrl = prompt('URL джерела — обов’язковий для VERIFIED:', '');
  if (sourceUrl === null) return;
  const sourceTitle = prompt('Назва джерела або матеріалу:', '');
  if (sourceTitle === null) return;
  const sourceTier = prompt('Рівень першого джерела: A, A-, B, B- або C:', 'B');
  if (sourceTier === null) return;
  const sourceIsPrimary = window.confirm(
    'Це першоджерело: оригінальне інтерв’ю, офіційний документ, запис або сторінка автора?',
  );
  const secondSourceUrl = prompt(
    'URL незалежного другого джерела. Залиште порожнім, якщо його ще немає:',
    '',
  );
  if (secondSourceUrl === null) return;
  let secondSourceTitle = '';
  let secondSourceTier = 'B';
  if (secondSourceUrl.trim()) {
    secondSourceTitle = prompt('Назва другого джерела:', '') ?? '';
    secondSourceTier = prompt('Рівень другого джерела: A, A-, B, B- або C:', 'B') ?? 'B';
  }
  const sensitive = window.confirm(
    'Історія містить тему війни, безпеки, здоров’я, фінансів, виборів, жертв або звинувачень?',
  );
  let reviewedBy = '';
  if (sensitive) {
    reviewedBy = prompt('Ім’я відповідального редактора для людського схвалення:', '') ?? '';
  }
  const series = prompt('Необов’язковий серіал у форматі ключ#епізод, наприклад paranoid#2:', '');
  if (series === null) return;
  const teaseNext = series.trim()
    ? prompt('Що ведучий може пообіцяти продовжити наступного разу:', '')
    : '';
  if (teaseNext === null) return;
  const seriesMatch = series.trim().match(/^(.+?)(?:#(\d+))?$/);
  const sources = [{
    id: 'source-1',
    url: sourceUrl.trim(),
    title: sourceTitle.trim(),
    tier: sourceTier.trim().toUpperCase(),
    primary: sourceIsPrimary,
    independent: true,
  }];
  if (secondSourceUrl.trim()) {
    sources.push({
      id: 'source-2',
      url: secondSourceUrl.trim(),
      title: secondSourceTitle.trim(),
      tier: secondSourceTier.trim().toUpperCase(),
      primary: false,
      independent: true,
    });
  }
  const fragments = storyData.split('|').map(part => part.trim()).filter(Boolean);
  const result = await window.pywebview.api.add_music_story(track.id, {
    category: category.trim().toUpperCase(),
    duration_class: durationClass.trim().toLowerCase(),
    hook: hook.trim(),
    story_data: fragments,
    verified_quote: verifiedQuote.trim(),
    source_url: sourceUrl.trim(),
    source_title: sourceTitle.trim(),
    sources,
    claims: fragments.map(text => ({
      text,
      source_ids: sources.map(source => source.id),
    })),
    sensitive,
    reviewed_by: reviewedBy.trim(),
    reviewed_at: reviewedBy.trim() ? new Date().toISOString() : '',
    confidence: 'verified',
    series_key: seriesMatch?.[1] || '',
    episode: Number(seriesMatch?.[2]) || 0,
    tease_next: (teaseNext || '').trim(),
  });
  if (!result.ok) {
    toast(result.error);
    return;
  }
  state.tracks[index] = result.track;
  state.preparedQueue = state.preparedQueue.filter(item => item.next_track_id !== track.id);
  if (state.nextPreparedTransition?.next_track_id === track.id) {
    state.nextPreparedTransition = null;
  }
  state.prefetchSignature = '';
  render();
  if (state.playing) void prefetchUpcomingTransitions();
  const verification = result.story?.verification || {};
  const statusLabel = {
    corroborated: 'два незалежні підтвердження',
    primary_source: 'перевірене першоджерело',
    single_source: 'одне надійне джерело',
  }[verification.status] || 'перевірено';
  toast(`Музичну історію додано: ${statusLabel}`);
};

window.editTrackAnalysis = async index => {
  const track = state.tracks[index];
  const vocal = prompt('Початок першого вокалу, секунд від старту:', String((Number(track.vocal_start_ms) || 0) / 1000));
  if (vocal === null) return;
  const outro = prompt('Початок outro, секунд від старту:', String((Number(track.outro_start_ms) || 0) / 1000));
  if (outro === null) return;
  const energy = prompt('Енергія треку від 1 до 10:', String(Number(track.energy) || 5));
  if (energy === null) return;
  const mood = prompt('Настрій: rock / party / chill / melancholic або власний:', track.mood || '');
  if (mood === null) return;
  const genre = prompt('Жанр: alternative / electronic / rock або власний:', track.genre || '');
  if (genre === null) return;
  const endType = prompt('Фінал: fade / cold / sustain / unknown:', track.end_type || 'unknown');
  if (endType === null) return;
  const verifiedFact = prompt('Додати один перевірений факт про трек? Можна залишити порожнім:', '');
  if (verifiedFact === null) return;
  const result = await window.pywebview.api.set_track_analysis(track.id, {
    vocal_start_ms: Math.max(0, Math.round((Number(vocal) || 0) * 1000)),
    outro_start_ms: Math.max(0, Math.round((Number(outro) || 0) * 1000)),
    energy: Math.max(1, Math.min(10, Number(energy) || 5)),
    mood: mood.trim(),
    genre: genre.trim(),
    end_type: endType.trim() || 'unknown',
  });
  if (!result.ok) {
    toast(result.error);
    return;
  }
  state.tracks[index] = result.track;
  if (verifiedFact.trim()) {
    const factResult = await window.pywebview.api.add_track_fact(track.id, verifiedFact.trim(), true);
    if (!factResult.ok) {
      toast(factResult.error);
      return;
    }
  }
  state.prefetchSignature = '';
  render();
  toast('Таймінг і характер треку збережено');
};

window.resolveTrack = async index => {
  const track = state.tracks[index];
  toast(`LUMEN Downloader: ${track.artist} — ${track.title}`);
  const result = await window.pywebview.api.resolve_track(track.id);
  if (result.ok) {
    state.tracks[index] = result.track;
    render();
    toast(result.cached ? 'Локальний аудіофайл уже готовий' : 'Трек завантажено локально');
    return;
  }
  toast(result.error);
};

function localUrl(path) {
  return '/' + path.split('/').map(encodeURIComponent).join('/');
}

function setOutputVolume(percent) {
  const volume = Math.max(0, Math.min(100, Number(percent) || 0));
  state.currentOutputVolume = volume;
  if (state.localAudio) state.localAudio.volume = volume / 100;
}

async function fadeOutputVolume(target, duration = 1400) {
  const start = state.currentOutputVolume;
  const steps = 20;
  for (let step = 1; step <= steps; step += 1) {
    await delay(duration / steps);
    setOutputVolume(start + (target - start) * (step / steps));
  }
  setOutputVolume(target);
}

async function playAtVolume(volume) {
  const track = state.tracks[state.index];
  if (!hasPlayable(track)) {
    toast('Трек ще не завантажений. Натисніть «Завантажити» у бібліотеці.');
    return false;
  }

  if (track.local_path) {
    if (!state.localAudio || state.audioTrackId !== track.id || state.localAudio.ended) {
      if (state.localAudio) state.localAudio.pause();
      state.localAudio = new Audio(localUrl(track.local_path));
      state.audioTrackId = track.id;
      state.localAudio.onplay = () => {
        state.playing = true;
        state.autoplayBlocked = false;
        noteAudibleOutput('local_track');
        $('#play').textContent = 'Ⅱ';
      };
      state.localAudio.ontimeupdate = () => maybeStartOutroVoice();
      state.localAudio.onloadedmetadata = () => {
        const measured = Math.round((state.localAudio.duration || 0) * 1000);
        if (measured > 0 && Math.abs((Number(track.duration_ms) || 0) - measured) > 1000) {
          track.duration_ms = measured;
          void window.pywebview.api.set_track_analysis(track.id, {duration_ms: measured});
        }
      };
      state.localAudio.onended = () => handleTrackEnded();
      state.localAudio.onerror = () => {
        state.playing = false;
        $('#play').textContent = '▶';
        toast('Не вдалося відкрити локальний аудіофайл');
      };
    }
    setOutputVolume(volume);
    try {
      await state.localAudio.play();
      return true;
    } catch (error) {
      state.autoplayBlocked = true;
      toast('Автозапуск заблоковано. Натисніть Play один раз.');
      return false;
    }
  }
  return false;
}

function sentenceCount(text) {
  return (text || '')
    .split(/[.!?]+(?:[»”"']+)?\s*/)
    .filter(part => part.trim()).length;
}

function currentEtaSeconds() {
  if (state.localAudio && Number.isFinite(state.localAudio.duration)) {
    return Math.max(0, state.localAudio.duration - state.localAudio.currentTime);
  }
  const track = state.tracks[state.index];
  return Math.max(0, (Number(track?.duration_ms) || 0) / 1000);
}

async function prefetchUpcomingTransitions() {
  if (!window.pywebview?.api || !state.tracks[state.index]) return;
  const upcoming = ensureUpcomingQueue();
  if (!upcoming.length) return;
  const ids = [state.tracks[state.index].id, ...upcoming.map(index => state.tracks[index].id)];
  const signature = ids.join(':');
  if (state.prefetching || state.prefetchSignature === signature) return;
  state.prefetching = true;
  state.prefetchSignature = signature;
  try {
    const immediate = await window.pywebview.api.prepare_transition_queue(
      ids.slice(0, 2),
      currentEtaSeconds(),
    );
    if (state.prefetchSignature !== signature) return;
    if (!immediate.ok || immediate.busy) {
      state.prefetchSignature = '';
      return;
    }
    state.preparedQueue = immediate.prepared || [];
    const nextTrack = state.tracks[upcoming[0]];
    state.nextPreparedTransition = await window.pywebview.api.get_prepared_transition(
      state.tracks[state.index].id,
      nextTrack.id,
      Number(nextTrack.vocal_start_ms) || 0,
    );
    render();
    if (ids.length > 2
        && state.prefetchSignature === signature) {
      const deeper = await window.pywebview.api.prepare_transition_queue(
        ids,
        currentEtaSeconds(),
      );
      if (deeper.ok && !deeper.busy && state.prefetchSignature === signature) {
        state.preparedQueue = deeper.prepared || state.preparedQueue;
        render();
      }
    }
    await refreshPilotClock();
  } catch (error) {
    state.prefetchSignature = '';
    console.warn('Transition pre-generation failed', error);
  } finally {
    state.prefetching = false;
    if (state.prefetchSignature !== signature && state.playing) {
      setTimeout(() => void prefetchUpcomingTransitions(), 250);
    }
  }
}

function maybeStartOutroVoice() {
  const prepared = state.nextPreparedTransition;
  if (
    state.outroVoiceStarted
    || prepared?.status !== 'ready'
    || prepared.transition_type !== 'talk_over_outro'
    || (!prepared.audio && !prepared.speech_text)
    || !state.localAudio
    || !Number.isFinite(state.localAudio.duration)
  ) return;
  const voiceEvent = (prepared.plan?.events || []).find(event => event.action === 'voice_start');
  if (!voiceEvent || voiceEvent.at_ms >= 0) return;
  const remainingMs = (state.localAudio.duration - state.localAudio.currentTime) * 1000;
  if (remainingMs > Math.abs(voiceEvent.at_ms) + 120) return;
  state.outroVoiceStarted = true;
  state.ducked = true;
  state.lastTransitionType = 'talk_over_outro';
  if (prepared.display_text) $('#intro').textContent = '«' + prepared.display_text + '»';
  void fadeOutputVolume(prepared.plan?.duck_percent || 32, 500);
  state.outroVoicePromise = playPreparedVoice(prepared);
}

async function ensureIntro(track, currentTrack, force = false) {
  if (!force && sentenceCount(track.intro) >= 1 && sentenceCount(track.intro) <= 3) {
    return {
      displayText: track.intro,
      speechText: track.intro_speech || track.intro,
    };
  }
  const result = await window.pywebview.api.make_intro(track.id, currentTrack?.id || null, '');
  if (result.ok) {
    track.intro = result.display_text || result.intro;
    track.intro_speech = result.speech_text || track.intro;
    track.intro_style = result.style || '';
    if (result.fallback || result.provider === 'template') {
      $('#intro').textContent = '«' + track.intro + '»';
      render();
      toast(result.provider_error
        ? `AI недоступний — озвучую перевірений локальний резерв. ${result.provider_error}`
        : 'Озвучую перевірений локальний резерв');
      return { displayText: track.intro, speechText: track.intro_speech };
    }
    $('#intro').textContent = '«' + track.intro + '»';
    render();
    if (result.provider_error) toast(result.provider_error);
  }
  const fallback = track.intro || '';
  return {
    displayText: fallback,
    speechText: track.intro_speech || fallback,
  };
}

async function beginCurrentTrack(
  currentTrack = null,
  withIntro = true,
  forceIntro = false,
  preparedTransition = null,
) {
  if (state.automationBusy) return false;
  state.automationBusy = true;
  const token = state.sequenceId;
  const track = state.tracks[state.index];
  state.lastAutomationError = '';
  try {
    if (preparedTransition) {
      const type = preparedTransition.transition_type || 'clean_segue';
      state.lastTransitionType = type;
      state.emergencySegue = preparedTransition.status === 'emergency';
      if (preparedTransition.display_text) {
        track.intro = preparedTransition.display_text;
        track.intro_speech = preparedTransition.speech_text || preparedTransition.display_text;
        track.intro_style = preparedTransition.style || '';
        $('#intro').textContent = '«' + preparedTransition.display_text + '»';
      }
      if (type === 'clean_segue' || (!preparedTransition.audio && !preparedTransition.speech_text)) {
        const started = await playAtVolume(programVolume());
        if (started) {
          state.broadcastStarted = true;
          void prefetchUpcomingTransitions();
        }
        return started;
      }
      if (type === 'between' || (type === 'talk_over_outro' && !state.outroVoiceStarted)) {
        state.ducked = false;
        await playPreparedVoice(preparedTransition);
        if (token !== state.sequenceId) return false;
        const started = await playAtVolume(programVolume());
        if (started) {
          state.broadcastStarted = true;
          void prefetchUpcomingTransitions();
        }
        return started;
      }
      if (type === 'talk_over_outro' && state.outroVoiceStarted) {
        const voiceStillPlaying = state.systemVoicePlaying
          || (!!state.voiceAudio && !state.voiceAudio.paused);
        const started = await playAtVolume(voiceStillPlaying ? introBedVolume() : programVolume());
        if (!started) return false;
        state.broadcastStarted = true;
        if (state.outroVoicePromise) await state.outroVoicePromise;
        if (token !== state.sequenceId) return false;
        if (voiceStillPlaying) await fadeOutputVolume(programVolume(), 800);
        state.ducked = false;
        void prefetchUpcomingTransitions();
        return true;
      }
      state.ducked = true;
      const started = await playAtVolume(preparedTransition.plan?.duck_percent || introBedVolume());
      if (!started) return false;
      state.broadcastStarted = true;
      await playPreparedVoice(preparedTransition);
      if (token !== state.sequenceId) return false;
      await fadeOutputVolume(programVolume(), 800);
      state.ducked = false;
      void prefetchUpcomingTransitions();
      return true;
    }

    if (!withIntro) {
      const started = await playAtVolume(programVolume());
      if (started) {
        state.broadcastStarted = true;
        void prefetchUpcomingTransitions();
      }
      return started;
    }

    // Start the song immediately. Text generation and local CPU TTS happen
    // while music plays at normal volume; duck only once speech is ready.
    state.ducked = false;
    const started = await playAtVolume(programVolume());
    if (!started) return false;
    state.broadcastStarted = true;
    state.autoplayBlocked = false;
    // Prepare the first song-to-song link immediately. Opening copy, spelling
    // checks, pronunciation and the next transition can now work in parallel.
    void prefetchUpcomingTransitions();
    const intro = await ensureIntro(track, currentTrack, forceIntro);
    if (token !== state.sequenceId) return false;
    await speak(intro.speechText, true);
    if (token !== state.sequenceId) return false;
    if (state.ducked) {
      await fadeOutputVolume(programVolume());
      state.ducked = false;
    }
    void prefetchUpcomingTransitions();
    return true;
  } catch (error) {
    state.lastAutomationError = String(error?.message || error);
    state.ducked = false;
    setOutputVolume(programVolume());
    console.error('Broadcast automation failed', error);
    toast('Автоматичний ефір: ' + state.lastAutomationError);
    return false;
  } finally {
    if (token === state.sequenceId) {
      state.automationBusy = false;
      if (state.pendingTrackEnd) {
        state.pendingTrackEnd = false;
        setTimeout(() => handleTrackEnded(), 0);
      }
    }
  }
}

async function startBroadcast() {
  if (!playableIndices().length) {
    toast('У локальному плейлисті немає аудіофайлів');
    return;
  }
  await beginCurrentTrack(null, true, true);
}

async function handleTrackEnded() {
  if (state.automationBusy) {
    state.pendingTrackEnd = true;
    return;
  }
  const current = state.tracks[state.index];
  state.playing = false;
  await window.pywebview.api.mark_played(current.id);
  current.play_count = (Number(current.play_count) || 0) + 1;
  let advancedSnapshot = null;
  if (typeof window.pywebview.api.advance_radio_queue === 'function') {
    try {
      advancedSnapshot = await window.pywebview.api.advance_radio_queue(current.id);
      applyRadioQueue(advancedSnapshot, false);
    } catch (error) {
      console.warn('Radio queue advance failed', error);
    }
  }
  const nextIndex = takeScheduledNext();
  const nextTrack = state.tracks[nextIndex];
  if (!nextTrack || (
    advancedSnapshot?.consumed_track_id === current.id
    && nextTrack.id === current.id
  )) {
    if (advancedSnapshot?.consumed_track_id === current.id) {
      removeTrackFromState(current.id);
    }
    state.broadcastStarted = false;
    state.audioTrackId = null;
    state.localAudio = null;
    state.prefetchSignature = '';
    render();
    toast('Трек відіграв і прибраний. Чекаю наступний AI-трек.');
    return;
  }
  let prepared = state.nextPreparedTransition;
  if (
    !prepared
    || (prepared.current_track_id && prepared.current_track_id !== current.id)
    || (prepared.next_track_id && prepared.next_track_id !== nextTrack.id)
  ) {
    prepared = await window.pywebview.api.get_prepared_transition(
      current.id,
      nextTrack.id,
      Number(nextTrack.vocal_start_ms) || 0,
    );
  }
  const preserveVoice = state.outroVoiceStarted
    && prepared?.transition_type === 'talk_over_outro';
  state.prefetchSignature = '';
  select(nextIndex, {preserveSchedule: true, preserveVoice});
  state.nextPreparedTransition = null;
  if (prepared?.content_type === 'story') {
    state.tracksSinceStory = 0;
  } else {
    state.tracksSinceStory += 1;
  }
  if (prepared?.content_type && prepared.content_type !== 'clean_segue') {
    state.tracksSinceHost = 0;
  } else {
    state.tracksSinceHost += 1;
  }
  if (advancedSnapshot?.consumed_track_id === current.id) {
    removeTrackFromState(current.id, nextTrack.id);
    render();
  }
  await beginCurrentTrack(current, false, false, prepared);
  await window.pywebview.api.mark_transition_aired(current.id, nextTrack.id);
  state.outroVoiceStarted = false;
  state.outroVoicePromise = null;
}

async function tune(index) {
  const current = state.tracks[state.index];
  select(index);
  await rebuildRadioQueue(index, false);
  state.tracksSinceHost = 0;
  await beginCurrentTrack(current, true, false);
}

window.tune = tune;

function pauseBroadcast() {
  state.sequenceId += 1;
  state.manualPause = true;
  state.pendingTrackEnd = false;
  if (state.localAudio) state.localAudio.pause();
  else state.player?.pauseVideo();
  stopVoice();
  state.playing = false;
  state.ducked = false;
  state.automationBusy = false;
  state.outroVoiceStarted = false;
  state.outroVoicePromise = null;
  $('#play').textContent = '▶';
}

$('#play').onclick = async () => {
  if (state.playing || state.automationBusy) {
    pauseBroadcast();
    return;
  }
  state.manualPause = false;
  state.lastAudibleAt = Date.now();
  if (!state.broadcastStarted || state.autoplayBlocked) await startBroadcast();
  else await playAtVolume(programVolume());
};

$('#prev').onclick = () => tune(sequentialNext(-1));
$('#next').onclick = () => tune(nextPlayable(1));
$('#volume').oninput = event => {
  state.settings.program_volume = event.target.value;
  const setting = $('[data-setting="program_volume"]');
  if (setting) setting.value = event.target.value;
  updateSettingOutput('program_volume', event.target.value);
  if (!state.ducked) setOutputVolume(event.target.value);
};
$('#shuffle').onclick = () => {
  const next = chooseUpcoming(state.index, state.upcomingIndices);
  if (next !== undefined && next !== null) tune(next);
};

$('#generate').onclick = async () => {
  const track = state.tracks[state.index];
  const result = await window.pywebview.api.make_intro(track.id);
  if (!result.ok) {
    toast(result.error);
    return;
  }
  track.intro = result.display_text || result.intro;
  track.intro_speech = result.speech_text || track.intro;
  track.intro_style = result.style || '';
  $('#intro').textContent = '«' + track.intro + '»';
  render();
  toast(['nvidia', 'secondary'].includes(result.provider)
    ? `${result.provider === 'nvidia' ? 'NVIDIA' : 'Другий AI'} створив підводку · ${result.style}`
    : (result.provider_error || 'Створено локальний fallback'));
};

function playVoiceAudio(audio) {
  stopVoice();
  return new Promise(resolve => {
    const finish = () => {
      if (state.voiceResolve === finish) state.voiceResolve = null;
      resolve();
    };
    state.voiceResolve = finish;
    state.voiceAudio = new Audio(audio);
    state.voiceAudio.onplay = () => noteAudibleOutput('tts_audio');
    state.voiceAudio.onended = finish;
    state.voiceAudio.onerror = () => {
      toast('Не вдалося програти TTS');
      finish();
    };
    state.voiceAudio.play().catch(() => {
      toast('Озвучення заблоковано. Натисніть Play один раз.');
      finish();
    });
  });
}

function playPreparedVoice(prepared) {
  if (prepared?.audio) return playVoiceAudio(prepared.audio);
  if (prepared?.speech_text) return playSystemVoice(prepared.speech_text);
  return Promise.resolve();
}

function playSystemVoice(text) {
  if (!text || !('speechSynthesis' in window)) return Promise.resolve();
  stopVoice();
  return new Promise(resolve => {
    const finish = () => {
      state.systemVoicePlaying = false;
      if (state.voiceResolve === finish) state.voiceResolve = null;
      resolve();
    };
    state.voiceResolve = finish;
    state.systemVoicePlaying = true;
    noteAudibleOutput('system_tts');
    const fallback = new SpeechSynthesisUtterance(text);
    fallback.lang = 'uk-UA';
    const systemVoice = window.speechSynthesis.getVoices().find(voice =>
      /^uk-UA$/i.test(voice.lang) && /ostap|остап/i.test(voice.name),
    );
    if (systemVoice) fallback.voice = systemVoice;
    fallback.pitch = 0.92;
    fallback.onend = finish;
    fallback.onerror = finish;
    window.speechSynthesis.speak(fallback);
  });
}

async function speak(text, duckWhenReady = false) {
  if (!String(text || '').trim()) return false;
  toast('Голос готується у фоні…');
  const result = await window.pywebview.api.synthesize_speech(text);
  if (duckWhenReady) {
    state.ducked = true;
    await fadeOutputVolume(introBedVolume(), 350);
  }
  if (result.ok) {
    toast(result.cached ? 'Голос готовий із кешу' : 'Локальний голос готовий');
    return playVoiceAudio(result.audio);
  }

  toast(result.error || 'TTS недоступний');
  return playSystemVoice(text);
}

const speakIntroButton = $('#speakIntro');
if (speakIntroButton) speakIntroButton.onclick = async () => {
  const track = state.tracks[state.index];
  const wasPlaying = state.playing;
  if (wasPlaying) {
    state.ducked = true;
    await fadeOutputVolume(introBedVolume(), 500);
  }
  await speak(track?.intro_speech || track?.intro || '');
  if (wasPlaying) {
    await fadeOutputVolume(programVolume());
    state.ducked = false;
  }
};

async function announceSafetyProtocol(result) {
  if (!result?.ok || !String(result.display_text || '').trim()) {
    toast(result?.error || 'Протокол не має тексту для ефіру');
    return false;
  }
  const text = result.display_text.trim();
  $('#intro').textContent = `«${text}»`;
  const musicWasPlaying = !!state.localAudio && !state.localAudio.paused;
  await speak(text, musicWasPlaying);
  if (musicWasPlaying && state.localAudio && !state.localAudio.paused) {
    await fadeOutputVolume(programVolume(), 700);
    state.ducked = false;
  }
  if (result.event_id && typeof window.pywebview?.api?.resolve_broadcast_event === 'function') {
    await window.pywebview.api.resolve_broadcast_event(result.event_id, 'aired');
  }
  await refreshBroadcastSafety();
  return true;
}

const correctionButton = $('#queueCorrection');
if (correctionButton) correctionButton.onclick = async () => {
  const original = window.prompt('Що саме прозвучало неточно?');
  if (original === null) return;
  const corrected = window.prompt('Яке формулювання є правильним?');
  if (corrected === null) return;
  const sourceTitle = window.prompt('Назва перевіреного джерела:');
  if (sourceTitle === null) return;
  const sourceUrl = window.prompt('Повний HTTP(S) URL джерела:');
  if (sourceUrl === null) return;
  const editor = window.prompt(
    'Відповідальний редактор:',
    state.settings.responsible_editor || '',
  );
  if (editor === null) return;
  correctionButton.disabled = true;
  try {
    const result = await window.pywebview.api.queue_correction(
      original,
      corrected,
      sourceUrl,
      sourceTitle,
      editor,
    );
    if (!result.ok) {
      toast(result.error);
      return;
    }
    state.emergencySegue = true;
    await announceSafetyProtocol(result);
    toast('Виправлення озвучено й зафіксовано');
  } catch (error) {
    toast(`Не вдалося поставити виправлення в ефір: ${error}`);
  } finally {
    correctionButton.disabled = false;
  }
};

const technicalPauseButton = $('#technicalPause');
if (technicalPauseButton) technicalPauseButton.onclick = async () => {
  technicalPauseButton.disabled = true;
  try {
    const result = await window.pywebview.api.emergency_protocol('technical_pause', {
      current_track_id: state.tracks[state.index]?.id || null,
      responsible_editor: state.settings.responsible_editor || '',
      initiated_by: 'operator',
    });
    state.emergencySegue = true;
    await announceSafetyProtocol(result);
  } catch (error) {
    toast(`Не вдалося запустити технічний протокол: ${error}`);
  } finally {
    technicalPauseButton.disabled = false;
  }
};

const apiFileInput = $('#apiFileInput');
if (apiFileInput) apiFileInput.onchange = async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  const status = $('#apiImportStatus');
  if (file.size > 65536) {
    status.textContent = 'TXT завеликий. Максимальний розмір — 64 КБ.';
    event.target.value = '';
    return;
  }
  try {
    $('#apiTextInput').value = await file.text();
    status.textContent = `Завантажено ${file.name}. Натисніть «Перевірити й зберегти».`;
  } catch (error) {
    status.textContent = `Не вдалося прочитати TXT: ${error}`;
  }
  event.target.value = '';
};

const importApiButton = $('#importApiText');
if (importApiButton) importApiButton.onclick = async () => {
  const text = $('#apiTextInput').value;
  const status = $('#apiImportStatus');
  importApiButton.disabled = true;
  status.textContent = 'Перевіряю формат ключів…';
  try {
    const result = await window.pywebview.api.import_api_text(text);
    if (!result.ok) {
      status.textContent = result.error;
      toast('API TXT не пройшов перевірку');
      return;
    }
    state.settings = result.settings || state.settings;
    $('#apiTextInput').value = '';
    const providerCounts = result.provider_counts || {};
    const providers = (result.providers || []).map((provider) => {
      const count = Number(providerCounts[provider] || 1);
      return count > 1 ? `${provider} ×${count}` : provider;
    }).join(', ');
    status.textContent = `Збережено локально: ${providers}. Секрети приховано.`;
    fillSettings();
    toast('API-ключі збережено');
  } catch (error) {
    status.textContent = `Не вдалося зберегти API: ${error}`;
  } finally {
    importApiButton.disabled = false;
  }
};

window.radioDiagnostics = () => ({
  bridge: !!window.pywebview?.api,
  tracks: state.tracks.length,
  localTracks: state.tracks.filter(hasPlayable).length,
  localPlaying: !!state.localAudio && !state.localAudio.paused,
  currentTrackId: state.tracks[state.index]?.id || null,
  currentOutputVolume: Math.round(state.currentOutputVolume),
  programVolume: programVolume(),
  introBedVolume: introBedVolume(),
  sequenceId: state.sequenceId,
  ducked: state.ducked,
  automationBusy: state.automationBusy,
  pendingTrackEnd: state.pendingTrackEnd,
  broadcastStarted: state.broadcastStarted,
  tracksSinceHost: state.tracksSinceHost,
  introSentences: sentenceCount(state.tracks[state.index]?.intro),
  introStyle: state.tracks[state.index]?.intro_style || '',
  hasSeparateSpeechText: !!state.tracks[state.index]?.intro_speech,
  scheduledTracks: state.upcomingIndices.length,
  prefetching: state.prefetching,
  preparedTransitions: state.preparedQueue.filter(item => item.status === 'ready').length,
  nextTransitionReady: state.nextPreparedTransition?.status === 'ready',
  lastTransitionType: state.lastTransitionType,
  emergencySegue: state.emergencySegue,
  outroVoiceStarted: state.outroVoiceStarted,
  lastAutomationError: state.lastAutomationError,
  playbackSource: 'local-audio-only',
  edgeTtsAudio: !!state.voiceAudio,
  ttsProvider: state.settings.use_styletts === '1' ? 'styletts2' : 'edge_tts',
  ttsPlaying: !!state.voiceAudio && !state.voiceAudio.paused,
  systemVoicePlaying: state.systemVoicePlaying,
  rotation: state.settings.rotation,
  rotationCycle: state.rotationCycle,
  sessionPlayedTracks: state.sessionPlayedTrackIds.size,
  scheduledTrackIds: state.upcomingIndices.map(index => state.tracks[index]?.id),
  radioBufferSize: state.radioQueue?.size || 0,
  radioBufferTarget: state.radioQueue?.target || 0,
  radioBufferRefilling: !!state.radioQueue?.refilling,
  radioBufferTrackIds: (state.radioQueue?.items || []).map(track => track.id),
  radioBufferError: state.radioQueue?.last_error || '',
  pilotClockVersion: state.pilotClock?.version || '',
  pilotClockSlot: state.pilotClock?.current_slot_id || '',
  hardPointAccuracy: state.pilotClock?.metrics?.hard_point_accuracy_percent ?? null,
  watchdogState: state.watchdogState,
  silenceWarnings: state.silenceWarnings,
  silenceFallbacks: state.silenceFallbacks,
  manualPause: state.manualPause,
  emergencyRecoveryBusy: state.emergencyRecoveryBusy,
  openCorrections: state.broadcastSafety?.open_corrections || 0,
  systemTts: 'speechSynthesis' in window,
});

$('#loadMore').onclick = () => {
  state.libraryVisible += 25;
  renderLibrary();
};

if ($('#applyUpdate')) {
  $('#applyUpdate').onclick = async () => {
    const button = $('#applyUpdate');
    button.disabled = true;
    button.textContent = 'Закриваю програму…';
    try {
      const result = await window.pywebview.api.apply_update();
      if (!result?.ok) throw new Error(result?.error || 'Патч не готовий');
      toast(result.message || 'Встановлюю оновлення…');
    } catch (error) {
      button.disabled = false;
      renderUpdateStatus();
      toast(`Не вдалося встановити оновлення: ${error?.message || error}`);
    }
  };
}

$('#saveSettings').onclick = async () => {
  try {
    const values = {};
    $$('[data-setting]').forEach(element => values[element.dataset.setting] = element.value);
    const simpleStationPrompt = $('#simpleStationPrompt');
    if (simpleStationPrompt) values.station_prompt = simpleStationPrompt.value;
    values.program_volume = state.settings.program_volume || $('#volume').value;
    const result = await window.pywebview.api.save_settings(values);
    $('#settingsLog').textContent = result.log || '';
    state.settings = result.settings;
    state.prefetchSignature = '';
    fillSettings();
    if (result.restarting) {
      // The backend is about to kill every LUMEN process and relaunch a fresh
      // one for the new style; nothing else in this window matters anymore.
      $('#saveSettings').disabled = true;
      toast('Стиль станції змінено — перезапускаю LUMEN Radio…');
      return;
    }
    if (result.tracks) {
      // Station style changed on the backend: the old AI library and its audio
      // files were purged, so any in-progress playback is no longer valid.
      if (state.localAudio) {
        state.localAudio.pause();
        state.localAudio = null;
      }
      state.audioTrackId = null;
      state.playing = false;
      state.broadcastStarted = false;
      state.autoplayBlocked = false;
      state.sessionPlayedTrackIds = new Set();
      state.preparedQueue = [];
      state.nextPreparedTransition = null;
      state.upcomingIndices = [];
      state.index = 0;
      state.tracks = result.tracks;
      $('#play').textContent = '▶';
      $('#nowTitle').textContent = 'Оберіть трек';
      $('#nowArtist').textContent = 'AI підбирає нові треки під новий стиль…';
      $('#intro').textContent = 'Підводка зʼявиться, коли AI знайде перший трек нового стилю.';
      applyRadioQueue(result.radio_queue, true);
      render();
      toast('Стиль станції змінено — бібліотеку очищено, AI шукає нові треки');
    } else {
      state.upcomingIndices = [];
      await rebuildRadioQueue(state.index, false);
      render();
      if (state.playing) void prefetchUpcomingTransitions();
      toast('Налаштування збережено');
    }
  } catch (error) {
    console.error('Save settings failed', error);
    $('#settingsLog').textContent = `ПОМИЛКА: ${error?.message || error}`;
    toast(`Не вдалося зберегти налаштування: ${error?.message || error}`);
  }
};

setInterval(() => {
  const local = state.localAudio;
  const current = local?.currentTime || 0;
  const duration = local?.duration || 0;
  $('#elapsed').textContent = `${Math.floor(current / 60)}:${String(Math.floor(current % 60)).padStart(2, '0')}`;
  $('#progress i').style.width = (duration && Number.isFinite(duration) ? current / duration * 100 : 0) + '%';
}, 1000);

setInterval(() => {
  if (booted) void refreshPilotClock();
}, 30000);

let updateRefreshBusy = false;
setInterval(async () => {
  const api = window.pywebview?.api;
  if (!booted || updateRefreshBusy || typeof api?.update_status !== 'function') return;
  updateRefreshBusy = true;
  try {
    state.updateStatus = await api.update_status();
    renderUpdateStatus();
  } catch (error) {
    console.warn('Update status refresh failed', error);
  } finally {
    updateRefreshBusy = false;
  }
}, 3000);

setInterval(() => {
  if (booted) checkSilenceWatchdog();
}, 250);

setInterval(() => {
  if (booted) void refreshBroadcastSafety();
}, 30000);

let queueRefreshBusy = false;
setInterval(async () => {
  const api = window.pywebview?.api;
  if (queueRefreshBusy || typeof api?.radio_queue_status !== 'function') return;
  queueRefreshBusy = true;
  try {
    let snapshot = await api.radio_queue_status();
    if (
      snapshot?.ok && snapshot.discovery_enabled
      && snapshot.size <= snapshot.refill_threshold
      && !snapshot.refilling && typeof api.request_radio_queue_refill === 'function'
    ) {
      snapshot = await api.request_radio_queue_refill();
    }
    if (snapshot?.ok) {
      const oldSignature = (state.radioQueue?.items || []).map(track => track.id).join(',');
      const newSignature = (snapshot.items || []).map(track => track.id).join(',');
      const shouldAutoStart = !state.broadcastStarted
        && !state.playing
        && !state.automationBusy
        && !state.autoplayBlocked
        && oldSignature !== newSignature
        && (snapshot.items || []).length > 0;
      const statusChanged = oldSignature !== newSignature
        || state.radioQueue?.refilling !== snapshot.refilling
        || state.radioQueue?.last_error !== snapshot.last_error
        || state.radioQueue?.phase !== snapshot.phase
        || state.radioQueue?.progress?.stage !== snapshot.progress?.stage
        || Math.round(Number(state.radioQueue?.progress?.percent || 0))
          !== Math.round(Number(snapshot.progress?.percent || 0))
        || JSON.stringify(state.radioQueue?.providers || [])
          !== JSON.stringify(snapshot.providers || []);
      applyRadioQueue(snapshot, shouldAutoStart);
      if (statusChanged) render();
      if (shouldAutoStart && playableIndices().length) {
        toast('Перший AI-трек готовий — запускаю ефір');
        await startBroadcast();
      }
    }
  } catch (error) {
    console.warn('Radio queue refresh failed', error);
  } finally {
    queueRefreshBusy = false;
  }
}, 5000);
