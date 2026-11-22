"""Command-line interface for the Pro² research engine."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from pro2.config import Objective, load_objective
from pro2.experience import ExperienceBank
from pro2.orchestrator import Engine
from pro2.security import detect_backends, select_backend
from pro2.util import truncate


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default="pro2.json",
        help="path to the objective config (default: pro2.json)",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="override the objective state directory",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pro2",
        description="Autonomous technical research and innovation engine",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    _add_common(sub.add_parser("init", help="measure the baseline and build a problem state"))
    _add_common(sub.add_parser("run", help="run the autonomous loop to budget exhaustion"))
    _add_common(sub.add_parser("resume", help="recover an interrupted run and continue"))
    _add_common(sub.add_parser("status", help="print the current engine state"))
    _add_common(sub.add_parser("inspect", help="print a detailed state snapshot"))
    _add_common(sub.add_parser("experiments", help="list experiments"))
    _add_common(sub.add_parser("experience", help="list Experience Bank entries"))
    _add_common(sub.add_parser("research", help="list collected research findings"))
    _add_common(sub.add_parser("doctor", help="check tools, config and providers"))
    _add_common(sub.add_parser("report", help="write and print the research report"))
    _add_common(sub.add_parser("pause", help="request a graceful pause"))
    _add_common(sub.add_parser("clean", help="remove orphaned experiment worktrees"))
    return parser


def _load_objective(args: argparse.Namespace) -> Objective:
    objective = load_objective(args.config)
    if args.state_dir:
        objective.state_dir = args.state_dir
    return objective


def _make_engine(args: argparse.Namespace) -> Engine:
    objective = _load_objective(args)
    engine = Engine(objective)
    config_path = Path(args.config).resolve()
    try:
        relative = config_path.relative_to(engine.repo)
    except ValueError:
        relative = None
    if relative is not None:
        engine.sandbox.ensure_excluded(str(relative))
    return engine


def _cmd_init(args: argparse.Namespace) -> int:
    engine = _make_engine(args)
    state = engine.initialize()
    print(
        f"initialized '{engine.objective.name}': baseline {state.baseline_metric} "
        f"@ {state.baseline_commit[:12]}"
    )
    print(
        f"problems={len(state.identified_problems)} hypotheses={len(state.hypotheses)} "
        f"provider={state.metadata.get('provider', 'unknown')}"
    )
    return 0


def _load_engine(args: argparse.Namespace) -> Engine:
    engine = _make_engine(args)
    engine.load()
    return engine


def _cmd_run(args: argparse.Namespace) -> int:
    engine = _make_engine(args)
    try:
        engine.load()
    except FileNotFoundError:
        engine.initialize()
    state = engine.run()
    print(
        f"run finished: status={state.status} "
        f"experiments={len(state.experiments)} best={state.best_metric}"
    )
    if state.stop_reason:
        print(f"stop reason: {state.stop_reason}")
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    engine = _load_engine(args)
    engine.recover()
    state = engine.run()
    print(
        f"resumed: status={state.status} experiments={len(state.experiments)} "
        f"best={state.best_metric}"
    )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    engine = _load_engine(args)
    state = engine.state
    print(f"objective: {engine.objective.name}")
    print(f"status: {state.status}  phase: {state.phase}")
    print(f"baseline: {state.baseline_metric} @ {state.baseline_commit}")
    print(f"best: {state.best_metric} @ {state.best_commit}")
    print(f"iteration: {state.iteration}  experiments: {len(state.experiments)}")
    print(f"budget used: {state.budget_used}")
    if state.stop_reason:
        print(f"stop reason: {state.stop_reason}")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    engine = _load_engine(args)
    state = engine.state
    print(engine.status_summary())
    print("")
    for problem in state.identified_problems:
        print(f"{problem.problem_id}: {problem.description} (severity {problem.severity})")
        for candidate in problem.candidate_solutions:
            print(
                f"  {candidate.candidate_id} belief={candidate.belief:.3f} {candidate.description}"
            )
    print("")
    for hypothesis in state.hypotheses:
        print(
            f"{hypothesis.hypothesis_id} [{hypothesis.status}] {hypothesis.title} "
            f"(attempts={hypothesis.attempts})"
        )
    return 0


def _cmd_experiments(args: argparse.Namespace) -> int:
    engine = _load_engine(args)
    experiments = engine.state.experiments
    if not experiments:
        print("no experiments recorded")
        return 0
    print("experiments:")
    for experiment in experiments:
        print(
            f"- {experiment.experiment_id} {experiment.status} "
            f"metric={experiment.candidate_metric} delta={experiment.delta} "
            f"reason={truncate(experiment.reason, 160)}"
        )
    return 0


def _cmd_experience(args: argparse.Namespace) -> int:
    engine = _make_engine(args)
    bank = ExperienceBank(engine.store.experience_file, engine.objective.experience.max_entries)
    entries = bank.all()
    if not entries:
        print("Experience Bank is empty")
        return 0
    for entry in entries:
        print(f"- [{entry.kind}] {entry.signature} -> {entry.outcome}")
    return 0


def _cmd_research(args: argparse.Namespace) -> int:
    engine = _load_engine(args)
    findings = engine.state.research_findings
    if not findings:
        print("no research findings recorded")
        return 0
    for finding in findings:
        print(f"- [{finding.get('source')}] {finding.get('title')} - {finding.get('url')}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    engine = _make_engine(args)
    objective = engine.objective
    print(f"python: {sys.version.splitlines()[0]}")
    print(f"executable: {sys.executable}")
    for tool in ("git", "uv", "opencode", "claude", "docker", "rg"):
        path = shutil.which(tool)
        print(f"{tool}: {path or 'missing'}")
    print(f"repository: {engine.repo}")
    print(f"state dir: {engine.state_dir}")
    print(f"baseline command: {objective.baseline.command}")
    print(f"verify command: {objective.verify.command}")
    print(f"tests: {len(objective.tests)}  diagnostics: {len(objective.diagnostics)}")
    try:
        print(f"agent provider: {engine.agent.resolve_provider()}")
        print(f"available providers: {engine.agent.available_providers()}")
    except Exception as exc:  # doctor must report, not crash
        print(f"agent provider: error ({exc})")
    backends = {k: v for k, v in detect_backends().items() if v}
    print(
        f"isolation: enabled={objective.security.enabled} "
        f"backend={select_backend(objective.security)} available={sorted(backends)}"
    )
    print(f"state file: {'present' if engine.store.state_file.exists() else 'missing'}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    engine = _load_engine(args)
    path = engine.write_report()
    print(f"report written: {path}")
    return 0


def _cmd_pause(args: argparse.Namespace) -> int:
    engine = _make_engine(args)
    engine.store.request_pause()
    print(f"pause requested: {engine.store.pause_file}")
    return 0


def _cmd_clean(args: argparse.Namespace) -> int:
    engine = _make_engine(args)
    removed = engine.sandbox.cleanup_orphans(active_paths=set())
    print(f"removed {len(removed)} orphaned worktrees/branches")
    for item in removed:
        print(f"- {item}")
    return 0


COMMANDS = {
    "init": _cmd_init,
    "run": _cmd_run,
    "resume": _cmd_resume,
    "status": _cmd_status,
    "inspect": _cmd_inspect,
    "experiments": _cmd_experiments,
    "experience": _cmd_experience,
    "research": _cmd_research,
    "doctor": _cmd_doctor,
    "report": _cmd_report,
    "pause": _cmd_pause,
    "clean": _cmd_clean,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = COMMANDS[args.command]
    try:
        return handler(args)
    except Exception as exc:
        print(f"pro2 {args.command}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
