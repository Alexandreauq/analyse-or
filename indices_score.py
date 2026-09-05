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

try:
    import yfinance as yf
except ImportError:
    yf = None


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
    """CAGR en % entre la valeur la plus ancienne et la plus récente."""
    if first_value <= 0 or years <= 0:
        return 0.0
    return ((last_value / first_value) ** (1 / years) - 1) * 100


def extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding: float) -> dict:
    """
    Calcule les ratios bruts nécessaires aux fonctions de score à partir des
    états financiers yfinance (financials, balance_sheet, cashflow — colonnes
    = dates d'exercice, la plus récente en premier) et des cours de clôture
    par date d'exercice (closes_by_year, même clés que les colonnes).
    """
    years_cols = list(financials.columns)  # plus récent en premier
    n_years = len(years_cols)
    latest, oldest = years_cols[0], years_cols[-1]

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

    net_debt_latest = total_debt[latest] - cash[latest]
    economic_assets_latest = equity[latest] + net_debt_latest
    roce = (ebit[latest] * (1 - tax_rate[latest]) / economic_assets_latest) * 100 if economic_assets_latest else 0.0
    roe = (net_income[latest] / equity[latest]) * 100 if equity[latest] else 0.0

    net_debt_ebitda = net_debt_latest / ebitda[latest] if ebitda[latest] else 0.0
    icr = ebit[latest] / (total_debt[latest] * 0.03) if total_debt[latest] else 10.0  # proxy frais financiers si non isolés

    cagr_ca = _cagr(revenue[oldest], revenue[latest], n_years - 1)
    cagr_ebitda = _cagr(ebitda[oldest], ebitda[latest], n_years - 1)

    fcf = op_cash_flow[latest] + capex[latest]  # capex déjà négatif dans yfinance
    fcf_conversion = (fcf / ebitda[latest]) * 100 if ebitda[latest] else 0.0

    ev_ebitda_by_year, pe_by_year = [], []
    for col in years_cols:
        price = closes_by_year.get(col)
        if price is None or not ebitda[col] or not net_income[col]:
            continue
        market_cap = price * shares_outstanding
        net_debt_year = total_debt[col] - cash[col]
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
    }


def fetch_company_financials(ticker: str) -> dict:
    if yf is None:
        raise RuntimeError("yfinance n'est pas installé (pip install yfinance)")
    t = yf.Ticker(ticker)
    financials = t.financials
    balance_sheet = t.balance_sheet
    cashflow = t.cashflow
    info = t.info

    shares_outstanding = info.get("sharesOutstanding") or 0.0
    history = t.history(period="6y")["Close"]
    closes_by_year = {}
    for col in financials.columns:
        target_date = col.date() if hasattr(col, "date") else col
        window = history[history.index.date <= target_date] if hasattr(history.index, "date") else history
        if len(window):
            closes_by_year[col] = float(window.iloc[-1])

    ratios = extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding)
    ratios["sector"] = info.get("sector")
    return ratios
