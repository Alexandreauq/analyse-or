import json
from datetime import datetime, timezone

import pytest

import ftse_intraday_collector as collector


def test_is_lse_likely_open_true_during_weekday_utc_window():
    assert collector.is_lse_likely_open(datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc))


def test_is_lse_likely_open_false_outside_hour_window():
    assert not collector.is_lse_likely_open(datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc))
    assert not collector.is_lse_likely_open(datetime(2026, 10, 26, 17, 0, tzinfo=timezone.utc))


def test_is_lse_likely_open_false_on_weekend():
    # 2026-10-24 est un samedi.
    assert not collector.is_lse_likely_open(datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc))


def test_load_ftse_intraday_history_returns_empty_list_when_missing(tmp_path):
    path = str(tmp_path / "absent.json")
    assert collector.load_ftse_intraday_history(path) == []


def test_load_ftse_intraday_history_returns_empty_list_on_corrupt_json(tmp_path):
    path = tmp_path / "corrompu.json"
    path.write_text("{not json", encoding="utf-8")
    assert collector.load_ftse_intraday_history(str(path)) == []


def test_trim_ftse_intraday_entries_drops_entries_older_than_retention():
    now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    entries = [
        {"time": "2026-08-01T10:00:00+00:00", "ticker": "ADM.L", "price": 20.0},  # trop vieux
        {"time": "2026-10-20T10:00:00+00:00", "ticker": "ADM.L", "price": 38.0},  # dans la fenetre
    ]
    trimmed = collector._trim_ftse_intraday_entries(entries, now)
    assert trimmed == [entries[1]]


def test_update_ftse_intraday_history_appends_and_persists(tmp_path):
    path = str(tmp_path / "ftse_intraday_history.json")
    now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    first = collector.update_ftse_intraday_history(
        [{"time": "2026-10-25T11:50:00+00:00", "ticker": "ADM.L", "price": 38.123456}],
        path=path, now=now,
    )
    assert first == [{"time": "2026-10-25T11:50:00+00:00", "ticker": "ADM.L", "price": 38.1235}]
    with open(path, encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk == first

    second = collector.update_ftse_intraday_history(
        [{"time": "2026-10-25T12:00:00+00:00", "ticker": "ADM.L", "price": 38.2}],
        path=path, now=now,
    )
    assert len(second) == 2


def test_update_ftse_intraday_history_trims_old_entries_on_each_call(tmp_path):
    path = str(tmp_path / "ftse_intraday_history.json")
    old_now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    collector.update_ftse_intraday_history(
        [{"time": "2026-09-01T11:00:00+00:00", "ticker": "ADM.L", "price": 30.0}],
        path=path, now=old_now,
    )
    later_now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    result = collector.update_ftse_intraday_history(
        [{"time": "2026-10-25T11:00:00+00:00", "ticker": "ADM.L", "price": 38.0}],
        path=path, now=later_now,
    )
    assert result == [{"time": "2026-10-25T11:00:00+00:00", "ticker": "ADM.L", "price": 38.0}]


def test_update_ftse_intraday_history_degrades_to_empty_list_on_nan():
    result = collector.update_ftse_intraday_history(
        [{"time": "2026-10-25T11:00:00+00:00", "ticker": "ADM.L", "price": float("nan")}],
        path="/chemin/invalide/pour/forcer/une/erreur.json",
    )
    assert result == []


class _FakeCloses:
    def __init__(self, values):
        self._values = values

    def dropna(self):
        return self

    def __len__(self):
        return len(self._values)

    @property
    def iloc(self):
        return self._values


def test_fetch_ftse_latest_prices_reads_the_last_close_per_ticker():
    donnees = {
        "ADM.L": {"Close": _FakeCloses([38.0, 38.5])},
        "BARC.L": {"Close": _FakeCloses([2.1, 2.15])},
    }

    def source_factice(tickers, **kwargs):
        return donnees

    prix = collector.fetch_ftse_latest_prices(["ADM.L", "BARC.L"], source=source_factice)
    assert prix == {"ADM.L": 38.5, "BARC.L": 2.15}


def test_fetch_ftse_latest_prices_skips_a_ticker_with_no_data_without_raising():
    donnees = {
        "ADM.L": {"Close": _FakeCloses([38.0])},
        "BARC.L": {"Close": _FakeCloses([])},
    }

    def source_factice(tickers, **kwargs):
        return donnees

    prix = collector.fetch_ftse_latest_prices(["ADM.L", "BARC.L"], source=source_factice)
    assert prix == {"ADM.L": 38.0}


def test_fetch_ftse_latest_prices_returns_empty_dict_when_the_whole_call_fails():
    def source_factice(tickers, **kwargs):
        raise RuntimeError("panne reseau")

    assert collector.fetch_ftse_latest_prices(["ADM.L"], source=source_factice) == {}


def test_main_skips_outside_market_hours(tmp_path):
    history_path = str(tmp_path / "hist.json")
    collector.main(
        now=datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc),  # samedi
        history_path=history_path,
        price_source=lambda tickers, **k: (_ for _ in ()).throw(AssertionError("ne doit jamais etre appele")),
    )
    assert collector.load_ftse_intraday_history(history_path) == []


def test_main_collects_ftse_tickers_only(tmp_path):
    indices_path = tmp_path / "indices.json"
    indices_path.write_text(json.dumps({"companies": [
        {"ticker": "ADM.L", "index": "FTSE"},
        {"ticker": "MC.PA", "index": "CAC40"},
    ]}), encoding="utf-8")
    history_path = str(tmp_path / "hist.json")

    appels = []

    def source_factice(tickers, **kwargs):
        appels.append(list(tickers))
        # Un seul ticker -> yfinance renvoie une structure plate (pas
        # groupee par ticker), voir fetch_ftse_latest_prices.
        return {"Close": _FakeCloses([38.5])}

    collector.main(
        now=datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc),
        indices_path=str(indices_path),
        history_path=history_path,
        price_source=source_factice,
    )
    assert appels == [["ADM.L"]]
    result = collector.load_ftse_intraday_history(history_path)
    assert result == [{"time": "2026-10-26T10:00:00+00:00", "ticker": "ADM.L", "price": 38.5}]
