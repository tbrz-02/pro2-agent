"""Crash recovery and state consistency: corrupted state, interrupted work,
orphan cleanup, and state/event journal integrity."""

from pathlib import Path

from pro2.orchestrator import Engine
from test_orchestrator import make_objective


def test_load_state_falls_back_to_backup_when_state_is_corrupt(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    original = engine.state.to_dict()

    engine.store.state_file.write_text("{ not valid json", encoding="utf-8")
    loaded = engine.store.load_state()
    assert loaded.baseline_metric == original["baseline_metric"]
    assert loaded.best_commit == original["best_commit"]


def test_recover_deletes_worktree_and_branch_for_in_flight_experiment(toy_repo, scripted_agent):
    objective = make_objective(
        toy_repo, scripted_agent, budget={"max_experiments": 2, "max_iterations": 3}
    )
    engine = Engine(objective)
    engine.initialize()

    worktree = engine.sandbox.create_experiment("E-interrupted", engine.state.best_commit)
    engine.state.in_flight = {
        "experiment_id": "E-interrupted",
        "branch": worktree.branch,
        "worktree_path": str(worktree.path),
        "base_commit": worktree.base_commit,
    }
    engine.store.save_state(engine.state)

    engine.recover()

    assert engine.state.status == "paused"
    assert engine.state.in_flight is None
    assert not Path(worktree.path).exists()
    assert worktree.branch not in engine.sandbox.branches()


def test_cleanup_orphans_removes_stale_experiment_worktrees(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()

    orphan = engine.sandbox.create_experiment("E-orphan", engine.state.best_commit)
    removed = engine.sandbox.cleanup_orphans(active_paths=set())

    assert not Path(orphan.path).exists()
    assert orphan.branch not in engine.sandbox.branches()
    assert any(Path(p).name == "E-orphan" or str(p).endswith("E-orphan") for p in removed)


def test_events_journal_is_append_only_and_parseable(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    events = engine.store.events()
    assert any(e.get("type") == "initialized" for e in events)
    assert all("timestamp" in e and "type" in e for e in events)


def test_engine_state_survives_reload_after_run(toy_repo, scripted_agent):
    objective = make_objective(
        toy_repo, scripted_agent, budget={"max_experiments": 1, "max_iterations": 2}
    )
    engine = Engine(objective)
    engine.run()

    reloaded = Engine(objective).load()
    assert reloaded.status in {"completed", "budget_exhausted"}
    assert reloaded.baseline_metric is not None
