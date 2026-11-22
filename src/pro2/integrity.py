"""Experimental integrity: pinned evaluator, reproducible lineage, invariants.

Science/engineering separation and Problem State ↔ experiment ↔ Git snapshot
invariants enforced by the engine: every experiment records the git snapshot
it started from, and the evaluator (verify spec + tests) is pinned at
initialization so a candidate cannot drift it mid-run.
"""

from __future__ import annotations

from pro2.config import Objective
from pro2.git_sandbox import GitSandbox
from pro2.util import sha256_text


def pin_evaluator(objective: Objective) -> str:
    """Digest of the verification contract (verify spec + tests) as configured."""
    payload = {
        "verify": objective.verify.to_dict(),
        "tests": [spec.to_dict() for spec in objective.tests],
    }
    return sha256_text(repr(sorted(payload["verify"].items()))) + sha256_text(
        repr(sorted((t["command"], t["timeout_seconds"]) for t in payload["tests"]))
    )


def evaluator_mismatch(objective: Objective, pinned: str) -> str | None:
    """Return an error message when the evaluator changed since pinning."""
    digest = pin_evaluator(objective)
    if digest != pinned:
        return (
            "verification evaluator changed during the run "
            f"(pinned={pinned[:12]}, current={digest[:12]}); refusing to continue"
        )
    return None


def snapshot_digest(commit: str) -> str:
    """Identity of the git snapshot an experiment started from."""
    return sha256_text(commit)


def assert_snapshot_integrity(sandbox: GitSandbox, commit: str) -> str:
    """Fail closed when the base commit is not a resolvable git object."""
    if not sandbox.commit_exists(commit):
        raise RuntimeError(f"experiment base commit does not exist in the repository: {commit}")
    return snapshot_digest(commit)


def lineage(base_commit: str, best_commit: str | None) -> list[str]:
    """Reproducible lineage: base snapshot, then the best-known reference."""
    chain = [base_commit]
    if best_commit and best_commit != base_commit:
        chain.append(best_commit)
    return chain


__all__ = [
    "assert_snapshot_integrity",
    "evaluator_mismatch",
    "lineage",
    "pin_evaluator",
    "snapshot_digest",
]
