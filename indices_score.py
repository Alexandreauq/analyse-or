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
