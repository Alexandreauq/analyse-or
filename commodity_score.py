"""
Score macro-fondamental par commodité — généralisation de gold_score.py
=========================================================================

Étend le moteur de score macro-fondamental de l'or (gold_score.py) à
l'argent, au pétrole (WTI) et au gaz naturel. Réutilise par import direct
les fonctions pures et déjà commodité-agnostiques de gold_score.py
(FactorResult, fetch_fred_series, score_from_momentum, percentile_rank,
factor_taux_reels, factor_dollar, factor_inflation_anticipee,
build_macro_calendar, compute_composite) — jamais réimplémentées. Ce module
n'ajoute que ce qui est réellement spécifique à chaque commodité.

Voir docs/superpowers/specs/2026-10-25-commodites-score-design.md pour le
détail des facteurs retenus par commodité et la découverte de terrain CFTC
(dataset Legacy vs Disaggregated, renommage des contrats pétrole/gaz en 2022).
"""

import os
import sys
from datetime import datetime, timedelta

import requests

try:
    import yfinance as yf
except ImportError:
    yf = None

from gold_score import (  # noqa: F401 — réexportés pour les appelants de ce module
    FactorResult,
    fetch_fred_series,
    score_from_momentum,
    percentile_rank,
    compute_composite,
    factor_taux_reels,
    factor_dollar,
    factor_inflation_anticipee,
    build_macro_calendar,
    check_technical_trigger,
)

CFTC_LEGACY_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
CFTC_DISAGGREGATED_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"


# ---------------------------------------------------------------------------
# 1. Positionnement CFTC — générique sur les deux schémas de dataset
# ---------------------------------------------------------------------------

def fetch_cftc_net_positions(dataset_url: str, market_name: str, long_field: str,
                              short_field: str, lookback_weeks: int = 156) -> list[float]:
    """Position nette (long - short) la plus récente en premier. Généralise
    gold_score.fetch_cftc_gold_net_positions à n'importe quel dataset/marché/
    schéma de champs (Legacy : noncomm_positions_*, Disaggregated :
    m_money_positions_*)."""
    params = {
        "$where": f"market_and_exchange_names='{market_name}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": lookback_weeks,
    }
    resp = requests.get(dataset_url, params=params, timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise ValueError(
            f"Aucune donnée CFTC pour '{market_name}' (dataset {dataset_url})."
        )
    return [float(r[long_field]) - float(r[short_field]) for r in rows]


def factor_positionnement_cftc_generic(label: str, weight: float, dataset_url: str,
                                        market_name: str, long_field: str, short_field: str):
    """Même logique que gold_score.factor_positionnement_cftc (percentile 50 =
    neutre, positionnement extrême = prudence -> score négatif), paramétrée
    pour n'importe quelle commodité/dataset."""
    net_positions = fetch_cftc_net_positions(dataset_url, market_name, long_field, short_field)
    current_net = net_positions[0]
    percentile = percentile_rank(current_net, net_positions)
    score = max(-10, min(10, -((percentile - 50) / 50) * 10))
    result = FactorResult(
        label, score, weight,
        f"{percentile:.0f}e percentile (3 ans) — position nette {current_net:,.0f} contrats",
    )
    return result, percentile


# ---------------------------------------------------------------------------
# 2. Ratio or/argent — spécifique à l'argent
# ---------------------------------------------------------------------------

def _fetch_gold_silver_ratio_history(lookback_days: int = 1095):
    """Renvoie (historique des ratios or/argent sur ~3 ans, ratio actuel).
    Isolé dans sa propre fonction pour être simple à mocker dans les tests
    (un seul point d'entrée réseau, pas deux séries yfinance à aligner dans
    le test)."""
    if yf is None:
        raise RuntimeError("yfinance n'est pas installé (pip install yfinance)")
    gold = yf.Ticker("GC=F").history(period=f"{lookback_days}d")["Close"]
    silver = yf.Ticker("SI=F").history(period=f"{lookback_days}d")["Close"]
    df = gold.to_frame("gold").join(silver.to_frame("silver"), how="inner")
    ratios = (df["gold"] / df["silver"]).tolist()
    return ratios, ratios[-1]


def factor_ratio_or_argent(weight: float) -> FactorResult:
    """Un ratio or/argent historiquement haut signifie que l'argent est
    relativement sous-évalué par rapport à l'or -> favorable à l'argent
    (signal de retour à la moyenne, spécifique à l'argent)."""
    history, current = _fetch_gold_silver_ratio_history()
    percentile = percentile_rank(current, history)
    score = max(-10, min(10, ((percentile - 50) / 50) * 10))
    return FactorResult(
        "Ratio or/argent", score, weight,
        f"{current:.1f} — {percentile:.0f}e percentile (3 ans)",
    )


# ---------------------------------------------------------------------------
# 3. Spread WTI-Brent — spécifique au pétrole
# ---------------------------------------------------------------------------

def _fetch_wti_brent_spread_series(lookback_days: int = 60):
    """Renvoie (spread actuel, spread il y a ~2 mois), spread = Brent - WTI
    (en $/baril, normalement positif — Brent cote en général au-dessus du WTI)."""
    if yf is None:
        raise RuntimeError("yfinance n'est pas installé (pip install yfinance)")
    wti = yf.Ticker("CL=F").history(period=f"{lookback_days}d")["Close"]
    brent = yf.Ticker("BZ=F").history(period=f"{lookback_days}d")["Close"]
    df = wti.to_frame("wti").join(brent.to_frame("brent"), how="inner")
    spread = df["brent"] - df["wti"]
    return float(spread.iloc[-1]), float(spread.iloc[0])


def factor_spread_wti_brent(weight: float) -> FactorResult:
    """Un resserrement du spread (Brent se rapproche du WTI) est favorable
    au WTI (offre US relativement moins abondante relativement au marché
    mondial) ; un écartement est défavorable."""
    current, past = _fetch_wti_brent_spread_series()
    score = score_from_momentum(past, current, scale=2.0)
    return FactorResult(
        "Spread WTI-Brent", score, weight,
        f"{current:+.2f} $/baril (il y a ~2 mois : {past:+.2f} $/baril)",
    )


# ---------------------------------------------------------------------------
# 4. Saisonnalité — spécifique au gaz naturel
# ---------------------------------------------------------------------------

def _fetch_gaz_seasonal_deviation(api_key: str, lookback_days: int = 1825):
    """Renvoie (prix actuel Henry Hub, moyenne historique du même mois
    calendaire sur les années précédentes disponibles) à partir de la série
    FRED DHHNGSP. Isolé dans sa propre fonction pour rester simple à mocker."""
    obs = fetch_fred_series("DHHNGSP", api_key, lookback_days=lookback_days)
    by_month = {}
    for o in obs:
        month = int(o["date"].split("-")[1])
        by_month.setdefault(month, []).append(float(o["value"]))
    current_price = float(obs[-1]["value"])
    current_month = int(obs[-1]["date"].split("-")[1])
    history_this_month = by_month.get(current_month, [current_price])
    seasonal_average = sum(history_this_month) / len(history_this_month)
    return current_price, seasonal_average


def factor_saisonnalite_gaz(api_key: str, weight: float) -> FactorResult:
    """Un prix au-dessus de sa moyenne saisonnière historique (même mois,
    années précédentes) est favorable (tension sur la demande/l'offre pour
    la saison) ; en dessous, défavorable."""
    current, seasonal_average = _fetch_gaz_seasonal_deviation(api_key)
    score = score_from_momentum(current, seasonal_average, scale=0.5)
    return FactorResult(
        "Saisonnalité (Henry Hub)", score, weight,
        f"{current:.2f} $/MMBtu (moyenne saisonnière du mois : {seasonal_average:.2f} $/MMBtu)",
    )


# ---------------------------------------------------------------------------
# 5. Renormalisation des poids (facteur EIA en placeholder neutre v1)
# ---------------------------------------------------------------------------

def renormalize_weights(factors: list[FactorResult]) -> list[FactorResult]:
    """Fait sommer à 1.0 les poids des facteurs dont le poids est non nul
    (les facteurs placeholder, poids 0, ne sont jamais gonflés et restent
    à 0). No-op si les poids non nuls somment déjà à 1.0."""
    active_weight = sum(f.weight for f in factors if f.weight > 0)
    if active_weight == 0 or abs(active_weight - 1.0) < 1e-9:
        return factors
    scale = 1.0 / active_weight
    return [
        FactorResult(f.name, f.score, f.weight * scale if f.weight > 0 else 0.0, f.raw_value)
        for f in factors
    ]


def factor_dynamique_recente(spot: float | None, ma200: float | None, weight: float) -> FactorResult:
    """Momentum générique prix vs MM200, en pourcentage d'écart plutôt qu'en
    valeur absolue (contrairement à gold_score.score_from_momentum appelé
    avec une échelle en $, ceci reste comparable entre commodités dont les
    prix n'ont pas le même ordre de grandeur — or ~2600$, argent ~30$,
    pétrole ~70$, gaz ~3$). Un écart de 5% au-dessus/en-dessous de la MM200
    sature le score à +-10."""
    if spot is None or ma200 is None or ma200 == 0:
        return FactorResult("Dynamique récente", 0.0, weight, "repère technique indisponible")
    pct_ecart = (spot - ma200) / ma200 * 100
    score = max(-10.0, min(10.0, (pct_ecart / 5.0) * 10.0))
    return FactorResult("Dynamique récente", score, weight, f"{pct_ecart:+.1f}% vs MM200")
