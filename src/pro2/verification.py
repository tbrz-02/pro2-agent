"""Independent verification: clean checkout, tests, metric measurement, comparison."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pro2.config import MetricSpec, Objective, extract_metric
from pro2.git_sandbox import GitSandbox
from pro2.runner import RunResult, run_command


@dataclass
class MetricMeasurement:
    value: float | None
    runs: list[RunResult] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    error: str | None = None

    @property
    def run(self) -> RunResult | None:
        return self.runs[-1] if self.runs else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "values": list(self.values),
            "error": self.error,
            "runs": [
                {
                    "command": r.command,
                    "exit_code": r.exit_code,
                    "duration_seconds": r.duration_seconds,
                    "timed_out": r.timed_out,
                    "diverged": r.diverged,
                    "memory_exceeded": r.memory_exceeded,
                    "stdout_path": str(r.stdout_path) if r.stdout_path else "",
                    "stderr_path": str(r.stderr_path) if r.stderr_path else "",
                }
                for r in self.runs
            ],
        }


@dataclass
class VerificationResult:
    accepted: bool
    baseline_metric: float
    candidate_metric: float | None
    delta: float
    reason: str
    measurement: MetricMeasurement
    tests: list[RunResult] = field(default_factory=list)
    tests_failed: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "baseline_metric": self.baseline_metric,
            "candidate_metric": self.candidate_metric,
            "delta": self.delta,
            "reason": self.reason,
            "measurement": self.measurement.to_dict(),
            "tests_failed": self.tests_failed,
            "error": self.error,
            "tests": [
                {
                    "command": r.command,
                    "exit_code": r.exit_code,
                    "duration_seconds": r.duration_seconds,
                    "timed_out": r.timed_out,
                    "stdout_path": str(r.stdout_path) if r.stdout_path else "",
                    "stderr_path": str(r.stderr_path) if r.stderr_path else "",
                }
                for r in self.tests
            ],
        }


def measure_metric(
    spec: MetricSpec,
    cwd: str | Path,
    artifacts_dir: str | Path,
    label: str,
    env: dict[str, str] | None = None,
    memory_limit_mb: int = 0,
) -> MetricMeasurement:
    runs: list[RunResult] = []
    values: list[float] = []
    merged_env = dict(spec.env)
    if env:
        merged_env.update(env)

    for index in range(spec.repeat):
        run = run_command(
            spec.command,
            cwd=cwd,
            timeout_seconds=spec.timeout_seconds,
            artifacts_dir=artifacts_dir,
            label=f"{label}-{index + 1}",
            env=merged_env,
            memory_limit_mb=memory_limit_mb,
        )
        runs.append(run)
        value = extract_metric(
            run.stdout + "\n" + run.stderr,
            metric_regex=spec.metric_regex,
            json_path=spec.json_path,
        )
        if value is not None:
            values.append(value)

    if not values:
        last = runs[-1] if runs else None
        detail = ""
        if last is not None:
            if last.error:
                detail = last.error
            elif last.timed_out:
                detail = "command timed out"
            elif last.exit_code != 0:
                detail = f"command exited with {last.exit_code}"
        return MetricMeasurement(
            value=None,
            runs=runs,
            error=f"could not extract finite metric: {detail}".strip(),
        )

    return MetricMeasurement(value=statistics.median(values), runs=runs, values=values)


def compare_metrics(
    baseline: float,
    candidate: float,
    direction: str,
    min_delta: float,
    target: float | None = None,
) -> tuple[bool, float, str]:
    if direction == "maximize":
        delta = candidate - baseline
        target_ok = target is None or candidate >= target
    else:
        delta = baseline - candidate
        target_ok = target is None or candidate <= target

    accepted = delta >= min_delta and target_ok
    if accepted:
        reason = f"improved by {delta:.6g} (baseline={baseline:.6g}, candidate={candidate:.6g})"
    elif not target_ok:
        reason = f"candidate {candidate:.6g} did not reach target {target:.6g}; delta={delta:.6g}"
    else:
        reason = (
            f"insufficient improvement: delta={delta:.6g} < required {min_delta:.6g} "
            f"(baseline={baseline:.6g}, candidate={candidate:.6g})"
        )
    return accepted, delta, reason


def verify_commit(
    sandbox: GitSandbox,
    objective: Objective,
    commit: str,
    baseline_metric: float,
    experiment_id: str,
    artifacts_dir: str | Path,
    min_delta: float | None = None,
    target: float | None = None,
    env: dict[str, str] | None = None,
) -> VerificationResult:
    """Verify a commit in a fresh worktree, never in the implementer's sandbox."""
    verify_path: Path | None = None
    try:
        verify_path = sandbox.create_verification_worktree(commit, experiment_id)
        tests: list[RunResult] = []
        for index, spec in enumerate(objective.tests):
            test_run = run_command(
                spec.command,
                cwd=verify_path,
                timeout_seconds=spec.timeout_seconds,
                artifacts_dir=artifacts_dir,
                label=f"{experiment_id}-test-{index + 1}",
                env={**spec.env, **(env or {})},
            )
            tests.append(test_run)
            if not test_run.ok:
                measurement = MetricMeasurement(value=None, runs=tests, error="tests failed")
                return VerificationResult(
                    accepted=False,
                    baseline_metric=baseline_metric,
                    candidate_metric=None,
                    delta=0.0,
                    reason=f"verification tests failed: {test_run.command}",
                    measurement=measurement,
                    tests=tests,
                    tests_failed=True,
                )

        measurement = measure_metric(
            objective.verify,
            verify_path,
            artifacts_dir,
            f"{experiment_id}-verify",
            env=env,
        )
        if measurement.value is None:
            return VerificationResult(
                accepted=False,
                baseline_metric=baseline_metric,
                candidate_metric=None,
                delta=0.0,
                reason=measurement.error or "verification metric unavailable",
                measurement=measurement,
                tests=tests,
                error=measurement.error,
            )

        accepted, delta, reason = compare_metrics(
            baseline=baseline_metric,
            candidate=measurement.value,
            direction=objective.success.direction,
            min_delta=objective.success.min_delta if min_delta is None else min_delta,
            target=objective.success.target if target is None else target,
        )
        return VerificationResult(
            accepted=accepted,
            baseline_metric=baseline_metric,
            candidate_metric=measurement.value,
            delta=delta,
            reason=reason,
            measurement=measurement,
            tests=tests,
        )
    except Exception as exc:  # verification must never crash the engine
        return VerificationResult(
            accepted=False,
            baseline_metric=baseline_metric,
            candidate_metric=None,
            delta=0.0,
            reason=f"verification error: {exc}",
            measurement=MetricMeasurement(value=None, error=str(exc)),
            error=str(exc),
        )
    finally:
        if verify_path is not None:
            sandbox.remove_verification_worktree(verify_path)
