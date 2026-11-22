"""Scientific hypothesis representation and priority selection."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from pro2.util import utc_now


@dataclass
class Hypothesis:
    """Represents a proposed modification to address a specific problem.

    Paper mapping: H_t = (P_k, C_{k,m}, Δm_t).
    """

    problem_id: str
    candidate_id: str
    modification: str
    hypothesis_id: str = ""
    title: str = ""
    target_delta: float | None = None
    instructions: str = ""
    status: str = "pending"
    created_at: str = field(default_factory=utc_now)
    attempts: int = 0
    review_feedback: str = ""
    experiment_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Engineering extensions (DERIVED/ENGINEERING): belief-adjacent confidence
    # used for adaptive rescheduling, and the falsification bookkeeping.
    confidence: float = 0.5
    edges: list[dict[str, str]] = field(default_factory=list)  # graph links
    falsified: bool = False
    falsification_reason: str = ""

    def __post_init__(self) -> None:
        if not self.hypothesis_id:
            self.hypothesis_id = f"H-{uuid.uuid4().hex[:10]}"
        if not self.title:
            self.title = self.modification[:100]
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        if self.target_delta is not None and self.target_delta < 0:
            raise ValueError("target_delta must be >= 0")
        if self.status not in {
            "pending",
            "running",
            "accepted",
            "rejected",
            "skipped",
            "interrupted",
            "falsified",
        }:
            raise ValueError(f"unknown hypothesis status: {self.status}")

    def add_edge(self, parent_id: str, relation: str = "supports") -> None:
        """Record a graph edge: supports | contradicts | derived_from."""
        if relation not in {"supports", "contradicts", "derived_from"}:
            raise ValueError(f"unknown relation: {relation}")
        if not parent_id:
            return
        if any(edge["parent_id"] == parent_id for edge in self.edges):
            return
        self.edges.append({"parent_id": parent_id, "relation": relation})

    def contradicts(self) -> list[str]:
        """Hypotheses this one contradicts — used for falsification bookkeeping."""
        return [edge["parent_id"] for edge in self.edges if edge["relation"] == "contradicts"]

    def adjust_confidence(self, delta: float) -> float:
        """Bounded confidence update used by adaptive rescheduling."""
        self.confidence = max(0.0, min(1.0, self.confidence + delta))
        return self.confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "candidate_id": self.candidate_id,
            "modification": self.modification,
            "hypothesis_id": self.hypothesis_id,
            "title": self.title,
            "target_delta": self.target_delta,
            "instructions": self.instructions,
            "status": self.status,
            "created_at": self.created_at,
            "attempts": self.attempts,
            "review_feedback": self.review_feedback,
            "experiment_id": self.experiment_id,
            "metadata": dict(self.metadata),
            "confidence": self.confidence,
            "edges": [dict(edge) for edge in self.edges],
            "falsified": self.falsified,
            "falsification_reason": self.falsification_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Hypothesis:
        return cls(
            problem_id=str(data["problem_id"]),
            candidate_id=str(data["candidate_id"]),
            modification=str(data.get("modification", "")),
            hypothesis_id=str(data.get("hypothesis_id", "")),
            title=str(data.get("title", "")),
            target_delta=(
                None if data.get("target_delta") is None else float(data["target_delta"])
            ),
            instructions=str(data.get("instructions", "")),
            status=str(data.get("status", "pending")),
            created_at=str(data.get("created_at", utc_now())),
            attempts=int(data.get("attempts", 0)),
            review_feedback=str(data.get("review_feedback", "")),
            experiment_id=data.get("experiment_id"),
            metadata=dict(data.get("metadata", {})),
            confidence=float(data.get("confidence", 0.5)),
            edges=[dict(e) for e in data.get("edges", [])],
            falsified=bool(data.get("falsified", False)),
            falsification_reason=str(data.get("falsification_reason", "")),
        )


def hypothesis_priority(belief: float, attempts: int) -> float:
    """Paper Eq. 2: Pri(H_t) = b_{k,m} / (N(P_k) + 1)."""
    if not 0.0 <= belief <= 1.0:
        raise ValueError("belief must be between 0.0 and 1.0")
    if attempts < 0:
        raise ValueError("attempts must be >= 0")
    return belief / (attempts + 1)


def adaptive_reschedule(state: Any) -> int:
    """DERIVED mechanism: boost attempts on problems with repeated falsification.

    Under-attempted problems rise in priority under the paper's
    b/(N+1) formula, so their hypotheses get rescheduled automatically.
    Returns the number of problems boosted.
    """
    falsified: dict[str, int] = {}
    for hypothesis in state.hypotheses:
        if hypothesis.status == "falsified":
            falsified[hypothesis.problem_id] = falsified.get(hypothesis.problem_id, 0) + 1
    boosted = 0
    for problem in state.identified_problems:
        boost = falsified.get(problem.problem_id, 0)
        if boost >= 2:
            problem.attempts += boost
            boosted += 1
    return boosted


def select_hypothesis(
    queue: Iterable[Hypothesis],
    beliefs: dict[str, float],
    attempts: dict[str, int],
) -> Hypothesis | None:
    """Select the highest-priority pending hypothesis.

    Tie-break is deterministic and independent of input ordering: higher
    hypothesis id wins. This keeps replays reproducible after state reload.
    """
    pending = [h for h in queue if h.status == "pending"]
    if not pending:
        return None

    def key(h: Hypothesis) -> tuple[float, str]:
        belief = beliefs.get(h.candidate_id, 0.0)
        return (hypothesis_priority(belief, attempts.get(h.problem_id, 0)), h.hypothesis_id)

    return max(pending, key=key)
