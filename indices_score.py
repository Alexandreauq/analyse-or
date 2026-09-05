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

from dataclasses import dataclass


WEIGHTS = {
    "rentabilite": 0.30,
    "structure_financiere": 0.25,
    "croissance": 0.20,
    "generation_cash": 0.15,
    "valorisation": 0.10,
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
