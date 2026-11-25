"""Cost-aware MCTS/UCT over engineering configurations."""

from __future__ import annotations

import json
import math
import random
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pro2.config import EngineeringParam
from pro2.util import deep_set

__all__ = [
    "EngineeringOptimizer",
    "EngineeringParam",
    "MCTSNode",
    "apply_config",
    "reward",
]


def reward(delta: float, duration_seconds: float, gamma: float) -> float:
    """Paper Eq. 4: R = Δh - γ ln(T_exec)."""
    return delta - gamma * math.log(max(duration_seconds, 1.0))


@dataclass
class MCTSNode:
    node_id: str
    parent_id: str | None
    config: dict[tuple[str, str], Any]
    untried: list[tuple[tuple[str, str], Any]] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    visits: int = 0
    reward_sum: float = 0.0
    metric: float | None = None
    duration_seconds: float = 0.0
    commit: str | None = None
    status: str = "pending"
    # Only nodes whose trial actually improved the root metric are eligible
    # for `best()`; failed/pending/rejected trials never win.
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "parent_id": self.parent_id,
            "config": {f"{path}::{key}": value for (path, key), value in self.config.items()},
            "children": list(self.children),
            "visits": self.visits,
            "reward_sum": self.reward_sum,
            "metric": self.metric,
            "duration_seconds": self.duration_seconds,
            "commit": self.commit,
            "status": self.status,
            "accepted": self.accepted,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCTSNode:
        config = {}
        for composite, value in (data.get("config") or {}).items():
            path, _, key = composite.partition("::")
            config[(path, key)] = value
        return cls(
            node_id=str(data["node_id"]),
            parent_id=data.get("parent_id"),
            config=config,
            children=list(data.get("children", [])),
            visits=int(data.get("visits", 0)),
            reward_sum=float(data.get("reward_sum", 0.0)),
            metric=data.get("metric"),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            commit=data.get("commit"),
            status=str(data.get("status", "pending")),
            accepted=bool(data.get("accepted", False)),
        )


class EngineeringOptimizer:
    def __init__(
        self,
        params: list[EngineeringParam],
        max_nodes: int = 8,
        gamma: float = 0.02,
        seed: int = 0,
        exploration_constant: float = 1.414,
        nodes: list[MCTSNode] | None = None,
        direction: str = "maximize",
    ) -> None:
        if direction not in {"maximize", "minimize"}:
            raise ValueError("direction must be 'maximize' or 'minimize'")
        self.params = params
        self.max_nodes = max_nodes
        self.gamma = gamma
        self.direction = direction
        self.exploration_constant = exploration_constant
        self._rng = random.Random(seed)
        self.nodes: dict[str, MCTSNode] = {}
        self.root_id = "N-root"
        if nodes:
            for node in nodes:
                self.nodes[node.node_id] = node
            if self.root_id not in self.nodes:
                self.nodes[self.root_id] = self._make_root()
        else:
            self.nodes[self.root_id] = self._make_root()

    def _make_root(self) -> MCTSNode:
        return MCTSNode(
            node_id=self.root_id,
            parent_id=None,
            config={},
            untried=self._all_options(),
            status="root",
        )

    def _all_options(self) -> list[tuple[tuple[str, str], Any]]:
        options: list[tuple[tuple[str, str], Any]] = []
        for param in self.params:
            for value in param.values:
                options.append(((param.path, param.key), value))
        self._rng.shuffle(options)
        return options

    def _options_for(self, config: dict[tuple[str, str], Any]) -> list[tuple[tuple[str, str], Any]]:
        assigned = set(config)
        options: list[tuple[tuple[str, str], Any]] = []
        for param in self.params:
            key = (param.path, param.key)
            if key in assigned:
                continue
            for value in param.values:
                options.append((key, value))
        self._rng.shuffle(options)
        return options

    def _trial_count(self) -> int:
        return max(0, len(self.nodes) - 1)

    def next_config(self) -> tuple[str | None, dict[tuple[str, str], Any]]:
        if self._trial_count() >= self.max_nodes or not self.params:
            return None, {}

        node = self.nodes[self.root_id]
        while not node.untried and node.children:
            node = max(
                (self.nodes[child_id] for child_id in node.children),
                key=lambda child: self.uct(child, node.visits),
            )

        if not node.untried:
            node.untried = self._options_for(node.config)
        if not node.untried:
            return None, {}

        (param_key, value) = node.untried.pop()
        config = dict(node.config)
        config[param_key] = value
        node_id = f"N-{uuid.uuid4().hex[:10]}"
        child = MCTSNode(
            node_id=node_id,
            parent_id=node.node_id,
            config=config,
            untried=self._options_for(config),
        )
        self.nodes[node_id] = child
        node.children.append(node_id)
        return node_id, config

    def uct(self, node: MCTSNode, parent_visits: int) -> float:
        if node.visits == 0:
            return float("inf")
        exploitation = node.reward_sum / node.visits
        exploration = self.exploration_constant * math.sqrt(
            math.log(max(parent_visits, 1)) / node.visits
        )
        return exploitation + exploration

    def record_result(
        self,
        node_id: str,
        delta: float,
        duration_seconds: float,
        metric: float | None,
        commit: str | None = None,
        status: str = "completed",
        accepted: bool = True,
    ) -> float:
        node = self.nodes[node_id]
        node.metric = metric
        node.duration_seconds = duration_seconds
        node.commit = commit
        node.status = status
        node.accepted = accepted and status == "completed"
        node.visits += 1
        if status == "failed":
            # Paper: failed branches are purged (hard rollback); no reward is
            # credited to the node or backpropagated to its ancestors.
            return 0.0
        value = reward(delta, duration_seconds, self.gamma)
        node.reward_sum += value
        parent_id = node.parent_id
        while parent_id:
            parent = self.nodes[parent_id]
            parent.visits += 1
            parent.reward_sum += value
            parent_id = parent.parent_id
        return value

    def best(self) -> MCTSNode:
        # Only completed, accepted trials (metric set and improved the root)
        # are eligible: failed/pending/rejected branches never win.
        candidates = [n for n in self.nodes.values() if n.metric is not None and n.accepted]
        if not candidates:
            return self.nodes[self.root_id]
        if self.direction == "minimize":
            # Paper Eq. 3/4: for minimize objectives the lowest-metric node wins;
            # ties prefer the most-visited node (stable final selection).
            return min(candidates, key=lambda n: (n.metric or float("inf"), -n.visits))
        return max(candidates, key=lambda n: (n.metric or float("-inf"), -n.visits))

    def to_dict(self) -> dict[str, Any]:
        return {
            "gamma": self.gamma,
            "max_nodes": self.max_nodes,
            "exploration_constant": self.exploration_constant,
            "direction": self.direction,
            "root_id": self.root_id,
            "nodes": [node.to_dict() for node in self.nodes.values()],
        }

    @classmethod
    def from_dict(
        cls,
        params: list[EngineeringParam],
        data: dict[str, Any],
        seed: int = 0,
    ) -> EngineeringOptimizer:
        nodes = [MCTSNode.from_dict(n) for n in data.get("nodes", [])]
        return cls(
            params,
            max_nodes=int(data.get("max_nodes", 8)),
            gamma=float(data.get("gamma", 0.02)),
            seed=seed,
            exploration_constant=float(data.get("exploration_constant", 1.414)),
            nodes=nodes,
            direction=str(data.get("direction", "maximize")),
        )


def apply_config(
    worktree: str | Path,
    config: dict[tuple[str, str], Any],
    params: list[EngineeringParam],
) -> dict[str, str]:
    """Apply a configuration to a worktree; return extra environment variables."""
    root = Path(worktree).resolve()
    lookup = {(p.path, p.key): p for p in params}
    env: dict[str, str] = {}

    for (path, key), value in config.items():
        param = lookup.get((path, key))
        adapter = param.adapter if param else "json"
        if adapter == "env":
            env[key] = str(value)
            continue

        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"engineering parameter escapes worktree: {path}")
        if not target.exists():
            raise FileNotFoundError(f"engineering target missing: {path}")

        if adapter == "json":
            data = json.loads(target.read_text(encoding="utf-8"))
            deep_set(data, key, value)
            target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        elif adapter == "text":
            text = target.read_text(encoding="utf-8")
            pattern = re.compile(rf"(?m)^(\s*{re.escape(key)}\s*[:=]\s*).*$")
            if pattern.search(text):
                text = pattern.sub(lambda m, replacement=str(value): m.group(1) + replacement, text)
            else:
                text += f"\n{key}={value}\n"
            target.write_text(text, encoding="utf-8")

    return env
