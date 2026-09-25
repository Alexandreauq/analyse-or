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


def _contrat(ticker, conid=4901, motif=None, **overrides):
    contrat = {
        "ticker": ticker, "conid": conid if motif is None else None,
        "exchange": "SBF", "currency": "EUR", "motif": motif,
        "detail": "resolu" if motif is None else "test",
    }
    contrat.update(overrides)
    return contrat


def _contrats(tickers):
    """Contrats resolus par defaut pour une liste de tickers — pratique
    pour les tests qui ne portent pas sur le filtre contrat lui-meme."""
    return {t: _contrat(t, conid=4900 + i) for i, t in enumerate(tickers)}


_CASH_ILLIMITE = 1e9


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
    contrats = _contrats(["A.PA", "B.PA", "C.PA"])

    retenus, rejets = portfolio.select_entries(signaux, [], plans, contrats, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA", "C.PA", "A.PA"]
    assert [r["rang"] for r in retenus] == [1, 2, 3]
    assert rejets == []


def test_select_entries_drops_surplus_signals_when_the_cap_is_reached():
    """Sursouscription (spec 3.5) : les signaux qui ne rentrent pas sont
    perdus, pas mis en file d'attente, et journalises avec leur rang."""
    # index cycle sur 3 valeurs (3 par indice, sous MAX_POSITIONS_PER_INDEX=4)
    # pour ne pas declencher le plafond de diversification, hors sujet ici.
    ouvertes = [_bot_position(f"OPEN{i}.PA", index=["CAC40", "DAX", "NASDAQ"][i % 3]) for i in range(9)]
    signaux = [_signal("A.PA", 10.0), _signal("B.PA", 48.8), _signal("C.PA", 26.9)]
    plans = {t: _plan(t) for t in ("A.PA", "B.PA", "C.PA")}
    contrats = _contrats(["A.PA", "B.PA", "C.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, ouvertes, plans, contrats, _CASH_ILLIMITE)

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
        signaux, ouvertes, {"A.PA": _plan("A.PA")}, _contrats(["A.PA"]), _CASH_ILLIMITE)

    assert retenus == []
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 10.0,
                       "raison": "signal_ignore_plafond_atteint"}]


def test_select_entries_zero_share_signal_does_not_consume_a_slot():
    """Spec 3.3 : la place liberee par un signal a 0 action reste
    disponible pour le signal suivant du classement. Ce test echoue si
    le filtre plafond est applique AVANT le filtre 0 action."""
    ouvertes = [_bot_position(f"OPEN{i}.PA", index=["CAC40", "DAX", "NASDAQ"][i % 3]) for i in range(9)]
    signaux = [_signal("CHER.PA", 90.0), _signal("B.PA", 48.8)]
    plans = {
        "CHER.PA": _plan("CHER.PA", quantite=0, cout=0.0,
                         motif="signal_ignore_prix_unitaire_superieur_au_budget"),
        "B.PA": _plan("B.PA"),
    }
    contrats = _contrats(["CHER.PA", "B.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, ouvertes, plans, contrats, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA"]
    assert rejets == [{
        "ticker": "CHER.PA", "rang": 1, "score": 90.0,
        "raison": "signal_ignore_prix_unitaire_superieur_au_budget",
    }]


def test_select_entries_skips_a_ticker_already_held_by_the_bot():
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 10.0)]
    plans = {"A.PA": _plan("A.PA"), "B.PA": _plan("B.PA")}
    contrats = _contrats(["A.PA", "B.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, [_bot_position("A.PA")], plans, contrats, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA"]
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 48.8,
                       "raison": "deja_en_portefeuille"}]


def test_select_entries_rejects_a_signal_without_a_plan():
    signaux = [_signal("A.PA", 48.8)]
    retenus, rejets = portfolio.select_entries(
        signaux, [], {}, _contrats(["A.PA"]), _CASH_ILLIMITE)

    assert retenus == []
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 48.8,
                       "raison": "plan_indisponible"}]


def test_select_entries_propagates_an_invalid_price_reason():
    signaux = [_signal("A.PA", 48.8)]
    plans = {"A.PA": _plan("A.PA", quantite=0, cout=0.0, motif="prix_ou_taux_invalide")}

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, _contrats(["A.PA"]), _CASH_ILLIMITE)

    assert retenus == []
    assert rejets[0]["raison"] == "prix_ou_taux_invalide"


# --- resolution de contrat (spec Global Constraints) ------------------

def test_select_entries_rejects_a_signal_with_no_contract_entry_at_all():
    """Un ticker jamais passe par contracts.resolve_conid (ou absent du
    dict par erreur) doit etre refuse, pas traite comme achetable."""
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 10.0)]
    plans = {t: _plan(t) for t in ("A.PA", "B.PA")}
    contrats = {"B.PA": _contrat("B.PA")}  # A.PA absent du dict

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, contrats, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA"]
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 48.8,
                       "raison": "contrat_non_resolu"}]


def test_select_entries_rejects_a_signal_whose_contract_failed_to_resolve():
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 10.0)]
    plans = {t: _plan(t) for t in ("A.PA", "B.PA")}
    contrats = {
        "A.PA": _contrat("A.PA", motif="contrat_non_resolu"),
        "B.PA": _contrat("B.PA"),
    }

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, contrats, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA"]
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 48.8,
                       "raison": "contrat_non_resolu"}]


def test_select_entries_missing_contract_does_not_consume_a_cap_slot():
    """Comme le filtre 0 action, un contrat non resolu ne doit pas
    consommer de place : le signal suivant du classement doit toujours
    pouvoir la prendre."""
    ouvertes = [_bot_position(f"OPEN{i}.PA", index=["CAC40", "DAX", "NASDAQ"][i % 3]) for i in range(9)]
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 10.0)]
    plans = {t: _plan(t) for t in ("A.PA", "B.PA")}
    contrats = {"B.PA": _contrat("B.PA")}

    retenus, rejets = portfolio.select_entries(
        signaux, ouvertes, plans, contrats, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA"]
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 48.8,
                       "raison": "contrat_non_resolu"}]


def test_select_entries_rejects_signal_when_sector_cap_reached():
    """audit Or/Actions 2026-09-21, point 3 : aucune contrainte de
    diversification n'existait -- MAX_POSITIONS_PER_SECTOR=3 doit
    bloquer un 4e signal du meme secteur, meme mieux note."""
    ouvertes = [
        _bot_position(f"OPEN{i}.PA", sector="Financial Services") for i in range(3)
    ]
    signaux = [_signal("D.PA", 90.0, sector="Financial Services")]
    plans = {"D.PA": _plan("D.PA")}
    contrats = _contrats(["D.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, ouvertes, plans, contrats, _CASH_ILLIMITE)

    assert retenus == []
    assert rejets == [{"ticker": "D.PA", "rang": 1, "score": 90.0,
                       "raison": "plafond_secteur_atteint"}]


def test_select_entries_rejects_signal_when_index_cap_reached():
    """MAX_POSITIONS_PER_INDEX=4 doit bloquer un 5e signal du meme
    indice."""
    ouvertes = [_bot_position(f"OPEN{i}.PA", index="CAC40") for i in range(4)]
    signaux = [_signal("D.PA", 90.0, index="CAC40")]
    plans = {"D.PA": _plan("D.PA")}
    contrats = _contrats(["D.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, ouvertes, plans, contrats, _CASH_ILLIMITE)

    assert retenus == []
    assert rejets == [{"ticker": "D.PA", "rang": 1, "score": 90.0,
                       "raison": "plafond_indice_atteint"}]


def test_select_entries_sector_cap_does_not_block_other_sectors():
    """Le plafond est par secteur, pas global -- un signal d'un autre
    secteur doit toujours passer."""
    ouvertes = [
        _bot_position(f"OPEN{i}.PA", sector="Financial Services") for i in range(3)
    ]
    signaux = [_signal("D.PA", 90.0, sector="Technology")]
    plans = {"D.PA": _plan("D.PA")}
    contrats = _contrats(["D.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, ouvertes, plans, contrats, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["D.PA"]
    assert rejets == []


def test_select_entries_sector_cap_counts_signals_retained_earlier_in_same_batch():
    """Le plafond doit aussi compter les signaux deja retenus PLUS HAUT
    dans le MEME classement, pas seulement les positions deja ouvertes --
    sinon deux signaux du meme secteur pourraient passer le meme jour."""
    signaux = [
        _signal("A.PA", 90.0, sector="Financial Services"),
        _signal("B.PA", 80.0, sector="Financial Services"),
        _signal("C.PA", 70.0, sector="Financial Services"),
        _signal("D.PA", 60.0, sector="Financial Services"),
    ]
    plans = {t: _plan(t) for t in ("A.PA", "B.PA", "C.PA", "D.PA")}
    contrats = _contrats(["A.PA", "B.PA", "C.PA", "D.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, contrats, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["A.PA", "B.PA", "C.PA"]
    assert rejets == [{"ticker": "D.PA", "rang": 4, "score": 60.0,
                       "raison": "plafond_secteur_atteint"}]


def test_select_entries_diversification_rejection_does_not_consume_a_slot():
    """Meme principe que le filtre 0 action/contrat non resolu : un
    signal rejete pour diversification ne doit pas consommer de place ni
    de budget -- le signal suivant du classement doit toujours pouvoir
    la prendre."""
    ouvertes = [
        _bot_position(f"OPEN{i}.PA", sector="Financial Services") for i in range(3)
    ]
    signaux = [
        _signal("CAP.PA", 90.0, sector="Financial Services"),
        _signal("B.PA", 48.8, sector="Technology"),
    ]
    plans = {"CAP.PA": _plan("CAP.PA"), "B.PA": _plan("B.PA")}
    contrats = _contrats(["CAP.PA", "B.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, ouvertes, plans, contrats, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA"]
    assert rejets == [{"ticker": "CAP.PA", "rang": 1, "score": 90.0,
                       "raison": "plafond_secteur_atteint"}]


def test_select_entries_signal_without_sector_is_never_capped():
    """Un signal sans secteur connu (ne devrait pas arriver en pratique)
    ne doit jamais etre bloque par le plafond de diversification --
    echec ouvert sur une donnee manquante, pas un rejet a tort."""
    ouvertes = [
        _bot_position(f"OPEN{i}.PA", sector="Financial Services") for i in range(3)
    ]
    signaux = [_signal("D.PA", 90.0, sector="")]
    plans = {"D.PA": _plan("D.PA")}
    contrats = _contrats(["D.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, ouvertes, plans, contrats, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["D.PA"]
    assert rejets == []


def test_select_entries_missing_contract_does_not_consume_budget():
    """Meme principe cote budget : un contrat non resolu ne doit pas
    engager de budget vis-a-vis du garde-fou de solde."""
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 10.0)]
    plans = {t: _plan(t) for t in ("A.PA", "B.PA")}
    contrats = {"B.PA": _contrat("B.PA")}

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, contrats, base_cash=500.0)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA"]
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 48.8,
                       "raison": "contrat_non_resolu"}]


# --- garde-fou de solde (spec 9.9, revu : cash total en devise de base) -

def test_select_entries_accepts_all_signals_when_base_cash_is_plentiful():
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 26.9), _signal("C.PA", 10.0)]
    plans = {t: _plan(t) for t in ("A.PA", "B.PA", "C.PA")}
    contrats = _contrats(["A.PA", "B.PA", "C.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, contrats, base_cash=3 * portfolio.BUDGET_EUR)

    assert [r["signal"]["ticker"] for r in retenus] == ["A.PA", "B.PA", "C.PA"]
    assert rejets == []


def test_select_entries_accepts_exactly_n_signals_and_rejects_the_next():
    """base_cash finance exactement 2 positions a BUDGET_EUR (500) chacune
    : la 3e, moins bien classee, est rejetee faute de solde."""
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 26.9), _signal("C.PA", 10.0)]
    plans = {t: _plan(t) for t in ("A.PA", "B.PA", "C.PA")}
    contrats = _contrats(["A.PA", "B.PA", "C.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, contrats, base_cash=2 * portfolio.BUDGET_EUR)

    assert [r["signal"]["ticker"] for r in retenus] == ["A.PA", "B.PA"]
    assert rejets == [{"ticker": "C.PA", "rang": 3, "score": 10.0,
                       "raison": "solde_insuffisant"}]


def test_select_entries_rejects_everything_with_zero_base_cash():
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 26.9)]
    plans = {t: _plan(t) for t in ("A.PA", "B.PA")}
    contrats = _contrats(["A.PA", "B.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, contrats, base_cash=0.0)

    assert retenus == []
    assert [r["raison"] for r in rejets] == ["solde_insuffisant", "solde_insuffisant"]


def test_select_entries_uses_a_fixed_budget_per_position_regardless_of_currency():
    """Le modele revu (spec 9.1, IDEAL) engage BUDGET_EUR par position sur
    un seul pool de cash en devise de base, quelle que soit la devise du
    signal — plus d'independance par devise."""
    signaux = [
        _signal("A.PA", 48.8, currency="EUR"),
        _signal("ADBE", 26.9, index="NASDAQ", currency="USD"),
    ]
    plans = {
        "A.PA": _plan("A.PA", devise_compte="EUR", cout=500.0),
        "ADBE": _plan("ADBE", devise_compte="USD", cout=540.0),
    }
    contrats = _contrats(["A.PA", "ADBE"])

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, contrats, base_cash=portfolio.BUDGET_EUR)

    assert [r["signal"]["ticker"] for r in retenus] == ["A.PA"]
    assert rejets == [{"ticker": "ADBE", "rang": 2, "score": 26.9,
                       "raison": "solde_insuffisant"}]


def test_select_entries_accepts_a_cost_exactly_equal_to_the_base_cash():
    signaux = [_signal("A.PA", 48.8)]
    plans = {"A.PA": _plan("A.PA", cout=500.0)}

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, _contrats(["A.PA"]), base_cash=portfolio.BUDGET_EUR)

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


# --- date limite -----------------------------------------------------

def test_deadline_date_adds_six_months():
    assert portfolio.deadline_date("2026-09-09") == "2027-03-09"
    assert portfolio.deadline_date("2026-01-31") == "2026-07-31"


def test_deadline_date_clamps_an_impossible_day_of_month():
    """31 aout + 6 mois = 28 fevrier (relativedelta, comme le
    paper-trading)."""
    assert portfolio.deadline_date("2026-08-31") == "2027-02-28"


def test_delay_and_stop_loss_constants_match_the_paper_trading():
    import indices_score

    assert portfolio.STOP_LOSS_PCT == indices_score.SIGNAL_STOP_LOSS_PCT
    assert portfolio.DELAY_MONTHS == indices_score.SIGNAL_SHADOW_DELAY_MONTHS


# --- regles de sortie ------------------------------------------------

def test_exit_reason_stop_loss_below_minus_twenty_percent():
    position = _bot_position("A.PA", prix_execution_reference=100.0)
    assert portfolio.exit_reason(position, {"current_price": 79.0}, "2026-09-14") == "stop_loss"


def test_exit_reason_stop_loss_is_inclusive_at_exactly_minus_twenty_percent():
    position = _bot_position("A.PA", prix_execution_reference=100.0)
    assert portfolio.exit_reason(position, {"current_price": 80.0}, "2026-09-14") == "stop_loss"


def test_exit_reason_none_just_above_the_stop_loss():
    position = _bot_position("A.PA", prix_execution_reference=100.0)
    assert portfolio.exit_reason(position, {"current_price": 80.01}, "2026-09-14") is None


def test_exit_reason_target_reached_is_inclusive():
    position = _bot_position("A.PA", target_exit_price=130.0)
    assert portfolio.exit_reason(position, {"current_price": 130.0}, "2026-09-14") == "objectif_atteint"


def test_exit_reason_delai_max_on_and_after_the_deadline():
    position = _bot_position("A.PA", date_limite="2026-09-14")
    assert portfolio.exit_reason(position, {"current_price": 110.0}, "2026-09-14") == "delai_max"
    assert portfolio.exit_reason(position, {"current_price": 110.0}, "2026-09-15") == "delai_max"


def test_exit_reason_none_before_the_deadline():
    position = _bot_position("A.PA", date_limite="2026-12-14")
    assert portfolio.exit_reason(position, {"current_price": 110.0}, "2026-09-14") is None


def test_exit_reason_stop_loss_wins_over_target_reached():
    """Cas contrive ou les deux conditions sont vraies : l'ordre de
    priorite strict de la spec 3.6 impose stop_loss."""
    position = _bot_position("A.PA", prix_execution_reference=100.0,
                             target_exit_price=50.0)
    assert portfolio.exit_reason(position, {"current_price": 79.0}, "2026-09-14") == "stop_loss"


def test_exit_reason_target_reached_wins_over_delai_max():
    position = _bot_position("A.PA", prix_execution_reference=100.0,
                             target_exit_price=130.0, date_limite="2026-09-01")
    assert portfolio.exit_reason(position, {"current_price": 131.0}, "2026-09-14") == "objectif_atteint"


def test_exit_reason_uses_the_real_fill_price_not_the_paper_entry_price():
    """Spec 3.6 : le stop-loss est relatif au prix d'execution REEL du
    bot. Ici le prix paper declencherait le stop, pas le prix reel."""
    position = _bot_position("A.PA", prix_execution_reference=90.0,
                             paper_entry_price=100.0, target_exit_price=130.0)
    assert portfolio.exit_reason(position, {"current_price": 79.0}, "2026-09-14") is None

    position_reelle_basse = _bot_position("A.PA", prix_execution_reference=100.0,
                                          paper_entry_price=90.0, target_exit_price=130.0)
    assert portfolio.exit_reason(
        position_reelle_basse, {"current_price": 79.0}, "2026-09-14") == "stop_loss"


def test_exit_reason_on_a_london_position_compares_in_pounds():
    """prix_execution_reference est en LIVRES, comme current_price et
    target_exit_price d'indices.json. Un melange avec les pence de
    prix_execution_cotation donnerait ici un stop_loss absurde."""
    position = _bot_position(
        "III.L", index="FTSE", devise_cotation="GBp", devise_compte="GBP",
        prix_execution_cotation=245.0, prix_execution_reference=2.45,
        target_exit_price=3.60, date_limite="2027-03-14")

    assert portfolio.exit_reason(position, {"current_price": 2.60}, "2026-09-14") is None
    assert portfolio.exit_reason(position, {"current_price": 1.90}, "2026-09-14") == "stop_loss"
    assert portfolio.exit_reason(position, {"current_price": 3.70}, "2026-09-14") == "objectif_atteint"


# --- aucune decision sur donnee absente ------------------------------

def test_exit_reason_none_when_the_ticker_disappeared_from_the_data():
    position = _bot_position("A.PA", prix_execution_reference=100.0)
    assert portfolio.exit_reason(position, None, "2026-09-14") is None


@pytest.mark.parametrize("prix", [None, float("nan")])
def test_exit_reason_none_when_the_current_price_is_missing(prix):
    """Jamais de vente declenchee par une donnee absente (spec 3.6)."""
    position = _bot_position("A.PA", prix_execution_reference=100.0,
                             date_limite="2026-01-01")
    assert portfolio.exit_reason(position, {"current_price": prix}, "2026-09-14") is None


def test_exit_reason_none_when_the_position_has_no_reference_price():
    position = _bot_position("A.PA", prix_execution_reference=None)
    assert portfolio.exit_reason(position, {"current_price": 10.0}, "2026-09-14") is None


# --- positions a cloturer --------------------------------------------

def test_positions_to_close_returns_only_eligible_positions():
    positions = [
        _bot_position("A.PA", prix_execution_reference=100.0),
        _bot_position("B.PA", prix_execution_reference=100.0, target_exit_price=130.0),
        _bot_position("C.PA", prix_execution_reference=100.0, date_limite="2026-09-01"),
    ]
    companies = {
        "A.PA": {"current_price": 79.0},
        "B.PA": {"current_price": 131.0},
        "C.PA": {"current_price": 110.0},
    }

    result = portfolio.positions_to_close(positions, companies, "2026-09-14")

    assert [(r["position"]["ticker"], r["close_reason"], r["current_price"]) for r in result] == [
        ("A.PA", "stop_loss", 79.0),
        ("B.PA", "objectif_atteint", 131.0),
        ("C.PA", "delai_max", 110.0),
    ]


def test_positions_to_close_leaves_untouched_what_has_no_data():
    positions = [
        _bot_position("A.PA", prix_execution_reference=100.0, date_limite="2026-01-01"),
        _bot_position("B.PA", prix_execution_reference=100.0, date_limite="2026-01-01"),
    ]
    companies = {"A.PA": {"current_price": None}}

    assert portfolio.positions_to_close(positions, companies, "2026-09-14") == []


def test_positions_to_close_does_not_mutate_the_positions():
    positions = [_bot_position("A.PA", prix_execution_reference=100.0)]
    portfolio.positions_to_close(positions, {"A.PA": {"current_price": 79.0}}, "2026-09-14")

    assert "close_reason" not in positions[0]
    assert positions[0]["quantite"] == 5


# --- non-regression contre le paper-trading (spec 7) -----------------

_SCENARIOS_SORTIE = [
    # (prix_entree, objectif, prix_courant, date_limite, aujourd_hui, attendu)
    (100.0, 130.0, 79.0, "2027-03-09", "2026-09-14", "stop_loss"),
    (100.0, 130.0, 80.0, "2027-03-09", "2026-09-14", "stop_loss"),
    (100.0, 130.0, 80.01, "2027-03-09", "2026-09-14", None),
    (100.0, 130.0, 131.0, "2027-03-09", "2026-09-14", "objectif_atteint"),
    (100.0, 130.0, 130.0, "2027-03-09", "2026-09-14", "objectif_atteint"),
    (100.0, 130.0, 129.99, "2027-03-09", "2026-09-14", None),
    (100.0, 130.0, 110.0, "2026-09-14", "2026-09-14", "delai_max"),
    (100.0, 130.0, 110.0, "2026-09-13", "2026-09-14", "delai_max"),
    (100.0, 130.0, 110.0, "2026-09-15", "2026-09-14", None),
    (100.0, 50.0, 79.0, "2027-03-09", "2026-09-14", "stop_loss"),
    (100.0, 130.0, 131.0, "2026-09-13", "2026-09-14", "objectif_atteint"),
    (100.0, 130.0, 110.0, "2027-03-09", "2026-09-14", None),
    # stop_loss et delai_max se declenchent tous les deux (objectif non
    # atteint) : stop_loss gagne, premier de l'ordre de priorite.
    (100.0, 200.0, 79.0, "2026-09-01", "2026-09-14", "stop_loss"),
    # les trois conditions se declenchent en meme temps : stop_loss gagne
    # toujours, quel que soit le nombre de conditions vraies simultanement.
    (100.0, 50.0, 79.0, "2026-01-01", "2026-09-14", "stop_loss"),
]


@pytest.mark.parametrize(
    "entree,objectif,courant,limite,aujourdhui,attendu", _SCENARIOS_SORTIE)
def test_exit_reason_matches_the_paper_trading_logic(
        entree, objectif, courant, limite, aujourdhui, attendu):
    """Garantie mecanique que la logique de sortie du bot reel et celle
    du paper-trading ne divergent pas (spec 7). A prix identiques, les
    deux doivent produire exactement la meme decision."""
    import indices_score

    bot_position = _bot_position(
        "BN.PA", prix_execution_reference=entree, target_exit_price=objectif,
        date_limite=limite)
    decision_bot = portfolio.exit_reason(
        bot_position, {"current_price": courant}, aujourdhui)

    paper_position = {
        "id": "BN.PA-2026-06-08", "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "status": "open", "entry_date": "2026-06-08", "entry_price": entree,
        "target_exit_price": objectif, "index_price_at_entry": 7500.0,
        "close_date": None, "close_price": None, "close_reason": None, "return_pct": None,
        "index_price_at_close": None, "index_return_pct": None,
        "shadow_close_date": limite, "shadow_resolved": False,
        "shadow_price": None, "shadow_return_pct": None,
    }
    resultat_paper = indices_score._close_eligible_positions(
        [paper_position], {"BN.PA": {"current_price": courant}},
        {"CAC40": 7600.0}, today=aujourdhui)
    decision_paper = resultat_paper[0]["close_reason"]

    assert decision_bot == attendu
    assert decision_bot == decision_paper


# --- reconciliation (spec 5.4) ---------------------------------------

def test_reconcile_keeps_positions_still_present_at_ibkr():
    locales = [_bot_position("A.PA", conid=4901, quantite=5)]
    chez_ibkr = [{"conid": 4901, "position": 5.0, "currency": "EUR", "contractDesc": "A"}]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert [p["ticker"] for p in result["actives"]] == ["A.PA"]
    assert result["cloturees_hors_bot"] == []
    assert result["anomalies_quantite"] == []
    assert result["ignorees"] == []


def test_reconcile_matches_conids_across_int_and_string_types():
    """L'API IBKR est documentee comme incoherente sur le type du conid
    (secdef/search le renvoie parfois en chaine, /portfolio/.../positions
    en nombre). Une position locale avec un conid int doit toujours etre
    appariee a une position IBKR qui rapporte le meme conid en chaine —
    sinon elle disparaitrait a tort en cloturees_hors_bot."""
    locales = [_bot_position("A.PA", conid=4901, quantite=5)]
    chez_ibkr = [{"conid": "4901", "position": 5.0, "currency": "EUR"}]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert [p["ticker"] for p in result["actives"]] == ["A.PA"]
    assert result["cloturees_hors_bot"] == []


def test_reconcile_marks_a_position_sold_outside_the_bot():
    """Vendue a la main par l'utilisateur : retiree du decompte des 10,
    jamais rouverte par le bot."""
    locales = [_bot_position("A.PA", conid=4901), _bot_position("B.PA", conid=4902)]
    chez_ibkr = [{"conid": 4902, "position": 5.0, "currency": "EUR"}]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert [p["ticker"] for p in result["actives"]] == ["B.PA"]
    assert [p["ticker"] for p in result["cloturees_hors_bot"]] == ["A.PA"]


def test_reconcile_treats_a_zero_quantity_ibkr_position_as_closed():
    locales = [_bot_position("A.PA", conid=4901)]
    chez_ibkr = [{"conid": 4901, "position": 0.0, "currency": "EUR"}]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert result["actives"] == []
    assert [p["ticker"] for p in result["cloturees_hors_bot"]] == ["A.PA"]


def test_reconcile_treats_a_negative_ibkr_quantity_as_closed():
    """Le bot ne doit jamais detenir de position negative (pas de short) :
    une quantite negative signale un probleme et est traitee comme une
    cloture hors bot, jamais comme active — sinon elle compterait dans le
    plafond de 10 et pourrait devenir candidate a une vente qui
    augmenterait le short au lieu de le clore."""
    locales = [_bot_position("A.PA", conid=4901)]
    chez_ibkr = [{"conid": 4901, "position": -5.0, "currency": "EUR"}]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert result["actives"] == []
    assert [p["ticker"] for p in result["cloturees_hors_bot"]] == ["A.PA"]
    assert result["anomalies_quantite"] == []


def test_reconcile_treats_an_explicit_null_ibkr_quantity_as_closed():
    """`"position": null` (par opposition a une cle absente) ne doit pas
    faire planter int(None) et interrompre toute la reconciliation du
    batch — une seule ligne corrompue ne doit jamais bloquer les autres
    (spec 5.4)."""
    locales = [_bot_position("A.PA", conid=4901), _bot_position("B.PA", conid=4902)]
    chez_ibkr = [
        {"conid": 4901, "position": None, "currency": "EUR"},
        {"conid": 4902, "position": 5.0, "currency": "EUR"},
    ]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert [p["ticker"] for p in result["actives"]] == ["B.PA"]
    assert [p["ticker"] for p in result["cloturees_hors_bot"]] == ["A.PA"]


def test_reconcile_never_touches_a_position_the_bot_did_not_open():
    """Position de l'utilisateur : ignoree, jamais vendue (spec 5.4,
    9.5). Elle ne compte pas non plus dans le plafond de 10."""
    locales = [_bot_position("A.PA", conid=4901)]
    chez_ibkr = [
        {"conid": 4901, "position": 5.0, "currency": "EUR"},
        {"conid": 77777, "position": 300.0, "currency": "USD", "contractDesc": "TSLA"},
    ]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert [p["ticker"] for p in result["actives"]] == ["A.PA"]
    assert result["ignorees"] == [
        {"conid": 77777, "position": 300.0, "currency": "USD", "contractDesc": "TSLA"}]


def test_reconcile_lets_the_ibkr_quantity_win_and_logs_the_anomaly():
    locales = [_bot_position("A.PA", conid=4901, quantite=5)]
    chez_ibkr = [{"conid": 4901, "position": 3.0, "currency": "EUR"}]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert result["actives"][0]["quantite"] == 3
    assert result["anomalies_quantite"] == [
        {"ticker": "A.PA", "conid": 4901, "quantite_locale": 5, "quantite_ibkr": 3}]


def test_reconcile_does_not_mutate_the_local_positions():
    locales = [_bot_position("A.PA", conid=4901, quantite=5)]
    portfolio.reconcile(locales, [{"conid": 4901, "position": 3.0, "currency": "EUR"}])

    assert locales[0]["quantite"] == 5


def test_reconcile_with_an_empty_ibkr_account_closes_everything_out_of_bot():
    locales = [_bot_position("A.PA", conid=4901), _bot_position("B.PA", conid=4902)]

    result = portfolio.reconcile(locales, [])

    assert result["actives"] == []
    assert len(result["cloturees_hors_bot"]) == 2
    assert result["ignorees"] == []


def test_reconcile_result_feeds_free_slots_correctly():
    """La reconciliation precede toute decision : le plafond se calcule
    sur `actives`, pas sur le journal local brut."""
    locales = [_bot_position(f"T{i}.PA", conid=5000 + i) for i in range(10)]
    chez_ibkr = [{"conid": 5000 + i, "position": 5.0, "currency": "EUR"} for i in range(8)]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert len(result["actives"]) == 8
    assert portfolio.free_slots(result["actives"]) == 2
