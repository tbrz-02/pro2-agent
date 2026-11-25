"""Persistent engine state, event journal, and exclusive run lock."""

from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pro2.hypothesis import Hypothesis
from pro2.problem_state import ProblemState
from pro2.records import Experiment
from pro2.util import (
    append_jsonl,
    atomic_write_text,
    read_json,
    read_jsonl,
    utc_now,
    write_json,
)


class StateLockedError(RuntimeError):
    """Raised when another engine process holds the state lock."""


@dataclass
class EngineState(ProblemState):
    """Problem State plus the operational state needed for resumable autonomy."""

    objective_name: str = ""
    status: str = "initialized"
    phase: str = "init"
    baseline_metric: float | None = None
    baseline_commit: str | None = None
    best_metric: float | None = None
    best_commit: str | None = None
    hypotheses: list[Hypothesis] = field(default_factory=list)
    experiments: list[Experiment] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    research_findings: list[dict[str, Any]] = field(default_factory=list)
    engineering_trials: list[dict[str, Any]] = field(default_factory=list)
    engineering: dict[str, Any] = field(default_factory=dict)
    budget_used: dict[str, float] = field(
        default_factory=lambda: {"experiments": 0, "wall_seconds": 0.0, "cost_usd": 0.0}
    )
    consecutive_failures: int = 0
    in_flight: dict[str, Any] | None = None
    insights: list[str] = field(default_factory=list)
    # Review/PI governance records (review_governance.py): escalations and
    # reviewer availability counts — fail-closed state for ambiguous decisions.
    escalations: list[dict[str, Any]] = field(default_factory=list)
    reviewer_unavailable: int = 0
    pi_malformed: int = 0
    pi_unavailable: int = 0
    stop_reason: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    run_started_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_hypothesis(self, hypothesis: Hypothesis) -> None:
        if any(h.hypothesis_id == hypothesis.hypothesis_id for h in self.hypotheses):
            raise ValueError(f"Hypothesis {hypothesis.hypothesis_id!r} already exists")
        self.hypotheses.append(hypothesis)

    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        return next((h for h in self.hypotheses if h.hypothesis_id == hypothesis_id), None)

    def pending_hypotheses(self) -> list[Hypothesis]:
        return [h for h in self.hypotheses if h.status == "pending"]

    def add_experiment(self, experiment: Experiment) -> None:
        self.experiments.append(experiment)

    def latest_experiment(self) -> Experiment | None:
        return self.experiments[-1] if self.experiments else None

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data.update(
            {
                "objective_name": self.objective_name,
                "status": self.status,
                "phase": self.phase,
                "baseline_metric": self.baseline_metric,
                "baseline_commit": self.baseline_commit,
                "best_metric": self.best_metric,
                "best_commit": self.best_commit,
                "hypotheses": [h.to_dict() for h in self.hypotheses],
                "experiments": [e.to_dict() for e in self.experiments],
                "evidence": list(self.evidence),
                "research_findings": list(self.research_findings),
                "engineering_trials": list(self.engineering_trials),
                "engineering": dict(self.engineering),
                "budget_used": dict(self.budget_used),
                "consecutive_failures": self.consecutive_failures,
                "in_flight": self.in_flight,
                "insights": list(self.insights),
                "escalations": [dict(e) for e in self.escalations],
                "reviewer_unavailable": self.reviewer_unavailable,
                "pi_malformed": self.pi_malformed,
                "pi_unavailable": self.pi_unavailable,
                "stop_reason": self.stop_reason,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "run_started_at": self.run_started_at,
                "metadata": dict(self.metadata),
            }
        )
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineState:
        base = ProblemState.from_dict(data)
        state = cls(
            problems=base.problems,
            baseline_id=base.baseline_id,
            iteration=base.iteration,
            identified_problems=base.identified_problems,
            objective_name=str(data.get("objective_name", "")),
            status=str(data.get("status", "initialized")),
            phase=str(data.get("phase", "init")),
            baseline_metric=data.get("baseline_metric"),
            baseline_commit=data.get("baseline_commit"),
            best_metric=data.get("best_metric"),
            best_commit=data.get("best_commit"),
            hypotheses=[Hypothesis.from_dict(h) for h in data.get("hypotheses", [])],
            experiments=[Experiment.from_dict(e) for e in data.get("experiments", [])],
            evidence=list(data.get("evidence", [])),
            research_findings=list(data.get("research_findings", [])),
            engineering_trials=list(data.get("engineering_trials", [])),
            engineering=dict(data.get("engineering", {})),
            budget_used=dict(
                data.get("budget_used", {"experiments": 0, "wall_seconds": 0.0, "cost_usd": 0.0})
            ),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
            in_flight=data.get("in_flight"),
            insights=list(data.get("insights", [])),
            escalations=[dict(e) for e in data.get("escalations", [])],
            reviewer_unavailable=int(data.get("reviewer_unavailable", 0)),
            pi_malformed=int(data.get("pi_malformed", 0)),
            pi_unavailable=int(data.get("pi_unavailable", 0)),
            stop_reason=str(data.get("stop_reason", "")),
            created_at=str(data.get("created_at", utc_now())),
            updated_at=str(data.get("updated_at", utc_now())),
            run_started_at=data.get("run_started_at"),
            metadata=dict(data.get("metadata", {})),
        )
        return state


class StateStore:
    """Filesystem-backed state with atomic writes, a journal, and a run lock."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.state_file = self.root / "state.json"
        self.backup_file = self.root / "state.json.bak"
        self.events_file = self.root / "events.jsonl"
        self.experience_file = self.root / "experience.jsonl"
        self.worktrees_dir = self.root / "worktrees"
        self.verify_dir = self.root / "verify"
        self.artifacts_dir = self.root / "artifacts"
        self.research_dir = self.root / "research"
        self.lock_file = self.root / "engine.lock"
        self.pause_file = self.root / "PAUSE"
        self._lock_handle: Any = None

    def ensure(self) -> None:
        for directory in (
            self.root,
            self.worktrees_dir,
            self.verify_dir,
            self.artifacts_dir,
            self.research_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def save_state(self, state: EngineState) -> None:
        self.ensure()
        state.updated_at = utc_now()
        payload = state.to_dict()
        if self.state_file.exists():
            shutil.copy2(self.state_file, self.backup_file)
        write_json(self.state_file, payload)
        # Always keep a backup copy: if state.json is corrupted, the engine
        # can fall back to the most recent known-good state.
        if not self.backup_file.exists():
            write_json(self.backup_file, payload)

    def load_state(self) -> EngineState:
        for path in (self.state_file, self.backup_file):
            if not path.exists():
                continue
            try:
                data = read_json(path)
            except (ValueError, OSError):
                continue
            if isinstance(data, dict):
                return EngineState.from_dict(data)
        raise FileNotFoundError(f"No readable engine state in {self.root}")

    def events(self) -> list[dict[str, Any]]:
        """All journaled events, oldest first (audit trail)."""
        return read_jsonl(self.events_file)

    def append_event(self, event_type: str, **fields: Any) -> None:
        self.ensure()
        record = {"type": event_type, "timestamp": utc_now(), **fields}
        append_jsonl(self.events_file, record)

    def artifact_path(self, *parts: str) -> Path:
        path = self.artifacts_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_artifact(self, relative_path: str, text: str) -> Path:
        path = self.artifact_path(relative_path)
        atomic_write_text(path, text)
        return path

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.ensure()
        handle = self.lock_file.open("a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StateLockedError(
                    f"engine state is locked by another process: {self.lock_file}"
                ) from exc
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n")
            handle.flush()
            self._lock_handle = handle
            yield
        finally:
            if self._lock_handle is handle:
                self._lock_handle = None
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def request_pause(self) -> None:
        self.ensure()
        self.pause_file.write_text(utc_now() + "\n", encoding="utf-8")

    def clear_pause(self) -> None:
        self.pause_file.unlink(missing_ok=True)

    def pause_requested(self) -> bool:
        return self.pause_file.exists()

    def disk_usage_bytes(self) -> int:
        """Total size of engine-owned state (worktrees, verify, artifacts, research)."""
        total = 0
        for directory in (
            self.root,
            self.worktrees_dir,
            self.verify_dir,
            self.artifacts_dir,
            self.research_dir,
        ):
            if not directory.is_dir():
                continue
            for path in directory.rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
        return total


__all__ = ["EngineState", "StateStore", "StateLockedError"]
