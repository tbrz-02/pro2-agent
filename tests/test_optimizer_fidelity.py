"""MCTS/UCT fidelity: failed nodes purge reward, completed nodes backpropagate."""

from pro2.optimizer import EngineeringOptimizer, EngineeringParam, reward


def _optimizer() -> EngineeringOptimizer:
    return EngineeringOptimizer(
        [EngineeringParam(path="cfg", key="lr", values=[0.1, 0.01])], max_nodes=5, gamma=0.02
    )


def test_record_result_failed_does_not_accumulate_reward():
    opt = _optimizer()
    node_id, _ = opt.next_config()
    parent = opt.nodes[opt.nodes[node_id].parent_id]
    parent_reward = parent.reward_sum
    parent_visits = parent.visits

    value = opt.record_result(
        node_id, delta=5.0, duration_seconds=1.0, metric=None, status="failed"
    )

    node = opt.nodes[node_id]
    assert value == 0.0
    assert node.reward_sum == 0.0
    assert node.visits == 1
    assert parent.reward_sum == parent_reward
    assert parent.visits == parent_visits


def test_record_result_completed_accumulates_reward_and_backpropagates():
    opt = _optimizer()
    node_id, _ = opt.next_config()
    parent = opt.nodes[opt.nodes[node_id].parent_id]

    value = opt.record_result(node_id, delta=1.0, duration_seconds=2.0, metric=0.8)

    node = opt.nodes[node_id]
    assert value == reward(1.0, 2.0, opt.gamma)
    assert node.reward_sum == value
    assert parent.reward_sum >= value
    assert opt.best().node_id == node_id
