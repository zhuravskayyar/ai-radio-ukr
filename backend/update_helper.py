"""Out-of-process patch runner used after the Vector Radio window closes."""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path


def _process_exists(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
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


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--root", required=True)
    parser.add_argument("--patch", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    patch = Path(args.patch).resolve()
    expected_updates = root / "updates"
    if patch.parent != expected_updates or not patch.is_file():
        return 2
    deadline = time.monotonic() + 30
    while _process_exists(args.pid) and time.monotonic() < deadline:
        time.sleep(0.25)
    result = subprocess.run(
        [
            str(patch),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/LANG=ukrainian",
        ],
        cwd=str(root),
        check=False,
    )
    launcher = root / "VectorRadio.exe"
    if result.returncode == 0 and launcher.is_file():
        subprocess.Popen([str(launcher)], cwd=str(root))
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
