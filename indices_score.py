"""
Score fondamental CAC 40 — Phase pilote (5 entreprises)
=========================================================

Calcule un score composite par entreprise à partir de 5 ans de comptes
publiés (via yfinance), selon la méthodologie décrite dans
Methodologie_Analyse_Indices.md (synthèse Vernimmen : rentabilité
comptable, analyse du financement, coût du capital, pratique de
l'évaluation).

Installation :
    pip install requests yfinance pandas
"""

import json
import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

try:
    import yfinance as yf
except ImportError:
    yf = None

import requests
import trafilatura


WEIGHTS = {
    "rentabilite": 0.24,
    "structure_financiere": 0.20,
    "croissance": 0.16,
    "generation_cash": 0.12,
    "valorisation": 0.08,
    "dynamique_recente": 0.10,
    "actualite_recente": 0.10,
}

COMPANIES = [
    {"ticker": "MC.PA", "name": "LVMH"},
    {"ticker": "TTE.PA", "name": "TotalEnergies"},
    {"ticker": "SU.PA", "name": "Schneider Electric"},
    {"ticker": "SAN.PA", "name": "Sanofi"},
    {"ticker": "BN.PA", "name": "Danone"},
]

SECTOR_PROFILES = {
    "Utilities": "defensif",
    "Consumer Defensive": "defensif",
    "Healthcare": "defensif",
    "Real Estate": "defensif",
    "Industrials": "standard",
    "Communication Services": "standard",
    "Energy": "cyclique",
    "Basic Materials": "cyclique",
    "Consumer Cyclical": "cyclique",
    "Technology": "cyclique",
}

SECTOR_ADJUSTMENT = {"defensif": 1.3, "standard": 1.0, "cyclique": 0.7}


@dataclass
class FactorResult:
    name: str
    score: float          # -10 à +10
    weight: float
    raw_value: str


def sector_risk_profile(sector: str | None) -> str:
    """Renvoie 'defensif' / 'standard' / 'cyclique' pour un secteur
    yfinance donné, 'standard' par défaut si secteur inconnu ou absent."""
    return SECTOR_PROFILES.get(sector, "standard")


def _clamp(value: float, low: float = -10.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def _is_missing(value) -> bool:
    """True si une valeur numérique issue de yfinance est absente ou NaN —
    yfinance ne garantit pas que chaque poste soit renseigné pour les 5
    années demandées."""
    try:
        return math.isnan(value)
    except TypeError:
        return value is None


def _safe_value(series, col):
    """Valeur de `series` à la date `col`, ou NaN si cette date est absente
    de son index. Les 3 relevés annuels yfinance (financials/balance_sheet/
    cashflow) n'ont pas toujours exactement les mêmes colonnes de dates
    pour une entreprise donnée (observé en production sur SAN.PA/BN.PA :
    `financials` remonte à 2021-12-31 mais `balance_sheet`/`cashflow` non)
    — indexer par une date qui vient d'un autre relevé (`years_cols`,
    dérivé de `financials.columns`) lève sinon `KeyError` plutôt que de
    dégrader vers une valeur manquante comme le reste de ce pipeline."""
    return series[col] if col in series.index else float("nan")


ROCE_SPREAD_SCALE = 5.0  # points d'écart ROCE - coût du capital pour un score plein


def score_rentabilite(roce: float, roe: float, cost_of_capital: float) -> FactorResult:
    """
    ROCE = rentabilité économique après IS (Résultat d'exploitation après
    IS / Actif économique). Le signal principal est l'écart entre le ROCE
    et le coût du capital (proxy simplifié) : au-dessus, l'entreprise crée
    de la valeur ; en dessous, elle en détruit. Le ROE est affiché à titre
    informatif (permet de repérer si la rentabilité des capitaux propres
    provient surtout de l'effet de levier plutôt que de la performance
    opérationnelle), sans peser directement sur le score.
    """
    spread = roce - cost_of_capital
    score = _clamp((spread / ROCE_SPREAD_SCALE) * 10)
    return FactorResult(
        "Rentabilité / création de valeur",
        score,
        WEIGHTS["rentabilite"],
        f"ROCE {roce:.1f}% vs coût du capital {cost_of_capital:.1f}% "
        f"(ROE {roe:.1f}%)",
    )


NET_DEBT_EBITDA_COMFORTABLE = 3.0   # seuil Standard, ajusté par profil sectoriel
NET_DEBT_EBITDA_RISKY = 5.5         # seuil Standard, ajusté par profil sectoriel
ICR_CRITICAL = 3.0                  # seuil Standard, ajusté par profil sectoriel
DEBT_INTEREST_RATE_PROXY = 3.0      # % taux d'intérêt proxy sur la dette totale
                                     # (frais financiers non fiablement isolés
                                     # chez ces entreprises) — utilisé pour l'ICR
                                     # et repris tel quel pour le coût de la
                                     # dette dans le calcul du WACC (Task 4).


def _score_leverage(ratio: float, comfortable: float, risky: float) -> float:
    """+10 à ratio nul, 0 au seuil confortable, -10 au seuil risqué et au-delà."""
    if ratio <= comfortable:
        return _clamp(10.0 - 10.0 * (ratio / comfortable))
    if ratio <= risky:
        return -10.0 * (ratio - comfortable) / (risky - comfortable)
    return -10.0


def _score_coverage(icr: float, critical: float) -> float:
    """-10 à ICR nul ou négatif, 0 au seuil critique, +10 au double du seuil critique."""
    if icr <= 0:
        return -10.0
    if icr <= critical:
        return -10.0 + 10.0 * (icr / critical)
    return _clamp(10.0 * (icr - critical) / critical, -10.0, 10.0)


def score_structure_financiere(net_debt_ebitda: float, icr: float, sector: str | None) -> FactorResult:
    """
    Dette nette/EBITDA et couverture des intérêts (ICR = EBIT / frais
    financiers nets), seuils Vernimmen ajustés par profil de risque
    sectoriel : un même niveau d'endettement ne représente pas le même
    risque selon la stabilité des flux de trésorerie du secteur.
    """
    profile = sector_risk_profile(sector)
    adjustment = SECTOR_ADJUSTMENT[profile]

    comfortable = NET_DEBT_EBITDA_COMFORTABLE * adjustment
    risky = NET_DEBT_EBITDA_RISKY * adjustment
    critical_icr = ICR_CRITICAL / adjustment

    leverage_score = _score_leverage(net_debt_ebitda, comfortable, risky)
    coverage_score = _score_coverage(icr, critical_icr)
    score = _clamp((leverage_score + coverage_score) / 2)

    return FactorResult(
        "Structure financière / solvabilité",
        score,
        WEIGHTS["structure_financiere"],
        f"Dette nette/EBITDA {net_debt_ebitda:.1f}x (seuil confort "
        f"{comfortable:.1f}x, profil {profile}) — ICR {icr:.1f}x",
    )


GROWTH_SCALE = 10.0          # % de CAGR moyen pour un score plein
GROWTH_DIVERGENCE_MARGIN = 5.0   # points d'écart CA/EBITDA tolérés avant pénalité
GROWTH_DIVERGENCE_PENALTY = 3.0


def score_croissance(cagr_ca: float, cagr_ebitda: float) -> FactorResult:
    """
    CAGR chiffre d'affaires et EBITDA sur 5 ans. Une croissance du CA non
    suivie par l'EBITDA signale une dégradation de la rentabilité -> pénalité.
    """
    base = _clamp(((cagr_ca + cagr_ebitda) / 2) / GROWTH_SCALE * 10)
    if cagr_ebitda < cagr_ca - GROWTH_DIVERGENCE_MARGIN:
        base = _clamp(base - GROWTH_DIVERGENCE_PENALTY)
    return FactorResult(
        "Croissance",
        base,
        WEIGHTS["croissance"],
        f"CAGR CA {cagr_ca:+.1f}%/an, CAGR EBITDA {cagr_ebitda:+.1f}%/an (5 ans)",
    )


FCF_CONVERSION_NEUTRAL = 50.0   # % de conversion FCF/EBITDA jugé neutre
FCF_CONVERSION_SCALE = 5.0      # points de conversion % pour 1 point de score


def score_generation_cash(fcf_conversion: float) -> FactorResult:
    """Conversion FCF/EBITDA (%) : au-dessus de 50%, la rentabilité comptable
    se traduit bien en cash réel ; en dessous, le BFR ou les capex absorbent
    l'essentiel de la génération de cash."""
    score = _clamp((fcf_conversion - FCF_CONVERSION_NEUTRAL) / FCF_CONVERSION_SCALE)
    return FactorResult(
        "Génération de cash",
        score,
        WEIGHTS["generation_cash"],
        f"Conversion FCF/EBITDA {fcf_conversion:.0f}%",
    )


VALUATION_PREMIUM_SCALE = 3.0       # % d'écart au multiple historique pour 1 point de score
VALUATION_GROWTH_DAMPENING_CAGR = 5.0   # au-dessus de ce CAGR EBITDA, une prime est jugée justifiée
VALUATION_GROWTH_DAMPENING_FACTOR = 0.4  # atténuation de la pénalité si croissance forte


def _premium_score(current: float, avg_5y: float, cagr_ebitda: float) -> float:
    """Décote vs moyenne 5 ans -> score positif (favorable) ; prime -> score
    négatif, mais atténué si la croissance de l'EBITDA justifie une prime."""
    if avg_5y == 0:
        return 0.0
    premium_pct = (current / avg_5y - 1.0) * 100
    if premium_pct <= 0:
        return _clamp(-premium_pct / VALUATION_PREMIUM_SCALE, 0.0, 10.0)
    dampening = (
        VALUATION_GROWTH_DAMPENING_FACTOR
        if cagr_ebitda >= VALUATION_GROWTH_DAMPENING_CAGR
        else 1.0
    )
    penalty = _clamp(premium_pct / VALUATION_PREMIUM_SCALE, 0.0, 10.0) * dampening
    return -penalty


def score_valorisation(
    current_ev_ebitda: float, avg_ev_ebitda_5y: float,
    current_pe: float, avg_pe_5y: float,
    cagr_ebitda: float,
) -> FactorResult:
    """Multiples EV/EBITDA et P/E actuels comparés à la moyenne 5 ans de
    l'entreprise elle-même (pas de comparaison à des pairs au v1). Une
    prime n'est pénalisée que modérément et seulement si elle n'est pas
    soutenue par la croissance de l'EBITDA (cf. Méthodologie section 5)."""
    ev_ebitda_score = _premium_score(current_ev_ebitda, avg_ev_ebitda_5y, cagr_ebitda)
    pe_score = _premium_score(current_pe, avg_pe_5y, cagr_ebitda)
    score = _clamp((ev_ebitda_score + pe_score) / 2)
    return FactorResult(
        "Valorisation relative",
        score,
        WEIGHTS["valorisation"],
        f"EV/EBITDA {current_ev_ebitda:.1f}x (moy. 5 ans {avg_ev_ebitda_5y:.1f}x) — "
        f"PER {current_pe:.1f}x (moy. 5 ans {avg_pe_5y:.1f}x)",
    )


PRICE_MOMENTUM_SCALE = 20.0    # % d'écart vs MM200 pour un score plein
QUARTERLY_ACCEL_SCALE = 10.0   # points d'écart de croissance pour un score plein


def score_dynamique_recente(
    ecart_pct_ma200: float | None,
    quarterly_yoy_growth_ca: float | None,
    cagr_ca: float,
) -> FactorResult:
    """Moyenne de 2 sous-signaux (chacun mis à l'échelle -10/+10
    indépendamment avant moyenne, car unités différentes) : tendance du
    cours vs MM200, et accélération du dernier trimestre publié vs la
    tendance 5 ans déjà calculée (cagr_ca). Si un sous-signal manque, la
    moyenne ne porte que sur celui disponible ; si aucun n'est
    disponible, score neutre 0.0."""
    sub_scores = []
    raw_parts = []
    if ecart_pct_ma200 is not None:
        sub_scores.append(_clamp(ecart_pct_ma200 / PRICE_MOMENTUM_SCALE * 10))
        raw_parts.append(f"Cours {ecart_pct_ma200:+.1f}% vs MM200")
    if quarterly_yoy_growth_ca is not None:
        acceleration = quarterly_yoy_growth_ca - cagr_ca
        sub_scores.append(_clamp(acceleration / QUARTERLY_ACCEL_SCALE * 10))
        raw_parts.append(
            f"CA dernier trim. {quarterly_yoy_growth_ca:+.1f}% vs an dernier "
            f"(tendance 5 ans {cagr_ca:+.1f}%)"
        )
    score = sum(sub_scores) / len(sub_scores) if sub_scores else 0.0
    raw_value = " — ".join(raw_parts) if raw_parts else "Données insuffisantes"
    return FactorResult("Dynamique récente", score, WEIGHTS["dynamique_recente"], raw_value)


NEWS_SENTIMENT_WINDOW_DAYS = 14


def score_actualite_recente(news_items: list[dict]) -> FactorResult:
    """Moyenne du sentiment des actus datées de moins de 14 jours, mise à
    l'échelle -10/+10. Neutre (0.0) si aucune actu récente exploitable —
    ni erreur, ni biais optimiste/pessimiste par défaut."""
    cutoff = datetime.now() - timedelta(days=NEWS_SENTIMENT_WINDOW_DAYS)
    recent_sentiments = []
    for item in news_items:
        try:
            item_date = datetime.strptime(item["date"], "%Y-%m-%d")
        except (ValueError, TypeError, KeyError):
            continue
        if item_date >= cutoff:
            recent_sentiments.append(item.get("sentiment", 0))
    if not recent_sentiments:
        return FactorResult(
            "Actualité récente", 0.0, WEIGHTS["actualite_recente"],
            "Aucune actualité récente exploitable",
        )
    avg_sentiment = sum(recent_sentiments) / len(recent_sentiments)
    score = _clamp(avg_sentiment * 10)
    return FactorResult(
        "Actualité récente", score, WEIGHTS["actualite_recente"],
        f"Ton moyen des {len(recent_sentiments)} actualités récentes : {avg_sentiment:+.2f}",
    )


def compute_composite(factors: list[FactorResult]) -> float:
    weighted_sum = sum(f.score * f.weight for f in factors)
    return round(weighted_sum * 10, 1)


def interpret(composite: float) -> str:
    if composite > 50:
        return "Profil fondamental très solide"
    if composite > 15:
        return "Solide"
    if composite > -15:
        return "Neutre"
    return "Fragile"


def get_row(df, *aliases):
    """Renvoie la première ligne du DataFrame dont le libellé correspond à
    l'un des alias fournis. Les libellés de lignes yfinance varient parfois
    d'une entreprise à l'autre (ex: 'Total Debt' absent chez certaines) —
    d'où la liste d'alias plutôt qu'un seul nom fixe."""
    for alias in aliases:
        if alias in df.index:
            return df.loc[alias]
    raise KeyError(
        f"Aucune des lignes {aliases} trouvée (lignes disponibles : {list(df.index)})"
    )


def _cagr(first_value: float, last_value: float, years: int) -> float:
    """CAGR en % entre la valeur la plus ancienne et la plus récente.

    Renvoie 0.0 (neutre) si une valeur est manquante (NaN) — yfinance ne
    fournit pas toujours 5 années pleines pour chaque poste — plutôt que de
    laisser un NaN se propager jusqu'à _clamp, qui le traiterait comme un
    score maximal (+10) au lieu d'une absence de donnée.
    """
    if years <= 0 or _is_missing(first_value) or _is_missing(last_value) or first_value <= 0:
        return 0.0
    return ((last_value / first_value) ** (1 / years) - 1) * 100


def _window_average(row, cols: list) -> float:
    """Moyenne d'une ligne yfinance sur un sous-ensemble de colonnes
    (années), en ignorant les valeurs manquantes (NaN)."""
    values = [row[c] for c in cols if not _is_missing(row[c])]
    return sum(values) / len(values) if values else float("nan")


def extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding: float) -> dict:
    """
    Calcule les ratios bruts nécessaires aux fonctions de score à partir des
    états financiers yfinance (financials, balance_sheet, cashflow — colonnes
    = dates d'exercice, la plus récente en premier) et des cours de clôture
    par date d'exercice (closes_by_year, même clés que les colonnes).
    """
    years_cols = list(financials.columns)  # plus récent en premier
    n_years = len(years_cols)
    latest = years_cols[0]

    revenue = get_row(financials, "Total Revenue", "Operating Revenue")
    ebitda = get_row(financials, "EBITDA", "Normalized EBITDA")
    ebit = get_row(financials, "EBIT", "Operating Income", "Total Operating Income As Reported")
    net_income = get_row(financials, "Net Income", "Net Income Common Stockholders")
    tax_rate = get_row(financials, "Tax Rate For Calcs")

    total_debt = get_row(balance_sheet, "Total Debt")
    cash = get_row(balance_sheet, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
    equity = get_row(balance_sheet, "Stockholders Equity", "Common Stock Equity")

    op_cash_flow = get_row(cashflow, "Operating Cash Flow")
    capex = get_row(cashflow, "Capital Expenditure")

    # _safe_value (pas un accès direct [latest]) : total_debt/cash/equity
    # viennent de balance_sheet, dont les colonnes ne correspondent pas
    # toujours exactement à celles de financials pour une entreprise donnée
    # (observé en production : financials remonte à une date que
    # balance_sheet/cashflow n'ont pas) — indexer par une date qui vient
    # d'un autre relevé lève sinon KeyError.
    total_debt_latest = _safe_value(total_debt, latest)
    cash_latest = _safe_value(cash, latest)
    equity_latest = _safe_value(equity, latest)

    net_debt_latest = (
        total_debt_latest - cash_latest
        if not _is_missing(total_debt_latest) and not _is_missing(cash_latest) else 0.0
    )
    economic_assets_latest = equity_latest + net_debt_latest
    # `if X else default` seul ne suffit pas à écarter un NaN (bool(nan) est
    # True en Python) — d'où le `and not _is_missing(X)` en plus du test de
    # vérité déjà présent, pour ne jamais laisser un NaN se propager dans un
    # score final via une division silencieusement invalide.
    roce = (
        (ebit[latest] * (1 - tax_rate[latest]) / economic_assets_latest) * 100
        if economic_assets_latest and not _is_missing(economic_assets_latest) else 0.0
    )
    roe = (
        (net_income[latest] / equity_latest) * 100
        if equity_latest and not _is_missing(equity_latest) else 0.0
    )

    net_debt_ebitda = (
        net_debt_latest / ebitda[latest]
        if ebitda[latest] and not _is_missing(ebitda[latest]) else 0.0
    )
    icr = (
        ebit[latest] / (total_debt_latest * (DEBT_INTEREST_RATE_PROXY / 100))
        if total_debt_latest and not _is_missing(total_debt_latest) else 10.0
    )  # proxy frais financiers si non isolés (DEBT_INTEREST_RATE_PROXY) — parenthèses
    # nécessaires pour rester strictement identique à l'ancien littéral `* 0.03`
    # (l'associativité par défaut donnait `(total_debt * 3.0) / 100`, qui diffère
    # de `total_debt * 0.03` d'1 ULP sur ~35% des valeurs)

    # CAGR lissé sur les 2 exercices les plus récents vs les 2 plus anciens
    # (plutôt qu'un simple point à point) pour réduire la sensibilité à une
    # année isolée atypique (ex : pic des prix de l'énergie en 2022 pour les
    # pétrolières) — voir Methodologie_Analyse_Indices.md §3.
    smoothing_window = 2 if n_years >= 4 else 1
    recent_cols = years_cols[:smoothing_window]
    old_cols = years_cols[-smoothing_window:]
    cagr_span = n_years - smoothing_window
    cagr_ca = _cagr(
        _window_average(revenue, old_cols), _window_average(revenue, recent_cols), cagr_span
    )
    cagr_ebitda = _cagr(
        _window_average(ebitda, old_cols), _window_average(ebitda, recent_cols), cagr_span
    )

    # FCF = Flux de trésorerie opérationnel - |Capex| (proxy OCF standard),
    # et non le montage "EBITDA - IS théorique - ΔBFR - investissements" décrit
    # dans Methodologie_Analyse_Indices.md §4 : le flux de trésorerie
    # opérationnel yfinance embarque déjà l'impôt effectivement payé et les
    # variations de BFR, ce qui est plus robuste que de les reconstruire à la
    # main sur 5 entreprises aux données hétérogènes.
    op_cash_flow_latest = _safe_value(op_cash_flow, latest)
    capex_latest = _safe_value(capex, latest)  # capex déjà négatif dans yfinance
    fcf = (
        op_cash_flow_latest + capex_latest
        if not _is_missing(op_cash_flow_latest) and not _is_missing(capex_latest) else 0.0
    )
    fcf_conversion = (
        (fcf / ebitda[latest]) * 100
        if ebitda[latest] and not _is_missing(ebitda[latest]) else 0.0
    )

    ev_ebitda_by_year, pe_by_year = [], []
    for col in years_cols:
        price = closes_by_year.get(col)
        total_debt_value = _safe_value(total_debt, col)
        cash_value = _safe_value(cash, col)
        if (
            price is None
            or not ebitda[col]
            or not net_income[col]
            or _is_missing(ebitda[col])
            or _is_missing(net_income[col])
            or _is_missing(total_debt_value)
            or _is_missing(cash_value)
        ):
            continue
        market_cap = price * shares_outstanding
        net_debt_year = total_debt_value - cash_value
        ev_ebitda_by_year.append((market_cap + net_debt_year) / ebitda[col])
        pe_by_year.append(market_cap / net_income[col])

    current_ev_ebitda = ev_ebitda_by_year[0] if ev_ebitda_by_year else 0.0
    avg_ev_ebitda_5y = sum(ev_ebitda_by_year) / len(ev_ebitda_by_year) if ev_ebitda_by_year else 0.0
    current_pe = pe_by_year[0] if pe_by_year else 0.0
    avg_pe_5y = sum(pe_by_year) / len(pe_by_year) if pe_by_year else 0.0

    return {
        "roce": roce,
        "roe": roe,
        "net_debt_ebitda": net_debt_ebitda,
        "icr": icr,
        "cagr_ca": cagr_ca,
        "cagr_ebitda": cagr_ebitda,
        "fcf_conversion": fcf_conversion,
        "current_ev_ebitda": current_ev_ebitda,
        "avg_ev_ebitda_5y": avg_ev_ebitda_5y,
        "current_pe": current_pe,
        "avg_pe_5y": avg_pe_5y,
        "fcf": fcf,
        "net_debt": net_debt_latest,
        "equity": equity_latest,
        "tax_rate": tax_rate[latest],
        "total_debt": total_debt_latest,
    }


def extract_quarterly_growth(quarterly_financials) -> float | None:
    """CA du dernier trimestre publié vs le même trimestre il y a un an
    (%). None si moins de 5 trimestres sont disponibles (yfinance
    n'expose généralement que les 4-5 derniers) ou si une des deux
    valeurs est manquante/NaN/nulle ou négative.

    Simplification assumée : `cols[4]` est traité comme "le même
    trimestre il y a un an" en supposant une cadence trimestrielle
    régulière sans trou. Si le calendrier fiscal d'une entreprise est
    irrégulier, `cols[4]` pourrait être un trimestre différent —
    dégradation silencieuse vers une comparaison légèrement inexacte,
    jugé acceptable pour un signal secondaire à 10% de poids.
    """
    cols = list(quarterly_financials.columns)
    if len(cols) < 5:
        return None
    revenue = get_row(quarterly_financials, "Total Revenue", "Operating Revenue")
    latest, year_ago = revenue[cols[0]], revenue[cols[4]]
    if _is_missing(latest) or _is_missing(year_ago) or year_ago <= 0:
        return None
    return (latest / year_ago - 1) * 100


def build_financial_narrative_context(
    financials, balance_sheet, cashflow, quarterly_financials,
) -> str:
    """Formate les séries annuelles (jusqu'à ~4 ans, le plus récent en
    premier) et les derniers trimestres en un texte structuré, destiné à
    être injecté dans le prompt Claude — pas de calcul ici, seulement de
    la mise en forme brute. Une ligne 'non disponible' remplace toute
    valeur manquante plutôt que de faire échouer le formatage."""
    revenue = get_row(financials, "Total Revenue", "Operating Revenue")
    ebitda = get_row(financials, "EBITDA", "Normalized EBITDA")
    ebit = get_row(financials, "EBIT", "Operating Income", "Total Operating Income As Reported")
    net_income = get_row(financials, "Net Income", "Net Income Common Stockholders")
    equity = get_row(balance_sheet, "Stockholders Equity", "Common Stock Equity")
    total_debt = get_row(balance_sheet, "Total Debt")
    cash = get_row(balance_sheet, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
    op_cash_flow = get_row(cashflow, "Operating Cash Flow")
    capex = get_row(cashflow, "Capital Expenditure")

    def _fmt(value) -> str:
        return "non disponible" if _is_missing(value) else f"{value:,.0f}"

    lines = ["Comptes annuels (le plus récent en premier) :"]
    for col in financials.columns:
        equity_value = _safe_value(equity, col)
        total_debt_value = _safe_value(total_debt, col)
        cash_value = _safe_value(cash, col)
        op_cash_flow_value = _safe_value(op_cash_flow, col)
        capex_value = _safe_value(capex, col)
        net_debt = (
            total_debt_value - cash_value
            if not _is_missing(total_debt_value) and not _is_missing(cash_value) else None
        )
        fcf = (
            op_cash_flow_value + capex_value
            if not _is_missing(op_cash_flow_value) and not _is_missing(capex_value) else None
        )
        lines.append(
            f"- {col.date() if hasattr(col, 'date') else col} : CA {_fmt(revenue[col])}, "
            f"EBITDA {_fmt(ebitda[col])}, EBIT {_fmt(ebit[col])}, "
            f"résultat net {_fmt(net_income[col])}, capitaux propres {_fmt(equity_value)}, "
            f"dette nette {_fmt(net_debt)}, FCF {_fmt(fcf)}"
        )

    lines.append("\nDerniers trimestres publiés (le plus récent en premier) :")
    if len(quarterly_financials.columns):
        quarterly_revenue = get_row(quarterly_financials, "Total Revenue", "Operating Revenue")
        for col in quarterly_financials.columns:
            lines.append(
                f"- {col.date() if hasattr(col, 'date') else col} : CA {_fmt(quarterly_revenue[col])}"
            )

    return "\n".join(lines)


def _column_date_iso(col) -> str:
    return col.date().isoformat() if hasattr(col, "date") else str(col)


def latest_quarter_date(quarterly_financials) -> str | None:
    """Date du trimestre le plus récent publié, format ISO (YYYY-MM-DD).
    None si aucune colonne (yfinance en panne, ou — cas rencontré en
    production pour LVMH/Schneider Electric/Danone — pas de
    quarterly_financials exploitable pour ce ticker du tout)."""
    cols = list(quarterly_financials.columns)
    if not cols:
        return None
    return _column_date_iso(cols[0])


def _resolve_financial_analysis_date(quarterly_financials, financials) -> str | None:
    """Date utilisée comme signal de fraîcheur pour l'analyse financière :
    le dernier trimestre publié si disponible, sinon le dernier exercice
    annuel en repli. Sans ce repli, une entreprise sans quarterly_financials
    exploitable (LVMH, Schneider Electric, Danone en pratique) verrait
    latest_quarter_date rester None indéfiniment — son analyse financière
    ne se régénérerait alors plus jamais (voir le mécanisme de carry-forward
    dans build_company_entry)."""
    quarter_date = latest_quarter_date(quarterly_financials)
    if quarter_date is not None:
        return quarter_date
    annual_cols = list(financials.columns)
    return _column_date_iso(annual_cols[0]) if annual_cols else None


def fetch_company_financials(ticker: str) -> dict:
    if yf is None:
        raise RuntimeError("yfinance n'est pas installé (pip install yfinance)")
    t = yf.Ticker(ticker)
    financials = t.financials
    balance_sheet = t.balance_sheet
    cashflow = t.cashflow
    quarterly_financials = t.quarterly_financials
    info = t.info

    shares_outstanding = info.get("sharesOutstanding") or 0.0
    history = t.history(period="6y")["Close"]
    closes_by_year = {}
    for col in financials.columns:
        target_date = col.date() if hasattr(col, "date") else col
        window = history[history.index.date <= target_date] if hasattr(history.index, "date") else history
        if len(window):
            closes_by_year[col] = float(window.iloc[-1])

    current_price = float(history.iloc[-1]) if len(history) else None
    ma200 = float(history.tail(200).mean()) if len(history) else None
    ecart_pct_ma200 = (
        (current_price - ma200) / ma200 * 100
        if current_price is not None and ma200 else None
    )

    ratios = extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding)
    ratios["sector"] = info.get("sector")
    ratios["ecart_pct_ma200"] = ecart_pct_ma200
    ratios["quarterly_yoy_growth_ca"] = extract_quarterly_growth(quarterly_financials)
    ratios["financial_context"] = build_financial_narrative_context(
        financials, balance_sheet, cashflow, quarterly_financials
    )
    ratios["latest_quarter_date"] = _resolve_financial_analysis_date(quarterly_financials, financials)
    ratios["current_price"] = current_price
    ratios["ma200"] = ma200
    ratios["shares_outstanding"] = shares_outstanding
    ratios["beta"] = info.get("beta")
    return ratios


NEWS_RSS_URL = "https://news.google.com/rss/search"
NEWS_MAX_ITEMS = 5


def parse_news_rss(xml_bytes: bytes) -> list[dict]:
    """Extrait titre/date/lien/source des N premiers <item> d'un flux RSS Google News."""
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall(".//item")[:NEWS_MAX_ITEMS]:
        title = item.findtext("title", default="")
        link = item.findtext("link", default="")
        pub_date_raw = item.findtext("pubDate", default="")
        source = item.findtext("source", default="")
        try:
            date_str = parsedate_to_datetime(pub_date_raw).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            date_str = ""
        items.append({"title": title, "date": date_str, "link": link, "source": source})
    return items


def fetch_news(company_name: str) -> list[dict]:
    params = {"q": company_name, "hl": "fr", "gl": "FR", "ceid": "FR:fr"}
    resp = requests.get(NEWS_RSS_URL, params=params, timeout=15)
    resp.raise_for_status()
    items = parse_news_rss(resp.content)
    for item in items:
        article_text = fetch_article_text(item["link"])
        result = summarize_news_item(item["title"], company_name, article_text)
        item["summary"] = result["summary"]
        item["sentiment"] = result["sentiment"]
    return items


ARTICLE_TEXT_MAX_CHARS = 4000


def fetch_article_text(url: str) -> str | None:
    """Récupère la page d'un article (la redirection Google News est suivie
    automatiquement par requests) et en extrait le texte principal via
    trafilatura. Renvoie None sur tout échec — statut HTTP, erreur réseau,
    page bloquée (paywall/anti-bot), extraction vide.

    Utilise un `except Exception` volontairement large : contrairement au
    reste du fichier, cette fonction dialogue avec des pages web tierces
    dont les modes d'échec (HTML malformé, timeout, blocage) ne sont pas un
    contrat stable qu'on peut énumérer précisément — le contrat de cette
    fonction est justement de ne jamais lever, quoi qu'il arrive côté page
    externe.
    """
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        text = trafilatura.extract(resp.text)
    except Exception:
        return None
    if not text:
        return None
    return text[:ARTICLE_TEXT_MAX_CHARS]


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


def summarize_news_item(title: str, company_name: str, article_text: str | None) -> dict:
    """Génère un résumé/contexte (1-2 phrases en français) et une
    classification de sentiment pour l'entreprise via l'API Anthropic, en
    un seul appel. Renvoie {"summary": "", "sentiment": 0} sur tout échec
    (clé API absente, erreur réseau, réponse HTTP non-200, JSON malformé)
    — ne lève jamais, même justification que fetch_article_text (dialogue
    avec un service tiers dont on ne peut pas énumérer précisément tous
    les modes d'échec)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"summary": "", "sentiment": 0}

    if article_text:
        prompt = (
            f"Voici un article de presse concernant l'entreprise {company_name}, "
            f"titré « {title} ».\n\nContenu de l'article :\n{article_text}\n\n"
            "En 1 à 2 phrases en français, résume le contexte et l'enjeu "
            "principal de cet article pour cette entreprise. Sois factuel et "
            "neutre, sans donner de conseil d'investissement."
        )
    else:
        prompt = (
            f"Voici uniquement le titre d'un article de presse concernant "
            f"l'entreprise {company_name} : « {title} ».\n\n"
            "Le contenu de l'article n'est pas disponible. En 1 phrase en "
            "français, propose un contexte prudent et hypothétique à partir "
            "de ce titre seul (formule-le comme une supposition, par exemple "
            "« Cet article suggère que... », sans jamais affirmer de faits "
            "que le titre seul ne permet pas de confirmer)."
        )

    prompt += (
        "\n\nRéponds uniquement avec un objet JSON valide, sans texte "
        "autour, de la forme : "
        '{"summary": "...", "sentiment": -1|0|1} '
        "où sentiment vaut -1 si l'actu est plutôt défavorable pour "
        "l'entreprise, 0 si neutre ou mixte, 1 si plutôt favorable."
    )

    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 150,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = text.split("\n", 1)[1]
        parsed = json.loads(text)
        summary = str(parsed.get("summary", "")).strip()
        sentiment = parsed.get("sentiment", 0)
        if sentiment not in (-1, 0, 1):
            sentiment = 0
        return {"summary": summary, "sentiment": sentiment}
    except Exception:
        return {"summary": "", "sentiment": 0}


ANTHROPIC_MODEL_ANALYSIS = "claude-opus-5"

FINANCIAL_ANALYSIS_SYSTEM_PROMPT = """Tu es un analyste financier qui \
applique la méthode du Vernimmen (synthèse du diagnostic financier : \
rentabilité économique et financière, structure financière et \
solvabilité, analyse de la trésorerie et du free cash-flow, dynamique \
récente) à une entreprise cotée. Rédige une analyse structurée en \
français, factuelle, sans conseil d'investissement ni recommandation \
d'achat/vente, à partir des seules données fournies."""


def generate_financial_analysis(
    company_name: str, financial_context: str, ratios_summary: str,
) -> str | None:
    """Génère l'analyse financière via Claude Opus 5 (thinking adaptatif,
    effort élevé — tâche de raisonnement/rédaction, pas de classification
    simple). None si la clé API est absente ou en cas d'échec — jamais
    d'exception, même contrat que summarize_news_item."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    prompt = (
        f"Entreprise : {company_name}\n\n{financial_context}\n\n"
        f"Ratios déjà calculés (ne pas les recalculer, les interpréter) :\n"
        f"{ratios_summary}\n\n"
        "Rédige une analyse structurée avec ces sections, dans cet "
        "ordre : diagnostic global (2-3 phrases), structure financière "
        "et solvabilité, rentabilité économique et financière, analyse "
        "de la trésorerie et du free cash-flow, dynamique récente "
        "(dernier trimestre vs tendance), synthèse.\n\n"
        "Réponds uniquement avec un objet JSON valide, sans texte "
        "autour, de la forme : {\"analysis_html\": \"...\"} où la valeur "
        "est le texte de l'analyse en HTML, en utilisant uniquement "
        "les balises <h3>, <p>, <ul>, <li>, <strong> (aucune autre "
        "balise, aucun style inline, aucun script)."
    )

    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL_ANALYSIS,
                "max_tokens": 16000,
                "system": FINANCIAL_ANALYSIS_SYSTEM_PROMPT,
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        text_block = next(
            (b["text"] for b in data["content"] if b.get("type") == "text"), None
        )
        if text_block is None:
            return None
        text = text_block.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = text.split("\n", 1)[1]
        parsed = json.loads(text)
        return parsed.get("analysis_html")
    except Exception:
        return None


OUTPUT_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "indices.json"
)

INDICES_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "indices_history.json"
)
HISTORY_RETENTION_PER_TICKER = 730  # ~2 ans, une entrée par jour et par ticker


def load_indices_history(path=INDICES_HISTORY_PATH) -> list[dict]:
    """Historique quotidien du score composite par entreprise. []  si le
    fichier n'existe pas encore ou est corrompu — jamais d'exception."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError:
            return []


def append_indices_history(entries: list[dict], path=INDICES_HISTORY_PATH) -> list[dict]:
    """Ajoute les entrées du jour (une par entreprise) et retrimme chaque
    ticker indépendamment à HISTORY_RETENTION_PER_TICKER, pour que l'ajout
    d'une entreprise ne tronque jamais l'historique d'une autre."""
    history = load_indices_history(path)
    history.extend(entries)
    by_ticker: dict[str, list[dict]] = {}
    for entry in history:
        by_ticker.setdefault(entry["ticker"], []).append(entry)
    trimmed = []
    for ticker_entries in by_ticker.values():
        trimmed.extend(ticker_entries[-HISTORY_RETENTION_PER_TICKER:])
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(trimmed, fh, ensure_ascii=False, indent=2)
    return trimmed


RAPID_DROP_POINTS = 20   # même seuil que le volet Or
RAPID_DROP_DAYS = 5      # même fenêtre que le volet Or
NEAR_ENTRY_PCT = 5.0     # écart max (%) au repère d'entrée pour "conditions réunies"


def compute_company_alerts(
    ticker: str, composite: float, current_price: float | None,
    entry_price: float | None, previous_history: list[dict],
) -> list[dict]:
    """Alertes de franchissement de seuil pour une entreprise, à partir de
    son propre sous-historique (déjà filtré par ticker par l'appelant).
    Ne lève jamais d'exception ; renvoie toujours au moins une alerte
    (`info` neutre si rien ne se déclenche)."""
    today_str = datetime.today().strftime("%d/%m/%Y")
    alerts = []

    prev_composite = None
    if previous_history:
        try:
            prev_composite = previous_history[-1]["composite"]
        except (KeyError, TypeError):
            prev_composite = None

    if prev_composite is not None and prev_composite <= 15 < composite:
        alerts.append({
            "kind": "watch",
            "title": "Score composite a franchi +15",
            "detail": "Surveillance active enclenchée pour cette entreprise.",
            "date": today_str,
        })

    cutoff = datetime.today().date() - timedelta(days=RAPID_DROP_DAYS)
    recent = []
    for e in previous_history:
        try:
            entry_date = datetime.strptime(e["date"], "%Y-%m-%d").date()
            if entry_date >= cutoff:
                recent.append(e)
        except (ValueError, KeyError, TypeError):
            # Ignore entries with malformed dates or missing "date" key
            pass
    recent_composites = []
    for e in recent:
        try:
            recent_composites.append(e["composite"])
        except (KeyError, TypeError):
            # Ignore entries with a missing/malformed "composite" key
            pass
    if recent_composites:
        max_recent = max(recent_composites)
        drop = composite - max_recent
        if drop <= -RAPID_DROP_POINTS:
            alerts.append({
                "kind": "risque",
                "title": "Chute rapide du score composite",
                "detail": f"Repricing de {drop:+.1f} points en moins de {RAPID_DROP_DAYS} jours.",
                "date": today_str,
            })

    near_entry = (
        current_price is not None and entry_price is not None and entry_price > 0
        and abs(current_price - entry_price) / entry_price * 100 < NEAR_ENTRY_PCT
    )
    if composite > 15 and near_entry:
        alerts.append({
            "kind": "entree",
            "title": "Conditions d'entrée réunies",
            "detail": f"Score favorable, cours à moins de {NEAR_ENTRY_PCT:.0f}% du repère d'entrée.",
            "date": today_str,
        })

    if not alerts:
        alerts.append({
            "kind": "info",
            "title": "Pas de signal actif",
            "detail": "Aucune des conditions de veille, d'entrée ou de risque n'est réunie aujourd'hui.",
            "date": today_str,
        })

    return alerts

# Repli utilisé quand le WACC réel de l'entreprise (estimate_wacc, plus bas)
# n'a pas pu être calculé pour un run donné (donnée manquante : bêta, taux
# sans risque...). Voir Methodologie_Analyse_Indices.md, section
# "Coût du capital réel par entreprise (WACC)".
COST_OF_CAPITAL_PROXY = 8.0  # %

DCF_PROJECTION_YEARS = 5
DCF_GROWTH_FLOOR = -5.0      # % croissance FCF minimum projetée
DCF_GROWTH_CAP = 15.0        # % croissance FCF maximum projetée
DCF_TERMINAL_GROWTH = 2.0    # % croissance perpétuelle (valeur terminale)
DCF_MIN_DISCOUNT_SPREAD = 1.0  # points d'écart minimum entre le taux
                               # d'actualisation et DCF_TERMINAL_GROWTH, pour
                               # éviter une valeur terminale de Gordon
                               # dégénérée (négative ou déraisonnablement
                               # grande) quand un WACC calculé se retrouve
                               # trop bas (ex : taux sans risque français
                               # négatif comme en 2020, bêta faible/négatif).


def estimate_dcf_price(
    fcf: float, cagr_ebitda: float, net_debt: float, shares_outstanding: float,
    discount_rate_pct: float,
) -> float | None:
    """Prix par action implicite d'un DCF simplifié : projette le FCF actuel
    sur 5 ans au taux de croissance historique de l'EBITDA (plafonné entre
    -5% et +15%/an pour éviter d'extrapoler un chiffre bruité de façon
    absurde), actualise au coût du capital fourni par l'appelant (WACC de
    l'entreprise, ou COST_OF_CAPITAL_PROXY en repli), ajoute une valeur
    terminale à croissance perpétuelle de 2%. None si le FCF de départ
    n'est pas positif (DCF non pertinent), si le nombre d'actions est
    nul/inconnu, ou si le taux d'actualisation est trop proche/inférieur à
    la croissance terminale (Gordon growth dégénère vers une valeur
    négative ou déraisonnablement grande — voir DCF_MIN_DISCOUNT_SPREAD)."""
    if (
        _is_missing(fcf) or fcf <= 0 or not shares_outstanding or _is_missing(net_debt)
        or _is_missing(discount_rate_pct)
        or discount_rate_pct - DCF_TERMINAL_GROWTH < DCF_MIN_DISCOUNT_SPREAD
    ):
        return None
    growth = _clamp(cagr_ebitda, DCF_GROWTH_FLOOR, DCF_GROWTH_CAP) / 100
    discount_rate = discount_rate_pct / 100
    terminal_growth = DCF_TERMINAL_GROWTH / 100

    pv_fcf = 0.0
    fcf_t = fcf
    for year in range(1, DCF_PROJECTION_YEARS + 1):
        fcf_t = fcf_t * (1 + growth)
        pv_fcf += fcf_t / (1 + discount_rate) ** year

    terminal_value = fcf_t * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / (1 + discount_rate) ** DCF_PROJECTION_YEARS

    enterprise_value = pv_fcf + pv_terminal
    equity_value = enterprise_value - net_debt
    return equity_value / shares_outstanding


def estimate_asset_based_price(equity: float, shares_outstanding: float) -> float | None:
    """Valeur comptable par action (capitaux propres / actions en
    circulation) — approche patrimoniale simplifiée, sans réévaluation des
    actifs à la valeur de marché (hors périmètre v1). None si les capitaux
    propres sont négatifs ou nuls (base non significative comme plancher
    de valorisation) ou si le nombre d'actions est nul/inconnu."""
    if not shares_outstanding or _is_missing(equity) or equity <= 0:
        return None
    return equity / shares_outstanding


def estimate_multiple_based_price(
    current_price: float, current_ev_ebitda: float, avg_ev_ebitda_5y: float
) -> float | None:
    """Prix impliqué par un retour du multiple EV/EBITDA actuel à sa
    moyenne 5 ans, en supposant que le prix varie proportionnellement au
    multiple — approximation qui ignore l'effet de la dette nette fixe,
    documentée comme telle (cf. Methodologie_Analyse_Indices.md), plutôt
    que de reconstruire précisément EV et capitalisation. None si le
    multiple actuel est nul/absent."""
    if not current_ev_ebitda or _is_missing(current_ev_ebitda) or _is_missing(current_price) or _is_missing(avg_ev_ebitda_5y):
        return None
    return current_price * (avg_ev_ebitda_5y / current_ev_ebitda)


def estimate_fair_value(
    dcf_price: float | None, asset_price: float | None, multiple_price: float | None
) -> float | None:
    """Moyenne des méthodes de valorisation disponibles (DCF, actif net,
    multiples) — ignore celles indisponibles (None) ; None si aucune des
    3 n'est calculable."""
    prices = [p for p in (dcf_price, asset_price, multiple_price) if p is not None]
    return sum(prices) / len(prices) if prices else None


VALUATION_MARGIN_OF_SAFETY = 0.30   # ±30%, cohérent avec le seuil de prime/décote
                                     # significative déjà utilisé dans score_valorisation
TECHNICAL_EXIT_MARGIN = 0.20        # +20% au-dessus de la MM200, cohérent avec
                                     # PRICE_MOMENTUM_SCALE de score_dynamique_recente


def estimate_entry_exit_prices(fair_value: float | None, ma200: float | None) -> dict:
    """Combine repère de valorisation (juste valeur ± 30%) et repère
    technique (MM200 / MM200 × 1,20) en moyennant ceux disponibles.
    Renvoie {"entry": float | None, "exit": float | None} — None des deux
    côtés si ni la valorisation ni la MM200 ne sont disponibles."""
    entry_candidates = []
    exit_candidates = []
    if fair_value is not None:
        entry_candidates.append(fair_value * (1 - VALUATION_MARGIN_OF_SAFETY))
        exit_candidates.append(fair_value * (1 + VALUATION_MARGIN_OF_SAFETY))
    if ma200 is not None and not _is_missing(ma200):
        entry_candidates.append(ma200)
        exit_candidates.append(ma200 * (1 + TECHNICAL_EXIT_MARGIN))
    entry = sum(entry_candidates) / len(entry_candidates) if entry_candidates else None
    exit_price = sum(exit_candidates) / len(exit_candidates) if exit_candidates else None
    return {"entry": entry, "exit": exit_price}


FRED_RISK_FREE_SERIES = "IRLTLT01FRM156N"  # OAT 10 ans (France), FRED/OCDE, mensuel


def fetch_risk_free_rate() -> float | None:
    """Dernier taux OAT 10 ans publié (FRED, série IRLTLT01FRM156N,
    mensuelle avec ~1-2 mois de décalage) — taux sans risque pour le
    CAPM. None si la clé API FRED est absente (aucun appel réseau dans ce
    cas) ou en cas d'échec réseau/API : toutes les entreprises retombent
    alors sur COST_OF_CAPITAL_PROXY pour ce run."""
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return None
    try:
        start = (datetime.today() - timedelta(days=120)).strftime("%Y-%m-%d")
        resp = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": FRED_RISK_FREE_SERIES,
                "api_key": api_key,
                "file_type": "json",
                "observation_start": start,
            },
            timeout=15,
        )
        resp.raise_for_status()
        obs = [o for o in resp.json()["observations"] if o["value"] != "."]
        if not obs:
            return None
        return float(obs[-1]["value"])
    except Exception:
        return None


MARKET_RISK_PREMIUM = 5.0        # % prime de risque marché (hypothèse fixe)

SIZE_PREMIUM_BANDS = [
    (50_000_000_000, 0.0),
    (10_000_000_000, 0.5),
    (2_000_000_000, 1.5),
    (0, 3.0),
]


def _size_premium(market_cap: float) -> float:
    """Prime de taille (points ajoutés au coût des fonds propres) selon
    la capitalisation boursière — parcourt les bandes de la plus grande à
    la plus petite, renvoie la première dont le seuil est strictement
    dépassé."""
    for threshold, premium in SIZE_PREMIUM_BANDS:
        if market_cap > threshold:
            return premium
    return SIZE_PREMIUM_BANDS[-1][1]


def estimate_wacc(
    risk_free_rate: float | None,
    beta: float | None,
    market_cap: float | None,
    total_debt: float | None,
    tax_rate: float | None,
) -> float | None:
    """WACC par entreprise (CAPM + prime de taille, Vernimmen). None si une
    donnée nécessaire manque/est invalide — le repli sur
    COST_OF_CAPITAL_PROXY se fait chez l'appelant, pas ici. Ne lève jamais
    d'exception, même si une donnée yfinance est d'un type inattendu
    (ex : bêta remonté comme chaîne de caractères)."""
    if (
        risk_free_rate is None or _is_missing(risk_free_rate)
        or beta is None or _is_missing(beta)
        or market_cap is None or _is_missing(market_cap) or market_cap <= 0
        or total_debt is None or _is_missing(total_debt) or total_debt < 0
        or tax_rate is None or _is_missing(tax_rate)
    ):
        return None
    try:
        cost_of_equity = risk_free_rate + beta * MARKET_RISK_PREMIUM + _size_premium(market_cap)
        cost_of_debt_after_tax = DEBT_INTEREST_RATE_PROXY * (1 - tax_rate)
        total_capital = market_cap + total_debt
        equity_weight = market_cap / total_capital
        debt_weight = total_debt / total_capital
        return equity_weight * cost_of_equity + debt_weight * cost_of_debt_after_tax
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def estimate_valuation_targets(data: dict, cost_of_capital: float) -> dict:
    """Combine DCF, actif net et multiples en une juste valeur, puis en
    repères d'entrée/sortie. Toujours ces 3 clés en sortie, valeurs à None
    si non calculables (jamais d'exception). `cost_of_capital` est le taux
    d'actualisation du DCF (WACC de l'entreprise, ou COST_OF_CAPITAL_PROXY
    en repli — résolu par l'appelant)."""
    dcf_price = estimate_dcf_price(
        data["fcf"], data["cagr_ebitda"], data["net_debt"], data["shares_outstanding"],
        cost_of_capital,
    )
    asset_price = estimate_asset_based_price(data["equity"], data["shares_outstanding"])
    multiple_price = (
        estimate_multiple_based_price(
            data["current_price"], data["current_ev_ebitda"], data["avg_ev_ebitda_5y"]
        )
        if data["current_price"] is not None else None
    )
    fair_value = estimate_fair_value(dcf_price, asset_price, multiple_price)
    entry_exit = estimate_entry_exit_prices(fair_value, data["ma200"])
    return {
        "fair_value": fair_value,
        "entry_price": entry_exit["entry"],
        "exit_price": entry_exit["exit"],
    }


def load_previous_company_analyses() -> dict:
    """Lit le docs/indices.json du run précédent (déjà commité) pour en
    extraire, par ticker, l'analyse financière et la date de trimestre
    qu'elle couvre. {} si le fichier n'existe pas encore ou est
    illisible — jamais d'exception."""
    if not os.path.exists(OUTPUT_JSON_PATH):
        return {}
    try:
        with open(OUTPUT_JSON_PATH, encoding="utf-8") as fh:
            previous = json.load(fh)
        return {
            c["ticker"]: {
                "financial_analysis_html": c.get("financial_analysis_html"),
                "financial_analysis_quarter": c.get("financial_analysis_quarter"),
            }
            for c in previous.get("companies", [])
            if c.get("ticker")
        }
    except Exception:
        return {}


def build_company_entry(
    ticker: str, name: str, risk_free_rate: float | None, previous_analyses: dict,
) -> dict:
    data = fetch_company_financials(ticker)
    sector = data["sector"]

    market_cap = (
        data["current_price"] * data["shares_outstanding"]
        if data["current_price"] is not None else None
    )
    wacc = estimate_wacc(
        risk_free_rate, data["beta"], market_cap, data["total_debt"], data["tax_rate"]
    )
    cost_of_capital = wacc if wacc is not None else COST_OF_CAPITAL_PROXY

    previous = previous_analyses.get(ticker, {})
    current_quarter = data["latest_quarter_date"]
    quarter_unchanged = (
        current_quarter is not None
        and current_quarter == previous.get("financial_analysis_quarter")
    )
    if (quarter_unchanged or current_quarter is None) and previous.get("financial_analysis_html"):
        # Trimestre inchangé, ou date de trimestre indisponible ce run (panne
        # yfinance sur le trimestriel) : on garde l'analyse existante plutôt
        # que de la régénérer (coût quotidien illimité si la panne persiste)
        # ou de la remplacer par rien.
        financial_analysis_html = previous["financial_analysis_html"]
        financial_analysis_quarter = previous.get("financial_analysis_quarter")
    else:
        ratios_summary = (
            f"ROCE {data['roce']:.1f}%, ROE {data['roe']:.1f}%, "
            f"dette nette/EBITDA {data['net_debt_ebitda']:.1f}x, "
            f"ICR {data['icr']:.1f}x, CAGR CA {data['cagr_ca']:+.1f}%/an, "
            f"CAGR EBITDA {data['cagr_ebitda']:+.1f}%/an, "
            f"conversion FCF/EBITDA {data['fcf_conversion']:.0f}%, "
            f"coût du capital {cost_of_capital:.1f}%"
        )
        generated = generate_financial_analysis(
            name, data["financial_context"], ratios_summary
        )
        if generated is not None:
            financial_analysis_html = generated
            financial_analysis_quarter = current_quarter
        elif previous.get("financial_analysis_html"):
            # Échec de génération (clé API absente, panne réseau/API) : garder
            # l'ancienne analyse valide plutôt que l'écraser par None.
            financial_analysis_html = previous["financial_analysis_html"]
            financial_analysis_quarter = previous.get("financial_analysis_quarter")
        else:
            financial_analysis_html = None
            financial_analysis_quarter = current_quarter

    # Les news sont récupérées avant la construction des facteurs : le
    # facteur "Actualité récente" dépend du sentiment attaché à chaque
    # actu par fetch_news. Un échec total du flux RSS dégrade vers
    # news = [] (voir fetch_news / summarize_news_item, qui ne lèvent
    # jamais), ce qui fait à son tour retomber score_actualite_recente([])
    # sur son cas neutre — la dépendance se dégrade proprement de bout
    # en bout, sans faire perdre le score fondamental déjà calculable.
    try:
        news = fetch_news(name)
    except Exception as e:
        print(f"Erreur récupération news pour {name} : {e}")
        news = []

    factors = [
        score_rentabilite(data["roce"], data["roe"], cost_of_capital),
        score_structure_financiere(data["net_debt_ebitda"], data["icr"], sector),
        score_croissance(data["cagr_ca"], data["cagr_ebitda"]),
        score_generation_cash(data["fcf_conversion"]),
        score_valorisation(
            data["current_ev_ebitda"], data["avg_ev_ebitda_5y"],
            data["current_pe"], data["avg_pe_5y"], data["cagr_ebitda"],
        ),
        score_dynamique_recente(
            data["ecart_pct_ma200"], data["quarterly_yoy_growth_ca"], data["cagr_ca"],
        ),
        score_actualite_recente(news),
    ]
    composite = compute_composite(factors)

    valuation_targets = estimate_valuation_targets(data, cost_of_capital)

    return {
        "ticker": ticker,
        "name": name,
        "sector": sector,
        "sector_profile": sector_risk_profile(sector),
        "score": composite,
        "interpretation": interpret(composite),
        "factors": [
            {"name": f.name, "score": f.score, "weight": f.weight, "raw_value": f.raw_value}
            for f in factors
        ],
        "news": news,
        "current_price": data["current_price"],
        "fair_value": valuation_targets["fair_value"],
        "entry_price": valuation_targets["entry_price"],
        "exit_price": valuation_targets["exit_price"],
        "wacc": cost_of_capital,
        "financial_analysis_html": financial_analysis_html,
        "financial_analysis_quarter": financial_analysis_quarter,
    }


def _attach_alerts_and_update_history(companies: list[dict]) -> None:
    """Calcule les alertes de chaque entreprise à partir de son historique
    et enregistre le score du jour. Dégrade vers alerts=[] pour toutes les
    entreprises si l'historique est illisible/inscriptible — ne doit
    jamais faire échouer la publication du score déjà calculé."""
    for company in companies:
        company["alerts"] = []
    try:
        history = load_indices_history()
        today_str = datetime.today().strftime("%Y-%m-%d")
        new_entries = []
        for company in companies:
            ticker_history = [e for e in history if e["ticker"] == company["ticker"]]
            company["alerts"] = compute_company_alerts(
                company["ticker"], company["score"], company["current_price"],
                company["entry_price"], ticker_history,
            )
            new_entries.append({
                "date": today_str, "ticker": company["ticker"], "composite": company["score"],
            })
        append_indices_history(new_entries)
    except Exception as e:
        print(f"Erreur historique/alertes Indices : {e}")


def _compute_health_summary(companies: list[dict]) -> dict:
    """Résumé de complétude du run : combien d'entreprises attendues
    (COMPANIES) ont effectivement un résultat dans `companies`, et
    lesquelles manquent. Une entreprise qui lève une exception dans la
    boucle de main() est silencieusement absente de `companies` — sans
    ce résumé, une panne partielle (ex : 2 entreprises sur 5 disparues)
    ne serait visible qu'en comptant les lignes à la main."""
    expected_tickers = [c["ticker"] for c in COMPANIES]
    returned_tickers = {c["ticker"] for c in companies}
    missing_tickers = [t for t in expected_tickers if t not in returned_tickers]
    return {
        "expected": len(expected_tickers),
        "returned": len(companies),
        "missing_tickers": missing_tickers,
    }


def main():
    risk_free_rate = fetch_risk_free_rate()
    previous_analyses = load_previous_company_analyses()
    companies = []
    for company in COMPANIES:
        try:
            companies.append(
                build_company_entry(
                    company["ticker"], company["name"], risk_free_rate, previous_analyses,
                )
            )
        except Exception as e:
            print(f"Erreur pour {company['ticker']} ({company['name']}) : {e}")

    _attach_alerts_and_update_history(companies)

    payload = {
        "updated": datetime.today().strftime("%Y-%m-%d"),
        "companies": companies,
        "health": _compute_health_summary(companies),
    }

    os.makedirs(os.path.dirname(OUTPUT_JSON_PATH), exist_ok=True)
    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f"Données exportées vers : {OUTPUT_JSON_PATH}")
    for c in companies:
        print(f"  {c['ticker']:<8} {c['name']:<20} score {c['score']:+.1f}  ({c['interpretation']})")


if __name__ == "__main__":
    main()
