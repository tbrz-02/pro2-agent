"""Scientific search hardening: hypothesis graph, confidence, falsification,
adaptive rescheduling, and paper-faithful priority selection."""

import pytest

from pro2.candidate_solution import CandidateSolution
from pro2.hypothesis import Hypothesis, adaptive_reschedule, select_hypothesis
from pro2.problem import Problem
from pro2.state import EngineState


def test_hypothesis_graph_edges_and_relations():
    h = Hypothesis(problem_id="P001", candidate_id="C001", modification="x")
    h.add_edge("H-abc", "supports")
    h.add_edge("H-def", "contradicts")
    assert [e["relation"] for e in h.edges] == ["supports", "contradicts"]
    assert h.contradicts() == ["H-def"]
    with pytest.raises(ValueError):
        h.add_edge("H-x", "bogus")
    h.add_edge("H-abc", "supports")  # duplicate ignored
    assert len(h.edges) == 2


def test_hypothesis_roundtrip_preserves_graph_and_falsification():
    h = Hypothesis(problem_id="P001", candidate_id="C001", modification="x", confidence=0.7)
    h.add_edge("H-parent", "supports")
    h.falsified = True
    h.falsification_reason = "delta -0.2"
    restored = Hypothesis.from_dict(h.to_dict())
    assert restored.edges == h.edges
    assert restored.confidence == 0.7
    assert restored.falsified is True
    assert restored.falsification_reason == "delta -0.2"


def test_hypothesis_confidence_bounds_and_adjust():
    with pytest.raises(ValueError):
        Hypothesis(problem_id="P", candidate_id="C", modification="x", confidence=1.5)
    h = Hypothesis(problem_id="P", candidate_id="C", modification="x")
    assert h.confidence == 0.5
    assert h.adjust_confidence(0.2) == pytest.approx(0.7)
    assert h.adjust_confidence(5) == pytest.approx(1.0)


def test_falsified_is_a_valid_status():
    h = Hypothesis(problem_id="P", candidate_id="C", modification="x", status="falsified")
    assert Hypothesis.from_dict(h.to_dict()).status == "falsified"


def test_select_hypothesis_uses_paper_priority():
    h1 = Hypothesis(problem_id="P", candidate_id="C1", modification="a")
    h2 = Hypothesis(problem_id="P", candidate_id="C2", modification="b")
    selected = select_hypothesis([h1, h2], {"C1": 0.9, "C2": 0.1}, {"P": 0})
    assert selected is h1
    assert select_hypothesis([], {}, {}) is None


def _state_with_falsified():
    state = EngineState()
    p1 = Problem(problem_id="P001", description="p1")
    p1.add_candidate(CandidateSolution("C001", "cand one", 0.5))
    p2 = Problem(problem_id="P002", description="p2")
    p2.add_candidate(CandidateSolution("C002", "cand two", 0.5))
    state.add_problem(p1)
    state.add_problem(p2)
    for _ in range(2):
        state.add_hypothesis(
            Hypothesis(problem_id="P001", candidate_id="C001", modification="x", status="falsified")
        )
    state.add_hypothesis(Hypothesis(problem_id="P002", candidate_id="C002", modification="y"))
    return state, p1


def test_adaptive_rescheduling_boosts_repeatedly_falsified_problem():
    state, p1 = _state_with_falsified()
    assert adaptive_reschedule(state) == 1
    assert p1.attempts == 2


def test_adaptive_rescheduling_no_boost_below_threshold():
    state = EngineState()
    problem = Problem(problem_id="P001", description="p1")
    problem.add_candidate(CandidateSolution("C001", "c", 0.5))
    state.add_problem(problem)
    state.add_hypothesis(
        Hypothesis(problem_id="P001", candidate_id="C001", modification="x", status="falsified")
    )
    assert adaptive_reschedule(state) == 0
    assert problem.attempts == 0
