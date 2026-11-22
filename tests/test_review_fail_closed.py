"""Reviewer fail-closed: malformed, missing, incomplete or failed reviews reject."""

import sys
from types import SimpleNamespace

from pro2.hypothesis import Hypothesis
from pro2.orchestrator import Engine
from test_orchestrator import make_objective


def _stats():
    return SimpleNamespace(changed_paths=["run.py"], added=3, removed=0, patch="", untracked=[])


def _hypothesis():
    return Hypothesis(problem_id="P001", candidate_id="C001", modification="one change")


def _stub_agent(tmp_path, body: str) -> list[str]:
    script = tmp_path / "stub_agent.py"
    script.write_text(body)
    return [sys.executable, str(script)]


def test_review_approves_when_agent_reports_approval(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    review = engine._review(_hypothesis(), _stats(), toy_repo)
    assert review["approved"] is True
    assert review["source"] == "agent"


def test_review_rejects_when_approved_is_missing(toy_repo, scripted_agent, tmp_path):
    cmd = _stub_agent(tmp_path, 'import json,sys\nprint(json.dumps({"reasons": ["x"]}))\n')
    engine = Engine(make_objective(toy_repo, cmd))
    review = engine._review(_hypothesis(), _stats(), toy_repo)
    assert review["approved"] is False


def test_review_rejects_when_agent_reports_not_true(toy_repo, scripted_agent, tmp_path):
    cmd = _stub_agent(
        tmp_path, 'import json,sys\nprint(json.dumps({"approved": "yes"}))\n'
    )
    engine = Engine(make_objective(toy_repo, cmd))
    assert engine._review(_hypothesis(), _stats(), toy_repo)["approved"] is False


def test_review_rejects_when_violations_present(toy_repo, scripted_agent, tmp_path):
    cmd = _stub_agent(
        tmp_path,
        'import json,sys\nprint(json.dumps({"approved": True, "violations": ["touches tests"]}))\n',
    )
    engine = Engine(make_objective(toy_repo, cmd))
    review = engine._review(_hypothesis(), _stats(), toy_repo)
    assert review["approved"] is False
    assert review["violations"] == ["touches tests"]


def test_review_rejects_when_single_variable_not_reported(toy_repo, scripted_agent, tmp_path):
    cmd = _stub_agent(
        tmp_path,
        'import json,sys\nprint(json.dumps({"approved": True, "single_variable": False}))\n',
    )
    engine = Engine(make_objective(toy_repo, cmd))
    assert engine._review(_hypothesis(), _stats(), toy_repo)["approved"] is False


def test_review_rejects_when_reviewer_returns_malformed_payload(
    toy_repo, scripted_agent, tmp_path
):
    cmd = _stub_agent(tmp_path, 'print("definitely not json")\n')
    engine = Engine(make_objective(toy_repo, cmd))
    assert engine._review(_hypothesis(), _stats(), toy_repo)["approved"] is False


def test_review_rejects_when_reviewer_returns_no_payload(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, ["/nonexistent/agent"]))
    assert engine._review(_hypothesis(), _stats(), toy_repo)["approved"] is False


def test_review_hard_constraints_reject_even_when_agent_approves(
    toy_repo, scripted_agent
):
    engine = Engine(
        make_objective(
            toy_repo,
            scripted_agent,
            constraints={"protected_paths": ["run.py"]},
        )
    )
    review = engine._review(_hypothesis(), _stats(), toy_repo)
    assert review["approved"] is False
    assert review["source"] == "heuristic"
    assert any("run.py" in reason for reason in review["reasons"])


def test_review_falls_back_to_heuristic_when_provider_unavailable(
    toy_repo, scripted_agent
):
    objective = make_objective(
        toy_repo, scripted_agent, agent={"provider": "local", "timeout_seconds": 30}
    )
    engine = Engine(objective)
    review = engine._review(_hypothesis(), _stats(), toy_repo)
    assert review["approved"] is True
    assert review["source"] == "heuristic"


def test_review_timeout_is_capped_by_experiment_budget(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    expected = min(
        engine.objective.agent.timeout_seconds,
        engine.objective.budget.max_experiment_seconds,
    )
    assert engine._review_timeout() == expected
