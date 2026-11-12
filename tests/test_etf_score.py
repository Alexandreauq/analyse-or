import pytest

from etf_score import compute_tracking_difference

def test_compute_tracking_difference_positive_when_etf_outperforms():
    # ETF +10% sur la période, indice +6% -> écart de +4 points
    etf_prices = [100.0, 110.0]
    benchmark_prices = [1000.0, 1060.0]
    result = compute_tracking_difference(etf_prices, benchmark_prices)
    assert result == pytest.approx(4.0, abs=0.01)

def test_compute_tracking_difference_negative_when_etf_underperforms():
    etf_prices = [100.0, 103.0]
    benchmark_prices = [1000.0, 1060.0]
    result = compute_tracking_difference(etf_prices, benchmark_prices)
    assert result == pytest.approx(-3.0, abs=0.01)

def test_compute_tracking_difference_none_when_series_too_short():
    assert compute_tracking_difference([100.0], [1000.0]) is None
    assert compute_tracking_difference([], []) is None

def test_compute_tracking_difference_none_when_benchmark_starts_at_zero():
    assert compute_tracking_difference([100.0, 110.0], [0.0, 10.0]) is None
