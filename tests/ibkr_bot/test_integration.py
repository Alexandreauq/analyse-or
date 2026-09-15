# tests/ibkr_bot/test_integration.py
# Test de bout en bout : chaine les VRAIES fonctions de chaque module
# (signals -> sizing -> contracts -> portfolio.select_entries ->
# portfolio.reconcile), a l'exception des deux points qui touchent
# reellement IBKR : `search_fn`/`info_fn` (injectes dans contracts.py) et
# les soldes de cash (hand-supplies, comme le ferait Plan B en lisant
# gateway.base_currency_cash()). Objectif (revue finale, Finding #4) :
# chaque test des autres fichiers reconstruit a la main la forme de
# sortie d'un autre module ; ce test-ci casse si un futur renommage de
# champ dans n'importe quel module change silencieusement le contrat
# entre eux.
import ibkr_bot.contracts as contracts
import ibkr_bot.portfolio as portfolio
import ibkr_bot.signals as signals
import ibkr_bot.sizing as sizing

TODAY = "2026-09-14"

# --- fixtures "docs/indices.json" / "docs/signal_tracking.json" -------

_INDICES = {
    "updated": TODAY,
    "index_currency": {
        "CAC40": "EUR", "DAX": "EUR", "NASDAQ": "USD", "DOW": "USD",
        "FTSE": "GBP", "SMI": "CHF", "IBEX35": "EUR", "FTSEMIB": "EUR",
    },
    "companies": [
        {"ticker": "MC.PA", "index": "CAC40", "score": 55.2, "current_price": 90.0},
        {"ticker": "ADBE", "index": "NASDAQ", "score": 40.0, "current_price": 520.0},
    ],
}

_PAPER_POSITIONS = [
    {"id": "MC.PA-2026-09-14", "ticker": "MC.PA", "name": "LVMH", "index": "CAC40",
     "status": "open", "entry_date": TODAY, "entry_price": 88.0, "target_exit_price": 120.0},
    {"id": "ADBE-2026-09-14", "ticker": "ADBE", "name": "Adobe", "index": "NASDAQ",
     "status": "open", "entry_date": TODAY, "entry_price": 510.0, "target_exit_price": 650.0},
]

# Taux EUR -> devise du signal, tel que renverrait gateway.exchange_rate()
# (seule les valeurs sont hand-suppliees ici, pas l'appel reseau).
_FX_RATES = {"EUR": 1.0, "USD": 1.08}

# --- doubles de gateway.search_contract / gateway.contract_info -------
# Seuls points mockes du test (avec base_cash plus bas) : les deux
# callables injectes dans contracts.resolve_conid, documentes comme
# provenant de gateway.py et lies a une base_url par l'appelant.

_SEARCH_RESPONSES = {
    "MC": [{
        "conid": "17275", "symbol": "MC", "description": "SBF",
        "sections": [{"secType": "STK"}],
    }],
    "ADBE": [{
        "conid": "202070", "symbol": "ADBE", "description": "NASDAQ",
        "sections": [{"secType": "STK"}],
    }],
}

_INFO_RESPONSES = {
    "17275": {"conid": 17275, "currency": "EUR", "listingExchange": "SBF"},
    "202070": {"conid": 202070, "currency": "USD", "listingExchange": "NASDAQ"},
}


def _search_fn(symbol):
    return _SEARCH_RESPONSES.get(symbol, [])


def _info_fn(conid):
    return _INFO_RESPONSES.get(str(conid), {})


def test_full_chain_from_signals_to_selection_and_reconciliation():
    # 1. signals.py (VRAI appel) : nouveaux signaux du jour.
    signaux, rejets_signaux = signals.collect_new_signals(
        _INDICES, _PAPER_POSITIONS, today=TODAY)

    assert rejets_signaux == []
    assert {s["ticker"] for s in signaux} == {"MC.PA", "ADBE"}

    # 2. sizing.py (VRAI appel) : un plan par signal.
    plans = {
        s["ticker"]: sizing.compute_quantity(
            s["ticker"], s["currency"], s["current_price"], _FX_RATES[s["currency"]])
        for s in signaux
    }
    assert plans["MC.PA"]["quantite"] == 5  # floor(500 EUR / 90 EUR)
    assert plans["ADBE"]["quantite"] == 1   # floor(500 EUR * 1.08 / 520 USD)

    # 3. contracts.py (VRAI appel, search_fn/info_fn hand-supplies).
    cache = {}
    contrats = {
        s["ticker"]: contracts.resolve_conid(
            s["ticker"], _search_fn, _info_fn, cache, today=TODAY)
        for s in signaux
    }
    assert contrats["MC.PA"]["conid"] == 17275
    assert contrats["MC.PA"]["motif"] is None
    assert contrats["ADBE"]["conid"] == 202070
    assert contrats["ADBE"]["motif"] is None

    # 4. portfolio.reconcile (VRAI appel) : le bot ne detient encore rien
    # aujourd'hui, ni localement ni chez IBKR.
    reconciliation = portfolio.reconcile([], [])
    assert reconciliation["actives"] == []

    # 5. portfolio.select_entries (VRAI appel), sous contrainte de solde :
    # base_cash ne finance qu'UNE position a BUDGET_EUR (500) — le signal
    # le mieux classe (MC.PA, score 55.2) doit l'emporter sur ADBE
    # (score 40.0).
    retenus, rejets_selection = portfolio.select_entries(
        signaux, reconciliation["actives"], plans, contrats,
        base_cash=portfolio.BUDGET_EUR)

    assert len(retenus) == 1
    retenu = retenus[0]
    assert retenu["signal"]["ticker"] == "MC.PA"
    assert retenu["plan"]["quantite"] == 5
    assert retenu["rang"] == 1

    assert len(rejets_selection) == 1
    assert rejets_selection[0]["ticker"] == "ADBE"
    assert rejets_selection[0]["raison"] == "solde_insuffisant"


def test_full_chain_rejects_a_signal_whose_contract_never_resolves():
    """Meme chaine, mais le contrat IBKR d'ADBE ne se resout pas (aucun
    candidat renvoye) : ADBE doit etre rejete `contrat_non_resolu` et ne
    doit ni consommer de place ni de budget, et MC.PA doit tout de meme
    passer."""
    signaux, _ = signals.collect_new_signals(_INDICES, _PAPER_POSITIONS, today=TODAY)
    plans = {
        s["ticker"]: sizing.compute_quantity(
            s["ticker"], s["currency"], s["current_price"], _FX_RATES[s["currency"]])
        for s in signaux
    }

    def _search_fn_sans_adbe(symbol):
        return [] if symbol == "ADBE" else _search_fn(symbol)

    cache = {}
    contrats = {
        s["ticker"]: contracts.resolve_conid(
            s["ticker"], _search_fn_sans_adbe, _info_fn, cache, today=TODAY)
        for s in signaux
    }
    assert contrats["ADBE"]["motif"] == "contrat_non_resolu"

    reconciliation = portfolio.reconcile([], [])
    retenus, rejets = portfolio.select_entries(
        signaux, reconciliation["actives"], plans, contrats,
        base_cash=portfolio.BUDGET_EUR)

    assert [r["signal"]["ticker"] for r in retenus] == ["MC.PA"]
    assert [(r["ticker"], r["raison"]) for r in rejets] == [("ADBE", "contrat_non_resolu")]
