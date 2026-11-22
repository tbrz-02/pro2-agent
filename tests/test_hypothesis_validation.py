import pytest

from pro2.candidate_solution import CandidateSolution
from pro2.hypothesis import Hypothesis
from pro2.problem import Problem
from pro2.problem_state import ProblemState


def build_state():
    state = ProblemState()

    problem = Problem("P001", "GPU memory bottleneck")
    problem.add_candidate(CandidateSolution("C001", "Reduce batch size", 0.5))
    problem.add_candidate(CandidateSolution("C002", "Gradient checkpointing", 0.6))

    state.add_problem(problem)
    return state


def test_valid_hypothesis_is_accepted():
    state = build_state()

    hypothesis = Hypothesis(
        problem_id="P001",
        candidate_id="C001",
        modification="Reduce batch size from 64 to 32",
    )

    assert state.validate_hypothesis(hypothesis) is True


def test_unknown_problem_is_rejected():
    state = build_state()

    hypothesis = Hypothesis(
        problem_id="P999",
        candidate_id="C001",
        modification="Reduce batch size",
    )

    with pytest.raises(ValueError, match="problem"):
        state.validate_hypothesis(hypothesis)


def test_unknown_candidate_is_rejected():
    state = build_state()

    hypothesis = Hypothesis(
        problem_id="P001",
        candidate_id="C999",
        modification="Reduce batch size",
    )

    with pytest.raises(ValueError, match="candidate"):
        state.validate_hypothesis(hypothesis)


def test_candidate_from_another_problem_is_rejected():
    state = build_state()

    another_problem = Problem("P002", "Data loading bottleneck")
    another_problem.add_candidate(CandidateSolution("C100", "Optimize data loading", 0.5))
    state.add_problem(another_problem)

    hypothesis = Hypothesis(
        problem_id="P001",
        candidate_id="C100",
        modification="Optimize data loading",
    )

    with pytest.raises(ValueError, match="candidate"):
        state.validate_hypothesis(hypothesis)
