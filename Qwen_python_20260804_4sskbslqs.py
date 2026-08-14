#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import ast
import json
import logging
import os
import queue
import re
import shutil
import sys
import threading
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("lumen.downloader")

try:
    import yt_dlp
    from yt_dlp.utils import DownloadError, match_filter_func
except ImportError:
    sys.exit("Потрібно встановити yt-dlp: python -m pip install -U yt-dlp")


def parse_urls(text):
    text = text.strip()
    if not text:
        return []

    # Варіант 1: JSON-масив, наприклад:
    # ["https://...", "https://..."]
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
    except Exception:
        pass

    # Варіант 2: Python-масив, наприклад:
    # ['https://...', 'https://...']
    try:
        data = ast.literal_eval(text)
        if isinstance(data, (list, tuple)):
            return [str(item).strip() for item in data if str(item).strip()]
    except Exception:
        pass

    # Варіант 3: просто текст, де URL можуть бути в рядках або через кому
    urls = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        # Шукаємо URL
        found = re.findall(r'https?://[^\s,]+', line)
        if found:
            for url in found:
                # Прибираємо можливі залишки лапок, дужок, ком тощо
                url = url.rstrip('\'",;)]}<>')
                if url and url not in urls:
                    urls.append(url)
        else:
            # Якщо рядок не схожий на URL, залишаємо як є.
            # Наприклад, якщо там ID або спеціальний формат посилання.
            if line not in urls:
                urls.append(line)

    return urls


def read_urls(source):
    # Читання зі stdin: python ytdlp_downloader.py -
    if source == '-':
        return parse_urls(sys.stdin.read())

    path = Path(source)
    if path.is_file():
        return parse_urls(path.read_text(encoding='utf-8-sig', errors='ignore'))

    # Якщо це не файл, вважаємо, що передали URL або текст із URL
    return parse_urls(source)


def unique_urls(urls):
    seen = set()
    result = []
    for url in urls:
        url = url.strip()
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


class DownloadStopped(Exception):
    """Зупинка завантаження з графічного інтерфейсу."""


def find_ffmpeg_location() -> str | None:
    """Знаходить FFmpeg у PATH або серед пакетів WinGet."""
    executable = shutil.which('ffmpeg')
    if executable:
        return str(Path(executable).parent)

    local_app_data = os.environ.get('LOCALAPPDATA')
    if not local_app_data:
        return None

    packages = Path(local_app_data) / 'Microsoft' / 'WinGet' / 'Packages'
    try:
        matches = sorted(
            packages.glob('Gyan.FFmpeg_*/*/bin/ffmpeg.exe'),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None

    return str(matches[0].parent) if matches else None


def find_javascript_runtime() -> str | None:
    """Find the private Deno installed with Vector Radio or a system copy."""
    candidates = [
        shutil.which('deno'),
        Path(sys.executable).with_name('deno.exe'),
        Path(__file__).resolve().parent / 'runtime' / 'Scripts' / 'deno.exe',
        Path(__file__).resolve().parent / 'runtime' / 'deno.exe',
    ]
    for value in candidates:
        if not value:
            continue
        path = Path(value)
        if path.is_file():
            return str(path.resolve())
    return None


def build_options(args, progress_hook=None, logger=None):
    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)
    out_base = outdir.as_posix()
    search_mode = bool(getattr(args, 'search', False))
    candidates = max(1, int(getattr(args, 'candidates', 5)))

    if args.number_playlist and not search_mode:
        outtmpl = (
            f"{out_base}/%(playlist_title)s/"
            f"%(playlist_index)03d - %(title)s [%(id)s].%(ext)s"
        )
    else:
        outtmpl = f"{out_base}/%(title)s [%(id)s].%(ext)s"

    # yt-dlp приймає великий словник параметрів із різними типами значень.
    # Any тут прибирає хибне попередження Pylance для внутрішнього _Params.
    opts: Any = {
        'outtmpl': outtmpl,
        'ignoreerrors': True,
        'retries': args.retries,
        'fragment_retries': args.retries,
        'socket_timeout': 30,
        'concurrent_fragment_downloads': 4,
        'windowsfilenames': True,
        'geo_bypass': True,
        'youtube_include_dash_manifest': True,
    }

    javascript_runtime = find_javascript_runtime()
    if javascript_runtime:
        opts['js_runtimes'] = {'deno': {'path': javascript_runtime}}

    if search_mode:
        # Перевіряємо кілька результатів і беремо перший доступний трек,
        # який пройшов фільтр. Ліміт 1 діє окремо для кожного запиту.
        # yt-dlp підтримує ytsearchN, але не підтримує псевдосхему ytmsearchN.
        opts['default_search'] = f'ytsearch{candidates}'
        opts['playlist_items'] = f'1-{candidates}'
        opts['max_downloads'] = 1
    else:
        opts['playlist_items'] = f'1-{args.limit}'
        opts['max_downloads'] = args.limit

    if args.cookies:
        opts['cookiefile'] = args.cookies

    if progress_hook:
        opts['progress_hooks'] = [progress_hook]

    if logger:
        opts['logger'] = logger

    if args.video:
        opts['format'] = 'bestvideo+bestaudio/best'
        opts['merge_output_format'] = args.video_format
    else:
        opts['format'] = 'bestaudio/best'

        if search_mode:
            # У Python API потрібна функція, а не текстовий match-filter.
            opts['match_filter'] = match_filter_func(
                '!is_live & duration >= 60 & duration <= 600'
            )

        ffmpeg_location = find_ffmpeg_location()
        postprocessors: list[dict[str, Any]] = []
        if args.convert and ffmpeg_location:
            postprocessors.append({
                'key': 'FFmpegExtractAudio',
                'preferredcodec': args.audio_format,
                'preferredquality': args.quality,
            })

        if ffmpeg_location:
            postprocessors.append({'key': 'FFmpegMetadata'})
            opts['postprocessors'] = postprocessors
            opts['ffmpeg_location'] = ffmpeg_location

    return opts


class _AttemptLogger:
    """Forward yt-dlp output while retaining concise failure diagnostics."""

    def __init__(self, logger):
        self.logger = logger
        self.errors: list[str] = []

    def debug(self, message):
        self.logger.debug(message)

    def info(self, message):
        self.logger.info(message)

    def warning(self, message):
        value = str(message or '').strip()
        if value and _is_age_restriction_error(value):
            self.errors.append(value)
        self.logger.warning(message)

    def error(self, message):
        value = str(message or '').strip()
        if value:
            self.errors.append(value)
        self.logger.error(message)


AGE_RESTRICTION_MARKERS = (
    'confirm your age',
    'age restricted',
    'age-restricted',
    'age verification',
    'inappropriate for some users',
)
YOUTUBE_AUTH_BROWSERS = {'chrome', 'edge', 'firefox'}


def _is_age_restriction_error(value: object) -> bool:
    lowered = str(value or '').casefold()
    return any(marker in lowered for marker in AGE_RESTRICTION_MARKERS)


def _youtube_auth_spec(browser: object, profile: object = None):
    name = str(browser or '').strip().casefold()
    if name in {'', '0', 'off', 'none', 'disabled'}:
        return None
    if name not in YOUTUBE_AUTH_BROWSERS:
        raise ValueError(
            'YouTube Auth підтримує лише Chrome, Edge або Firefox'
        )
    profile_name = str(profile or '').strip() or None
    return (name, profile_name, None, None) if profile_name else (name,)


def _friendly_download_error(value: object) -> str:
    text = str(value or '').strip()
    lowered = text.casefold()
    if not text:
        return ''
    if _is_age_restriction_error(text):
        return 'віково обмежений результат пропущено'
    if 'cookie' in lowered and (
        'decrypt' in lowered or 'database' in lowered or 'failed to load' in lowered
    ):
        return 'YouTube Auth не зміг прочитати cookies браузера'
    if 'http error 403' in lowered or '403: forbidden' in lowered:
        return 'YouTube відхилив завантаження (HTTP 403)'
    if 'video is not available' in lowered or 'this video is unavailable' in lowered:
        return 'відео недоступне'
    if 'private video' in lowered:
        return 'приватне відео пропущено'
    return re.sub(r'^ERROR:\s*', '', text, flags=re.IGNORECASE)[:240]


def _search_targets(item: str, music_search: bool) -> list[str]:
    if not music_search:
        return [item]
    # YouTube Music search pages are not directly supported by yt-dlp and can
    # redirect to an unrelated channel or album. Keep all retries in ordinary
    # YouTube search, where inaccessible entries can be skipped safely.
    return list(dict.fromkeys([
        item,
        f'{item} official audio',
        f'{item} topic',
    ]))


def is_download_limit_error(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    return 'maxdownloads' in name or 'max downloads' in text


AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.wav', '.webm'}


def _media_infos(payload):
    """Yield real media entries from either a video or search/playlist result."""
    if not isinstance(payload, dict):
        return
    entries = payload.get('entries')
    if entries:
        for entry in entries:
            yield from _media_infos(entry)
        return
    if payload.get('id') or payload.get('filepath') or payload.get('_filename'):
        yield payload


def _downloaded_audio_path(info, ydl, output_dir: Path) -> Path | None:
    candidates = []
    if isinstance(info, dict):
        candidates.extend([
            info.get('filepath'),
            info.get('_filename'),
            info.get('filename'),
            info.get('temp_filename'),
            info.get('tmpfilename'),
        ])
        candidates.extend(
            item.get('filepath')
            for item in info.get('requested_downloads', []) or []
            if isinstance(item, dict)
        )
        candidates.extend(
            item.get('filename')
            for item in info.get('requested_downloads', []) or []
            if isinstance(item, dict)
        )
        candidates.extend((info.get('__files_to_move') or {}).values())
        try:
            candidates.append(ydl.prepare_filename(info))
        except Exception:
            pass
    for value in candidates:
        if not value:
            continue
        path = Path(value)
        if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS:
            return path.resolve()

    media_id = str((info or {}).get('id') or '')
    if not media_id:
        return None
    matches = [
        path for path in output_dir.rglob('*')
        if path.is_file()
        and path.suffix.casefold() in AUDIO_EXTENSIONS
        and f'[{media_id}]' in path.name
    ]
    return max(matches, key=lambda path: path.stat().st_mtime).resolve() if matches else None


def download_audio_item(
    item: str,
    output: str | Path,
    *,
    search: bool = False,
    music_search: bool = False,
    candidates: int = 5,
    retries: int = 5,
    youtube_auth_browser: str | None = None,
    youtube_auth_profile: str | None = None,
    validator=None,
    progress_callback=None,
):
    """Download one local audio file using the same engine as the LUMEN GUI.

    Radio playback receives the completed local path; it never consumes the
    remote media URL as an audio stream.
    """
    output_dir = Path(output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    args = argparse.Namespace(
        output=str(output_dir),
        limit=1,
        number_playlist=False,
        cookies=None,
        video=False,
        video_format='mp4',
        convert=True,
        audio_format='mp3',
        quality='0',
        retries=max(1, int(retries)),
        search=bool(search),
        music_search=bool(music_search),
        candidates=max(1, int(candidates)),
    )
    targets = _search_targets(item, bool(search and music_search))
    auth_spec = _youtube_auth_spec(
        youtube_auth_browser, youtube_auth_profile,
    )

    last_error: Exception | None = None
    failure_details: list[str] = []
    for target in targets:
        completed_infos = []
        filter_rejections: list[str] = []
        validated_media_ids: set[str] = set()

        def capture_progress(data):
            status = str(data.get('status') or '')
            downloaded_bytes = int(data.get('downloaded_bytes') or 0)
            total_bytes = int(
                data.get('total_bytes') or data.get('total_bytes_estimate') or 0
            )
            percent = (
                min(100, max(0, downloaded_bytes / total_bytes * 100))
                if total_bytes else (100 if status == 'finished' else 0)
            )
            if progress_callback:
                progress_callback({
                    'status': status,
                    'percent': round(percent, 1),
                    'downloaded_bytes': downloaded_bytes,
                    'total_bytes': total_bytes,
                    'speed': float(data.get('speed') or 0),
                    'eta': int(data.get('eta') or 0),
                })
            if data.get('status') == 'finished' and isinstance(data.get('info_dict'), dict):
                completed_infos.append(data['info_dict'])

        attempt_logger = _AttemptLogger(LOGGER)
        opts = build_options(args, progress_hook=capture_progress, logger=attempt_logger)
        # Search playlists must continue after a private, age-restricted,
        # unavailable, or HTTP 403 result. A direct URL should still surface
        # its extraction failure immediately.
        opts['ignoreerrors'] = bool(search)
        opts['noplaylist'] = not search
        base_filter = opts.get('match_filter')
        if validator:
            def validated_filter(info, *, incomplete=False):
                if base_filter:
                    rejected = base_filter(info, incomplete=incomplete)
                    if rejected:
                        if not incomplete:
                            filter_rejections.append(str(rejected))
                        return rejected
                if not incomplete and not validator(info):
                    reason = 'результат не збігається з виконавцем і назвою треку'
                    filter_rejections.append(reason)
                    return reason
                if not incomplete:
                    media_id = str(info.get('id') or '').strip()
                    if media_id:
                        validated_media_ids.add(media_id)
                return None

            opts['match_filter'] = validated_filter

        def try_download(attempt_opts):
            ydl = None
            try:
                with yt_dlp.YoutubeDL(attempt_opts) as ydl:
                    payload = ydl.extract_info(target, download=True)
                    infos = [*completed_infos, *_media_infos(payload)]
                    for info in reversed(infos):
                        if validator and str(info.get('id') or '') not in validated_media_ids:
                            continue
                        path = _downloaded_audio_path(info, ydl, output_dir)
                        if path:
                            return {'path': path, 'info': info}, None
            except Exception as exc:
                # A completed audio file is valid even when an optional
                # postprocessor or max_downloads=1 raises afterwards. The
                # radio consumes the local file, not a remote media URL.
                if ydl is not None:
                    try:
                        for info in reversed(completed_infos):
                            if validator and str(info.get('id') or '') not in validated_media_ids:
                                continue
                            path = _downloaded_audio_path(info, ydl, output_dir)
                            if path:
                                return {'path': path, 'info': info}, None
                    except Exception:
                        pass
                return None, exc
            return None, None

        downloaded, attempt_error = try_download(opts)
        if downloaded:
            return downloaded
        if attempt_error:
            last_error = attempt_error

        raw_failures = [attempt_error, *attempt_logger.errors]
        age_restricted = any(
            _is_age_restriction_error(value)
            for value in raw_failures
            if value
        )

        # Browser cookies are deliberately absent from ordinary searches.
        # Retry the same target with the selected browser session only when
        # yt-dlp explicitly reports an age gate. If that still fails, the
        # surrounding search continues to the next target/candidate.
        if auth_spec and age_restricted:
            LOGGER.info(
                'YouTube age restriction detected; retrying with %s cookies',
                auth_spec[0],
            )
            auth_logger = _AttemptLogger(LOGGER)
            auth_opts = dict(opts)
            auth_opts['logger'] = auth_logger
            auth_opts['cookiesfrombrowser'] = auth_spec
            downloaded, auth_error = try_download(auth_opts)
            if downloaded:
                return downloaded
            if auth_error:
                last_error = auth_error
            raw_failures.extend([auth_error, *auth_logger.errors])

        for value in raw_failures:
            friendly = _friendly_download_error(value)
            if friendly and friendly not in failure_details:
                failure_details.append(friendly)
        if filter_rejections:
            detail = 'результати не пройшли перевірку назви, виконавця або доступності'
            if detail not in failure_details:
                failure_details.append(detail)

    if not failure_details and last_error:
        friendly = _friendly_download_error(last_error)
        if friendly:
            failure_details.append(friendly)
    if not failure_details:
        failure_details.append('YouTube не повернув доступного аудіо')
    details = ': ' + '; '.join(failure_details[-3:])
    raise RuntimeError(f'LUMEN Downloader не завантажив локальний аудіофайл{details}')


def run_downloads(items, args, progress_hook=None, logger=None, item_hook=None):
    """Запускає прямі URL разом або по одному пошуковому запиту."""
    if not getattr(args, 'search', False):
        opts = build_options(args, progress_hook=progress_hook, logger=logger)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download(items)
        return

    for index, query in enumerate(items[:args.limit], start=1):
        if item_hook:
            item_hook(index, len(items[:args.limit]), query)

        targets = _search_targets(
            query, bool(getattr(args, 'music_search', False)),
        )

        last_error: Exception | None = None
        success = False
        for target in targets:
            # Новий YoutubeDL для кожної спроби: max_downloads=1 тоді означає
            # рівно один успішний результат на одну назву треку.
            opts = build_options(args, progress_hook=progress_hook, logger=logger)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    result = ydl.download([target])
                if result in (None, 0):
                    success = True
                    break
            except DownloadStopped:
                raise
            except Exception as exc:
                if is_download_limit_error(exc):
                    success = True
                    break
                last_error = exc

        if not success and last_error:
            if logger:
                logger.error(f'Не вдалося обробити "{query}": {last_error}')
            else:
                print(f'Помилка для "{query}": {last_error}')


def launch_gui():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError:
        sys.exit('Tkinter не знайдено. Встановіть Python із підтримкою Tcl/Tk.')

    root = tk.Tk()
    root.title('LUMEN — завантажувач медіа')
    root.geometry('960x760')
    root.minsize(800, 660)
    root.configure(bg='#0b0f13')

    colors = {
        'bg': '#0b0f13',
        'panel': '#121820',
        'field': '#0e141a',
        'border': '#26313b',
        'text': '#f4f7f9',
        'muted': '#93a4b3',
        'accent': '#b8ff24',
        'accent_dark': '#8dcc00',
    }

    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('TFrame', background=colors['bg'])
    style.configure('Panel.TFrame', background=colors['panel'])
    style.configure('TLabel', background=colors['bg'], foreground=colors['text'], font=('Segoe UI', 10))
    style.configure('Muted.TLabel', background=colors['bg'], foreground=colors['muted'], font=('Segoe UI', 9))
    style.configure('Panel.TLabel', background=colors['panel'], foreground=colors['text'], font=('Segoe UI', 10))
    style.configure('Title.TLabel', background=colors['bg'], foreground=colors['text'], font=('Segoe UI Semibold', 20))
    style.configure('Accent.TButton', background=colors['accent'], foreground='#091000', font=('Segoe UI Semibold', 10), padding=(16, 9))
    style.map('Accent.TButton', background=[('active', colors['accent_dark']), ('disabled', '#52612f')])
    style.configure('TButton', background='#1a232c', foreground=colors['text'], font=('Segoe UI', 10), padding=(12, 8))
    style.map('TButton', background=[('active', '#25313c')])
    style.configure('TCheckbutton', background=colors['panel'], foreground=colors['text'], font=('Segoe UI', 10))
    style.map('TCheckbutton', background=[('active', colors['panel'])])
    style.configure('TCombobox', fieldbackground=colors['field'], background=colors['field'], foreground=colors['text'])
    style.configure('Horizontal.TProgressbar', troughcolor=colors['border'], background=colors['accent'])

    output_var = tk.StringVar(value=str(Path.cwd() / 'downloads'))
    cookies_var = tk.StringVar()
    limit_var = tk.StringVar(value='100')
    audio_format_var = tk.StringVar(value='mp3')
    video_var = tk.BooleanVar(value=False)
    convert_var = tk.BooleanVar(value=True)
    number_var = tk.BooleanVar(value=True)
    search_var = tk.BooleanVar(value=True)
    music_search_var = tk.BooleanVar(value=True)
    candidates_var = tk.StringVar(value='5')
    status_var = tk.StringVar(value='Готово до роботи')
    progress_var = tk.DoubleVar(value=0)
    events: queue.Queue[tuple[str, Any]] = queue.Queue()
    stop_event = threading.Event()
    worker: threading.Thread | None = None

    container = ttk.Frame(root, padding=(28, 22))
    container.pack(fill='both', expand=True)

    ttk.Label(container, text='LUMEN DOWNLOADER', style='Title.TLabel').pack(anchor='w')
    ttk.Label(
        container,
        text='Вставте назви «Виконавець — Трек», URL, JSON/Python-масив або відкрийте TXT.',
        style='Muted.TLabel',
    ).pack(anchor='w', pady=(3, 16))

    input_panel = ttk.Frame(container, style='Panel.TFrame', padding=14)
    input_panel.pack(fill='both', expand=True)

    top_line = ttk.Frame(input_panel, style='Panel.TFrame')
    top_line.pack(fill='x', pady=(0, 9))
    ttk.Label(top_line, text='ТРЕКИ АБО ПОСИЛАННЯ', style='Panel.TLabel').pack(side='left')

    url_text = tk.Text(
        input_panel,
        height=11,
        wrap='word',
        undo=True,
        bg=colors['field'],
        fg=colors['text'],
        insertbackground=colors['accent'],
        selectbackground='#405221',
        relief='flat',
        borderwidth=0,
        padx=12,
        pady=10,
        font=('Consolas', 10),
    )
    url_text.pack(fill='both', expand=True)

    def count_input_urls() -> int:
        return len(unique_urls(parse_urls(url_text.get('1.0', 'end'))))

    def load_txt() -> None:
        path = filedialog.askopenfilename(
            title='Виберіть TXT із назвами або легальними URL',
            filetypes=[('Текстові файли', '*.txt'), ('Усі файли', '*.*')],
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding='utf-8-sig', errors='ignore')
            url_text.delete('1.0', 'end')
            url_text.insert('1.0', content)
            status_var.set(f'Завантажено з TXT: {count_input_urls()} джерел')
        except OSError as exc:
            messagebox.showerror('Не вдалося відкрити TXT', str(exc))

    def choose_output() -> None:
        path = filedialog.askdirectory(title='Папка для збереження', initialdir=output_var.get())
        if path:
            output_var.set(path)

    def choose_cookies() -> None:
        path = filedialog.askopenfilename(
            title='Виберіть cookies.txt',
            filetypes=[('cookies.txt', '*.txt'), ('Усі файли', '*.*')],
        )
        if path:
            cookies_var.set(path)

    ttk.Button(top_line, text='Відкрити TXT', command=load_txt).pack(side='right')
    ttk.Button(
        top_line,
        text='Очистити',
        command=lambda: url_text.delete('1.0', 'end'),
    ).pack(side='right', padx=(0, 8))

    settings = ttk.Frame(container, style='Panel.TFrame', padding=14)
    settings.pack(fill='x', pady=(12, 0))
    settings.columnconfigure(1, weight=1)

    ttk.Label(settings, text='Папка:', style='Panel.TLabel').grid(row=0, column=0, sticky='w', padx=(0, 10), pady=5)
    output_entry = tk.Entry(
        settings,
        textvariable=output_var,
        bg=colors['field'],
        fg=colors['text'],
        insertbackground=colors['accent'],
        relief='flat',
        font=('Segoe UI', 10),
    )
    output_entry.grid(row=0, column=1, sticky='ew', pady=5, ipady=7)
    ttk.Button(settings, text='Огляд', command=choose_output).grid(row=0, column=2, padx=(9, 0), pady=5)

    ttk.Label(settings, text='Cookies:', style='Panel.TLabel').grid(row=1, column=0, sticky='w', padx=(0, 10), pady=5)
    cookies_entry = tk.Entry(
        settings,
        textvariable=cookies_var,
        bg=colors['field'],
        fg=colors['text'],
        insertbackground=colors['accent'],
        relief='flat',
        font=('Segoe UI', 10),
    )
    cookies_entry.grid(row=1, column=1, sticky='ew', pady=5, ipady=7)
    ttk.Button(settings, text='Огляд', command=choose_cookies).grid(row=1, column=2, padx=(9, 0), pady=5)

    options = ttk.Frame(settings, style='Panel.TFrame')
    options.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(8, 2))
    ttk.Label(options, text='Ліміт:', style='Panel.TLabel').pack(side='left')
    limit_spin = tk.Spinbox(
        options,
        from_=1,
        to=9999,
        width=6,
        textvariable=limit_var,
        bg=colors['field'],
        fg=colors['text'],
        buttonbackground=colors['border'],
        relief='flat',
        font=('Segoe UI', 10),
    )
    limit_spin.pack(side='left', padx=(7, 18), ipady=5)
    ttk.Label(options, text='Формат:', style='Panel.TLabel').pack(side='left')
    format_box = ttk.Combobox(
        options,
        textvariable=audio_format_var,
        values=('mp3', 'm4a', 'aac', 'flac', 'opus', 'vorbis', 'wav'),
        width=8,
        state='readonly',
    )
    format_box.pack(side='left', padx=(7, 18))
    ttk.Checkbutton(options, text='Конвертувати через FFmpeg', variable=convert_var).pack(side='left', padx=(0, 18))
    ttk.Checkbutton(options, text='Відео', variable=video_var).pack(side='left', padx=(0, 18))
    ttk.Checkbutton(options, text='Нумерувати', variable=number_var).pack(side='left')

    search_options = ttk.Frame(settings, style='Panel.TFrame')
    search_options.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(7, 2))
    ttk.Checkbutton(
        search_options,
        text='Шукати за назвами',
        variable=search_var,
    ).pack(side='left', padx=(0, 18))
    ttk.Checkbutton(
        search_options,
        text='YouTube Music',
        variable=music_search_var,
    ).pack(side='left', padx=(0, 18))
    ttk.Label(search_options, text='Кандидатів:', style='Panel.TLabel').pack(side='left')
    candidates_spin = tk.Spinbox(
        search_options,
        from_=1,
        to=20,
        width=5,
        textvariable=candidates_var,
        bg=colors['field'],
        fg=colors['text'],
        buttonbackground=colors['border'],
        relief='flat',
        font=('Segoe UI', 10),
    )
    candidates_spin.pack(side='left', padx=(7, 0), ipady=5)

    progress = ttk.Progressbar(container, variable=progress_var, maximum=100)
    progress.pack(fill='x', pady=(14, 5))
    ttk.Label(container, textvariable=status_var, style='Muted.TLabel').pack(anchor='w')

    log_text = tk.Text(
        container,
        height=6,
        state='disabled',
        wrap='word',
        bg=colors['field'],
        fg=colors['muted'],
        relief='flat',
        borderwidth=0,
        padx=10,
        pady=8,
        font=('Consolas', 9),
    )
    log_text.pack(fill='both', expand=False, pady=(8, 0))

    buttons = ttk.Frame(container)
    buttons.pack(fill='x', pady=(13, 0))
    start_button = ttk.Button(buttons, text='Завантажити', style='Accent.TButton')
    start_button.pack(side='left')
    stop_button = ttk.Button(buttons, text='Зупинити', state='disabled')
    stop_button.pack(side='left', padx=(9, 0))
    ttk.Label(
        buttons,
        text='Завантажуйте лише контент, на який маєте право.',
        style='Muted.TLabel',
    ).pack(side='right')

    def append_log(message: str) -> None:
        if not message:
            return
        log_text.configure(state='normal')
        log_text.insert('end', message.rstrip() + '\n')
        if int(log_text.index('end-1c').split('.')[0]) > 500:
            log_text.delete('1.0', '100.0')
        log_text.see('end')
        log_text.configure(state='disabled')

    class GuiLogger:
        def debug(self, message: str) -> None:
            if message.startswith('[download]') and 'Destination:' in message:
                events.put(('log', message))

        def warning(self, message: str) -> None:
            events.put(('log', f'Попередження: {message}'))

        def error(self, message: str) -> None:
            events.put(('log', f'Помилка: {message}'))

    def progress_hook(data: dict[str, Any]) -> None:
        if stop_event.is_set():
            raise DownloadStopped('Зупинено користувачем')

        state = data.get('status')
        info = data.get('info_dict') or {}
        title = info.get('title') or 'Медіафайл'
        if state == 'downloading':
            downloaded = data.get('downloaded_bytes') or 0
            total = data.get('total_bytes') or data.get('total_bytes_estimate') or 0
            percent = (downloaded / total * 100) if total else 0
            events.put(('progress', percent))
            speed = data.get('_speed_str', '').strip()
            events.put(('status', f'{title} — {percent:.1f}% {speed}'.strip()))
        elif state == 'finished':
            events.put(('progress', 100))
            events.put(('status', f'Обробка: {title}'))
            events.put(('log', f'Готово: {title}'))

    def set_running(running: bool) -> None:
        start_button.configure(state='disabled' if running else 'normal')
        stop_button.configure(state='normal' if running else 'disabled')

    def download_worker(items: list[str], args: argparse.Namespace) -> None:
        def item_hook(index: int, total: int, query: str) -> None:
            if stop_event.is_set():
                raise DownloadStopped('Зупинено користувачем')
            events.put(('log', f'Пошук {index}/{total}: {query}'))
            events.put(('status', f'Пошук: {query}'))

        try:
            run_downloads(
                items,
                args,
                progress_hook=progress_hook,
                logger=GuiLogger(),
                item_hook=item_hook,
            )
            if stop_event.is_set():
                events.put(('stopped', None))
            else:
                events.put(('done', args.output))
        except Exception as exc:
            if stop_event.is_set() or isinstance(exc, DownloadStopped):
                events.put(('stopped', None))
                return
            if is_download_limit_error(exc):
                events.put(('done', args.output))
            else:
                events.put(('error', str(exc)))

    def start_download() -> None:
        nonlocal worker
        items = unique_urls(parse_urls(url_text.get('1.0', 'end')))
        if not items:
            messagebox.showwarning('Порожній список', 'Вставте назви треків, URL або відкрийте TXT-файл.')
            return

        try:
            limit = int(limit_var.get())
            if limit <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning('Неправильний ліміт', 'Вкажіть ціле число більше нуля.')
            return

        try:
            candidates = int(candidates_var.get())
            if candidates <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning('Неправильне число', 'Кількість кандидатів має бути більшою за нуль.')
            return

        output = output_var.get().strip()
        if not output:
            messagebox.showwarning('Не вибрана папка', 'Виберіть папку для збереження.')
            return

        items = items[:limit]
        args = argparse.Namespace(
            output=output,
            limit=limit,
            number_playlist=number_var.get(),
            cookies=cookies_var.get().strip() or None,
            video=video_var.get(),
            video_format='mp4',
            convert=convert_var.get() and not video_var.get(),
            audio_format=audio_format_var.get(),
            quality='0',
            retries=5,
            search=search_var.get(),
            music_search=music_search_var.get(),
            candidates=candidates,
        )

        stop_event.clear()
        progress_var.set(0)
        set_running(True)
        mode = 'пошук за назвами' if args.search else 'прямі URL'
        append_log(f'Режим: {mode}. Джерел: {len(items)}. Папка: {output}')
        status_var.set('Підготовка…')
        worker = threading.Thread(target=download_worker, args=(items, args), daemon=True)
        worker.start()

    def stop_download() -> None:
        stop_event.set()
        stop_button.configure(state='disabled')
        status_var.set('Зупинка після поточного мережевого кроку…')

    def process_events() -> None:
        try:
            while True:
                event, payload = events.get_nowait()
                if event == 'log':
                    append_log(str(payload))
                elif event == 'progress':
                    progress_var.set(float(payload))
                elif event == 'status':
                    status_var.set(str(payload))
                elif event == 'done':
                    set_running(False)
                    progress_var.set(100)
                    status_var.set('Завантаження завершено')
                    append_log(f'Усі доступні файли оброблено. Папка: {payload}')
                elif event == 'stopped':
                    set_running(False)
                    status_var.set('Зупинено користувачем')
                    append_log('Завантаження зупинено.')
                elif event == 'error':
                    set_running(False)
                    status_var.set('Сталася помилка')
                    append_log(f'Помилка: {payload}')
                    messagebox.showerror('Помилка завантаження', str(payload))
        except queue.Empty:
            pass
        root.after(100, process_events)

    def close_window() -> None:
        if worker and worker.is_alive():
            if not messagebox.askyesno('Завантаження триває', 'Зупинити завантаження і закрити вікно?'):
                return
            stop_event.set()
        root.destroy()

    start_button.configure(command=start_download)
    stop_button.configure(command=stop_download)
    root.protocol('WM_DELETE_WINDOW', close_window)
    root.after(100, process_events)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(
        description='Завантаження через yt-dlp: пошук за "Виконавець — Трек" або прямі URL.'
    )

    parser.add_argument('input', nargs='?', help='TXT-файл із назвами/URL, один запит або - для stdin')
    parser.add_argument('-f', '--file', help='Альтернативний TXT-файл')
    parser.add_argument('--gui', action='store_true',
                        help='Відкрити графічний інтерфейс')
    parser.add_argument('-l', '--limit', type=int, default=100,
                        help='Перші N елементів/файлів (за замовчуванням 100)')
    parser.add_argument('-o', '--output', default='downloads',
                        help='Папка для завантажень')

    parser.add_argument('--search', action='store_true',
                        help='Шукати кожен рядок як "Виконавець — Трек"')
    parser.add_argument('--music-search', action='store_true',
                        help='Використовувати пошук YouTube Music')
    parser.add_argument('--candidates', type=int, default=5,
                        help='Кількість результатів для перевірки (за замовчуванням 5)')

    parser.add_argument('--video', action='store_true',
                        help='Завантажувати відео, а не аудіо')
    parser.add_argument('--convert', action='store_true',
                        help='Конвертувати аудіо (потрібен FFmpeg)')
    parser.add_argument('--audio-format', default='mp3',
                        choices=['mp3', 'm4a', 'aac', 'flac', 'opus', 'vorbis', 'wav'])
    parser.add_argument('--quality', default='0',
                        help='Якість аудіо для конвертації, 0 = найкраща')

    parser.add_argument('--video-format', default='mp4',
                        choices=['mp4', 'mkv', 'webm'])

    parser.add_argument('--cookies', help='Файл cookies.txt для приватних/закритих сторінок')
    parser.add_argument('--number-playlist', action='store_true',
                        help='Створити папку плейлиста і нумерувати треки за playlist_index')
    parser.add_argument('--retries', type=int, default=5,
                        help='Кількість повторів при помилках')

    args = parser.parse_args()

    # Подвійний клік або запуск без аргументів відкриває віконний режим.
    if args.gui or (not args.input and not args.file):
        launch_gui()
        return

    if args.limit <= 0:
        parser.error('--limit має бути більше 0')
    if args.candidates <= 0:
        parser.error('--candidates має бути більше 0')

    items = []

    if args.file:
        items.extend(read_urls(args.file))

    if args.input:
        items.extend(read_urls(args.input))

    if not items:
        parser.error('Потрібно передати TXT-файл, назву треку, URL або дані через stdin')

    items = unique_urls(items)[:args.limit]

    print(f'Режим: {"ПОШУК" if args.search else "ПРЯМІ URL"}')
    print(f'Кількість джерел: {len(items)}')
    print(f'Ліміт: {args.limit}')
    print(f'Папка: {args.output}')

    try:
        run_downloads(
            items,
            args,
            item_hook=lambda index, total, query: print(
                f'\nПошук {index}/{total}: {query}'
            ),
        )
    except KeyboardInterrupt:
        print('\nЗупинено користувачем.')
        sys.exit(130)
    except Exception as e:
        if is_download_limit_error(e):
            print('Ліміт завантажень досягнуто.')
        elif isinstance(e, DownloadError):
            sys.exit(f'Помилка завантаження: {e}')
        else:
            sys.exit(f'Помилка: {e}')


if __name__ == '__main__':
    main()
