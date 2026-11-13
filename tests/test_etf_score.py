import math

import pytest
import pandas as pd

import etf_score
from etf_score import compute_tracking_difference, fetch_etf_data, build_etf_entry, _align_by_date

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


def test_align_by_date_keeps_only_common_dates_in_order():
    # L'ETF couvre 01-02-03, le benchmark 02-03-04 -- seules les dates
    # communes (02, 03) doivent ressortir, dans l'ordre chronologique.
    etf_history = [
        {"date": "2026-11-01", "close": 100.0},
        {"date": "2026-11-02", "close": 101.0},
        {"date": "2026-11-03", "close": 102.0},
    ]
    benchmark_history = [
        {"date": "2026-11-02", "price": 1000.0},
        {"date": "2026-11-03", "price": 1010.0},
        {"date": "2026-11-04", "price": 1020.0},
    ]
    etf_values, benchmark_values = _align_by_date(etf_history, benchmark_history)
    assert etf_values == [101.0, 102.0]
    assert benchmark_values == [1000.0, 1010.0]


def test_align_by_date_uses_the_overlap_not_the_full_range():
    # Le cas reel qui motive cette fonction : l'ETF n'a que 2 ans
    # d'historique, le benchmark 6 ans -- sans intersection, comparer
    # etf[0]/etf[-1] a benchmark[0]/benchmark[-1] donnerait un ecart
    # silencieusement faux (periodes differentes). Ici, seule la date
    # commune (11-02) existe des deux cotes -- une seule paire de valeurs,
    # donc compute_tracking_difference (qui exige >= 2 points) y verrait
    # une serie trop courte, pas un chiffre invente.
    etf_history = [{"date": "2026-11-02", "close": 101.0}]
    benchmark_history = [
        {"date": "2021-01-04", "price": 900.0},
        {"date": "2026-11-02", "price": 1000.0},
    ]
    etf_values, benchmark_values = _align_by_date(etf_history, benchmark_history)
    assert etf_values == [101.0]
    assert benchmark_values == [1000.0]
    assert compute_tracking_difference(etf_values, benchmark_values) is None


def test_align_by_date_returns_empty_lists_when_no_common_dates():
    etf_history = [{"date": "2026-11-01", "close": 100.0}]
    benchmark_history = [{"date": "2026-10-01", "price": 1000.0}]
    assert _align_by_date(etf_history, benchmark_history) == ([], [])


def test_align_by_date_sorts_even_when_input_is_out_of_order():
    etf_history = [
        {"date": "2026-11-03", "close": 102.0},
        {"date": "2026-11-01", "close": 100.0},
        {"date": "2026-11-02", "close": 101.0},
    ]
    benchmark_history = [
        {"date": "2026-11-02", "price": 1000.0},
        {"date": "2026-11-01", "price": 990.0},
        {"date": "2026-11-03", "price": 1010.0},
    ]
    etf_values, benchmark_values = _align_by_date(etf_history, benchmark_history)
    assert etf_values == [100.0, 101.0, 102.0]
    assert benchmark_values == [990.0, 1000.0, 1010.0]


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


def test_fetch_etf_data_drops_nan_rows_from_history(monkeypatch):
    # Un seul point NaN au milieu d'une serie par ailleurs valide : meme
    # convention que history.dropna() dans fetch_company_financials
    # (indices_score.py) -- la ligne NaN est ecartee, pas propagee, et le
    # reste de la serie (toujours >= 1 point valide) est conserve.
    history_df = _fake_history_df([
        ("2026-11-01", 100.0), ("2026-11-02", float("nan")), ("2026-11-03", 102.0),
    ])
    fake = _FakeEtfTicker({}, history_df)
    monkeypatch.setattr(etf_score.yf, "Ticker", lambda t: fake)
    result = fetch_etf_data("CW8.PA")
    assert result["price_history"] == [
        {"date": "2026-11-01", "close": pytest.approx(100.0)},
        {"date": "2026-11-03", "close": pytest.approx(102.0)},
    ]
    assert result["current_price"] == pytest.approx(102.0)
    assert math.isfinite(result["current_price"])
    for point in result["price_history"]:
        assert math.isfinite(point["close"])


def test_fetch_etf_data_raises_when_history_entirely_nan(monkeypatch):
    # Toute la serie est inutilisable une fois les NaN retires -- meme
    # garde que l'historique vide avant nettoyage (le "vide" doit aussi
    # couvrir "vide apres nettoyage", pas seulement "vide au depart").
    history_df = _fake_history_df([
        ("2026-11-01", float("nan")), ("2026-11-02", float("nan")),
    ])
    fake = _FakeEtfTicker({}, history_df)
    monkeypatch.setattr(etf_score.yf, "Ticker", lambda t: fake)
    with pytest.raises(ValueError):
        fetch_etf_data("CW8.PA")


class _FakeEtfTickerInfoRaises:
    """.info leve une exception a l'acces (ex. timeout/panne reseau sur cet
    endpoint specifique) alors que .history() reussit -- doit degrader vers
    dividend_yield=None plutot que de faire echouer tout fetch_etf_data
    (Fix 6 : un hic sur le dividende ne doit pas faire tomber l'ETF entier)."""

    def __init__(self, history_df):
        self._history_df = history_df

    @property
    def info(self):
        raise RuntimeError("panne reseau sur .info")

    def history(self, period=None):
        return self._history_df


def test_fetch_etf_data_degrades_dividend_yield_when_info_raises(monkeypatch):
    history_df = _fake_history_df([("2026-11-01", 100.0), ("2026-11-02", 101.5)])
    fake = _FakeEtfTickerInfoRaises(history_df)
    monkeypatch.setattr(etf_score.yf, "Ticker", lambda t: fake)
    result = fetch_etf_data("CW8.PA")
    assert result["dividend_yield"] is None
    assert result["current_price"] == pytest.approx(101.5)
    assert result["price_history"] == [
        {"date": "2026-11-01", "close": pytest.approx(100.0)},
        {"date": "2026-11-02", "close": pytest.approx(101.5)},
    ]


def test_fetch_etf_data_rejects_non_finite_dividend_yield(monkeypatch):
    # Un dividend_yield NaN/inf renvoye par yfinance ne doit jamais
    # atteindre le payload tel quel (json.dump(..., allow_nan=False) dans
    # main() planterait sinon) -- traite comme None, meme convention que
    # l'absence pure et simple de la cle dividendYield.
    history_df = _fake_history_df([("2026-11-01", 100.0), ("2026-11-02", 101.5)])
    fake = _FakeEtfTicker({"dividendYield": float("nan")}, history_df)
    monkeypatch.setattr(etf_score.yf, "Ticker", lambda t: fake)
    result = fetch_etf_data("CW8.PA")
    assert result["dividend_yield"] is None


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
    # Cle "price", pas "close" -- c'est la forme reelle produite par
    # indices_score.fetch_index_price_history(), la seule source reelle
    # de benchmark_price_history en production (voir le point d'appel
    # dans indices_score.py::main()).
    benchmark_history = [{"date": "2026-11-01", "price": 1000.0}, {"date": "2026-11-02", "price": 1060.0}]
    entry = build_etf_entry(_CATALOG_ENTRY, benchmark_price_history=benchmark_history)
    assert entry["etf_info"]["tracking_difference_pct"] == pytest.approx(4.0, abs=0.01)


def test_build_etf_entry_aligns_dates_before_computing_tracking_difference(monkeypatch):
    # Le benchmark porte une date supplementaire plus ancienne (couverture
    # plus longue, cas reel : ETF ~2 ans, benchmark ~6 ans) -- sans
    # alignement, comparer bêtement [0]/[-1] des deux series donnerait un
    # ecart calcule sur des periodes differentes, silencieusement faux.
    # Avec alignement sur les seules dates communes (11-01, 11-02), le
    # resultat doit etre identique a des series deja alignees d'entree.
    monkeypatch.setattr(etf_score, "fetch_etf_data", lambda ticker: {
        "current_price": 110.0,
        "price_history": [{"date": "2026-11-01", "close": 100.0}, {"date": "2026-11-02", "close": 110.0}],
        "dividend_yield": None,
    })
    benchmark_history = [
        {"date": "2021-01-04", "price": 800.0},  # hors de la couverture de l'ETF
        {"date": "2026-11-01", "price": 1000.0},
        {"date": "2026-11-02", "price": 1060.0},
    ]
    entry = build_etf_entry(_CATALOG_ENTRY, benchmark_price_history=benchmark_history)
    assert entry["etf_info"]["tracking_difference_pct"] == pytest.approx(4.0, abs=0.01)


def test_build_etf_entry_tracking_difference_none_when_no_overlapping_dates(monkeypatch):
    monkeypatch.setattr(etf_score, "fetch_etf_data", lambda ticker: {
        "current_price": 110.0,
        "price_history": [{"date": "2026-11-01", "close": 100.0}, {"date": "2026-11-02", "close": 110.0}],
        "dividend_yield": None,
    })
    benchmark_history = [{"date": "2021-01-04", "price": 800.0}, {"date": "2021-01-05", "price": 810.0}]
    entry = build_etf_entry(_CATALOG_ENTRY, benchmark_price_history=benchmark_history)
    assert entry["etf_info"]["tracking_difference_pct"] is None


def test_build_etf_entry_includes_isin_from_catalog(monkeypatch):
    monkeypatch.setattr(etf_score, "fetch_etf_data", lambda ticker: {
        "current_price": 450.0,
        "price_history": [{"date": "2026-11-01", "close": 440.0}, {"date": "2026-11-02", "close": 450.0}],
        "dividend_yield": None,
    })
    entry = build_etf_entry(_CATALOG_ENTRY, benchmark_price_history=None)
    assert entry["etf_info"]["isin"] == "LU1681043599"


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
