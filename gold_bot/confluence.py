# gold_bot/confluence.py
# Portage fidèle du moteur de confluence de docs/scalping.js (voir
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md). Depuis le
# 2026-09-29, compute_signal utilise le moteur de suivi de tendance par
# figures chartistes (voir gold_bot/chart_patterns.py) et non plus
# l'ancien moteur contre-tendance sur chandelier qu'il utilisait avant
# cette date.
import math
from datetime import datetime, timezone

import requests

import gold_bot.chart_patterns as chart_patterns

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
        # type(e).__name__ (ex: "ConnectTimeout"), jamais str(e) : les
        # exceptions requests/urllib3 embarquent l'URL complète avec la
        # query string, donc apikey en clair -- str(e) finit tel quel
        # dans decisions_log.jsonl puis le résumé quotidien envoyé par
        # email (voir gold_bot.notify) à chaque coupure réseau. Trouvé
        # lors de l'audit pré-lancement du 2026-09-29.
        raise RuntimeError(f"Impossible de contacter Twelve Data : {type(e).__name__}")
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


SCALP_TAKEPROFIT_RISK_MULTIPLE = 1.5  # INCHANGÉ : seuil minimum du filtre R:R (meets_minimum_risk_reward)
SCALP_PIVOT_K = 3  # tendance générale — pivots SÉPARÉS de ceux des figures chartistes (voir CHARTPATTERN_PIVOT_K plus bas). Réintroduit après la revue finale du 29/09 : docs/scalping.js:399-407 calcule la tendance sur des pivots K=3 distincts des pivots K=5 des figures elles-mêmes ; les fusionner en un seul K=5 rendait Tête-Épaule/Tête-Épaule inversée/Triangle symétrique mathématiquement impossibles à déclencher (la condition de tendance et la condition de la figure portaient sur exactement les mêmes pivots).
CHARTPATTERN_STOP_BUFFER = chart_patterns.CHARTPATTERN_HEIGHT_TOLERANCE  # réutilise l'échelle existante ($1), pas un nouveau nombre magique
SCALP_MIN_CANDLES = 2 * chart_patterns.CHARTPATTERN_PIVOT_K + 1  # plancher minimal pour qu'un seul pivot soit détectable ; le vrai filtrage (assez de pivots pour une figure complète) est géré par detect_chart_patterns elle-même

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


def is_market_closed(date: datetime) -> bool:
    """XAU/USD (comme le forex) est fermé du vendredi ~22h UTC au
    dimanche ~22h UTC — bornes volontairement prudentes (les brokers
    varient de quelques dizaines de minutes selon le fournisseur de
    liquidité), mieux vaut rater un peu de marché réel aux bords que
    trader du bruit sur un marché fermé. Indépendant de la fraîcheur
    annoncée par l'API (voir STALE_CANDLE_THRESHOLD_SECONDS dans
    gold_bot.loop.run_cycle) : Twelve Data peut renvoyer une bougie à
    l'horodatage à jour même marché fermé — trouvé en production le
    week-end du 12-13/09/2026 côté paper-trading (29 fausses positions
    ouvertes sur du bruit d'API), d'où ce garde basé uniquement sur
    l'horloge murale, jamais sur les données reçues. Déplacé de
    gold_bot.loop vers ici le 2026-09-29 : gold_bot.backtest en avait
    besoin lui aussi et ne l'avait jamais eu — environ 25% des bougies
    5min de l'historique Twelve Data tombent dans cette fenêtre, avec un
    vrai mouvement de prix (pas plates), et ces bougies non filtrées
    avaient à elles seules expliqué la quasi-totalité de la perte
    annuelle d'un backtest (20 trades sur 816, taux de réussite 5% contre
    16% sur le reste de l'année). Portage fidèle de isMarketClosed
    (scalping_tracker.js). `date` doit être en UTC."""
    day = date.weekday()  # lundi = 0 ... dimanche = 6
    hour = date.hour
    if day == 5:  # samedi : fermé toute la journée
        return True
    if day == 4 and hour >= 22:  # vendredi à partir de 22h UTC
        return True
    if day == 6 and hour < 22:  # dimanche avant 22h UTC
        return True
    return False


def meets_minimum_risk_reward(entry_price: float, stop_loss: float, take_profit: float, direction: str) -> bool:
    """Garde-fou ratio risque/rendement : refuse un trade dont l'objectif
    potentiel (souvent plafonne par le niveau de support/resistance
    oppose le plus proche, puisque l'entree a justement lieu pres d'un
    niveau) ne compense pas suffisamment le risque pris — le repli
    SCALP_TAKEPROFIT_RISK_MULTIPLE ne se declenche en pratique presque
    jamais sans ce garde-fou. Constate sur donnees reelles avant ce
    correctif : gain moyen $6.22 / perte moyenne $8.87 (ratio 0.7),
    causant un leger P&L negatif malgre un taux de reussite > 50%. Le
    seuil reprend volontairement le meme multiple que le repli, pour ne
    jamais accepter un trade moins bon que ce que le repli lui-meme
    viserait. Portage fidele de meetsMinimumRiskReward (docs/scalping.js)."""
    if direction == "achat":
        risk = entry_price - stop_loss
        reward = take_profit - entry_price
    else:
        risk = stop_loss - entry_price
        reward = entry_price - take_profit
    return risk > 0 and reward / risk >= SCALP_TAKEPROFIT_RISK_MULTIPLE


def compute_signal(candles: list[dict]) -> dict:
    """Moteur de suivi de tendance par figures chartistes (portage de
    docs/chart_patterns.js::detectChartPatterns, voir gold_bot/chart_patterns.py)
    — remplace le moteur contre-tendance sur chandelier utilisé jusqu'au
    2026-09-29, retiré de ce fichier (confirmé trop passif en production
    réelle : aucun signal en ~36h avec dry_run désactivé, cohérent avec
    le backtest du 27/09 : ~4 trades/90 jours). Ne lève jamais
    d'exception -- `candles` trop court ou aucune figure détectée renvoie
    neutre avec tous les prix à None, de même qu'en pleine fenêtre de
    black-out macro (voir is_news_blackout)."""
    price = candles[-1]["close"] if candles else None
    if not price or len(candles) < SCALP_MIN_CANDLES:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}
    as_of = datetime.fromisoformat(candles[-1]["time"].replace(" ", "T")).replace(tzinfo=timezone.utc)
    if is_news_blackout(as_of):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}

    trend_pivots = detect_pivots(candles, SCALP_PIVOT_K)
    trend = classify_trend(trend_pivots)
    chart_pivots = detect_pivots(candles, chart_patterns.CHARTPATTERN_PIVOT_K)
    pattern = chart_patterns.detect_chart_patterns(chart_pivots, trend, price)
    if pattern is None:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": trend, "pattern": None}

    direction = "achat" if pattern["direction"] == "haussier" else "vente"
    breakout = pattern["breakoutPrice"]
    height = pattern["patternHeight"]
    if direction == "achat":
        stop_loss = breakout - CHARTPATTERN_STOP_BUFFER
        take_profit = breakout + height
    else:
        stop_loss = breakout + CHARTPATTERN_STOP_BUFFER
        take_profit = breakout - height

    if not meets_minimum_risk_reward(price, stop_loss, take_profit, direction):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": trend, "pattern": None}

    return {"status": direction, "price": price, "entry": price, "stop_loss": stop_loss,
            "take_profit": take_profit, "trend": trend, "pattern": pattern}
