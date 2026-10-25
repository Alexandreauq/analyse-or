# Commodités — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Généraliser le score macro-fondamental de l'or (`gold_score.py`) à 3 nouvelles commodités (argent, pétrole WTI, gaz naturel) via un nouveau module `commodity_score.py`, et remplacer l'onglet "Or" du site par un onglet "Commodités" avec sous-navigation par commodité, en retirant l'onglet Scalping et l'onglet Suivi (devenus inutiles, le bot `gold_bot/` restant intact et inchangé).

**Architecture:** `commodity_score.py` (nouveau, racine du repo) importe directement les fonctions pures et déjà commodité-agnostiques de `gold_score.py` (`FactorResult`, `fetch_fred_series`, `score_from_momentum`, `percentile_rank`, `compute_composite`, `factor_taux_reels`, `factor_dollar`, `factor_inflation_anticipee`, `build_macro_calendar`, `get_gold_spot_and_ma200`-équivalent générique, `check_technical_trigger`) et ajoute les éléments réellement spécifiques à chaque commodité (positionnement CFTC sur 2 schémas de dataset différents, ratio or/argent, spread WTI-Brent, saisonnalité gaz) via une config par commodité. Écrit un JSON par commodité au même format que `score.json`. Le frontend (`docs/index.html`) réutilise `render(data)`/`renderFactorRows` tels quels, paramétrés par une petite config de sous-navigation.

**Tech Stack:** Python 3, pytest, `requests`, `yfinance` (déjà des dépendances, aucune nouvelle à ajouter), JS vanilla (`docs/index.html`).

**Spec:** `docs/superpowers/specs/2026-10-25-commodites-score-design.md`

## Global Constraints

- **Ne jamais modifier `gold_score.py`** (sauf si un bug bloquant y est découvert pendant l'implémentation — dans ce cas, s'arrêter et documenter plutôt que de corriger silencieusement). Il est importé, pas dupliqué ni réécrit.
- **Ne jamais toucher à `gold_bot/`, `deploy/gold-bot-*.service`, ni aux tests associés.** Le bot reste intact, éteint mais non supprimé.
- **Ne jamais toucher à `#portefeuilleTabOr`** (onglet Or du Portefeuille, fonctionnalité séparée) ni au suivi de performance des signaux "entrée" de l'onglet Indices (fonctionnalité séparée).
- Noms de marché CFTC et dataset EXACTS, vérifiés en direct (ne pas utiliser d'autres noms, même plausibles) :
  - Argent : dataset `https://publicreporting.cftc.gov/resource/6dca-aqww.json` (Legacy), `market_and_exchange_names = "SILVER - COMMODITY EXCHANGE INC."`, champs `noncomm_positions_long_all`/`noncomm_positions_short_all`.
  - Pétrole : dataset `https://publicreporting.cftc.gov/resource/72hh-3qpy.json` (Disaggregated), `market_and_exchange_names = "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE"`, champs `m_money_positions_long_all`/`m_money_positions_short_all`.
  - Gaz naturel : même dataset Disaggregated, `market_and_exchange_names = "NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE"`, mêmes champs `m_money_positions_long_all`/`m_money_positions_short_all`.
- Tickers yfinance : argent `SI=F`, or (déjà existant) `GC=F`, pétrole WTI `CL=F`, Brent `BZ=F`, gaz naturel `NG=F`.
- Série FRED pour la saisonnalité gaz : `DHHNGSP` (vérifiée active, données récentes).
- Le facteur "stocks EIA" (pétrole + gaz) est un placeholder neutre en v1 (score 0, `raw_value` explicite) avec renormalisation des autres poids à 100% — jamais un calcul bloquant ni une exception qui ferait échouer tout le run.
- Pas d'email pour argent/pétrole/gaz en v1 — uniquement écriture JSON + historique.
- Tests : toute fonction de calcul pur (scoring, renormalisation, ratio, spread, saisonnalité) doit avoir un test unitaire avec des données injectées (pas d'appel réseau dans les tests) — TDD, écrire le test avant le code à chaque étape.

---

### Task 1 : `commodity_score.py` — infrastructure CFTC générique + facteurs spécifiques

**Files:**
- Create: `commodity_score.py`
- Test: `tests/test_commodity_score.py`

**Interfaces:**
- Consumes (import direct depuis `gold_score.py`, existant, ne pas modifier) : `FactorResult`, `fetch_fred_series(series_id, api_key, lookback_days=45)`, `score_from_momentum(current, past, scale) -> float`, `percentile_rank(value, history) -> float`, `factor_taux_reels(api_key) -> FactorResult`, `factor_dollar() -> FactorResult`, `factor_inflation_anticipee(api_key) -> FactorResult`.
- Produces : `fetch_cftc_net_positions(dataset_url, market_name, long_field, short_field, lookback_weeks=156) -> list[float]`, `factor_positionnement_cftc_generic(label, weight, dataset_url, market_name, long_field, short_field) -> tuple[FactorResult, float]`, `factor_ratio_or_argent(weight) -> FactorResult`, `factor_spread_wti_brent(weight) -> FactorResult`, `factor_saisonnalite_gaz(api_key, weight) -> FactorResult`, `factor_dynamique_recente(spot, ma200, weight) -> FactorResult`, `renormalize_weights(factors: list[FactorResult]) -> list[FactorResult]` (tous consommés par la Tâche 2).

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `tests/test_commodity_score.py` :

```python
import pytest
from unittest.mock import patch, MagicMock

from commodity_score import (
    fetch_cftc_net_positions,
    factor_positionnement_cftc_generic,
    factor_ratio_or_argent,
    factor_spread_wti_brent,
    factor_saisonnalite_gaz,
    renormalize_weights,
)
from gold_score import FactorResult


def _fake_response(json_data, status_ok=True):
    resp = MagicMock()
    resp.json.return_value = json_data
    if not status_ok:
        resp.raise_for_status.side_effect = Exception("HTTP error")
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_fetch_cftc_net_positions_computes_long_minus_short_most_recent_first():
    rows = [
        {"m_money_positions_long_all": "100", "m_money_positions_short_all": "40"},
        {"m_money_positions_long_all": "90", "m_money_positions_short_all": "50"},
    ]
    with patch("commodity_score.requests.get", return_value=_fake_response(rows)) as mock_get:
        result = fetch_cftc_net_positions(
            "https://example.test/resource.json", "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
            "m_money_positions_long_all", "m_money_positions_short_all",
        )
    assert result == [60.0, 40.0]
    called_params = mock_get.call_args.kwargs["params"]
    assert called_params["$where"] == "market_and_exchange_names='WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE'"


def test_fetch_cftc_net_positions_raises_on_empty_rows():
    with patch("commodity_score.requests.get", return_value=_fake_response([])):
        with pytest.raises(ValueError):
            fetch_cftc_net_positions("https://example.test/resource.json", "X", "a", "b")


def test_factor_positionnement_cftc_generic_extreme_percentile_gives_negative_score():
    # Position nette actuelle = la plus haute de l'historique -> percentile 100 -> score -10
    history = [100.0, 10.0, 20.0, 30.0, 40.0]
    with patch("commodity_score.fetch_cftc_net_positions", return_value=history):
        result, percentile = factor_positionnement_cftc_generic(
            "Positionnement CFTC", 0.20, "https://x", "MARKET", "long", "short",
        )
    assert percentile == 100.0
    assert result.score == -10.0
    assert result.weight == 0.20
    assert result.name == "Positionnement CFTC"


def test_factor_ratio_or_argent_high_ratio_is_favorable_to_silver():
    # Ratio actuel au plus haut de l'historique -> percentile 100 -> favorable à l'argent -> score +10
    with patch("commodity_score._fetch_gold_silver_ratio_history", return_value=([100.0, 50.0, 60.0, 70.0], 100.0)):
        result = factor_ratio_or_argent(0.25)
    assert result.score == 10.0
    assert result.weight == 0.25
    assert "percentile" in result.raw_value.lower() or "ratio" in result.raw_value.lower()


def test_factor_spread_wti_brent_narrowing_spread_is_favorable():
    # Spread actuel plus petit que le spread passé -> resserrement -> favorable au WTI -> score positif
    with patch("commodity_score._fetch_wti_brent_spread_series", return_value=(2.0, 6.0)):
        result = factor_spread_wti_brent(0.15)
    assert result.score > 0
    assert result.weight == 0.15


def test_factor_saisonnalite_gaz_above_seasonal_average_is_favorable():
    with patch("commodity_score._fetch_gaz_seasonal_deviation", return_value=(3.80, 3.20)):
        result = factor_saisonnalite_gaz("fake-key", 0.25)
    assert result.score > 0
    assert result.weight == 0.25


def test_factor_saisonnalite_gaz_below_seasonal_average_is_unfavorable():
    with patch("commodity_score._fetch_gaz_seasonal_deviation", return_value=(2.50, 3.20)):
        result = factor_saisonnalite_gaz("fake-key", 0.25)
    assert result.score < 0


def test_renormalize_weights_scales_up_when_a_factor_is_missing():
    factors = [
        FactorResult("A", 5.0, 0.20, "a"),
        FactorResult("B", -2.0, 0.20, "b"),
        FactorResult("C", 0.0, 0.0, "placeholder, poids retiré"),
    ]
    out = renormalize_weights(factors)
    # Les poids non nuls doivent sommer à 1.0 après renormalisation ; le facteur
    # à poids nul (placeholder) reste à 0 et n'est jamais gonflé.
    assert sum(f.weight for f in out) == pytest.approx(1.0)
    assert out[2].weight == 0.0
    assert out[0].weight == pytest.approx(0.5)
    assert out[1].weight == pytest.approx(0.5)


def test_renormalize_weights_is_a_noop_when_nothing_missing():
    factors = [FactorResult("A", 1.0, 0.6, "a"), FactorResult("B", 1.0, 0.4, "b")]
    out = renormalize_weights(factors)
    assert out[0].weight == 0.6
    assert out[1].weight == 0.4


def test_factor_dynamique_recente_above_ma200_is_favorable():
    from commodity_score import factor_dynamique_recente
    result = factor_dynamique_recente(spot=105.0, ma200=100.0, weight=0.20)
    assert result.score > 0
    assert result.weight == 0.20


def test_factor_dynamique_recente_below_ma200_is_unfavorable():
    from commodity_score import factor_dynamique_recente
    result = factor_dynamique_recente(spot=95.0, ma200=100.0, weight=0.20)
    assert result.score < 0


def test_factor_dynamique_recente_clamps_at_plus_minus_10():
    from commodity_score import factor_dynamique_recente
    result = factor_dynamique_recente(spot=200.0, ma200=100.0, weight=0.20)
    assert result.score == 10.0


def test_factor_dynamique_recente_neutral_when_technical_data_missing():
    from commodity_score import factor_dynamique_recente
    result = factor_dynamique_recente(spot=None, ma200=None, weight=0.20)
    assert result.score == 0.0
    assert result.weight == 0.20
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

Run: `python3 -m pytest tests/test_commodity_score.py -v`
Expected: `ModuleNotFoundError: No module named 'commodity_score'` (ou équivalent — le fichier n'existe pas encore).

- [ ] **Step 3 : Créer `commodity_score.py`**

```python
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
```

Ajouter aussi, dans la même section 5 de `commodity_score.py` (après
`renormalize_weights`) :

```python
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
```

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

Run: `python3 -m pytest tests/test_commodity_score.py -v`
Expected: 13 tests PASS.

- [ ] **Step 5 : Commit**

```bash
git add commodity_score.py tests/test_commodity_score.py
git commit -m "feat(commodites): infrastructure CFTC generique + facteurs argent/petrole/gaz"
```

---

### Task 2 : `commodity_score.py` — config par commodité, orchestration, I/O, CLI

**Files:**
- Modify: `commodity_score.py`
- Test: `tests/test_commodity_score.py`

**Interfaces:**
- Consumes : tout ce que produit la Tâche 1, plus `gold_score.get_gold_spot_and_ma200` (comme référence de pattern, NON réutilisée telle quelle — généralisée ci-dessous en `get_spot_and_ma200(ticker)`).
- Produces : `get_spot_and_ma200(ticker: str) -> tuple[float, float]`, `COMMODITY_CONFIGS: dict`, `build_factors(commodity_key, api_key, *, spot=None, ma200=None) -> tuple[list[FactorResult], float | None]`, `run_commodity(commodity_key, *, api_key=None, daily_snapshot=None) -> dict` (le payload JSON écrit), `main(argv=None)` (CLI, lit `sys.argv[1]` = clé commodité).

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_commodity_score.py` :

```python
import json

from commodity_score import (
    get_spot_and_ma200,
    COMMODITY_CONFIGS,
    build_factors,
    run_commodity,
)


def test_commodity_configs_cover_argent_petrole_gaz():
    assert set(COMMODITY_CONFIGS.keys()) == {"argent", "petrole", "gaz"}
    for key, cfg in COMMODITY_CONFIGS.items():
        assert "ticker" in cfg and "unite" in cfg and "label" in cfg


def test_get_spot_and_ma200_uses_given_ticker():
    hist_mock = MagicMock()
    hist_mock.tail.return_value.mean.return_value = 50.0
    hist_mock.iloc = [None] * 199 + [55.0]
    with patch("commodity_score.yf") as mock_yf:
        mock_yf.Ticker.return_value.history.return_value = {"Close": hist_mock}
        spot, ma200 = get_spot_and_ma200("SI=F")
        mock_yf.Ticker.assert_called_with("SI=F")


def test_build_factors_argent_includes_ratio_or_argent_and_no_eia_placeholder():
    with patch("commodity_score.factor_taux_reels", return_value=FactorResult("Taux réels US 10 ans", 1.0, 0.30, "x")), \
         patch("commodity_score.factor_dollar", return_value=FactorResult("Dollar (DXY)", 1.0, 0.20, "x")), \
         patch("commodity_score.factor_positionnement_cftc_generic", return_value=(FactorResult("Positionnement CFTC", 1.0, 0.15, "x"), 50.0)), \
         patch("commodity_score.factor_ratio_or_argent", return_value=FactorResult("Ratio or/argent", 1.0, 0.25, "x")):
        factors, cftc_percentile = build_factors("argent", api_key="fake-key", spot=105.0, ma200=100.0)
    names = [f.name for f in factors]
    assert "Ratio or/argent" in names
    assert "Dynamique récente" in names
    assert "Stocks EIA" not in names
    assert cftc_percentile == 50.0
    dyn = next(f for f in factors if f.name == "Dynamique récente")
    assert dyn.score > 0  # spot > ma200 ci-dessus


def test_build_factors_petrole_includes_eia_placeholder_renormalized():
    with patch("commodity_score.factor_dollar", return_value=FactorResult("Dollar (DXY)", 1.0, 0.20, "x")), \
         patch("commodity_score.factor_positionnement_cftc_generic", return_value=(FactorResult("Positionnement CFTC", 1.0, 0.20, "x"), 50.0)), \
         patch("commodity_score.factor_spread_wti_brent", return_value=FactorResult("Spread WTI-Brent", 1.0, 0.15, "x")):
        factors, _ = build_factors("petrole", api_key="fake-key", spot=None, ma200=None)
    eia_factor = next(f for f in factors if f.name == "Stocks EIA")
    assert eia_factor.score == 0.0
    assert eia_factor.weight == 0.0
    assert "indisponible" in eia_factor.raw_value.lower()
    dyn = next(f for f in factors if f.name == "Dynamique récente")
    assert dyn.score == 0.0  # spot/ma200 absents ici -> neutre, pas une erreur
    assert sum(f.weight for f in factors) == pytest.approx(1.0)


def test_run_commodity_writes_json_payload_with_expected_shape(tmp_path, monkeypatch):
    monkeypatch.setattr("commodity_score.OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr("commodity_score.DOCS_DIR", str(tmp_path / "docs"))
    fake_factors = [FactorResult("Dollar (DXY)", 2.0, 1.0, "x")]
    with patch("commodity_score.build_factors", return_value=(fake_factors, 42.0)), \
         patch("commodity_score.get_spot_and_ma200", return_value=(100.0, 95.0)), \
         patch("commodity_score.build_macro_calendar", return_value=[]), \
         patch("commodity_score.os.environ.get", return_value="fake-key"):
        payload = run_commodity("argent", api_key="fake-key", daily_snapshot=False)
    assert payload["composite_score"] == pytest.approx(20.0)
    assert payload["factors"][0]["name"] == "Dollar (DXY)"
    assert payload["technical"]["spot"] == 100.0
    assert payload["technical"]["unite"] == "$ / oz"
    assert "interpretation" in payload


def test_run_commodity_passes_spot_ma200_into_build_factors(tmp_path, monkeypatch):
    monkeypatch.setattr("commodity_score.OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr("commodity_score.DOCS_DIR", str(tmp_path / "docs"))
    fake_factors = [FactorResult("X", 1.0, 1.0, "x")]
    with patch("commodity_score.build_factors", return_value=(fake_factors, None)) as mock_build, \
         patch("commodity_score.get_spot_and_ma200", return_value=(70.0, 65.0)), \
         patch("commodity_score.build_macro_calendar", return_value=[]):
        run_commodity("petrole", api_key="fake-key", daily_snapshot=False)
    mock_build.assert_called_once_with("petrole", "fake-key", spot=70.0, ma200=65.0)


def test_run_commodity_passes_none_spot_ma200_when_technical_fetch_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("commodity_score.OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr("commodity_score.DOCS_DIR", str(tmp_path / "docs"))
    fake_factors = [FactorResult("X", 1.0, 1.0, "x")]
    with patch("commodity_score.build_factors", return_value=(fake_factors, None)) as mock_build, \
         patch("commodity_score.get_spot_and_ma200", side_effect=RuntimeError("indisponible")), \
         patch("commodity_score.build_macro_calendar", return_value=[]):
        run_commodity("petrole", api_key="fake-key", daily_snapshot=False)
    mock_build.assert_called_once_with("petrole", "fake-key", spot=None, ma200=None)
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

Run: `python3 -m pytest tests/test_commodity_score.py -v`
Expected: `ImportError` / `AttributeError` sur les nouveaux symboles (pas encore définis).

- [ ] **Step 3 : Ajouter à `commodity_score.py`**

Ajouter en haut du fichier, à côté des imports existants : `import json` (si pas déjà présent), et à la fin du fichier (après la section 5 déjà écrite en Tâche 1) :

```python
# ---------------------------------------------------------------------------
# 6. Technique : spot + MM200, générique par ticker
# ---------------------------------------------------------------------------

def get_spot_and_ma200(ticker: str) -> tuple[float, float]:
    if yf is None:
        raise RuntimeError("yfinance n'est pas installé (pip install yfinance)")
    hist = yf.Ticker(ticker).history(period="1y")["Close"]
    spot = hist.iloc[-1]
    ma200 = hist.tail(200).mean()
    return spot, ma200


# ---------------------------------------------------------------------------
# 7. Config par commodité
# ---------------------------------------------------------------------------

COMMODITY_CONFIGS = {
    "argent": {
        "label": "Argent",
        "ticker": "SI=F",
        "unite": "$ / oz",
    },
    "petrole": {
        "label": "Pétrole (WTI)",
        "ticker": "CL=F",
        "unite": "$ / baril",
    },
    "gaz": {
        "label": "Gaz naturel",
        "ticker": "NG=F",
        "unite": "$ / MMBtu",
    },
}

_EIA_PLACEHOLDER = FactorResult(
    "Stocks EIA", 0.0, 0.0,
    "indisponible — nécessite une clé API EIA (gratuite sur eia.gov), pas encore configurée",
)


def build_factors(commodity_key: str, api_key: str, *, spot: float | None = None,
                   ma200: float | None = None) -> tuple[list[FactorResult], float | None]:
    """Construit la liste de facteurs (déjà renormalisée) et le percentile
    CFTC (pour la logique d'alerte) pour une commodité donnée. spot/ma200
    sont fournis par l'appelant (déjà récupérés avant, voir run_commodity)
    pour alimenter le facteur "Dynamique récente" — None si le repère
    technique n'a pas pu être récupéré (le facteur devient alors neutre,
    jamais une exception)."""
    if commodity_key == "argent":
        cftc_factor, cftc_percentile = factor_positionnement_cftc_generic(
            "Positionnement CFTC", 0.15, CFTC_LEGACY_URL,
            "SILVER - COMMODITY EXCHANGE INC.",
            "noncomm_positions_long_all", "noncomm_positions_short_all",
        )
        factors = [
            factor_taux_reels(api_key),
            factor_dollar(),
            cftc_factor,
            factor_ratio_or_argent(0.25),
            factor_dynamique_recente(spot, ma200, 0.10),
        ]
        return renormalize_weights(factors), cftc_percentile

    if commodity_key == "petrole":
        cftc_factor, cftc_percentile = factor_positionnement_cftc_generic(
            "Positionnement CFTC", 0.20, CFTC_DISAGGREGATED_URL,
            "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
            "m_money_positions_long_all", "m_money_positions_short_all",
        )
        factors = [
            factor_dollar(),
            cftc_factor,
            factor_spread_wti_brent(0.15),
            factor_dynamique_recente(spot, ma200, 0.20),
            _EIA_PLACEHOLDER,
        ]
        return renormalize_weights(factors), cftc_percentile

    if commodity_key == "gaz":
        cftc_factor, cftc_percentile = factor_positionnement_cftc_generic(
            "Positionnement CFTC", 0.20, CFTC_DISAGGREGATED_URL,
            "NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE",
            "m_money_positions_long_all", "m_money_positions_short_all",
        )
        dollar_factor = factor_dollar()
        factors = [
            cftc_factor,
            factor_saisonnalite_gaz(api_key, 0.25),
            factor_dynamique_recente(spot, ma200, 0.20),
            # factor_dollar() renvoie un poids fixe (0.15, celui de l'or) ;
            # on force ici le poids voulu pour le gaz (0.10) avant renormalisation.
            FactorResult(dollar_factor.name, dollar_factor.score, 0.10, dollar_factor.raw_value),
            _EIA_PLACEHOLDER,
        ]
        return renormalize_weights(factors), cftc_percentile

    raise ValueError(f"Commodité inconnue : {commodity_key!r} (attendu : {list(COMMODITY_CONFIGS)})")


# ---------------------------------------------------------------------------
# 8. Orchestration, I/O, CLI — même pattern que gold_score.main()
# ---------------------------------------------------------------------------

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(OUTPUT_DIR, "docs")


def _history_path(commodity_key: str) -> str:
    return os.path.join(OUTPUT_DIR, f"score_history_{commodity_key}.json")


def load_history(commodity_key: str) -> list:
    path = _history_path(commodity_key)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError:
            return []


def append_history(commodity_key: str, entry: dict, max_entries: int = 730) -> list:
    history = load_history(commodity_key)
    history.append(entry)
    history = history[-max_entries:]
    with open(_history_path(commodity_key), "w", encoding="utf-8") as fh:
        json.dump(history, fh, ensure_ascii=False, indent=2)
    return history


def run_commodity(commodity_key: str, *, api_key: str | None = None, daily_snapshot: bool | None = None) -> dict:
    api_key = api_key or os.environ.get("FRED_API_KEY")
    if not api_key:
        raise RuntimeError("FRED_API_KEY absent (variable d'environnement ou paramètre api_key).")
    if daily_snapshot is None:
        daily_snapshot = os.environ.get("DAILY_SNAPSHOT", "true").lower() != "false"

    cfg = COMMODITY_CONFIGS[commodity_key]

    # Le repère technique (spot/MM200) est récupéré AVANT build_factors, pour
    # alimenter le facteur "Dynamique récente" avec de vraies valeurs plutôt
    # qu'un placeholder à brancher après coup — spot/ma200 restent None (et
    # le facteur devient neutre, jamais une exception) si ce fetch échoue.
    spot = ma200 = None
    technical_note = "Repère technique indisponible"
    try:
        spot, ma200 = get_spot_and_ma200(cfg["ticker"])
        technical_note = check_technical_trigger(spot, ma200)
    except Exception as e:
        technical_note = f"Repère technique indisponible : {e}"

    factors, cftc_percentile = build_factors(commodity_key, api_key, spot=spot, ma200=ma200)
    composite = compute_composite(factors)

    calendar = build_macro_calendar(api_key)

    from gold_score import interpret  # import tardif : pure fonction, pas de cycle
    payload = {
        "date": datetime.today().strftime("%Y-%m-%d"),
        "commodity": commodity_key,
        "label": cfg["label"],
        "composite_score": composite,
        "interpretation": interpret(composite),
        "cftc_percentile": cftc_percentile,
        "factors": [
            {"name": f.name, "score": f.score, "weight": f.weight, "raw_value": f.raw_value}
            for f in factors
        ],
        "technical": {"note": technical_note, "spot": spot, "ma200": ma200, "unite": cfg["unite"]},
        "calendar": calendar,
        "alerts": [],
    }

    if daily_snapshot:
        append_history(commodity_key, {
            "date": payload["date"], "composite": composite, "cftc_percentile": cftc_percentile,
        })

    root_path = os.path.join(OUTPUT_DIR, f"score_{commodity_key}.json")
    with open(root_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    os.makedirs(DOCS_DIR, exist_ok=True)
    docs_path = os.path.join(DOCS_DIR, f"score_{commodity_key}.json")
    with open(docs_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    return payload


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] not in COMMODITY_CONFIGS:
        print(f"Usage : python3 commodity_score.py <{'|'.join(COMMODITY_CONFIGS)}>")
        sys.exit(1)
    commodity_key = argv[0]
    payload = run_commodity(commodity_key)
    print(f"{COMMODITY_CONFIGS[commodity_key]['label']} — composite {payload['composite_score']:+.1f} — {payload['interpretation']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

Run: `python3 -m pytest tests/test_commodity_score.py -v`
Expected: tous les tests PASS (17 tests au total dans le fichier après
cette tâche).

- [ ] **Step 5 : Vérification manuelle avec de vraies données (une seule fois, pas en CI)**

Run: `FRED_API_KEY=<ta clé> python3 commodity_score.py argent` (puis `petrole`, puis `gaz`)
Expected: pas d'exception, un score composite affiché, `score_argent.json`/`docs/score_argent.json` (etc.) créés avec un contenu plausible (poids qui somment à 1.0, pas de `NaN`).

- [ ] **Step 6 : Commit**

```bash
git add commodity_score.py tests/test_commodity_score.py
git commit -m "feat(commodites): config par commodite, orchestration et CLI commodity_score.py"
```

---

### Task 3 : Workflow GitHub Actions

**Files:**
- Modify: `.github/workflows/daily.yml`

**Interfaces:**
- Consumes : `commodity_score.py` (Tâches 1-2), secret GH Actions `FRED_API_KEY` (déjà configuré).

- [ ] **Step 1 : Ajouter les 3 nouveaux appels**

Dans `.github/workflows/daily.yml`, juste après l'étape existante `Calculer
le score et envoyer l'email` (qui lance `gold_score.py`), ajouter :

```yaml
      - name: Calculer le score des autres commodités (argent, pétrole, gaz)
        run: |
          python3 commodity_score.py argent
          python3 commodity_score.py petrole
          python3 commodity_score.py gaz
        env:
          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}
          DAILY_SNAPSHOT: ${{ steps.daily_slot.outputs.is_daily }}
```

Puis étendre l'étape `Publier les données mises à jour sur le site` pour
inclure les nouveaux fichiers dans le `git add` :

```yaml
          git add docs/score.json score.json score_history.json dashboard.html \
                  docs/score_argent.json score_argent.json score_history_argent.json \
                  docs/score_petrole.json score_petrole.json score_history_petrole.json \
                  docs/score_gaz.json score_gaz.json score_history_gaz.json
```

(remplace la ligne `git add` existante par celle-ci, le reste de l'étape —
`git diff --staged --quiet || git commit`, `git pull --rebase --autostash`,
`git push` — ne change pas).

- [ ] **Step 2 : Vérifier la syntaxe YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/daily.yml', encoding='utf-8'))"`
Expected: pas d'exception.

- [ ] **Step 3 : Commit**

```bash
git add .github/workflows/daily.yml
git commit -m "feat(commodites): calcule argent/petrole/gaz dans le workflow horaire"
```

---

### Task 4 : Frontend — retrait de l'onglet Scalping et de l'onglet Suivi

**Files:**
- Modify: `docs/index.html`
- Delete (si plus aucune référence après ce retrait, vérifier par grep avant de supprimer) : `docs/scalping.js`

**Interfaces:**
- Ne modifie aucune interface consommée par d'autres tâches de ce plan — purement soustractif.

- [ ] **Step 1 : Localiser précisément tout ce qui doit disparaître**

Avant de modifier quoi que ce soit, lancer ces recherches dans
`docs/index.html` pour repérer l'état actuel exact (les numéros de ligne
donnés ci-dessous datent du 2026-10-25 et auront dérivé si une tâche
précédente a déjà édité le fichier — **relocalise avec ces recherches,
ne te fie pas aux numéros de ligne**) :

```bash
grep -n "orTabScalping\|orTabSuivi\|orScalpingView\|orSuiviView\|scalpTrackingPanel\|suivi-active\|loadScalpingModule\|loadScalpChart\|loadScalpTracking\|startScalpingPolling\|stopScalpingPolling\|scalping.js\|chart_patterns.js" docs/index.html
```

Ce retrait touche, dans `docs/index.html` :
1. Le CSS `#or.suivi-active` et toutes ses sous-règles (bloc autour de la
   ligne 907-931 au 2026-10-25).
2. Le markup des boutons `#orTabScalping` et `#orTabSuivi` dans
   `.or-tabbar` (garder uniquement `#orTabLongTerme` — qui sera lui-même
   remplacé par la sous-navigation Commodités dans la Tâche 5, donc ne pas
   sur-travailler ici : retire juste les deux boutons en trop pour l'instant).
3. Les blocs `<div id="orScalpingView" hidden>...</div>` et
   `<div id="orSuiviView" hidden>...</div>` en entier (markup HTML).
4. Le chargement du script `<script src="scalping.js">` ou équivalent
   lazy-load, les fonctions `loadScalpChart()`, `loadScalpingModule()`,
   `loadScalpTracking()`, `startScalpingPolling()`, `stopScalpingPolling()`
   et leurs variables d'état (`scalpingJsLoaded`, etc.).
5. Les 3 `addEventListener('click', ...)` sur `orTabLongTerme`/
   `orTabScalping`/`orTabSuivi` (lignes ~6489-6522 au 2026-10-25) — **ne pas
   les supprimer entièrement ici**, la Tâche 5 les remplace par la
   sous-navigation Commodités ; pour cette tâche, simplifie-les pour ne
   garder que la logique qui affiche `#content` (retire tout ce qui
   référence `orScalpingView`/`orSuiviView`/`suivi-active`/
   `stopScalpingPolling`/`loadScalpTracking`).
6. Les références à `route.screen === 'or'` qui mentionnent
   `scalpingJsLoaded`/`orScalpingView` dans la logique de routing (ligne
   ~6442-6444 au 2026-10-25) — à retirer (ce cas ne peut plus se produire).
7. **Ne pas toucher** à `#portefeuilleTabOr` ni à aucun autre usage de
   "Or"/"scalp" lié au Portefeuille — vérifie par le contexte (section
   `id="portefeuille"`) avant de supprimer quoi que ce soit.

- [ ] **Step 2 : Appliquer les retraits**

Utilise `grep -n` / lecture ciblée pour chaque zone listée ci-dessus,
retire-les une par une avec Edit (jamais une réécriture globale du
fichier — trop gros, trop risqué).

- [ ] **Step 3 : Vérifier qu'aucune référence orpheline ne reste**

Run:
```bash
grep -n "orTabScalping\|orTabSuivi\|orScalpingView\|orSuiviView\|scalpTrackingPanel\|suivi-active\|loadScalpingModule\|loadScalpChart\|loadScalpTracking\|startScalpingPolling\|stopScalpingPolling" docs/index.html
```
Expected: aucun résultat.

```bash
grep -rn "scalping.js\|chart_patterns.js" docs/index.html docs/*.js 2>/dev/null
```
Si aucun résultat et que `docs/scalping.js` (et `docs/chart_patterns.js` s'il
n'est utilisé que par le module scalping — vérifier par grep avant) ne sont
plus référencés nulle part, les supprimer :
```bash
git rm docs/scalping.js
# git rm docs/chart_patterns.js   # seulement si confirmé orphelin par le grep ci-dessus
```

- [ ] **Step 4 : Vérifier la syntaxe des scripts inline**

Run (pattern déjà utilisé dans ce projet pour valider `docs/index.html` après édition, voir historique du projet) :
```bash
node -e "
const fs = require('fs');
const html = fs.readFileSync('docs/index.html', 'utf-8');
const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/g;
let m, i=0, ok=true;
while ((m = re.exec(html))) {
  if (/\bsrc=/.test(m[1])) continue;
  i++;
  try { new Function(m[2]); } catch (e) { ok = false; console.log('Erreur script #' + i + ':', e.message); }
}
console.log(ok ? 'OK (' + i + ' scripts verifies)' : 'ERREURS');
"
```
Expected: `OK (...)`.

- [ ] **Step 5 : Commit**

```bash
git add docs/index.html
git status --porcelain docs/scalping.js   # confirme s'il a été supprimé ou gardé, selon Step 3
git commit -m "feat(commodites): retire les onglets Scalping et Suivi de l'ecran Or"
```

---

### Task 5 : Frontend — onglet "Commodités" avec sous-navigation Or/Argent/Pétrole/Gaz

**Files:**
- Modify: `docs/index.html`

**Interfaces:**
- Consumes : `score.json`, `docs/score_argent.json`, `docs/score_petrole.json`, `docs/score_gaz.json` (Tâche 2 — même forme que `score.json`, avec en plus `technical.unite`).
- Produces : aucune interface consommée par une autre tâche de ce plan (dernière tâche frontend).

- [ ] **Step 1 : Renommer l'écran et sa navigation**

Dans `docs/index.html` :
- `<h1>Or</h1>` (dans `<div id="or" class="screen" hidden>`) -> `<h1>Commodités</h1>`.
- Les deux `<span>Or</span>` dans les boutons `.nav-item[data-route="or"]`
  (desktop + mobile, repérer avec `grep -n 'data-route="or"'`) ->
  `<span>Commodités</span>`. **Ne pas toucher** au troisième `<span>Or</span>`
  qui appartient à `#portefeuilleTabOr` (vérifier le contexte englobant
  avant chaque remplacement — ne jamais faire un remplacement global de
  `>Or<` dans tout le fichier).

- [ ] **Step 2 : Remplacer `.or-tabbar` par la sous-navigation Commodités**

Remplacer le `<div class="or-tabbar" role="tablist">` (qui ne contient plus
que `#orTabLongTerme` après la Tâche 4) par :

```html
<div class="or-tabbar" role="tablist">
  <button class="or-tab-btn" id="commodityTabOr" data-commodity="or" role="tab" aria-selected="true" type="button">Or</button>
  <button class="or-tab-btn" id="commodityTabArgent" data-commodity="argent" role="tab" aria-selected="false" type="button">Argent</button>
  <button class="or-tab-btn" id="commodityTabPetrole" data-commodity="petrole" role="tab" aria-selected="false" type="button">Pétrole</button>
  <button class="or-tab-btn" id="commodityTabGaz" data-commodity="gaz" role="tab" aria-selected="false" type="button">Gaz</button>
</div>
```

- [ ] **Step 3 : Généraliser `loadScore()` et `render(data)`**

Repérer `async function loadScore()` et `function render(data)` par
`grep -n "function loadScore\|function render(data)"`. Les remplacer par :

```javascript
const COMMODITY_SCORE_FILES = {
  or: 'score.json',
  argent: 'score_argent.json',
  petrole: 'score_petrole.json',
  gaz: 'score_gaz.json',
};
let activeCommodity = 'or';

async function loadScore(commodity) {
  commodity = commodity || activeCommodity;
  const content = document.getElementById('content');
  content.innerHTML = `<div class="skeleton"><div class="skeleton-line"></div><div class="skeleton-line"></div><div class="skeleton-line"></div><div class="skeleton-line"></div></div>`;
  try {
    const file = COMMODITY_SCORE_FILES[commodity] || COMMODITY_SCORE_FILES.or;
    const res = await fetch(file + '?t=' + Date.now());
    if (!res.ok) throw new Error(file + ' introuvable');
    const data = await res.json();
    render(data);
  } catch (e) {
    content.innerHTML = `<div class="empty">Données indisponibles pour l'instant.<br>${e.message}</div>`;
  }
}
```

(le reste de `loadScore` — la gestion d'erreur — ne change pas de forme,
seule la source du fetch devient paramétrée).

Dans `render(data)`, remplacer les deux lignes qui codent en dur `$ / oz` :

```javascript
const t = data.technical || {};
const unite = t.unite || '$ / oz';
const spotStr = t.spot ? `${t.spot.toLocaleString('fr-FR', {maximumFractionDigits:0})} ${unite}` : 'indisponible';
const ma200Str = t.ma200 ? `${t.ma200.toLocaleString('fr-FR', {maximumFractionDigits:0})} ${unite}` : 'indisponible';
```

Le texte de la notice "Détail du calcul" (qui mentionne littéralement
"favorable à l'or") doit devenir générique : remplacer
`plus elle s'écarte du centre, plus le signal est marqué.` et le passage
`favorable à l'or` par une formulation neutre, par exemple remplacer le
fragment HTML existant :

```javascript
Barre à droite
(doré) = favorable à l'or, à gauche (rouge) = défavorable — plus
elle s'écarte du centre, plus le signal est marqué.
```

par :

```javascript
Barre à droite
(doré) = favorable, à gauche (rouge) = défavorable — plus
elle s'écarte du centre, plus le signal est marqué.
```

- [ ] **Step 4 : Câbler les 4 boutons de sous-navigation**

Repérer le bloc `document.getElementById('orTabLongTerme').addEventListener`
restant après la Tâche 4 (celui qui affiche `#content`) et le remplacer par :

```javascript
['or', 'argent', 'petrole', 'gaz'].forEach(key => {
  const btnId = 'commodityTab' + key.charAt(0).toUpperCase() + key.slice(1);
  document.getElementById(btnId).addEventListener('click', () => {
    if (activeCommodity === key) return;
    activeCommodity = key;
    ['or', 'argent', 'petrole', 'gaz'].forEach(k => {
      const id = 'commodityTab' + k.charAt(0).toUpperCase() + k.slice(1);
      document.getElementById(id).setAttribute('aria-selected', String(k === key));
    });
    showTabView(document.getElementById('content'));
    loadScore(key);
  });
});
```

- [ ] **Step 5 : Adapter le routing initial**

Repérer le bloc de routing (`if (route.screen === 'or' && !scoreLoaded) {
scoreLoaded = true; loadScore(); }`, ligne ~6438 au 2026-10-25, déjà
partiellement nettoyé en Tâche 4) — `loadScore()` sans argument utilise déjà
`activeCommodity` par défaut (`'or'`), donc ce bloc n'a pas besoin de
changer de forme, seulement de rester cohérent avec les retraits de la
Tâche 4 (vérifier qu'aucune référence à `orScalpingView`/
`scalpingJsLoaded` ne traîne encore à proximité).

- [ ] **Step 6 : Vérifier la syntaxe des scripts inline**

Même commande qu'à la Tâche 4, Step 4.
Expected: `OK (...)`.

- [ ] **Step 7 : Test manuel local**

Ouvrir `docs/index.html` dans un navigateur (ou servir le dossier `docs/`
en local, ex. `python3 -m http.server --directory docs`), naviguer vers
l'onglet "Commodités", cliquer sur chacun des 4 boutons Or/Argent/Pétrole/Gaz
et vérifier que chacun affiche un score différent sans erreur console
(nécessite que la Tâche 2 ait déjà généré `docs/score_argent.json`,
`docs/score_petrole.json`, `docs/score_gaz.json` localement via
`python3 commodity_score.py <clé>` — sinon le fetch échoue proprement avec
le message "Données indisponibles", ce qui est un échec de test attendu
tant que ces fichiers n'existent pas encore, pas un bug de cette tâche).

- [ ] **Step 8 : Commit**

```bash
git add docs/index.html
git commit -m "feat(commodites): onglet Commodites avec sous-navigation Or/Argent/Petrole/Gaz"
```

---

### Task 6 : Vérification finale et nettoyage

**Files:**
- Modify (potentiellement) : tout fichier où une incohérence serait trouvée pendant cette vérification.

**Interfaces:** aucune nouvelle — tâche de vérification transverse.

- [ ] **Step 1 : Suite de tests Python complète**

Run: `python3 -m pytest tests/ -v`
Expected: tous les tests passent, y compris `tests/test_commodity_score.py`
et l'intégralité de la suite existante (aucune régression, en particulier
sur `tests/gold_bot/` et `tests/test_indices_score.py`, intouchés par ce
plan).

- [ ] **Step 2 : Suites de tests JS**

Run: `node docs/real_price_chart.test.js && node docs/company_quote_widget.test.js`
(et tout autre `docs/*.test.js` présent — lister avec `ls docs/*.test.js`)
Expected: tous PASS.

- [ ] **Step 3 : Grep de cohérence finale**

```bash
grep -n "scalping\|Scalping" docs/index.html
```
Expected : plus aucune occurrence liée à l'onglet retiré (si `gold_bot`/VPS
sont mentionnés ailleurs dans le site, hors de cet écran, c'est hors
périmètre de ce plan et ne doit pas être touché).

```bash
grep -rn "gold_score\." commodity_score.py
```
Expected : uniquement des imports (`from gold_score import ...`), jamais de
redéfinition locale d'une fonction déjà importée.

- [ ] **Step 4 : requirements.txt**

Vérifier qu'aucune nouvelle dépendance n'a été ajoutée (ce plan n'en
nécessite aucune — `requests`/`yfinance` déjà présents). Si une tâche
précédente en a ajouté une par erreur, la retirer et documenter pourquoi
dans le commit.

- [ ] **Step 5 : Commit final si des ajustements ont été faits**

```bash
git add -A
git commit -m "chore(commodites): verification finale et nettoyage"
```

(Ne rien committer si aucun ajustement n'a été nécessaire — cette étape est
conditionnelle.)
