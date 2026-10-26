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
from backtest_vwap_ftmo import (  # noqa: E402
    CONTRACT_SIZE, VOLUME_STEP, MIN_VOLUME, MAX_VOLUME,
    REFERENCE_RISK_PCT, REFERENCE_ISC,
)
import gold_bot.risk as risk  # noqa: E402 -- la VRAIE fonction d'arrondi de lot de la prod

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


def simulate_challenge_from(
    trades: list[dict], start_idx: int, risk_pct: float, isc: float,
    profit_target_pct: float = PROFIT_TARGET_PCT,
) -> dict:
    """Simule une phase (Challenge: objectif 10% par defaut, ou
    Verification: appeler avec profit_target_pct=0.05) en partant du
    trade d'indice `start_idx` dans `trades` (deja trie
    chronologiquement), avec un compte frais a `isc`. Renvoie {"issue":
    "objectif_atteint"|"perte_quotidienne"|"perte_globale"|"incomplet",
    "n_trades": nombre de trades consommes}."""
    overall_floor = isc * (1.0 - OVERALL_LOSS_PCT)
    target = isc * (1.0 + profit_target_pct)

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
    la repartition des issues en %. APPROXIMATION CONTINUE -- ignore le
    pas de lot reel (voir simulate_challenge_from_quantized ci-dessous
    pour la version realiste, qui reutilise risk.round_to_volume_step)."""
    results = {}
    n = len(trades)
    for risk_pct in risk_pcts:
        counts = {"objectif_atteint": 0, "perte_quotidienne": 0, "perte_globale": 0, "incomplet": 0}
        for start_idx in range(n):
            outcome = simulate_challenge_from(trades, start_idx, risk_pct, isc)
            counts[outcome["issue"]] += 1
        results[risk_pct] = {k: round(100 * v / n, 1) for k, v in counts.items()}
    return results


def _effective_distances(trades: list[dict]) -> list[float]:
    """Pour chaque trade de reference (genere a REFERENCE_RISK_PCT=1%,
    REFERENCE_ISC=100k$, choisi precisement pour que la distorsion du
    pas de lot y soit negligeable -- voir backtest_vwap_ftmo.py), retrouve
    la distance entree->stop implicite a partir du volume REELEMENT
    arrondi qui a servi a generer ce trade : volume = risked_dollars /
    (distance * CONTRACT_SIZE), donc distance = risked_dollars /
    (volume * CONTRACT_SIZE). Cette distance ne depend pas du risk_pct/
    ISC vises par le scenario teste -- elle vient du signal (VWAP/EMA),
    pas du compte -- donc reutilisable pour requantifier a n'importe
    quel (risk_pct, ISC) sans refaire tourner le moteur complet."""
    risked_at_reference = REFERENCE_RISK_PCT * REFERENCE_ISC
    return [risked_at_reference / (t["volume"] * CONTRACT_SIZE) for t in trades]


def simulate_challenge_from_quantized(
    trades: list[dict], distances: list[float], start_idx: int, risk_pct: float, isc: float,
    profit_target_pct: float = PROFIT_TARGET_PCT,
) -> dict:
    """Comme simulate_challenge_from, mais calcule a CHAQUE trade la
    taille REELLEMENT tradable via risk.round_to_volume_step (la
    fonction de production, pas une reimplementation) : raw_size =
    (risk_pct * balance) / (distance * CONTRACT_SIZE), puis arrondie au
    pas de lot (0.01) et au minimum (0.01) de XAUUSD. Si le resultat
    tombe sous le minimum, le trade est SAUTE (round_to_volume_step
    renvoie None en prod -- le bot ne prend jamais plus de risque que
    vise, il s'abstient plutot, voir gold_bot/risk.py) : le solde ne
    change pas, mais le suivi du plancher quotidien continue normalement
    (d'autres trades le meme jour peuvent toujours le faire casser).
    `profit_target_pct` : 0.10 pour la phase Challenge (defaut), 0.05
    pour la phase Verification."""
    overall_floor = isc * (1.0 - OVERALL_LOSS_PCT)
    target = isc * (1.0 + profit_target_pct)

    balance = isc
    jour_courant = None
    plancher_quotidien = None
    balance_debut_jour = isc
    n_sautes = 0

    for k in range(start_idx, len(trades)):
        t = trades[k]
        jour = _cet_day(t["entry_time"])
        if jour != jour_courant:
            plancher_quotidien = balance_debut_jour * (1.0 - DAILY_LOSS_PCT)
            jour_courant = jour
            balance_debut_jour = balance

        distance = distances[k]
        raw_size = (risk_pct * balance) / (distance * CONTRACT_SIZE)
        size = risk.round_to_volume_step(raw_size, VOLUME_STEP, MIN_VOLUME, MAX_VOLUME)
        if size is None:
            n_sautes += 1
            continue

        risked_dollars = size * distance * CONTRACT_SIZE
        worst_equity = balance + risked_dollars * t["r_pire"]
        if worst_equity <= overall_floor:
            return {"issue": "perte_globale", "n_trades": k - start_idx + 1, "n_sautes": n_sautes}
        if worst_equity <= plancher_quotidien:
            return {"issue": "perte_quotidienne", "n_trades": k - start_idx + 1, "n_sautes": n_sautes}

        balance = balance + risked_dollars * t["r_final"]
        if balance <= overall_floor:
            return {"issue": "perte_globale", "n_trades": k - start_idx + 1, "n_sautes": n_sautes}
        if balance <= plancher_quotidien:
            return {"issue": "perte_quotidienne", "n_trades": k - start_idx + 1, "n_sautes": n_sautes}
        if balance >= target:
            return {"issue": "objectif_atteint", "n_trades": k - start_idx + 1, "n_sautes": n_sautes}

    return {"issue": "incomplet", "n_trades": len(trades) - start_idx, "n_sautes": n_sautes}


def pass_rate_by_risk_quantized(trades: list[dict], isc: float, risk_pcts: list[float]) -> dict:
    """Version quantifiee reelle de pass_rate_by_risk -- meme protocole
    (tous les points de depart testes), mais sizing reellement arrondi
    au pas de lot a chaque trade via simulate_challenge_from_quantized.
    Ajoute le % moyen de trades sautes (taille sous le minimum) comme
    diagnostic."""
    results = {}
    n = len(trades)
    distances = _effective_distances(trades)
    for risk_pct in risk_pcts:
        counts = {"objectif_atteint": 0, "perte_quotidienne": 0, "perte_globale": 0, "incomplet": 0}
        total_sautes = 0
        total_trades_vus = 0
        for start_idx in range(n):
            outcome = simulate_challenge_from_quantized(trades, distances, start_idx, risk_pct, isc)
            counts[outcome["issue"]] += 1
            total_sautes += outcome["n_sautes"]
            total_trades_vus += outcome["n_trades"]
        results[risk_pct] = {k: round(100 * v / n, 1) for k, v in counts.items()}
        results[risk_pct]["pct_trades_sautes"] = (
            round(100 * total_sautes / total_trades_vus, 1) if total_trades_vus else 0.0
        )
    return results


VERIFICATION_TARGET_PCT = 0.05


def full_2step_pass_rate(trades: list[dict], isc: float, risk_pct: float) -> float:
    """Chaine Challenge (10%) -> Verification (5%, compte frais a `isc`,
    repart juste APRES le dernier trade consomme par le Challenge --
    approximation documentee dans ftmo_simulator.simulate_2step_full :
    deux tirages independants sur la meme courbe de marche, pas une
    execution continue). % de start_idx (sur tous les trades comme
    point de depart candidat) qui reussissent les DEUX phases."""
    n = len(trades)
    reussites = 0
    for start_idx in range(n):
        challenge = simulate_challenge_from(trades, start_idx, risk_pct, isc)
        if challenge["issue"] != "objectif_atteint":
            continue
        verif_start = start_idx + challenge["n_trades"]
        if verif_start >= n:
            continue  # plus de trades disponibles pour la Verification -- pas une reussite constatee
        verification = simulate_challenge_from(
            trades, verif_start, risk_pct, isc, profit_target_pct=VERIFICATION_TARGET_PCT)
        if verification["issue"] == "objectif_atteint":
            reussites += 1
    return round(100 * reussites / n, 1)


def full_2step_pass_rate_quantized(trades: list[dict], isc: float, risk_pct: float) -> float:
    """Version quantifiee reelle de full_2step_pass_rate."""
    n = len(trades)
    distances = _effective_distances(trades)
    reussites = 0
    for start_idx in range(n):
        challenge = simulate_challenge_from_quantized(trades, distances, start_idx, risk_pct, isc)
        if challenge["issue"] != "objectif_atteint":
            continue
        verif_start = start_idx + challenge["n_trades"]
        if verif_start >= n:
            continue
        verification = simulate_challenge_from_quantized(
            trades, distances, verif_start, risk_pct, isc, profit_target_pct=VERIFICATION_TARGET_PCT)
        if verification["issue"] == "objectif_atteint":
            reussites += 1
    return round(100 * reussites / n, 1)


if __name__ == "__main__":
    trades_path = os.path.join(os.path.dirname(__file__), "data", "trades_reference.json")
    trades = _load_trades(trades_path)
    print(f"{len(trades)} trades charges, {trades[0]['entry_time']} -> {trades[-1]['entry_time']}")

    risk_pcts = [0.005, 0.01, 0.02, 0.05, 0.10]

    print("\n########## APPROXIMATION CONTINUE (ancienne, ignore le pas de lot) ##########")
    for isc in (10_000.0, 100_000.0):
        print(f"\n=== ISC = {isc:,.0f}$ (Challenge 2-Step, objectif 10%) ===")
        results = pass_rate_by_risk(trades, isc, risk_pcts)
        for risk_pct, dist in results.items():
            print(f"risk_pct={risk_pct:.1%} : {dist}")

    print("\n########## QUANTIFIEE REELLE (nouvelle, risk.round_to_volume_step) ##########")
    for isc in (10_000.0, 100_000.0):
        print(f"\n=== ISC = {isc:,.0f}$ (Challenge 2-Step, objectif 10%) ===")
        results = pass_rate_by_risk_quantized(trades, isc, risk_pcts)
        for risk_pct, dist in results.items():
            print(f"risk_pct={risk_pct:.1%} : {dist}")

    print("\n########## CHAINE CHALLENGE -> VERIFICATION (2-Step complet) ##########")
    for isc in (10_000.0, 100_000.0):
        for risk_pct in (0.01, 0.02):
            continu = full_2step_pass_rate(trades, isc, risk_pct)
            quantifie = full_2step_pass_rate_quantized(trades, isc, risk_pct)
            print(f"ISC={isc:,.0f}$ risk_pct={risk_pct:.1%} : continu={continu}% | quantifie_reel={quantifie}%")
