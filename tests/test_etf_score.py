import pytest
import pandas as pd

import etf_score
from etf_score import compute_tracking_difference, fetch_etf_data, build_etf_entry

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


class _FakeEtfTicker:
    def __init__(self, info, history_df):
        self.info = info
        self._history_df = history_df

    def history(self, period=None):
        return self._history_df


def _fake_history_df(rows):
    # rows: list of (date_str, close)
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame({"Close": [r[1] for r in rows]}, index=idx)


def test_fetch_etf_data_returns_price_and_history(monkeypatch):
    history_df = _fake_history_df([("2026-11-01", 100.0), ("2026-11-02", 101.5)])
    fake = _FakeEtfTicker({"dividendYield": 0.015}, history_df)
    monkeypatch.setattr(etf_score.yf, "Ticker", lambda t: fake)
    result = fetch_etf_data("CW8.PA")
    assert result["current_price"] == pytest.approx(101.5)
    assert result["price_history"] == [
        {"date": "2026-11-01", "close": pytest.approx(100.0)},
        {"date": "2026-11-02", "close": pytest.approx(101.5)},
    ]
    assert result["dividend_yield"] == pytest.approx(0.015)


def test_fetch_etf_data_dividend_yield_none_for_capitalizing_etf(monkeypatch):
    history_df = _fake_history_df([("2026-11-01", 100.0), ("2026-11-02", 101.5)])
    fake = _FakeEtfTicker({}, history_df)  # pas de cle dividendYield
    monkeypatch.setattr(etf_score.yf, "Ticker", lambda t: fake)
    result = fetch_etf_data("CW8.PA")
    assert result["dividend_yield"] is None


def test_fetch_etf_data_raises_when_history_empty(monkeypatch):
    fake = _FakeEtfTicker({}, _fake_history_df([]))
    monkeypatch.setattr(etf_score.yf, "Ticker", lambda t: fake)
    with pytest.raises(ValueError):
        fetch_etf_data("CW8.PA")


_CATALOG_ENTRY = {
    "ticker": "CW8.PA", "name": "Amundi MSCI World", "isin": "LU1681043599",
    "benchmark_name": "MSCI World", "benchmark_ticker": "URTH",
    "ter_pct": 0.38, "replication": "physique",
    "distribution": "capitalisant", "pea_eligible": True,
}


def test_build_etf_entry_basic_shape(monkeypatch):
    monkeypatch.setattr(etf_score, "fetch_etf_data", lambda ticker: {
        "current_price": 450.0,
        "price_history": [{"date": "2026-11-01", "close": 440.0}, {"date": "2026-11-02", "close": 450.0}],
        "dividend_yield": None,
    })
    entry = build_etf_entry(_CATALOG_ENTRY, benchmark_price_history=None)
    assert entry["ticker"] == "CW8.PA"
    assert entry["index"] == "ETF"
    assert entry["is_etf"] is True
    assert entry["current_price"] == 450.0
    assert entry["news"] == []
    assert "score" not in entry
    assert "factors" not in entry
    assert "sector" not in entry
    assert "is_financial" not in entry
    assert "is_trust" not in entry
    assert "alerts" not in entry
    assert entry["etf_info"]["ter_pct"] == 0.38
    assert entry["etf_info"]["pea_eligible"] is True
    assert entry["etf_info"]["dividend_yield"] is None
    assert entry["etf_info"]["tracking_difference_pct"] is None  # pas de benchmark fourni


def test_build_etf_entry_computes_tracking_difference_when_benchmark_given(monkeypatch):
    monkeypatch.setattr(etf_score, "fetch_etf_data", lambda ticker: {
        "current_price": 110.0,
        "price_history": [{"date": "2026-11-01", "close": 100.0}, {"date": "2026-11-02", "close": 110.0}],
        "dividend_yield": None,
    })
    benchmark_history = [{"date": "2026-11-01", "close": 1000.0}, {"date": "2026-11-02", "close": 1060.0}]
    entry = build_etf_entry(_CATALOG_ENTRY, benchmark_price_history=benchmark_history)
    assert entry["etf_info"]["tracking_difference_pct"] == pytest.approx(4.0, abs=0.01)


def test_build_etf_entry_includes_price_history_for_pipeline(monkeypatch):
    # Forme confirmee contre le code reel d'indices_score.py (extract_ratios,
    # ligne ~4017 : ratios["_price_history_daily"] = [{"date", "ticker",
    # "price"}, ...]) et update_price_history (ligne ~4696 : entry["price"]).
    # Le champ est "price", pas "close" -- la cle "close" n'existe que dans
    # fetch_etf_data()["price_history"], pas dans _price_history_daily.
    monkeypatch.setattr(etf_score, "fetch_etf_data", lambda ticker: {
        "current_price": 450.0,
        "price_history": [{"date": "2026-11-01", "close": 440.0}],
        "dividend_yield": None,
    })
    entry = build_etf_entry(_CATALOG_ENTRY, benchmark_price_history=None)
    assert entry["_price_history_daily"] == [
        {"date": "2026-11-01", "ticker": "CW8.PA", "price": 440.0}
    ]
