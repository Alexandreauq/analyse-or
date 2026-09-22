# gold_bot/backtest.py
# Rejoue le VRAI moteur de décision du bot (gold_bot.confluence.compute_signal
# — contre-tendance, confirmation par chandelier uniquement, voir son
# commentaire de module) sur de l'historique réel XAU/USD, pour mesurer une
# performance qu'aucun trade de paper-trading existant ne représente (les 34
# trades de docs/scalping_tracking.json utilisent tous une figure chartiste,
# jamais un chandelier — voir docs/superpowers specs/plans liés à l'audit du
# 2026-09-21). Outil d'analyse hors-ligne, ne place jamais d'ordre, ne
# partage aucun état avec gold_bot.loop.
import json
import os
from datetime import datetime, timedelta, timezone

import requests

import gold_bot.confluence as confluence

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
# Plafond de l'API Twelve Data pour un seul appel time_series.
MAX_OUTPUT_SIZE = 5000
# Aligné sur l'appel réel de confluence.fetch_gold_candles (outputsize=90) :
# la fenêtre glissante soumise à compute_signal doit avoir la même taille
# que ce que le bot voit réellement en production, jamais tout l'historique
# accumulé depuis le début du backtest.
SIGNAL_WINDOW_SIZE = 90
DEFAULT_BACKTEST_DAYS = 60
RESULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_results.json")


def fetch_gold_candles_range(api_key: str, start: datetime, end: datetime) -> list[dict]:
    """Récupère tout l'historique XAU/USD 1min entre `start` et `end`
    (bornes incluses, UTC), en paginant par blocs de MAX_OUTPUT_SIZE via le
    paramètre `end_date` de Twelve Data (chaque appel renvoie au plus
    MAX_OUTPUT_SIZE bougies se terminant à `end_date`, les plus récentes en
    premier). Résultat trié chronologiquement, sans doublon. Lève
    RuntimeError au premier échec réseau/API — mêmes contrats d'erreur que
    confluence.fetch_gold_candles."""
    if start >= end:
        raise ValueError("start doit être strictement antérieur à end")

    all_candles: dict[str, dict] = {}
    cursor = end
    # Marge généreuse : même avec des pages plus petites que MAX_OUTPUT_SIZE
    # (marché fermé, trous de données), ce plafond laisse largement de quoi
    # couvrir la plage demandée sans boucler indéfiniment en cas de réponse
    # inattendue de l'API.
    max_iterations = max(10, int((end - start).total_seconds() / 60 / MAX_OUTPUT_SIZE) + 10)

    for _ in range(max_iterations):
        params = {
            "symbol": "XAU/USD",
            "interval": "1min",
            "outputsize": str(MAX_OUTPUT_SIZE),
            "end_date": cursor.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": "UTC",
            "apikey": api_key,
        }
        response = requests.get(TWELVE_DATA_URL, params=params, timeout=30)
        if not response.ok:
            raise RuntimeError(f"Twelve Data a répondu {response.status_code}")
        data = response.json()
        if data.get("status") == "error" or not isinstance(data.get("values"), list):
            raise RuntimeError(f"Réponse Twelve Data invalide : {data.get('message', 'pas de données')}")
        values = data["values"]
        if not values:
            break
        for v in values:
            t = v["datetime"]
            all_candles[t] = {
                "time": t,
                "open": confluence._parse_float(v["open"]),
                "high": confluence._parse_float(v["high"]),
                "low": confluence._parse_float(v["low"]),
                "close": confluence._parse_float(v["close"]),
            }
        oldest = datetime.fromisoformat(values[-1]["datetime"].replace(" ", "T")).replace(tzinfo=timezone.utc)
        if oldest <= start:
            break
        cursor = oldest - timedelta(minutes=1)

    result = [
        c for c in all_candles.values()
        if start <= datetime.fromisoformat(c["time"].replace(" ", "T")).replace(tzinfo=timezone.utc) <= end
    ]
    result.sort(key=lambda c: c["time"])
    return result


def _compute_return(direction: str, entry_price: float, close_price: float) -> tuple[float, float]:
    """Achat : gagnant si le prix monte. Vente : gagnant si le prix baisse.
    Même convention que computeReturn (scalping_tracker.js)."""
    return_usd = close_price - entry_price if direction == "achat" else entry_price - close_price
    return_pct = (return_usd / entry_price) * 100
    return return_usd, return_pct


def _close_trade(trade: dict, close_price: float, reason: str, close_time: str) -> None:
    return_usd, return_pct = _compute_return(trade["direction"], trade["entry_price"], close_price)
    trade["close_time"] = close_time
    trade["close_price"] = close_price
    trade["close_reason"] = reason
    trade["return_usd"] = return_usd
    trade["return_pct"] = return_pct


def _open_trade_from_signal(signal: dict, entry_time: str) -> dict:
    return {
        "direction": signal["status"],
        "entry_time": entry_time,
        "entry_price": signal["entry"],
        "stop_loss": signal["stop_loss"],
        "take_profit": signal["take_profit"],
        "trend_at_entry": signal["trend"],
        "pattern_at_entry": signal["pattern"]["name"] if signal["pattern"] else None,
    }


def simulate_trades(candles: list[dict], window_size: int = SIGNAL_WINDOW_SIZE,
                     signal_fn=confluence.compute_signal) -> list[dict]:
    """Rejoue `signal_fn` (compute_signal par défaut) bougie par bougie,
    comme si le bot tournait en continu sur cet historique — une position à
    la fois (jamais d'empilement, comme la vraie boucle), fenêtre glissante
    de `window_size` bougies (jamais tout l'historique accumulé, pour
    refléter fidèlement ce que confluence.fetch_gold_candles renvoie
    réellement en production : toujours les 90 dernières bougies, jamais
    plus).

    Une position ouverte peut se clôturer de 3 façons, dans cet ordre de
    priorité — même hiérarchie que gold_bot.bot.decide_and_act : (1) SL/TP
    touché (SL gagnant en cas de toucher simultané sur la même bougie,
    même désambiguïsation prudente que decidePositionOutcome,
    scalping_tracker.js) ; (2) clôture forcée si la bougie courante tombe
    dans une fenêtre de black-out macro (confluence.is_news_blackout),
    même sans signal de retournement — reproduit le comportement réel de
    decide_and_act, qui clôture toute position ouverte avant une
    publication à fort impact indépendamment du signal ; (3) renversement
    sur signal opposé — `signal_fn` est rappelée à chaque bougie tant
    qu'une position reste ouverte (comme la vraie boucle, qui réévalue
    compute_signal à chaque cycle même position ouverte), et un signal
    dans le sens opposé clôture la position courante et en ouvre
    immédiatement une nouvelle, à la même bougie. Un signal dans le même
    sens ou neutre ne fait rien (position déjà ouverte dans le même sens,
    comme decide_and_act). Une position encore ouverte à la fin de
    `candles` est clôturée au dernier prix connu (raison "fin_backtest")
    plutôt qu'ignorée, pour ne perdre aucun trade des statistiques."""
    trades: list[dict] = []
    open_trade: dict | None = None
    min_needed = confluence.SCALP_MIN_CANDLES

    for i in range(min_needed, len(candles) + 1):
        current = candles[i - 1]
        window = candles[max(0, i - window_size):i]

        if open_trade is not None:
            is_achat = open_trade["direction"] == "achat"
            sl = open_trade["stop_loss"]
            tp = open_trade["take_profit"]
            sl_touched = current["low"] <= sl if is_achat else current["high"] >= sl
            if sl_touched:
                _close_trade(open_trade, sl, "sl_hit", current["time"])
                trades.append(open_trade)
                open_trade = None
                continue
            tp_touched = current["high"] >= tp if is_achat else current["low"] <= tp
            if tp_touched:
                _close_trade(open_trade, tp, "tp_hit", current["time"])
                trades.append(open_trade)
                open_trade = None
                continue

            current_dt = datetime.fromisoformat(current["time"].replace(" ", "T")).replace(tzinfo=timezone.utc)
            if confluence.is_news_blackout(current_dt):
                _close_trade(open_trade, current["close"], "news_blackout", current["time"])
                trades.append(open_trade)
                open_trade = None
                continue

            signal = signal_fn(window)
            if signal["status"] in ("achat", "vente") and signal["status"] != open_trade["direction"]:
                _close_trade(open_trade, current["close"], "renversement", current["time"])
                trades.append(open_trade)
                open_trade = _open_trade_from_signal(signal, current["time"])
            continue

        signal = signal_fn(window)
        if signal["status"] in ("achat", "vente"):
            open_trade = _open_trade_from_signal(signal, current["time"])

    if open_trade is not None:
        last = candles[-1]
        _close_trade(open_trade, last["close"], "fin_backtest", last["time"])
        trades.append(open_trade)

    return trades


def summarize_trades(trades: list[dict]) -> dict:
    """Statistiques agrégées : taux de réussite, P&L en $ et en multiple de
    risque (R = return_usd / |entry_price - stop_loss|), gains/pertes
    moyens. `None` pour toute moyenne sans échantillon (pas de trade, pas
    de gain, pas de perte) plutôt qu'une division par zéro déguisée."""
    if not trades:
        return {
            "trade_count": 0, "win_count": 0, "loss_count": 0, "win_rate_pct": None,
            "total_return_usd": 0.0, "avg_return_usd": None,
            "avg_win_usd": None, "avg_loss_usd": None, "avg_r_multiple": None,
        }

    wins = [t for t in trades if t["return_usd"] > 0]
    losses = [t for t in trades if t["return_usd"] <= 0]
    r_multiples = [
        t["return_usd"] / abs(t["entry_price"] - t["stop_loss"])
        for t in trades if abs(t["entry_price"] - t["stop_loss"]) > 0
    ]
    total_return_usd = sum(t["return_usd"] for t in trades)

    return {
        "trade_count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate_pct": len(wins) / len(trades) * 100,
        "total_return_usd": total_return_usd,
        "avg_return_usd": total_return_usd / len(trades),
        "avg_win_usd": (sum(t["return_usd"] for t in wins) / len(wins)) if wins else None,
        "avg_loss_usd": (sum(t["return_usd"] for t in losses) / len(losses)) if losses else None,
        "avg_r_multiple": (sum(r_multiples) / len(r_multiples)) if r_multiples else None,
    }


def run_backtest(api_key: str, days: int = DEFAULT_BACKTEST_DAYS, end: datetime | None = None) -> dict:
    """Un backtest complet : récupère `days` jours d'historique se terminant
    à `end` (défaut : maintenant), rejoue le moteur réel, résume. `end` est
    injectable pour les tests, même convention que loop.run_cycle(now=...)."""
    end = end or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    candles = fetch_gold_candles_range(api_key, start, end)
    trades = simulate_trades(candles)
    summary = summarize_trades(trades)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "candle_count": len(candles),
        "summary": summary,
        "trades": trades,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Backtest du moteur de confluence Or réel (gold_bot.confluence) sur données historiques Twelve Data.")
    parser.add_argument("--days", type=int, default=DEFAULT_BACKTEST_DAYS,
                         help=f"Nombre de jours d'historique à couvrir (défaut : {DEFAULT_BACKTEST_DAYS}).")
    parser.add_argument("--output", default=RESULTS_PATH, help="Chemin du fichier JSON de résultats.")
    args = parser.parse_args()

    api_key = os.environ["TWELVE_DATA_API_KEY"]
    result = run_backtest(api_key, days=args.days)

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"Résultats complets écrits dans {args.output}")


if __name__ == "__main__":
    main()
