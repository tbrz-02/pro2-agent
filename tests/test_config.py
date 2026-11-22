import json
import math

import pytest

from pro2.config import Objective, extract_metric, load_objective


def minimal_config(tmp_path):
    return {
        "name": "toy",
        "description": "Improve the toy score",
        "repo": str(tmp_path),
        "success": {"metric": "score", "direction": "maximize", "min_delta": 0.5},
        "verify": {"command": "python run.py", "metric_regex": r"SCORE=([0-9.]+)"},
    }


def test_load_objective_applies_defaults(tmp_path):
    cfg = minimal_config(tmp_path)
    path = tmp_path / "pro2.json"
    path.write_text(json.dumps(cfg))

    objective = load_objective(path)

    assert objective.name == "toy"
    assert objective.budget.max_experiments == 5
    assert objective.agent.provider == "auto"
    assert objective.constraints.max_changed_files == 3
    assert objective.state_dir == ".pro2"


def test_load_objective_from_file(tmp_path):
    cfg = minimal_config(tmp_path)
    path = tmp_path / "pro2.json"
    path.write_text(json.dumps(cfg))

    objective = load_objective(path)

    assert objective.success.direction == "maximize"
    assert objective.repo == tmp_path.resolve()
    assert objective.verify.metric_regex == r"SCORE=([0-9.]+)"


def test_objective_roundtrips_through_dict(tmp_path):
    path = tmp_path / "pro2.json"
    path.write_text(json.dumps(minimal_config(tmp_path)))
    objective = load_objective(path)

    restored = Objective.from_dict(objective.to_dict())

    assert restored.to_dict() == objective.to_dict()


def test_invalid_direction_is_rejected(tmp_path):
    cfg = minimal_config(tmp_path)
    cfg["success"]["direction"] = "sideways"
    path = tmp_path / "pro2.json"
    path.write_text(json.dumps(cfg))

    with pytest.raises(ValueError, match="direction"):
        load_objective(path)


def test_metric_spec_requires_regex_or_json_path(tmp_path):
    cfg = minimal_config(tmp_path)
    cfg["verify"] = {"command": "python run.py"}
    path = tmp_path / "pro2.json"
    path.write_text(json.dumps(cfg))

    with pytest.raises(ValueError, match="metric_regex|json_path"):
        load_objective(path)


def test_missing_command_is_rejected(tmp_path):
    cfg = minimal_config(tmp_path)
    cfg["verify"] = {"metric_regex": r"SCORE=([0-9.]+)"}
    path = tmp_path / "pro2.json"
    path.write_text(json.dumps(cfg))

    with pytest.raises(ValueError, match="command"):
        load_objective(path)


def test_extract_metric_from_regex():
    assert extract_metric("noise\nSCORE=2.5\nmore", r"SCORE=([0-9.]+)") == 2.5


def test_extract_metric_from_json_path():
    output = '{"result": {"score": 3.25}}'
    assert extract_metric(output, json_path="result.score") == 3.25


def test_extract_metric_rejects_non_finite():
    assert extract_metric("SCORE=nan", r"SCORE=(\S+)") is None
    assert extract_metric("SCORE=inf", r"SCORE=(\S+)") is None
    assert not math.isfinite(float("nan"))


def test_extract_metric_returns_none_when_absent():
    assert extract_metric("nothing here", r"SCORE=([0-9.]+)") is None
