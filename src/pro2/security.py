"""Agent and experiment isolation: filesystem, network, credential, subprocess policy.

Fail-safe design: when isolation is enabled and no backend is available,
agent execution raises IsolationError instead of running unprotected.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pro2.util import sha256_text

SECRET_ENV_PATTERN = re.compile(
    r"(SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|API_KEY|PRIVATE|AUTH|SESSION|COOKIE|"
    r"SSH|AWS_|ANTHROPIC|OPENAI_|GITHUB_TOKEN|KEY)",
    re.IGNORECASE,
)

CREDENTIAL_DIRS = ("ssh", "aws", "gnupg", "kube", "netrc", "docker", "gcloud", "azure")


class IsolationError(RuntimeError):
    """Isolation required but unavailable; execution is refused (fail-closed)."""


@dataclass
class SecurityConfig:
    """Policy for isolating agents and experiment commands."""

    enabled: bool = False
    backend: str = "auto"  # auto | unshare | docker | bwrap | nsjail | none
    network: str = "deny"  # deny | allow
    credential_protection: bool = True

    def __post_init__(self) -> None:
        if self.backend not in {"auto", "unshare", "docker", "bwrap", "nsjail", "none"}:
            raise ValueError(f"unknown isolation backend: {self.backend}")
        if self.network not in {"deny", "allow"}:
            raise ValueError("network must be 'deny' or 'allow'")
        if not self.enabled and self.backend not in {"auto", "none"}:
            raise ValueError("backend is only meaningful when isolation is enabled")

    def to_dict(self) -> dict[str, object]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SecurityConfig:
        return cls(
            enabled=bool(data.get("enabled", False)),
            backend=str(data.get("backend", "auto")),
            network=str(data.get("network", "deny")),
            credential_protection=bool(data.get("credential_protection", True)),
        )


def _probe(command: list[str]) -> bool:
    return subprocess.run(command, capture_output=True).returncode == 0


def detect_backends() -> dict[str, bool]:
    """Probe isolation backends; returns a name -> available mapping."""
    available = {
        tool: bool(shutil.which(tool)) for tool in ("unshare", "bwrap", "nsjail", "docker")
    }
    if available["unshare"]:
        available["unshare"] = _probe(["unshare", "-rm", "true"])
        available["unshare_net"] = _probe(["unshare", "-n", "true"])
    if available["docker"]:
        available["docker"] = _probe(["docker", "info"])
    return available


def select_backend(config: SecurityConfig) -> str | None:
    """Choose an available backend for the config; None when none is usable."""
    if not config.enabled:
        return None
    if config.backend not in {"auto", "none"}:
        return config.backend if detect_backends().get(config.backend) else None
    available = detect_backends()
    candidates = ["unshare", "docker", "bwrap", "nsjail"]
    if config.network == "deny" and not available.get("unshare_net"):
        candidates = ["docker", "bwrap", "nsjail", "unshare"]
    for name in candidates:
        if available.get(name):
            return name
    return None


def credential_paths(home: Path | None = None) -> list[str]:
    """Paths that hold credentials and must never be writable or visible to agents."""
    home = home or Path.home()
    paths = [str(home / f".{name}") for name in CREDENTIAL_DIRS] + [str(home / ".netrc")]
    return [p for p in paths if os.path.exists(p)]


def scrub_environment(env: dict[str, str] | None) -> dict[str, str]:
    """Drop secret-looking environment variables from a copy of env."""
    return {k: v for k, v in (env or {}).items() if not SECRET_ENV_PATTERN.search(k)}


def _build_unshare_wrapper(command: list[str], readonly_dirs: list[str]) -> list[str]:
    body = ["set -e"]
    for path in readonly_dirs:
        body.append(f'mount --bind "{path}" "{path}" 2>/dev/null')
        body.append(f'mount -o remount,bind,ro "{path}" 2>/dev/null')
    body.append('exec "$@"')
    return ["unshare", "-r", "-m", "sh", "-c", "\n".join(body), "sh", *command]


def _build_docker_wrapper(command: list[str], readonly_dirs: list[str]) -> list[str]:
    run = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "65534",
        "--cap-drop",
        "ALL",
        "--read-only",
    ]
    for path in readonly_dirs:
        run.extend(["-v", f"{path}:{path}:ro"])
    run.extend(["alpine:latest", "sh", "-c", 'exec "$@"', "sh", *command])
    return run


def enforce_isolation(
    config: SecurityConfig,
    command: list[str],
    cwd: str | Path,
    readonly_dirs: list[str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Wrap a command so it runs inside an isolation boundary.

    Returns (wrapped_command, scrubbed_env). Raises IsolationError when
    isolation is enabled but no usable backend is available (fail-closed).
    """
    if not config.enabled:
        return list(command), scrub_environment(os.environ)
    backend = select_backend(config)
    if backend is None:
        raise IsolationError(
            "isolation is enabled but no backend is available "
            f"(probed: {sorted(detect_backends())}); install unshare/bwrap/nsjail/docker "
            "or set security.enabled=false"
        )
    if config.network == "deny" and backend == "unshare" and not detect_backends()["unshare_net"]:
        raise IsolationError("isolation network=deny requires `unshare -n`, unavailable")

    readonly = list(readonly_dirs or [])
    cwd_path = Path(cwd).resolve()
    if str(cwd_path) not in readonly:
        readonly.append(str(cwd_path))

    if backend == "unshare":
        wrapped = _build_unshare_wrapper(command, readonly)
    elif backend == "docker":
        wrapped = _build_docker_wrapper(command, readonly)
    elif backend == "bwrap":
        wrapped = [
            "bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--ro-bind",
            "/",
            "/",
            *(item for path in readonly for item in ("--ro-bind", path, path)),
            "--",
            *command,
        ]
    elif backend == "nsjail":
        wrapped = ["nsjail", "--", *command]
    else:  # none
        wrapped = list(command)
    return wrapped, scrub_environment(os.environ)


def isolation_digest(config: SecurityConfig | None) -> str:
    """Stable digest of the isolation policy, stored in state for audit."""
    return sha256_text(repr(config.to_dict() if config else {"enabled": False}))


__all__ = [
    "CREDENTIAL_DIRS",
    "IsolationError",
    "SECRET_ENV_PATTERN",
    "SecurityConfig",
    "credential_paths",
    "detect_backends",
    "enforce_isolation",
    "isolation_digest",
    "scrub_environment",
    "select_backend",
]
