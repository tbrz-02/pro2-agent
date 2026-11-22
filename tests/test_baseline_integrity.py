"""Baseline integrity: persisted root metric, independent measurement, best merged."""

import subprocess

import pytest

from pro2.orchestrator import Engine
from test_orchestrator import make_objective


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_engineering_persists_root_metric_and_merges_best_to_primary(
    toy_repo, scripted_agent
):
    objective = make_objective(
        toy_repo,
        scripted_agent,
        budget={"max_experiments": 3, "max_wall_seconds": 120},
        engineering={
            "enabled": True,
            "max_nodes": 1,
            "params": [{"path": "config.json", "key": "score", "values": [3.0]}],
        },
    )
    engine = Engine(objective)
    engine.initialize()

    # The scripted agent implements score=2.0; the science phase accepts it.
    experiment = engine.step()
    assert experiment is not None and experiment.accepted
    assert engine.state.best_metric == 2.0

    engine.run_engineering()
    state = engine.state

    assert state.engineering["root_metric"] == 2.0
    trial = state.engineering_trials[0]
    assert trial["status"] == "completed"
    # Independent measurement against the persisted root metric (3.0 - 2.0).
    assert trial["delta"] == pytest.approx(1.0)
    assert state.best_metric == 3.0

    # The highest-validation engineering trajectory is merged into the primary
    # branch as the final reference model.
    head = git(toy_repo, "rev-parse", "HEAD")
    rc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", state.best_commit, head], cwd=toy_repo
    ).returncode
    assert rc == 0


def test_engineering_trials_fail_when_protected_path_changes(
    toy_repo, scripted_agent
):
    objective = make_objective(
        toy_repo,
        scripted_agent,
        budget={"max_experiments": 3, "max_wall_seconds": 120},
        engineering={
            "enabled": True,
            "max_nodes": 1,
            "params": [{"path": "config.json", "key": "score", "values": [3.0]}],
            "protected_paths": ["config.json"],
        },
    )
    engine = Engine(objective)
    engine.initialize()
    engine.step()

    engine.run_engineering()

    trial = engine.state.engineering_trials[0]
    assert trial["status"] == "rejected"
    assert "config.json" in trial["reason"]
