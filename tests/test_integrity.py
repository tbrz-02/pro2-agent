"""Experimental integrity: pinned evaluator, lineage, invariants."""

import pytest

from pro2.config import MetricSpec
from pro2.git_sandbox import GitSandbox
from pro2.integrity import (
    assert_snapshot_integrity,
    evaluator_mismatch,
    lineage,
    pin_evaluator,
    snapshot_digest,
)
from pro2.orchestrator import Engine
from test_orchestrator import make_objective


def test_pin_evaluator_stable_and_changes_with_spec(toy_repo, scripted_agent):
    objective = make_objective(toy_repo, scripted_agent)
    pinned = pin_evaluator(objective)
    assert pinned == pin_evaluator(objective)

    objective.verify = MetricSpec(command="echo SCORE=9", metric_regex=r"SCORE=([0-9.]+)")
    assert evaluator_mismatch(objective, pinned) is not None
    assert evaluator_mismatch(objective, pin_evaluator(objective)) is None


def test_assert_snapshot_integrity_resolves_real_commits(toy_repo, scripted_agent):
    sandbox = GitSandbox(toy_repo, toy_repo / ".pro2" / "worktrees", toy_repo / ".pro2" / "verify")
    digest = assert_snapshot_integrity(sandbox, sandbox.head())
    assert digest == snapshot_digest(sandbox.head())
    with pytest.raises(RuntimeError):
        assert_snapshot_integrity(sandbox, "0" * 40)


def test_lineage_chain(toy_repo, scripted_agent):
    assert lineage("abc", None) == ["abc"]
    assert lineage("abc", "abc") == ["abc"]
    assert lineage("abc", "def") == ["abc", "def"]


def test_engine_pins_evaluator_at_initialize(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    assert engine.state.metadata["evaluator_digest"] == pin_evaluator(engine.objective)


def test_experiment_records_lineage_and_snapshot(toy_repo, scripted_agent):
    objective = make_objective(
        toy_repo,
        scripted_agent,
        budget={"max_experiments": 1, "max_iterations": 2, "max_wall_seconds": 120},
    )
    engine = Engine(objective)
    engine.run()
    assert engine.state.experiments
    experiment = engine.state.experiments[0]
    assert experiment.lineage
    assert experiment.lineage[0] == experiment.base_commit
    assert experiment.snapshot_digest == snapshot_digest(experiment.base_commit)


def test_engine_assert_invariants_rejects_bad_state(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    engine.state.best_commit = "0" * 40
    with pytest.raises(RuntimeError):
        engine._assert_invariants()
    engine.state.best_commit = engine.sandbox.head()
    engine.state.phase = "bogus"
    with pytest.raises(RuntimeError):
        engine._assert_invariants()
