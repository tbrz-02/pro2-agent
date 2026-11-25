"""Pro² Three-State Problem taxonomy: Resolved / Resource-Limited / Unresolved."""

import pytest

from pro2.orchestrator import Engine
from pro2.problem import PROBLEM_STATUSES, Problem, _migrate_status
from pro2.problem_state import ProblemState
from pro2.records import Experiment
from pro2.state import EngineState
from test_orchestrator import make_objective


def test_problem_starts_unresolved_by_default():
    problem = Problem("P001", "A performance problem")
    assert problem.status == "unresolved"


def test_problem_set_status_records_transition_reason():
    problem = Problem("P001", "A performance problem")
    changed = problem.set_status("resolved", reason="candidate accepted", evidence="E-1")
    assert changed is True
    assert problem.status == "resolved"
    assert problem.status_reasons == [
        {
            "from": "unresolved",
            "to": "resolved",
            "reason": "candidate accepted",
            "evidence": "E-1",
            "at": problem.status_reasons[0]["at"],
        }
    ]


def test_problem_set_status_rejects_unknown_state():
    problem = Problem("P001", "A performance problem")
    with pytest.raises(ValueError):
        problem.set_status("bogus", "nope")


def test_problem_set_status_terminal_resolved():
    problem = Problem("P001", "A performance problem")
    problem.set_status("resolved", reason="accepted")
    assert problem.set_status("resource_limited", reason="late") is False
    assert problem.status == "resolved"


def test_problem_set_status_resource_limited_then_resolved():
    problem = Problem("P001", "A performance problem")
    assert problem.set_status("resource_limited", reason="budget") is True
    assert problem.set_status("resolved", reason="accepted later") is True
    assert problem.status == "resolved"
    assert [r["to"] for r in problem.status_reasons] == ["resource_limited", "resolved"]


def test_problem_status_roundtrips_through_dict():
    problem = Problem("P001", "A performance problem")
    problem.set_status("resource_limited", reason="budget exhausted")
    restored = Problem.from_dict(problem.to_dict())
    assert restored.status == "resource_limited"
    assert [r["to"] for r in restored.status_reasons] == ["resource_limited"]


def test_problem_state_set_problem_status_and_transitions():
    state = ProblemState()
    state.add_problem(Problem("P001", "A performance problem"))
    state.set_problem_status("P001", "resolved", reason="done")
    problem = state.get_problem("P001")
    assert problem.status == "resolved"
    transitions = state.problem_transitions()
    assert transitions[0]["problem_id"] == "P001"
    assert transitions[0]["to"] == "resolved"
    with pytest.raises(ValueError):
        state.set_problem_status("UNKNOWN", "resolved", reason="nope")


def test_migrate_status_maps_legacy_open_to_unresolved():
    assert _migrate_status("open") == "unresolved"
    assert _migrate_status("resolved") == "resolved"
    assert _migrate_status("weird") == "unresolved"


def test_engine_state_roundtrips_problem_transitions():
    state = EngineState()
    state.add_problem(Problem("P001", "A performance problem"))
    state.set_problem_status("P001", "resolved", reason="verified")
    restored = EngineState.from_dict(state.to_dict())
    assert restored.get_problem("P001").status == "resolved"
    assert restored.problem_transitions()[0]["problem_id"] == "P001"


def test_engine_step_marks_problem_resolved_on_accepted_candidate(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    experiment = engine.step()
    assert experiment is not None
    assert experiment.accepted is True
    problem = engine.state.get_problem(experiment.problem_id)
    assert problem.status == "resolved"
    transitions = engine.state.problem_transitions()
    assert any(t["problem_id"] == problem.problem_id and t["to"] == "resolved" for t in transitions)
    events = [e for e in engine.store.events() if e.get("type") == "problem_transition"]
    assert any(e.get("problem_id") == problem.problem_id for e in events)


def test_engine_settle_problems_marks_resource_limited_at_budget(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent, budget={"max_experiments": 0}))
    engine.initialize()
    engine.state.stop_reason = "experiment budget exhausted (0)"
    engine._settle_problems()
    assert all(p.status == "resource_limited" for p in engine.state.identified_problems)


def test_engine_runaway_check_marks_resource_limited_on_failed_experiment(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    problem = engine.state.identified_problems[0]
    experiment = Experiment(
        experiment_id="E-test",
        hypothesis_id="H-x",
        problem_id=problem.problem_id,
        candidate_id="C001",
        status="failed",
        reason="stalled",
        duration_seconds=engine.objective.budget.max_experiment_seconds + 1,
    )
    engine.state.add_experiment(experiment)
    engine._runaway_check(experiment)
    assert problem.status == "resource_limited"


def test_problem_statuses_tuple_is_three_state():
    assert PROBLEM_STATUSES == ("unresolved", "resource_limited", "resolved")
