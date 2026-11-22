"""Objective configuration: measurable goals, commands, budgets and policies."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pro2.security import SecurityConfig
from pro2.util import deep_get


@dataclass
class MetricSpec:
    """A command whose output contains one scalar metric."""

    command: str
    metric_regex: str | None = None
    json_path: str | None = None
    timeout_seconds: float = 300.0
    repeat: int = 1
    env: dict[str, str] = field(default_factory=dict)
    divergence_regex: str | None = None
    memory_limit_mb: int = 0

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise ValueError("metric command must not be empty")
        if not self.metric_regex and not self.json_path:
            raise ValueError("metric_regex or json_path is required to extract a metric")
        if self.metric_regex:
            try:
                re.compile(self.metric_regex)
            except re.error as exc:
                raise ValueError(f"invalid metric_regex: {exc}") from exc
        if self.repeat < 1:
            raise ValueError("repeat must be >= 1")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self.divergence_regex:
            try:
                re.compile(self.divergence_regex)
            except re.error as exc:
                raise ValueError(f"invalid divergence_regex: {exc}") from exc
        if self.memory_limit_mb < 0:
            raise ValueError("memory_limit_mb must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "metric_regex": self.metric_regex,
            "json_path": self.json_path,
            "timeout_seconds": self.timeout_seconds,
            "repeat": self.repeat,
            "env": dict(self.env),
            "divergence_regex": self.divergence_regex,
            "memory_limit_mb": self.memory_limit_mb,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetricSpec:
        return cls(
            command=str(data.get("command", "")),
            metric_regex=data.get("metric_regex"),
            json_path=data.get("json_path"),
            timeout_seconds=float(data.get("timeout_seconds", 300)),
            repeat=int(data.get("repeat", 1)),
            env={str(k): str(v) for k, v in (data.get("env") or {}).items()},
            divergence_regex=data.get("divergence_regex"),
            memory_limit_mb=int(data.get("memory_limit_mb", 0)),
        )


@dataclass
class CommandSpec:
    command: str
    timeout_seconds: float = 300.0
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise ValueError("command must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "timeout_seconds": self.timeout_seconds,
            "env": dict(self.env),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandSpec:
        return cls(
            command=str(data.get("command", "")),
            timeout_seconds=float(data.get("timeout_seconds", 300)),
            env={str(k): str(v) for k, v in (data.get("env") or {}).items()},
        )


@dataclass
class SuccessCriteria:
    metric: str
    direction: str = "maximize"
    min_delta: float = 0.0
    target: float | None = None

    def __post_init__(self) -> None:
        if not self.metric.strip():
            raise ValueError("success metric name is required")
        if self.direction not in {"maximize", "minimize"}:
            raise ValueError("direction must be 'maximize' or 'minimize'")
        if self.min_delta < 0:
            raise ValueError("min_delta must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "direction": self.direction,
            "min_delta": self.min_delta,
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SuccessCriteria:
        return cls(
            metric=str(data.get("metric", "score")),
            direction=str(data.get("direction", "maximize")),
            min_delta=float(data.get("min_delta", 0.0)),
            target=None if data.get("target") is None else float(data["target"]),
        )


@dataclass
class Budget:
    max_iterations: int = 10
    max_experiments: int = 5
    max_wall_seconds: float = 3600.0
    max_experiment_seconds: float = 600.0
    max_cost_usd: float = 0.0
    max_consecutive_failures: int = 3
    max_disk_bytes: int = 0  # 0 = unlimited

    def __post_init__(self) -> None:
        if self.max_iterations < 0 or self.max_experiments < 0:
            raise ValueError("budgets must be >= 0")
        if self.max_wall_seconds <= 0 or self.max_experiment_seconds <= 0:
            raise ValueError("time budgets must be > 0")
        if self.max_disk_bytes < 0:
            raise ValueError("max_disk_bytes must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Budget:
        return cls(
            max_iterations=int(data.get("max_iterations", 10)),
            max_experiments=int(data.get("max_experiments", 5)),
            max_wall_seconds=float(data.get("max_wall_seconds", 3600)),
            max_experiment_seconds=float(data.get("max_experiment_seconds", 600)),
            max_cost_usd=float(data.get("max_cost_usd", 0.0)),
            max_consecutive_failures=int(data.get("max_consecutive_failures", 3)),
            max_disk_bytes=int(data.get("max_disk_bytes", 0)),
        )


@dataclass
class Constraints:
    protected_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=lambda: [".git/**", ".pro2/**"])
    max_changed_files: int = 3
    max_diff_lines: int = 200
    require_clean_worktree: bool = True
    allow_test_edits: bool = False
    allow_network: bool = True

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Constraints:
        return cls(
            protected_paths=list(data.get("protected_paths", [])),
            forbidden_paths=list(data.get("forbidden_paths", [".git/**", ".pro2/**"])),
            max_changed_files=int(data.get("max_changed_files", 3)),
            max_diff_lines=int(data.get("max_diff_lines", 200)),
            require_clean_worktree=bool(data.get("require_clean_worktree", True)),
            allow_test_edits=bool(data.get("allow_test_edits", False)),
            allow_network=bool(data.get("allow_network", True)),
        )


@dataclass
class AgentConfig:
    provider: str = "auto"
    model: str = ""
    command: list[str] = field(default_factory=list)
    timeout_seconds: float = 600.0
    max_retries: int = 1
    base_url: str = ""
    api_key_env: str = ""
    cost_per_1k_tokens: float = 0.0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("agent timeout_seconds must be > 0")

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentConfig:
        return cls(
            provider=str(data.get("provider", "auto")),
            model=str(data.get("model", "")),
            command=[str(x) for x in data.get("command", [])],
            timeout_seconds=float(data.get("timeout_seconds", 600)),
            max_retries=int(data.get("max_retries", 1)),
            base_url=str(data.get("base_url", "")),
            api_key_env=str(data.get("api_key_env", "")),
            cost_per_1k_tokens=float(data.get("cost_per_1k_tokens", 0.0)),
        )


@dataclass
class ResearchConfig:
    enabled: bool = True
    providers: list[str] = field(default_factory=lambda: ["github", "arxiv"])
    max_queries: int = 3
    max_results_per_query: int = 5
    timeout_seconds: float = 20.0
    # Scientific search hardening: refetch every finding and confirm against
    # the primary source (Parser success != verification).
    verify_primary: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.verify_primary, bool):
            raise ValueError("verify_primary must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchConfig:
        return cls(
            enabled=bool(data.get("enabled", True)),
            providers=list(data.get("providers", ["github", "arxiv"])),
            max_queries=int(data.get("max_queries", 3)),
            max_results_per_query=int(data.get("max_results_per_query", 5)),
            timeout_seconds=float(data.get("timeout_seconds", 20)),
            verify_primary=bool(data.get("verify_primary", True)),
        )


@dataclass
class EngineeringParam:
    path: str
    key: str
    values: list[Any] = field(default_factory=list)
    adapter: str = "json"

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError("engineering parameter needs at least one value")
        if self.adapter not in {"json", "env", "text"}:
            raise ValueError("engineering adapter must be json, env or text")
        if self.adapter in {"json", "text"} and not self.path:
            raise ValueError("engineering json/text parameter requires a path")

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineeringParam:
        return cls(
            path=str(data.get("path", "")),
            key=str(data.get("key", "")),
            values=list(data.get("values", [])),
            adapter=str(data.get("adapter", "json")),
        )


@dataclass
class EngineeringConfig:
    enabled: bool = False
    gamma: float = 0.02
    max_nodes: int = 8
    exploration_constant: float = 1.414
    params: list[EngineeringParam] = field(default_factory=list)
    protected_paths: list[str] = field(default_factory=list)
    direction: str = (
        "maximize"  # ENGINEERING: added — objective direction for best-candidate selection
    )

    def __post_init__(self) -> None:
        if self.direction not in {"maximize", "minimize"}:
            raise ValueError("direction must be 'maximize' or 'minimize'")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "gamma": self.gamma,
            "max_nodes": self.max_nodes,
            "exploration_constant": self.exploration_constant,
            "params": [p.to_dict() for p in self.params],
            "protected_paths": list(self.protected_paths),
            "direction": self.direction,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineeringConfig:
        return cls(
            enabled=bool(data.get("enabled", False)),
            gamma=float(data.get("gamma", 0.02)),
            max_nodes=int(data.get("max_nodes", 8)),
            exploration_constant=float(data.get("exploration_constant", 1.414)),
            params=[EngineeringParam.from_dict(p) for p in data.get("params", [])],
            protected_paths=list(data.get("protected_paths", [])),
            direction=str(data.get("direction", "maximize")),
        )


@dataclass
class ExperienceConfig:
    enabled: bool = True
    max_entries: int = 500
    retrieval_limit: int = 3

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperienceConfig:
        return cls(
            enabled=bool(data.get("enabled", True)),
            max_entries=int(data.get("max_entries", 500)),
            retrieval_limit=int(data.get("retrieval_limit", 3)),
        )


@dataclass
class Objective:
    name: str
    description: str
    repo: Path
    success: SuccessCriteria
    baseline: MetricSpec
    verify: MetricSpec
    diagnostics: list[CommandSpec] = field(default_factory=list)
    tests: list[CommandSpec] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    constraints: Constraints = field(default_factory=Constraints)
    agent: AgentConfig = field(default_factory=AgentConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    engineering: EngineeringConfig = field(default_factory=EngineeringConfig)
    experience: ExperienceConfig = field(default_factory=ExperienceConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    state_dir: str = ".pro2"
    supervision_interval: int = 10
    seed: int = 0
    seed_problems: list[dict[str, Any]] = field(default_factory=list)
    seed_hypotheses: list[dict[str, Any]] = field(default_factory=list)
    report_path: str = "report.md"
    belief_positive_delta: float = 0.1
    belief_negative_delta: float = 0.1

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("objective name is required")
        if not self.repo.exists():
            raise ValueError(f"repository does not exist: {self.repo}")
        if self.supervision_interval < 1:
            raise ValueError("supervision_interval must be >= 1")

    @property
    def state_path(self) -> Path:
        return self.repo / self.state_dir

    @property
    def report_file(self) -> Path:
        return self.state_path / self.report_path

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "repo": str(self.repo),
            "success": self.success.to_dict(),
            "baseline": self.baseline.to_dict(),
            "verify": self.verify.to_dict(),
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "tests": [t.to_dict() for t in self.tests],
            "budget": self.budget.to_dict(),
            "constraints": self.constraints.to_dict(),
            "agent": self.agent.to_dict(),
            "research": self.research.to_dict(),
            "engineering": self.engineering.to_dict(),
            "experience": self.experience.to_dict(),
            "security": self.security.to_dict(),
            "state_dir": self.state_dir,
            "supervision_interval": self.supervision_interval,
            "seed": self.seed,
            "seed_problems": list(self.seed_problems),
            "seed_hypotheses": list(self.seed_hypotheses),
            "report_path": self.report_path,
            "belief_positive_delta": self.belief_positive_delta,
            "belief_negative_delta": self.belief_negative_delta,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], base_dir: Path | None = None) -> Objective:
        repo_value = Path(str(data.get("repo", ".")))
        if not repo_value.is_absolute():
            repo_value = (base_dir or Path.cwd()) / repo_value
        repo = repo_value.resolve()

        verify = MetricSpec.from_dict(data.get("verify") or {})
        baseline_data = data.get("baseline")
        baseline = MetricSpec.from_dict(baseline_data) if baseline_data else verify

        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            repo=repo,
            success=SuccessCriteria.from_dict(data.get("success") or {}),
            baseline=baseline,
            verify=verify,
            diagnostics=[CommandSpec.from_dict(x) for x in data.get("diagnostics", [])],
            tests=[CommandSpec.from_dict(x) for x in data.get("tests", [])],
            budget=Budget.from_dict(data.get("budget") or {}),
            constraints=Constraints.from_dict(data.get("constraints") or {}),
            agent=AgentConfig.from_dict(data.get("agent") or {}),
            research=ResearchConfig.from_dict(data.get("research") or {}),
            engineering=EngineeringConfig.from_dict(data.get("engineering") or {}),
            experience=ExperienceConfig.from_dict(data.get("experience") or {}),
            security=SecurityConfig.from_dict(data.get("security") or {}),
            state_dir=str(data.get("state_dir", ".pro2")),
            supervision_interval=int(data.get("supervision_interval", 10)),
            seed=int(data.get("seed", 0)),
            seed_problems=list(data.get("seed_problems", [])),
            seed_hypotheses=list(data.get("seed_hypotheses", [])),
            report_path=str(data.get("report_path", "report.md")),
            belief_positive_delta=float(data.get("belief_positive_delta", 0.1)),
            belief_negative_delta=float(data.get("belief_negative_delta", 0.1)),
        )


def load_objective(path: str | Path) -> Objective:
    config_path = Path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("objective config must be a JSON object")
    return Objective.from_dict(data, base_dir=config_path.parent)


def extract_metric(
    output: str,
    metric_regex: str | None = None,
    json_path: str | None = None,
) -> float | None:
    """Extract a finite scalar from command output.

    JSON paths are tried first; regex uses the first capture group when present.
    """
    if json_path:
        candidates = [output.strip()]
        candidates.extend(reversed([line.strip() for line in output.splitlines() if line.strip()]))
        for candidate in candidates:
            try:
                value = deep_get(json.loads(candidate), json_path)
            except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
                continue
            number = _finite_float(value)
            if number is not None:
                return number

    if metric_regex:
        match = re.search(metric_regex, output)
        if match:
            raw = match.group(1) if match.groups() else match.group(0)
            return _finite_float(raw)
    return None


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
