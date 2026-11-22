"""End-to-end: full engine run with a scripted agent + CLI smoke tests."""

import json

from pro2.cli import main as cli_main
from pro2.orchestrator import Engine
from test_orchestrator import make_objective, objective_dict


def test_engine_e2e_accepts_improvement_and_records_lineage(toy_repo, scripted_agent):
    objective = make_objective(
        toy_repo,
        scripted_agent,
        budget={"max_experiments": 1, "max_iterations": 2, "max_wall_seconds": 120},
    )
    engine = Engine(objective)
    state = engine.run()

    assert state.status in {"completed", "budget_exhausted"}
    assert state.best_commit
    if state.experiments:
        assert state.experiments[0].lineage
        assert state.experiments[0].snapshot_digest


def test_cli_doctor_smoke(toy_repo, scripted_agent, tmp_path):
    config = tmp_path / "pro2.json"
    config.write_text(json.dumps(objective_dict(toy_repo, scripted_agent)))
    assert cli_main(["doctor", "--config", str(config)]) == 0


def test_cli_run_e2e(toy_repo, scripted_agent, tmp_path):
    config = tmp_path / "pro2.json"
    config.write_text(
        json.dumps(
            objective_dict(
                toy_repo,
                scripted_agent,
                budget={"max_experiments": 1, "max_iterations": 2, "max_wall_seconds": 120},
            )
        )
    )
    assert cli_main(["run", "--config", str(config)]) == 0
