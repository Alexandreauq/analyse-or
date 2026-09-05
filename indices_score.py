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
from datetime import datetime
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

    net_debt_latest = total_debt[latest] - cash[latest]
    economic_assets_latest = equity[latest] + net_debt_latest
    roce = (ebit[latest] * (1 - tax_rate[latest]) / economic_assets_latest) * 100 if economic_assets_latest else 0.0
    roe = (net_income[latest] / equity[latest]) * 100 if equity[latest] else 0.0

    net_debt_ebitda = net_debt_latest / ebitda[latest] if ebitda[latest] else 0.0
    icr = ebit[latest] / (total_debt[latest] * 0.03) if total_debt[latest] else 10.0  # proxy frais financiers si non isolés

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
    fcf = op_cash_flow[latest] + capex[latest]  # capex déjà négatif dans yfinance
    fcf_conversion = (fcf / ebitda[latest]) * 100 if ebitda[latest] else 0.0

    ev_ebitda_by_year, pe_by_year = [], []
    for col in years_cols:
        price = closes_by_year.get(col)
        if (
            price is None
            or not ebitda[col]
            or not net_income[col]
            or _is_missing(ebitda[col])
            or _is_missing(net_income[col])
            or _is_missing(total_debt[col])
            or _is_missing(cash[col])
        ):
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
        item["summary"] = summarize_news_item(item["title"], company_name, article_text)
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


def summarize_news_item(title: str, company_name: str, article_text: str | None) -> str:
    """Génère un résumé/contexte en français (1-2 phrases) via l'API
    Anthropic. Renvoie "" sur tout échec (clé API absente, erreur réseau,
    réponse HTTP non-200, réponse malformée) — ne lève jamais, même
    justification que fetch_article_text (dialogue avec un service tiers
    dont on ne peut pas énumérer précisément tous les modes d'échec)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ""

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
        return data["content"][0]["text"].strip()
    except Exception:
        return ""


OUTPUT_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "indices.json"
)

# Approximation simplifiée du coût du capital (pas de calcul de bêta désendetté
# au v1) : taux sans risque + prime de risque marché, cf. section "hors périmètre"
# de la méthodologie.
COST_OF_CAPITAL_PROXY = 8.0  # %


def build_company_entry(ticker: str, name: str) -> dict:
    data = fetch_company_financials(ticker)
    sector = data["sector"]

    factors = [
        score_rentabilite(data["roce"], data["roe"], COST_OF_CAPITAL_PROXY),
        score_structure_financiere(data["net_debt_ebitda"], data["icr"], sector),
        score_croissance(data["cagr_ca"], data["cagr_ebitda"]),
        score_generation_cash(data["fcf_conversion"]),
        score_valorisation(
            data["current_ev_ebitda"], data["avg_ev_ebitda_5y"],
            data["current_pe"], data["avg_pe_5y"], data["cagr_ebitda"],
        ),
    ]
    composite = compute_composite(factors)

    # La récupération des news est une donnée secondaire, distincte du score
    # fondamental (cf. Methodologie_Analyse_Indices.md, "Décisions actées") :
    # un échec du flux RSS (timeout, format Google modifié, rate limit) ne
    # doit pas faire perdre toute l'entrée (dont le score) déjà calculée,
    # même logique que get_gold_spot_and_ma200() dans gold_score.py.
    try:
        news = fetch_news(name)
    except Exception as e:
        print(f"Erreur récupération news pour {name} : {e}")
        news = []

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
    }


def main():
    companies = []
    for company in COMPANIES:
        try:
            companies.append(build_company_entry(company["ticker"], company["name"]))
        except Exception as e:
            print(f"Erreur pour {company['ticker']} ({company['name']}) : {e}")

    payload = {
        "updated": datetime.today().strftime("%Y-%m-%d"),
        "companies": companies,
    }

    os.makedirs(os.path.dirname(OUTPUT_JSON_PATH), exist_ok=True)
    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f"Données exportées vers : {OUTPUT_JSON_PATH}")
    for c in companies:
        print(f"  {c['ticker']:<8} {c['name']:<20} score {c['score']:+.1f}  ({c['interpretation']})")


if __name__ == "__main__":
    main()
