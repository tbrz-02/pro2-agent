"""PI supervision governance: fail-closed on unavailable / ambiguous PI output."""

from pro2.agents import AgentError
from pro2.orchestrator import Engine
from test_orchestrator import make_objective


class _FailingAgentResult:
    """Stub agent whose ask_json always raises AgentError (unavailable)."""

    def ask_json(self, *args, **kwargs):
        raise AgentError("agent down")


def test_supervise_counts_pi_unavailable(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    engine.agent = _FailingAgentResult()
    assert engine.supervise() is True
    assert engine.state.pi_unavailable == 1
    # Escalation threshold: MAX_PI_MALFORMED (3) repeated unavailability.
    engine.state.pi_unavailable = 3
    assert engine._escalation_threshold_reached() is not None
    assert "PI supervisor unavailable" in engine._escalation_threshold_reached()


def test_escalation_below_threshold_returns_none(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    assert engine._escalation_threshold_reached() is None


def test_escalation_records_event(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    engine.agent = _FailingAgentResult()
    engine.supervise()
    events = [e for e in engine.store.events() if e.get("type") == "escalation"]
    assert events


def test_state_roundtrips_pi_unavailable(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    engine.state.pi_unavailable = 2
    engine.store.save_state(engine.state)
    engine.load()
    assert engine.state.pi_unavailable == 2
    assert engine._escalation_threshold_reached() is None


def test_recover_marks_interrupted_engineering_trials_and_nodes(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    state = engine.state
    state.phase = "engineering"
    state.engineering = {
        "nodes": [
            {
                "node_id": "N-running",
                "parent_id": None,
                "config": {},
                "visits": 0,
                "reward_sum": 0.0,
                "status": "running",
            },
            {
                "node_id": "N-pending",
                "parent_id": None,
                "config": {},
                "visits": 0,
                "reward_sum": 0.0,
                "status": "pending",
            },
        ]
    }
    state.engineering_trials = [
        {"trial_id": "ENG-1", "node_id": "N-running", "status": "running"},
        {"trial_id": "ENG-2", "node_id": "N-pending", "status": "pending"},
    ]
    state.in_flight = {
        "experiment_id": "E-engineering",
        "worktree_path": str(toy_repo / "nonexistent-worktree"),
        "branch": "pro2/exp/nonexistent",
        "base_commit": state.best_commit,
    }
    engine.store.save_state(state)

    engine.recover()

    trials = {t["trial_id"]: t["status"] for t in state.engineering_trials}
    assert trials["ENG-1"] == "interrupted"
    assert trials["ENG-2"] == "interrupted"
    nodes = {n["node_id"]: n["status"] for n in state.engineering["nodes"]}
    assert nodes["N-running"] == "interrupted"
    assert nodes["N-pending"] == "interrupted"
    assert state.in_flight is None
    events = [e["type"] for e in engine.store.events()]
    assert "engineering_trials_interrupted" in events
    assert "recovered" in events


def test_run_during_engineering_resumes_engineering_loop(toy_repo, scripted_agent, monkeypatch):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.initialize()
    engine.state.phase = "engineering"
    engine.state.best_commit = engine.state.baseline_commit

    calls = []

    def fake_run_engineering():
        calls.append(1)
        engine.state.phase = "done"

    monkeypatch.setattr(engine, "run_engineering", fake_run_engineering)
    state = engine.run()
    assert calls == [1]
    assert state.phase == "done"
