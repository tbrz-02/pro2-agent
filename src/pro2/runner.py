"""Bounded subprocess execution with timeouts, memory limits, stall and divergence detection."""

from __future__ import annotations

import math
import os
import re
import resource
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pro2.security import SecurityConfig, enforce_isolation, scrub_environment
from pro2.util import truncate


@dataclass
class RunResult:
    command: str
    cwd: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    memory_exceeded: bool = False
    diverged: bool = False
    stalled: bool = False
    killed_reason: str = ""
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    peak_rss_bytes: int = 0
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not (
            self.timed_out or self.memory_exceeded or self.diverged or self.stalled or self.error
        )


class _Tail:
    def __init__(self, limit: int) -> None:
        self.limit = max(1024, limit)
        self._chunks: deque[str] = deque()
        self._size = 0
        self._lock = threading.Lock()
        self.total = 0

    def append(self, text: str) -> None:
        with self._lock:
            self._chunks.append(text)
            self._size += len(text)
            self.total += len(text)
            while self._size > self.limit and len(self._chunks) > 1:
                removed = self._chunks.popleft()
                self._size -= len(removed)

    def text(self) -> str:
        with self._lock:
            value = "".join(self._chunks)
        return truncate(value, self.limit)


def _reader(stream: Any, tail: _Tail, path: Path) -> None:
    try:
        with path.open("w", encoding="utf-8", errors="replace") as handle:
            while True:
                chunk = stream.readline()
                if not chunk:
                    break
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", errors="replace")
                handle.write(chunk)
                handle.flush()
                tail.append(chunk)
    except (OSError, ValueError):
        return
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _group_rss_bytes(pgid: int) -> int:
    total = 0
    page_size = os.sysconf("SC_PAGE_SIZE")
    try:
        entries = os.listdir("/proc")
    except OSError:
        return 0
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            raw = Path(f"/proc/{entry}/stat").read_text(encoding="utf-8", errors="replace")
            rest = raw.rsplit(")", 1)[1].split()
            if int(rest[2]) != pgid:
                continue
            total += int(rest[21]) * page_size
        except (OSError, IndexError, ValueError):
            continue
    return total


def _kill_group(pid: int) -> None:
    # Graceful-then-forced: TERM the whole group, SIGKILL it if it survives.
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.killpg(os.getpgid(pid), 0)
    except (ProcessLookupError, PermissionError, OSError):
        return
    time.sleep(0.5)
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _preexec(memory_limit_mb: int, cpu_seconds: float) -> Any:
    def set_limits() -> None:
        if memory_limit_mb > 0:
            limit = int(memory_limit_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        if cpu_seconds > 0:
            cpu = max(1, math.ceil(cpu_seconds))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))

    return set_limits


def run_command(
    command: str | list[str],
    cwd: str | Path,
    timeout_seconds: float = 300.0,
    artifacts_dir: str | Path | None = None,
    label: str = "run",
    env: dict[str, str] | None = None,
    memory_limit_mb: int = 0,
    max_output_bytes: int = 2 * 1024 * 1024,
    stall_seconds: float = 0.0,
    divergence_regex: str | None = None,
    stdin_text: str | None = None,
    isolation: SecurityConfig | None = None,
) -> RunResult:
    """Execute a command in its own process group and enforce resource guards.

    When isolation is enabled, the command is wrapped in the selected backend
    and the environment is scrubbed of secret-looking variables. Raises
    IsolationError if isolation is requested but no backend is available.
    """
    cwd_path = Path(cwd)
    command_text = command if isinstance(command, str) else " ".join(command)
    artifact_root = Path(artifacts_dir) if artifacts_dir else Path.cwd() / ".pro2" / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label)[:80] or "run"
    stdout_path = artifact_root / f"{safe_label}.stdout.log"
    stderr_path = artifact_root / f"{safe_label}.stderr.log"

    if not cwd_path.is_dir():
        return RunResult(
            command=command_text,
            cwd=str(cwd_path),
            exit_code=127,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            error=f"working directory does not exist: {cwd_path}",
        )

    if isolation is not None and isolation.enabled:
        command, _scrub_env = enforce_isolation(isolation, command, cwd_path)
        merged_env = scrub_environment(os.environ)
    else:
        merged_env = os.environ.copy()
    merged_env.setdefault("PYTHONUNBUFFERED", "1")
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})

    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd_path),
            shell=isinstance(command, str),
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=merged_env,
            start_new_session=True,
            preexec_fn=_preexec(memory_limit_mb, timeout_seconds),
        )
    except (OSError, ValueError) as exc:
        return RunResult(
            command=command_text,
            cwd=str(cwd_path),
            exit_code=127,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            error=f"failed to start command: {exc}",
        )

    if stdin_text is not None and process.stdin is not None:
        try:
            process.stdin.write(stdin_text)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    stdout_tail = _Tail(max_output_bytes)
    stderr_tail = _Tail(max_output_bytes)
    threads = [
        threading.Thread(
            target=_reader, args=(process.stdout, stdout_tail, stdout_path), daemon=True
        ),
        threading.Thread(
            target=_reader, args=(process.stderr, stderr_tail, stderr_path), daemon=True
        ),
    ]
    for thread in threads:
        thread.start()

    pattern = re.compile(divergence_regex) if divergence_regex else None
    pgid = process.pid
    peak_rss = 0
    last_output = stdout_tail.total + stderr_tail.total
    last_change = started
    timed_out = memory_exceeded = diverged = stalled = False
    killed_reason = ""

    while process.poll() is None:
        now = time.monotonic()
        elapsed = now - started
        peak_rss = max(peak_rss, _group_rss_bytes(pgid))

        if elapsed > timeout_seconds:
            timed_out = True
            killed_reason = "timeout"
            _kill_group(process.pid)
            break

        if memory_limit_mb > 0 and peak_rss > memory_limit_mb * 1024 * 1024:
            memory_exceeded = True
            killed_reason = "memory_limit"
            _kill_group(process.pid)
            break

        if pattern is not None:
            combined = stdout_tail.text() + stderr_tail.text()
            if pattern.search(combined):
                diverged = True
                killed_reason = "divergence"
                _kill_group(process.pid)
                break

        current_output = stdout_tail.total + stderr_tail.total
        if current_output != last_output:
            last_output = current_output
            last_change = now
        elif stall_seconds > 0 and (now - last_change) > stall_seconds:
            stalled = True
            killed_reason = "stalled_output"
            _kill_group(process.pid)
            break

        time.sleep(0.1)

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _kill_group(process.pid)
        process.wait(timeout=10)

    for thread in threads:
        thread.join(timeout=2)

    duration = time.monotonic() - started
    stderr_text = stderr_tail.text()
    if memory_limit_mb > 0 and "MemoryError" in stderr_text:
        memory_exceeded = True

    return RunResult(
        command=command_text,
        cwd=str(cwd_path),
        exit_code=process.returncode if process.returncode is not None else -9,
        stdout=stdout_tail.text(),
        stderr=stderr_text,
        duration_seconds=duration,
        timed_out=timed_out,
        memory_exceeded=memory_exceeded,
        diverged=diverged,
        stalled=stalled,
        killed_reason=killed_reason,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        peak_rss_bytes=peak_rss,
    )
