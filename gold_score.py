"""
Score macro-fondamental de l'or — Phase 1 (or métal / GLD)
=============================================================

Calcule le score composite décrit dans la méthodologie (Methodologie_Analyse_Or.md),
à partir de données réelles quand une source gratuite fiable existe, et de valeurs
saisies manuellement pour les facteurs qui n'ont pas d'API publique simple.

Sources automatisées :
    - FRED (Réserve fédérale de St. Louis)     -> taux réels 10 ans, breakevens d'inflation,
                                                    dates de publication CPI et PCE
    - Yahoo Finance (via yfinance)              -> cours spot or, DXY, moyenne mobile 200j
    - CFTC Socrata API (Legacy Futures Only)    -> positionnement spéculatif (percentile 3 ans)
    - Calendrier officiel de la Fed (figé en dur)-> dates des réunions FOMC 2026

Sources non automatisées pour l'instant (voir MANUAL_INPUTS ci-dessous) :
    - Ton de la Fed (lecture qualitative des communications du FOMC)
    - Flux ETF (GLD + IAU) — pas de source gratuite fiable trouvée pour l'instant
      (SPDR bloque l'accès automatisé à son CSV, yfinance ne couvre pas les parts
      d'ETF) ; à revisiter avec une source payante ou un scraping plus robuste
    - Achats des banques centrales — rapport trimestriel World Gold Council (PDF)

Installation :
    pip install requests yfinance pandas

Configuration :
    Créer une clé API FRED gratuite ici : https://fred.stlouisfed.org/docs/api/api_key.html
    puis définir la variable d'environnement FRED_API_KEY.
"""

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests

try:
    import yfinance as yf
except ImportError:
    yf = None


# ---------------------------------------------------------------------------
# 1. Poids et échelle — issus de la méthodologie (section 3)
# ---------------------------------------------------------------------------

WEIGHTS = {
    "taux_reels": 0.30,
    "dollar": 0.15,
    "inflation_anticipee": 0.15,
    "ton_fed": 0.15,
    "flux_etf": 0.10,
    "positionnement_cftc": 0.10,
    "banques_centrales": 0.05,
}

# Valeurs saisies à la main tant que les sources ne sont pas automatisées.
# Score attendu entre -10 (très défavorable à l'or) et +10 (très favorable).
MANUAL_INPUTS = {
    "ton_fed": 5,            # à ajuster après chaque FOMC / discours majeur
    "flux_etf": 3,           # à ajuster à partir des encours publiés par SPDR/iShares
    "banques_centrales": 7,  # à ajuster après chaque rapport trimestriel WGC
}


@dataclass
class FactorResult:
    name: str
    score: float          # -10 à +10
    weight: float
    raw_value: str         # valeur brute pour affichage / audit


# ---------------------------------------------------------------------------
# 2. FRED — taux réels et anticipations d'inflation
# ---------------------------------------------------------------------------

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"


def fetch_fred_series(series_id: str, api_key: str, lookback_days: int = 45):
    """Récupère les dernières observations d'une série FRED."""
    start = (datetime.today() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
    }
    resp = requests.get(FRED_BASE_URL, params=params, timeout=15)
    resp.raise_for_status()
    obs = [o for o in resp.json()["observations"] if o["value"] != "."]
    if not obs:
        raise ValueError(f"Aucune observation exploitable pour la série {series_id}")
    return obs


def score_from_momentum(current: float, past: float, scale: float) -> float:
    """Convertit une variation en score -10/+10, borné, selon une échelle donnée."""
    delta = current - past
    score = (delta / scale) * 10
    return max(-10, min(10, score))


def factor_taux_reels(api_key: str) -> FactorResult:
    obs = fetch_fred_series("DFII10", api_key)  # TIPS 10 ans
    current = float(obs[-1]["value"])
    past = float(obs[0]["value"])
    # Une baisse des taux réels est favorable à l'or -> score inversé
    score = score_from_momentum(past, current, scale=0.5)  # 0.5 pt = mouvement significatif
    return FactorResult("Taux réels US 10 ans", score, WEIGHTS["taux_reels"],
                         f"{current:.2f}% (il y a ~45j : {past:.2f}%)")


def factor_inflation_anticipee(api_key: str) -> FactorResult:
    obs = fetch_fred_series("T10YIE", api_key)  # breakeven 10 ans
    current = float(obs[-1]["value"])
    past = float(obs[0]["value"])
    score = score_from_momentum(current, past, scale=0.3)
    return FactorResult("Anticipations d'inflation", score, WEIGHTS["inflation_anticipee"],
                         f"{current:.2f}% (il y a ~45j : {past:.2f}%)")


# ---------------------------------------------------------------------------
# 3. Yahoo Finance — cours spot, dollar, moyenne mobile 200 jours
# ---------------------------------------------------------------------------

def factor_dollar() -> FactorResult:
    if yf is None:
        raise RuntimeError("yfinance n'est pas installé (pip install yfinance)")
    dxy = yf.Ticker("DX-Y.NYB").history(period="2mo")["Close"]
    current, past = dxy.iloc[-1], dxy.iloc[0]
    # Un dollar qui baisse est favorable à l'or -> score inversé
    score = score_from_momentum(past, current, scale=2.0)
    return FactorResult("Dollar (DXY)", score, WEIGHTS["dollar"],
                         f"{current:.2f} (il y a 2 mois : {past:.2f})")


def get_gold_spot_and_ma200():
    if yf is None:
        raise RuntimeError("yfinance n'est pas installé (pip install yfinance)")
    hist = yf.Ticker("GC=F").history(period="1y")["Close"]
    spot = hist.iloc[-1]
    ma200 = hist.tail(200).mean()
    return spot, ma200


# ---------------------------------------------------------------------------
# 4. CFTC — Commitment of Traders (positionnement spéculatif)
# ---------------------------------------------------------------------------

CFTC_LEGACY_FUTURES_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
CFTC_GOLD_MARKET_NAME = "GOLD - COMMODITY EXCHANGE INC."


def fetch_cftc_gold_net_positions(lookback_weeks: int = 156):
    """
    Interroge l'API publique du CFTC (dataset Legacy Futures Only, resource
    id 6dca-aqww) et renvoie l'historique du positionnement net des
    spéculateurs ("non-commercial") sur les futures Or du Comex, du plus
    récent au plus ancien. 156 semaines ≈ 3 ans, cohérent avec la
    méthodologie.
    """
    params = {
        "$where": f"market_and_exchange_names='{CFTC_GOLD_MARKET_NAME}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": lookback_weeks,
    }
    resp = requests.get(CFTC_LEGACY_FUTURES_URL, params=params, timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise ValueError(
            "Aucune donnée renvoyée par le CFTC — vérifie le nom du marché "
            f"'{CFTC_GOLD_MARKET_NAME}' ou l'identifiant du dataset (6dca-aqww)."
        )
    net_positions = [
        float(r["noncomm_positions_long_all"]) - float(r["noncomm_positions_short_all"])
        for r in rows
    ]
    return net_positions  # index 0 = semaine la plus récente


def percentile_rank(value: float, history: list[float]) -> float:
    """Percentile du dernier point par rapport à l'historique fourni (0-100)."""
    below_or_equal = sum(1 for v in history if v <= value)
    return (below_or_equal / len(history)) * 100


def factor_positionnement_cftc():
    """
    Un positionnement déjà très long (percentile élevé sur 3 ans) est un
    signal de prudence, pas d'achat -> score négatif quand le percentile
    est élevé. Renvoie (FactorResult, percentile) — le percentile brut sert
    aussi à la logique d'alerte (section 6 de la méthodologie).
    """
    net_positions = fetch_cftc_gold_net_positions()
    current_net = net_positions[0]
    percentile = percentile_rank(current_net, net_positions)

    # Percentile 50 = neutre, 100 = positionnement extrême -> score négatif
    score = -((percentile - 50) / 50) * 10
    score = max(-10, min(10, score))
    result = FactorResult(
        "Positionnement CFTC", score, WEIGHTS["positionnement_cftc"],
        f"{percentile:.0f}e percentile (3 ans) — position nette {current_net:,.0f} contrats",
    )
    return result, percentile


# ---------------------------------------------------------------------------
# 5. Facteurs manuels (en attendant l'automatisation)
# ---------------------------------------------------------------------------
#
# Note sur "flux ETF" : deux tentatives d'automatisation ont échoué et ont été
# abandonnées — le CSV public de SPDR bloque les requêtes automatisées
# (renvoie un PDF au lieu du CSV attendu), et yfinance.get_shares_full() ne
# couvre pas l'historique des parts pour les ETF comme GLD (fonction pensée
# pour les actions classiques). À revisiter plus tard avec une autre source.

def manual_factor(key: str, label: str) -> FactorResult:
    score = MANUAL_INPUTS[key]
    return FactorResult(label, score, WEIGHTS[key], f"{score:+d} (saisie manuelle)")


# ---------------------------------------------------------------------------
# 7. Score composite et interprétation
# ---------------------------------------------------------------------------

def compute_composite(factors: list[FactorResult]) -> float:
    weighted_sum = sum(f.score * f.weight for f in factors)
    return round(weighted_sum * 10, 1)  # ramené à l'échelle -100 / +100


def interpret(composite: float) -> str:
    if composite > 50:
        return "Contexte très favorable"
    if composite > 15:
        return "Favorable — en attente d'un déclencheur technique"
    if composite > -15:
        return "Neutre — pas d'action"
    return "Défavorable — pas d'entrée même sur signal technique"


def check_technical_trigger(spot: float, ma200: float) -> str:
    ecart_pct = (spot - ma200) / ma200 * 100
    if abs(ecart_pct) < 1:
        return f"Cours proche de la MM200 ({ecart_pct:+.1f}%) — zone de décision"
    if spot > ma200:
        return f"Cours au-dessus de la MM200 ({ecart_pct:+.1f}%) — tendance haussière"
    return f"Cours en-dessous de la MM200 ({ecart_pct:+.1f}%) — tendance baissière"


# ---------------------------------------------------------------------------
# 8. Calendrier macro (FOMC + publications CPI/PCE)
# ---------------------------------------------------------------------------

FRED_RELEASES_URL = "https://api.stlouisfed.org/fred/release/dates"

# Réunions FOMC 2026, publiées à l'avance par la Fed (federalreserve.gov).
# À compléter avec le calendrier 2027 une fois publié par la Fed.
FOMC_2026_MEETINGS = [
    ("2026-01-27", "2026-01-28"),
    ("2026-03-17", "2026-03-18"),
    ("2026-04-28", "2026-04-29"),
    ("2026-06-16", "2026-06-17"),
    ("2026-07-28", "2026-07-29"),
    ("2026-09-15", "2026-09-16"),
    ("2026-10-27", "2026-10-28"),
    ("2026-12-08", "2026-12-09"),
]

FRED_RELEASE_IDS = {
    "cpi": (10, "CPI (Indice des prix à la consommation)"),
    "pce": (54, "PCE (dépenses de consommation des ménages)"),
}


def next_fomc_meeting():
    """Renvoie la prochaine réunion FOMC à venir, ou None si le calendrier
    connu (2026) est épuisé."""
    today = datetime.today().date()
    for start, end in FOMC_2026_MEETINGS:
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
        if end_date >= today:
            display = f"{start_date.strftime('%d/%m')}\u2013{end_date.strftime('%d/%m/%Y')}"
            return {"sort_date": start_date, "date": display, "label": "Réunion FOMC", "kind": "Fed"}
    return None


def fetch_fred_next_release(release_id: int, api_key: str, label: str):
    """Interroge le calendrier des publications FRED et renvoie la
    prochaine date de publication (à partir d'aujourd'hui) pour un
    release_id donné. Par défaut, FRED masque les dates futures sans
    données associées -> include_release_dates_with_no_data=true est requis."""
    params = {
        "release_id": release_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "asc",
        "include_release_dates_with_no_data": "true",
        "limit": 10000,
    }
    resp = requests.get(FRED_RELEASES_URL, params=params, timeout=15)
    resp.raise_for_status()
    dates = resp.json().get("release_dates", [])
    today = datetime.today().date()
    upcoming = [
        d for d in dates
        if datetime.strptime(d["date"], "%Y-%m-%d").date() >= today
    ]
    if not upcoming:
        raise ValueError(f"Aucune date de publication future trouvée pour {label}.")
    date_obj = datetime.strptime(upcoming[0]["date"], "%Y-%m-%d").date()
    return {"sort_date": date_obj, "date": date_obj.strftime("%d/%m/%Y"),
            "label": label, "kind": "Donnée"}


def build_macro_calendar(api_key: str):
    events = []
    fomc = next_fomc_meeting()
    if fomc:
        events.append(fomc)
    for _, (release_id, label) in FRED_RELEASE_IDS.items():
        try:
            events.append(fetch_fred_next_release(release_id, api_key, label))
        except Exception as e:
            events.append({"sort_date": None, "date": "?",
                            "label": f"{label} (indisponible : {e})", "kind": "Donnée"})
    events.sort(key=lambda e: e["sort_date"] or datetime.max.date())
    for e in events:
        e.pop("sort_date", None)
    return events


# ---------------------------------------------------------------------------
# 9. Historique et logique d'alerte (section 6 de la méthodologie)
# ---------------------------------------------------------------------------

HISTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "score_history.json")
CFTC_EXTREME_PERCENTILE = 90       # au-delà : positionnement jugé extrême
CFTC_CAUTION_PERCENTILE = 85       # au-delà : bloque un signal d'entrée
NEAR_SUPPORT_PCT = 1.0             # écart max (%) à la MM200 pour "proche d'un support"
RAPID_DROP_POINTS = 20             # chute du score composite jugée rapide
RAPID_DROP_DAYS = 5
FED_BLACKOUT_HOURS = 48            # pas d'entrée si événement Fed dans ce délai


def load_history(path=HISTORY_PATH):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError:
            return []


def append_history(entry, path=HISTORY_PATH, max_entries=730):
    history = load_history(path)
    history.append(entry)
    history = history[-max_entries:]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(history, fh, ensure_ascii=False, indent=2)
    return history


def compute_alerts(composite, previous_history, cftc_percentile, ecart_pct_ma200, hours_to_next_fomc):
    """
    Traduit les règles de la section 6 de la méthodologie en alertes concrètes,
    à partir du score du jour et de l'historique des exécutions précédentes.
    """
    today_str = datetime.today().strftime("%d/%m/%Y")
    alerts = []

    prev_composite = previous_history[-1]["composite"] if previous_history else None

    # --- Watch : franchissement de +15 à la hausse ---------------------------
    if prev_composite is not None and prev_composite <= 15 < composite:
        alerts.append({
            "kind": "watch",
            "title": "Score composite a franchi +15",
            "detail": "Surveillance active du déclencheur technique enclenchée.",
            "date": today_str,
        })

    # --- Risque : chute de plus de RAPID_DROP_POINTS en moins de RAPID_DROP_DAYS
    cutoff = datetime.today().date() - timedelta(days=RAPID_DROP_DAYS)
    recent = [
        e for e in previous_history
        if datetime.strptime(e["date"], "%Y-%m-%d").date() >= cutoff
    ]
    if recent:
        max_recent = max(e["composite"] for e in recent)
        drop = composite - max_recent
        if drop <= -RAPID_DROP_POINTS:
            alerts.append({
                "kind": "risque",
                "title": "Chute rapide du score composite",
                "detail": f"Repricing de {drop:+.1f} points en moins de {RAPID_DROP_DAYS} jours.",
                "date": today_str,
            })

    # --- Risque : positionnement CFTC en zone extrême -------------------------
    if cftc_percentile is not None and (
        cftc_percentile >= CFTC_EXTREME_PERCENTILE or cftc_percentile <= (100 - CFTC_EXTREME_PERCENTILE)
    ):
        alerts.append({
            "kind": "risque",
            "title": "Positionnement CFTC en zone extrême",
            "detail": f"{cftc_percentile:.0f}e percentile (3 ans) — prudence avant toute nouvelle position.",
            "date": today_str,
        })

    # --- Entrée : les 4 conditions de la section 5 réunies simultanément -----
    near_support = ecart_pct_ma200 is not None and abs(ecart_pct_ma200) < NEAR_SUPPORT_PCT
    cftc_ok = cftc_percentile is not None and cftc_percentile < CFTC_CAUTION_PERCENTILE
    fed_ok = hours_to_next_fomc is None or hours_to_next_fomc > FED_BLACKOUT_HOURS
    if composite > 15 and near_support and cftc_ok and fed_ok:
        alerts.append({
            "kind": "entree",
            "title": "Conditions d'entrée réunies",
            "detail": "Score favorable, cours proche d'un support, positionnement non extrême, "
                      "pas d'événement Fed imminent.",
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


# ---------------------------------------------------------------------------
# 10. Génération du tableau de bord HTML (autonome, sans serveur)
# ---------------------------------------------------------------------------

OUTPUT_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "score.json")
OUTPUT_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")

# Dossier servi par GitHub Pages : le site PWA (index.html, manifest, etc.)
# lit score.json depuis ici via un fetch() côté navigateur.
DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
DOCS_JSON_PATH = os.path.join(DOCS_DIR, "score.json")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Score macro-fondamental — Or</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Inter:wght@400;500;600&display=swap');
  :root {
    --bg: #15161c; --panel: #1b1d25; --border: #2a2d38;
    --text: #edeef3; --muted: #8a90a3;
    --gold: #c6a15b; --rust: #b25539;
  }
  * { box-sizing: border-box; }
  body {
    background: var(--bg); color: var(--text); font-family: 'Inter', sans-serif;
    margin: 0; padding: 40px 48px 56px;
  }
  .wrap { max-width: 900px; margin: 0 auto; }
  header { display:flex; justify-content:space-between; align-items:flex-end; margin-bottom: 28px; }
  .eyebrow { color: var(--muted); font-size: 13px; margin-bottom: 6px; }
  h1 { font-family: 'Fraunces', serif; font-weight: 500; font-size: 26px; margin: 0; }
  .header-right { text-align: right; color: var(--muted); font-size: 13px; line-height: 1.6; }
  .hero { display:flex; align-items:center; gap: 32px; padding: 20px 0; }
  .hero-number { font-family: 'Fraunces', serif; font-weight: 500; font-size: 88px; line-height: 1; color: var(--gold); }
  .hero-label { font-size: 19px; font-weight: 500; margin-bottom: 8px; }
  .hero-sub { color: var(--muted); font-size: 14px; line-height: 1.6; max-width: 460px; margin: 0; }
  .divider { height: 1px; background: var(--border); margin: 8px 0 32px; }
  h2 { font-family: 'Fraunces', serif; font-weight: 500; font-size: 17px; margin: 0 0 16px; }
  .factor-row { padding: 14px 0; border-bottom: 1px solid var(--border); }
  .factor-head { display:flex; justify-content:space-between; margin-bottom: 8px; font-size: 14px; }
  .factor-name { font-weight: 500; }
  .factor-weight { color: var(--muted); }
  .bar-track { position:relative; height:6px; background: var(--border); border-radius: 3px; margin-bottom: 8px; }
  .bar-center { position:absolute; left:50%; top:-2px; bottom:-2px; width:1px; background: var(--muted); opacity:0.5; }
  .bar-fill { position:absolute; top:0; bottom:0; border-radius: 3px; }
  .factor-note { color: var(--muted); font-size: 13px; line-height: 1.5; margin: 0; }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-top: 28px; }
  .tech-row { display:flex; justify-content:space-between; font-size:14px; padding: 6px 0; }
  .tech-label { color: var(--muted); }
  .tech-value { font-weight: 500; }
  .cal-row { display:grid; grid-template-columns: 110px 1fr auto; gap:10px; font-size:13px;
             padding:8px 0; border-bottom:1px solid var(--border); align-items:baseline; }
  .cal-row:last-child { border-bottom: none; }
  .cal-date { color: var(--gold); }
  .cal-kind { color: var(--muted); font-size:12px; }
  .alert-row { border-left: 2px solid; padding: 4px 0 10px 12px; margin-bottom: 12px; }
  .alert-head { display:flex; justify-content:space-between; font-size:13px; font-weight:500; }
  .alert-date { color: var(--muted); font-weight:400; }
  .alert-detail { color: var(--muted); font-size:13px; line-height:1.5; margin:4px 0 0; }
  footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid var(--border); color: var(--muted); font-size: 12px; line-height: 1.6; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <div class="eyebrow">Projet analyse or — phase 1</div>
      <h1>Score macro-fondamental</h1>
    </div>
    <div class="header-right" id="date-label"></div>
  </header>

  <section class="hero">
    <div class="hero-number" id="composite-number"></div>
    <div>
      <div class="hero-label" id="interpretation"></div>
      <p class="hero-sub">Sur une échelle de -100 à +100. Seuils : entrée envisageable au-dessus de +15
      sous réserve d'un déclencheur technique, prudence en dessous de -15.</p>
    </div>
  </section>

  <div class="divider"></div>

  <h2>Décomposition par facteur</h2>
  <div id="factors"></div>

  <div class="panel">
    <h2>Repère technique</h2>
    <div class="tech-row"><span class="tech-label">Cours spot</span><span class="tech-value" id="spot-value"></span></div>
    <div class="tech-row"><span class="tech-label">Moyenne mobile 200j</span><span class="tech-value" id="ma200-value"></span></div>
    <p class="hero-sub" id="tech-note"></p>
  </div>

  <div class="panel">
    <h2>Calendrier macro</h2>
    <div id="calendar"></div>
  </div>

  <div class="panel">
    <h2>Alertes</h2>
    <div id="alerts"></div>
  </div>

  <footer>
    Données réelles issues de gold_score.py (FRED, Yahoo Finance, CFTC) et de saisies manuelles
    pour les facteurs non encore automatisés (ton de la Fed, flux ETF, banques centrales).
  </footer>
</div>

<script>
const DATA = __SCORE_DATA__;

document.getElementById('date-label').textContent = DATA.date;
document.getElementById('composite-number').textContent =
  (DATA.composite_score > 0 ? '+' : '') + DATA.composite_score;
document.getElementById('interpretation').textContent = DATA.interpretation;

const factorsEl = document.getElementById('factors');
DATA.factors.forEach(f => {
  const pct = Math.min(50, Math.abs(f.score) / 10 * 50);
  const positive = f.score >= 0;
  const row = document.createElement('div');
  row.className = 'factor-row';
  row.innerHTML = `
    <div class="factor-head">
      <span class="factor-name">${f.name}</span>
      <span class="factor-weight">${Math.round(f.weight * 100)}%</span>
    </div>
    <div class="bar-track">
      <div class="bar-center"></div>
      <div class="bar-fill" style="left:${positive ? 50 : 50 - pct}%; width:${pct}%; background:${positive ? 'var(--gold)' : 'var(--rust)'};"></div>
    </div>
    <p class="factor-note">${f.raw_value}</p>
  `;
  factorsEl.appendChild(row);
});

const t = DATA.technical;
document.getElementById('spot-value').textContent = t.spot ? `${t.spot.toLocaleString('fr-FR', {maximumFractionDigits:0})} $ / oz` : 'indisponible';
document.getElementById('ma200-value').textContent = t.ma200 ? `${t.ma200.toLocaleString('fr-FR', {maximumFractionDigits:0})} $ / oz` : 'indisponible';
document.getElementById('tech-note').textContent = t.note;

const calEl = document.getElementById('calendar');
(DATA.calendar || []).forEach(c => {
  const row = document.createElement('div');
  row.className = 'cal-row';
  row.innerHTML = `
    <span class="cal-date">${c.date}</span>
    <span>${c.label}</span>
    <span class="cal-kind">${c.kind}</span>
  `;
  calEl.appendChild(row);
});

function alertColor(kind) {
  if (kind === 'risque') return 'var(--rust)';
  if (kind === 'watch' || kind === 'entree') return 'var(--gold)';
  return 'var(--muted)';
}

const alertsEl = document.getElementById('alerts');
(DATA.alerts || []).forEach(a => {
  const row = document.createElement('div');
  row.className = 'alert-row';
  row.style.borderLeftColor = alertColor(a.kind);
  row.innerHTML = `
    <div class="alert-head"><span>${a.title}</span><span class="alert-date">${a.date}</span></div>
    <p class="alert-detail">${a.detail}</p>
  `;
  alertsEl.appendChild(row);
});
</script>
</body>
</html>
"""


def generate_dashboard_html(payload, path=OUTPUT_HTML_PATH):
    html = HTML_TEMPLATE.replace("__SCORE_DATA__", json.dumps(payload, ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


# ---------------------------------------------------------------------------
# 11. Envoi par email (résumé dans le corps + tableau de bord en pièce jointe)
# ---------------------------------------------------------------------------
#
# Les clients email n'exécutent pas de JavaScript : contrairement à
# dashboard.html (qui construit son contenu via JS à partir de DATA), le
# corps de l'email est un HTML statique généré directement en Python, avec
# des styles en ligne (pas de <style>/@import, mal supportés par les clients
# mail). Le dashboard interactif complet est joint en pièce jointe.

import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def build_email_html(payload) -> str:
    def alert_color(kind):
        if kind == "risque":
            return "#b25539"
        if kind in ("watch", "entree"):
            return "#c6a15b"
        return "#8a90a3"

    alerts_html = "".join(
        f'<p style="margin:0 0 10px;padding:8px 12px;border-left:3px solid {alert_color(a["kind"])};'
        f'color:#edeef3;font-family:Arial,sans-serif;">'
        f'<strong>{a["title"]}</strong><br>'
        f'<span style="color:#8a90a3;font-size:13px;">{a["detail"]}</span></p>'
        for a in payload["alerts"]
    )

    factors_rows = "".join(
        f'<tr>'
        f'<td style="padding:8px 12px;border-bottom:1px solid #2a2d38;color:#edeef3;'
        f'font-family:Arial,sans-serif;font-size:14px;">{f["name"]}</td>'
        f'<td style="padding:8px 12px;border-bottom:1px solid #2a2d38;color:#8a90a3;'
        f'font-family:Arial,sans-serif;font-size:14px;text-align:right;">{f["score"]:+.1f}</td>'
        f'</tr>'
        for f in payload["factors"]
    )

    return f"""
    <html><body style="background:#15161c;color:#edeef3;font-family:Arial,sans-serif;padding:24px;">
      <h2 style="color:#c6a15b;margin:0 0 4px;">Score macro-fondamental — Or</h2>
      <p style="color:#8a90a3;margin:0 0 20px;">{payload['date']}</p>
      <p style="font-size:48px;font-weight:bold;color:#c6a15b;margin:0;">{payload['composite_score']:+.1f}</p>
      <p style="font-size:16px;margin:4px 0 24px;">{payload['interpretation']}</p>

      <h3 style="margin:0 0 8px;">Alertes</h3>
      {alerts_html}

      <h3 style="margin:20px 0 8px;">Facteurs</h3>
      <table style="border-collapse:collapse;width:100%;">{factors_rows}</table>

      <p style="color:#8a90a3;font-size:12px;margin-top:24px;">
        Tableau de bord complet et interactif en pièce jointe (dashboard.html).
      </p>
    </body></html>
    """


def send_email(payload, dashboard_path=OUTPUT_HTML_PATH):
    """
    Envoie le résumé par email. Ignoré silencieusement (avec un message) si
    les identifiants ne sont pas configurés, pour ne pas casser une
    exécution locale où l'email n'est pas encore mis en place.
    """
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    mail_to = os.environ.get("MAIL_TO") or smtp_user

    if not smtp_user or not smtp_password:
        print("\n(Envoi d'email ignoré : SMTP_USER / SMTP_PASSWORD non configurés.)")
        return False

    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Score macro-fondamental Or — {payload['date']} ({payload['composite_score']:+.1f})"
    msg["From"] = smtp_user
    msg["To"] = mail_to
    msg.attach(MIMEText(build_email_html(payload), "html"))

    if os.path.exists(dashboard_path):
        with open(dashboard_path, "rb") as fh:
            part = MIMEApplication(fh.read(), Name="dashboard.html")
        part["Content-Disposition"] = 'attachment; filename="dashboard.html"'
        msg.attach(part)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, [mail_to], msg.as_string())

    print(f"\nEmail envoyé à {mail_to}")
    return True


# ---------------------------------------------------------------------------
# 12. Exécution
# ---------------------------------------------------------------------------

def main():
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        print("ERREUR : variable d'environnement FRED_API_KEY absente.")
        print("Crée une clé gratuite sur https://fred.stlouisfed.org/docs/api/api_key.html")
        sys.exit(1)

    factors = [
        factor_taux_reels(api_key),
        factor_dollar(),
        factor_inflation_anticipee(api_key),
        manual_factor("ton_fed", "Ton de la Fed"),
        manual_factor("flux_etf", "Flux ETF (GLD + IAU)"),
        manual_factor("banques_centrales", "Achats banques centrales"),
    ]
    cftc_factor, cftc_percentile = factor_positionnement_cftc()
    factors.insert(5, cftc_factor)  # conserve l'ordre d'affichage d'origine

    composite = compute_composite(factors)

    print("=" * 60)
    print(f"SCORE MACRO-FONDAMENTAL OR — {datetime.today():%d/%m/%Y}")
    print("=" * 60)
    for f in factors:
        print(f"  {f.name:<32} score {f.score:+5.1f}  (poids {f.weight:.0%})  — {f.raw_value}")
    print("-" * 60)
    print(f"  SCORE COMPOSITE : {composite:+.1f}  ->  {interpret(composite)}")

    spot = ma200 = None
    technical_note = "Repère technique indisponible"
    try:
        spot, ma200 = get_gold_spot_and_ma200()
        technical_note = check_technical_trigger(spot, ma200)
        print(f"\n  {technical_note}")
        print(f"  Cours spot : {spot:,.0f} $ / oz | MM200 : {ma200:,.0f} $ / oz")
    except Exception as e:
        technical_note = f"Repère technique indisponible : {e}"
        print(f"\n  ({technical_note})")

    print("=" * 60)

    calendar = build_macro_calendar(api_key)
    print("\nCALENDRIER MACRO")
    for c in calendar:
        print(f"  {c['date']:<18} {c['label']:<45} {c['kind']}")

    # Heures avant la prochaine réunion FOMC (pour la condition "pas d'événement
    # Fed imminent" de la logique d'entrée)
    fomc = next_fomc_meeting()
    hours_to_next_fomc = None
    if fomc:
        fomc_start = datetime.combine(fomc["sort_date"], datetime.min.time())
        hours_to_next_fomc = (fomc_start - datetime.now()).total_seconds() / 3600

    ecart_pct_ma200 = (spot - ma200) / ma200 * 100 if spot and ma200 else None

    history = load_history()
    alerts = compute_alerts(composite, history, cftc_percentile, ecart_pct_ma200, hours_to_next_fomc)

    print("\nALERTES")
    for a in alerts:
        print(f"  [{a['kind'].upper():<7}] {a['title']} — {a['detail']}")

    append_history({
        "date": datetime.today().strftime("%Y-%m-%d"),
        "composite": composite,
        "cftc_percentile": cftc_percentile,
    })

    payload = {
        "date": datetime.today().strftime("%Y-%m-%d"),
        "composite_score": composite,
        "interpretation": interpret(composite),
        "factors": [
            {"name": f.name, "score": f.score, "weight": f.weight, "raw_value": f.raw_value}
            for f in factors
        ],
        "technical": {"note": technical_note, "spot": spot, "ma200": ma200},
        "calendar": calendar,
        "alerts": alerts,
    }

    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(DOCS_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    html_path = generate_dashboard_html(payload)

    print(f"\nDonnées exportées vers : {OUTPUT_JSON_PATH}")
    print(f"Copie pour le site (GitHub Pages) : {DOCS_JSON_PATH}")
    print(f"Tableau de bord généré : {html_path}")
    print("Ouvre ce fichier .html directement dans ton navigateur pour le voir.")

    send_email(payload, html_path)


if __name__ == "__main__":
    main()
