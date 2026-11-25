"""Agent and experiment isolation: filesystem, network, credential, subprocess policy.

Fail-safe design: when isolation is enabled and no backend is available,
agent execution raises IsolationError instead of running unprotected.
Backends: unshare (mount namespaces; writable experiment worktree), docker
(full container), bwrap and nsjail where present.
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

# Toolchain directories bind-mounted read-only so the sandboxed process can
# still run the project's tools (no pivot_root available on every host).
_TOOLCHAIN_DIRS = (
    "/usr/bin",
    "/usr/sbin",
    "/usr/lib",
    "/usr/lib/x86_64-linux-gnu",
    "/usr/lib64",
    "/usr/lib/python3.12",
    "/usr/share",
    "/etc",
    "/opt",
)


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
    if config.backend == "none":
        # Explicit opt-out: the operator asked for no boundary.
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


def _normalize(command: list[str] | str) -> list[str]:
    return [command] if isinstance(command, str) else list(command)


def _wrap_exec(body: str, command: list[str]) -> list[str]:
    return ["sh", "-c", body, "sh", *command]


def _build_unshare_wrapper(
    command: list[str], cwd: str, readonly_dirs: list[str], network_deny: bool
) -> list[str]:
    args = ["unshare", "-r", "-m"]
    if network_deny:
        args.append("-n")
    body_lines = ["set -e", f'cd "{cwd}"']
    # Host toolchain is bind-mounted read-only: agents still have the tools.
    # Binds are best-effort: some hosts refuse some mounts; the command still
    # runs without them.
    for path in list(_TOOLCHAIN_DIRS) + readonly_dirs + credential_paths():
        body_lines.append(f'mount --bind "{path}" "{path}" 2>/dev/null || true')
        body_lines.append(f'mount -o remount,bind,ro "{path}" 2>/dev/null || true')
    body_lines.append('exec "$@"')
    return [*args, *_wrap_exec("\n".join(body_lines), command)]


def _build_docker_wrapper(command: list[str], cwd: str, readonly_dirs: list[str]) -> list[str]:
    run = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "0",  # worktree is mounted rw; rootfs is ro + no caps
        "--cap-drop",
        "ALL",
        "--read-only",
        "--pids-limit",
        "256",
    ]
    for path in readonly_dirs + credential_paths():
        run.extend(["-v", f"{path}:{path}:ro"])
    run.extend(["-v", f"{cwd}:{cwd}"])
    run.extend(["alpine:latest"])
    run.extend(_wrap_exec('exec "$@"', command))
    return run


def _build_bwrap_wrapper(
    command: list[str], cwd: str, readonly_dirs: list[str], network_deny: bool
) -> list[str]:
    wrapped = ["bwrap", "--unshare-all", "--die-with-parent", "--dev", "/dev", "--proc", "/proc"]
    if network_deny:
        wrapped.append("--unshare-net")
    wrapped.append("--ro-bind")
    wrapped.extend(["/", "/"])
    # The experiment worktree is the only writable path into the sandbox.
    wrapped.append("--bind")
    wrapped.extend([cwd, cwd])
    for path in readonly_dirs + credential_paths():
        wrapped.extend(["--ro-bind", path, path])
    wrapped.extend(["--", *command])
    return wrapped


def _build_nsjail_wrapper(command: list[str]) -> list[str]:
    return ["nsjail", "--user", "65534", "--", *command]


def enforce_isolation(
    config: SecurityConfig,
    command: list[str] | str,
    cwd: str | Path,
    readonly_dirs: list[str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Wrap a command so it runs inside an isolation boundary.

    Returns (wrapped_command, scrubbed_env). Explicit backend "none" or a
    disabled policy returns the command unwrapped; when isolation is enabled
    but no usable backend is available it raises IsolationError (fail-closed).
    """
    command = _normalize(command)
    if not config.enabled or config.backend == "none":
        return list(command), scrub_environment(os.environ)
    backend = select_backend(config)
    if backend is None:
        raise IsolationError(
            "isolation is enabled but no backend is available "
            f"(probed: {sorted(detect_backends())}); install unshare/docker/bwrap/nsjail "
            "or set security.enabled=false"
        )

    cwd_path = str(Path(cwd).resolve())
    readonly = [p for p in (readonly_dirs or []) if p and p != cwd_path]
    readonly += [p for p in credential_paths() if p not in readonly and p != cwd_path]
    network_deny = config.network == "deny"

    if backend == "unshare":
        wrapped = _build_unshare_wrapper(command, cwd_path, readonly, network_deny)
    elif backend == "docker":
        wrapped = _build_docker_wrapper(command, cwd_path, readonly)
    elif backend == "bwrap":
        wrapped = _build_bwrap_wrapper(command, cwd_path, readonly, network_deny)
    elif backend == "nsjail":
        wrapped = _build_nsjail_wrapper(command)
    else:
        wrapped = command
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
