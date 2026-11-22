"""Candidate solution model for scientific hypothesis exploration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CandidateSolution:
    """Represents a candidate solution and its current belief."""

    candidate_id: str
    description: str
    belief: float
    evidence: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    rationale: str = ""
    patch: str | None = None
    instructions: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.belief <= 1.0:
            raise ValueError("belief must be between 0.0 and 1.0")

    def adjust_belief(self, delta: float) -> float:
        """Apply a bounded belief update and return the new belief."""
        self.belief = max(0.0, min(1.0, self.belief + delta))
        return self.belief

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "description": self.description,
            "belief": self.belief,
            "evidence": list(self.evidence),
            "references": list(self.references),
            "rationale": self.rationale,
            "patch": self.patch,
            "instructions": self.instructions,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateSolution:
        return cls(
            candidate_id=str(data["candidate_id"]),
            description=str(data.get("description", "")),
            belief=float(data.get("belief", 0.5)),
            evidence=list(data.get("evidence", [])),
            references=list(data.get("references", [])),
            rationale=str(data.get("rationale", "")),
            patch=data.get("patch"),
            instructions=str(data.get("instructions", "")),
            metadata=dict(data.get("metadata", {})),
        )
