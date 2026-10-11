"""Backtest de la variante BASELINE (sans filtre de volume) : metriques
apprentissage / validation, verification des 10 plus gros trades, et
buy & hold equipondere pour comparaison.

RECHERCHE / EXPLORATOIRE - PAS DU CODE DE PRODUCTION. Voir README.md.

Prerequis : avoir lance build_weekly_grid.py au prealable (genere
data/processed/*.pkl, non committes).
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
    compute_entry_signal, simulate, week_id_to_date, TRAIN_VAL_CUTOFF,
    POSITION_NOTIONAL,
)

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)

print("Chargement des donnees...")
price_wide, volume_wide, sectors = load_data()
week_ids = list(price_wide.index)
week_dates = pd.Series([week_id_to_date(w) for w in week_ids], index=week_ids)
cutoff = pd.Timestamp(TRAIN_VAL_CUTOFF)

print("Calcul phase Weinstein, force relative...")
stage = compute_stage(price_wide)
entry_signal = compute_entry_signal(stage)
pct_rank = compute_rs_percentile(price_wide)

n_entries_total = int(entry_signal.values.sum())
print("Nb signaux d'entree bruts (transition vers Achat, toutes dates):", n_entries_total)


def metrics_for_bucket(trades_df, concurrent_count, bucket_mask_weeks, entry_dates):
    """bucket_mask_weeks: array bool par semaine (True si la semaine appartient
    a la fenetre consideree) ; entry_dates: Series bool (True si le trade est
    entre dans cette fenetre)."""
    if len(trades_df) == 0:
        return dict(n_trades=0, net_return_pct=np.nan, capital_base=np.nan,
                     total_pnl=np.nan, max_dd_pct=np.nan, win_rate=np.nan,
                     peak_concurrent=0, calmar=np.nan)
    peak_concurrent = int(concurrent_count[bucket_mask_weeks].max()) if bucket_mask_weeks.any() else 0
    capital_base = peak_concurrent * POSITION_NOTIONAL
    sub = trades_df[entry_dates]
    total_pnl = sub["pnl_eur"].sum()
    net_return_pct = (total_pnl / capital_base * 100) if capital_base > 0 else np.nan
    win_rate = (sub["net_return"] > 0).mean() * 100 if len(sub) else np.nan

    # drawdown : courbe d'equite a partir des trades du bucket, ordonnes par semaine de sortie
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

    week_is_train = (week_dates < cutoff).values
    week_is_val = ~week_is_train

    m_train = metrics_for_bucket(trades_df, concurrent_count, week_is_train, is_train)
    m_val = metrics_for_bucket(trades_df, concurrent_count, week_is_val, is_val)
    return trades_df, dict(train=m_train, val=m_val)


print("\n=== BASELINE (sans filtre de volume) ===")
trades_base, metrics_base = run_variant(None)
print("Apprentissage:", metrics_base["train"])
print("Validation   :", metrics_base["val"])

print("\nNombre total de trades (baseline):", len(trades_base))
print("Repartition des motifs de sortie:\n", trades_base["reason"].value_counts())

print("\n--- 10 plus gros trades (valeur absolue de PnL) ---")
top10 = trades_base.reindex(trades_base["pnl_eur"].abs().sort_values(ascending=False).index).head(10)
print(top10[["ticker", "entry_week_id", "exit_week_id", "entry_price", "exit_price", "net_return", "pnl_eur", "reason"]])

print("\n--- Buy & hold equipondere ---")


def buy_hold(start_date, end_date):
    w_start_candidates = [w for w in week_ids if week_dates[w] >= start_date]
    w_end_candidates = [w for w in week_ids if week_dates[w] <= end_date]
    if not w_start_candidates or not w_end_candidates:
        return np.nan, 0
    w_start = w_start_candidates[0]
    w_end = w_end_candidates[-1]
    p0 = price_wide.loc[w_start]
    p1 = price_wide.loc[w_end]
    valid = p0.notna() & p1.notna() & (p0 > 0)
    rets = (p1[valid] / p0[valid] - 1.0)
    return rets.mean() * 100, int(valid.sum())


bh_train_ret, bh_train_n = buy_hold(week_dates.iloc[0], cutoff - pd.Timedelta(days=1))
bh_val_ret, bh_val_n = buy_hold(cutoff, week_dates.iloc[-1])
print(f"Buy&hold equipondere apprentissage : {bh_train_ret:.1f}% (n={bh_train_n} tickers)")
print(f"Buy&hold equipondere validation    : {bh_val_ret:.1f}% (n={bh_val_n} tickers)")

OUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(OUT_DIR, exist_ok=True)
with open(os.path.join(OUT_DIR, "baseline_cache.pkl"), "wb") as f:
    pickle.dump(dict(trades_base=trades_base, metrics_base=metrics_base,
                      bh_train_ret=bh_train_ret, bh_val_ret=bh_val_ret), f)
print("\nOK baseline sauvegarde.")
