"""Wait for Vector Radio to close completely, then launch a fresh instance."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def _process_exists(pid):
    if os.name != "nt":
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
    )
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


def _launch(root):
    launcher = root / "VectorRadio.exe"
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    if launcher.is_file():
        subprocess.Popen(
            [str(launcher)], cwd=str(root), creationflags=flags, close_fds=True,
        )
        return
    interpreter = Path(sys.executable)
    pythonw = interpreter.with_name("pythonw.exe")
    if pythonw.is_file():
        interpreter = pythonw
    subprocess.Popen(
        [str(interpreter), str(root / "main.py")],
        cwd=str(root), creationflags=flags, close_fds=True,
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / "main.py").is_file():
        return 2

    deadline = time.monotonic() + 25
    while _process_exists(args.pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    if _process_exists(args.pid):
        return 3
    # Give WebView2 and the audio backend a short moment to release cache and
    # media handles before the fresh process scans or replaces local files.
    time.sleep(0.5)
    _launch(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
