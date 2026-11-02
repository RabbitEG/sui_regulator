from __future__ import annotations

import errno
import fcntl
import os
import signal
import subprocess
import sys
from contextlib import AbstractContextManager
from pathlib import Path


def identity(pid: int) -> dict | None:
    """Linux process identity; zombie/unknown processes are never considered live."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat[stat.rfind(")") + 2 :].split()
        if fields[0] in {"Z", "X"}:
            return None
        return {
            "pid": pid,
            "start_ticks": int(fields[19]),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        }
    except (OSError, ValueError, IndexError):
        return None


def alive(value: dict | None) -> bool:
    return bool(value and identity(value["pid"]) == value)


def signal_owned(value: dict | None, sig: int = signal.SIGTERM) -> bool:
    if not value or not alive(value) or value["pid"] in {1, os.getpid()}:
        return False
    fd = None
    try:
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            try:
                fd = os.pidfd_open(value["pid"])
            except OSError as exc:
                if exc.errno not in {errno.ENOSYS, errno.EINVAL}:
                    return False
            if fd is not None:
                if not alive(value):
                    return False
                signal.pidfd_send_signal(fd, sig)
                return True
        # Older kernels have no pidfd. Exact boot/PID/start check is best effort;
        # it cannot eliminate the final check-to-signal PID reuse race.
        if alive(value):
            os.kill(value["pid"], sig)
            return True
    except (ProcessLookupError, PermissionError):
        return False
    finally:
        if fd is not None:
            os.close(fd)
    return False


class FileLock(AbstractContextManager):
    def __init__(self, path: Path):
        self.path, self.fd = path, None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def child_environment() -> dict[str, str]:
    env = os.environ.copy()
    source = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def detached(
    module: str, args: list[str], *, env: dict | None = None, input_data: str | None = None
) -> subprocess.Popen:
    process = subprocess.Popen(
        [sys.executable, "-m", module, *args],
        stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=env or child_environment(),
    )
    if input_data is not None:
        assert process.stdin is not None
        try:
            process.stdin.write(input_data.encode())
            process.stdin.close()
        except BrokenPipeError:
            process.wait(timeout=5)
            raise
    return process
