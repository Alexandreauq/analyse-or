"""Validation rigoureuse du signal `oversold` (RSI14 Wilder < 30) --
seul resultat positif et coherent du test trader-scanner precedent
(trader_scanner_standalone.py, commit a8577ad), mais signale a l'epoque
comme : (1) RSI VECTORISE APPROXIME (ewm, pas la recursion exacte), (2)
une seule passe sur toute la donnee (pas un walk-forward strict), (3)
chevauchement avec le signal Weinstein+RS jamais verifie.

RECHERCHE / EXPLORATOIRE - PAS DU CODE DE PRODUCTION. Complete
trader_scanner_factor_README.md (volet b) et walk_forward_robustness_README.md.

CE SCRIPT REPRODUIT LE RSI EXACT DE PRODUCTION, PAS UNE APPROXIMATION.
Point important decouvert en ecrivant ce script : day-trader-skill/
trader-scanner/scan.py::find_setups() + tag_entry() (trader_scanner_
factor.py:173-183) appellent rsi() sur une fenetre ROULANTE DE 30
CLOTURES SEULEMENT (closes[-30:]), reseedee a chaque jour (le seed =
moyenne simple des 14 premiers deltas DE CETTE FENETRE DE 30, pas un
Wilder convergé sur tout l'historique). C'est DIFFERENT d'un Wilder
"long terme" classique (seede une fois au debut de l'historique puis
jamais reseed) ET different de l'approximation ewm(adjust=False) du
script precedent (qui elle aussi seede une seule fois, au tout premier
jour de l'historique, puis ne reseed jamais). Les trois RSI (exact
production/reseed-30j, Wilder long-terme classique, ewm approxime) ne
sont PAS mathematiquement identiques. Ce script implemente le PREMIER
(exact production), vectorise via sliding_window_view pour la vitesse,
et VALIDE son resultat contre l'implementation de reference ligne par
ligne (trader_scanner_factor.rsi) avant de l'utiliser a l'echelle.
"""
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
REPO = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
DAILY_CACHE_PATH = os.path.join(SCRIPT_DIR, "data", "daily_closes.pkl")

import numpy as np
import pandas as pd

from trader_scanner_factor import rsi as exact_rsi_ref, EXCLUDE_TICKERS
from trader_scanner_standalone import simulate_rule, STOP_LOSS_PCT, ROUNDTRIP_COST_PCT
from backtest_engine import compute_stage, week_id_to_date

N = 14
WINDOW = 30  # == MIN_DAILY_BARS dans trader_scanner_factor.py, fenetre reelle passee a rsi()
HOLD_DAYS_DEFAULT = 20
WINDOWS_1Y = [
    ("2020-10-26", "2021-10-26"), ("2021-10-26", "2022-10-26"),
    ("2022-10-26", "2023-10-26"), ("2023-10-26", "2024-10-26"),
    ("2024-10-26", "2025-10-26"), ("2025-10-26", "2026-10-26"),
]


def rsi_exact_rolling(closes: np.ndarray) -> np.ndarray:
    """RSI Wilder EXACT (reseed a chaque jour sur une fenetre de 30
    clotures), vectorise sur tous les jours a la fois via
    sliding_window_view. Retourne un tableau de longueur
    len(closes)-WINDOW+1, aligne sur closes[WINDOW-1:] (index du DERNIER
    jour de chaque fenetre)."""
    n = len(closes)
    if n < WINDOW:
        return np.array([])
    windows = np.lib.stride_tricks.sliding_window_view(closes, WINDOW)  # (n-29, 30)
    deltas = np.diff(windows, axis=1)  # (n-29, 29)
    gains = np.clip(deltas, 0.0, None)
    losses = np.clip(-deltas, 0.0, None)
    ag = gains[:, :N].mean(axis=1)
    al = losses[:, :N].mean(axis=1)
    for i in range(N, deltas.shape[1]):  # 15 pas de recursion Wilder, vectorises sur toutes les fenetres
        ag = (ag * (N - 1) + gains[:, i]) / N
        al = (al * (N - 1) + losses[:, i]) / N
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = ag / al
        out = 100 - 100 / (1 + rs)
    out = np.where(al == 0.0, 100.0, out)
    return out


def validate_against_reference(daily: dict, n_checks: int = 40) -> None:
    """Confirme que rsi_exact_rolling() reproduit EXACTEMENT (tolerance
    flottante) trader_scanner_factor.rsi() applique a la meme fenetre de
    30 clotures -- condition necessaire avant de faire confiance au
    resultat vectorise a l'echelle de 1296 tickers."""
    rng = np.random.default_rng(42)
    tickers = [t for t in daily if len(daily[t].dropna()) >= WINDOW + 5]
    sample_tickers = rng.choice(tickers, size=min(8, len(tickers)), replace=False)
    checked, max_abs_diff = 0, 0.0
    for tk in sample_tickers:
        closes = daily[tk].dropna().values
        fast = rsi_exact_rolling(closes)
        positions = rng.choice(len(fast), size=min(5, len(fast)), replace=False)
        for pos in positions:
            window = closes[pos:pos + WINDOW].tolist()
            ref = exact_rsi_ref(window)
            diff = abs(fast[pos] - ref)
            max_abs_diff = max(max_abs_diff, diff)
            assert diff < 1e-9, f"{tk} pos={pos}: fast={fast[pos]} ref={ref} diff={diff}"
            checked += 1
    print(f"Validation RSI exact vs reference : {checked} points verifies sur {len(sample_tickers)} tickers, "
          f"ecart max {max_abs_diff:.2e} -- OK" if checked else "AUCUN POINT VERIFIE")


def oversold_series_for_ticker(closes: pd.Series, threshold: float = 30.0):
    """-> (signal bool indexe comme closes, rsi_values alignes)."""
    vals = closes.values
    rsi_vals = rsi_exact_rolling(vals)
    if len(rsi_vals) == 0:
        empty = pd.Series(False, index=closes.index)
        return empty, pd.Series(np.nan, index=closes.index)
    rsi_full = np.full(len(vals), np.nan)
    rsi_full[WINDOW - 1:] = rsi_vals
    sig = pd.Series(rsi_full < threshold, index=closes.index)
    return sig.fillna(False), pd.Series(rsi_full, index=closes.index)


def simulate_rule_custom_horizon(closes: pd.Series, fires: pd.Series, hold_days: int) -> list[dict]:
    idx = closes.index
    vals = closes.values
    n = len(vals)
    fire_positions = np.where(fires.values)[0]
    trades = []
    for pos in fire_positions:
        entry_price = vals[pos]
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue
        exit_pos = min(pos + hold_days, n - 1)
        exit_price = vals[exit_pos]
        for j in range(pos + 1, exit_pos + 1):
            if not np.isfinite(vals[j]):
                continue
            if vals[j] / entry_price - 1.0 <= STOP_LOSS_PCT:
                exit_price = vals[j]
                break
        net_ret = (exit_price / entry_price - 1.0) - ROUNDTRIP_COST_PCT
        trades.append(dict(entry_date=idx[pos], net_return=net_ret, ticker=None))
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return dict(n=0, rendement_moyen_pct=np.nan, rendement_median_pct=np.nan, taux_reussite_pct=np.nan)
    rets = np.array([t["net_return"] for t in trades]) * 100
    return dict(n=len(rets), rendement_moyen_pct=float(rets.mean()),
                rendement_median_pct=float(np.median(rets)), taux_reussite_pct=float((rets > 0).mean() * 100))


def main():
    t0 = time.time()
    print("Chargement des clotures quotidiennes en cache...")
    daily = pd.read_pickle(DAILY_CACHE_PATH)
    daily = {tk: s.dropna() for tk, s in daily.items() if tk not in EXCLUDE_TICKERS}
    print(f"{len(daily)} tickers charges.")

    validate_against_reference(daily)

    # --- 1. Global, RSI exact vs RSI approxime (deja publie) ---
    print("\nCalcul du signal oversold (RSI EXACT, fenetre reseedee 30j) sur tout l'univers...")
    all_trades = []
    oversold_points = []  # (ticker, date) pour le test de chevauchement
    rsi_cache = {}
    for i, (tk, closes) in enumerate(daily.items()):
        if len(closes) < WINDOW + 1:
            continue
        sig, rsi_vals = oversold_series_for_ticker(closes, threshold=30.0)
        rsi_cache[tk] = (closes, rsi_vals)
        trades = simulate_rule(closes, sig)
        for t in trades:
            t["ticker"] = tk
        all_trades.extend(trades)
        for d in closes.index[sig.values]:
            oversold_points.append((tk, d))
        if (i + 1) % 300 == 0:
            print(f"  ...{i+1}/{len(daily)} tickers traites ({time.time()-t0:.0f}s)")

    exact_global = summarize(all_trades)
    print(f"\n=== oversold, RSI EXACT (reseed 30j), horizon {HOLD_DAYS_DEFAULT}j, global ===")
    print(exact_global)
    approx_global = dict(n=61737, rendement_moyen_pct=1.9179840129,
                          rendement_median_pct=1.424144569, taux_reussite_pct=56.4863857978)
    print(f"Pour comparaison, RSI APPROXIME (ewm, script precedent) : {approx_global}")

    # --- 2. Walk-forward strict : 6 fenetres ~1 an, independantes ---
    print("\n=== Decoupage walk-forward, 6 fenetres ~1 an (RSI exact) ===")
    per_window = []
    for w_start_s, w_end_s in WINDOWS_1Y:
        w_start, w_end = pd.Timestamp(w_start_s), pd.Timestamp(w_end_s)
        sub = [t for t in all_trades if w_start <= t["entry_date"] < w_end]
        m = summarize(sub)
        m["debut"], m["fin"] = w_start_s, w_end_s
        per_window.append(m)
        print(f"  {w_start_s} -> {w_end_s} : n={m['n']:6d}  moyenne={m['rendement_moyen_pct']:6.2f}%  "
              f"reussite={m['taux_reussite_pct']:5.1f}%")
    n_positive_windows = sum(1 for m in per_window if m["n"] > 0 and m["rendement_moyen_pct"] > 0)
    print(f"Fenetres positives : {n_positive_windows}/6")

    # --- 3. Sensibilite aux parametres (seuil RSI x horizon de sortie) ---
    print("\n=== Sensibilite seuil RSI x horizon de sortie (global, RSI exact) ===")
    sensitivity = []
    for threshold in (25.0, 30.0, 35.0):
        sig_by_ticker = {}
        for tk, (closes, rsi_vals) in rsi_cache.items():
            sig_by_ticker[tk] = pd.Series(rsi_vals.values < threshold, index=closes.index).fillna(False)
        for hold_days in (15, 20, 25):
            trades_h = []
            for tk, (closes, _) in rsi_cache.items():
                trades_h.extend(simulate_rule_custom_horizon(closes, sig_by_ticker[tk], hold_days))
            m = summarize(trades_h)
            m["seuil_rsi"], m["horizon_j"] = threshold, hold_days
            sensitivity.append(m)
            print(f"  seuil<{threshold:.0f} horizon={hold_days:2d}j : n={m['n']:6d}  "
                  f"moyenne={m['rendement_moyen_pct']:5.2f}%  reussite={m['taux_reussite_pct']:5.1f}%")

    # --- 4. Chevauchement avec le signal Weinstein+RS (stage par semaine) ---
    print("\n=== Chevauchement avec la phase Weinstein (stage hebdo) ===")
    with open(os.path.join(REPO, "docs", "price_history.json"), encoding="utf-8") as f:
        ph = json.load(f)
    pdf = pd.DataFrame(ph)
    pdf["date"] = pd.to_datetime(pdf["date"])
    pdf = pdf[~pdf["ticker"].isin(EXCLUDE_TICKERS)]
    pdf["week_id"] = pdf["date"].apply(lambda d: f"{d.isocalendar().year:04d}-W{d.isocalendar().week:02d}")
    pdf = pdf.sort_values("date").drop_duplicates(subset=["ticker", "week_id"], keep="last")
    price_wide = pdf.pivot(index="week_id", columns="ticker", values="price")
    week_sort = sorted(price_wide.index, key=lambda w: tuple(int(x) for x in w.split("-W")))
    price_wide = price_wide.loc[week_sort]
    stage = compute_stage(price_wide)

    def week_id_of(d: pd.Timestamp) -> str:
        iso = d.isocalendar()
        return f"{iso.year:04d}-W{iso.week:02d}"

    counts = {"Achat": 0, "Declin": 0, "Neutre": 0, "hors_univers_ou_manquant": 0}
    for tk, d in oversold_points:
        wid = week_id_of(d)
        if tk in stage.columns and wid in stage.index:
            v = stage.loc[wid, tk]
            counts[v if v in ("Achat", "Declin", "Neutre") else "hors_univers_ou_manquant"] += 1
        else:
            counts["hors_univers_ou_manquant"] += 1
    total = sum(counts.values())
    print(f"Sur {total} occurrences du signal oversold (RSI exact) :")
    for k, v in counts.items():
        print(f"  {k:28s}: {v:6d} ({v/total*100:5.1f}%)")

    # --- 5. Performance DU TRADE (pas seulement du signal) par regime Weinstein a l'entree ---
    print("\n=== Performance des trades oversold, ventilee par regime Weinstein a l'entree ===")
    perf_by_regime = {}
    for t in all_trades:
        wid = week_id_of(t["entry_date"])
        v = stage.loc[wid, t["ticker"]] if (t["ticker"] in stage.columns and wid in stage.index) else None
        regime = v if v in ("Achat", "Declin", "Neutre") else "hors_univers_ou_manquant"
        perf_by_regime.setdefault(regime, []).append(t["net_return"] * 100)
    regime_breakdown = {}
    for regime, rets in perf_by_regime.items():
        arr = np.array(rets)
        regime_breakdown[regime] = dict(n=len(arr), rendement_moyen_pct=float(arr.mean()),
                                         rendement_median_pct=float(np.median(arr)),
                                         taux_reussite_pct=float((arr > 0).mean() * 100))
        print(f"  {regime:28s}: n={len(arr):6d}  moyenne={arr.mean():5.2f}%  reussite={(arr>0).mean()*100:5.1f}%")

    results = dict(
        global_exact=exact_global, global_approx_reference=approx_global,
        per_window=per_window, n_fenetres_positives=n_positive_windows,
        sensitivity=sensitivity, chevauchement_stage_weinstein=counts,
        n_total_oversold_signals=total, performance_par_regime_weinstein=regime_breakdown,
    )
    out_path = os.path.join(SCRIPT_DIR, "oversold_validation_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str, ensure_ascii=False)
    print(f"\nResultats ecrits dans {out_path} ({time.time()-t0:.0f}s total)")


if __name__ == "__main__":
    main()
