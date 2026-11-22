import sys

import pytest

from pro2.agents import AgentConfig, AgentError, AgentRunner, parse_json_response


def test_parse_json_response_handles_plain_json():
    assert parse_json_response('{"a": 1}') == {"a": 1}


def test_parse_json_response_handles_fenced_json():
    assert parse_json_response('text\n```json\n{"a": 1}\n```\nmore') == {"a": 1}


def test_parse_json_response_handles_surrounding_prose():
    assert parse_json_response('Here you go: {"a": 1} thanks') == {"a": 1}


def test_parse_json_response_returns_none_for_garbage():
    assert parse_json_response("no json here") is None


def test_command_provider_runs_and_returns_text(tmp_path, scripted_agent):
    runner = AgentRunner(
        AgentConfig(provider="command", command=scripted_agent, timeout_seconds=20),
        tmp_path / "art",
    )

    result = runner.run("IMPLEMENT", "TASK: IMPLEMENT\nSCORE_TARGET=2.0", tmp_path)

    assert result.ok is True
    assert "implemented target 2.0" in result.text
    assert result.raw_path.exists()


def test_command_provider_ask_json(tmp_path, scripted_agent):
    runner = AgentRunner(
        AgentConfig(provider="command", command=scripted_agent, timeout_seconds=20),
        tmp_path / "art",
    )

    payload, result = runner.ask_json("REVIEW", "TASK: REVIEW", tmp_path)

    assert result.ok is True
    assert payload["approved"] is True


def test_missing_command_provider_fails_gracefully(tmp_path):
    runner = AgentRunner(AgentConfig(provider="command", command=[]), tmp_path / "art")

    with pytest.raises(AgentError, match="command"):
        runner.run("IMPLEMENT", "x", tmp_path)


def test_unknown_provider_fails_gracefully(tmp_path):
    runner = AgentRunner(AgentConfig(provider="nonsense"), tmp_path / "art")

    with pytest.raises(AgentError, match="provider"):
        runner.run("IMPLEMENT", "x", tmp_path)


def test_provider_command_failure_is_reported(tmp_path):
    runner = AgentRunner(
        AgentConfig(
            provider="command",
            command=[sys.executable, "-c", "import sys; sys.exit(3)"],
            timeout_seconds=10,
        ),
        tmp_path / "art",
    )

    result = runner.run("IMPLEMENT", "x", tmp_path)

    assert result.ok is False
    assert "3" in result.error


def test_available_providers_includes_command(tmp_path):
    runner = AgentRunner(
        AgentConfig(provider="command", command=[sys.executable, "-c", "print(1)"]),
        tmp_path / "art",
    )

    assert "command" in runner.available_providers()


def test_agent_artifacts_are_written(tmp_path, scripted_agent):
    runner = AgentRunner(
        AgentConfig(provider="command", command=scripted_agent, timeout_seconds=20),
        tmp_path / "art",
    )
    result = runner.run("REVIEW", "TASK: REVIEW", tmp_path)

    assert result.raw_path.exists()
    assert "approved" in result.raw_path.read_text()


def test_agent_config_roundtrip():
    cfg = AgentConfig(provider="command", command=["x"], model="m", timeout_seconds=5)

    assert AgentConfig.from_dict(cfg.to_dict()).to_dict() == cfg.to_dict()
