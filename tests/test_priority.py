from pro2.hypothesis import Hypothesis, hypothesis_priority, select_hypothesis


def make_hypothesis(hid, problem_id="P001", candidate_id="C001", modification="change"):
    return Hypothesis(
        problem_id=problem_id,
        candidate_id=candidate_id,
        modification=modification,
        hypothesis_id=hid,
    )


def test_priority_is_belief_over_attempts_plus_one():
    assert hypothesis_priority(0.8, 0) == 0.8
    assert hypothesis_priority(0.8, 3) == 0.2
    assert hypothesis_priority(0.0, 10) == 0.0


def test_priority_rejects_out_of_range_belief():
    try:
        hypothesis_priority(1.5, 0)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_priority_penalizes_repeated_problem_attempts():
    fresh = hypothesis_priority(0.9, 0)
    attempted = hypothesis_priority(0.9, 4)

    assert attempted < fresh


def test_select_hypothesis_returns_highest_priority():
    beliefs = {"C001": 0.5, "C002": 0.9}
    attempts = {"P001": 0, "P002": 0}
    queue = [
        make_hypothesis("H1", "P001", "C001"),
        make_hypothesis("H2", "P002", "C002"),
    ]

    selected = select_hypothesis(queue, beliefs, attempts)

    assert selected.hypothesis_id == "H2"


def test_select_hypothesis_tie_breaks_deterministically_by_creation_order():
    beliefs = {"C001": 0.5, "C002": 0.5}
    attempts = {"P001": 0, "P002": 0}
    queue = [
        make_hypothesis("H2", "P002", "C002"),
        make_hypothesis("H1", "P001", "C001"),
    ]

    assert select_hypothesis(queue, beliefs, attempts).hypothesis_id == "H2"
    assert select_hypothesis(list(reversed(queue)), beliefs, attempts).hypothesis_id == "H2"


def test_select_hypothesis_skips_non_pending():
    beliefs = {"C001": 0.9, "C002": 0.1}
    attempts = {"P001": 0, "P002": 0}
    queue = [
        make_hypothesis("H1", "P001", "C001"),
        make_hypothesis("H2", "P002", "C002"),
    ]
    queue[0].status = "running"

    assert select_hypothesis(queue, beliefs, attempts).hypothesis_id == "H2"


def test_select_hypothesis_returns_none_for_empty_queue():
    assert select_hypothesis([], {}, {}) is None
