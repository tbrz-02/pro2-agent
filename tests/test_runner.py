import sys
import time

from pro2.runner import run_command


def test_run_command_captures_output(tmp_path):
    result = run_command(
        [sys.executable, "-c", "print('hello'); print('oops', file=__import__('sys').stderr)"],
        cwd=tmp_path,
        timeout_seconds=10,
        artifacts_dir=tmp_path / "art",
        label="capture",
    )

    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert "oops" in result.stderr
    assert result.timed_out is False
    assert result.stdout_path.exists()
    assert result.stderr_path.exists()


def test_run_command_times_out(tmp_path):
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        timeout_seconds=1,
        artifacts_dir=tmp_path / "art",
        label="timeout",
    )

    assert result.timed_out is True
    assert result.exit_code != 0
    assert result.duration_seconds >= 1


def test_run_command_detects_divergence(tmp_path):
    result = run_command(
        [sys.executable, "-c", "import time; print('loss=nan'); time.sleep(30)"],
        cwd=tmp_path,
        timeout_seconds=10,
        artifacts_dir=tmp_path / "art",
        label="diverge",
        divergence_regex=r"(?i)\bnan\b",
    )

    assert result.diverged is True
    assert result.timed_out is False


def test_run_command_enforces_memory_limit(tmp_path):
    result = run_command(
        [
            sys.executable,
            "-c",
            "x = bytearray(300 * 1024 * 1024); print(len(x))",
        ],
        cwd=tmp_path,
        timeout_seconds=20,
        artifacts_dir=tmp_path / "art",
        label="mem",
        memory_limit_mb=128,
    )

    assert result.exit_code != 0
    assert result.memory_exceeded is True or result.exit_code != 0


def test_run_command_reports_missing_working_directory(tmp_path):
    result = run_command(
        [sys.executable, "-c", "print(1)"],
        cwd=tmp_path / "missing",
        timeout_seconds=5,
        artifacts_dir=tmp_path / "art",
        label="missing",
    )

    assert result.exit_code != 0
    assert result.error


def test_run_command_duration_is_measured(tmp_path):
    start = time.monotonic()
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(0.2)"],
        cwd=tmp_path,
        timeout_seconds=10,
        artifacts_dir=tmp_path / "art",
        label="duration",
    )
    elapsed = time.monotonic() - start

    assert 0.15 <= result.duration_seconds <= elapsed + 0.5
