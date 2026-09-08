"""
Score fondamental — indices boursiers
=======================================

Calcule un score composite par entreprise à partir de 5 ans de comptes
publiés (via yfinance), selon la méthodologie décrite dans
Methodologie_Analyse_Indices.md (synthèse Vernimmen : rentabilité
comptable, analyse du financement, coût du capital, pratique de
l'évaluation). Un seul run couvre plusieurs indices (CAC 40, DAX
aujourd'hui) : chaque entreprise de COMPANIES porte son propre champ
"index" (voir <INDICE>_COMPANIES + INDEX_NAMES) plutôt qu'un seul indice
par run.

Installation :
    pip install requests yfinance pandas
"""

import json
import math
import os
import smtplib
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from dateutil.relativedelta import relativedelta

try:
    import yfinance as yf
except ImportError:
    yf = None

import pandas as pd
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

CAC40_COMPANIES = [
    {"ticker": "MC.PA", "name": "LVMH"},
    {"ticker": "TTE.PA", "name": "TotalEnergies"},
    {"ticker": "SU.PA", "name": "Schneider Electric"},
    {"ticker": "SAN.PA", "name": "Sanofi"},
    {"ticker": "BN.PA", "name": "Danone"},
    # Lot 1 d'extension CAC 40 (10 entreprises, tickers Yahoo Finance
    # vérifiés individuellement — plusieurs diffèrent du mnémonique
    # Euronext naïf + suffixe .PA, ex : STMicroelectronics/Stellantis).
    {"ticker": "AIR.PA", "name": "Airbus"},
    {"ticker": "MT.PA", "name": "ArcelorMittal"},
    {"ticker": "OR.PA", "name": "L'Oréal"},
    {"ticker": "DG.PA", "name": "Vinci"},
    {"ticker": "RMS.PA", "name": "Hermès International"},
    {"ticker": "ENGI.PA", "name": "Engie"},
    {"ticker": "ORA.PA", "name": "Orange"},
    {"ticker": "STMPA.PA", "name": "STMicroelectronics"},
    {"ticker": "STLAP.PA", "name": "Stellantis"},
    {"ticker": "CA.PA", "name": "Carrefour"},
    # Lot 2 d'extension CAC 40 (10 entreprises), tickers Yahoo Finance
    # vérifiés individuellement comme le lot 1.
    {"ticker": "SAF.PA", "name": "Safran"},
    {"ticker": "LR.PA", "name": "Legrand"},
    {"ticker": "SGO.PA", "name": "Saint-Gobain"},
    {"ticker": "HO.PA", "name": "Thales"},
    {"ticker": "VIE.PA", "name": "Veolia Environnement"},
    {"ticker": "KER.PA", "name": "Kering"},
    {"ticker": "CAP.PA", "name": "Capgemini"},
    {"ticker": "RI.PA", "name": "Pernod Ricard"},
    {"ticker": "RNO.PA", "name": "Renault"},
    {"ticker": "ERF.PA", "name": "Eurofins Scientific"},
    # Lot 3 d'extension CAC 40 (9 entreprises).
    {"ticker": "EL.PA", "name": "EssilorLuxottica"},
    {"ticker": "PUB.PA", "name": "Publicis Groupe"},
    {"ticker": "DSY.PA", "name": "Dassault Systèmes"},
    {"ticker": "URW.PA", "name": "Unibail-Rodamco-Westfield"},
    {"ticker": "ENX.PA", "name": "Euronext"},
    {"ticker": "FGR.PA", "name": "Eiffage"},
    {"ticker": "BVI.PA", "name": "Bureau Veritas"},
    {"ticker": "EN.PA", "name": "Bouygues"},
    # Réintégrées après correction du vrai bug (voir
    # build_financial_narrative_context) : leur quarterly_financials
    # yfinance a bien 1 colonne mais sans ligne "Total Revenue" (juste des
    # lignes de nombre d'actions), ce qui levait un KeyError non rattrapé
    # et faisait échouer toute l'entreprise — pas un problème de fetch.
    {"ticker": "AI.PA", "name": "Air Liquide"},
    {"ticker": "ML.PA", "name": "Michelin"},
    {"ticker": "AC.PA", "name": "Accor"},
    # Banques et assurance (voir FINANCIAL_SECTOR_TICKERS) : méthodologie
    # adaptée (ROE, ratio de levier, P/E + P/B) plutôt que la grille
    # standard, EBITDA/EBIT étant indisponibles chez yfinance pour ces
    # entreprises. 37 entreprises au total désormais — le reliquat par
    # rapport aux 40 constituants du CAC 40 n'a pas été audité valeur par
    # valeur (composition de l'indice qui évolue dans le temps).
    {"ticker": "BNP.PA", "name": "BNP Paribas"},
    {"ticker": "GLE.PA", "name": "Société Générale"},
    {"ticker": "ACA.PA", "name": "Crédit Agricole"},
    {"ticker": "CS.PA", "name": "AXA"},
]

# Lot 1 DAX (10 entreprises, toutes non-financières — même méthode que le
# lot 1 CAC 40 : banques/assurances traitées à part dans un lot ultérieur).
# Tickers Yahoo Finance vérifiés individuellement (suffixe .DE = Xetra) ;
# VOW3/HEN3 sont les actions de préférence (Vorzugsaktien), pas un
# mnémonique inventé — c'est ce que Yahoo Finance référence réellement
# pour Volkswagen et Henkel.
DAX_COMPANIES = [
    {"ticker": "SAP.DE", "name": "SAP"},
    {"ticker": "SIE.DE", "name": "Siemens"},
    {"ticker": "DTE.DE", "name": "Deutsche Telekom"},
    {"ticker": "VOW3.DE", "name": "Volkswagen"},
    {"ticker": "MBG.DE", "name": "Mercedes-Benz Group"},
    {"ticker": "BAS.DE", "name": "BASF"},
    {"ticker": "BAYN.DE", "name": "Bayer"},
    {"ticker": "SHL.DE", "name": "Siemens Healthineers"},
    {"ticker": "ADS.DE", "name": "Adidas"},
    {"ticker": "HEN3.DE", "name": "Henkel"},
    # Lot 2 DAX (10 entreprises), tickers Yahoo Finance vérifiés
    # individuellement comme le lot 1 — attention particulière à MRK.DE
    # (Merck KGaA, l'entreprise allemande) qui ne doit pas être confondue
    # avec MRK (Merck & Co, l'entreprise américaine sur le NYSE).
    {"ticker": "DHL.DE", "name": "DHL Group"},
    {"ticker": "IFX.DE", "name": "Infineon Technologies"},
    {"ticker": "MRK.DE", "name": "Merck KGaA"},
    {"ticker": "FRE.DE", "name": "Fresenius"},
    {"ticker": "CON.DE", "name": "Continental"},
    {"ticker": "RWE.DE", "name": "RWE"},
    {"ticker": "EOAN.DE", "name": "E.ON"},
    {"ticker": "RHM.DE", "name": "Rheinmetall"},
    {"ticker": "BEI.DE", "name": "Beiersdorf"},
    {"ticker": "MTX.DE", "name": "MTU Aero Engines"},
    # Banques et assurances allemandes (voir FINANCIAL_SECTOR_TICKERS) :
    # tickers Yahoo Finance vérifiés individuellement comme le reste du DAX.
    {"ticker": "DBK.DE", "name": "Deutsche Bank"},
    {"ticker": "CBK.DE", "name": "Commerzbank"},
    {"ticker": "ALV.DE", "name": "Allianz"},
    {"ticker": "MUV2.DE", "name": "Munich Re"},
    # Lot 3 DAX (10 entreprises), tickers Yahoo Finance vérifiés
    # individuellement comme les lots précédents. Vonovia (foncière) et
    # Deutsche Börse (opérateur de marché) inclus comme entreprises
    # standards malgré un profil de bilan atypique — même traitement que
    # Unibail-Rodamco-Westfield/Euronext dans le CAC 40.
    {"ticker": "VNA.DE", "name": "Vonovia"},
    {"ticker": "DB1.DE", "name": "Deutsche Börse"},
    {"ticker": "SY1.DE", "name": "Symrise"},
    {"ticker": "ZAL.DE", "name": "Zalando"},
    {"ticker": "DTG.DE", "name": "Daimler Truck"},
    {"ticker": "QIA.DE", "name": "Qiagen"},
    {"ticker": "BNR.DE", "name": "Brenntag"},
    {"ticker": "HEI.DE", "name": "Heidelberg Materials"},
    # Lot 4 DAX (dernier lot pour compléter la couverture), tickers Yahoo
    # Finance vérifiés individuellement. Hannover Rück est une assurance
    # (voir FINANCIAL_SECTOR_TICKERS), les 4 autres sont standards.
    {"ticker": "BMW.DE", "name": "BMW"},
    {"ticker": "ENR.DE", "name": "Siemens Energy"},
    {"ticker": "G1A.DE", "name": "GEA Group"},
    {"ticker": "HNR1.DE", "name": "Hannover Rück"},
    # Mise à jour composition DAX (2026-09-08, vérifiée via Wikipedia) :
    # Porsche AG, Sartorius et Covestro sont sortis de l'indice ; Fresenius
    # Medical Care, Porsche SE et Scout24 y sont entrés. Airbus (AIR.PA),
    # également entrée dans le DAX, n'est PAS dupliquée ici : déjà suivie
    # côté CAC40 (voir CAC40_COMPANIES) — un choix explicite de
    # l'utilisateur pour éviter un double suivi/double alerte sur la même
    # entreprise. Le DAX est donc à 39/40 par ce choix, pas par erreur.
    {"ticker": "FME.DE", "name": "Fresenius Medical Care"},
    {"ticker": "PAH3.DE", "name": "Porsche SE"},
    {"ticker": "G24.DE", "name": "Scout24"},
]

# Chaque entreprise de COMPANIES porte son propre indice ("index" ajouté
# ici, pas dans CAC40_COMPANIES/DAX_COMPANIES eux-mêmes, pour garder ces
# listes lisibles) — remplace l'ancienne constante unique INDEX_KEY,
# insuffisante dès qu'un 2e indice existe. INDEX_NAMES : nom affiché par
# indice, complété au fil de l'ajout de nouveaux indices.
COMPANIES = (
    [{**c, "index": "CAC40"} for c in CAC40_COMPANIES]
    + [{**c, "index": "DAX"} for c in DAX_COMPANIES]
)
INDEX_NAMES = {"CAC40": "CAC 40", "DAX": "DAX"}

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

# Correction manuelle par ticker : certaines entreprises n'ont pas de
# "sector" renseigné du tout chez yfinance (info.get("sector") vaut None),
# ce qui les fait retomber sur le profil "standard" par défaut plutôt que
# leur vrai profil de risque — pas un secteur mal classé dans
# SECTOR_PROFILES, une vraie absence de donnée à la source. Complété au
# fil des cas rencontrés en production, pas une liste exhaustive à priori.
SECTOR_OVERRIDE_BY_TICKER = {
    "MT.PA": "Basic Materials",  # ArcelorMittal (sidérurgie, cyclique)
}

# Entreprises à double classe d'actions (ordinaires + préférence) dont le
# "sharesOutstanding" yfinance ne compte qu'une seule classe, alors que les
# données financières (résultat net, FCF...) couvrent l'entreprise entière
# — fausse toute valorisation par action calculée à la main (repéré via
# Volkswagen : juste valeur DCF à 494€ pour un cours à 81€, soit ~2,4x le
# cours réel). Confirmé via un diagnostic dédié comparant cours ×
# sharesOutstanding à marketCap (Yahoo) sur les 79 entreprises du run :
# ces 5 tickers montrent un écart net (ratio 0,30-0,50 — P911.DE tombe
# quasiment pile sur 0,50, cohérent avec ses 455,5M actions ordinaires +
# 455,5M actions de préférence = 911M au total, connu publiquement), les
# 74 autres sont cohérentes à <0,01% près. Pour ces 5 précisément,
# marketCap/cours est fiable — remplace sharesOutstanding. Volontairement
# une liste explicite plutôt qu'une règle automatique pour toutes les
# entreprises : Stellantis (STLAP.PA) montre l'écart inverse au même
# diagnostic (c'est marketCap qui est l'outlier là, sharesOutstanding est
# juste), et Michelin (ML.PA) a un marketCap Yahoo carrément à zéro — une
# règle générale aurait dégradé ces deux-là au lieu de les corriger.
SHARES_OUTSTANDING_FROM_MARKET_CAP_TICKERS = {
    "VOW3.DE",  # Volkswagen
    "HEN3.DE",  # Henkel
    "MRK.DE",   # Merck KGaA
}

# Banques et assurances françaises (BNP Paribas, Société Générale, Crédit
# Agricole, AXA) et allemandes (Deutsche Bank, Commerzbank, Allianz,
# Munich Re) : yfinance n'expose ni EBITDA, ni (pour les banques) EBIT
# dans leurs comptes — vérifié via un diagnostic dédié pour les 4
# françaises ; les 4 allemandes n'ont pas été rediagnostiquées
# individuellement mais suivent le même modèle économique (établissement
# financier, pas d'EBITDA/EBIT comptable) — à surveiller au premier run
# réel plutôt qu'une certitude absolue comme pour les françaises. Ces
# notions n'ont de toute façon pas le même sens pour un établissement
# financier, dont le "chiffre d'affaires" est un produit net bancaire et
# non un résultat d'exploitation classique avec amortissements. Ces
# entreprises passent par extract_ratios_financial et les fonctions
# score_*_financiere (ROE, ratio de levier, conversion cash, P/E + P/B)
# plutôt que par la méthodologie standard — voir aussi le badge "Profil
# financier" côté frontend (docs/index.html), affiché pour que cette
# différence de méthodologie soit visible des utilisateurs.
FINANCIAL_SECTOR_TICKERS = {
    "BNP.PA", "GLE.PA", "ACA.PA", "CS.PA",
    "DBK.DE", "CBK.DE", "ALV.DE", "MUV2.DE", "HNR1.DE",
}


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


# --- Variantes "profil financier" (banques, assurances) -------------------
#
# yfinance n'expose ni EBITDA, ni (pour les banques) EBIT dans les comptes
# de ces entreprises (vérifié via un diagnostic dédié sur BNP.PA/GLE.PA/
# ACA.PA/CS.PA, pas une lacune ponctuelle) — et ces notions n'ont de toute
# façon pas le même sens pour un établissement financier, dont le "chiffre
# d'affaires" est un produit net bancaire et non un résultat d'exploitation
# classique avec amortissements. Les 4 fonctions ci-dessous remplacent
# score_rentabilite / score_structure_financiere / score_generation_cash /
# score_valorisation pour les tickers de FINANCIAL_SECTOR_TICKERS, en
# réutilisant les indicateurs standards du secteur. Croissance, dynamique
# récente et actualité récente restent inchangées (indépendantes d'EBITDA).

def score_rentabilite_financiere(roe: float, cost_of_capital: float) -> FactorResult:
    """ROE (résultat net / capitaux propres) à la place du ROCE : pour un
    établissement financier, le levier fait partie intégrante du modèle
    économique plutôt qu'un effet à isoler — même logique d'écart au coût
    du capital que score_rentabilite, appliquée au ROE."""
    spread = roe - cost_of_capital
    score = _clamp((spread / ROCE_SPREAD_SCALE) * 10)
    return FactorResult(
        "Rentabilité / création de valeur",
        score,
        WEIGHTS["rentabilite"],
        f"ROE {roe:.1f}% vs coût du capital {cost_of_capital:.1f}% (profil financier)",
    )


LEVERAGE_RATIO_COMFORTABLE = 6.0   # % capitaux propres/actif total jugé confortable
LEVERAGE_RATIO_RISKY = 3.0         # % proche du minimum réglementaire indicatif (ratio
                                    # de levier Bâle III, 3%) — seuil de vigilance, pas
                                    # une lecture réglementaire précise (CET1 réel non
                                    # exposé par yfinance)


def _score_capital_ratio(ratio: float, comfortable: float, risky: float) -> float:
    """+10 au ratio confortable et au-delà, -10 au seuil risqué et en
    dessous, linéaire entre les deux — inverse de _score_leverage : ici,
    plus le ratio capitaux propres/actif est élevé, plus c'est solide."""
    if ratio <= risky:
        return -10.0
    if ratio >= comfortable:
        return 10.0
    return -10.0 + 20.0 * (ratio - risky) / (comfortable - risky)


def score_structure_financiere_bancaire(leverage_ratio: float) -> FactorResult:
    """Capitaux propres/actif total, l'indicateur de solidité standard
    pour une banque/assurance — remplace dette nette/EBITDA + ICR, qui
    supposent un EBITDA et des frais financiers isolables absents ici."""
    score = _score_capital_ratio(leverage_ratio, LEVERAGE_RATIO_COMFORTABLE, LEVERAGE_RATIO_RISKY)
    return FactorResult(
        "Structure financière / solvabilité",
        score,
        WEIGHTS["structure_financiere"],
        f"Capitaux propres/actif total {leverage_ratio:.1f}% (seuil confort "
        f"{LEVERAGE_RATIO_COMFORTABLE:.0f}%, vigilance sous {LEVERAGE_RATIO_RISKY:.0f}%) "
        f"— profil financier",
    )


def score_croissance_financiere(cagr_ca: float, cagr_net_income: float) -> FactorResult:
    """CAGR chiffre d'affaires et résultat net (remplace CAGR EBITDA,
    indisponible) — réutilise le calcul de score_croissance tel quel, seul
    le libellé change."""
    base = score_croissance(cagr_ca, cagr_net_income)
    return FactorResult(
        base.name, base.score, base.weight,
        f"CAGR CA {cagr_ca:+.1f}%/an, CAGR résultat net {cagr_net_income:+.1f}%/an "
        f"(5 ans, profil financier)",
    )


CASH_CONVERSION_FINANCIAL_NEUTRAL = 80.0   # % OCF/résultat net jugé neutre
CASH_CONVERSION_FINANCIAL_SCALE = 40.0     # échelle volontairement large (voir docstring)


def score_generation_cash_financiere(cash_conversion: float) -> FactorResult:
    """Flux de trésorerie opérationnel / résultat net (%), en repli du
    FCF/EBITDA (EBITDA indisponible). Échelle nettement plus large que la
    version standard (40 points contre 5) : l'OCF d'une banque encaisse
    les variations d'encours de crédits/dépôts, bien plus volatiles d'un
    exercice à l'autre que pour une entreprise non financière — signal à
    interpréter avec prudence, pondération inchangée (12%) mais amplitude
    de score volontairement amortie."""
    score = _clamp((cash_conversion - CASH_CONVERSION_FINANCIAL_NEUTRAL) / CASH_CONVERSION_FINANCIAL_SCALE)
    return FactorResult(
        "Génération de cash",
        score,
        WEIGHTS["generation_cash"],
        f"OCF/résultat net {cash_conversion:.0f}% (profil financier, signal volatil)",
    )


def score_valorisation_financiere(
    current_pe: float, avg_pe_5y: float,
    current_pb: float, avg_pb_5y: float,
    cagr_net_income: float,
) -> FactorResult:
    """P/E et P/B (Price/Book, standard pour valoriser une banque/
    assurance) comparés à leur moyenne 5 ans — remplace EV/EBITDA
    (indisponible) par P/B, garde P/E."""
    pe_score = _premium_score(current_pe, avg_pe_5y, cagr_net_income)
    pb_score = _premium_score(current_pb, avg_pb_5y, cagr_net_income)
    score = _clamp((pe_score + pb_score) / 2)
    return FactorResult(
        "Valorisation relative",
        score,
        WEIGHTS["valorisation"],
        f"PER {current_pe:.1f}x (moy. 5 ans {avg_pe_5y:.1f}x) — "
        f"P/B {current_pb:.1f}x (moy. 5 ans {avg_pb_5y:.1f}x) — profil financier",
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

NEWS_IMPORTANCE_LEVELS = ("mineure", "notable", "majeure")
# Poids par niveau d'importance pour score_actualite_recente : une actu
# majeure très négative doit pouvoir faire basculer le facteur à elle
# seule, même entourée de plusieurs actus mineures neutres — une moyenne
# simple diluait ça (une actu ultra importante pesait pareil qu'une
# mention mineure). Valeurs de départ, à ajuster une fois de vrais
# exemples observés en production.
NEWS_IMPORTANCE_WEIGHTS = {"mineure": 1.0, "notable": 2.0, "majeure": 4.0}


def score_actualite_recente(news_items: list[dict]) -> FactorResult:
    """Moyenne pondérée du sentiment des actus datées de moins de 14
    jours — poids par importance (voir NEWS_IMPORTANCE_WEIGHTS), une actu
    majeure pèse jusqu'à 4x plus qu'une actu mineure plutôt qu'un poids
    égal qui la diluerait au milieu de plusieurs actus mineures neutres.
    Si toutes les actus sont "mineure" (poids 1.0 chacune), ça reproduit
    exactement l'ancienne moyenne simple. Mise à l'échelle -10/+10.
    Neutre (0.0) si aucune actu récente exploitable — ni erreur, ni
    biais optimiste/pessimiste par défaut."""
    cutoff = datetime.now() - timedelta(days=NEWS_SENTIMENT_WINDOW_DAYS)
    recent = []
    for item in news_items:
        try:
            item_date = datetime.strptime(item["date"], "%Y-%m-%d")
        except (ValueError, TypeError, KeyError):
            continue
        if item_date >= cutoff:
            recent.append(item)
    if not recent:
        return FactorResult(
            "Actualité récente", 0.0, WEIGHTS["actualite_recente"],
            "Aucune actualité récente exploitable",
        )
    weighted_sum = 0.0
    total_weight = 0.0
    for item in recent:
        weight = NEWS_IMPORTANCE_WEIGHTS.get(item.get("importance", "mineure"), 1.0)
        weighted_sum += item.get("sentiment", 0) * weight
        total_weight += weight
    avg_sentiment = weighted_sum / total_weight if total_weight else 0.0
    score = _clamp(avg_sentiment * 10)
    majeure_count = sum(1 for item in recent if item.get("importance") == "majeure")
    majeure_note = f", dont {majeure_count} majeure(s)" if majeure_count else ""
    return FactorResult(
        "Actualité récente", score, WEIGHTS["actualite_recente"],
        f"Ton moyen pondéré des {len(recent)} actualités récentes : {avg_sentiment:+.2f}{majeure_note}",
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


def _try_get_row(df, *aliases):
    """Comme get_row, mais renvoie None au lieu de lever KeyError si aucun
    alias ne correspond — pour une ligne réellement optionnelle (EBITDA/
    EBIT chez un établissement financier, où la notion n'existe pas),
    à distinguer d'une ligne attendue mais manquante par accident."""
    try:
        return get_row(df, *aliases)
    except KeyError:
        return None


def _get_row_or_nan(df, *aliases):
    """Comme _try_get_row, mais renvoie une ligne entièrement NaN (alignée
    sur les colonnes de df) plutôt que None quand aucun alias ne
    correspond — pour une ligne consommée ensuite via l'indexation
    [col]/_safe_value(...), qui suppose un objet indexable comme une
    vraie ligne yfinance plutôt qu'un None à gérer à chaque site d'appel.
    Cas rencontré en production : E.ON (EOAN.DE) n'a aucune ligne "Total
    Debt" ni aucun équivalent (ni "Long Term Debt", ni "Current Debt") —
    un vrai trou de données, pas un alias manquant à ajouter."""
    row = _try_get_row(df, *aliases)
    if row is not None:
        return row
    return pd.Series(float("nan"), index=df.columns)


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

    total_debt = _get_row_or_nan(balance_sheet, "Total Debt")
    cash = get_row(balance_sheet, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
    equity = get_row(balance_sheet, "Stockholders Equity", "Common Stock Equity")

    op_cash_flow = get_row(cashflow, "Operating Cash Flow")
    # Replis en cascade : "Net PPE Purchase And Sale" (ex : Veolia
    # Environnement, pas de ligne "Capital Expenditure" isolée — nette les
    # cessions d'immobilisations contre les achats, proche du capex pur
    # tant que les cessions restent marginales) puis "Net Investment
    # Properties Purchase And Sale" (ex : Vonovia — une foncière investit
    # en achetant des immeubles de placement, pas des PPE industrielles ;
    # même convention de signe négatif pour les deux replis).
    capex = get_row(
        cashflow, "Capital Expenditure", "Net PPE Purchase And Sale",
        "Net Investment Properties Purchase And Sale",
    )

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

    # FCF lissé sur la même fenêtre que le CAGR (recent_cols, 1-2 exercices)
    # — utilisé comme point de départ du DCF à la place de `fcf` (le seul
    # dernier exercice) pour les entreprises cycliques, dont le FCF récent
    # peut être en haut ou en bas de cycle (voir estimate_valuation_targets).
    # Repli sur `fcf` si la fenêtre n'a aucun exercice exploitable.
    fcf_by_recent_year = []
    for col in recent_cols:
        ocf_value = _safe_value(op_cash_flow, col)
        capex_value = _safe_value(capex, col)
        if not _is_missing(ocf_value) and not _is_missing(capex_value):
            fcf_by_recent_year.append(ocf_value + capex_value)
    fcf_normalized = (
        sum(fcf_by_recent_year) / len(fcf_by_recent_year) if fcf_by_recent_year else fcf
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
        "fcf_normalized": fcf_normalized,
        "net_debt": net_debt_latest,
        "equity": equity_latest,
        "tax_rate": tax_rate[latest],
        "total_debt": total_debt_latest,
    }


def extract_ratios_financial(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding: float) -> dict:
    """Variante d'extract_ratios pour les banques/assurances de
    FINANCIAL_SECTOR_TICKERS : ni EBITDA ni (pour les banques) EBIT
    n'existent dans leurs comptes yfinance — voir le commentaire sur
    FINANCIAL_SECTOR_TICKERS. Calcule ROE, ratio de levier (capitaux
    propres/actif total), conversion cash (OCF/résultat net), P/E et P/B
    plutôt que ROCE/dette nette-EBITDA/ICR/FCF-EBITDA/EV-EBITDA.

    fcf/cagr_ebitda/net_debt/current_ev_ebitda/avg_ev_ebitda_5y sont tout
    de même présents dans le dict renvoyé, à des valeurs neutres (0.0) :
    estimate_valuation_targets() y accède sans condition pour toutes les
    entreprises, et ces valeurs neutres désactivent proprement le DCF et
    la valorisation par multiple EV/EBITDA via leur garde-fou déjà
    existant (fcf <= 0 -> None, current_ev_ebitda == 0 -> None) — la juste
    valeur repose alors uniquement sur l'approche patrimoniale (valeur
    comptable par action), une ancre usuelle pour ce secteur, plutôt que
    de construire un DCF avec un FCF ou une croissance d'EBITDA qui
    n'existent pas."""
    years_cols = list(financials.columns)
    n_years = len(years_cols)
    latest = years_cols[0]

    revenue = get_row(financials, "Total Revenue", "Operating Revenue")
    net_income = get_row(financials, "Net Income", "Net Income Common Stockholders")
    tax_rate = get_row(financials, "Tax Rate For Calcs")

    total_assets = get_row(balance_sheet, "Total Assets")
    equity = get_row(balance_sheet, "Stockholders Equity", "Common Stock Equity")
    total_debt = _get_row_or_nan(balance_sheet, "Total Debt")

    op_cash_flow = get_row(cashflow, "Operating Cash Flow")

    equity_latest = _safe_value(equity, latest)
    total_assets_latest = _safe_value(total_assets, latest)
    total_debt_latest = _safe_value(total_debt, latest)
    net_income_latest = _safe_value(net_income, latest)
    op_cash_flow_latest = _safe_value(op_cash_flow, latest)

    roe = (
        (net_income_latest / equity_latest) * 100
        if equity_latest and not _is_missing(equity_latest) and not _is_missing(net_income_latest)
        else 0.0
    )
    leverage_ratio = (
        (equity_latest / total_assets_latest) * 100
        if total_assets_latest and not _is_missing(total_assets_latest) and not _is_missing(equity_latest)
        else 0.0
    )
    cash_conversion = (
        (op_cash_flow_latest / net_income_latest) * 100
        if net_income_latest and not _is_missing(net_income_latest) and not _is_missing(op_cash_flow_latest)
        else 0.0
    )

    smoothing_window = 2 if n_years >= 4 else 1
    recent_cols = years_cols[:smoothing_window]
    old_cols = years_cols[-smoothing_window:]
    cagr_span = n_years - smoothing_window
    cagr_ca = _cagr(
        _window_average(revenue, old_cols), _window_average(revenue, recent_cols), cagr_span
    )
    cagr_net_income = _cagr(
        _window_average(net_income, old_cols), _window_average(net_income, recent_cols), cagr_span
    )

    pe_by_year, pb_by_year = [], []
    for col in years_cols:
        price = closes_by_year.get(col)
        equity_value = _safe_value(equity, col)
        if (
            price is None
            or not net_income[col] or _is_missing(net_income[col])
            or not equity_value or _is_missing(equity_value)
        ):
            continue
        market_cap = price * shares_outstanding
        pe_by_year.append(market_cap / net_income[col])
        pb_by_year.append(market_cap / equity_value)

    current_pe = pe_by_year[0] if pe_by_year else 0.0
    avg_pe_5y = sum(pe_by_year) / len(pe_by_year) if pe_by_year else 0.0
    current_pb = pb_by_year[0] if pb_by_year else 0.0
    avg_pb_5y = sum(pb_by_year) / len(pb_by_year) if pb_by_year else 0.0

    return {
        "roe": roe,
        "leverage_ratio": leverage_ratio,
        "cash_conversion": cash_conversion,
        "cagr_ca": cagr_ca,
        "cagr_net_income": cagr_net_income,
        "current_pe": current_pe,
        "avg_pe_5y": avg_pe_5y,
        "current_pb": current_pb,
        "avg_pb_5y": avg_pb_5y,
        "equity": equity_latest,
        "tax_rate": tax_rate[latest],
        "total_debt": total_debt_latest,
        # Valeurs neutres pour désactiver proprement DCF / multiple EV-EBITDA
        # dans estimate_valuation_targets (voir docstring ci-dessus).
        "fcf": 0.0,
        "fcf_normalized": 0.0,
        "cagr_ebitda": 0.0,
        "net_debt": 0.0,
        "current_ev_ebitda": 0.0,
        "avg_ev_ebitda_5y": 0.0,
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
    # _try_get_row (pas get_row) pour EBITDA/EBIT : absentes des comptes
    # yfinance des banques/assurances (voir FINANCIAL_SECTOR_TICKERS) — pas
    # une panne, ces notions n'existent pas pour un établissement financier.
    ebitda = _try_get_row(financials, "EBITDA", "Normalized EBITDA")
    ebit = _try_get_row(financials, "EBIT", "Operating Income", "Total Operating Income As Reported")
    net_income = get_row(financials, "Net Income", "Net Income Common Stockholders")
    equity = get_row(balance_sheet, "Stockholders Equity", "Common Stock Equity")
    total_debt = _get_row_or_nan(balance_sheet, "Total Debt")
    cash = get_row(balance_sheet, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
    op_cash_flow = get_row(cashflow, "Operating Cash Flow")
    # Replis en cascade : "Net PPE Purchase And Sale" (ex : Veolia
    # Environnement, pas de ligne "Capital Expenditure" isolée — nette les
    # cessions d'immobilisations contre les achats, proche du capex pur
    # tant que les cessions restent marginales) puis "Net Investment
    # Properties Purchase And Sale" (ex : Vonovia — une foncière investit
    # en achetant des immeubles de placement, pas des PPE industrielles ;
    # même convention de signe négatif pour les deux replis).
    capex = get_row(
        cashflow, "Capital Expenditure", "Net PPE Purchase And Sale",
        "Net Investment Properties Purchase And Sale",
    )

    def _fmt(value) -> str:
        return "non disponible" if value is None or _is_missing(value) else f"{value:,.0f}"

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
        ebitda_value = ebitda[col] if ebitda is not None else None
        ebit_value = ebit[col] if ebit is not None else None
        lines.append(
            f"- {col.date() if hasattr(col, 'date') else col} : CA {_fmt(revenue[col])}, "
            f"EBITDA {_fmt(ebitda_value)}, EBIT {_fmt(ebit_value)}, "
            f"résultat net {_fmt(net_income[col])}, capitaux propres {_fmt(equity_value)}, "
            f"dette nette {_fmt(net_debt)}, FCF {_fmt(fcf)}"
        )

    lines.append("\nDerniers trimestres publiés (le plus récent en premier) :")
    # `len(...columns)` vérifie seulement qu'il y a des colonnes, pas que la
    # ligne "Total Revenue" existe dedans — insuffisant pour Air Liquide,
    # Michelin, Accor : yfinance leur renvoie un quarterly_financials avec
    # 1 colonne mais seulement des lignes de nombre d'actions (Diluted/Basic
    # Average Shares), jamais de chiffre d'affaires. Un vrai trou de données
    # trimestrielles côté source, pas une panne — get_row lèverait sinon un
    # KeyError non rattrapé qui ferait échouer toute l'entreprise.
    quarterly_revenue = None
    if len(quarterly_financials.columns):
        try:
            quarterly_revenue = get_row(quarterly_financials, "Total Revenue", "Operating Revenue")
        except KeyError:
            quarterly_revenue = None
    if quarterly_revenue is not None:
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


MIN_STATEMENT_ROWS = 10       # un relevé annuel sain a ~40-50 lignes ; un
                              # relevé dégradé (observé en production sur
                              # Air Liquide/Michelin/Accor) n'en a que 2
                              # ('Diluted/Basic Average Shares'), sans
                              # lever d'exception
FETCH_RETRY_ATTEMPTS = 3
FETCH_RETRY_DELAY_SECONDS = 2.0


def _fetch_statement_with_retry(ticker: str, attribute_name: str):
    """Certains appels yfinance renvoient occasionnellement un relevé
    dégradé (quasi vide) sans lever d'exception, plutôt vu sur les
    tickers traités plus tard dans la boucle des entreprises (rate-
    limiting probable de Yahoo) — un diagnostic isolé sur ces mêmes
    tickers, hors de la boucle complète, renvoyait les données
    complètes : pas un vrai trou de données à la source. Un nouveau
    `yf.Ticker(...)` à chaque tentative (pas le même objet réutilisé)
    pour éviter de retomber sur un résultat mis en cache par yfinance."""
    statement = None
    for attempt in range(FETCH_RETRY_ATTEMPTS):
        statement = getattr(yf.Ticker(ticker), attribute_name)
        if len(statement.index) >= MIN_STATEMENT_ROWS:
            return statement
        if attempt < FETCH_RETRY_ATTEMPTS - 1:
            time.sleep(FETCH_RETRY_DELAY_SECONDS)
    return statement


def fetch_company_financials(ticker: str) -> dict:
    if yf is None:
        raise RuntimeError("yfinance n'est pas installé (pip install yfinance)")
    t = yf.Ticker(ticker)
    financials = _fetch_statement_with_retry(ticker, "financials")
    balance_sheet = _fetch_statement_with_retry(ticker, "balance_sheet")
    cashflow = _fetch_statement_with_retry(ticker, "cashflow")
    quarterly_financials = t.quarterly_financials
    info = t.info
    shares_outstanding = info.get("sharesOutstanding") or 0.0
    beta = info.get("beta")
    sector = info.get("sector")
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

    if ticker in SHARES_OUTSTANDING_FROM_MARKET_CAP_TICKERS:
        market_cap = info.get("marketCap")
        if market_cap and current_price:
            shares_outstanding = market_cap / current_price

    is_financial = ticker in FINANCIAL_SECTOR_TICKERS
    if is_financial:
        ratios = extract_ratios_financial(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding)
    else:
        ratios = extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding)
    ratios["is_financial"] = is_financial
    ratios["sector"] = SECTOR_OVERRIDE_BY_TICKER.get(ticker) or sector
    ratios["ecart_pct_ma200"] = ecart_pct_ma200
    try:
        # Même précaution que build_financial_narrative_context pour le
        # même motif (voir son commentaire) : une entreprise dont le
        # quarterly_financials a des colonnes mais pas la ligne CA ne doit
        # pas faire échouer toute l'entreprise pour un sous-signal
        # secondaire (10% du score).
        ratios["quarterly_yoy_growth_ca"] = extract_quarterly_growth(quarterly_financials)
    except KeyError:
        ratios["quarterly_yoy_growth_ca"] = None
    ratios["financial_context"] = build_financial_narrative_context(
        financials, balance_sheet, cashflow, quarterly_financials
    )
    ratios["latest_quarter_date"] = _resolve_financial_analysis_date(quarterly_financials, financials)
    ratios["current_price"] = current_price
    ratios["ma200"] = ma200
    ratios["shares_outstanding"] = shares_outstanding
    ratios["beta"] = beta
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


def fetch_news(company_name: str, previous_classifications: dict | None = None) -> list[dict]:
    """`previous_classifications` (optionnel, {lien: {summary, sentiment,
    importance}}) permet de réutiliser la classification déjà attribuée à
    une actu déjà vue lors d'un run précédent au lieu de rappeler Claude —
    évite qu'un même article change de classification d'un jour à l'autre
    (voir load_previous_company_analyses) et réduit le nombre d'appels."""
    params = {"q": company_name, "hl": "fr", "gl": "FR", "ceid": "FR:fr"}
    resp = requests.get(NEWS_RSS_URL, params=params, timeout=15)
    resp.raise_for_status()
    items = parse_news_rss(resp.content)
    previous_classifications = previous_classifications or {}
    for item in items:
        cached = previous_classifications.get(item["link"])
        if cached is not None:
            item["summary"] = cached.get("summary")
            item["sentiment"] = cached.get("sentiment")
            item["importance"] = cached.get("importance") or "mineure"
            continue
        article_text = fetch_article_text(item["link"])
        result = summarize_news_item(item["title"], company_name, article_text)
        item["summary"] = result["summary"]
        item["sentiment"] = result["sentiment"]
        item["importance"] = result["importance"]
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
    """Génère un résumé/contexte (1-2 phrases en français), une
    classification de sentiment et un niveau d'importance pour
    l'entreprise via l'API Anthropic, en un seul appel. Renvoie
    {"summary": "", "sentiment": 0, "importance": "mineure"} sur tout
    échec (clé API absente, erreur réseau, réponse HTTP non-200, JSON
    malformé) — ne lève jamais, même justification que
    fetch_article_text (dialogue avec un service tiers dont on ne peut
    pas énumérer précisément tous les modes d'échec). "mineure" en repli
    par défaut plutôt que "majeure" : une classification ratée ne doit
    jamais gonfler artificiellement le poids d'une actu ni déclencher à
    tort l'alerte "actu majeure"."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"summary": "", "sentiment": 0, "importance": "mineure"}

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
        "\n\nÉvalue aussi l'importance de cette actu pour l'entreprise, "
        "sur 3 niveaux :\n"
        "- \"majeure\" : OPA/fusion-acquisition, changement de direction "
        "générale (PDG/DG), avertissement sur résultats ou révision "
        "significative de la guidance, procédure judiciaire/réglementaire "
        "à fort impact, choc macro ou sectoriel touchant directement "
        "l'entreprise.\n"
        "- \"notable\" : résultats trimestriels sans avertissement, "
        "partenariat ou contrat commercial significatif, mouvement de "
        "matières premières pertinent pour le secteur de l'entreprise.\n"
        "- \"mineure\" : tout le reste (actualité générale, simple "
        "mention, communication mineure).\n\n"
        "Réponds uniquement avec un objet JSON valide, sans texte "
        "autour, de la forme : "
        '{"summary": "...", "sentiment": -1|0|1, "importance": '
        '"mineure"|"notable"|"majeure"} '
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
                "max_tokens": 200,
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
        importance = parsed.get("importance", "mineure")
        if importance not in NEWS_IMPORTANCE_LEVELS:
            importance = "mineure"
        if sentiment not in (-1, 0, 1):
            sentiment = 0
        return {"summary": summary, "sentiment": sentiment, "importance": importance}
    except Exception:
        return {"summary": "", "sentiment": 0, "importance": "mineure"}


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
    except Exception as e:
        # Avant, cette exception était avalée en silence — une vraie panne
        # (ex : crédit API Anthropic épuisé, trouvé en diagnostic) restait
        # invisible indéfiniment pour toute entreprise sans analyse à
        # reprendre par carry-forward. Même style de log que
        # fetch_news/build_company_entry pour les autres échecs "doux".
        print(f"Erreur génération analyse financière pour {company_name} : {e}")
        return None


OUTPUT_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "indices.json"
)

INDICES_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "indices_history.json"
)
HISTORY_RETENTION_PER_TICKER = 730  # ~2 ans, une entrée par jour et par ticker

SIGNAL_TRACKING_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "signal_tracking.json"
)
# Indices utilisés comme benchmark de chaque position (voir "index" sur
# chaque société — CAC40/DAX) : tickers yfinance correspondants.
INDEX_YFINANCE_TICKERS = {"CAC40": "^FCHI", "DAX": "^GDAXI"}
SIGNAL_STOP_LOSS_PCT = -20.0     # % perte déclenchant une clôture anticipée
SIGNAL_SHADOW_DELAY_MONTHS = 6   # délai max avant clôture forcée du signal
                                  # ET date du benchmark "tenir 6 mois pleins"
                                  # (même valeur, volontairement — voir
                                  # docs/superpowers/specs/2026-09-08-signal-performance-tracking-design.md)


def load_signal_tracking() -> list[dict]:
    """Positions de suivi des signaux (ouvertes et clôturées). [] si le
    fichier n'existe pas encore ou est corrompu — jamais d'exception."""
    if not os.path.exists(SIGNAL_TRACKING_PATH):
        return []
    try:
        with open(SIGNAL_TRACKING_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("positions", [])
    except (json.JSONDecodeError, AttributeError):
        return []


def save_signal_tracking(positions: list[dict]) -> None:
    """Écrit docs/signal_tracking.json — même dossier que docs/indices.json
    (servi statiquement au frontend), pas indices_history.json (racine,
    non servi)."""
    os.makedirs(os.path.dirname(SIGNAL_TRACKING_PATH), exist_ok=True)
    with open(SIGNAL_TRACKING_PATH, "w", encoding="utf-8") as fh:
        json.dump({"positions": positions}, fh, ensure_ascii=False, indent=2)


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
    news_items: list[dict] | None = None,
) -> list[dict]:
    """Alertes de franchissement de seuil pour une entreprise, à partir de
    son propre sous-historique (déjà filtré par ticker par l'appelant).
    `news_items` est optionnel (défaut None) pour ne rien changer au
    comportement des appelants existants qui ne le fournissent pas. Ne
    lève jamais d'exception ; renvoie toujours au moins une alerte
    (`info` neutre si rien ne se déclenche — calculé après l'alerte
    "actu_majeure" ci-dessous, pas avant, pour ne jamais afficher "pas de
    signal actif" en même temps qu'une vraie actu majeure).

    Toute actu classée "majeure" et encore dans sa fenêtre de pertinence
    (NEWS_SENTIMENT_WINDOW_DAYS) reste dans la liste retournée à CHAQUE
    run tant qu'elle est d'actualité — cette liste alimente aussi bien le
    panneau "Alertes" que le badge affiché sur le site, qui doivent
    rester vrais tant que l'actu est pertinente, pas seulement le jour de
    sa détection. Le dédoublonnage "ne pas ré-envoyer un email pour la
    même actu" est une décision distincte, prise par l'appelant
    (_attach_alerts_and_update_history) à partir de cette même liste —
    volontairement PAS ici, pour ne pas faire disparaître l'alerte de
    l'affichage simplement parce qu'elle a déjà été mailée."""
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

    # Actu majeure : indépendant du score et du prix — contrairement à
    # l'alerte "entree", un événement majeur (OPA, changement de
    # direction, avertissement sur résultats...) mérite un signal même
    # si le cours n'a pas encore bougé. Suivie par lien d'article (pas
    # par date) pour ne jamais re-notifier deux fois pour la même actu
    # tant qu'elle reste dans la fenêtre de NEWS_SENTIMENT_WINDOW_DAYS.
    news_cutoff = datetime.today().date() - timedelta(days=NEWS_SENTIMENT_WINDOW_DAYS)
    for item in (news_items or []):
        if item.get("importance") != "majeure":
            continue
        link = item.get("link")
        if not link:
            continue
        try:
            item_date = datetime.strptime(item["date"], "%Y-%m-%d").date()
        except (ValueError, TypeError, KeyError):
            continue
        if item_date < news_cutoff:
            continue
        alerts.append({
            "kind": "actu_majeure",
            "title": item.get("title") or "Actualité majeure",
            "detail": item.get("summary") or "Actualité classée comme majeure pour cette entreprise.",
            "date": today_str,
            "link": link,
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


ASSET_QUALITY_FLOOR = 0.3  # décote maximale (70%) appliquée à la valeur
                            # comptable — voir estimate_asset_based_price


def estimate_asset_based_price(
    equity: float, shares_outstanding: float,
    roe: float | None = None, cost_of_capital: float | None = None,
) -> float | None:
    """Valeur comptable par action (capitaux propres / actions en
    circulation), pondérée par un facteur qualité ROE/coût du capital
    (`justified P/B` — modèle du résultat résiduel : un P/B de 1 n'est
    justifié que si ROE = coût du capital ; en dessous, la valeur
    comptable surestime la valeur économique réelle, un capital qui ne
    couvre pas son propre coût détruisant de la valeur). Repéré sur
    Volkswagen (juste valeur à 232€ pour un cours à 83€, book value très
    au-dessus du marché — restructuration en cours, FCF négatif, ROE
    faible) : sans cette pondération, la valeur comptable brute écrasait
    le DCF/multiples dans la moyenne pondérée pour les cycliques.
    `roe`/`cost_of_capital` optionnels (défaut None) pour ne rien changer
    au comportement des appelants existants qui ne les fournissent pas —
    renvoie alors la valeur comptable brute (facteur 1.0), comme avant.
    Le facteur est plafonné à 1.0 (jamais de prime au-dessus du book
    value brut — les méthodes DCF/multiples portent déjà l'upside des
    entreprises performantes) et à ASSET_QUALITY_FLOOR au plancher (décote
    jamais totale, cette approximation reste simplifiée). None si les
    capitaux propres sont négatifs ou nuls (base non significative comme
    plancher de valorisation) ou si le nombre d'actions est nul/inconnu."""
    if not shares_outstanding or _is_missing(equity) or equity <= 0:
        return None
    book_value_per_share = equity / shares_outstanding
    if (
        roe is None or _is_missing(roe)
        or cost_of_capital is None or _is_missing(cost_of_capital) or cost_of_capital <= 0
    ):
        return book_value_per_share
    quality_factor = _clamp(roe / cost_of_capital, ASSET_QUALITY_FLOOR, 1.0)
    return book_value_per_share * quality_factor


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


# Avant : moyenne simple des 3 méthodes pour toutes les entreprises. Le DCF
# extrapole 5 ans de croissance depuis le FCF actuel — fiable pour une
# entreprise défensive aux flux prévisibles, beaucoup moins pour une
# cyclique dont le FCF peut être en haut ou en bas de cycle (repéré via
# Volkswagen : juste valeur à 494€ pour un cours à 81€, dont une partie
# tenait à ça une fois le bug sharesOutstanding corrigé). La valeur
# comptable (actif net) et le retour au multiple historique sont moins
# sensibles à ce biais. Pondère donc les 3 méthodes par le profil
# sectoriel déjà calculé (sector_risk_profile) plutôt qu'une moyenne
# identique pour toutes.
VALUATION_METHOD_WEIGHTS = {
    "defensif": {"dcf": 1.3, "asset": 0.8, "multiple": 1.0},
    "standard": {"dcf": 1.0, "asset": 1.0, "multiple": 1.0},
    "cyclique": {"dcf": 0.6, "asset": 1.2, "multiple": 1.1},
}


def estimate_fair_value(
    dcf_price: float | None, asset_price: float | None, multiple_price: float | None,
    sector_profile: str = "standard",
) -> float | None:
    """Moyenne pondérée des méthodes de valorisation disponibles (DCF,
    actif net, multiples) selon le profil sectoriel — ignore celles
    indisponibles (None), en renormalisant les poids sur celles qui
    restent ; None si aucune des 3 n'est calculable. Poids égaux (1.0)
    si `sector_profile` est inconnu, ce qui reproduit exactement l'ancien
    comportement (moyenne simple)."""
    weights = VALUATION_METHOD_WEIGHTS.get(sector_profile, VALUATION_METHOD_WEIGHTS["standard"])
    available = [
        (price, weight) for price, weight in (
            (dcf_price, weights["dcf"]),
            (asset_price, weights["asset"]),
            (multiple_price, weights["multiple"]),
        )
        if price is not None
    ]
    if not available:
        return None
    total_weight = sum(weight for _, weight in available)
    return sum(price * weight for price, weight in available) / total_weight


# --- Repères d'entrée/sortie pondérés par le risque ------------------------
#
# Avant : marge de sécurité fixe (±30% autour de la juste valeur, +20% pour
# la sortie technique) pour toutes les entreprises, quel que soit leur
# risque réel. Deux indicateurs déjà calculés (aucun nouvel appel yfinance)
# affinent maintenant ça :
# - le bêta (déjà utilisé pour le WACC) élargit ou resserre la marge de
#   sécurité proportionnellement au risque relatif au marché ;
# - la dynamique récente (écart au MM200, déjà calculé pour le facteur du
#   même nom) décale le repère technique : évite de recommander une entrée
#   sur une action en tendance baissière prononcée juste parce qu'elle est
#   sous sa MM200 (le piège classique du "couteau qui tombe"), et inversement
#   permet de suivre une tendance haussière confirmée plutôt que d'attendre
#   un retour à la MM200 qui peut ne jamais venir.

BETA_NEUTRAL = 1.0
VALUATION_MARGIN_BASE = 0.30   # marge à bêta neutre — valeur inchangée par rapport à avant
VALUATION_MARGIN_MIN = 0.15
VALUATION_MARGIN_MAX = 0.45
TECHNICAL_MARGIN_BASE = 0.20   # marge à bêta neutre — valeur inchangée par rapport à avant
TECHNICAL_MARGIN_MIN = 0.10
TECHNICAL_MARGIN_MAX = 0.30
TECHNICAL_MOMENTUM_ADJUSTMENT_MAX = 0.15   # décalage max (±15%) du repère technique


def _risk_adjusted_margin(beta: float | None, base: float, floor: float, cap: float) -> float:
    """Marge proportionnelle au bêta plutôt que fixe : une action volatile
    (bêta > 1) mérite une marge de sécurité plus large avant d'être jugée
    attractive, une action stable (bêta < 1) une marge plus resserrée.
    Repli sur la marge de base si le bêta est indisponible ou invalide."""
    if beta is None or _is_missing(beta) or beta <= 0:
        return base
    return _clamp(base * (beta / BETA_NEUTRAL), floor, cap)


def _momentum_adjustment(ecart_pct_ma200: float | None) -> float:
    """Décale le repère technique dans le sens de la tendance récente
    (même échelle que score_dynamique_recente), plafonné à ±15% — 0.0
    (aucun décalage) si l'écart à la MM200 est indisponible."""
    if ecart_pct_ma200 is None or _is_missing(ecart_pct_ma200):
        return 0.0
    return _clamp(
        ecart_pct_ma200 / PRICE_MOMENTUM_SCALE * TECHNICAL_MOMENTUM_ADJUSTMENT_MAX,
        -TECHNICAL_MOMENTUM_ADJUSTMENT_MAX, TECHNICAL_MOMENTUM_ADJUSTMENT_MAX,
    )


def estimate_entry_exit_prices(
    fair_value: float | None, ma200: float | None,
    beta: float | None, ecart_pct_ma200: float | None,
) -> dict:
    """Combine repère de valorisation (juste valeur ± marge pondérée par le
    bêta) et repère technique (MM200 décalée selon la dynamique récente) en
    moyennant ceux disponibles. Renvoie {"entry": float | None,
    "exit": float | None} — None des deux côtés si ni la valorisation ni la
    MM200 ne sont disponibles."""
    valuation_margin = _risk_adjusted_margin(
        beta, VALUATION_MARGIN_BASE, VALUATION_MARGIN_MIN, VALUATION_MARGIN_MAX
    )
    technical_margin = _risk_adjusted_margin(
        beta, TECHNICAL_MARGIN_BASE, TECHNICAL_MARGIN_MIN, TECHNICAL_MARGIN_MAX
    )
    momentum_adjustment = _momentum_adjustment(ecart_pct_ma200)

    entry_candidates = []
    exit_candidates = []
    if fair_value is not None:
        entry_candidates.append(fair_value * (1 - valuation_margin))
        exit_candidates.append(fair_value * (1 + valuation_margin))
    if ma200 is not None and not _is_missing(ma200):
        entry_candidates.append(ma200 * (1 + momentum_adjustment))
        exit_candidates.append(ma200 * (1 + technical_margin + momentum_adjustment))
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


DEBT_WEIGHT_CAP = 0.75  # part maximale de la dette dans la pondération du WACC
                         # (voir estimate_wacc) — sans ce plafond, un constructeur
                         # auto dont la dette de financement captif (crédit aux
                         # clients/concessionnaires via sa filiale financière,
                         # sans rapport avec le risque de l'activité industrielle)
                         # écrase largement une capitalisation boursière déprimée
                         # fait retomber le WACC quasiment au seul coût de la
                         # dette — repéré sur Volkswagen (WACC à 3.4%, en dessous
                         # du coût de la dette après impôt à taux constant, alors
                         # qu'un WACC industriel plausible est plutôt 6-9%).


def estimate_cost_of_equity(
    risk_free_rate: float | None, beta: float | None, market_cap: float | None,
) -> float | None:
    """Coût des seuls fonds propres (CAPM + prime de taille, Vernimmen) —
    sans mélange avec le coût de la dette, contrairement au WACC
    (estimate_wacc, qui appelle cette fonction en interne). Exposée
    séparément pour estimate_asset_based_price : le ROE d'une entreprise
    se compare au coût de SES fonds propres, pas au WACC — une dette bon
    marché (ex : financement captif d'un constructeur auto) fait
    baisser le WACC sans rendre les fonds propres eux-mêmes moins
    exigeants. None si une donnée nécessaire manque/est invalide. Ne
    lève jamais d'exception."""
    if (
        risk_free_rate is None or _is_missing(risk_free_rate)
        or beta is None or _is_missing(beta)
        or market_cap is None or _is_missing(market_cap) or market_cap <= 0
    ):
        return None
    try:
        return risk_free_rate + beta * MARKET_RISK_PREMIUM + _size_premium(market_cap)
    except (TypeError, ValueError):
        return None


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
    (ex : bêta remonté comme chaîne de caractères).

    La pondération dette/fonds propres est plafonnée à DEBT_WEIGHT_CAP
    (voir sa docstring) plutôt que d'utiliser directement le ratio de
    marché total_debt/(market_cap+total_debt), pour ne pas laisser une
    dette de financement captif (constructeurs auto notamment) écraser
    le coût des fonds propres dans le mix."""
    cost_of_equity = estimate_cost_of_equity(risk_free_rate, beta, market_cap)
    if (
        cost_of_equity is None
        or total_debt is None or _is_missing(total_debt) or total_debt < 0
        or tax_rate is None or _is_missing(tax_rate)
    ):
        return None
    try:
        cost_of_debt_after_tax = DEBT_INTEREST_RATE_PROXY * (1 - tax_rate)
        total_capital = market_cap + total_debt
        debt_weight = min(total_debt / total_capital, DEBT_WEIGHT_CAP)
        equity_weight = 1 - debt_weight
        return equity_weight * cost_of_equity + debt_weight * cost_of_debt_after_tax
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def estimate_valuation_targets(
    data: dict, cost_of_capital: float, cost_of_equity: float | None = None,
) -> dict:
    """Combine DCF, actif net et multiples en une juste valeur (pondérée
    par le profil sectoriel — voir VALUATION_METHOD_WEIGHTS), puis en
    repères d'entrée/sortie. Toujours ces 3 clés en sortie, valeurs à None
    si non calculables (jamais d'exception). `cost_of_capital` est le taux
    d'actualisation du DCF (WACC de l'entreprise, ou COST_OF_CAPITAL_PROXY
    en repli — résolu par l'appelant). `cost_of_equity` (optionnel) sert
    de référence à la décote qualité de la valeur comptable
    (estimate_asset_based_price) au lieu du WACC — repli sur
    `cost_of_capital` si absent, pour ne rien changer au comportement des
    appelants existants qui ne le fournissent pas."""
    sector_profile = sector_risk_profile(data["sector"])
    # Point de départ du DCF lissé sur 2 exercices pour les cycliques (voir
    # extract_ratios/fcf_normalized) plutôt que le seul dernier exercice,
    # qui peut être en haut ou en bas de cycle pour ce type d'entreprise.
    dcf_fcf = data["fcf_normalized"] if sector_profile == "cyclique" else data["fcf"]
    dcf_price = estimate_dcf_price(
        dcf_fcf, data["cagr_ebitda"], data["net_debt"], data["shares_outstanding"],
        cost_of_capital,
    )
    asset_price = estimate_asset_based_price(
        data["equity"], data["shares_outstanding"], data["roe"],
        cost_of_equity if cost_of_equity is not None else cost_of_capital,
    )
    multiple_price = (
        estimate_multiple_based_price(
            data["current_price"], data["current_ev_ebitda"], data["avg_ev_ebitda_5y"]
        )
        if data["current_price"] is not None else None
    )
    fair_value = estimate_fair_value(dcf_price, asset_price, multiple_price, sector_profile)
    entry_exit = estimate_entry_exit_prices(
        fair_value, data["ma200"], data["beta"], data["ecart_pct_ma200"]
    )
    return {
        "fair_value": fair_value,
        "entry_price": entry_exit["entry"],
        "exit_price": entry_exit["exit"],
    }


def load_previous_company_analyses() -> dict:
    """Lit le docs/indices.json du run précédent (déjà commité) pour en
    extraire, par ticker, l'analyse financière et la date de trimestre
    qu'elle couvre, ainsi que la classification (résumé/sentiment/
    importance) déjà attribuée à chaque actu par son lien
    (`news_classifications`) — voir fetch_news, qui la réutilise au lieu
    de rappeler Claude sur une actu déjà vue, pour que la classification
    d'un article donné reste stable d'un run à l'autre plutôt que de
    dériver (ex : "majeure" un jour, "mineure" le lendemain pour le même
    article) et faire disparaître/réapparaître l'alerte "actu_majeure" au
    hasard. {} si le fichier n'existe pas encore ou est illisible —
    jamais d'exception."""
    if not os.path.exists(OUTPUT_JSON_PATH):
        return {}
    try:
        with open(OUTPUT_JSON_PATH, encoding="utf-8") as fh:
            previous = json.load(fh)
        return {
            c["ticker"]: {
                "financial_analysis_html": c.get("financial_analysis_html"),
                "financial_analysis_quarter": c.get("financial_analysis_quarter"),
                "news_classifications": {
                    n["link"]: {
                        "summary": n.get("summary"),
                        "sentiment": n.get("sentiment"),
                        "importance": n.get("importance"),
                    }
                    for n in c.get("news", [])
                    if n.get("link")
                },
            }
            for c in previous.get("companies", [])
            if c.get("ticker")
        }
    except Exception:
        return {}


def load_previous_alert_kinds() -> dict:
    """Lit le docs/indices.json du run précédent pour en extraire, par
    ticker, l'ensemble des types d'alerte actifs hier (ex : {"entree"}) —
    permet de détecter un signal "entrée" nouvellement apparu aujourd'hui
    plutôt qu'un signal qui persiste depuis plusieurs jours (pas de mail
    à répétition tant que le cours reste proche du repère). {} si le
    fichier n'existe pas encore ou est illisible — jamais d'exception."""
    if not os.path.exists(OUTPUT_JSON_PATH):
        return {}
    try:
        with open(OUTPUT_JSON_PATH, encoding="utf-8") as fh:
            previous = json.load(fh)
        return {
            c["ticker"]: {a["kind"] for a in c.get("alerts", [])}
            for c in previous.get("companies", [])
            if c.get("ticker")
        }
    except Exception:
        return {}


def load_previous_alerted_news_links() -> dict:
    """Lit le docs/indices.json du run précédent pour en extraire, par
    ticker, les liens d'actus déjà signalées par une alerte "actu_majeure"
    — permet de ne jamais re-notifier deux fois pour la même actu tant
    qu'elle reste dans la fenêtre de NEWS_SENTIMENT_WINDOW_DAYS. {} si le
    fichier n'existe pas encore ou est illisible — jamais d'exception."""
    if not os.path.exists(OUTPUT_JSON_PATH):
        return {}
    try:
        with open(OUTPUT_JSON_PATH, encoding="utf-8") as fh:
            previous = json.load(fh)
        return {
            c["ticker"]: {
                a["link"] for a in c.get("alerts", [])
                if a.get("kind") == "actu_majeure" and a.get("link")
            }
            for c in previous.get("companies", [])
            if c.get("ticker")
        }
    except Exception:
        return {}


def build_company_entry(
    ticker: str, name: str, risk_free_rate: float | None, previous_analyses: dict,
    index_key: str = "CAC40",
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
    cost_of_equity = estimate_cost_of_equity(risk_free_rate, data["beta"], market_cap)

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
        if data["is_financial"]:
            # Profil financier (voir FINANCIAL_SECTOR_TICKERS) : ROE/ratio
            # de levier/CAGR résultat net plutôt que ROCE/dette nette-
            # EBITDA/ICR/CAGR EBITDA, absents pour ces entreprises.
            ratios_summary = (
                f"[Profil financier — méthodologie adaptée, EBITDA/EBIT non "
                f"disponibles pour cette entreprise] "
                f"ROE {data['roe']:.1f}%, capitaux propres/actif total "
                f"{data['leverage_ratio']:.1f}%, CAGR CA {data['cagr_ca']:+.1f}%/an, "
                f"CAGR résultat net {data['cagr_net_income']:+.1f}%/an, "
                f"conversion cash (OCF/résultat net) {data['cash_conversion']:.0f}%, "
                f"coût du capital {cost_of_capital:.1f}%"
            )
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
        news = fetch_news(name, previous.get("news_classifications", {}))
    except Exception as e:
        print(f"Erreur récupération news pour {name} : {e}")
        news = []

    if data["is_financial"]:
        factors = [
            score_rentabilite_financiere(data["roe"], cost_of_capital),
            score_structure_financiere_bancaire(data["leverage_ratio"]),
            score_croissance_financiere(data["cagr_ca"], data["cagr_net_income"]),
            score_generation_cash_financiere(data["cash_conversion"]),
            score_valorisation_financiere(
                data["current_pe"], data["avg_pe_5y"],
                data["current_pb"], data["avg_pb_5y"], data["cagr_net_income"],
            ),
            score_dynamique_recente(
                data["ecart_pct_ma200"], data["quarterly_yoy_growth_ca"], data["cagr_ca"],
            ),
            score_actualite_recente(news),
        ]
    else:
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

    valuation_targets = estimate_valuation_targets(data, cost_of_capital, cost_of_equity)

    return {
        "ticker": ticker,
        "name": name,
        "index": index_key,
        "sector": sector,
        "sector_profile": sector_risk_profile(sector),
        "is_financial": data["is_financial"],
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


def _attach_alerts_and_update_history(companies: list[dict]) -> tuple[list[dict], list[tuple]]:
    """Calcule les alertes de chaque entreprise à partir de son historique
    et enregistre le score du jour. Dégrade vers alerts=[] pour toutes les
    entreprises si l'historique est illisible/inscriptible — ne doit
    jamais faire échouer la publication du score déjà calculé. Renvoie
    (newly_triggered_entree, newly_triggered_major_news) :
    - newly_triggered_entree : entreprises dont le signal "entree" vient
      d'apparaître aujourd'hui (absent des alertes de la veille) ;
    - newly_triggered_major_news : liste de (entreprise, alerte) pour
      chaque actu "actu_majeure" nouvellement signalée aujourd'hui (une
      entreprise peut en avoir plusieurs le même jour, rare mais possible).
    ([], []) si rien de nouveau ou en cas d'échec, jamais d'exception."""
    for company in companies:
        company["alerts"] = []
    newly_triggered_entree = []
    newly_triggered_major_news = []
    try:
        history = load_indices_history()
        previous_alert_kinds = load_previous_alert_kinds()
        previous_alerted_news_links = load_previous_alerted_news_links()
        today_str = datetime.today().strftime("%Y-%m-%d")
        new_entries = []
        for company in companies:
            ticker_history = [e for e in history if e["ticker"] == company["ticker"]]
            company["alerts"] = compute_company_alerts(
                company["ticker"], company["score"], company["current_price"],
                company["entry_price"], ticker_history,
                news_items=company.get("news", []),
            )
            today_kinds = {a["kind"] for a in company["alerts"]}
            if "entree" in today_kinds and "entree" not in previous_alert_kinds.get(company["ticker"], set()):
                newly_triggered_entree.append(company)
            # L'alerte "actu_majeure" reste affichée tant que l'actu est
            # pertinente (voir compute_company_alerts), mais ne doit
            # déclencher un email qu'une seule fois par actu — dédoublonnage
            # par lien fait ici, pas dans compute_company_alerts, pour ne
            # pas faire disparaître l'alerte de l'affichage une fois mailée.
            already_alerted_links = previous_alerted_news_links.get(company["ticker"], set())
            for alert in company["alerts"]:
                if alert["kind"] == "actu_majeure" and alert.get("link") not in already_alerted_links:
                    newly_triggered_major_news.append((company, alert))
            new_entries.append({
                "date": today_str, "ticker": company["ticker"], "composite": company["score"],
            })
        append_indices_history(new_entries)
    except Exception as e:
        print(f"Erreur historique/alertes Indices : {e}")
        return [], []
    return newly_triggered_entree, newly_triggered_major_news


# Même serveur/couple de secrets GitHub Actions que gold_score.py
# (SMTP_USER/SMTP_PASSWORD) — voir .github/workflows/indices.yml.
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SITE_BASE_URL = "https://alexandreauq.github.io/analyse-or/"


def _entry_alert_detail(company: dict) -> str:
    """Texte de l'alerte "entree" elle-même (déjà rédigé par
    compute_company_alerts) — plutôt que de reformuler la condition
    séparément et risquer une divergence avec ce qui est réellement
    affiché sur le site."""
    for alert in company.get("alerts", []):
        if alert.get("kind") == "entree":
            return alert.get("detail", "")
    return ""


ENTRY_ALERT_NEWS_COUNT = 2  # nombre d'actus récentes reprises dans l'email


def _entry_alert_context(company: dict) -> str:
    """Contexte du signal : dynamique récente (prix/trimestre, déjà
    calculée pour le facteur du même nom) + les actus les plus fraîches
    déjà résumées (fetch_news/summarize_news_item) — pas une affirmation
    qu'une actu précise a "causé" le signal (c'est un seuil mécanique
    score + prix), seulement le contexte disponible pour l'interpréter."""
    parts = []
    dynamique = next(
        (f for f in company.get("factors", []) if f.get("name") == "Dynamique récente"), None
    )
    if dynamique and dynamique.get("raw_value"):
        parts.append(
            f'<p style="color:#edeef3;font-size:13px;line-height:1.6;margin:0 0 14px;">'
            f'{dynamique["raw_value"]}</p>'
        )
    recent_news = [n for n in company.get("news", []) if n.get("summary")][:ENTRY_ALERT_NEWS_COUNT]
    for n in recent_news:
        meta = " · ".join(part for part in (n.get("source"), n.get("date")) if part)
        parts.append(
            f'<p style="margin:0 0 14px;padding:10px 14px;background:#1b1d25;'
            f'border-left:3px solid #2a2d38;font-family:Arial,sans-serif;">'
            f'<strong style="color:#edeef3;font-size:13px;">{n["title"]}</strong><br>'
            f'<span style="color:#8a90a3;font-size:11px;">{meta}</span><br>'
            f'<span style="color:#8a90a3;font-size:12px;line-height:1.5;">{n["summary"]}</span></p>'
        )
    return "".join(parts)


def build_entry_alert_email_html(company: dict) -> str:
    """Un email par entreprise (pas un digest groupé) : objet et contenu
    portent sur cette seule entreprise, dans le même langage visuel que
    le site (Fraunces remplacé par une police sans-serif — non
    disponible dans un email — mais mêmes couleurs et hiérarchie)."""
    index_name = INDEX_NAMES.get(company.get("index"), company.get("index", ""))
    score = company["score"]
    score_color = "#b99a68" if score >= 0 else "#a35540"
    detail = _entry_alert_detail(company)
    context_html = _entry_alert_context(company)
    fiche_url = f"{SITE_BASE_URL}#indices/{company['ticker']}"

    return f"""
    <html><body style="background:#15161c;margin:0;padding:0;">
      <div style="max-width:480px;margin:0 auto;padding:32px 24px;font-family:Arial,Helvetica,sans-serif;">
        <p style="color:#8a90a3;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;margin:0 0 10px;">
          {index_name} — Signal d'entrée
        </p>
        <h1 style="color:#edeef3;font-size:24px;font-weight:bold;margin:0 0 2px;">{company['name']}</h1>
        <p style="color:#8a90a3;font-size:13px;margin:0 0 24px;">{company['ticker']}</p>

        <div style="background:#1b1d25;border:1px solid #2a2d38;border-radius:10px;padding:20px 20px 16px;margin:0 0 20px;">
          <p style="color:{score_color};font-size:42px;font-weight:bold;margin:0;line-height:1;">{score:+.1f}</p>
          <p style="color:#edeef3;font-size:14px;margin:8px 0 0;">{company['interpretation']}</p>
        </div>

        <table style="width:100%;border-collapse:collapse;margin:0 0 20px;">
          <tr>
            <td style="padding:9px 0;border-bottom:1px solid #2a2d38;color:#8a90a3;font-size:13px;font-family:Arial,sans-serif;">Cours actuel</td>
            <td style="padding:9px 0;border-bottom:1px solid #2a2d38;color:#edeef3;font-size:13px;font-family:Arial,sans-serif;text-align:right;">{company['current_price']:.2f} €</td>
          </tr>
          <tr>
            <td style="padding:9px 0;border-bottom:1px solid #2a2d38;color:#8a90a3;font-size:13px;font-family:Arial,sans-serif;">Repère d'entrée</td>
            <td style="padding:9px 0;border-bottom:1px solid #2a2d38;color:#b99a68;font-size:13px;font-family:Arial,sans-serif;text-align:right;">{company['entry_price']:.2f} €</td>
          </tr>
          <tr>
            <td style="padding:9px 0;color:#8a90a3;font-size:13px;font-family:Arial,sans-serif;">Repère de sortie</td>
            <td style="padding:9px 0;color:#a35540;font-size:13px;font-family:Arial,sans-serif;text-align:right;">{company['exit_price']:.2f} €</td>
          </tr>
        </table>

        <p style="color:#8a90a3;font-size:13px;line-height:1.6;margin:0 0 20px;">{detail}</p>

        {f'''<p style="color:#8a90a3;font-size:11px;letter-spacing:0.06em;text-transform:uppercase;margin:0 0 12px;">
          Pourquoi ce signal ?
        </p>
        {context_html}''' if context_html else ''}

        <a href="{fiche_url}" style="display:inline-block;background:#b99a68;color:#15161c;
           font-weight:bold;font-size:14px;padding:13px 26px;border-radius:8px;text-decoration:none;">
          Voir la fiche complète →
        </a>

        <p style="color:#8a90a3;font-size:11px;line-height:1.5;margin:32px 0 0;">
          Score composite favorable et cours proche du repère d'entrée — pas un conseil d'investissement.
        </p>
      </div>
    </body></html>
    """


def send_entry_alert_email(companies: list[dict]) -> bool:
    """Envoie un email par entreprise dont le signal "entree" vient
    d'apparaître aujourd'hui (pas un digest groupé). Ignoré
    silencieusement (avec un message) si les identifiants SMTP ne sont
    pas configurés ou si `companies` est vide — jamais d'exception,
    même contrat que gold_score.send_email."""
    if not companies:
        return False
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    mail_to = os.environ.get("MAIL_TO") or smtp_user
    if not smtp_user or not smtp_password:
        print("\n(Envoi d'email d'alerte entrée ignoré : SMTP_USER / SMTP_PASSWORD non configurés.)")
        return False

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            for company in companies:
                index_name = INDEX_NAMES.get(company.get("index"), company.get("index", ""))
                msg = MIMEMultipart("mixed")
                msg["Subject"] = f"{company['name']} ({index_name}) — signal d'entrée"
                msg["From"] = smtp_user
                msg["To"] = mail_to
                msg.attach(MIMEText(build_entry_alert_email_html(company), "html"))
                server.sendmail(smtp_user, [mail_to], msg.as_string())
        tickers = ", ".join(c["ticker"] for c in companies)
        print(f"\nEmail(s) d'alerte entrée envoyé(s) à {mail_to} ({tickers})")
        return True
    except Exception as e:
        print(f"Erreur envoi email d'alerte entrée : {e}")
        return False


def _find_news_item_by_link(company: dict, link: str) -> dict | None:
    for item in company.get("news", []):
        if item.get("link") == link:
            return item
    return None


def build_major_news_alert_email_html(company: dict, alert: dict) -> str:
    """Email centré sur l'actu elle-même (pas le score/prix, contrairement
    à l'alerte entrée) : titre, source, résumé, lien direct vers la fiche."""
    index_name = INDEX_NAMES.get(company.get("index"), company.get("index", ""))
    news_item = _find_news_item_by_link(company, alert.get("link", "")) or {}
    sentiment = news_item.get("sentiment", 0)
    sentiment_label = {-1: "Défavorable", 0: "Neutre", 1: "Favorable"}.get(sentiment, "Neutre")
    sentiment_color = {-1: "#a35540", 0: "#8a90a3", 1: "#b99a68"}.get(sentiment, "#8a90a3")
    meta = " · ".join(part for part in (news_item.get("source"), alert.get("date")) if part)
    fiche_url = f"{SITE_BASE_URL}#indices/{company['ticker']}"

    return f"""
    <html><body style="background:#15161c;margin:0;padding:0;">
      <div style="max-width:480px;margin:0 auto;padding:32px 24px;font-family:Arial,Helvetica,sans-serif;">
        <p style="color:#8a90a3;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;margin:0 0 10px;">
          {company['name']} ({index_name}) — Actu majeure
        </p>
        <span style="display:inline-block;background:{sentiment_color};color:#15161c;
           font-size:11px;font-weight:bold;padding:3px 10px;border-radius:999px;margin:0 0 14px;">
          {sentiment_label}
        </span>

        <h1 style="color:#edeef3;font-size:20px;font-weight:bold;margin:0 0 6px;line-height:1.3;">{alert['title']}</h1>
        <p style="color:#8a90a3;font-size:12px;margin:0 0 20px;">{meta}</p>

        <div style="background:#1b1d25;border:1px solid #2a2d38;border-radius:10px;padding:16px 18px;margin:0 0 24px;">
          <p style="color:#edeef3;font-size:13px;line-height:1.6;margin:0;">{alert['detail']}</p>
        </div>

        <a href="{fiche_url}" style="display:inline-block;background:#b99a68;color:#15161c;
           font-weight:bold;font-size:14px;padding:13px 26px;border-radius:8px;text-decoration:none;">
          Voir la fiche complète →
        </a>

        <p style="color:#8a90a3;font-size:11px;line-height:1.5;margin:32px 0 0;">
          Actualité classée automatiquement comme majeure pour cette entreprise — pas un conseil d'investissement.
        </p>
      </div>
    </body></html>
    """


def send_major_news_alert_email(triggered: list[tuple]) -> bool:
    """Envoie un email par (entreprise, alerte "actu_majeure") nouvellement
    apparue aujourd'hui — chaque alerte de ce type est déjà garantie
    nouvelle par construction (compute_company_alerts ne l'émet que pour
    un lien pas encore signalé). Ignoré silencieusement (avec un message)
    si les identifiants SMTP ne sont pas configurés ou si `triggered` est
    vide — jamais d'exception, même contrat que send_entry_alert_email."""
    if not triggered:
        return False
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    mail_to = os.environ.get("MAIL_TO") or smtp_user
    if not smtp_user or not smtp_password:
        print("\n(Envoi d'email d'alerte actu majeure ignoré : SMTP_USER / SMTP_PASSWORD non configurés.)")
        return False

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            for company, alert in triggered:
                index_name = INDEX_NAMES.get(company.get("index"), company.get("index", ""))
                msg = MIMEMultipart("mixed")
                msg["Subject"] = f"{company['name']} ({index_name}) — actu majeure"
                msg["From"] = smtp_user
                msg["To"] = mail_to
                msg.attach(MIMEText(build_major_news_alert_email_html(company, alert), "html"))
                server.sendmail(smtp_user, [mail_to], msg.as_string())
        tickers = ", ".join(c["ticker"] for c, _ in triggered)
        print(f"\nEmail(s) d'alerte actu majeure envoyé(s) à {mail_to} ({tickers})")
        return True
    except Exception as e:
        print(f"Erreur envoi email d'alerte actu majeure : {e}")
        return False


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
                    index_key=company["index"],
                )
            )
        except Exception as e:
            print(f"Erreur pour {company['ticker']} ({company['name']}) : {e}")

    newly_triggered_entree, newly_triggered_major_news = _attach_alerts_and_update_history(companies)
    send_entry_alert_email(newly_triggered_entree)
    send_major_news_alert_email(newly_triggered_major_news)

    payload = {
        "updated": datetime.today().strftime("%Y-%m-%d"),
        "index_names": INDEX_NAMES,
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
