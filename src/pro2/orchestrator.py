"""Autonomous research orchestrator: init, science loop, engineering MCTS, recovery."""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from pro2.agents import (
    AgentError,
    AgentResult,
    AgentRunner,
    build_diagnosis_prompt,
    build_hypothesis_prompt,
    build_implement_prompt,
    build_review_prompt,
    build_supervisor_prompt,
)
from pro2.candidate_solution import CandidateSolution
from pro2.config import Objective
from pro2.evidence import (
    build_file_context,
    collect_environment,
    evidence_text,
    heuristic_diagnosis,
    run_diagnostics,
)
from pro2.experience import ExperienceBank
from pro2.experiment import *  # noqa: F403  (re-export convenience)
from pro2.git_sandbox import GitError, GitSandbox, ProtectedPathError, Worktree
from pro2.hypothesis import Hypothesis, adaptive_reschedule, select_hypothesis
from pro2.integrity import (
    assert_snapshot_integrity,
    evaluator_mismatch,
    pin_evaluator,
)
from pro2.integrity import (
    lineage as experiment_lineage,
)
from pro2.optimizer import EngineeringOptimizer, apply_config
from pro2.problem import Problem
from pro2.records import EngineeringTrial, Experiment
from pro2.research import ResearchEngine, ResearchFinding
from pro2.state import EngineState, StateStore
from pro2.util import sha256_text, truncate, utc_now
from pro2.verification import MetricMeasurement, measure_metric, verify_commit


class Engine:
    """Runs the complete Pro² loop against one objective."""

    # Fail-closed escalation: repeated reviewer unavailability pauses the run
    # for human triage instead of silently continuing or approving.
    MAX_REVIEWER_UNAVAILABLE = 3
    MAX_PI_MALFORMED = 3

    def __init__(
        self,
        objective: Objective,
        state_dir: str | Path | None = None,
        agent: AgentRunner | None = None,
        sandbox: GitSandbox | None = None,
    ) -> None:
        self.objective = objective
        self.repo = objective.repo
        self.state_dir = Path(state_dir) if state_dir else objective.state_path
        self.store = StateStore(self.state_dir)
        self.store.ensure()
        self.sandbox = sandbox or GitSandbox(
            self.repo, self.store.worktrees_dir, self.store.verify_dir
        )
        self.agent = agent or AgentRunner(
            objective.agent, self.store.artifacts_dir, security=objective.security
        )
        self.experience = (
            ExperienceBank(self.store.experience_file, objective.experience.max_entries)
            if objective.experience.enabled
            else None
        )
        self.research = ResearchEngine(objective.research, self.store.research_dir)
        self.state = EngineState(objective_name=objective.name)
        self._run_started: float | None = None
        self._wall_at_start = 0.0

    # ------------------------------------------------------------------ load
    def load(self) -> EngineState:
        self.state = self.store.load_state()
        return self.state

    def initialize(self) -> EngineState:
        with self.store.lock():
            return self._initialize_locked()

    def _initialize_locked(self) -> EngineState:
        self.store.ensure()
        self.sandbox.ensure_state_excluded(self.objective.state_dir)
        if self.objective.constraints.require_clean_worktree and not self.sandbox.is_clean():
            raise RuntimeError(
                "repository must be clean before initialization; commit or stash changes"
            )

        head = self.sandbox.head()
        baseline = self._measure_commit(self.objective.baseline, head, "baseline")
        if baseline.value is None:
            raise RuntimeError(f"baseline measurement failed: {baseline.error}")

        self.state = EngineState(
            objective_name=self.objective.name,
            status="initialized",
            phase="science",
            baseline_metric=baseline.value,
            baseline_commit=head,
            best_metric=baseline.value,
            best_commit=head,
            baseline_id=head,
            run_started_at=None,
            metadata={
                "objective": self.objective.to_dict(),
                "provider": self._provider_name(),
                "baseline_measurement": baseline.to_dict(),
                # Integrity: pin the evaluator contract so candidates cannot
                # drift verify/tests mid-run.
                "evaluator_digest": pin_evaluator(self.objective),
            },
        )
        self.store.append_event(
            "baseline_measured",
            commit=head,
            metric=baseline.value,
            measurement=baseline.to_dict(),
        )

        env_evidence, environment = collect_environment(self.repo, self.store.research_dir)
        diag_path = self.sandbox.create_verification_worktree(head, "diagnostics")
        try:
            diag_evidence, diagnostics, diagnostic_text = run_diagnostics(
                self.objective, diag_path, self.store.artifacts_dir
            )
        finally:
            self.sandbox.remove_verification_worktree(diag_path)

        all_evidence = env_evidence + diag_evidence
        self.state.evidence = [item.to_dict() for item in all_evidence]

        initial_findings = self.research.search([self.objective.description])
        self.state.research_findings = [f.to_dict() for f in initial_findings]
        research_text = self.research.format_for_prompt(initial_findings)

        if self.objective.seed_problems:
            self._add_seed_problems()
        else:
            self._diagnose(
                evidence_text(all_evidence, diagnostic_text, environment),
                build_file_context(self.repo),
                research_text,
            )

        self._attach_research_candidates(initial_findings)
        self._research_problems()

        if self.objective.seed_hypotheses:
            self._add_seed_hypotheses()
        else:
            self._generate_hypotheses()

        if not self.state.identified_problems:
            raise RuntimeError("initialization produced no problems to investigate")

        self.state.status = "initialized"
        self.state.phase = "science"
        self.state.iteration = 0
        self.store.save_state(self.state)
        self.store.append_event(
            "initialized",
            baseline_metric=self.state.baseline_metric,
            baseline_commit=self.state.baseline_commit,
            problems=len(self.state.identified_problems),
            hypotheses=len(self.state.hypotheses),
        )
        return self.state

    # -------------------------------------------------------------- diagnosis
    def _diagnose(self, evidence: str, file_context: str, research_text: str) -> None:
        prompt = build_diagnosis_prompt(self.objective, evidence, file_context, research_text)
        payload: dict[str, Any] | None = None
        try:
            response, result = self.agent.ask_json("DIAGNOSE", prompt, self.repo)
            if result.cost_usd:
                self._add_cost(result.cost_usd)
            if isinstance(response, dict):
                payload = response
        except AgentError as exc:
            self.store.append_event("agent_unavailable", task="DIAGNOSE", error=str(exc))

        if payload and payload.get("problems"):
            self._parse_problems(payload)
            self.store.append_event(
                "diagnosed", source="agent", problems=len(self.state.identified_problems)
            )
            return

        diagnostics = self._diagnostic_runs()
        problems = heuristic_diagnosis(self.objective, self.repo, diagnostics, evidence)
        for problem in problems:
            self.state.add_problem(problem)
            self.state.problems.append(problem.description)
        self.store.append_event("diagnosed", source="heuristic", problems=len(problems))

    def _diagnostic_runs(self) -> list[Any]:
        # Diagnostics were already run in a throwaway worktree; re-read their
        # recorded metadata rather than re-executing them.
        from pro2.runner import RunResult

        runs = []
        for item in self.state.evidence:
            if item.get("kind") != "diagnostic":
                continue
            metadata = item.get("metadata") or {}
            runs.append(
                RunResult(
                    command=metadata.get("command", ""),
                    cwd=str(self.repo),
                    exit_code=int(metadata.get("exit_code", 0)),
                    duration_seconds=float(metadata.get("duration_seconds", 0.0)),
                    timed_out=bool(metadata.get("timed_out", False)),
                    stdout_path=Path(item["content_path"]) if item.get("content_path") else None,
                )
            )
        return runs

    def _parse_problems(self, payload: dict[str, Any]) -> None:
        for index, item in enumerate(payload.get("problems", [])):
            if not isinstance(item, dict):
                continue
            problem_id = str(item.get("problem_id") or f"P{index + 1:03d}")
            if self.state.get_problem(problem_id):
                continue
            description = str(item.get("description", "")).strip()
            if not description:
                continue
            problem = Problem(
                problem_id=problem_id,
                description=description,
                severity=_clamp(float(item.get("severity", 0.5))),
                evidence_ids=[str(x) for x in item.get("evidence_ids", [])],
                profiling_evidence=[str(x) for x in item.get("evidence_ids", [])],
            )
            for candidate_data in item.get("candidates", []):
                if not isinstance(candidate_data, dict):
                    continue
                candidate_id = str(
                    candidate_data.get("candidate_id")
                    or f"C{len(problem.candidate_solutions) + 1:03d}"
                )
                try:
                    candidate = CandidateSolution(
                        candidate_id=candidate_id,
                        description=str(candidate_data.get("description", "")),
                        belief=_clamp(float(candidate_data.get("initial_belief", 0.5))),
                        references=[str(x) for x in candidate_data.get("references", [])],
                        rationale=str(candidate_data.get("rationale", "")),
                        patch=candidate_data.get("patch"),
                        instructions=str(candidate_data.get("instructions", "")),
                        metadata=dict(candidate_data.get("metadata", {})),
                    )
                    problem.add_candidate(candidate)
                except ValueError:
                    continue
            if not problem.candidate_solutions:
                problem.add_candidate(
                    CandidateSolution(
                        f"C{len(problem.candidate_solutions) + 1:03d}",
                        f"Address: {description}",
                        0.4,
                        rationale="Fallback candidate for an otherwise unsolved problem.",
                    )
                )
            self.state.add_problem(problem)
            self.state.problems.append(problem.description)

    def _add_seed_problems(self) -> None:
        for index, item in enumerate(self.objective.seed_problems):
            problem = Problem(
                problem_id=str(item.get("problem_id") or f"P{index + 1:03d}"),
                description=str(item.get("description", "")),
                severity=_clamp(float(item.get("severity", 0.5))),
                evidence_ids=[str(x) for x in item.get("evidence_ids", [])],
            )
            for candidate_data in item.get("candidates", []):
                candidate = CandidateSolution(
                    candidate_id=str(candidate_data.get("candidate_id") or "C001"),
                    description=str(candidate_data.get("description", "")),
                    belief=_clamp(float(candidate_data.get("belief", 0.5))),
                    references=[str(x) for x in candidate_data.get("references", [])],
                    rationale=str(candidate_data.get("rationale", "")),
                    patch=candidate_data.get("patch"),
                    instructions=str(candidate_data.get("instructions", "")),
                    metadata=dict(candidate_data.get("metadata", {})),
                )
                problem.add_candidate(candidate)
            if not problem.candidate_solutions:
                problem.add_candidate(
                    CandidateSolution("C001", f"Address: {problem.description}", 0.5)
                )
            if self.state.get_problem(problem.problem_id) is None:
                self.state.add_problem(problem)
                self.state.problems.append(problem.description)

    def _attach_research_candidates(self, findings: list[ResearchFinding]) -> None:
        for problem in self.state.identified_problems:
            existing_refs = {
                ref for candidate in problem.candidate_solutions for ref in candidate.references
            }
            problem_tokens = _word_tokens(problem.description)
            attached = 0
            for finding in findings:
                if attached >= 2:
                    break
                # Only verified findings become candidates; unverified search
                # results stay in the research log but are never trusted.
                if not finding.verified:
                    continue
                if not problem_tokens or not problem_tokens.intersection(
                    _word_tokens(f"{finding.title} {finding.summary}")
                ):
                    continue
                if finding.url in existing_refs:
                    continue
                attached += 1
                existing_refs.add(finding.url)
                candidate_id = f"C{len(problem.candidate_solutions) + 1:03d}"
                if problem.get_candidate(candidate_id):
                    continue
                problem.add_candidate(
                    CandidateSolution(
                        candidate_id,
                        f"Apply finding: {finding.title}",
                        0.35,
                        references=[finding.url],
                        rationale=(
                            finding.summary or "Retrieved from literature/repository research."
                        ),
                        metadata={"source": finding.source},
                    )
                )

    def _research_problems(self) -> None:
        if not self.objective.research.enabled:
            return
        try:
            findings = self.research.search_problems(self.state.identified_problems)
        except Exception as exc:  # research must never block the loop
            self.store.append_event("research_error", error=str(exc))
            return
        known = {item.get("url") for item in self.state.research_findings}
        for finding in findings:
            if finding.url not in known:
                self.state.research_findings.append(finding.to_dict())
                known.add(finding.url)

    # ------------------------------------------------------------- hypotheses
    def _generate_hypotheses(self) -> None:
        created = 0
        for problem in self.state.identified_problems:
            for candidate in problem.candidate_solutions[:3]:
                if created >= 5:
                    return
                experience_text = self._experience_for(problem, candidate.description)
                payload: dict[str, Any] | None = None
                try:
                    response, result = self.agent.ask_json(
                        "HYPOTHESIZE",
                        build_hypothesis_prompt(
                            self.objective, problem, candidate, experience_text
                        ),
                        self.repo,
                    )
                    if result.cost_usd:
                        self._add_cost(result.cost_usd)
                    if isinstance(response, dict):
                        payload = response
                except AgentError:
                    payload = None

                if payload and payload.get("hypotheses"):
                    for item in payload["hypotheses"]:
                        if not isinstance(item, dict):
                            continue
                        confidence = 0.5
                        if isinstance(item.get("confidence"), (int, float)):
                            confidence = _clamp(float(item["confidence"]))
                        hypothesis = Hypothesis(
                            problem_id=problem.problem_id,
                            candidate_id=str(item.get("candidate_id") or candidate.candidate_id),
                            modification=str(
                                item.get("modification")
                                or item.get("title")
                                or candidate.description
                            ),
                            title=str(item.get("title", "")),
                            target_delta=_optional_float(item.get("target_delta")),
                            instructions=str(item.get("instructions", candidate.instructions)),
                            confidence=confidence,
                        )
                        for edge in item.get("edges", []) or []:
                            if isinstance(edge, dict):
                                hypothesis.add_edge(
                                    str(edge.get("parent_id", "")),
                                    str(edge.get("relation", "supports")),
                                )
                        for parent_id in item.get("contradicts", []) or []:
                            hypothesis.add_edge(str(parent_id), "contradicts")
                        if self._add_hypothesis_safely(hypothesis):
                            created += 1
                elif candidate.instructions or candidate.patch:
                    hypothesis = Hypothesis(
                        problem_id=problem.problem_id,
                        candidate_id=candidate.candidate_id,
                        modification=candidate.description,
                        title=candidate.description[:100],
                        instructions=candidate.instructions,
                        metadata={"patch": candidate.patch} if candidate.patch else {},
                    )
                    if self._add_hypothesis_safely(hypothesis):
                        created += 1

    def _add_seed_hypotheses(self) -> None:
        for item in self.objective.seed_hypotheses:
            hypothesis = Hypothesis(
                problem_id=str(item.get("problem_id", "")),
                candidate_id=str(item.get("candidate_id", "")),
                modification=str(item.get("modification") or item.get("title") or ""),
                hypothesis_id=str(item.get("hypothesis_id", "")),
                title=str(item.get("title", "")),
                target_delta=_optional_float(item.get("target_delta")),
                instructions=str(item.get("instructions", "")),
                metadata=dict(item.get("metadata", {})),
            )
            try:
                self.state.validate_hypothesis(hypothesis)
            except ValueError:
                continue
            self._add_hypothesis_safely(hypothesis)

    def _add_hypothesis_safely(self, hypothesis: Hypothesis) -> bool:
        try:
            self.state.validate_hypothesis(hypothesis)
        except ValueError:
            return False
        if self.state.get_hypothesis(hypothesis.hypothesis_id):
            return False
        self.state.add_hypothesis(hypothesis)
        return True

    def _refill_hypotheses(self) -> None:
        covered = {(h.problem_id, h.candidate_id) for h in self.state.hypotheses}
        for problem in self.state.identified_problems:
            for candidate in problem.candidate_solutions:
                if (problem.problem_id, candidate.candidate_id) in covered:
                    continue
                if candidate.instructions or candidate.patch:
                    hypothesis = Hypothesis(
                        problem_id=problem.problem_id,
                        candidate_id=candidate.candidate_id,
                        modification=candidate.description,
                        instructions=candidate.instructions,
                        metadata={"patch": candidate.patch} if candidate.patch else {},
                    )
                    self._add_hypothesis_safely(hypothesis)
                    covered.add((problem.problem_id, candidate.candidate_id))
        if not self.state.pending_hypotheses() and not self.objective.seed_problems:
            self._generate_hypotheses()

    # -------------------------------------------------------------- integrity
    def _assert_integrity(self) -> None:
        """Fail closed when the verification evaluator drifted from the pin."""
        pinned = self.state.metadata.get("evaluator_digest")
        if not pinned:
            return
        error = evaluator_mismatch(self.objective, str(pinned))
        if error:
            self.store.append_event("integrity_violation", error=error)
            raise RuntimeError(error)

    def _assert_invariants(self) -> None:
        """Paper §2: Problem State ↔ experiment ↔ Git snapshot invariants."""
        if self.state.phase not in {"science", "engineering", "done"}:
            raise RuntimeError(f"invariant violated: unexpected phase {self.state.phase!r}")
        if self.state.best_commit is None:
            raise RuntimeError("invariant violated: best_commit is None")
        if not self.sandbox.commit_exists(self.state.best_commit):
            raise RuntimeError(
                f"invariant violated: best_commit {self.state.best_commit[:12]} "
                "is not a git object in the repository"
            )

    # -------------------------------------------------------------- step
    def step(self) -> Experiment | None:
        with self.store.lock():
            return self._step_locked()

    def _step_locked(self) -> Experiment | None:
        # Fail closed when the evaluator or invariants were violated.
        self._assert_integrity()
        self._assert_invariants()
        if self.state.status in {"initialized", "paused"}:
            self.state.status = "running"
        if self.store.pause_requested():
            self.state.status = "paused"
            self.state.stop_reason = "pause requested"
            self.store.save_state(self.state)
            return None

        reason = self._budget_reason()
        if reason:
            self.state.status = "budget_exhausted"
            self.state.stop_reason = reason
            self.store.save_state(self.state)
            self.store.append_event("budget_exhausted", reason=reason)
            return None

        # Paper: adaptive rescheduling — boost under-attempted problems whose
        # hypotheses keep being falsified, then re-score pending hypotheses.
        adaptive_reschedule(self.state)
        hypothesis = select_hypothesis(
            self.state.pending_hypotheses(),
            self.state.candidate_beliefs(),
            self.state.problem_attempts(),
        )
        if hypothesis is None:
            self._refill_hypotheses()
            hypothesis = select_hypothesis(
                self.state.pending_hypotheses(),
                self.state.candidate_beliefs(),
                self.state.problem_attempts(),
            )
        if hypothesis is None:
            self.state.stop_reason = "no pending hypotheses"
            self.state.status = "completed"
            self.store.save_state(self.state)
            return None

        return self._run_hypothesis(hypothesis)

    def _run_hypothesis(self, hypothesis: Hypothesis) -> Experiment:
        problem = self.state.get_problem(hypothesis.problem_id)
        candidate = self.state.get_candidate(hypothesis.problem_id, hypothesis.candidate_id)
        if problem is None or candidate is None:
            hypothesis.status = "skipped"
            self.store.save_state(self.state)
            raise RuntimeError("selected hypothesis has no valid problem/candidate")

        experiment = Experiment(
            experiment_id=f"E-{uuid.uuid4().hex[:10]}",
            hypothesis_id=hypothesis.hypothesis_id,
            problem_id=problem.problem_id,
            candidate_id=candidate.candidate_id,
            base_commit=self.state.best_commit or self.sandbox.head(),
            baseline_metric=self.state.baseline_metric,
        )
        # Reproducible lineage + git snapshot identity for the experiment.
        experiment.lineage = experiment_lineage(experiment.base_commit, self.state.best_commit)
        experiment.snapshot_digest = assert_snapshot_integrity(self.sandbox, experiment.base_commit)
        hypothesis.status = "running"
        hypothesis.attempts += 1
        hypothesis.experiment_id = experiment.experiment_id
        problem.record_attempt()
        self.state.add_experiment(experiment)
        self.state.in_flight = experiment.to_dict()
        self.store.save_state(self.state)
        self.store.append_event(
            "experiment_started",
            experiment_id=experiment.experiment_id,
            hypothesis_id=hypothesis.hypothesis_id,
            problem_id=problem.problem_id,
            candidate_id=candidate.candidate_id,
            base_commit=experiment.base_commit,
        )

        worktree: Worktree | None = None
        started = time.monotonic()
        try:
            worktree = self.sandbox.create_experiment(
                experiment.experiment_id, experiment.base_commit
            )
            experiment.branch = worktree.branch
            experiment.worktree_path = str(worktree.path)

            agent_cost = 0.0
            commit: str | None = None
            review: dict[str, Any] = {}
            feedback = ""
            attempts = self.objective.agent.max_retries + 1

            for attempt in range(attempts):
                fingerprint_before = self.sandbox.fingerprint(active=worktree.path)
                patch = hypothesis.metadata.get("patch") or candidate.patch
                if patch:
                    applied, error = self._apply_patch(worktree.path, patch)
                    if not applied:
                        experiment.reason = f"patch failed to apply: {error}"
                        break
                else:
                    result = self._run_agent(
                        "IMPLEMENT",
                        build_implement_prompt(
                            self.objective,
                            hypothesis,
                            problem,
                            candidate,
                            evidence_text([item for item in self.state.evidence], "", ""),
                            self._experience_for(problem, hypothesis.title),
                            feedback,
                        ),
                        worktree.path,
                        timeout_seconds=min(
                            self.objective.agent.timeout_seconds,
                            self.objective.budget.max_experiment_seconds,
                        ),
                    )
                    agent_cost += result.cost_usd
                    experiment.agent = result.to_dict()
                    if not result.ok:
                        experiment.reason = f"implementation agent failed: {result.error}"
                        break

                fingerprint_after = self.sandbox.fingerprint(active=worktree.path)
                if fingerprint_after != fingerprint_before:
                    experiment.reason = (
                        "agent modified files outside the experiment worktree; aborting"
                    )
                    break

                commit = self.sandbox.commit_all(
                    worktree.path, f"pro2: {hypothesis.title or hypothesis.modification}"
                )
                if commit is None:
                    experiment.reason = "implementation produced no changes"
                    break

                stats = self.sandbox.diff_stats(worktree.path, experiment.base_commit)
                experiment.diff = {
                    "changed_paths": stats.changed_paths,
                    "added": stats.added,
                    "removed": stats.removed,
                    "patch": stats.patch,
                    "untracked": stats.untracked or [],
                }
                review = self._review(hypothesis, stats, worktree.path)
                experiment.review = review
                if review.get("approved"):
                    # Explicit approval record: reviewer identity + diff digest.
                    self._record_approval(experiment)
                    break
                feedback = truncate("\n".join(review.get("reasons", [])) or "review rejected", 1200)
                hypothesis.review_feedback = feedback
                if attempt + 1 >= attempts:
                    experiment.reason = "reviewer rejected: " + feedback
                    break

            if commit is None:
                self._reject(
                    experiment,
                    hypothesis,
                    problem,
                    candidate,
                    experiment.reason or "no commit produced",
                )
                return experiment

            if not review.get("approved"):
                self._reject(
                    experiment,
                    hypothesis,
                    problem,
                    candidate,
                    experiment.reason or "reviewer rejected the change",
                )
                return experiment

            self._assert_integrity()
            verification = verify_commit(
                self.sandbox,
                self.objective,
                commit,
                self.state.best_metric if self.state.best_metric is not None else 0.0,
                experiment.experiment_id,
                self.store.artifacts_dir,
                min_delta=(
                    hypothesis.target_delta
                    if hypothesis.target_delta is not None
                    else self.objective.success.min_delta
                ),
                target=self.objective.success.target,
            )
            experiment.commit = commit
            experiment.candidate_metric = verification.candidate_metric
            experiment.delta = verification.delta
            experiment.verification = verification.to_dict()
            experiment.duration_seconds = time.monotonic() - started
            experiment.ended_at = utc_now()
            experiment.accepted = verification.accepted
            experiment.reason = verification.reason

            if verification.accepted:
                candidate.adjust_belief(self.objective.belief_positive_delta)
                hypothesis.status = "accepted"
                hypothesis.adjust_confidence(0.15)
                candidate.evidence.append(experiment.experiment_id)
                self.state.best_commit = commit
                self.state.best_metric = verification.candidate_metric
                self.sandbox.set_best(commit)
                self.state.consecutive_failures = 0
                self.state.insights.append(f"{hypothesis.title}: {verification.reason}")
                self._record_experience(experiment, hypothesis, problem, candidate, accepted=True)
                experiment.status = "accepted"
            else:
                candidate.adjust_belief(-self.objective.belief_negative_delta)
                # Falsification (paper: hypothesis executed and the metric did
                # not improve) is distinct from an unverified rejection: only a
                # measured outcome can falsify.
                falsified = verification.candidate_metric is not None
                hypothesis.status = "falsified" if falsified else "rejected"
                if falsified:
                    hypothesis.falsified = True
                    hypothesis.falsification_reason = verification.reason
                hypothesis.adjust_confidence(-0.25 if falsified else -0.15)
                candidate.evidence.append(experiment.experiment_id)
                self.state.consecutive_failures += 1
                self._record_experience(experiment, hypothesis, problem, candidate, accepted=False)
                experiment.status = "rejected"

            self._finish_experiment(experiment, agent_cost)
            return experiment
        except Exception as exc:
            experiment.duration_seconds = time.monotonic() - started
            experiment.ended_at = utc_now()
            experiment.accepted = False
            experiment.status = "failed"
            experiment.reason = f"experiment error: {exc}"
            hypothesis.status = "rejected"
            self.state.consecutive_failures += 1
            self._record_experience(experiment, hypothesis, problem, candidate, accepted=False)
            self._finish_experiment(experiment, experiment.agent.get("cost_usd", 0.0))
            return experiment
        finally:
            if worktree is not None:
                self.sandbox.remove_experiment(worktree)

    def _reject(
        self,
        experiment: Experiment,
        hypothesis: Hypothesis,
        problem: Problem,
        candidate: CandidateSolution,
        reason: str,
    ) -> None:
        experiment.accepted = False
        experiment.status = "rejected"
        experiment.reason = reason
        experiment.ended_at = utc_now()
        experiment.duration_seconds = max(experiment.duration_seconds, 0.0)
        hypothesis.status = "rejected"
        candidate.adjust_belief(-self.objective.belief_negative_delta)
        self.state.consecutive_failures += 1
        self._record_experience(experiment, hypothesis, problem, candidate, accepted=False)
        self._finish_experiment(experiment, experiment.agent.get("cost_usd", 0.0))

    def _finish_experiment(self, experiment: Experiment, agent_cost: float) -> None:
        self.state.budget_used["experiments"] = (
            int(self.state.budget_used.get("experiments", 0)) + 1
        )
        self.state.budget_used["cost_usd"] = (
            float(self.state.budget_used.get("cost_usd", 0.0)) + agent_cost
        )
        self.state.budget_used["wall_seconds"] = (
            float(self.state.budget_used.get("wall_seconds", 0.0)) + experiment.duration_seconds
        )
        self.state.iteration += 1
        self.state.in_flight = None
        self.store.save_state(self.state)
        self.store.append_event(
            "experiment_finished",
            experiment_id=experiment.experiment_id,
            accepted=experiment.accepted,
            status=experiment.status,
            metric=experiment.candidate_metric,
            delta=experiment.delta,
            reason=experiment.reason,
        )

    # ----------------------------------------------------------------- review
    def _heuristic_review(
        self, hypothesis: Hypothesis, stats: Any, worktree: Path
    ) -> dict[str, Any]:
        violations: list[str] = []
        changed = list(stats.changed_paths) + list(stats.untracked or [])
        if not changed:
            violations.append("no files changed")
        if len(stats.changed_paths) > self.objective.constraints.max_changed_files:
            violations.append(
                f"changed {len(stats.changed_paths)} files, limit is "
                f"{self.objective.constraints.max_changed_files}"
            )
        if stats.added + stats.removed > self.objective.constraints.max_diff_lines:
            violations.append(
                f"diff has {stats.added + stats.removed} lines, limit is "
                f"{self.objective.constraints.max_diff_lines}"
            )
        protected = list(self.objective.constraints.protected_paths) + list(
            self.objective.constraints.forbidden_paths
        )
        for changed_path in changed:
            for pattern in protected:
                if _path_matches(changed_path, pattern):
                    violations.append(f"protected path modified: {changed_path}")
            if not self.objective.constraints.allow_test_edits and _is_test_path(changed_path):
                violations.append(f"test file modified: {changed_path}")
        if violations:
            return {
                "approved": False,
                "single_variable": False,
                "reasons": list(violations),
                "violations": list(violations),
                "source": "heuristic",
            }
        return {
            "approved": True,
            "single_variable": True,
            "reasons": ["heuristic review passed"],
            "violations": [],
            "source": "heuristic",
        }

    def _review(self, hypothesis: Hypothesis, stats: Any, worktree: Path) -> dict[str, Any]:
        # Hard constraints are a mandatory gate: a violating diff rejects even
        # if the reviewer agent claims approval.
        heuristic = self._heuristic_review(hypothesis, stats, worktree)
        if not heuristic["approved"]:
            return heuristic

        reviewer_meta = {
            "reviewer_id": "",
            "reviewer_source": "heuristic",
            "review_digest": sha256_text(stats.patch),
        }

        diff_summary = (
            f"{len(stats.changed_paths)} files, +{stats.added}/-{stats.removed}\n"
            + "\n".join(stats.changed_paths)
        )
        try:
            payload, result = self.agent.ask_json(
                "REVIEW",
                build_review_prompt(self.objective, hypothesis, stats.patch, diff_summary),
                worktree,
                timeout_seconds=self._review_timeout(),
            )
        except AgentError:
            # Fail closed: an unavailable reviewer must never silently approve.
            self.state.reviewer_unavailable += 1
            self._escalate("reviewer unavailable")
            return {
                "approved": False,
                "single_variable": False,
                "reasons": ["reviewer unavailable; failing closed"],
                "violations": [],
                "source": "agent",
                "reviewer": reviewer_meta,
            }
        if result.cost_usd:
            self._add_cost(result.cost_usd)
        reviewer_meta["reviewer_id"] = result.session_id or result.model or "unknown"
        reviewer_meta["reviewer_source"] = result.provider or "agent"

        if not isinstance(payload, dict):
            self.state.reviewer_unavailable += 1
            self._escalate("reviewer returned malformed output")
            return {
                "approved": False,
                "single_variable": False,
                "reasons": ["reviewer returned a missing or malformed review"],
                "violations": [],
                "source": "agent",
                "reviewer": reviewer_meta,
            }
        agent_violations = [str(x) for x in payload.get("violations", []) if str(x).strip()]
        if payload.get("approved") is not True or agent_violations:
            return {
                "approved": False,
                "single_variable": False,
                "reasons": agent_violations or ["reviewer did not approve the change"],
                "violations": agent_violations,
                "source": "agent",
                "reviewer": reviewer_meta,
            }
        if payload.get("single_variable") is not True:
            return {
                "approved": False,
                "single_variable": False,
                "reasons": ["reviewer flagged multiple variables in the diff"],
                "violations": [],
                "source": "agent",
                "reviewer": reviewer_meta,
            }
        return {
            "approved": True,
            "single_variable": True,
            "reasons": [str(x) for x in payload.get("reasons", []) if str(x).strip()]
            or ["agent approved"],
            "violations": [],
            "source": "agent",
            "reviewer": reviewer_meta,
        }

    def _escalate(self, reason: str) -> None:
        """Record a fail-closed escalation and journal it to the event log."""
        entry = {"reason": reason, "timestamp": utc_now()}
        self.state.escalations.append(entry)
        self.store.append_event("escalation", **entry)
        self.store.save_state(self.state)

    def _record_approval(self, experiment: Experiment) -> None:
        """Explicit approval record: reviewer identity + diff digest on the experiment."""
        review = experiment.review or {}
        reviewer = review.get("reviewer") or {}
        experiment.reviewer_id = str(reviewer.get("reviewer_id", ""))
        experiment.reviewer_source = str(reviewer.get("reviewer_source", ""))
        experiment.review_digest = str(reviewer.get("review_digest", ""))
        record = {
            "experiment_id": experiment.experiment_id,
            "hypothesis_id": experiment.hypothesis_id,
            "reviewer_id": experiment.reviewer_id,
            "reviewer_source": experiment.reviewer_source,
            "diff_sha256": experiment.review_digest,
        }
        self.state.metadata["last_approval"] = record
        self.store.append_event("approval_recorded", **record)
        self.store.save_state(self.state)

    def approval_records(self) -> list[dict[str, Any]]:
        """Ledger of explicit reviewer approval records (audit trail)."""
        return [e for e in self.store.events() if e.get("type") == "approval_recorded"]

    def _review_timeout(self) -> float:
        return min(
            self.objective.agent.timeout_seconds, self.objective.budget.max_experiment_seconds
        )

    # ----------------------------------------------------------- agent helper
    def _run_agent(
        self, task: str, prompt: str, cwd: Path, timeout_seconds: float | None = None
    ) -> AgentResult:
        try:
            return self.agent.run(task, prompt, cwd, timeout_seconds=timeout_seconds)
        except AgentError as exc:
            return AgentResult(ok=False, error=str(exc), provider="none")

    def _apply_patch(self, worktree: Path, patch: str) -> tuple[bool, str]:
        patch_path = worktree / ".pro2_patch.diff"
        patch_path.write_text(patch, encoding="utf-8")
        try:
            result = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", str(patch_path)],
                cwd=str(worktree),
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            patch_path.unlink(missing_ok=True)
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        return True, ""

    # -------------------------------------------------------------- experience
    def _experience_for(self, problem: Problem, query: str) -> str:
        if not self.experience:
            return ""
        entries = self.experience.search(
            f"{problem.description} {query}",
            limit=self.objective.experience.retrieval_limit,
            certified_only=True,
        )
        return self.experience.format_for_prompt(entries)

    def _record_experience(
        self,
        experiment: Experiment,
        hypothesis: Hypothesis,
        problem: Problem,
        candidate: CandidateSolution,
        accepted: bool,
    ) -> None:
        if not self.experience:
            return
        signature = (
            f"{problem.problem_id}: {problem.description} -> {hypothesis.title}"
            if accepted
            else f"{problem.problem_id}: {experiment.reason or 'rejected'}"
        )
        self.experience.add(
            kind="success" if accepted else "failure",
            signature=signature,
            context=f"{self.objective.name} / {problem.description}",
            root_cause=problem.description,
            fix=hypothesis.modification if accepted else f"avoid: {hypothesis.modification}",
            outcome="accepted" if accepted else "rejected",
            tags=[self.objective.name, problem.problem_id, candidate.candidate_id],
            evidence=[experiment.experiment_id],
            # Certification: only independently verified outcomes are
            # authoritative; unverified lessons carry low confidence.
            certified=accepted,
            confidence=1.0 if accepted else 0.2,
        )

    # ------------------------------------------------------------------- run
    def run(
        self,
        max_experiments: int | None = None,
        max_seconds: float | None = None,
    ) -> EngineState:
        if self.state.best_commit is None:
            self.initialize()

        self.state.status = "running"
        self.state.run_started_at = utc_now()
        self.state.stop_reason = ""
        self.store.clear_pause()
        self._run_started = time.monotonic()
        self._wall_at_start = float(self.state.budget_used.get("wall_seconds", 0.0))
        self.store.save_state(self.state)
        self.store.append_event("run_started")

        status = "completed"
        while True:
            if self.store.pause_requested():
                status = "paused"
                self.state.stop_reason = "pause requested"
                break
            reason = self._budget_reason(max_experiments, max_seconds)
            if reason:
                status = "budget_exhausted"
                self.state.stop_reason = reason
                break
            escalation = self._escalation_threshold_reached()
            if escalation:
                status = "paused"
                self.state.stop_reason = escalation
                break
            experiment = self.step()
            if experiment is None:
                status = "completed"
                if not self.state.stop_reason:
                    self.state.stop_reason = "no runnable hypotheses"
                break
            if (
                len(self.state.experiments) % max(1, self.objective.supervision_interval) == 0
                and not self.supervise()
            ):
                status = "completed"
                break

        self.state.status = status
        if (
            status == "completed"
            and self.objective.engineering.enabled
            and self.objective.engineering.params
            and self.state.phase == "science"
        ):
            self.run_engineering()
            self.state.status = "completed"

        self.write_report()
        self.store.save_state(self.state)
        self.store.append_event(
            "run_finished", status=self.state.status, reason=self.state.stop_reason
        )
        return self.state

    def _budget_reason(
        self,
        max_experiments: int | None = None,
        max_seconds: float | None = None,
    ) -> str:
        experiment_limit = (
            max_experiments
            if max_experiments is not None
            else self.objective.budget.max_experiments
        )
        if int(self.state.budget_used.get("experiments", 0)) >= experiment_limit:
            return f"experiment budget exhausted ({experiment_limit})"
        if self.state.iteration >= self.objective.budget.max_iterations:
            return f"iteration budget exhausted ({self.objective.budget.max_iterations})"

        wall_limit = (
            max_seconds if max_seconds is not None else self.objective.budget.max_wall_seconds
        )
        elapsed = float(self.state.budget_used.get("wall_seconds", 0.0))
        if self._run_started is not None:
            elapsed += time.monotonic() - self._run_started
        if elapsed >= wall_limit:
            return f"wall-clock budget exhausted ({wall_limit:.0f}s)"

        cost_limit = self.objective.budget.max_cost_usd
        if cost_limit > 0 and float(self.state.budget_used.get("cost_usd", 0.0)) >= cost_limit:
            return f"cost budget exhausted (${cost_limit:.2f})"
        if self.state.consecutive_failures >= self.objective.budget.max_consecutive_failures:
            return (
                f"consecutive failure limit reached "
                f"({self.objective.budget.max_consecutive_failures})"
            )
        disk_limit = self.objective.budget.max_disk_bytes
        if disk_limit > 0:
            disk_used = self.store.disk_usage_bytes()
            if disk_used > disk_limit:
                return f"disk budget exhausted ({disk_used} > {disk_limit} bytes)"
        return ""

    def supervise(self) -> bool:
        summary = self.status_summary()
        try:
            payload, result = self.agent.ask_json(
                "SUPERVISE", build_supervisor_prompt(self.objective, summary), self.repo
            )
            if result.cost_usd:
                self._add_cost(result.cost_usd)
        except AgentError:
            return True
        if not isinstance(payload, dict):
            # Ambiguous PI output: record it, escalate, keep running but
            # bounded — repeated ambiguity pauses the run for human triage.
            self.state.pi_malformed += 1
            self._escalate("PI supervisor returned malformed output")
            return True
        if payload.get("new_problems"):
            self._parse_problems({"problems": payload["new_problems"]})
        if not payload.get("continue", True):
            self.state.stop_reason = str(payload.get("stop_reason") or "PI stopped the run")
            return False
        if payload.get("notes"):
            self.state.insights.append(str(payload["notes"]))
        return True

    def _escalation_threshold_reached(self) -> str | None:
        if self.state.reviewer_unavailable >= self.MAX_REVIEWER_UNAVAILABLE:
            return (
                f"reviewer unavailable {self.state.reviewer_unavailable} times; escalate to human"
            )
        if self.state.pi_malformed >= self.MAX_PI_MALFORMED:
            return f"PI supervisor ambiguous {self.state.pi_malformed} times; escalate to human"
        return None

    # ------------------------------------------------------------ engineering
    def run_engineering(self) -> None:
        config = self.objective.engineering
        if not config.enabled or not config.params:
            return
        self.state.phase = "engineering"
        self.state.engineering.setdefault("root_commit", self.state.best_commit)
        root_commit = self.state.engineering.get("root_commit") or self.state.best_commit
        root_metric = self.state.engineering.get("root_metric") or (self.state.best_metric or 0.0)
        self.state.engineering["root_metric"] = root_metric
        optimizer = (
            EngineeringOptimizer.from_dict(
                config.params,
                self.state.engineering,
                seed=self.objective.seed,
            )
            if self.state.engineering.get("nodes")
            else EngineeringOptimizer(
                config.params,
                max_nodes=config.max_nodes,
                gamma=config.gamma,
                seed=self.objective.seed,
                exploration_constant=config.exploration_constant,
                direction=config.direction,
            )
        )
        self.store.append_event("engineering_started", root_commit=root_commit)
        self._assert_integrity()
        self._assert_invariants()

        while True:
            reason = self._budget_reason()
            if reason:
                self.state.stop_reason = reason
                break
            node_id, node_config = optimizer.next_config()
            if node_id is None:
                break

            trial = EngineeringTrial(
                trial_id=f"ENG-{uuid.uuid4().hex[:8]}",
                node_id=node_id,
                parent_id=optimizer.nodes[node_id].parent_id,
                config={f"{path}::{key}": value for (path, key), value in node_config.items()},
                base_commit=root_commit,
            )
            self.state.engineering_trials.append(trial.to_dict())
            started = time.monotonic()
            worktree: Worktree | None = None
            try:
                worktree = self.sandbox.create_experiment(trial.trial_id, root_commit)
                env = apply_config(worktree.path, node_config, config.params)
                commit = self.sandbox.commit_all(
                    worktree.path, f"pro2 engineering: {trial.trial_id}"
                )
                if commit is None:
                    optimizer.record_result(
                        node_id,
                        delta=0.0,
                        duration_seconds=0.0,
                        metric=None,
                        status="no_change",
                    )
                    trial.status = "no_change"
                    trial.reason = "configuration produced no change"
                    continue
                protected = (
                    list(self.objective.constraints.protected_paths)
                    + list(self.objective.constraints.forbidden_paths)
                    + list(config.protected_paths)
                )
                try:
                    self.sandbox.assert_not_protected(worktree.path, root_commit, protected)
                except ProtectedPathError as exc:
                    optimizer.record_result(
                        node_id,
                        delta=0.0,
                        duration_seconds=0.0,
                        metric=None,
                        status="protected_violation",
                    )
                    trial.status = "rejected"
                    trial.reason = str(exc)
                    continue

                self._assert_integrity()
                verification = verify_commit(
                    self.sandbox,
                    self.objective,
                    commit,
                    root_metric,
                    trial.trial_id,
                    self.store.artifacts_dir,
                    min_delta=0.0,
                    env=env,
                )
                duration = time.monotonic() - started
                metric = verification.candidate_metric
                delta = verification.delta
                value = optimizer.record_result(
                    node_id,
                    delta=delta,
                    duration_seconds=duration,
                    metric=metric,
                    commit=commit,
                    status="completed" if metric is not None else "failed",
                )
                trial.commit = commit
                trial.metric = metric
                trial.delta = delta
                trial.duration_seconds = duration
                trial.reward = value
                trial.accepted = verification.accepted
                trial.status = "completed" if metric is not None else "failed"
                trial.reason = verification.reason
                trial.ended_at = utc_now()
                if (
                    verification.accepted
                    and metric is not None
                    and self._is_better(metric, self.state.best_metric)
                ):
                    self.state.best_metric = metric
                    self.state.best_commit = commit
                    self.sandbox.set_best(commit)
                self.state.budget_used["experiments"] = (
                    int(self.state.budget_used.get("experiments", 0)) + 1
                )
            except Exception as exc:
                optimizer.record_result(
                    node_id, delta=0.0, duration_seconds=0.0, metric=None, status="failed"
                )
                trial.status = "failed"
                trial.reason = str(exc)
                trial.ended_at = utc_now()
            finally:
                if worktree is not None:
                    self.sandbox.remove_experiment(worktree)
                if self.state.engineering_trials:
                    self.state.engineering_trials[-1] = trial.to_dict()
                self.state.engineering = optimizer.to_dict()
                self.state.engineering["root_commit"] = root_commit
                self.store.save_state(self.state)
                self.store.append_event(
                    "engineering_trial_finished",
                    trial_id=trial.trial_id,
                    status=trial.status,
                    metric=trial.metric,
                    reward=trial.reward,
                )

        self.state.phase = "done"
        self.state.engineering = optimizer.to_dict()
        self.state.engineering["root_commit"] = root_commit
        self.state.engineering["root_metric"] = root_metric
        self._merge_best_into_primary(optimizer.best())
        self.store.save_state(self.state)
        self.store.append_event("engineering_finished", best_node=optimizer.best().node_id)

    def _merge_best_into_primary(self, best: Any) -> None:
        """Paper: merge the highest-validation trajectory back into the primary branch."""
        if best.commit is None or best.metric is None:
            return
        try:
            merged = self.sandbox.merge_into_primary(best.commit, "pro2 engineering")
        except GitError as exc:
            self.store.append_event("engineering_merge_failed", error=str(exc))
            return
        if merged:
            self.state.metadata["engineering_merged_commit"] = merged
            self.store.append_event("engineering_merged_best", commit=merged, metric=best.metric)

    # --------------------------------------------------------------- recovery
    def recover(self) -> EngineState:
        with self.store.lock():
            in_flight = self.state.in_flight
            if in_flight:
                experiment_id = str(in_flight.get("experiment_id", ""))
                experiment = next(
                    (e for e in self.state.experiments if e.experiment_id == experiment_id),
                    None,
                )
                if experiment is not None:
                    experiment.status = "interrupted"
                    experiment.accepted = False
                    experiment.ended_at = utc_now()
                    experiment.reason = "engine crashed or was interrupted mid-experiment"
                    hypothesis = self.state.get_hypothesis(experiment.hypothesis_id)
                    if hypothesis is not None:
                        hypothesis.status = "interrupted"
                if in_flight.get("worktree_path") and in_flight.get("branch"):
                    worktree = Worktree(
                        experiment_id=experiment_id,
                        branch=str(in_flight.get("branch", "")),
                        path=Path(str(in_flight["worktree_path"])),
                        base_commit=str(in_flight.get("base_commit", "")),
                    )
                    self.sandbox.remove_experiment(worktree)
                self.state.in_flight = None
                self.state.status = "paused"
                self.state.stop_reason = "recovered interrupted experiment"
                self.store.save_state(self.state)
                self.store.append_event(
                    "recovered", experiment_id=experiment_id, action="interrupted"
                )
            interrupted = 0
            for trial in self.state.engineering_trials:
                if trial.get("status") == "running":
                    trial["status"] = "interrupted"
                    interrupted += 1
            for node in self.state.engineering.get("nodes", []):
                if node.get("status") in {"running", "pending"}:
                    node["status"] = "interrupted"
            if interrupted:
                self.store.save_state(self.state)
                self.store.append_event("engineering_trials_interrupted", count=interrupted)
            self.sandbox.cleanup_orphans(active_paths=set())
            return self.state

    # ------------------------------------------------------------------ misc
    def _measure_commit(self, spec: Any, commit: str, label: str) -> MetricMeasurement:
        path = self.sandbox.create_verification_worktree(commit, label)
        try:
            return measure_metric(
                spec, path, self.store.artifacts_dir, label, isolation=self.objective.security
            )
        finally:
            self.sandbox.remove_verification_worktree(path)

    def _provider_name(self) -> str:
        try:
            return self.agent.resolve_provider()
        except Exception:
            return "unknown"

    def _is_better(self, metric: float, best: float | None) -> bool:
        if best is None:
            return True
        if self.objective.success.direction == "minimize":
            return metric < best
        return metric > best

    def _add_cost(self, cost: float) -> None:
        if cost <= 0:
            return
        self.state.budget_used["cost_usd"] = float(
            self.state.budget_used.get("cost_usd", 0.0)
        ) + float(cost)

    def status_summary(self) -> str:
        pending = len(self.state.pending_hypotheses())
        lines = [
            f"Objective: {self.objective.name}",
            f"Status: {self.state.status}",
            f"Phase: {self.state.phase}",
            f"Baseline: {self.state.baseline_metric} @ {self.state.baseline_commit}",
            f"Best: {self.state.best_metric} @ {self.state.best_commit}",
            f"Problems: {len(self.state.identified_problems)}",
            f"Hypotheses: {len(self.state.hypotheses)} ({pending} pending)",
            f"Experiments: {len(self.state.experiments)}",
            f"Budget used: {json.dumps(self.state.budget_used)}",
            f"Stop reason: {self.state.stop_reason or '(none)'}",
        ]
        if self.state.experiments:
            lines.append("Recent experiments:")
            for experiment in self.state.experiments[-5:]:
                lines.append(
                    f"  {experiment.experiment_id} {experiment.status} "
                    f"metric={experiment.candidate_metric} delta={experiment.delta}"
                )
        return "\n".join(lines)

    def write_report(self) -> str:
        path = self.objective.report_file
        lines = [
            "# Pro2 Research Report",
            "",
            f"Objective: **{self.objective.name}**",
            "",
            self.objective.description,
            "",
            "## State",
            "",
            f"- Status: {self.state.status}",
            f"- Phase: {self.state.phase}",
            f"- Baseline: {self.state.baseline_metric} @ `{self.state.baseline_commit}`",
            f"- Best: {self.state.best_metric} @ `{self.state.best_commit}`",
            f"- Iterations: {self.state.iteration}",
            f"- Budget used: `{json.dumps(self.state.budget_used)}`",
            f"- Stop reason: {self.state.stop_reason or '(none)'}",
            "",
            "## Problems",
            "",
        ]
        for problem in self.state.identified_problems:
            lines.append(
                f"### {problem.problem_id}: {problem.description}\n"
                f"- severity: {problem.severity}\n"
                f"- attempts: {problem.attempts}\n"
                f"- evidence: {', '.join(problem.evidence_ids) or '(none)'}\n"
            )
            for candidate in problem.candidate_solutions:
                lines.append(
                    f"  - {candidate.candidate_id} (belief {candidate.belief:.3f}): "
                    f"{candidate.description}"
                )
        lines.extend(["", "## Experiments", ""])
        if self.state.experiments:
            lines.append("| id | hypothesis | status | metric | delta | reason |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for experiment in self.state.experiments:
                lines.append(
                    f"| {experiment.experiment_id} | {experiment.hypothesis_id} | "
                    f"{experiment.status} | {experiment.candidate_metric} | "
                    f"{experiment.delta} | {experiment.reason.replace('|', '/')} |"
                )
        else:
            lines.append("(no experiments yet)")
        lines.extend(["", "## Engineering Trials", ""])
        if self.state.engineering_trials:
            for trial in self.state.engineering_trials:
                lines.append(
                    f"- {trial.get('trial_id')} {trial.get('status')} "
                    f"metric={trial.get('metric')} reward={trial.get('reward')}"
                )
        else:
            lines.append("(none)")
        lines.extend(["", "## Research Findings", ""])
        for finding in self.state.research_findings[:20]:
            lines.append(
                f"- [{finding.get('source')}] {finding.get('title')} - {finding.get('url')}"
            )
        if not self.state.research_findings:
            lines.append("(none)")
        lines.extend(["", "## Experience Bank", ""])
        if self.experience:
            for entry in self.experience.all()[-10:]:
                lines.append(f"- [{entry.kind}] {entry.signature} -> {entry.outcome}")
        else:
            lines.append("(disabled)")
        lines.extend(["", "## Insights", ""])
        lines.extend(f"- {insight}" for insight in self.state.insights[-20:])
        if not self.state.insights:
            lines.append("(none)")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _word_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]{3,}", text.lower())}


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _path_matches(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    pattern = pattern.replace("\\", "/").lstrip("./")
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return normalized == prefix or normalized.startswith(prefix + "/")
    if fnmatch.fnmatch(normalized, pattern):
        return True
    if "/" not in pattern and fnmatch.fnmatch(Path(normalized).name, pattern):
        return True
    return False


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    name = Path(normalized).name
    return (
        normalized.startswith("tests/")
        or normalized.startswith("test/")
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )
