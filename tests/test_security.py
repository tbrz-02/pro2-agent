"""Security hardening: isolation detection, scrubbing, fail-closed policy."""

import pytest

from pro2.security import (
    IsolationError,
    SecurityConfig,
    credential_paths,
    detect_backends,
    enforce_isolation,
    isolation_digest,
    scrub_environment,
    select_backend,
)


def test_detect_backends_shape():
    backends = detect_backends()
    assert isinstance(backends, dict)
    assert set(backends) >= {"unshare", "bwrap", "nsjail", "docker"}


def test_security_config_validation():
    with pytest.raises(ValueError):
        SecurityConfig(enabled=True, backend="bwrap-not-a-thing")
    with pytest.raises(ValueError):
        SecurityConfig(enabled=True, network="maybe")
    with pytest.raises(ValueError):
        SecurityConfig(enabled=False, backend="unshare")


def test_scrub_environment_drops_secret_looking_vars():
    env = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "sk-test",
        "ANTHROPIC_API_KEY": "sk-test",
        "GITHUB_TOKEN": "ghp_test",
        "AWS_SECRET_ACCESS_KEY": "shhh",
        "PRO2_TASK": "keep-me",
    }
    scrubbed = scrub_environment(env)
    assert "PATH" in scrubbed
    assert "PRO2_TASK" in scrubbed
    assert not any(
        key in scrubbed
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY")
    )


def test_credential_paths_only_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    assert str(ssh) in credential_paths()
    assert str(tmp_path / ".aws") not in credential_paths()


def test_enforce_isolation_disabled_returns_command_unwrapped():
    command, _ = enforce_isolation(SecurityConfig(enabled=False), ["echo", "hi"], "/tmp")
    assert command == ["echo", "hi"]


def test_enforce_isolation_fails_closed_when_no_backend(monkeypatch):
    monkeypatch.setattr(
        "pro2.security.detect_backends",
        lambda: {k: False for k in ("unshare", "docker", "bwrap", "nsjail", "unshare_net")},
    )
    config = SecurityConfig(enabled=True, backend="auto")
    with pytest.raises(IsolationError):
        enforce_isolation(config, ["echo", "hi"], "/tmp")


def test_enforce_isolation_backend_none_is_opt_out():
    # Explicit "none" backend: no boundary, but never an error.
    config = SecurityConfig(enabled=True, backend="none")
    command, _ = enforce_isolation(config, ["echo", "hi"], "/tmp")
    assert command == ["echo", "hi"]


def test_enforce_isolation_unshare_wrap_when_available():
    if not detect_backends().get("unshare"):
        pytest.skip("unshare backend unavailable in this environment")
    config = SecurityConfig(enabled=True, backend="auto", network="allow")
    wrapped, _ = enforce_isolation(config, ["echo", "hi"], "/tmp")
    assert wrapped[0] == "unshare"


def test_isolation_digest_stable_and_changes_with_policy():
    assert isolation_digest(SecurityConfig()) == isolation_digest(SecurityConfig())
    assert isolation_digest(SecurityConfig(enabled=True)) != isolation_digest(
        SecurityConfig(enabled=False)
    )
    assert isolation_digest(None)


def test_select_backend_none_when_disabled():
    assert select_backend(SecurityConfig(enabled=False)) is None
