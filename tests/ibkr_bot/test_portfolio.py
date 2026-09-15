import json

import pytest

import ibkr_bot.portfolio as portfolio


def _signal(ticker, score, **overrides):
    signal = {
        "id": f"{ticker}-2026-09-14",
        "ticker": ticker,
        "name": ticker,
        "index": "CAC40",
        "currency": "EUR",
        "entry_date": "2026-09-14",
        "paper_entry_price": 100.0,
        "target_exit_price": 130.0,
        "score": score,
        "current_price": 100.0,
    }
    signal.update(overrides)
    return signal


def _plan(ticker, quantite=5, devise_compte="EUR", cout=500.0, motif=None):
    return {
        "ticker": ticker,
        "quantite": quantite,
        "devise_cotation": devise_compte,
        "devise_compte": devise_compte,
        "taux_de_change": 1.0,
        "budget_converti": 500.0,
        "prix_unitaire_cotation": 100.0,
        "cout_estime_devise_compte": cout,
        "motif": motif,
    }


def _bot_position(ticker, **overrides):
    position = {
        "id": f"{ticker}-2026-06-14",
        "ticker": ticker,
        "conid": 4901,
        "index": "CAC40",
        "devise_cotation": "EUR",
        "devise_compte": "EUR",
        "quantite": 5,
        "prix_execution_cotation": 100.0,
        "prix_execution_reference": 100.0,
        "paper_entry_price": 99.5,
        "entry_date": "2026-06-14",
        "target_exit_price": 130.0,
        "date_limite": "2026-12-14",
    }
    position.update(overrides)
    return position


_CASH_ILLIMITE = {"EUR": 1e9, "USD": 1e9, "GBP": 1e9, "CHF": 1e9}


# --- classement ------------------------------------------------------

def test_rank_signals_orders_by_descending_score():
    signaux = [_signal("A.PA", 10.0), _signal("B.PA", 48.8), _signal("C.PA", 26.9)]
    assert [s["ticker"] for s in portfolio.rank_signals(signaux)] == [
        "B.PA", "C.PA", "A.PA"]


def test_rank_signals_breaks_ties_by_ticker_for_determinism():
    signaux = [_signal("Z.PA", 20.0), _signal("A.PA", 20.0), _signal("M.PA", 20.0)]
    assert [s["ticker"] for s in portfolio.rank_signals(signaux)] == [
        "A.PA", "M.PA", "Z.PA"]


def test_rank_signals_does_not_mutate_its_input():
    signaux = [_signal("A.PA", 10.0), _signal("B.PA", 48.8)]
    portfolio.rank_signals(signaux)
    assert [s["ticker"] for s in signaux] == ["A.PA", "B.PA"]


# --- plafond ---------------------------------------------------------

def test_free_slots_with_no_open_position():
    assert portfolio.free_slots([]) == 10


def test_free_slots_with_one_open_position():
    assert portfolio.free_slots([_bot_position("A.PA")]) == 9


def test_free_slots_with_exactly_ten_open_positions():
    assert portfolio.free_slots([_bot_position(f"T{i}.PA") for i in range(10)]) == 0


def test_free_slots_never_goes_negative():
    assert portfolio.free_slots([_bot_position(f"T{i}.PA") for i in range(12)]) == 0


def test_max_positions_is_ten():
    assert portfolio.MAX_POSITIONS == 10


# --- selection des entrees -------------------------------------------

def test_select_entries_fills_free_slots_in_rank_order():
    signaux = [_signal("A.PA", 10.0), _signal("B.PA", 48.8), _signal("C.PA", 26.9)]
    plans = {t: _plan(t) for t in ("A.PA", "B.PA", "C.PA")}

    retenus, rejets = portfolio.select_entries(signaux, [], plans, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA", "C.PA", "A.PA"]
    assert [r["rang"] for r in retenus] == [1, 2, 3]
    assert rejets == []


def test_select_entries_drops_surplus_signals_when_the_cap_is_reached():
    """Sursouscription (spec 3.5) : les signaux qui ne rentrent pas sont
    perdus, pas mis en file d'attente, et journalises avec leur rang."""
    ouvertes = [_bot_position(f"OPEN{i}.PA") for i in range(9)]
    signaux = [_signal("A.PA", 10.0), _signal("B.PA", 48.8), _signal("C.PA", 26.9)]
    plans = {t: _plan(t) for t in ("A.PA", "B.PA", "C.PA")}

    retenus, rejets = portfolio.select_entries(signaux, ouvertes, plans, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA"]
    assert rejets == [
        {"ticker": "C.PA", "rang": 2, "score": 26.9,
         "raison": "signal_ignore_plafond_atteint"},
        {"ticker": "A.PA", "rang": 3, "score": 10.0,
         "raison": "signal_ignore_plafond_atteint"},
    ]


def test_select_entries_takes_nothing_when_the_cap_is_already_reached():
    ouvertes = [_bot_position(f"OPEN{i}.PA") for i in range(10)]
    signaux = [_signal("A.PA", 10.0)]

    retenus, rejets = portfolio.select_entries(
        signaux, ouvertes, {"A.PA": _plan("A.PA")}, _CASH_ILLIMITE)

    assert retenus == []
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 10.0,
                       "raison": "signal_ignore_plafond_atteint"}]


def test_select_entries_zero_share_signal_does_not_consume_a_slot():
    """Spec 3.3 : la place liberee par un signal a 0 action reste
    disponible pour le signal suivant du classement. Ce test echoue si
    le filtre plafond est applique AVANT le filtre 0 action."""
    ouvertes = [_bot_position(f"OPEN{i}.PA") for i in range(9)]
    signaux = [_signal("CHER.PA", 90.0), _signal("B.PA", 48.8)]
    plans = {
        "CHER.PA": _plan("CHER.PA", quantite=0, cout=0.0,
                         motif="signal_ignore_prix_unitaire_superieur_au_budget"),
        "B.PA": _plan("B.PA"),
    }

    retenus, rejets = portfolio.select_entries(signaux, ouvertes, plans, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA"]
    assert rejets == [{
        "ticker": "CHER.PA", "rang": 1, "score": 90.0,
        "raison": "signal_ignore_prix_unitaire_superieur_au_budget",
    }]


def test_select_entries_skips_a_ticker_already_held_by_the_bot():
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 10.0)]
    plans = {"A.PA": _plan("A.PA"), "B.PA": _plan("B.PA")}

    retenus, rejets = portfolio.select_entries(
        signaux, [_bot_position("A.PA")], plans, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA"]
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 48.8,
                       "raison": "deja_en_portefeuille"}]


def test_select_entries_rejects_a_signal_without_a_plan():
    signaux = [_signal("A.PA", 48.8)]
    retenus, rejets = portfolio.select_entries(signaux, [], {}, _CASH_ILLIMITE)

    assert retenus == []
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 48.8,
                       "raison": "plan_indisponible"}]


def test_select_entries_propagates_an_invalid_price_reason():
    signaux = [_signal("A.PA", 48.8)]
    plans = {"A.PA": _plan("A.PA", quantite=0, cout=0.0, motif="prix_ou_taux_invalide")}

    retenus, rejets = portfolio.select_entries(signaux, [], plans, _CASH_ILLIMITE)

    assert retenus == []
    assert rejets[0]["raison"] == "prix_ou_taux_invalide"


# --- garde-fou de solde (spec 9.9) -----------------------------------

def test_select_entries_rejects_a_signal_the_cash_cannot_fund():
    signaux = [_signal("A.PA", 48.8)]
    plans = {"A.PA": _plan("A.PA", cout=500.0)}

    retenus, rejets = portfolio.select_entries(signaux, [], plans, {"EUR": 100.0})

    assert retenus == []
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 48.8,
                       "raison": "solde_insuffisant"}]


def test_select_entries_decrements_the_cash_cumulatively():
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 26.9), _signal("C.PA", 10.0)]
    plans = {t: _plan(t, cout=500.0) for t in ("A.PA", "B.PA", "C.PA")}

    retenus, rejets = portfolio.select_entries(signaux, [], plans, {"EUR": 1100.0})

    assert [r["signal"]["ticker"] for r in retenus] == ["A.PA", "B.PA"]
    assert rejets == [{"ticker": "C.PA", "rang": 3, "score": 10.0,
                       "raison": "solde_insuffisant"}]


def test_select_entries_keeps_currencies_independent():
    """Un EUR epuise ne doit pas bloquer un signal finance en USD."""
    signaux = [
        _signal("A.PA", 48.8, currency="EUR"),
        _signal("ADBE", 26.9, index="NASDAQ", currency="USD"),
    ]
    plans = {
        "A.PA": _plan("A.PA", devise_compte="EUR", cout=500.0),
        "ADBE": _plan("ADBE", devise_compte="USD", cout=540.0),
    }

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, {"EUR": 100.0, "USD": 1000.0})

    assert [r["signal"]["ticker"] for r in retenus] == ["ADBE"]
    assert rejets[0]["raison"] == "solde_insuffisant"


def test_select_entries_treats_an_unknown_currency_as_zero_cash():
    signaux = [_signal("ABBN.SW", 48.8, index="SMI", currency="CHF")]
    plans = {"ABBN.SW": _plan("ABBN.SW", devise_compte="CHF", cout=470.0)}

    retenus, rejets = portfolio.select_entries(signaux, [], plans, {"EUR": 5000.0})

    assert retenus == []
    assert rejets[0]["raison"] == "solde_insuffisant"


def test_select_entries_accepts_a_cost_exactly_equal_to_the_cash():
    signaux = [_signal("A.PA", 48.8)]
    plans = {"A.PA": _plan("A.PA", cout=500.0)}

    retenus, rejets = portfolio.select_entries(signaux, [], plans, {"EUR": 500.0})

    assert len(retenus) == 1
    assert rejets == []


# --- persistance -----------------------------------------------------

def test_load_positions_degrades_to_empty_list(tmp_path):
    assert portfolio.load_positions(str(tmp_path / "absent.json")) == []
    corrupted = tmp_path / "positions.json"
    corrupted.write_text("nope", encoding="utf-8")
    assert portfolio.load_positions(str(corrupted)) == []


def test_save_positions_then_load_positions_round_trips(tmp_path):
    path = str(tmp_path / "nested" / "positions.json")
    positions = [_bot_position("A.PA")]
    portfolio.save_positions(positions, path)

    assert portfolio.load_positions(path) == positions
    assert json.loads(open(path, encoding="utf-8").read())["positions"][0]["ticker"] == "A.PA"


def test_default_positions_path_points_inside_ibkr_bot_package():
    assert portfolio.POSITIONS_PATH.replace("\\", "/").endswith("ibkr_bot/positions.json")


def test_is_missing_covers_none_and_nan():
    assert portfolio._is_missing(None) is True
    assert portfolio._is_missing(float("nan")) is True
    assert portfolio._is_missing(0.0) is False
