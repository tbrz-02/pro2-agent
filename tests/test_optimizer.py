import json
import math

import pytest

from pro2.optimizer import EngineeringOptimizer, EngineeringParam, apply_config, reward


def test_reward_penalizes_slow_runs():
    fast = reward(delta=1.0, duration_seconds=10, gamma=0.1)
    slow = reward(delta=1.0, duration_seconds=1000, gamma=0.1)

    assert fast > slow
    assert fast == pytest.approx(1.0 - 0.1 * math.log(10))


def test_optimizer_selects_and_expands_root():
    params = [EngineeringParam(path="config.json", key="work", values=[2.0, 3.0])]
    opt = EngineeringOptimizer(params, max_nodes=4, gamma=0.0, seed=1)

    node_id, config = opt.next_config()

    assert node_id is not None
    assert config[("config.json", "work")] in {2.0, 3.0}
    assert opt.root_id is not None


def test_optimizer_uct_prefers_unvisited_children():
    params = [EngineeringParam(path="config.json", key="work", values=[2.0, 3.0, 4.0])]
    opt = EngineeringOptimizer(params, max_nodes=10, gamma=0.0, seed=1)

    first, _ = opt.next_config()
    opt.record_result(first, delta=1.0, duration_seconds=1.0, metric=2.0, commit="a")

    second, config = opt.next_config()

    assert second != first
    assert config


def test_optimizer_backpropagates_visits():
    params = [EngineeringParam(path="config.json", key="work", values=[2.0])]
    opt = EngineeringOptimizer(params, max_nodes=3, gamma=0.0, seed=1)
    node_id, _ = opt.next_config()
    opt.record_result(node_id, delta=1.0, duration_seconds=1.0, metric=2.0, commit="a")

    root = opt.nodes[opt.root_id]
    assert root.visits == 1
    assert opt.nodes[node_id].visits == 1


def test_optimizer_best_is_highest_metric():
    params = [EngineeringParam(path="config.json", key="work", values=[2.0, 3.0])]
    opt = EngineeringOptimizer(params, max_nodes=5, gamma=0.0, seed=1)
    a, _ = opt.next_config()
    opt.record_result(a, delta=1.0, duration_seconds=1.0, metric=2.0, commit="a")
    b, _ = opt.next_config()
    opt.record_result(b, delta=2.0, duration_seconds=1.0, metric=3.0, commit="b")

    assert opt.best().node_id == b


def test_optimizer_stops_after_max_nodes():
    params = [EngineeringParam(path="config.json", key="work", values=[2.0, 3.0, 4.0])]
    opt = EngineeringOptimizer(params, max_nodes=2, gamma=0.0, seed=1)
    for _ in range(2):
        node_id, _ = opt.next_config()
        opt.record_result(node_id, delta=1.0, duration_seconds=1.0, metric=2.0, commit="a")

    assert opt.next_config() == (None, {})


def test_apply_config_writes_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"work": 1.0, "nested": {"lr": 0.1}}))
    params = [
        EngineeringParam(path="config.json", key="work", values=[2.0]),
        EngineeringParam(path="config.json", key="nested.lr", values=[0.2]),
    ]

    env = apply_config(
        tmp_path,
        {("config.json", "work"): 2.0, ("config.json", "nested.lr"): 0.2},
        params,
    )

    data = json.loads(path.read_text())
    assert data["work"] == 2.0
    assert data["nested"]["lr"] == 0.2
    assert env == {}


def test_apply_config_env_adapter(tmp_path):
    params = [EngineeringParam(path="", key="WORK", values=[3.0], adapter="env")]

    env = apply_config(tmp_path, {("", "WORK"): 3.0}, params)

    assert env["WORK"] == "3.0"
