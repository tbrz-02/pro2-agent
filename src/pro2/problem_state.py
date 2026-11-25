"""Core Problem State model for Pro² Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pro2.candidate_solution import CandidateSolution
from pro2.hypothesis import Hypothesis
from pro2.problem import Problem


@dataclass
class ProblemState:
    """Represents the evolving state of scientific problem exploration."""

    problems: list[str] = field(default_factory=list)
    baseline_id: str | None = None
    iteration: int = 0
    identified_problems: list[Problem] = field(default_factory=list)

    def add_problem(self, problem: Problem) -> None:
        """Register a problem, rejecting duplicate problem IDs."""
        if any(existing.problem_id == problem.problem_id for existing in self.identified_problems):
            raise ValueError(f"Problem {problem.problem_id!r} already exists in state")

        self.identified_problems.append(problem)

    def get_problem(self, problem_id: str) -> Problem | None:
        """Retrieve a problem by its unique ID."""
        return next(
            (problem for problem in self.identified_problems if problem.problem_id == problem_id),
            None,
        )

    def get_candidate(self, problem_id: str, candidate_id: str) -> CandidateSolution | None:
        problem = self.get_problem(problem_id)
        return problem.get_candidate(candidate_id) if problem else None

    def validate_hypothesis(self, hypothesis: Hypothesis) -> bool:
        """Validate that a hypothesis targets an existing problem and candidate."""
        problem = self.get_problem(hypothesis.problem_id)
        if problem is None:
            raise ValueError(f"Unknown problem: {hypothesis.problem_id}")
        if problem.get_candidate(hypothesis.candidate_id) is None:
            raise ValueError(
                f"Unknown candidate {hypothesis.candidate_id!r} for problem "
                f"{hypothesis.problem_id!r}"
            )
        return True

    def record_attempt(self, problem_id: str) -> int:
        problem = self.get_problem(problem_id)
        if problem is None:
            raise ValueError(f"Unknown problem: {problem_id}")
        return problem.record_attempt()

    def set_problem_status(self, problem_id: str, status: str, reason: str, evidence: str = ""):
        """Move a problem through the three-state taxonomy; returns the Problem."""
        problem = self.get_problem(problem_id)
        if problem is None:
            raise ValueError(f"Unknown problem: {problem_id}")
        problem.set_status(status, reason, evidence)
        return problem

    def problem_transitions(self) -> list[dict[str, Any]]:
        """Every recorded three-state transition, oldest first."""
        transitions: list[dict[str, Any]] = []
        for problem in self.identified_problems:
            transitions.extend(
                {
                    "problem_id": problem.problem_id,
                    **transition,
                }
                for transition in problem.status_reasons
            )
        return transitions

    def unresolved_problems(self) -> list[Problem]:
        return [p for p in self.identified_problems if p.status == "unresolved"]

    def candidate_beliefs(self) -> dict[str, float]:
        return {
            candidate.candidate_id: candidate.belief
            for problem in self.identified_problems
            for candidate in problem.candidate_solutions
        }

    def problem_attempts(self) -> dict[str, int]:
        return {problem.problem_id: problem.attempts for problem in self.identified_problems}

    def to_dict(self) -> dict[str, Any]:
        return {
            "problems": list(self.problems),
            "baseline_id": self.baseline_id,
            "iteration": self.iteration,
            "identified_problems": [p.to_dict() for p in self.identified_problems],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProblemState:
        return cls(
            problems=list(data.get("problems", [])),
            baseline_id=data.get("baseline_id"),
            iteration=int(data.get("iteration", 0)),
            identified_problems=[Problem.from_dict(p) for p in data.get("identified_problems", [])],
        )
