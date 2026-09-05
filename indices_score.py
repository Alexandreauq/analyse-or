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
