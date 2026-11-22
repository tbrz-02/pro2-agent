"""Serializable records shared by the engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pro2.util import utc_now


@dataclass
class Evidence:
    evidence_id: str
    kind: str
    source: str
    summary: str
    content_path: str = ""
    content_hash: str = ""
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Evidence:
        return cls(
            evidence_id=str(data["evidence_id"]),
            kind=str(data.get("kind", "")),
            source=str(data.get("source", "")),
            summary=str(data.get("summary", "")),
            content_path=str(data.get("content_path", "")),
            content_hash=str(data.get("content_hash", "")),
            created_at=str(data.get("created_at", utc_now())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class Experiment:
    experiment_id: str
    hypothesis_id: str
    problem_id: str
    candidate_id: str
    branch: str = ""
    worktree_path: str = ""
    base_commit: str = ""
    commit: str | None = None
    status: str = "running"
    started_at: str = field(default_factory=utc_now)
    ended_at: str | None = None
    duration_seconds: float = 0.0
    baseline_metric: float | None = None
    candidate_metric: float | None = None
    delta: float | None = None
    accepted: bool = False
    reason: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    diff: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    agent: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    # Reproducible lineage: snapshots the experiment built on and the best
    # reference at experiment start (integrity.py).
    lineage: list[str] = field(default_factory=list)
    snapshot_digest: str = ""
    # Review governance records (review_governance.py).
    reviewer_id: str = ""
    reviewer_source: str = ""
    review_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Experiment:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class EngineeringTrial:
    trial_id: str
    node_id: str
    parent_id: str | None
    config: dict[str, Any]
    base_commit: str = ""
    commit: str | None = None
    metric: float | None = None
    delta: float | None = None
    duration_seconds: float = 0.0
    reward: float | None = None
    accepted: bool = False
    status: str = "running"
    reason: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now)
    ended_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineeringTrial:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})
