"""Renforcement de la validation walk-forward du signal Weinstein+RS :

AXE A -- fenetres glissantes mensuelles (~1 an chacune) sur toute la
periode, en plus des 6 fenetres fixes non chevauchantes de
walk_forward_eval.py, pour voir si l'edge est stable dans le temps ou
concentre sur quelques periodes chanceuses.
ATTENTION : des fenetres qui se chevauchent a 90%+ ne sont PAS des
echantillons independants. Elles completent l'analyse des 6 fenetres
disjointes, elles ne la remplacent pas.

AXE B -- sensibilite du signal au seuil RS_TOP_PERCENTILE (0.70,
documente comme un choix arbitraire dans le README, § Hypotheses de
reconstruction point 2 : "top 20-30%" operationnalise a la borne la
plus inclusive). Si l'edge s'effondre pour de petites perturbations de
ce seuil sur les 6 fenetres d'origine, c'est un signe de surajustement
meme si le resultat central (seuil 0.70) est positif.

RECHERCHE / EXPLORATOIRE - PAS DU CODE DE PRODUCTION. Ne modifie jamais
backtest_engine.py ni walk_forward_eval.py (reutilises en lecture) ; ne
touche jamais a docs/price_history.json ni a aucun fichier de
production. RS_TOP_PERCENTILE est mute via l'objet module backtest_engine
(be.RS_TOP_PERCENTILE = ...) plutot que par edit de fichier : simulate()
lit ce nom comme global de son propre module a chaque appel, donc la
mutation en memoire du process suffit et n'affecte aucun autre script.
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
REPO = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))

import numpy as np
import pandas as pd
import backtest_engine as be
from backtest_engine import (
    compute_stage, compute_rs_percentile, compute_entry_signal, simulate,
    week_id_to_date, POSITION_NOTIONAL,
)

EXCLUDE_TICKERS = {
    "^AEX", "^DJI", "^FCHI", "^FTSE", "^GDAXI", "^GSPC", "^HSI", "^IBEX",
    "^N225", "^NDX", "^SBF120", "^SSMI", "^STOXX50E", "FTSEMIB.MI",
    "MT.PA", "IHG.L", "MTLN.L", "CPG.L",
}


def week_id_of(d: pd.Timestamp) -> str:
    iso = d.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def week_sort_key(wid: str):
    y, w = wid.split("-W")
    return (int(y), int(w))


print("Construction de price_wide depuis docs/price_history.json (identique a walk_forward_eval.py)...")
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
print("price_wide:", price_wide.shape, "premiere semaine", week_ids_sorted[0], "derniere", week_ids_sorted[-1])

with open(os.path.join(REPO, "docs", "indices.json"), encoding="utf-8") as f:
    idx = json.load(f)
sector_map = {c["ticker"]: c.get("sector") or "Inconnu" for c in idx["companies"]}
sectors = pd.Series({t: sector_map.get(t, "Inconnu") for t in price_wide.columns}, name="sector")

week_dates = pd.Series([week_id_to_date(w) for w in week_ids_sorted], index=week_ids_sorted)
data_start = week_dates.iloc[0]
data_end = week_dates.iloc[-1] + pd.Timedelta(days=7)

print("Calcul phase Weinstein + force relative (independants du seuil RS_TOP_PERCENTILE)...")
stage = compute_stage(price_wide)
entry_signal = compute_entry_signal(stage)
pct_rank = compute_rs_percentile(price_wide)


def metrics_for_window(trades_df, concurrent_count, win_start, win_end):
    week_in_win = ((week_dates >= win_start) & (week_dates < win_end)).values
    entry_in_win = (trades_df["entry_date"] >= win_start) & (trades_df["entry_date"] < win_end)
    sub = trades_df[entry_in_win]
    if len(sub) == 0:
        return dict(n_trades=0, net_return_pct=np.nan)
    peak_concurrent = int(concurrent_count[week_in_win].max()) if week_in_win.any() else 0
    capital_base = peak_concurrent * POSITION_NOTIONAL
    total_pnl = sub["pnl_eur"].sum()
    net_return_pct = (total_pnl / capital_base * 100) if capital_base > 0 else np.nan
    return dict(n_trades=len(sub), net_return_pct=net_return_pct)


def buy_hold_window(win_start, win_end):
    w_start_candidates = [w for w in week_ids_sorted if week_dates[w] >= win_start]
    w_end_candidates = [w for w in week_ids_sorted if week_dates[w] < win_end]
    if not w_start_candidates or not w_end_candidates:
        return np.nan, 0
    w_start, w_end = w_start_candidates[0], w_end_candidates[-1]
    p0, p1 = price_wide.loc[w_start], price_wide.loc[w_end]
    valid = p0.notna() & p1.notna() & (p0 > 0)
    rets = (p1[valid] / p0[valid] - 1.0)
    return rets.mean() * 100, int(valid.sum())


def run_simulation(rs_top_percentile):
    be.RS_TOP_PERCENTILE = rs_top_percentile
    trades_df, concurrent_count, _wids = simulate(price_wide, stage, entry_signal, pct_rank, None, sectors)
    trades_df["entry_date"] = trades_df["entry_week_id"].map(week_dates)
    return trades_df, concurrent_count


# ============================================================
# AXE A : fenetres glissantes mensuelles (~1 an chacune), seuil baseline 0.70
# ============================================================
print("\n=== AXE A : fenetres glissantes mensuelles (seuil baseline 0.70) ===")
baseline_trades, baseline_concurrent = run_simulation(0.70)

rolling_windows = []
cur = data_start
while cur + pd.DateOffset(years=1) <= data_end:
    rolling_windows.append((cur, cur + pd.DateOffset(years=1)))
    cur = cur + pd.DateOffset(months=1)

rows_a = []
for w_start, w_end in rolling_windows:
    m = metrics_for_window(baseline_trades, baseline_concurrent, w_start, w_end)
    bh_ret, _ = buy_hold_window(w_start, w_end)
    ecart = (m["net_return_pct"] - bh_ret) if (not np.isnan(m["net_return_pct"]) and not np.isnan(bh_ret)) else np.nan
    rows_a.append(dict(debut=w_start.date(), fin=(w_end - pd.Timedelta(days=1)).date(),
                        trades=m["n_trades"], strat=m["net_return_pct"], buy_hold=bh_ret, ecart=ecart))

df_a = pd.DataFrame(rows_a)
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)
pd.set_option("display.float_format", lambda x: f"{x:.2f}")
print(df_a.to_string(index=False))

n_valid_a = int(df_a["ecart"].notna().sum())
n_beats_a = int((df_a["ecart"] > 0).sum())
print(f"\n{len(df_a)} fenetres glissantes mensuelles (chevauchement massif -- PAS des echantillons independants)")
print(f"Fenetres ou la strategie bat le buy&hold : {n_beats_a}/{n_valid_a}")
print(f"Ecart strategie-buy&hold -- moyenne: {df_a['ecart'].mean():.1f} pts  mediane: {df_a['ecart'].median():.1f} pts  "
      f"ecart-type: {df_a['ecart'].std():.1f} pts  min: {df_a['ecart'].min():.1f} pts  max: {df_a['ecart'].max():.1f} pts")

# ============================================================
# AXE B : sensibilite au seuil RS_TOP_PERCENTILE, 6 fenetres disjointes d'origine
# ============================================================
print("\n=== AXE B : sensibilite au seuil RS_TOP_PERCENTILE, 6 fenetres disjointes ===")

fixed_windows = []
cur = data_start
while cur < data_end:
    nxt = cur + pd.DateOffset(years=1)
    fixed_windows.append((cur, min(nxt, data_end)))
    cur = nxt

percentiles_to_test = [0.60, 0.65, 0.70, 0.75, 0.80]
rows_b = []
detail_b = {}
for p in percentiles_to_test:
    trades_p, conc_p = run_simulation(p)
    n_beats, n_valid, ecarts = 0, 0, []
    for w_start, w_end in fixed_windows:
        m = metrics_for_window(trades_p, conc_p, w_start, w_end)
        bh_ret, _ = buy_hold_window(w_start, w_end)
        if np.isnan(m["net_return_pct"]) or np.isnan(bh_ret):
            continue
        n_valid += 1
        ecart = m["net_return_pct"] - bh_ret
        ecarts.append(ecart)
        if ecart > 0:
            n_beats += 1
    detail_b[p] = ecarts
    rows_b.append(dict(seuil_percentile=p, fenetres_beats=f"{n_beats}/{n_valid}",
                        ecart_moyen_pts=np.mean(ecarts) if ecarts else np.nan,
                        n_trades_total=len(trades_p)))

df_b = pd.DataFrame(rows_b)
print(df_b.to_string(index=False))
print("\nEcarts detailles par fenetre et par seuil :")
for p, ecarts in detail_b.items():
    print(f"  seuil {p:.2f} -> " + ", ".join(f"{e:+.1f}" for e in ecarts))

be.RS_TOP_PERCENTILE = 0.70  # restaure la valeur par defaut avant de quitter
