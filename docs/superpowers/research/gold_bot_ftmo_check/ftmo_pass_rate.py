# docs/superpowers/research/gold_bot_ftmo_check/ftmo_pass_rate.py
# RECHERCHE / EXPLORATOIRE -- PAS DU CODE DE PRODUCTION.
#
# Synthetise rapidement, pour n'importe quelle combinaison (risk_pct,
# ISC, date de depart), la courbe d'equite FTMO a partir de la liste de
# trades de reference (backtest_vwap_ftmo.py, risk_pct=1% nominal) --
# sans refaire tourner le moteur : le sizing par le risque rend le %
# de variation de balance par trade INVARIANT au niveau de balance
# (risked_dollars = risk_pct * balance, donc PnL% = risk_pct * R,
# quel que soit risk_pct ou le point de depart) -- modulo la
# quantification au pas de lot/plafond de levier, ecartee ici (voir
# ftmo_simulator_README.md pour la verification empirique de cette
# hypothese sur un sous-ensemble).
#
# Priorite de verification a chaque trade, identique a
# ftmo_simulator.simulate_2step_phase : perte globale > perte
# quotidienne > objectif atteint (verifie seulement a la CLOTURE de
# chaque trade -- pas sur un pic intra-trade non capture par r_pire,
# qui ne suit que le pire, jamais le meilleur -- limite documentee).
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from ftmo_simulator import _cet_day  # noqa: E402

DAILY_LOSS_PCT = 0.05
OVERALL_LOSS_PCT = 0.10
PROFIT_TARGET_PCT = 0.10  # phase Challenge


def _load_trades(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    trades = [
        {**t, "entry_time": datetime.fromisoformat(t["entry_time"]),
         "exit_time": datetime.fromisoformat(t["exit_time"])}
        for t in raw
    ]
    trades.sort(key=lambda t: t["entry_time"])
    return trades


def simulate_challenge_from(trades: list[dict], start_idx: int, risk_pct: float, isc: float) -> dict:
    """Simule la phase Challenge (objectif 10%) en partant du trade
    d'indice `start_idx` dans `trades` (deja trie chronologiquement),
    avec un compte frais a `isc`. Renvoie {"issue":
    "objectif_atteint"|"perte_quotidienne"|"perte_globale"|"incomplet",
    "n_trades": nombre de trades consommes}."""
    overall_floor = isc * (1.0 - OVERALL_LOSS_PCT)
    target = isc * (1.0 + PROFIT_TARGET_PCT)

    balance = isc
    jour_courant = None
    plancher_quotidien = None
    balance_debut_jour = isc

    for k in range(start_idx, len(trades)):
        t = trades[k]
        jour = _cet_day(t["entry_time"])
        if jour != jour_courant:
            plancher_quotidien = balance_debut_jour * (1.0 - DAILY_LOSS_PCT)
            jour_courant = jour
            balance_debut_jour = balance

        risked = balance * risk_pct
        worst_equity = balance + risked * t["r_pire"]
        if worst_equity <= overall_floor:
            return {"issue": "perte_globale", "n_trades": k - start_idx + 1}
        if worst_equity <= plancher_quotidien:
            return {"issue": "perte_quotidienne", "n_trades": k - start_idx + 1}

        balance = balance + risked * t["r_final"]
        if balance <= overall_floor:
            return {"issue": "perte_globale", "n_trades": k - start_idx + 1}
        if balance <= plancher_quotidien:
            return {"issue": "perte_quotidienne", "n_trades": k - start_idx + 1}
        if balance >= target:
            return {"issue": "objectif_atteint", "n_trades": k - start_idx + 1}

    return {"issue": "incomplet", "n_trades": len(trades) - start_idx}


def pass_rate_by_risk(trades: list[dict], isc: float, risk_pcts: list[float]) -> dict:
    """Pour chaque risk_pct, teste TOUS les points de depart possibles
    (chaque trade de la liste comme date de depart candidate) et renvoie
    la repartition des issues en %."""
    results = {}
    n = len(trades)
    for risk_pct in risk_pcts:
        counts = {"objectif_atteint": 0, "perte_quotidienne": 0, "perte_globale": 0, "incomplet": 0}
        for start_idx in range(n):
            outcome = simulate_challenge_from(trades, start_idx, risk_pct, isc)
            counts[outcome["issue"]] += 1
        results[risk_pct] = {k: round(100 * v / n, 1) for k, v in counts.items()}
    return results


if __name__ == "__main__":
    trades_path = os.path.join(os.path.dirname(__file__), "data", "trades_reference.json")
    trades = _load_trades(trades_path)
    print(f"{len(trades)} trades charges, {trades[0]['entry_time']} -> {trades[-1]['entry_time']}")

    for isc in (10_000.0, 100_000.0):
        print(f"\n=== ISC = {isc:,.0f}$ (Challenge 2-Step, objectif 10%) ===")
        results = pass_rate_by_risk(trades, isc, [0.005, 0.01, 0.02, 0.05, 0.10])
        for risk_pct, dist in results.items():
            print(f"risk_pct={risk_pct:.1%} : {dist}")
