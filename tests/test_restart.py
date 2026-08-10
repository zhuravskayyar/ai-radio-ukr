import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main as radio_main
from backend.api import RadioAPI


class _FakeKernel32:
    def __init__(self):
        self.errors = [183, 0]
        self.handles = [101, 202]
        self.closed = []

    def CreateMutexW(self, *_args):
        return self.handles.pop(0)

    def GetLastError(self):
        return self.errors.pop(0)

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return True


class RestartTests(unittest.TestCase):
    def tearDown(self):
        radio_main._SINGLE_INSTANCE_HANDLE = None

    def test_waiting_instance_closes_contender_mutex_handle(self):
        kernel32 = _FakeKernel32()
        fake_windll = SimpleNamespace(kernel32=kernel32)
        with patch.object(radio_main.sys, "platform", "win32"), patch.object(
            radio_main.ctypes, "windll", fake_windll,
        ), patch.object(radio_main.time, "sleep"):
            acquired = radio_main._acquire_single_instance_lock(timeout=1, poll=0)
            self.assertTrue(acquired)
            self.assertEqual(kernel32.closed, [101])
            self.assertEqual(radio_main._SINGLE_INSTANCE_HANDLE, 202)
            radio_main._release_single_instance_lock()
            self.assertEqual(kernel32.closed, [101, 202])

    def test_restart_uses_out_of_process_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / "backend" / "restart_helper.py"
            helper.parent.mkdir(parents=True)
            helper.write_text("# helper", encoding="utf-8")
            api = RadioAPI(root)
            try:
                with patch("backend.api.subprocess.Popen") as popen:
                    api._launch_restart_helper()
                command = popen.call_args.args[0]
                self.assertIn(str(helper), command)
                self.assertIn("--pid", command)
                self.assertIn("--root", command)
                self.assertEqual(popen.call_args.kwargs["cwd"], str(root))
            finally:
                api.shutdown()


if __name__ == "__main__":
    unittest.main()
