"""Resource governance: divergence, disk budgets, and reliable termination."""

import sys

import pytest

from pro2.config import MetricSpec
from pro2.orchestrator import Engine
from pro2.verification import measure_metric
from test_orchestrator import make_objective


def test_metric_spec_accepts_divergence_and_memory_fields():
    spec = MetricSpec(
        command="echo x",
        metric_regex="x",
        divergence_regex="loss=NaN",
        memory_limit_mb=512,
    )
    assert spec.divergence_regex == "loss=NaN"
    assert spec.memory_limit_mb == 512
    with pytest.raises(ValueError):
        MetricSpec(command="echo x", metric_regex="x", divergence_regex="(")
    with pytest.raises(ValueError):
        MetricSpec(command="echo x", metric_regex="x", memory_limit_mb=-1)


def test_measure_metric_flags_divergence(tmp_path):
    spec = MetricSpec(
        command=(
            f"{sys.executable} -c \"print('loss=NaN', flush=True); import time; time.sleep(2)\""
        ),
        metric_regex=r"loss=([0-9.]+)",
        divergence_regex="loss=NaN",
    )
    measurement = measure_metric(spec, tmp_path, tmp_path, "diverge")
    assert measurement.runs
    assert measurement.runs[0].diverged is True


def test_measure_metric_healthy_run_not_diverged(tmp_path):
    spec = MetricSpec(
        command=f"{sys.executable} -c \"print('loss=0')\"",
        metric_regex=r"loss=([0-9.]+)",
    )
    measurement = measure_metric(spec, tmp_path, tmp_path, "healthy")
    assert measurement.value == 0.0
    assert not measurement.runs[0].diverged


def test_disk_budget_reason(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.store.write_artifact("probe.bin", "x" * 100)
    assert engine.store.disk_usage_bytes() >= 100
    engine.objective.budget.max_disk_bytes = 1
    reason = engine._budget_reason()
    assert reason and "disk budget" in reason


def test_disk_budget_zero_means_unlimited(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent))
    engine.objective.budget.max_disk_bytes = 0
    assert "disk budget" not in (engine._budget_reason() or "")
