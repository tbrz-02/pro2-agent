"""EngineeringOptimizer fidelity: direction, failed-node semantics, persisted tree."""

import pytest

from pro2.config import EngineeringParam
from pro2.optimizer import EngineeringOptimizer, MCTSNode


def params():
    return [EngineeringParam(path="config.json", key="work", values=[2.0, 3.0])]


def test_best_is_highest_metric_when_accepted():
    opt = EngineeringOptimizer(params(), max_nodes=4, seed=1)
    a, _ = opt.next_config()
    opt.record_result(a, delta=1.0, duration_seconds=1.0, metric=2.0, commit="a")
    b, _ = opt.next_config()
    opt.record_result(b, delta=2.0, duration_seconds=1.0, metric=3.0, commit="b")
    assert opt.best().node_id == b


def test_best_respects_minimize_direction():
    opt = EngineeringOptimizer(params(), max_nodes=4, seed=1, direction="minimize")
    a, _ = opt.next_config()
    opt.record_result(a, delta=1.0, duration_seconds=1.0, metric=2.0, commit="a")
    b, _ = opt.next_config()
    opt.record_result(b, delta=2.0, duration_seconds=1.0, metric=1.5, commit="b")
    assert opt.best().node_id == b


def test_best_excludes_failed_and_rejected_nodes():
    opt = EngineeringOptimizer(params(), max_nodes=4, seed=1)
    a, _ = opt.next_config()
    opt.record_result(a, delta=0.0, duration_seconds=1.0, metric=9.0, status="failed")
    b, _ = opt.next_config()
    opt.record_result(b, delta=0.0, duration_seconds=1.0, metric=8.0, status="failed")
    # Nothing accepted: best falls back to the root (never a failed node).
    assert opt.best().node_id == opt.root_id
    assert not any(n.accepted for n in opt.nodes.values())


def test_best_excludes_pending_and_interrupted_nodes():
    opt = EngineeringOptimizer(params(), max_nodes=4, seed=1)
    a, _ = opt.next_config()
    node = opt.nodes[a]
    node.metric = 5.0
    node.accepted = False
    node.status = "interrupted"
    assert opt.best().node_id == opt.root_id


def test_record_result_marks_node_accepted_only_for_completed():
    opt = EngineeringOptimizer(params(), max_nodes=4, seed=1)
    a, _ = opt.next_config()
    opt.record_result(a, delta=1.0, duration_seconds=1.0, metric=2.0, commit="a")
    assert opt.nodes[a].accepted is True
    opt.record_result(
        a, delta=1.0, duration_seconds=1.0, metric=2.0, status="failed", accepted=True
    )
    assert opt.nodes[a].accepted is False


def test_record_result_failed_has_no_reward_or_backprop():
    opt = EngineeringOptimizer(params(), max_nodes=4, seed=1)
    a, _ = opt.next_config()
    b, _ = opt.next_config()
    value = opt.record_result(b, delta=1.0, duration_seconds=1.0, metric=None, status="failed")
    assert value == 0.0
    root = opt.nodes[opt.root_id]
    assert root.visits == 0
    assert opt.nodes[b].reward_sum == 0.0


def test_record_result_backpropagates_reward_to_root():
    opt = EngineeringOptimizer(params(), max_nodes=4, gamma=0.0, seed=1)
    a, _ = opt.next_config()
    value = opt.record_result(a, delta=2.0, duration_seconds=1.0, metric=4.0, commit="a")
    root = opt.nodes[opt.root_id]
    assert root.visits >= 1
    assert root.reward_sum == pytest.approx(value)


def test_tree_persists_and_restores_accepted_flags():
    opt = EngineeringOptimizer(params(), max_nodes=4, seed=1)
    a, _ = opt.next_config()
    opt.record_result(a, delta=1.0, duration_seconds=1.0, metric=2.0, commit="a")
    restored = EngineeringOptimizer.from_dict(params(), opt.to_dict(), seed=1)
    assert restored.direction == "maximize"
    assert restored.best().node_id == opt.best().node_id
    assert any(n.accepted for n in restored.nodes.values())


def test_restored_minimize_tree_best_is_min_metric():
    opt = EngineeringOptimizer(params(), max_nodes=4, seed=1, direction="minimize")
    a, _ = opt.next_config()
    opt.record_result(a, delta=1.0, duration_seconds=1.0, metric=0.5, commit="a")
    restored = EngineeringOptimizer.from_dict(params(), opt.to_dict(), seed=1)
    assert restored.direction == "minimize"
    assert restored.best().metric == 0.5


def test_node_to_dict_roundtrips_all_fields():
    node = MCTSNode(
        node_id="N-1",
        parent_id=None,
        config={("config.json", "work"): 3.0},
        status="completed",
        accepted=True,
        metric=2.0,
    )
    restored = MCTSNode.from_dict(node.to_dict())
    assert restored == node
