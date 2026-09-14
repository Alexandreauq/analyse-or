# gold_bot/confluence.py
# Portage fidèle du moteur de confluence de docs/scalping.js (voir
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md). Le
# comportement du JS fait foi, y compris la condition de tendance
# volontairement inversée dans compute_signal (achat exige un contexte
# de tendance "baissier" près d'un support — stratégie de renversement
# sur structure, pas de suivi de tendance).
import math
from datetime import datetime, timezone

import requests

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"


def _parse_float(raw):
    """Comme parseFloat en JS : une valeur non numérique donne NaN plutôt
    que de lever, pour que le contrôle de validité en aval (math.isfinite)
    l'attrape au bon endroit plutôt qu'un crash prématuré."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float("nan")


def fetch_gold_candles(api_key: str) -> list[dict]:
    """Récupère les dernières bougies 1min XAU/USD via Twelve Data.
    Renvoie un tableau chronologique (plus ancien en premier), jamais
    vide en cas de succès. Lève RuntimeError au message clair en cas
    d'échec réseau, de quota dépassé, ou de réponse invalide."""
    params = {
        "symbol": "XAU/USD",
        "interval": "1min",
        "outputsize": "90",
        "timezone": "UTC",
        "apikey": api_key,
    }
    try:
        response = requests.get(TWELVE_DATA_URL, params=params, timeout=15)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Impossible de contacter Twelve Data : {e}")
    if not response.ok:
        raise RuntimeError(f"Twelve Data a répondu {response.status_code}")
    data = response.json()
    if data.get("status") == "error" or not isinstance(data.get("values"), list):
        raise RuntimeError(f"Réponse Twelve Data invalide : {data.get('message', 'pas de données')}")
    candles = [
        {
            "time": v["datetime"],
            "open": _parse_float(v["open"]),
            "high": _parse_float(v["high"]),
            "low": _parse_float(v["low"]),
            "close": _parse_float(v["close"]),
        }
        for v in data["values"]
    ]
    candles.reverse()
    if not candles:
        raise RuntimeError("Twelve Data a renvoyé une liste de bougies vide")
    if any(not math.isfinite(c["close"]) for c in candles):
        raise RuntimeError("Twelve Data a renvoyé des valeurs de prix invalides")
    return candles


def detect_pivots(candles: list[dict], k: int) -> list[dict]:
    """Un pivot haut à l'index i : High[i] strictement supérieur aux High
    des k bougies avant ET après (symétrique pour un pivot bas sur Low)."""
    pivots = []
    for i in range(k, len(candles) - k):
        is_high = (
            all(candles[j]["high"] < candles[i]["high"] for j in range(i - k, i))
            and all(candles[j]["high"] < candles[i]["high"] for j in range(i + 1, i + 1 + k))
        )
        if is_high:
            pivots.append({"index": i, "type": "high", "price": candles[i]["high"]})
        is_low = (
            all(candles[j]["low"] > candles[i]["low"] for j in range(i - k, i))
            and all(candles[j]["low"] > candles[i]["low"] for j in range(i + 1, i + 1 + k))
        )
        if is_low:
            pivots.append({"index": i, "type": "low", "price": candles[i]["low"]})
    pivots.sort(key=lambda p: p["index"])
    return pivots


def classify_trend(pivots: list[dict]) -> str:
    """Haussier si les 2 derniers pivots bas ET les 2 derniers pivots hauts
    confirmés sont strictement croissants ; symétrique pour baissier ;
    neutre sinon (y compris si pas assez de pivots d'un type ou l'autre)."""
    lows = [p for p in pivots if p["type"] == "low"]
    highs = [p for p in pivots if p["type"] == "high"]
    if len(lows) < 2 or len(highs) < 2:
        return "neutre"
    last_lows = lows[-2:]
    last_highs = highs[-2:]
    lows_rising = last_lows[1]["price"] > last_lows[0]["price"]
    highs_rising = last_highs[1]["price"] > last_highs[0]["price"]
    lows_falling = last_lows[1]["price"] < last_lows[0]["price"]
    highs_falling = last_highs[1]["price"] < last_highs[0]["price"]
    if lows_rising and highs_rising:
        return "haussier"
    if lows_falling and highs_falling:
        return "baissier"
    return "neutre"


def current_levels(pivots: list[dict], current_price: float) -> dict:
    """Support = dernier pivot bas confirmé sous current_price ;
    résistance = dernier pivot haut confirmé au-dessus. None si aucun
    pivot de ce côté."""
    below_lows = [p for p in pivots if p["type"] == "low" and p["price"] < current_price]
    above_highs = [p for p in pivots if p["type"] == "high" and p["price"] > current_price]
    return {
        "support": below_lows[-1]["price"] if below_lows else None,
        "resistance": above_highs[-1]["price"] if above_highs else None,
    }


def compute_rsi(closes: list[float], period: int) -> float:
    """RSI classique : compare la moyenne des hausses à la moyenne des
    baisses sur les `period` dernières variations. 100 si aucune baisse
    (évite une division par zéro)."""
    changes = [closes[i] - closes[i - 1] for i in range(len(closes) - period, len(closes))]
    gains = [c for c in changes if c > 0]
    losses = [-c for c in changes if c < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _ema_last(values: list[float], period: int) -> float:
    """EMA seedée par une SMA des `period` premières valeurs. Dernière
    valeur uniquement."""
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _ema_series(values: list[float], period: int) -> list[float]:
    """Série complète des EMA (une valeur par index à partir de
    period-1) — nécessaire pour la ligne signal du MACD."""
    k = 2 / (period + 1)
    out = []
    ema = sum(values[:period]) / period
    out.append(ema)
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
        out.append(ema)
    return out


def compute_macd(closes: list[float], fast_period: int, slow_period: int, signal_period: int) -> dict:
    """MACD(fast, slow, signal). Renvoie uniquement le dernier point."""
    fast_series = _ema_series(closes, fast_period)
    slow_series = _ema_series(closes, slow_period)
    offset = len(fast_series) - len(slow_series)
    macd_series = [fast_series[offset + i] - slow_series[i] for i in range(len(slow_series))]
    signal = _ema_last(macd_series, signal_period)
    macd = macd_series[-1]
    return {"macd": macd, "signal": signal, "histogram": macd - signal}


def compute_bollinger(closes: list[float], period: int, mult: float) -> dict:
    """Bandes de Bollinger : MM `period` ± mult × écart-type population.
    Dernier point uniquement. Non utilisé par compute_signal (comme dans
    le JS d'origine) — conservé pour parité de test."""
    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((c - mean) ** 2 for c in window) / period
    stdev = variance ** 0.5
    return {"middle": mean, "upper": mean + mult * stdev, "lower": mean - mult * stdev}


def _body_size(c):
    return abs(c["close"] - c["open"])


def _is_bullish(c):
    return c["close"] > c["open"]


def _upper_wick(c):
    return c["high"] - max(c["open"], c["close"])


def _lower_wick(c):
    return min(c["open"], c["close"]) - c["low"]


def match_candlestick_pattern(candles: list[dict], trend: str) -> dict | None:
    """Reconnaît un sous-ensemble de 8 figures de chandeliers sur les 1 à
    3 dernières bougies. `trend` est la tendance courte au moment de
    l'examen — plusieurs figures n'ont de sens qu'en contexte. Renvoie la
    première figure trouvée (3 bougies avant 2 avant 1) ou None."""
    n = len(candles)
    if n < 1:
        return None
    last = candles[-1]

    if n >= 3:
        c1, c2, c3 = candles[-3:]
        if trend == "baissier" and not _is_bullish(c1) and _body_size(c2) < _body_size(c1) * 0.5 \
                and _is_bullish(c3) and c3["close"] > (c1["open"] + c1["close"]) / 2:
            return {"name": "Étoile du Matin", "direction": "haussier"}
        if trend == "haussier" and _is_bullish(c1) and _body_size(c2) < _body_size(c1) * 0.5 \
                and not _is_bullish(c3) and c3["close"] < (c1["open"] + c1["close"]) / 2:
            return {"name": "Étoile du Soir", "direction": "baissier"}

    if n >= 2:
        prev, cur = candles[-2:]
        if trend == "baissier" and not _is_bullish(prev) and _is_bullish(cur) \
                and cur["open"] <= prev["close"] and cur["close"] >= prev["open"]:
            return {"name": "Englobante haussière", "direction": "haussier"}
        if trend == "haussier" and _is_bullish(prev) and not _is_bullish(cur) \
                and cur["open"] >= prev["close"] and cur["close"] <= prev["open"]:
            return {"name": "Englobante baissière", "direction": "baissier"}
        if trend == "baissier" and not _is_bullish(prev) and _is_bullish(cur) \
                and cur["open"] < prev["close"] and cur["close"] > (prev["open"] + prev["close"]) / 2 \
                and cur["close"] < prev["open"]:
            return {"name": "Pénétrante", "direction": "haussier"}
        if trend == "haussier" and _is_bullish(prev) and not _is_bullish(cur) \
                and cur["open"] > prev["close"] and cur["close"] < (prev["open"] + prev["close"]) / 2 \
                and cur["close"] > prev["open"]:
            return {"name": "Nuage noir", "direction": "baissier"}

    body = _body_size(last)
    upper_wick = _upper_wick(last)
    lower_wick = _lower_wick(last)
    if trend == "baissier" and lower_wick >= body * 2 and upper_wick < body * 0.3:
        return {"name": "Marteau", "direction": "haussier"}
    if trend == "haussier" and upper_wick >= body * 2 and lower_wick < body * 0.3:
        return {"name": "Étoile filante", "direction": "baissier"}

    return None


SCALP_PIVOT_K = 3
SCALP_RSI_PERIOD = 14
SCALP_MACD_FAST = 12
SCALP_MACD_SLOW = 26
SCALP_MACD_SIGNAL = 9
SCALP_BOLLINGER_PERIOD = 20
SCALP_BOLLINGER_MULT = 2
SCALP_TAKEPROFIT_RISK_MULTIPLE = 1.5
SCALP_LEVEL_PROXIMITY = 0.5
SCALP_MIN_CANDLES = max(SCALP_BOLLINGER_PERIOD, SCALP_MACD_SLOW + SCALP_MACD_SIGNAL) + 1
SCALP_STOP_BUFFER = SCALP_LEVEL_PROXIMITY * 3

# Fenêtre de black-out autour des publications macro à très fort impact
# (CPI/Emploi US/FOMC, décision BCE, décision Bank of England) — portage
# fidèle de la même constante côté docs/scalping.js (voir son commentaire
# pour le raisonnement complet, les sources officielles, et pourquoi
# MyFxBook n'est pas utilisé comme source — pas d'API publique, testé le
# 13/09/2026, 403 Forbidden). Ces deux listes DOIVENT rester
# synchronisées — mettre à jour les deux quand chaque calendrier 2027 est
# publié.
SCALP_NEWS_BLACKOUT_MINUTES = 15
SCALP_HIGH_IMPACT_EVENTS_UTC = [
    # CPI (indice des prix à la consommation US), 8h30 ET — source :
    # bls.gov/schedule/news_release/cpi.htm
    "2026-01-13T13:30:00Z", "2026-02-13T13:30:00Z", "2026-03-11T12:30:00Z",
    "2026-04-10T12:30:00Z", "2026-05-12T12:30:00Z", "2026-06-10T12:30:00Z",
    "2026-07-14T12:30:00Z", "2026-08-12T12:30:00Z", "2026-09-11T12:30:00Z",
    "2026-10-14T12:30:00Z", "2026-11-10T13:30:00Z", "2026-12-10T13:30:00Z",
    # Emploi US / NFP (Employment Situation), 8h30 ET — source :
    # bls.gov/schedule/news_release/empsit.htm
    "2026-01-09T13:30:00Z", "2026-02-11T13:30:00Z", "2026-03-06T13:30:00Z",
    "2026-04-03T12:30:00Z", "2026-05-08T12:30:00Z", "2026-06-05T12:30:00Z",
    "2026-07-02T12:30:00Z", "2026-08-07T12:30:00Z", "2026-09-04T12:30:00Z",
    "2026-10-02T12:30:00Z", "2026-11-06T13:30:00Z", "2026-12-04T13:30:00Z",
    # Décision FOMC (taux directeur US), 14h00 ET, 2e jour de chaque
    # réunion — source : federalreserve.gov/monetarypolicy/fomccalendars.htm
    "2026-01-28T19:00:00Z", "2026-03-18T18:00:00Z", "2026-04-29T18:00:00Z",
    "2026-06-17T18:00:00Z", "2026-07-29T18:00:00Z", "2026-09-16T18:00:00Z",
    "2026-10-28T18:00:00Z", "2026-12-09T19:00:00Z",
    # Décision BCE (taux directeur zone euro), 14h15 CET/CEST, 2e jour de
    # chaque réunion — source : ecb.europa.eu/press/calendars/mgcgc
    "2026-03-19T13:15:00Z", "2026-04-30T12:15:00Z", "2026-06-11T12:15:00Z",
    "2026-07-23T12:15:00Z", "2026-09-10T12:15:00Z", "2026-10-29T13:15:00Z",
    "2026-12-17T13:15:00Z",
    # Décision Bank of England (taux directeur GBP), 12h00 heure de
    # Londres — source : bankofengland.co.uk/monetary-policy/upcoming-mpc-dates
    "2026-02-05T12:00:00Z", "2026-03-19T12:00:00Z", "2026-04-30T11:00:00Z",
    "2026-06-18T11:00:00Z", "2026-07-30T11:00:00Z", "2026-09-17T11:00:00Z",
    "2026-11-05T12:00:00Z", "2026-12-17T12:00:00Z",
]


def is_news_blackout(date: datetime) -> bool:
    """`date` tombe-t-elle dans la fenêtre de black-out
    (± SCALP_NEWS_BLACKOUT_MINUTES) d'une publication macro à très fort
    impact ? Utilisé pour neutraliser compute_signal plutôt que de
    laisser le moteur technique interpréter le bruit de la publication
    comme un vrai signal."""
    window = SCALP_NEWS_BLACKOUT_MINUTES * 60
    for iso in SCALP_HIGH_IMPACT_EVENTS_UTC:
        event = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if abs((date - event).total_seconds()) <= window:
            return True
    return False


def compute_signal(candles: list[dict]) -> dict:
    """Moteur de confluence : combine tendance + S/R + indicateurs +
    chandeliers en un signal achat/vente/neutre, avec entry/stop_loss/
    take_profit si un signal est émis. Ne lève jamais d'exception —
    `candles` trop court renvoie neutre avec tous les prix à None, de
    même qu'en pleine fenêtre de black-out macro (voir is_news_blackout)."""
    price = candles[-1]["close"] if candles else None
    if not price or len(candles) < SCALP_MIN_CANDLES:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}
    as_of = datetime.fromisoformat(candles[-1]["time"].replace(" ", "T")).replace(tzinfo=timezone.utc)
    if is_news_blackout(as_of):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}

    pivots = detect_pivots(candles, SCALP_PIVOT_K)
    trend = classify_trend(pivots)
    levels = current_levels(pivots, price)
    closes = [c["close"] for c in candles]
    rsi = compute_rsi(closes, SCALP_RSI_PERIOD)
    macd = compute_macd(closes, SCALP_MACD_FAST, SCALP_MACD_SLOW, SCALP_MACD_SIGNAL)
    pattern = match_candlestick_pattern(candles, trend)

    near_support = levels["support"] is not None and abs(price - levels["support"]) <= SCALP_LEVEL_PROXIMITY
    near_resistance = levels["resistance"] is not None and abs(price - levels["resistance"]) <= SCALP_LEVEL_PROXIMITY
    broke_resistance = levels["resistance"] is not None and price > levels["resistance"]
    broke_support = levels["support"] is not None and price < levels["support"]

    structurel_achat = trend == "baissier" and (near_support or broke_resistance)
    structurel_vente = trend == "haussier" and (near_resistance or broke_support)

    confirmation_achat = rsi < 70 and macd["macd"] > macd["signal"] and pattern is not None and pattern["direction"] == "haussier"
    confirmation_vente = rsi > 30 and macd["macd"] < macd["signal"] and pattern is not None and pattern["direction"] == "baissier"

    if structurel_achat and confirmation_achat:
        stop_loss = levels["support"] - SCALP_STOP_BUFFER if levels["support"] is not None else price - price * 0.001
        risk = price - stop_loss
        take_profit = levels["resistance"] if (levels["resistance"] is not None and levels["resistance"] > price) \
            else price + risk * SCALP_TAKEPROFIT_RISK_MULTIPLE
        return {"status": "achat", "price": price, "entry": price, "stop_loss": stop_loss,
                "take_profit": take_profit, "trend": trend, "pattern": pattern}
    if structurel_vente and confirmation_vente:
        stop_loss = levels["resistance"] + SCALP_STOP_BUFFER if levels["resistance"] is not None else price + price * 0.001
        risk = stop_loss - price
        take_profit = levels["support"] if (levels["support"] is not None and levels["support"] < price) \
            else price - risk * SCALP_TAKEPROFIT_RISK_MULTIPLE
        return {"status": "vente", "price": price, "entry": price, "stop_loss": stop_loss,
                "take_profit": take_profit, "trend": trend, "pattern": pattern}
    return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
            "take_profit": None, "trend": trend, "pattern": None}
