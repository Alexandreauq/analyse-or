import json

import ibkr_bot.signals as signals


def _indices(updated="2026-09-14", companies=None, currencies=None):
    return {
        "updated": updated,
        "index_currency": currencies or {
            "CAC40": "EUR", "DAX": "EUR", "NASDAQ": "USD", "DOW": "USD",
            "FTSE": "GBP", "SMI": "CHF", "IBEX35": "EUR", "FTSEMIB": "EUR",
            "NIKKEI225": "JPY", "HANGSENG": "HKD",
        },
        "companies": companies if companies is not None else [],
    }


def _paper_position(**overrides):
    position = {
        "id": "GLE.PA-2026-09-14",
        "ticker": "GLE.PA",
        "name": "Societe Generale",
        "index": "CAC40",
        "status": "open",
        "entry_date": "2026-09-14",
        "entry_price": 73.63,
        "target_exit_price": 105.19,
    }
    position.update(overrides)
    return position


def test_collect_new_signals_keeps_only_today_open_positions():
    positions = [
        _paper_position(id="GLE.PA-2026-09-14", ticker="GLE.PA", entry_date="2026-09-14"),
        _paper_position(id="BN.PA-2026-09-13", ticker="BN.PA", entry_date="2026-09-13"),
        _paper_position(id="MC.PA-2026-09-14", ticker="MC.PA", entry_date="2026-09-14",
                        status="closed"),
    ]
    indices = _indices(companies=[
        {"ticker": "GLE.PA", "index": "CAC40", "score": 12.4, "current_price": 74.1},
        {"ticker": "BN.PA", "index": "CAC40", "score": 30.0, "current_price": 60.0},
        {"ticker": "MC.PA", "index": "CAC40", "score": 26.9, "current_price": 415.0},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert [s["ticker"] for s in found] == ["GLE.PA"]
    assert rejets == []


def test_collect_new_signals_does_not_rebuy_a_signal_opened_yesterday():
    """Une alerte 'entree' reste affichee plusieurs jours : la position
    paper ouverte hier est toujours 'open' aujourd'hui, elle ne doit pas
    ressortir comme un nouveau signal (voir spec 4.6)."""
    positions = [_paper_position(id="GLE.PA-2026-09-13", entry_date="2026-09-13")]
    indices = _indices(companies=[
        {"ticker": "GLE.PA", "index": "CAC40", "score": 12.4, "current_price": 74.1},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")
    assert found == []
    assert rejets == []


def test_collect_new_signals_attaches_score_currency_and_current_price():
    positions = [_paper_position()]
    indices = _indices(companies=[
        {
            "ticker": "GLE.PA", "index": "CAC40", "score": 12.4, "current_price": 74.1,
            "sector": "Financial Services",
        },
    ])
    found, _ = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found[0] == {
        "id": "GLE.PA-2026-09-14",
        "ticker": "GLE.PA",
        "name": "Societe Generale",
        "index": "CAC40",
        "currency": "EUR",
        "sector": "Financial Services",
        "entry_date": "2026-09-14",
        "paper_entry_price": 73.63,
        "target_exit_price": 105.19,
        "score": 12.4,
        "current_price": 74.1,
    }


def test_collect_new_signals_returns_nothing_when_indices_are_stale():
    """Donnees du jour perimees : aucun ordre ce jour-la, ni entree ni
    sortie (voir spec 4.5)."""
    positions = [_paper_position()]
    indices = _indices(updated="2026-09-13", companies=[
        {"ticker": "GLE.PA", "index": "CAC40", "score": 12.4, "current_price": 74.1},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found == []
    assert rejets == [{"ticker": None, "raison": "donnees_perimees"}]


def test_collect_new_signals_rejects_nikkei_and_hangseng():
    positions = [
        _paper_position(id="7203.T-2026-09-14", ticker="7203.T", index="NIKKEI225"),
        _paper_position(id="0005.HK-2026-09-14", ticker="0005.HK", index="HANGSENG"),
    ]
    indices = _indices(companies=[
        {"ticker": "7203.T", "index": "NIKKEI225", "score": 40.0, "current_price": 2800.0},
        {"ticker": "0005.HK", "index": "HANGSENG", "score": 35.0, "current_price": 65.0},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found == []
    assert rejets == [
        {"ticker": "7203.T", "raison": "index_hors_perimetre"},
        {"ticker": "0005.HK", "raison": "index_hors_perimetre"},
    ]


def test_collect_new_signals_rejects_ticker_absent_from_indices():
    positions = [_paper_position(ticker="DELISTED.PA", id="DELISTED.PA-2026-09-14")]
    indices = _indices(companies=[])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found == []
    assert rejets == [{"ticker": "DELISTED.PA", "raison": "score_indisponible"}]


def test_collect_new_signals_rejects_missing_or_nan_score():
    positions = [
        _paper_position(id="A.PA-2026-09-14", ticker="A.PA"),
        _paper_position(id="B.PA-2026-09-14", ticker="B.PA"),
    ]
    indices = _indices(companies=[
        {"ticker": "A.PA", "index": "CAC40", "score": None, "current_price": 10.0},
        {"ticker": "B.PA", "index": "CAC40", "score": float("nan"), "current_price": 10.0},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found == []
    assert rejets == [
        {"ticker": "A.PA", "raison": "score_indisponible"},
        {"ticker": "B.PA", "raison": "score_indisponible"},
    ]


def test_collect_new_signals_rejects_missing_or_nan_current_price():
    positions = [
        _paper_position(id="A.PA-2026-09-14", ticker="A.PA"),
        _paper_position(id="B.PA-2026-09-14", ticker="B.PA"),
    ]
    indices = _indices(companies=[
        {"ticker": "A.PA", "index": "CAC40", "score": 12.0, "current_price": None},
        {"ticker": "B.PA", "index": "CAC40", "score": 12.0, "current_price": float("nan")},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found == []
    assert rejets == [
        {"ticker": "A.PA", "raison": "prix_indisponible"},
        {"ticker": "B.PA", "raison": "prix_indisponible"},
    ]


def test_collect_new_signals_rejects_missing_target_exit_price():
    positions = [_paper_position(target_exit_price=None)]
    indices = _indices(companies=[
        {"ticker": "GLE.PA", "index": "CAC40", "score": 12.4, "current_price": 74.1},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found == []
    assert rejets == [{"ticker": "GLE.PA", "raison": "prix_indisponible"}]


def test_indices_are_fresh_compares_updated_to_today():
    assert signals.indices_are_fresh({"updated": "2026-09-14"}, "2026-09-14") is True
    assert signals.indices_are_fresh({"updated": "2026-09-13"}, "2026-09-14") is False
    assert signals.indices_are_fresh({}, "2026-09-14") is False


def test_load_indices_degrades_to_empty_dict(tmp_path):
    assert signals.load_indices(str(tmp_path / "absent.json")) == {}
    corrupted = tmp_path / "indices.json"
    corrupted.write_text("{not json", encoding="utf-8")
    assert signals.load_indices(str(corrupted)) == {}


def test_load_indices_reads_a_valid_file(tmp_path):
    path = tmp_path / "indices.json"
    path.write_text(json.dumps(_indices()), encoding="utf-8")
    assert signals.load_indices(str(path))["updated"] == "2026-09-14"


def test_load_signal_tracking_degrades_to_empty_list(tmp_path):
    assert signals.load_signal_tracking(str(tmp_path / "absent.json")) == []
    corrupted = tmp_path / "signal_tracking.json"
    corrupted.write_text("[[[", encoding="utf-8")
    assert signals.load_signal_tracking(str(corrupted)) == []


def test_load_signal_tracking_reads_positions_key(tmp_path):
    path = tmp_path / "signal_tracking.json"
    path.write_text(json.dumps({"positions": [_paper_position()]}), encoding="utf-8")
    result = signals.load_signal_tracking(str(path))
    assert [p["ticker"] for p in result] == ["GLE.PA"]


def test_is_missing_covers_none_and_nan():
    assert signals._is_missing(None) is True
    assert signals._is_missing(float("nan")) is True
    assert signals._is_missing(0.0) is False
    assert signals._is_missing(12.4) is False
