import sys

import pytest

from pro2.config import MetricSpec
from pro2.git_sandbox import GitSandbox
from pro2.verification import compare_metrics, measure_metric, verify_commit


def spec(command, regex=r"SCORE=([0-9.]+)", timeout=30):
    return MetricSpec(command=command, metric_regex=regex, timeout_seconds=timeout)


def test_measure_metric_runs_command(toy_repo, tmp_path):
    measurement = measure_metric(
        spec(f"{sys.executable} run.py"), toy_repo, tmp_path / "art", "baseline"
    )

    assert measurement.value == 1.0
    assert measurement.run.exit_code == 0
    assert measurement.error is None


def test_measure_metric_reports_parse_failure(toy_repo, tmp_path):
    measurement = measure_metric(
        spec(f"{sys.executable} run.py", regex=r"MISSING=([0-9.]+)"),
        toy_repo,
        tmp_path / "art",
        "baseline",
    )

    assert measurement.value is None
    assert "metric" in measurement.error.lower()


def test_compare_accepts_improvement():
    accepted, delta, reason = compare_metrics(
        1.0, 2.0, direction="maximize", min_delta=0.5, target=None
    )

    assert accepted is True
    assert delta == pytest.approx(1.0)
    assert "improved" in reason.lower()


def test_compare_rejects_insufficient_improvement():
    accepted, delta, _ = compare_metrics(1.0, 1.1, direction="maximize", min_delta=0.5, target=None)

    assert accepted is False
    assert delta == pytest.approx(0.1)


def test_compare_honors_absolute_target():
    accepted, _, _ = compare_metrics(1.0, 3.0, direction="maximize", min_delta=0.0, target=4.0)

    assert accepted is False


def test_compare_supports_minimize():
    accepted, delta, _ = compare_metrics(
        10.0, 7.0, direction="minimize", min_delta=1.0, target=None
    )

    assert accepted is True
    assert delta == pytest.approx(3.0)


def test_verify_commit_accepts_real_improvement(toy_repo, tmp_path):
    from pro2.config import Objective

    sandbox = GitSandbox(toy_repo, tmp_path / "wt", tmp_path / "verify")
    wt = sandbox.create_experiment("E1", sandbox.head())
    (wt.path / "config.json").write_text('{"score": 2.0, "work": 1.0}\n')
    commit = sandbox.commit_all(wt.path, "raise")

    objective = Objective.from_dict(
        {
            "name": "toy",
            "description": "toy",
            "repo": str(toy_repo),
            "success": {"metric": "score", "direction": "maximize", "min_delta": 0.5},
            "verify": {"command": f"{sys.executable} run.py", "metric_regex": r"SCORE=([0-9.]+)"},
        }
    )

    result = verify_commit(sandbox, objective, commit, 1.0, "E1", tmp_path / "art")

    assert result.accepted is True
    assert result.candidate_metric == 2.0
    sandbox.remove_experiment(wt)


def test_verify_commit_rejects_unchanged_metric(toy_repo, tmp_path):
    from pro2.config import Objective

    sandbox = GitSandbox(toy_repo, tmp_path / "wt", tmp_path / "verify")
    wt = sandbox.create_experiment("E1", sandbox.head())
    (wt.path / "notes.txt").write_text("no metric change\n")
    commit = sandbox.commit_all(wt.path, "noop")

    objective = Objective.from_dict(
        {
            "name": "toy",
            "description": "toy",
            "repo": str(toy_repo),
            "success": {"metric": "score", "direction": "maximize", "min_delta": 0.5},
            "verify": {"command": f"{sys.executable} run.py", "metric_regex": r"SCORE=([0-9.]+)"},
        }
    )

    result = verify_commit(sandbox, objective, commit, 1.0, "E1", tmp_path / "art")

    assert result.accepted is False
    assert "insufficient" in result.reason.lower() or "improved" in result.reason.lower()
    sandbox.remove_experiment(wt)


def test_verify_commit_rejects_failing_tests(toy_repo, tmp_path):
    from pro2.config import Objective

    sandbox = GitSandbox(toy_repo, tmp_path / "wt", tmp_path / "verify")
    wt = sandbox.create_experiment("E1", sandbox.head())
    (wt.path / "config.json").write_text('{"score": 2.0, "work": 1.0}\n')
    commit = sandbox.commit_all(wt.path, "raise")

    objective = Objective.from_dict(
        {
            "name": "toy",
            "description": "toy",
            "repo": str(toy_repo),
            "success": {"metric": "score", "direction": "maximize", "min_delta": 0.5},
            "verify": {"command": f"{sys.executable} run.py", "metric_regex": r"SCORE=([0-9.]+)"},
            "tests": [{"command": f"{sys.executable} -c 'import sys; sys.exit(1)'"}],
        }
    )

    result = verify_commit(sandbox, objective, commit, 1.0, "E1", tmp_path / "art")

    assert result.accepted is False
    assert result.tests_failed is True
    sandbox.remove_experiment(wt)
