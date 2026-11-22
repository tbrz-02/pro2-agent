"""Environment observation, diagnostics, and evidence-backed problem discovery."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from pro2.candidate_solution import CandidateSolution
from pro2.config import Objective
from pro2.problem import Problem
from pro2.records import Evidence
from pro2.runner import RunResult, run_command
from pro2.util import sha256_text, truncate, utc_now


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _tool_version(command: str) -> str:
    try:
        result = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=10)
        return (result.stdout or result.stderr).strip().splitlines()[0][:120]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "missing"


def collect_environment(repo: Path, state_dir: Path) -> tuple[list[Evidence], str]:
    repo = Path(repo)
    tracked = _git(repo, "ls-files").splitlines()
    if not tracked:
        tracked = [
            str(p.relative_to(repo))
            for p in repo.rglob("*")
            if p.is_file()
            and not any(part in {".git", ".venv", ".pro2", "node_modules"} for part in p.parts)
        ][:500]

    extensions: dict[str, int] = {}
    for name in tracked:
        suffix = Path(name).suffix.lower() or "(none)"
        extensions[suffix] = extensions.get(suffix, 0) + 1

    tools = {
        name: _tool_version(name)
        for name in ("python3", "git", "uv", "opencode", "claude", "docker", "rg")
    }
    payload = {
        "observed_at": utc_now(),
        "head": _git(repo, "rev-parse", "HEAD"),
        "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "status": _git(repo, "status", "--porcelain"),
        "tracked_files": tracked[:500],
        "extensions": dict(sorted(extensions.items(), key=lambda kv: -kv[1])),
        "tools": tools,
        "python": os.sys.version.splitlines()[0],
        "cpu_count": os.cpu_count(),
    }

    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "environment.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    summary = (
        f"Repository: {repo}\n"
        f"Branch: {payload['branch']}  HEAD: {payload['head'][:12]}\n"
        f"Tracked files: {len(tracked)}\n"
        f"Extensions: {payload['extensions']}\n"
        f"Tools: {json.dumps(tools)}\n"
        f"Python: {payload['python']}\n"
        f"CPU count: {payload['cpu_count']}\n"
        f"Working tree status: {payload['status'] or '(clean)'}\n"
    )
    evidence = Evidence(
        evidence_id="E-environment",
        kind="environment",
        source=str(path),
        summary=truncate(summary, 2000),
        content_path=str(path),
        content_hash=sha256_text(summary),
        metadata={"head": payload["head"], "branch": payload["branch"]},
    )
    return [evidence], summary


def run_diagnostics(
    objective: Objective,
    repo: Path,
    artifacts_dir: Path,
) -> tuple[list[Evidence], list[RunResult], str]:
    evidence: list[Evidence] = []
    runs: list[RunResult] = []
    sections: list[str] = []
    for index, spec in enumerate(objective.diagnostics):
        run = run_command(
            spec.command,
            cwd=repo,
            timeout_seconds=min(spec.timeout_seconds, objective.budget.max_experiment_seconds),
            artifacts_dir=artifacts_dir,
            label=f"diagnostic-{index + 1}",
            env=spec.env,
        )
        runs.append(run)
        output = (run.stdout + "\n" + run.stderr).strip()
        evidence_id = f"E-diagnostic-{index + 1}"
        evidence.append(
            Evidence(
                evidence_id=evidence_id,
                kind="diagnostic",
                source=spec.command,
                summary=(
                    f"exit={run.exit_code} duration={run.duration_seconds:.2f}s "
                    f"timed_out={run.timed_out}"
                ),
                content_path=str(run.stdout_path or ""),
                content_hash=sha256_text(output),
                metadata={
                    "command": spec.command,
                    "exit_code": run.exit_code,
                    "duration_seconds": run.duration_seconds,
                    "timed_out": run.timed_out,
                    "stderr_path": str(run.stderr_path or ""),
                },
            )
        )
        sections.append(
            f"### diagnostic {index + 1}: {spec.command}\n"
            f"exit={run.exit_code} duration={run.duration_seconds:.2f}s "
            f"timed_out={run.timed_out}\n{truncate(output, 3000)}"
        )
    return evidence, runs, "\n\n".join(sections)


def build_file_context(repo: Path, max_files: int = 40, max_bytes: int = 8000) -> str:
    repo = Path(repo)
    tracked = _git(repo, "ls-files").splitlines()
    if not tracked:
        tracked = [
            str(p.relative_to(repo))
            for p in repo.rglob("*")
            if p.is_file()
            and not any(part in {".git", ".venv", ".pro2", "node_modules"} for part in p.parts)
        ][:max_files]

    lines = ["Files:"]
    lines.extend(f"- {name}" for name in tracked[:max_files])
    interesting = [
        name
        for name in tracked
        if Path(name).name.lower()
        in {
            "readme.md",
            "readme",
            "pyproject.toml",
            "package.json",
            "cargo.toml",
            "go.mod",
            "requirements.txt",
            "makefile",
            "justfile",
        }
    ]
    budget = max_bytes
    for name in interesting:
        path = repo / name
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        snippet = truncate(text, min(3000, budget))
        budget -= len(snippet)
        lines.append(f"\n--- {name} ---\n{snippet}")
        if budget <= 0:
            break
    return "\n".join(lines)


def heuristic_diagnosis(
    objective: Objective,
    repo: Path,
    diagnostics: list[RunResult],
    environment_text: str,
) -> list[Problem]:
    """Fallback diagnosis when no LLM is available. Uses only real observations."""
    problems: list[Problem] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"P{counter:03d}"

    def add(
        description: str,
        evidence: list[str],
        severity: float,
        candidates: list[CandidateSolution],
    ) -> None:
        problems.append(
            Problem(
                problem_id=next_id(),
                description=description,
                profiling_evidence=evidence,
                severity=severity,
                evidence_ids=evidence,
                candidate_solutions=candidates,
            )
        )

    for index, run in enumerate(diagnostics):
        command = objective.diagnostics[index].command
        evidence_id = f"E-diagnostic-{index + 1}"
        if run.timed_out:
            add(
                f"Diagnostic timed out: {command}",
                [evidence_id],
                0.8,
                [
                    CandidateSolution(
                        f"C{len(problems) + 1:03d}",
                        f"Bound or optimize the slow diagnostic: {command}",
                        0.5,
                        rationale="A timeout indicates unbounded work or I/O starvation.",
                    )
                ],
            )
        elif run.exit_code != 0:
            tail = (run.stderr or run.stdout).strip().splitlines()[-1:] or ["no output"]
            add(
                f"Diagnostic fails: {command} ({tail[0][:200]})",
                [evidence_id],
                0.9,
                [
                    CandidateSolution(
                        f"C{len(problems) + 1:03d}",
                        f"Repair the failing diagnostic: {command}",
                        0.6,
                        rationale="A red diagnostic blocks reliable measurement.",
                    )
                ],
            )
        elif run.duration_seconds > 5.0:
            add(
                f"Diagnostic is slow ({run.duration_seconds:.1f}s): {command}",
                [evidence_id],
                0.6,
                [
                    CandidateSolution(
                        f"C{len(problems) + 1:03d}",
                        f"Profile and reduce runtime of: {command}",
                        0.5,
                        rationale="Wall-clock cost limits iteration throughput.",
                    )
                ],
            )

    todo_count = 0
    large_files: list[tuple[str, int]] = []
    for path in _git(repo, "ls-files").splitlines():
        full = repo / path
        if full.suffix not in {".py", ".js", ".ts", ".rs", ".go", ".java", ".md", ".json"}:
            continue
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        todo_count += len(re.findall(r"\b(TODO|FIXME|XXX|HACK)\b", text))
        line_count = text.count("\n") + 1
        if line_count > 600:
            large_files.append((path, line_count))

    if todo_count >= 5:
        add(
            f"Codebase contains {todo_count} TODO/FIXME markers",
            ["E-environment"],
            0.3,
            [
                CandidateSolution(
                    f"C{len(problems) + 1:03d}",
                    "Triage and resolve the highest-impact TODO/FIXME markers",
                    0.35,
                    rationale="Accumulated deferred work hides known defects.",
                )
            ],
        )

    for path, line_count in large_files[:2]:
        add(
            f"Large file may concentrate complexity: {path} ({line_count} lines)",
            ["E-environment"],
            0.4,
            [
                CandidateSolution(
                    f"C{len(problems) + 1:03d}",
                    f"Extract cohesive units from {path}",
                    0.4,
                    rationale="Large files are harder to test and change safely.",
                )
            ],
        )

    if objective.success.target is not None:
        add(
            f"Baseline does not reach the target for {objective.success.metric}",
            ["E-environment"],
            0.7,
            [
                CandidateSolution(
                    f"C{len(problems) + 1:03d}",
                    f"Improve {objective.success.metric} toward {objective.success.target}",
                    0.5,
                    rationale="The measurable success target is still unmet.",
                )
            ],
        )

    if not problems:
        add(
            "No diagnostic bottleneck was detected; the objective still needs improvement",
            ["E-environment"],
            0.5,
            [
                CandidateSolution(
                    f"C{len(problems) + 1:03d}",
                    f"Run targeted experiments to improve {objective.success.metric}",
                    0.4,
                    rationale="Absence of diagnostic failures does not imply target success.",
                )
            ],
        )
    return problems


def evidence_text(evidence: list[Evidence], diagnostic_text: str, environment_text: str) -> str:
    parts = [environment_text]
    if diagnostic_text:
        parts.append(diagnostic_text)
    for item in evidence:
        if isinstance(item, dict):
            parts.append(f"- {item.get('evidence_id')} [{item.get('kind')}] {item.get('summary')}")
        else:
            parts.append(f"- {item.evidence_id} [{item.kind}] {item.summary}")
    return "\n\n".join(parts)


def evidence_to_dicts(items: list[Evidence]) -> list[dict[str, Any]]:
    return [item.to_dict() for item in items]
