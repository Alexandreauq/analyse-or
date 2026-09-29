# gold_bot/session_breakout.py
# Moteur de signal "cassure de range de session, confirmée par le volume"
# -- voir docs/superpowers/specs/2026-09-29-gold-bot-session-breakout-
# volume-design.md. Hypothèse de marché différente du moteur à figures
# chartistes de gold_bot/confluence.py (toujours présent, non modifié) :
# variation structurelle de la liquidité/participation selon les sessions
# de trading, confirmée par le volume réel (tick volume), plutôt que de
# la reconnaissance de forme pure. N'est PAS branché sur le bot en
# production par ce module -- la décision de bascule se prend après le
# plan de validation multi-années décrit dans la spec.
from datetime import datetime, timezone

import gold_bot.confluence as confluence

ASIAN_SESSION_START_HOUR = 0
ASIAN_SESSION_END_HOUR = 8
TRADING_WINDOW_START_HOUR = 8
TRADING_WINDOW_END_HOUR = 16
MIN_ASIAN_SESSION_CANDLES = 48
VOLUME_CONFIRMATION_MULTIPLE = 1.5
VOLUME_CONFIRMATION_LOOKBACK = 20


def _candle_dt(candle: dict) -> datetime:
    return datetime.fromisoformat(candle["time"].replace(" ", "T")).replace(tzinfo=timezone.utc)


def compute_asian_range(candles: list[dict], as_of: datetime) -> dict | None:
    """Plus haut/plus bas de la session asiatique (00h-08h UTC) du jour
    UTC de `as_of`. None si moins de MIN_ASIAN_SESSION_CANDLES bougies
    complètes sont disponibles pour cette fenêtre (trou de données ou
    fenêtre pas encore écoulée) -- neutre plutôt que deviner sur un
    échantillon non représentatif."""
    today = as_of.date()
    session_candles = [
        c for c in candles
        if _candle_dt(c).date() == today
        and ASIAN_SESSION_START_HOUR <= _candle_dt(c).hour < ASIAN_SESSION_END_HOUR
    ]
    if len(session_candles) < MIN_ASIAN_SESSION_CANDLES:
        return None
    return {
        "high": max(c["high"] for c in session_candles),
        "low": min(c["low"] for c in session_candles),
    }


def _fresh_crossing(candles: list[dict], asian_range: dict) -> tuple[str, float] | None:
    """Bougie de franchissement : le close courant dépasse une borne du
    range que le close précédent ne dépassait pas encore -- purement
    local (2 dernières bougies), pour que rester au-delà du range après
    la cassure ne redéclenche pas le signal indéfiniment. Une deuxième
    cassure plus tard dans la journée (après un retour dans le range)
    reste possible et légitime."""
    if len(candles) < 2:
        return None
    previous_close = candles[-2]["close"]
    current_close = candles[-1]["close"]
    if current_close > asian_range["high"] and previous_close <= asian_range["high"]:
        return "achat", current_close
    if current_close < asian_range["low"] and previous_close >= asian_range["low"]:
        return "vente", current_close
    return None


def _has_volume_confirmation(candles: list[dict]) -> bool:
    """tick_volume de la dernière bougie > VOLUME_CONFIRMATION_MULTIPLE
    fois la moyenne des VOLUME_CONFIRMATION_LOOKBACK bougies précédentes.
    False si pas assez d'historique pour calculer une moyenne."""
    if len(candles) < VOLUME_CONFIRMATION_LOOKBACK + 1:
        return False
    lookback = candles[-1 - VOLUME_CONFIRMATION_LOOKBACK:-1]
    average = sum(c["tick_volume"] for c in lookback) / len(lookback)
    if average <= 0:
        return False
    return candles[-1]["tick_volume"] > VOLUME_CONFIRMATION_MULTIPLE * average


def compute_signal(candles: list[dict]) -> dict:
    """Moteur de suivi de tendance par cassure de session confirmée par le
    volume -- voir le design pour le détail complet. Ne lève jamais
    d'exception : candles vide, hors fenêtre de trading, range invalide,
    cassure non fraîche, volume insuffisant, ratio risque/rendement
    insuffisant, ou black-out news renvoient tous neutre avec les prix à
    None."""
    price = candles[-1]["close"] if candles else None
    if not price:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}
    as_of = _candle_dt(candles[-1])
    if confluence.is_news_blackout(as_of):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}
    if not (TRADING_WINDOW_START_HOUR <= as_of.hour < TRADING_WINDOW_END_HOUR):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}

    asian_range = compute_asian_range(candles, as_of)
    if asian_range is None:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}

    crossing = _fresh_crossing(candles, asian_range)
    if crossing is None:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}
    direction, breakout_price = crossing

    if not _has_volume_confirmation(candles):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}

    range_height = asian_range["high"] - asian_range["low"]
    if direction == "achat":
        stop_loss = asian_range["high"] - confluence.CHARTPATTERN_STOP_BUFFER
        take_profit = breakout_price + range_height
        trend = "haussier"
    else:
        stop_loss = asian_range["low"] + confluence.CHARTPATTERN_STOP_BUFFER
        take_profit = breakout_price - range_height
        trend = "baissier"

    if not confluence.meets_minimum_risk_reward(price, stop_loss, take_profit, direction):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": trend, "pattern": None}

    return {"status": direction, "price": price, "entry": price, "stop_loss": stop_loss,
            "take_profit": take_profit, "trend": trend, "pattern": None}
