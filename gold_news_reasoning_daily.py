# gold_news_reasoning_daily.py (en-tete + fonctions de ce Task -- les
# imports reseau/API sont ajoutes au Task 2, ne pas les ajouter ici)
"""Test prospectif IA en paper-trading sur l'or (XAUUSD) -- voir
docs/superpowers/specs/2026-11-06-gold-news-reasoning-daily-paper-trading-design.md.
Execute quotidiennement via .github/workflows/gold_news_reasoning_daily.yml
(jours ouvres). Aucun lien avec gold_bot/ -- purement observationnel,
aucun compte reel, aucune execution de trade."""
from datetime import date, timedelta

MIN_STOP_PCT = 0.03
MAX_STOP_PCT = 0.15
MAX_HOLD_DAYS = 21


def normalize_direction_call(appel) -> str | None:
    """Normalise un appel directionnel textuel (haussier/leger_haussier/...,
    baissier/leger_baissier/..., neutre/neutre_prudent/...) en "achat",
    "vente", ou None (neutre, non reconnu, ou type inattendu -- aucune
    position n'est alors ouverte, voir spec #4.2)."""
    if not isinstance(appel, str):
        return None
    normalized = appel.strip().lower()
    if "neutre" in normalized:
        return None
    if "haussier" in normalized:
        return "achat"
    if "baissier" in normalized:
        return "vente"
    return None


def clamp_stop_pct(raw_pct: float) -> float:
    """Ramene une distance de stop (valeur absolue, fraction -- ex. 0.08
    pour 8%) a la fourchette [MIN_STOP_PCT, MAX_STOP_PCT]. Prend la
    valeur absolue d'abord (le modele pourrait renvoyer un signe)."""
    magnitude = abs(raw_pct)
    return max(MIN_STOP_PCT, min(MAX_STOP_PCT, magnitude))


def compute_safety_stop_price(entry_price: float, direction: str, raw_stop_pct: float) -> float:
    """raw_stop_pct est la proposition BRUTE du modele -- clampee ici,
    jamais appliquee telle quelle. Pour un achat, le stop est SOUS le
    prix d'entree ; pour une vente, AU-DESSUS."""
    magnitude = clamp_stop_pct(raw_stop_pct)
    if direction == "achat":
        return entry_price * (1 - magnitude)
    return entry_price * (1 + magnitude)


def compute_safety_stop_pct(entry_price: float, stop_price: float, direction: str) -> float:
    """Toujours une valeur NEGATIVE (perte exprimee en %), quel que soit
    le sens de la position."""
    if direction == "achat":
        return stop_price / entry_price - 1
    return entry_price / stop_price - 1


def compute_return_pct(entry_price: float, close_price: float, direction: str) -> float:
    if direction == "achat":
        return close_price / entry_price - 1
    return entry_price / close_price - 1


def compute_buyhold_return_pct(entry_price: float, market_close_price: float) -> float:
    """TOUJOURS brut (jamais ajuste par direction) -- repere passif
    "aurait-on mieux fait de juste detenir de l'or sur cette periode"."""
    return market_close_price / entry_price - 1


def compute_max_close_date(entry_date: date) -> date:
    return entry_date + timedelta(days=MAX_HOLD_DAYS)


def find_stop_breach(direction: str, stop_price: float, ohlc_rows: list[dict]) -> dict | None:
    """ohlc_rows : liste de {"date": "YYYY-MM-DD", "high": float, "low":
    float} dans N'IMPORTE QUEL ordre -- cette fonction les trie
    elle-meme par date pour garantir de renvoyer le PREMIER jour
    chronologique de franchissement, jamais le plus extreme. Renvoie la
    ligne entiere (pour que l'appelant recupere sa date), ou None si
    aucun franchissement."""
    for row in sorted(ohlc_rows, key=lambda r: r["date"]):
        if direction == "achat" and row["low"] <= stop_price:
            return row
        if direction == "vente" and row["high"] >= stop_price:
            return row
    return None
