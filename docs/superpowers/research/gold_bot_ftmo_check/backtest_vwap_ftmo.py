# docs/superpowers/research/gold_bot_ftmo_check/backtest_vwap_ftmo.py
# RECHERCHE / EXPLORATOIRE -- PAS DU CODE DE PRODUCTION.
#
# Reconstruit la liste reelle de trades du moteur gold_bot.vwap_reversion
# (XAUUSD, 8.5 ans de bougies 5min MetaApi, cf. data/xauusd_5min_raw.json)
# en reutilisant gold_bot.bot.decide_and_act_vwap SANS REIMPLEMENTATION --
# une seule passe de reference (risk_pct=1%, ISC=100000$, choisi petit
# pour limiter la distorsion d'arrondi au pas de lot/plafond de levier),
# a la MEME cadence que la production (decision toutes les 15min, sur
# une fenetre glissante de 1000 bougies 5min = CANDLES_FETCH_LIMIT de
# gold_bot/loop.py -- l'EMA200/EMA50 est donc "re-seedee" a chaque appel
# sur cette seule fenetre, exactement comme en production, jamais une
# EMA continue sur 8.5 ans).
#
# Execution intra-jour des stops/take-profits : scannee bougie par
# bougie 5min (le broker reel execute un stop/TP des qu'il est touche,
# independamment du cycle de decision du bot) -- hors du cycle 15min.
#
# Chaque trade est converti en multiple-R (PnL realise / capital
# risque = risk_pct * balance au moment de l'entree) + pire excursion
# intra-trade en multiple-R, pour pouvoir re-synthetiser rapidement
# n'importe quelle combinaison (risk_pct, ISC, date de depart) sans
# refaire tourner le moteur -- le sizing par le risque rend le %
# retourne par trade invariant au niveau de balance (modulo
# quantification au pas de lot, ecartee par le choix de risk_pct=1% en
# reference, voir ftmo_simulator_README.md pour la verification).
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta

import gold_bot.bot as bot
import gold_bot.vwap_reversion as vwap_reversion
import gold_bot.risk as risk
import shared_risk

CANDLES_FETCH_LIMIT = 1000  # ibkr_bot.gold_bot.loop.CANDLES_FETCH_LIMIT -- meme fenetre que la prod
CONTRACT_SIZE = 100.0
VOLUME_STEP = 0.01
MIN_VOLUME = 0.01
MAX_VOLUME = 100.0
REFERENCE_RISK_PCT = 0.01
REFERENCE_ISC = 100_000.0
CIRCUIT_BREAKER_THRESHOLD = 0.10  # meme defaut que shared_risk.CircuitBreaker (profil deploye en reel)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace(" ", "T")).replace(tzinfo=timezone.utc)


def _pnl_dollars(direction: str, entry: float, exit_: float, volume: float) -> float:
    sign = 1.0 if direction == "achat" else -1.0
    return sign * (exit_ - entry) * volume * CONTRACT_SIZE


def _to_metaapi_position(leg_id: int, leg: dict) -> dict:
    return {
        "id": leg_id, "symbol": "XAUUSD",
        "type": "POSITION_TYPE_BUY" if leg["direction"] == "achat" else "POSITION_TYPE_SELL",
        "openPrice": leg["entry_price"], "stopLoss": leg["stop_loss"],
        "time": leg["entry_time"].isoformat(), "takeProfit": leg.get("take_profit"),
    }


def run_reference_backtest(candles_5min: list[dict]) -> list[dict]:
    """Renvoie la liste des trades reels (une entree = 1 ou 2 jambes,
    partielle + runner), chaque jambe avec : entry_time, exit_time,
    direction, entry_price, exit_price, volume, r_final (PnL $ / capital
    risque initial de l'ENTREE ENTIERE, pas de la jambe seule -- cf. le
    risque est pris sur le total_size avant scission partial/runner),
    r_pire (pire excursion en R atteinte avant la sortie, mesuree sur
    cette jambe uniquement)."""
    n = len(candles_5min)
    circuit_breaker = shared_risk.CircuitBreaker(threshold_pct=CIRCUIT_BREAKER_THRESHOLD)
    balance = REFERENCE_ISC
    legs_open: list[dict] = []  # chaque: direction, entry_price, stop_loss, take_profit, volume, entry_time, entry_idx, risked_dollars, worst_r
    next_leg_id = 1
    trades: list[dict] = []

    # Index des pas de decision 15min : un pas tous les 3 bougies 5min,
    # a partir du moment ou 1000 bougies sont disponibles.
    decision_idxs = set(range(CANDLES_FETCH_LIMIT - 1, n, 3))

    i = 0
    while i < n:
        c = candles_5min[i]
        now = _parse(c["time"])

        # 1) Execution intra-barre des stops/take-profits deja ouverts
        #    (le broker reel execute des qu'un niveau est touche, pas au
        #    prochain cycle de decision). Convention conservatrice en cas
        #    de double-touche dans la meme bougie (stop ET TP tous les
        #    deux dans high/low) : le stop est traite en premier (pire cas
        #    pour le compte, cf. limite documentee dans le README).
        still_open = []
        for leg in legs_open:
            # BUG REEL TROUVE EN SESSION (trade du 2022-09-28, krach
            # obligataire UK -- jour de volatilite genuinement elevee) :
            # un GAP entre deux bougies peut sauter par-dessus le niveau de
            # stop sans qu'aucune des deux bougies ne le "touche" dans son
            # propre [low, high] (ex. bougie N : high=1632.07, bougie N+1 :
            # open=1632.41, low=1632.37, stop=1632.124 -- jamais dans
            # [low, high] d'une seule bougie). Sans ce cas, la position
            # tenait jusqu'a la cloture de session a -6.36R au lieu de
            # s'arreter a ~-1R. Direction-aware : le OPEN de la bougie
            # compte aussi, avec un remplissage au OPEN (pas au niveau
            # nominal du stop) si le gap est deja passe le niveau --
            # convention realiste (le stop devient un ordre au marche une
            # fois franchi, jamais garanti a son prix nominal sur un gap).
            direction = leg["direction"]
            stop = leg["stop_loss"]
            if direction == "achat":
                stop_touched_in_bar = c["low"] <= stop <= c["high"]
                stop_gapped_through = c["open"] <= stop
            else:
                stop_touched_in_bar = c["low"] <= stop <= c["high"]
                stop_gapped_through = c["open"] >= stop
            stop_hit = stop_touched_in_bar or stop_gapped_through
            stop_fill = (min(stop, c["open"]) if direction == "achat" else max(stop, c["open"])
                         ) if stop_gapped_through and not stop_touched_in_bar else stop

            tp = leg["take_profit"]
            tp_hit = False
            tp_fill = tp
            if tp is not None:
                if direction == "achat":
                    tp_touched_in_bar = c["low"] <= tp <= c["high"]
                    tp_gapped_through = c["open"] >= tp
                else:
                    tp_touched_in_bar = c["low"] <= tp <= c["high"]
                    tp_gapped_through = c["open"] <= tp
                tp_hit = tp_touched_in_bar or tp_gapped_through
                tp_fill = (max(tp, c["open"]) if direction == "achat" else min(tp, c["open"])
                           ) if tp_gapped_through and not tp_touched_in_bar else tp

            exit_price = None
            if stop_hit:
                exit_price = stop_fill
            elif tp_hit:
                exit_price = tp_fill

            # Suivi de la pire excursion en R, a chaque bougie tant que la
            # jambe reste ouverte (y compris celle qui declenche la sortie).
            floating = _pnl_dollars(leg["direction"], leg["entry_price"], c["close"], leg["volume"])
            r_now = floating / leg["risked_dollars"] if leg["risked_dollars"] else 0.0
            leg["worst_r"] = min(leg["worst_r"], r_now)

            if exit_price is not None:
                pnl = _pnl_dollars(leg["direction"], leg["entry_price"], exit_price, leg["volume"])
                r_final = pnl / leg["risked_dollars"] if leg["risked_dollars"] else 0.0
                leg["worst_r"] = min(leg["worst_r"], r_final)
                balance += pnl
                trades.append({
                    "entry_time": leg["entry_time"], "exit_time": now,
                    "direction": leg["direction"], "entry_price": leg["entry_price"],
                    "exit_price": exit_price, "volume": leg["volume"],
                    "r_final": r_final, "r_pire": leg["worst_r"],
                })
            else:
                still_open.append(leg)
        legs_open = still_open

        # 2) Cloture de fin de session (22h UTC) -- a la cadence 15min
        #    comme en prod (decide_and_act_vwap le gere), simule ici
        #    directement au close de la bougie courante.
        if legs_open and now.hour >= vwap_reversion.SESSION_END_HOUR_UTC:
            for leg in legs_open:
                pnl = _pnl_dollars(leg["direction"], leg["entry_price"], c["close"], leg["volume"])
                r_final = pnl / leg["risked_dollars"] if leg["risked_dollars"] else 0.0
                leg["worst_r"] = min(leg["worst_r"], r_final)
                balance += pnl
                trades.append({
                    "entry_time": leg["entry_time"], "exit_time": now,
                    "direction": leg["direction"], "entry_price": leg["entry_price"],
                    "exit_price": c["close"], "volume": leg["volume"],
                    "r_final": r_final, "r_pire": leg["worst_r"],
                })
            legs_open = []

        # 3) Decision toutes les 15min (fenetre glissante de 1000 bougies
        #    5min, exactement comme gold_bot.loop -- jamais plus d'historique).
        if i in decision_idxs:
            window = candles_5min[max(0, i - CANDLES_FETCH_LIMIT + 1):i + 1]
            equity = balance
            for leg in legs_open:
                equity += _pnl_dollars(leg["direction"], leg["entry_price"], c["close"], leg["volume"])
            open_positions = [_to_metaapi_position(1000 + idx, leg) for idx, leg in enumerate(legs_open)]
            result = bot.decide_and_act_vwap(
                window, contract_size=CONTRACT_SIZE, balance=balance, equity=equity,
                volume_step=VOLUME_STEP, min_volume=MIN_VOLUME, max_volume=MAX_VOLUME,
                open_positions=open_positions, circuit_breaker=circuit_breaker,
                symbol="XAUUSD", risk_pct=REFERENCE_RISK_PCT,
            )
            if result["action"] == "simulation":
                # "clôture_simulee" n'est en pratique jamais renvoyee ici :
                # l'etape 2 ci-dessus ferme deja toute jambe ouverte des la
                # premiere bougie 5min a hour_utc >= SESSION_END_HOUR_UTC,
                # strictement avant que ce pas de decision 15min puisse
                # jamais voir une position ouverte a cette heure-la. Gardee
                # par defense (fidelite a decide_and_act_vwap) via un set
                # d'indices fermes explicite, jamais une re-derivation
                # ambigue a partir de `result["steps"]` apres coup.
                closed_indices: set[int] = set()
                for step in result["steps"]:
                    if step["type"] == "ouverture_simulee":
                        risked_dollars = REFERENCE_RISK_PCT * balance
                        legs_open.append({
                            "direction": step["direction"], "entry_price": step["entry"],
                            "stop_loss": step["stop_loss"], "take_profit": step.get("take_profit"),
                            "volume": step["volume"], "entry_time": now,
                            "risked_dollars": risked_dollars, "worst_r": 0.0,
                        })
                    elif step["type"] == "modification_simulee":
                        idx = step["position_id"] - 1000
                        if 0 <= idx < len(legs_open):
                            legs_open[idx]["stop_loss"] = step["new_stop_loss"]
                    elif step["type"] == "clôture_simulee":
                        idx = step["position_id"] - 1000
                        if 0 <= idx < len(legs_open) and idx not in closed_indices:
                            leg = legs_open[idx]
                            pnl = _pnl_dollars(leg["direction"], leg["entry_price"], c["close"], leg["volume"])
                            r_final = pnl / leg["risked_dollars"] if leg["risked_dollars"] else 0.0
                            leg["worst_r"] = min(leg["worst_r"], r_final)
                            balance += pnl
                            trades.append({
                                "entry_time": leg["entry_time"], "exit_time": now,
                                "direction": leg["direction"], "entry_price": leg["entry_price"],
                                "exit_price": c["close"], "volume": leg["volume"],
                                "r_final": r_final, "r_pire": leg["worst_r"],
                            })
                            closed_indices.add(idx)
                if closed_indices:
                    legs_open = [leg for j, leg in enumerate(legs_open) if j not in closed_indices]

        i += 1

    return [
        {**t, "entry_time": t["entry_time"].isoformat(), "exit_time": t["exit_time"].isoformat()}
        for t in trades
    ]


if __name__ == "__main__":
    cache_path = os.path.join(os.path.dirname(__file__), "data", "xauusd_5min_raw.json")
    with open(cache_path, encoding="utf-8") as fh:
        candles = json.load(fh)
    print(f"{len(candles)} bougies 5min chargees ({candles[0]['time']} -> {candles[-1]['time']})")

    trades = run_reference_backtest(candles)
    print(f"{len(trades)} trades (jambes) generes")

    out_path = os.path.join(os.path.dirname(__file__), "data", "trades_reference.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(trades, fh, indent=2)
    print(f"ecrit dans {out_path}")
