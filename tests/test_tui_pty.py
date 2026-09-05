from __future__ import annotations

import fcntl
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).parents[1]
BACKEND = ROOT / "tests/fixtures/tui_backend.py"


class TuiPtyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="paddock-tui-pty-")
        cls.binary = Path(cls.temporary.name) / "paddock-tui"
        environment = os.environ.copy()
        environment.setdefault("GOCACHE", "/tmp/paddock-go-test-cache")
        subprocess.run(
            ["go", "build", "-o", cls.binary, "./cmd/paddock-tui"],
            cwd=ROOT, env=environment, check=True, capture_output=True, text=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def start_tui(self) -> tuple[subprocess.Popen[bytes], int]:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios_winsize(), struct.pack("HHHH", 24, 100, 0, 0))
        environment = os.environ.copy()
        environment.update({
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "PADDOCK_PYTHON": str(BACKEND),
        })
        process = subprocess.Popen(
            [self.binary], stdin=slave, stdout=slave, stderr=slave,
            env=environment, close_fds=True, start_new_session=True,
        )
        os.close(slave)
        self.addCleanup(self.stop_tui, process, master)
        return process, master

    @staticmethod
    def stop_tui(process: subprocess.Popen[bytes], master: int) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        try:
            os.close(master)
        except OSError:
            pass

    def read_until(self, process, master: int, expected: bytes, timeout: float = 4) -> bytes:
        output = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            ready, _, _ = select.select([master], [], [], 0.1)
            if not ready:
                continue
            try:
                output.extend(os.read(master, 65536))
            except OSError:
                break
            if expected in output:
                return bytes(output)
        self.fail(f"TUI output never contained {expected!r}; got {bytes(output)!r}")

    def test_keyboard_navigates_from_sites_table_to_details_and_back(self) -> None:
        process, master = self.start_tui()
        self.read_until(process, master, b"Stop All")
        os.write(master, b"\t")
        self.read_until(process, master, b"HTTP(S)")
        os.write(master, b"\r")
        self.read_until(process, master, b"/srv/linguine")
        os.write(master, b"\x1b")
        self.read_until(process, master, b"Search")
        os.write(master, b"q")
        process.wait(timeout=2)
        self.assertEqual(0, process.returncode)

    def test_dashboard_operation_emits_spinner_and_completion_toast(self) -> None:
        process, master = self.start_tui()
        self.read_until(process, master, b"Stop All")
        os.write(master, b" ")
        self.read_until(process, master, b"Stopping All")
        self.read_until(process, master, b"Stopped all configured services")
        os.write(master, b"q")
        process.wait(timeout=2)
        self.assertEqual(0, process.returncode)


def termios_winsize() -> int:
    # Linux TIOCSWINSZ; kept local so this suite needs only the Python stdlib.
    return 0x5414


if __name__ == "__main__":
    unittest.main()
