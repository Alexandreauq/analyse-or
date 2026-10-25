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

import json
import math
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
    # .dropna() au point d'extraction yfinance : convention du dépôt (voir
    # indices_score.py) pour qu'une valeur manquante/NaN de la série ne se
    # propage jamais jusqu'au JSON publié (incident du 2026-09-19).
    gold = yf.Ticker("GC=F").history(period=f"{lookback_days}d")["Close"].dropna()
    silver = yf.Ticker("SI=F").history(period=f"{lookback_days}d")["Close"].dropna()
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
    # .dropna() — même convention que ci-dessus.
    wti = yf.Ticker("CL=F").history(period=f"{lookback_days}d")["Close"].dropna()
    brent = yf.Ticker("BZ=F").history(period=f"{lookback_days}d")["Close"].dropna()
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
    current_price = float(obs[-1]["value"])
    current_month = int(obs[-1]["date"].split("-")[1])
    # Exclut l'observation la plus récente (obs[-1], "aujourd'hui") du calcul
    # de la moyenne saisonnière : la moyenne doit représenter "ce que fait
    # normalement ce mois les années précédentes" (docstring/spec), jamais
    # inclure la lecture du jour elle-même — sinon le prix du jour se compare
    # partiellement à lui-même, diluant l'écart réel d'environ 15-20% selon
    # la profondeur d'historique disponible pour ce mois (bug trouvé en
    # revue finale, voir docs/superpowers/plans/2026-10-25-commodites-score-plan.md).
    history_this_month = [
        float(o["value"]) for o in obs[:-1]
        if int(o["date"].split("-")[1]) == current_month
    ]
    if not history_this_month:
        history_this_month = [current_price]
    seasonal_average = sum(history_this_month) / len(history_this_month)
    if not math.isfinite(current_price) or not math.isfinite(seasonal_average):
        raise ValueError(
            "Valeur FRED non-finie pour DHHNGSP (donnée corrompue) — "
            f"current={current_price!r} seasonal_average={seasonal_average!r}"
        )
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
    # Un spot/ma200 non-finite (NaN, +-inf) doit être traité exactement comme
    # None, pas laissé se propager : avec l'ordre d'arguments ci-dessous,
    # min(10.0, nan) renvoie 10.0 (pas nan — vérifié expérimentalement, voir
    # le rapport de fix), donc sans cette garde un repère technique corrompu
    # produirait silencieusement un score +10 (signal maximalement favorable)
    # au lieu de rester neutre.
    if (
        spot is None or ma200 is None
        or not math.isfinite(spot) or not math.isfinite(ma200)
        or ma200 == 0
    ):
        return FactorResult("Dynamique récente", 0.0, weight, "repère technique indisponible")
    pct_ecart = (spot - ma200) / ma200 * 100
    score = max(-10.0, min(10.0, (pct_ecart / 5.0) * 10.0))
    return FactorResult("Dynamique récente", score, weight, f"{pct_ecart:+.1f}% vs MM200")


# ---------------------------------------------------------------------------
# 6. Technique : spot + MM200, générique par ticker
# ---------------------------------------------------------------------------

def get_spot_and_ma200(ticker: str) -> tuple[float, float]:
    if yf is None:
        raise RuntimeError("yfinance n'est pas installé (pip install yfinance)")
    # .dropna() — même convention que les autres pulls yfinance ci-dessus.
    hist = yf.Ticker(ticker).history(period="1y")["Close"].dropna()
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
        dollar_factor = factor_dollar()
        factors = [
            factor_taux_reels(api_key),
            # factor_dollar() renvoie un poids fixe (0.15, celui de l'or) ;
            # on force ici le poids voulu pour l'argent (0.20, spec) avant renormalisation.
            FactorResult(dollar_factor.name, dollar_factor.score, 0.20, dollar_factor.raw_value),
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
        dollar_factor = factor_dollar()
        factors = [
            # factor_dollar() renvoie un poids fixe (0.15, celui de l'or) ;
            # on force ici le poids voulu pour le pétrole (0.20, spec) avant renormalisation.
            FactorResult(dollar_factor.name, dollar_factor.score, 0.20, dollar_factor.raw_value),
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
        json.dump(history, fh, ensure_ascii=False, allow_nan=False, indent=2)
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
        json.dump(payload, fh, ensure_ascii=False, allow_nan=False, indent=2)

    os.makedirs(DOCS_DIR, exist_ok=True)
    docs_path = os.path.join(DOCS_DIR, f"score_{commodity_key}.json")
    with open(docs_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, allow_nan=False, indent=2)

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
