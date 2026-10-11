"""Grille complete du test du filtre de confirmation par le volume :
baseline (sans filtre) + N in {8,13} semaines x seuil in {1.2,1.5,2.0},
avec metriques apprentissage / validation separees pour chaque variante.

RECHERCHE / EXPLORATOIRE - PAS DU CODE DE PRODUCTION. Voir README.md pour
le resultat retenu (filtre volume rejete) et la methode de selection
(meilleur seuil choisi sur apprentissage seul, valide sans retouche).

Prerequis : avoir lance build_weekly_grid.py au prealable.
"""
import os
import sys
import pickle

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import numpy as np
import pandas as pd
from backtest_engine import (
    load_data, compute_stage, compute_rs_percentile, compute_volume_ratio,
    compute_entry_signal, simulate, week_id_to_date, TRAIN_VAL_CUTOFF, POSITION_NOTIONAL,
)

pd.set_option("display.width", 160)

print("Chargement + calculs communs...")
price_wide, volume_wide, sectors = load_data()
week_ids = list(price_wide.index)
week_dates = pd.Series([week_id_to_date(w) for w in week_ids], index=week_ids)
cutoff = pd.Timestamp(TRAIN_VAL_CUTOFF)
week_is_train = (week_dates < cutoff).values
week_is_val = ~week_is_train

stage = compute_stage(price_wide)
entry_signal = compute_entry_signal(stage)
pct_rank = compute_rs_percentile(price_wide)


def metrics_for_bucket(trades_df, concurrent_count, bucket_mask_weeks, entry_dates):
    if len(trades_df) == 0 or entry_dates.sum() == 0:
        return dict(n_trades=0, net_return_pct=np.nan, capital_base=np.nan,
                     total_pnl=np.nan, max_dd_pct=np.nan, win_rate=np.nan,
                     peak_concurrent=0, calmar=np.nan)
    peak_concurrent = int(concurrent_count[bucket_mask_weeks].max()) if bucket_mask_weeks.any() else 0
    capital_base = peak_concurrent * POSITION_NOTIONAL
    sub = trades_df[entry_dates]
    total_pnl = sub["pnl_eur"].sum()
    net_return_pct = (total_pnl / capital_base * 100) if capital_base > 0 else np.nan
    win_rate = (sub["net_return"] > 0).mean() * 100 if len(sub) else np.nan

    sub_sorted = sub.sort_values("exit_week_idx")
    cum = sub_sorted["pnl_eur"].cumsum()
    running_max = cum.cummax()
    dd = cum - running_max
    max_dd_eur = dd.min() if len(dd) else 0.0
    max_dd_pct = (max_dd_eur / capital_base * 100) if capital_base > 0 else np.nan

    years = max((bucket_mask_weeks.sum() / 52.0), 0.25)
    annualized = (1 + net_return_pct / 100) ** (1 / years) - 1 if net_return_pct > -100 else np.nan
    calmar = (annualized * 100 / abs(max_dd_pct)) if (max_dd_pct and max_dd_pct < 0) else np.nan

    return dict(n_trades=len(sub), net_return_pct=net_return_pct, capital_base=capital_base,
                 total_pnl=total_pnl, max_dd_pct=max_dd_pct, win_rate=win_rate,
                 peak_concurrent=peak_concurrent, calmar=calmar)


def run_variant(vol_ok):
    trades_df, concurrent_count, wids = simulate(price_wide, stage, entry_signal, pct_rank, vol_ok, sectors)
    if len(trades_df) == 0:
        return trades_df, dict(train={}, val={})
    trades_df["entry_date"] = trades_df["entry_week_id"].map(week_dates)
    is_train = trades_df["entry_date"] < cutoff
    is_val = ~is_train
    m_train = metrics_for_bucket(trades_df, concurrent_count, week_is_train, is_train)
    m_val = metrics_for_bucket(trades_df, concurrent_count, week_is_val, is_val)
    return trades_df, dict(train=m_train, val=m_val)


results = []

print("Variante BASELINE (sans filtre volume, seuil=1.0)...")
trades_b, metrics_b = run_variant(None)
results.append(dict(N=None, threshold=1.0, label="sans_filtre", trades=trades_b, metrics=metrics_b))

for N in (8, 13):
    ratio = compute_volume_ratio(volume_wide, N)
    n_missing = ratio.isna().sum().sum()
    print(f"N={N} : valeurs de ratio volume manquantes (hist. insuffisante) = {n_missing}")
    for thr in (1.2, 1.5, 2.0):
        vol_ok = ratio.isna() | (ratio > thr)
        print(f"Variante N={N} seuil>{thr} ...")
        trades_v, metrics_v = run_variant(vol_ok)
        results.append(dict(N=N, threshold=thr, label=f"N{N}_seuil{thr}", trades=trades_v, metrics=metrics_v))

# tableau recapitulatif
rows = []
for r in results:
    mt, mv = r["metrics"]["train"], r["metrics"]["val"]
    rows.append(dict(
        variante=r["label"], N=r["N"], seuil=r["threshold"],
        train_n=mt.get("n_trades"), train_ret=mt.get("net_return_pct"),
        train_dd=mt.get("max_dd_pct"), train_calmar=mt.get("calmar"), train_peak=mt.get("peak_concurrent"),
        val_n=mv.get("n_trades"), val_ret=mv.get("net_return_pct"),
        val_dd=mv.get("max_dd_pct"), val_calmar=mv.get("calmar"), val_peak=mv.get("peak_concurrent"),
    ))
rec_df = pd.DataFrame(rows)
print("\n=== TABLEAU RECAPITULATIF (apprentissage + validation, toutes variantes) ===")
print(rec_df.to_string(index=False))

OUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(OUT_DIR, exist_ok=True)
with open(os.path.join(OUT_DIR, "grid_results.pkl"), "wb") as f:
    pickle.dump(dict(results=results, rec_df=rec_df), f)

print("\nOK -> grid_results.pkl sauvegarde.")
