# docs/superpowers/research/gold_bot_ftmo_check/ftmo_simulator.py
# RECHERCHE / EXPLORATOIRE -- PAS DU CODE DE PRODUCTION.
#
# Simule les regles FTMO reelles (verifiees via WebFetch sur ftmo.com/
# en/trading-objectives/ le 2026-10-26, pas de source tierce) sur une
# courbe d'equite deja calculee ailleurs (voir backtest_vwap_ftmo.py).
#
# Regles 2-Step (celles simulees ici, voir ftmo_simulator_README.md pour
# le detail et pourquoi le 1-Step est ecarte) :
# - perte quotidienne max = 5% de l'ISC, recalculee chaque jour a 00:00
#   CE(S)T a partir du SOLDE (balance, pas equity) enregistre a ce
#   moment-la moins 5%*ISC -- le jour 1 utilise l'ISC comme base.
#   L'EQUITE (balance + P&L flottant) doit rester au-dessus de ce
#   plancher a tout instant du jour.
# - perte globale max = 10% de l'ISC, STATIQUE (ISC - 10%*ISC), ne
#   bouge jamais -- contrairement au 1-Step qui est trailing.
# - objectif de profit = 10% (phase Challenge), 5% (phase Verification).
# - minimum 4 jours de trading (un jour de trading = au moins une
#   position ouverte ce jour civil CE(S)T) -- pas simule ici (verifie a
#   part, une contrainte de temps, pas de risque).
# - PAS de regle de consistance / best-day sur le 2-Step (confirme par
#   la page officielle) -- seul le 1-Step en a une.
from __future__ import annotations
from datetime import datetime, timedelta, timezone


CET_OFFSET_HOURS = 1  # CET = UTC+1 (CEST = UTC+2 en ete, non distingue ici : voir limite dans le README)


def _cet_day(ts_utc: datetime) -> "datetime.date":
    return (ts_utc + timedelta(hours=CET_OFFSET_HOURS)).date()


def simulate_2step_phase(
    equity_curve: list[tuple[datetime, float, float]],
    isc: float,
    profit_target_pct: float,
    daily_loss_pct: float = 0.05,
    overall_loss_pct: float = 0.10,
) -> dict:
    """`equity_curve` : liste triee (timestamp_utc, balance, equity) a
    haute frequence (toutes les 15 min dans ce chantier) depuis le debut
    de la phase. `balance` = solde realise (hors P&L flottant),
    `equity` = balance + P&L flottant des positions ouvertes a cet
    instant -- c'est equity qui doit rester au-dessus du plancher de
    perte quotidienne (regle officielle : "Equity must not fall below
    this limit").

    Renvoie : {"issue": "objectif_atteint"|"perte_quotidienne"|"perte_globale"|"incomplet",
    "ts_issue": ..., "jours_de_trading": set(...)}. "incomplet" si la
    courbe se termine sans qu'aucune des trois conditions ne survienne
    (fin de l'historique disponible, pas un vrai verdict)."""
    if not equity_curve:
        return {"issue": "incomplet", "ts_issue": None, "jours_de_trading": set()}

    overall_floor = isc * (1.0 - overall_loss_pct)
    target_equity = isc * (1.0 + profit_target_pct)

    jour_courant = None
    plancher_quotidien = None
    jours_de_trading: set = set()
    balance_precedente = isc

    for ts, balance, equity in equity_curve:
        jour = _cet_day(ts)
        if jour != jour_courant:
            # Nouveau jour CET : le plancher du jour se fige sur le solde
            # EXISTANT AU DEBUT de ce jour (celui issu de la veille), pas
            # sur le solde qui vient de changer DANS ce nouveau jour.
            plancher_quotidien = balance_precedente * (1.0 - daily_loss_pct)
            jour_courant = jour
        if balance != balance_precedente:
            jours_de_trading.add(jour)
        balance_precedente = balance

        if equity <= overall_floor:
            return {"issue": "perte_globale", "ts_issue": ts, "jours_de_trading": jours_de_trading}
        if equity <= plancher_quotidien:
            return {"issue": "perte_quotidienne", "ts_issue": ts, "jours_de_trading": jours_de_trading}
        if equity >= target_equity:
            return {"issue": "objectif_atteint", "ts_issue": ts, "jours_de_trading": jours_de_trading}

    return {"issue": "incomplet", "ts_issue": None, "jours_de_trading": jours_de_trading}


def simulate_2step_full(
    equity_curve: list[tuple[datetime, float, float]], isc: float,
) -> dict:
    """Challenge (10%) puis Verification (5%) si le Challenge reussit --
    la Verification repart d'un compte frais a l'ISC (pas de continuite
    d'equite entre phases, regle FTMO), donc on cherche le POINT DE
    DEPART suivant dans la meme courbe (apres la fin du Challenge) et on
    relance une simulation independante avec un nouveau plancher/objectif
    -- approximation : ce chantier traite les deux phases comme deux
    tirages INDEPENDANTS sur la meme courbe de marche plutot que de
    chainer une seule execution continue (voir limite dans le README :
    en realite le trader continue a trader pendant que le compte de
    Verification est ouvert, ce qui n'est pas modelise ici)."""
    challenge = simulate_2step_phase(equity_curve, isc, profit_target_pct=0.10)
    return {"challenge": challenge}
