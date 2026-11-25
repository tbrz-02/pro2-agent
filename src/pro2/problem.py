"""Problem model for individual performance bottlenecks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pro2.candidate_solution import CandidateSolution
from pro2.util import utc_now

# Pro² Three-State Problem taxonomy: every problem moves through exactly one
# of these states, with evidence-backed transitions recorded in the journal.
PROBLEM_STATUSES = ("unresolved", "resource_limited", "resolved")


def _migrate_status(status: Any) -> str:
    """Map legacy/unknown states onto the three-state taxonomy."""
    if status in PROBLEM_STATUSES:
        return status
    # Legacy pre-taxonomy status used for "still being worked on".
    return "unresolved"


@dataclass
class Problem:
    """Represents an identified scientific or performance problem."""

    problem_id: str
    description: str
    profiling_evidence: list[str] = field(default_factory=list)
    candidate_solutions: list[CandidateSolution] = field(default_factory=list)
    severity: float = 0.5
    evidence_ids: list[str] = field(default_factory=list)
    attempts: int = 0
    status: str = "unresolved"
    metadata: dict[str, Any] = field(default_factory=dict)
    # Three-state transition log (evidence-backed; appended by set_status).
    status_reasons: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 0.0 <= self.severity <= 1.0:
            raise ValueError("severity must be between 0.0 and 1.0")
        if self.attempts < 0:
            raise ValueError("attempts must be >= 0")
        if self.status not in PROBLEM_STATUSES:
            raise ValueError(f"unknown problem status: {self.status!r}")

    def set_status(self, status: str, reason: str, evidence: str = "") -> bool:
        """Move the problem through the three-state taxonomy.

        Evidence-backed transitions: `resolved` is terminal; `resource_limited`
        means execution stopped because of resource budgets rather than
        scientific outcome. Returns True when the state changed.
        """
        if status not in PROBLEM_STATUSES:
            raise ValueError(f"unknown problem status: {status!r}")
        if status == self.status:
            return False
        if self.status == "resolved":
            # `resolved` is terminal: no transition moves out of it.
            return False
        if status == "resolved" and self.status not in {"unresolved", "resource_limited"}:
            return False
        self.status_reasons.append(
            {
                "from": self.status,
                "to": status,
                "reason": reason or f"moved to {status}",
                "evidence": evidence,
                "at": utc_now(),
            }
        )
        self.status = status
        return True

    def has_resolved_candidate(self) -> bool:
        return any(c.metadata.get("resolved") for c in self.candidate_solutions)

    def add_candidate(self, candidate: CandidateSolution) -> None:
        """Attach a candidate solution, rejecting duplicate IDs."""
        if any(
            existing.candidate_id == candidate.candidate_id for existing in self.candidate_solutions
        ):
            raise ValueError(
                f"Candidate {candidate.candidate_id!r} already exists "
                f"in problem {self.problem_id!r}"
            )

        self.candidate_solutions.append(candidate)

    def get_candidate(self, candidate_id: str) -> CandidateSolution | None:
        return next(
            (c for c in self.candidate_solutions if c.candidate_id == candidate_id),
            None,
        )

    def record_attempt(self) -> int:
        self.attempts += 1
        return self.attempts

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "description": self.description,
            "profiling_evidence": list(self.profiling_evidence),
            "candidate_solutions": [c.to_dict() for c in self.candidate_solutions],
            "severity": self.severity,
            "evidence_ids": list(self.evidence_ids),
            "attempts": self.attempts,
            "status": self.status,
            "metadata": dict(self.metadata),
            "status_reasons": [dict(r) for r in self.status_reasons],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Problem:
        return cls(
            problem_id=str(data["problem_id"]),
            description=str(data.get("description", "")),
            profiling_evidence=list(data.get("profiling_evidence", [])),
            candidate_solutions=[
                CandidateSolution.from_dict(c) for c in data.get("candidate_solutions", [])
            ],
            severity=float(data.get("severity", 0.5)),
            evidence_ids=list(data.get("evidence_ids", [])),
            attempts=int(data.get("attempts", 0)),
            status=_migrate_status(data.get("status", "unresolved")),
            metadata=dict(data.get("metadata", {})),
            status_reasons=[dict(r) for r in data.get("status_reasons", [])],
        )
