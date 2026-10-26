"""Teste les regles "daily mode" de trader-scanner (day-trader-skill) comme
facteur candidat sur le signal de base Weinstein+RS deja valide par
walk_forward_eval.py.

RECHERCHE / EXPLORATOIRE - PAS DU CODE DE PRODUCTION. Voir README.md et
trader_scanner_factor_README.md dans ce dossier.

BLOCAGE INITIAL LEVE : docs/price_history.json n'a de vraies cloture
quotidiennes que depuis le 2026-09-28 (~1 mois), insuffisant pour calculer
breakout_20d/RSI14/etc. sur l'historique 2020-2026. Ce script reconstruit
l'historique quotidien necessaire via yfinance (test de faisabilite :
119/120 tickers echantillon recuperes en 26.5s, ~0.22s/ticker, AUCUN
rate-limiting detecte -- extrapole a l'univers complet de ~1296 tickers,
~5 minutes, donc PAS de reduction de perimetre necessaire).

IMPORTANT (environnement local uniquement) : sur cette machine, yfinance
echoue avec une erreur SSL ("unable to get local issuer certificate") tant
que la variable d'environnement CURL_CA_BUNDLE ne pointe pas vers un
fichier CA incluant a la fois certifi ET le certificat d'interception
Norton Antivirus (C:\\ProgramData\\Norton\\Antivirus\\wscert.pem) -- Norton
intercepte le trafic HTTPS de curl_cffi (utilise par yfinance) mais pas
celui de `requests`. Voir build_ca_bundle() ci-dessous : genere ce fichier
combine automatiquement si besoin, aucune action manuelle requise. Cette
particularite est locale a cette machine ; un run sur une autre machine
(VPS, CI) n'aura probablement pas ce probleme et peut ignorer
build_ca_bundle() si CURL_CA_BUNDLE n'est pas necessaire.

Donnees quotidiennes reconstruites -> data/daily_closes.pkl (gitignore,
JAMAIS docs/price_history.json). A regenerer avec --rebuild si besoin.
"""
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
REPO = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
DAILY_CACHE_PATH = os.path.join(DATA_DIR, "daily_closes.pkl")

import numpy as np
import pandas as pd
from backtest_engine import (
    compute_stage, compute_rs_percentile, compute_entry_signal, simulate,
    week_id_to_date, POSITION_NOTIONAL, STOP_LOSS_PCT, ROUNDTRIP_COST_PCT,
)

EXCLUDE_TICKERS = {
    "^AEX", "^DJI", "^FCHI", "^FTSE", "^GDAXI", "^GSPC", "^HSI", "^IBEX",
    "^N225", "^NDX", "^SBF120", "^SSMI", "^STOXX50E", "FTSEMIB.MI",
    "MT.PA", "IHG.L", "MTLN.L", "CPG.L",
}

MIN_DAILY_BARS = 30
DAILY_FETCH_START = "2020-10-01"


def week_id_of(d: pd.Timestamp) -> str:
    iso = d.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def week_sort_key(wid: str):
    y, w = wid.split("-W")
    return (int(y), int(w))


def build_ca_bundle() -> str | None:
    """Concatene certifi + le certificat d'interception Norton (si present
    sur cette machine) dans un fichier temporaire, et retourne son chemin.
    None si Norton n'est pas installe -- la plupart des machines n'ont pas
    besoin de ce contournement, voir docstring du module."""
    norton_cert = r"C:\ProgramData\Norton\Antivirus\wscert.pem"
    if not os.path.exists(norton_cert):
        return None
    import certifi
    import tempfile
    combined = os.path.join(tempfile.gettempdir(), "analyse_or_combined_ca_bundle.pem")
    with open(combined, "wb") as out:
        with open(certifi.where(), "rb") as f:
            out.write(f.read())
        out.write(b"\n")
        with open(norton_cert, "rb") as f:
            out.write(f.read())
    return combined


def fetch_daily_closes(tickers: list[str]) -> dict[str, pd.Series]:
    """{ticker: Series(date -> close quotidien)}, cloture journaliere
    telechargee via yfinance depuis DAILY_FETCH_START. Les echecs
    (ticker introuvable, "possibly delisted") sont silencieusement
    absents du dict -- un ticker manquant est traite plus loin comme
    "pas de donnee quotidienne" (jamais une fausse valeur)."""
    import yfinance as yf
    out = {}
    t0 = time.time()
    n_err = 0
    for i, tk in enumerate(tickers):
        try:
            df = yf.Ticker(tk).history(start=DAILY_FETCH_START, interval="1d")
            if df is not None and not df.empty:
                s = df["Close"].copy()
                s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                out[tk] = s
            else:
                n_err += 1
        except Exception:
            n_err += 1
        if (i + 1) % 100 == 0:
            print(f"  ...{i+1}/{len(tickers)} tickers ({time.time()-t0:.0f}s ecoule, {n_err} echecs)")
    print(f"Reconstruction terminee : {len(out)}/{len(tickers)} tickers OK, {n_err} echecs, {time.time()-t0:.0f}s")
    return out


def load_or_build_daily_closes(tickers: list[str], rebuild: bool) -> dict[str, pd.Series]:
    if (not rebuild) and os.path.exists(DAILY_CACHE_PATH):
        print(f"Cache trouve : {DAILY_CACHE_PATH}")
        return pd.read_pickle(DAILY_CACHE_PATH)
    ca_bundle = build_ca_bundle()
    if ca_bundle:
        os.environ["CURL_CA_BUNDLE"] = ca_bundle
        print(f"CURL_CA_BUNDLE defini sur {ca_bundle} (contournement interception Norton locale)")
    print(f"Telechargement de l'historique quotidien pour {len(tickers)} tickers depuis {DAILY_FETCH_START}...")
    daily = fetch_daily_closes(tickers)
    os.makedirs(DATA_DIR, exist_ok=True)
    pd.to_pickle(daily, DAILY_CACHE_PATH)
    print(f"Cache ecrit : {DAILY_CACHE_PATH}")
    return daily


def rsi(closes: np.ndarray, n: int = 14) -> float | None:
    """Wilder's RSI -- reproduit a l'identique day-trader-skill/trader-scanner/scan.py:485-495."""
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def find_setups(c: list[float]) -> list[str]:
    """Reproduit a l'identique day-trader-skill/trader-scanner/scan.py:516-539
    (closes tries du plus ancien au plus recent, >= 30 valeurs)."""
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r = rsi(c)
    r_prev = rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags


RULES = ["breakout_20d", "bull_flag", "reclaim_ma20", "oversold", "oversold_bounce"]


def tag_entry(daily_series: pd.Series | None, entry_date: pd.Timestamp) -> list[str]:
    """Tags trader-scanner actifs au dernier jour de bourse STRICTEMENT
    avant entry_date (jamais le jour meme ni apres -- aucune fuite). []
    si la donnee quotidienne est absente ou insuffisante (< MIN_DAILY_BARS),
    jamais une tag fabriquee."""
    if daily_series is None:
        return []
    closes = daily_series[daily_series.index < entry_date]
    if len(closes) < MIN_DAILY_BARS:
        return []
    return find_setups(list(closes.values[-max(MIN_DAILY_BARS, 21):]))


def metrics_for_subset(sub: pd.DataFrame, week_in_win: np.ndarray, all_week_ids: list[str]) -> dict:
    """Comme walk_forward_eval.metrics_for_window, mais le peak_concurrent
    est recalcule a partir du SOUS-ENSEMBLE filtre de trades (jamais reutilise
    depuis le concurrent_count de la simulation complete, qui compterait les
    positions des trades EXCLUS par le filtre)."""
    if len(sub) == 0:
        return dict(n_trades=0, net_return_pct=np.nan, capital_base=0.0, win_rate=np.nan)
    n_weeks = len(all_week_ids)
    conc = np.zeros(n_weeks, dtype=int)
    for _, row in sub.iterrows():
        conc[row["entry_week_idx"]:row["exit_week_idx"] + 1] += 1
    peak_concurrent = int(conc[week_in_win].max()) if week_in_win.any() else 0
    capital_base = peak_concurrent * POSITION_NOTIONAL
    total_pnl = sub["pnl_eur"].sum()
    net_return_pct = (total_pnl / capital_base * 100) if capital_base > 0 else np.nan
    win_rate = (sub["net_return"] > 0).mean() * 100
    return dict(n_trades=len(sub), net_return_pct=net_return_pct, capital_base=capital_base, win_rate=win_rate)


def main():
    rebuild = "--rebuild" in sys.argv

    print("Construction de price_wide (hebdo) depuis docs/price_history.json...")
    with open(os.path.join(REPO, "docs", "price_history.json"), encoding="utf-8") as f:
        ph = json.load(f)
    pdf = pd.DataFrame(ph)
    pdf["date"] = pd.to_datetime(pdf["date"])
    pdf = pdf[~pdf["ticker"].isin(EXCLUDE_TICKERS)]
    pdf["week_id"] = pdf["date"].apply(week_id_of)
    pdf = pdf.sort_values("date")
    pdf = pdf.drop_duplicates(subset=["ticker", "week_id"], keep="last")
    price_wide = pdf.pivot(index="week_id", columns="ticker", values="price")
    week_ids_sorted = sorted(price_wide.index, key=week_sort_key)
    price_wide = price_wide.loc[week_ids_sorted]
    tickers = list(price_wide.columns)
    print("price_wide:", price_wide.shape, "univers:", len(tickers), "tickers")

    with open(os.path.join(REPO, "docs", "indices.json"), encoding="utf-8") as f:
        idx = json.load(f)
    sector_map = {c["ticker"]: c.get("sector") or "Inconnu" for c in idx["companies"]}
    sectors = pd.Series({t: sector_map.get(t, "Inconnu") for t in tickers}, name="sector")
    week_dates = pd.Series([week_id_to_date(w) for w in week_ids_sorted], index=week_ids_sorted)

    print("Calcul phase Weinstein, force relative, signal d'entree (baseline, identique a walk_forward_eval.py)...")
    stage = compute_stage(price_wide)
    entry_signal = compute_entry_signal(stage)
    pct_rank = compute_rs_percentile(price_wide)
    trades_df, concurrent_count, wids = simulate(price_wide, stage, entry_signal, pct_rank, None, sectors)
    trades_df["entry_date"] = trades_df["entry_week_id"].map(week_dates)
    print(f"Trades baseline (tout l'historique) : {len(trades_df)}")

    daily = load_or_build_daily_closes(tickers, rebuild)

    print("Tagging de chaque entree avec les regles trader-scanner actives...")
    for rule in RULES:
        trades_df[rule] = False
    trades_df["n_tags"] = 0
    tags_per_trade = []
    for _, row in trades_df.iterrows():
        tags = tag_entry(daily.get(row["ticker"]), row["entry_date"])
        tags_per_trade.append(tags)
    for i, tags in enumerate(tags_per_trade):
        for rule in tags:
            trades_df.iloc[i, trades_df.columns.get_loc(rule)] = True
        trades_df.iloc[i, trades_df.columns.get_loc("n_tags")] = len(tags)

    n_with_daily = sum(1 for t in trades_df["ticker"] if t in daily)
    print(f"Entrees avec donnee quotidienne disponible : {n_with_daily}/{len(trades_df)}")
    for rule in RULES:
        print(f"  {rule}: actif sur {trades_df[rule].sum()} entrees / {len(trades_df)}")

    # 6 fenetres non chevauchantes ~1 an, identiques a walk_forward_eval.py
    data_start = week_dates.iloc[0]
    data_end = week_dates.iloc[-1] + pd.Timedelta(days=7)
    windows = []
    cur = data_start
    while cur < data_end:
        nxt = cur + pd.DateOffset(years=1)
        windows.append((cur, min(nxt, data_end)))
        cur = nxt

    def buy_hold_window(win_start, win_end):
        w_start_candidates = [w for w in week_ids_sorted if week_dates[w] >= win_start]
        w_end_candidates = [w for w in week_ids_sorted if week_dates[w] < win_end]
        if not w_start_candidates or not w_end_candidates:
            return np.nan
        w_start, w_end = w_start_candidates[0], w_end_candidates[-1]
        p0, p1 = price_wide.loc[w_start], price_wide.loc[w_end]
        valid = p0.notna() & p1.notna() & (p0 > 0)
        return (p1[valid] / p0[valid] - 1.0).mean() * 100

    print("\n=== BASELINE (Weinstein+RS seul, deja etabli) vs FILTRE trader-scanner, par fenetre ===\n")
    results = []
    for i, (w_start, w_end) in enumerate(windows, 1):
        week_in_win = ((week_dates >= w_start) & (week_dates < w_end)).values
        entry_in_win = (trades_df["entry_date"] >= w_start) & (trades_df["entry_date"] < w_end)
        base_sub = trades_df[entry_in_win]
        bh_ret = buy_hold_window(w_start, w_end)
        base_m = metrics_for_subset(base_sub, week_in_win, week_ids_sorted)
        row = dict(fenetre=i, debut=w_start.date(), fin=(w_end - pd.Timedelta(days=1)).date(),
                   buy_hold=bh_ret, base_trades=base_m["n_trades"], base_rendement=base_m["net_return_pct"])
        for rule in RULES:
            rule_sub = base_sub[base_sub[rule]]
            m = metrics_for_subset(rule_sub, week_in_win, week_ids_sorted)
            row[f"{rule}_n"] = m["n_trades"]
            row[f"{rule}_ret"] = m["net_return_pct"]
        results.append(row)

    df = pd.DataFrame(results)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.float_format", lambda x: f"{x:.2f}")
    print(df.to_string(index=False))

    print("\n=== Agrege sur toutes les fenetres (somme ponderee des trades, PAS une moyenne simple des % -- voir note methodologique) ===")
    print(f"Baseline            : {df['base_trades'].sum()} trades, rendement moyen fenetre {df['base_rendement'].mean():.1f}%")
    for rule in RULES:
        n = df[f"{rule}_n"].sum()
        valid = df[f"{rule}_ret"].notna()
        avg = df.loc[valid, f"{rule}_ret"].mean() if valid.any() else float("nan")
        print(f"  filtre {rule:15s}: {n} trades au total, rendement moyen fenetre (fenetres avec >=1 trade) {avg:.1f}%")

    out_path = os.path.join(SCRIPT_DIR, "trader_scanner_factor_results.json")
    df.to_json(out_path, orient="records", date_format="iso", indent=2)
    print(f"\nResultats detailles ecrits dans {out_path}")


if __name__ == "__main__":
    main()
