"""Review governance hardening: approval records, escalation, fail-closed."""

from pro2.orchestrator import Engine
from test_orchestrator import make_objective


def test_approval_records_journaled_on_accepted_experiment(toy_repo, scripted_agent):
    objective = make_objective(
        toy_repo,
        scripted_agent,
        budget={"max_experiments": 1, "max_iterations": 2, "max_wall_seconds": 120},
    )
    engine = Engine(objective)
    state = engine.run()
    assert state.experiments
    experiment = state.experiments[0]
    assert experiment.reviewer_id or experiment.reviewer_source

    records = engine.approval_records()
    assert records
    assert records[0]["experiment_id"] == experiment.experiment_id
    assert "diff_sha256" in records[0]
    assert engine.state.metadata.get("last_approval", {}).get("experiment_id")


def test_escalation_threshold_initially_none(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    assert engine._escalation_threshold_reached() is None
    assert engine.state.escalations == []


def test_escalation_recorded_when_threshold_reached(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.state.reviewer_unavailable = engine.MAX_REVIEWER_UNAVAILABLE
    reason = engine._escalation_threshold_reached()
    assert reason and "reviewer" in reason
    engine._escalate(reason)
    assert engine.state.escalations[-1]["reason"] == reason
    assert engine.store.events()[-1]["type"] == "escalation"


def test_experiment_reviewer_metadata_roundtrip(toy_repo, scripted_agent):
    objective = make_objective(
        toy_repo, scripted_agent, budget={"max_experiments": 1, "max_iterations": 2}
    )
    engine = Engine(objective)
    engine.run()
    experiment = engine.state.experiments[0]
    reloaded = engine.state.to_dict()
    assert reloaded["experiments"][0]["reviewer_id"] == experiment.reviewer_id
    assert reloaded["experiments"][0]["review_digest"] == experiment.review_digest
