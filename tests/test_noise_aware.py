"""Noise-aware evaluation: repeated-run stats + noise-aware acceptance."""

from pro2.verification import compare_metrics


def test_compare_metrics_noise_weight_rejects_tiny_delta_within_noise():
    # delta = 0.2 with std = 0.4 and noise_weight = 1.0 requires delta >= 0.2+0.4.
    accepted, _delta, _reason = compare_metrics(
        1.0, 1.2, direction="maximize", min_delta=0.1, candidate_std=0.4, noise_weight=1.0
    )
    assert accepted is False


def test_compare_metrics_noise_weight_accepts_clear_signal():
    accepted, _delta, _reason = compare_metrics(
        1.0, 1.5, direction="maximize", min_delta=0.1, candidate_std=0.1, noise_weight=2.0
    )
    assert accepted is True


def test_compare_metrics_default_ignores_noise():
    accepted, _delta, _reason = compare_metrics(
        1.0, 1.01, direction="maximize", min_delta=0.001, candidate_std=0.5
    )
    assert accepted is True


def test_measure_metric_records_stats(toy_repo, tmp_path):
    from pro2.config import MetricSpec
    from pro2.verification import measure_metric as mm

    spec = MetricSpec(command="echo SCORE=1.5", metric_regex=r"SCORE=([0-9.]+)", repeat=3)
    measurement = mm(spec, tmp_path, tmp_path, "label")
    assert measurement.value == 1.5
    stats = measurement.stats
    assert stats["count"] == 3
    assert stats["mean"] == 1.5
    assert stats["std"] == 0.0
    assert stats["min"] == 1.5
    assert stats["max"] == 1.5
    assert measurement.to_dict()["stats"]["count"] == 3


def test_measure_metric_stats_vary_with_values(toy_repo, tmp_path):
    from pro2.verification import _measurement_stats

    stats = _measurement_stats([1.0, 2.0, 3.0])
    assert stats["mean"] == 2.0
    assert stats["min"] == 1.0
    assert stats["max"] == 3.0
    assert stats["std"] > 0
