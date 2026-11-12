# tests/test_etf_catalog.py
from etf_catalog import ETF_CATALOG

def test_etf_catalog_has_between_18_and_25_entries():
    assert 18 <= len(ETF_CATALOG) <= 25

def test_etf_catalog_tickers_are_unique():
    tickers = [e["ticker"] for e in ETF_CATALOG]
    assert len(tickers) == len(set(tickers))

def test_etf_catalog_entries_have_required_fields():
    required = {
        "ticker", "name", "isin", "benchmark_name", "benchmark_ticker",
        "ter_pct", "replication", "distribution", "pea_eligible",
    }
    for entry in ETF_CATALOG:
        assert required.issubset(entry.keys()), entry["ticker"]

def test_etf_catalog_enum_fields_are_valid():
    for entry in ETF_CATALOG:
        assert entry["replication"] in ("physique", "synthetique"), entry["ticker"]
        assert entry["distribution"] in ("capitalisant", "distribuant"), entry["ticker"]
        assert isinstance(entry["pea_eligible"], bool), entry["ticker"]
        assert isinstance(entry["ter_pct"], (int, float)) and 0 <= entry["ter_pct"] < 2, entry["ticker"]
