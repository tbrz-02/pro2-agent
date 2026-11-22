import json
import sys

from pro2.cli import main


def write_config(repo, agent_cmd):
    cfg = {
        "name": "toy",
        "description": "Improve the toy score",
        "repo": str(repo),
        "success": {"metric": "score", "direction": "maximize", "min_delta": 0.5},
        "baseline": {"command": f"{sys.executable} run.py", "metric_regex": r"SCORE=([0-9.]+)"},
        "verify": {"command": f"{sys.executable} run.py", "metric_regex": r"SCORE=([0-9.]+)"},
        "diagnostics": [],
        "tests": [],
        "budget": {"max_experiments": 1, "max_wall_seconds": 120, "max_experiment_seconds": 60},
        "research": {"enabled": False},
        "agent": {"provider": "command", "command": agent_cmd, "timeout_seconds": 30},
    }
    path = repo / "pro2.json"
    path.write_text(json.dumps(cfg))
    return path


def test_cli_init_and_status(toy_repo, scripted_agent, capsys):
    config = write_config(toy_repo, scripted_agent)

    assert main(["init", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "initialized" in out.lower() or "baseline" in out.lower()

    assert main(["status", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "toy" in out
    assert "baseline" in out.lower()


def test_cli_run_and_report(toy_repo, scripted_agent, capsys):
    config = write_config(toy_repo, scripted_agent)
    main(["init", "--config", str(config)])

    assert main(["run", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "experiment" in out.lower()

    assert main(["report", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "report" in out.lower()


def test_cli_experiments_lists_records(toy_repo, scripted_agent, capsys):
    config = write_config(toy_repo, scripted_agent)
    main(["init", "--config", str(config)])
    main(["run", "--config", str(config)])

    assert main(["experiments", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "accepted" in out.lower()


def test_cli_doctor_reports_tools(toy_repo, scripted_agent, capsys):
    config = write_config(toy_repo, scripted_agent)

    assert main(["doctor", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "python" in out.lower()
    assert "git" in out.lower()


def test_cli_missing_config_returns_error(capsys):
    assert main(["status", "--config", "/nonexistent/pro2.json"]) != 0
