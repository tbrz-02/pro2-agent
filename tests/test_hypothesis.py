from pro2.hypothesis import Hypothesis


def test_hypothesis_identifies_target_problem():
    hypothesis = Hypothesis(
        problem_id="P001",
        candidate_id="C001",
        modification="Reduce batch size from 64 to 32",
    )

    assert hypothesis.problem_id == "P001"


def test_hypothesis_identifies_candidate():
    hypothesis = Hypothesis(
        problem_id="P001",
        candidate_id="C001",
        modification="Enable gradient checkpointing",
    )

    assert hypothesis.candidate_id == "C001"


def test_hypothesis_stores_proposed_modification():
    hypothesis = Hypothesis(
        problem_id="P001",
        candidate_id="C001",
        modification="Enable gradient checkpointing",
    )

    assert hypothesis.modification == "Enable gradient checkpointing"


def test_hypotheses_can_propose_different_modifications():
    first = Hypothesis("P001", "C001", "Reduce batch size")
    second = Hypothesis("P001", "C001", "Enable gradient checkpointing")

    assert first.modification != second.modification
    assert first.problem_id == second.problem_id
    assert first.candidate_id == second.candidate_id
