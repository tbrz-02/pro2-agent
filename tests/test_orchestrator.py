import json
import sys
from pathlib import Path

import pytest

from pro2.config import Objective
from pro2.experiment import Experiment
from pro2.orchestrator import Engine


def objective_dict(repo, agent_cmd, **overrides):
    cfg = {
        "name": "toy",
        "description": "Improve the toy score",
        "repo": str(repo),
        "success": {"metric": "score", "direction": "maximize", "min_delta": 0.5},
        "baseline": {"command": f"{sys.executable} run.py", "metric_regex": r"SCORE=([0-9.]+)"},
        "verify": {"command": f"{sys.executable} run.py", "metric_regex": r"SCORE=([0-9.]+)"},
        "diagnostics": [],
        "tests": [],
        "budget": {"max_experiments": 1, "max_wall_seconds": 120, "max_experiment_seconds": 60},
        "research": {"enabled": False},
        "agent": {"provider": "command", "command": agent_cmd, "timeout_seconds": 30},
    }
    cfg.update(overrides)
    return cfg


def make_objective(repo, agent_cmd, **overrides):
    return Objective.from_dict(objective_dict(repo, agent_cmd, **overrides))


def test_initialize_collects_baseline_and_problems(toy_repo, scripted_agent, tmp_path):
    engine = Engine(make_objective(toy_repo, scripted_agent))

    state = engine.initialize()

    assert state.baseline_metric == 1.0
    assert state.baseline_commit
    assert state.best_commit == state.baseline_commit
    assert state.identified_problems
    assert state.hypotheses
    assert state.status == "initialized"


def test_engine_accepts_verified_improvement(toy_repo, scripted_agent, tmp_path):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()

    experiment = engine.step()

    assert experiment is not None
    assert experiment.accepted is True
    assert experiment.candidate_metric == 2.0
    assert engine.state.best_commit == experiment.commit
    assert engine.state.best_commit != engine.state.baseline_commit
    assert engine.state.experiments[0].experiment_id == experiment.experiment_id
    assert engine.state.identified_problems[0].candidate_solutions[0].belief > 0.6


def test_engine_rejects_no_improvement_and_rolls_back(toy_repo, scripted_agent, tmp_path):
    seed = {
        "seed_problems": [
            {
                "problem_id": "P001",
                "description": "score is fixed",
                "candidates": [
                    {
                        "candidate_id": "C001",
                        "description": "set score",
                        "belief": 0.7,
                    }
                ],
            }
        ],
        "seed_hypotheses": [
            {
                "hypothesis_id": "H001",
                "problem_id": "P001",
                "candidate_id": "C001",
                "title": "no-op",
                "modification": "set the same score",
                "instructions": "SCORE_TARGET=1.0",
                "target_delta": 0.5,
            }
        ],
    }
    engine = Engine(make_objective(toy_repo, scripted_agent, **seed))
    engine.initialize()
    baseline = engine.state.best_commit

    experiment = engine.step()

    assert experiment is not None
    assert experiment.accepted is False
    assert engine.state.best_commit == baseline
    assert experiment.worktree_path and not Path(experiment.worktree_path).exists()
    assert engine.state.identified_problems[0].candidate_solutions[0].belief < 0.7


def test_engine_stops_at_experiment_budget(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()

    engine.run()

    assert len(engine.state.experiments) == 1
    assert engine.state.status in {"completed", "budget_exhausted"}
    assert engine.state.stop_reason


def test_engine_persists_state_across_instances(toy_repo, scripted_agent):
    objective = make_objective(toy_repo, scripted_agent)
    engine = Engine(objective)
    engine.initialize()
    engine.step()
    best = engine.state.best_commit

    engine2 = Engine(objective)
    engine2.load()

    assert engine2.state.best_commit == best
    assert len(engine2.state.experiments) == 1


def test_engine_recovery_cleans_interrupted_experiment(toy_repo, scripted_agent):
    objective = make_objective(toy_repo, scripted_agent)
    engine = Engine(objective)
    engine.initialize()

    wt = engine.sandbox.create_experiment("E-crash", engine.state.best_commit)
    experiment = Experiment(
        experiment_id="E-crash",
        hypothesis_id="H-crash",
        problem_id="P001",
        candidate_id="C001",
        branch=wt.branch,
        worktree_path=str(wt.path),
        base_commit=wt.base_commit,
        status="running",
    )
    engine.state.experiments.append(experiment)
    engine.state.in_flight = experiment.to_dict()
    engine.state.status = "running"
    engine.store.save_state(engine.state)

    engine.recover()

    assert engine.state.in_flight is None
    assert not wt.path.exists()
    assert engine.state.experiments[-1].status == "interrupted"
    assert engine.state.status == "paused"


def test_engine_writes_report(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    engine.step()

    path = engine.write_report()

    text = Path(path).read_text()
    assert "Pro2 Research Report" in text
    assert "toy" in text
    assert "accepted" in text.lower()


def test_engine_refuses_dirty_repository(toy_repo, scripted_agent):
    (toy_repo / "dirty.txt").write_text("x")
    engine = Engine(make_objective(toy_repo, scripted_agent))

    with pytest.raises(RuntimeError, match="clean"):
        engine.initialize()


def test_engine_records_events(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    engine.step()

    events = (Path(engine.state_dir) / "events.jsonl").read_text().splitlines()
    types = [json.loads(line)["type"] for line in events]

    assert "baseline_measured" in types
    assert "experiment_started" in types
    assert "experiment_finished" in types


def test_engine_experience_bank_records_outcome(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    engine.step()

    assert len(engine.experience.all()) >= 1


def test_engine_runs_engineering_phase_after_science(toy_repo, scripted_agent):
    seed = {
        "seed_problems": [
            {
                "problem_id": "P001",
                "description": "score is fixed",
                "candidates": [{"candidate_id": "C001", "description": "set score", "belief": 0.7}],
            }
        ],
        "seed_hypotheses": [
            {
                "hypothesis_id": "H001",
                "problem_id": "P001",
                "candidate_id": "C001",
                "title": "no-op",
                "modification": "set the same score",
                "instructions": "SCORE_TARGET=1.0",
                "target_delta": 0.5,
            }
        ],
    }
    objective = make_objective(
        toy_repo,
        scripted_agent,
        budget={"max_experiments": 3, "max_iterations": 5, "max_wall_seconds": 120},
        engineering={
            "enabled": True,
            "max_nodes": 1,
            "params": [{"path": "config.json", "key": "score", "values": [3.0], "adapter": "json"}],
        },
        **seed,
    )
    engine = Engine(objective)
    engine.initialize()

    state = engine.run()

    assert state.phase == "done"
    assert len(state.engineering_trials) == 1
    assert state.engineering_trials[0]["status"] == "completed"
    assert state.best_metric == 3.0
    assert state.engineering_trials[0]["reward"] is not None
