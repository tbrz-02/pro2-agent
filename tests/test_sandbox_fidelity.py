"""Isolation wrappers: filesystem/network/credential/resource isolation fidelity."""

import shutil
import subprocess

import pytest

from pro2.runner import run_command
from pro2.security import (
    SecurityConfig,
    _build_bwrap_wrapper,
    _build_docker_wrapper,
    _build_nsjail_wrapper,
    _build_unshare_wrapper,
    enforce_isolation,
    select_backend,
)

UNSHARE_AVAILABLE = shutil.which("unshare") is not None


def _unshare_wrapper(network="deny"):
    return _build_unshare_wrapper(["echo", "hi"], "/tmp", ["/extra/ro"], network == "deny")


def test_unshare_wrapper_adds_net_namespace_when_denied():
    wrapped = _unshare_wrapper("deny")
    assert "-n" in wrapped


def test_unshare_wrapper_omits_net_when_allowed():
    wrapped = _unshare_wrapper("allow")
    assert "-n" not in wrapped


def test_unshare_wrapper_binds_readonly_dirs_and_execs():
    wrapped = _unshare_wrapper("deny")
    body = " ".join(wrapped)
    assert "mount --bind" in body
    assert 'mount -o remount,bind,ro "/extra/ro"' in body
    assert 'exec "$@"' in body
    assert wrapped[0] == "unshare"


def _docker_mount_values(wrapped):
    return [wrapped[i + 1] for i, a in enumerate(wrapped) if a == "-v" and i + 1 < len(wrapped)]


def test_docker_wrapper_is_full_container_boundary():
    wrapped = _build_docker_wrapper(["echo", "hi"], "/tmp", ["/extra/ro"])
    assert wrapped[0] == "docker" and wrapped[1] == "run"
    assert "--network" in wrapped and "none" in wrapped
    assert "--read-only" in wrapped
    assert "--pids-limit" in wrapped
    assert "--cap-drop" in wrapped
    assert "--user" in wrapped
    # The experiment worktree is the writable mount; other paths are read-only.
    mounts = _docker_mount_values(wrapped)
    assert "/tmp:/tmp" in mounts
    assert "/extra/ro:/extra/ro:ro" in mounts


def test_docker_wrapper_worktree_mount_is_writable():
    wrapped = _build_docker_wrapper(["echo", "hi"], "/tmp", [])
    mounts = _docker_mount_values(wrapped)
    assert "/tmp:/tmp" in mounts
    assert "/tmp:/tmp:ro" not in mounts


def test_bwrap_wrapper_keeps_root_ro_and_worktree_writable():
    wrapped = _build_bwrap_wrapper(["echo", "hi"], "/tmp", [], network_deny=True)
    assert "--ro-bind" in wrapped
    assert "--bind" in wrapped
    assert "/tmp" in wrapped
    assert "--unshare-net" in wrapped
    assert wrapped[-1] == "hi"


def test_nsjail_wrapper_runs_isolated_as_unprivileged_user():
    assert _build_nsjail_wrapper(["echo", "hi"]) == [
        "nsjail",
        "--user",
        "65534",
        "--",
        "echo",
        "hi",
    ]


def test_select_backend_none_is_explicit_opt_out():
    assert select_backend(SecurityConfig(enabled=True, backend="none")) is None


def test_enforce_isolation_normalizes_string_commands():
    # A string command stays one shell string (no argv split); the engine's
    # string commands run through the shell as before.
    wrapped, _ = enforce_isolation(SecurityConfig(enabled=True, backend="none"), "echo hi", "/tmp")
    assert wrapped == ["echo hi"]


@pytest.mark.skipif(not UNSHARE_AVAILABLE, reason="unshare unavailable in this environment")
def test_run_command_smoke_with_unshare(tmp_path):
    result = run_command(
        "echo",
        cwd=tmp_path,
        timeout_seconds=10,
        isolation=SecurityConfig(enabled=True, backend="unshare", network="deny"),
    )
    assert result.ok, result.stderr


@pytest.mark.skipif(not UNSHARE_AVAILABLE, reason="unshare unavailable in this environment")
def test_enforce_isolation_runs_wrapped_command(tmp_path):
    wrapped, _ = enforce_isolation(
        SecurityConfig(enabled=True, backend="unshare", network="deny"),
        ["echo", "wrapped-ok"],
        tmp_path,
    )
    result = subprocess.run(wrapped, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert "wrapped-ok" in result.stdout
