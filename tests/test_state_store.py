import json

import pytest

from pro2.state import EngineState, StateLockedError, StateStore


def test_state_store_roundtrip(tmp_path):
    store = StateStore(tmp_path / ".pro2")
    state = EngineState(objective_name="toy")
    state.iteration = 3
    state.best_commit = "abc123"

    store.save_state(state)
    loaded = store.load_state()

    assert loaded.iteration == 3
    assert loaded.best_commit == "abc123"
    assert loaded.objective_name == "toy"


def test_state_store_writes_backup(tmp_path):
    store = StateStore(tmp_path / ".pro2")
    store.save_state(EngineState(objective_name="one"))
    store.save_state(EngineState(objective_name="two"))

    assert (tmp_path / ".pro2" / "state.json").exists()
    assert (tmp_path / ".pro2" / "state.json.bak").exists()


def test_state_store_appends_events(tmp_path):
    store = StateStore(tmp_path / ".pro2")
    store.append_event("experiment_started", experiment_id="E1")
    store.append_event("experiment_finished", experiment_id="E1", accepted=True)

    lines = (tmp_path / ".pro2" / "events.jsonl").read_text().splitlines()

    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "experiment_started"
    assert json.loads(lines[1])["accepted"] is True


def test_lock_is_exclusive(tmp_path):
    store = StateStore(tmp_path / ".pro2")
    with store.lock():
        with pytest.raises(StateLockedError):
            with store.lock():
                pass


def test_corrupt_state_falls_back_to_backup(tmp_path):
    store = StateStore(tmp_path / ".pro2")
    store.save_state(EngineState(objective_name="one"))
    store.save_state(EngineState(objective_name="two"))
    (tmp_path / ".pro2" / "state.json").write_text("{not json")

    loaded = store.load_state()

    assert loaded.objective_name in {"one", "two"}


def test_state_serializes_problems_and_hypotheses(tmp_path):
    from pro2.candidate_solution import CandidateSolution
    from pro2.hypothesis import Hypothesis
    from pro2.problem import Problem

    state = EngineState(objective_name="toy")
    problem = Problem("P001", "slow")
    problem.add_candidate(CandidateSolution("C001", "cache", 0.7))
    state.add_problem(problem)
    state.add_hypothesis(Hypothesis("P001", "C001", "add cache", hypothesis_id="H1"))

    store = StateStore(tmp_path / ".pro2")
    store.save_state(state)
    loaded = store.load_state()

    assert loaded.get_problem("P001").candidate_solutions[0].candidate_id == "C001"
    assert loaded.hypotheses[0].hypothesis_id == "H1"
