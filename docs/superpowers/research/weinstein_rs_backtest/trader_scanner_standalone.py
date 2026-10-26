"""Test "signal autonome" (volet b) des regles trader-scanner, complement
plus leger du test "filtre sur Weinstein+RS" de trader_scanner_factor.py
(volet a, methodologie principale, resultats dans
trader_scanner_factor_README.md).

RECHERCHE / EXPLORATOIRE - PAS DU CODE DE PRODUCTION.

Difference methodologique volontaire et explicite par rapport au volet (a) :
le volet (a) retague CHAQUE entree avec la fonction find_setups() exacte de
day-trader-skill (boucle Python, RSI exact par recursion Wilder) -- rapide
car seulement 838 entrees. Ici, il faut evaluer le signal sur ~1296 tickers
x ~1500 jours (~2M points), ce qui serait trop lent avec cette meme boucle
Python. Les regles sont donc VECTORISEES (pandas rolling/ewm) -- le RSI
vectorise (ewm Wilder) differe legerement du RSI exact par recursion sur les
tout premiers jours de la fenetre mais converge vers la meme valeur, l'effet
sur le signal binaire (<30 ou non) est negligeable sur un historique de
plusieurs annees. C'est une approximation deliberee pour cette analyse
secondaire, pas une erreur.

Sortie de position : horizon fixe de 20 jours de bourse (~4 semaines), ou
plus tot si stop -10% touche (meme stop que le moteur principal), cout
aller-retour 0.30% identique. PAS le moteur simulate() complet (pas de
notion de phase Weinstein ici, le signal est cense etre autonome).
"""
import os
import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DAILY_CACHE_PATH = os.path.join(SCRIPT_DIR, "data", "daily_closes.pkl")
STOP_LOSS_PCT = -0.10
ROUNDTRIP_COST_PCT = 0.0030
HOLD_DAYS = 20

WINDOWS = [
    ("2020-10-26", "2021-10-26"), ("2021-10-26", "2022-10-26"),
    ("2022-10-26", "2023-10-26"), ("2023-10-26", "2024-10-26"),
    ("2024-10-26", "2025-10-26"), ("2025-10-26", "2026-10-26"),
]


def rsi_vectorized(closes: pd.Series, n: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - 100 / (1 + rs)
    out[(avg_loss == 0.0) & avg_gain.notna()] = 100.0
    return out


def signals_for_ticker(closes: pd.Series) -> pd.DataFrame:
    """DataFrame indexe par date, une colonne bool par regle -- signal
    calcule a la cloture du jour (donc exploitable seulement a partir du
    jour suivant, voir simulate_rule())."""
    hi20 = closes.shift(1).rolling(20).max()
    ma20 = closes.rolling(20).mean()
    ma20_prev = closes.shift(1).rolling(20).mean()
    run = closes.shift(5) / closes.shift(15) - 1
    roll5_max = closes.rolling(5).max()
    roll5_min = closes.rolling(5).min()
    rng5 = (roll5_max - roll5_min) / closes
    r = rsi_vectorized(closes)
    r_prev = r.shift(1)
    prev_close = closes.shift(1)

    out = pd.DataFrame(index=closes.index)
    out["breakout_20d"] = closes > hi20
    out["bull_flag"] = (run > 0.10) & (rng5 < 0.05)
    out["reclaim_ma20"] = (prev_close < ma20_prev) & (closes > ma20)
    out["oversold"] = r < 30
    out["oversold_bounce"] = (r_prev < 30) & (closes > prev_close)
    out["_valid"] = closes.rolling(30).count() >= 30  # MIN_DAILY_BARS
    for col in out.columns:
        if col != "_valid":
            out[col] = out[col].fillna(False) & out["_valid"]
    return out


def simulate_rule(closes: pd.Series, fires: pd.Series) -> list[dict]:
    """Entree au cours de CLOTURE du jour ou `fires` est vrai (le signal est
    connu a la cloture -- en pratique il faudrait entrer le lendemain a
    l'ouverture, simplification acceptee et documentee pour ce complement).
    Sortie au plus tard apres HOLD_DAYS jours de bourse, plus tot si le
    retour cumule <= STOP_LOSS_PCT un jour donne."""
    idx = closes.index
    vals = closes.values
    n = len(vals)
    fire_positions = np.where(fires.values)[0]
    trades = []
    for pos in fire_positions:
        entry_price = vals[pos]
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue
        exit_pos = min(pos + HOLD_DAYS, n - 1)
        exit_price = vals[exit_pos]
        exit_reason = "horizon"
        for j in range(pos + 1, exit_pos + 1):
            if not np.isfinite(vals[j]):
                continue
            ret_j = vals[j] / entry_price - 1.0
            if ret_j <= STOP_LOSS_PCT:
                exit_price = vals[j]
                exit_reason = "stop"
                break
        net_ret = (exit_price / entry_price - 1.0) - ROUNDTRIP_COST_PCT
        trades.append(dict(entry_date=idx[pos], exit_reason=exit_reason, net_return=net_ret))
    return trades


def main():
    daily = pd.read_pickle(DAILY_CACHE_PATH)
    print(f"{len(daily)} tickers charges depuis le cache.")

    rules = ["breakout_20d", "bull_flag", "reclaim_ma20", "oversold", "oversold_bounce"]
    all_trades = {r: [] for r in rules}

    for i, (tk, closes) in enumerate(daily.items()):
        sig = signals_for_ticker(closes)
        for rule in rules:
            trades = simulate_rule(closes, sig[rule])
            for t in trades:
                t["ticker"] = tk
            all_trades[rule].extend(trades)
        if (i + 1) % 300 == 0:
            print(f"  ...{i+1}/{len(daily)} tickers traites")

    # buy&hold de reference : rendement moyen sur HOLD_DAYS jours, calcule
    # sur un echantillonnage regulier (tous les 5 jours) pour comparer a
    # un horizon equivalent, par fenetre.
    bh_rows = []
    for tk, closes in daily.items():
        vals = closes.values
        n = len(vals)
        for pos in range(0, n - HOLD_DAYS, 5):
            p0, p1 = vals[pos], vals[pos + HOLD_DAYS]
            if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                bh_rows.append(dict(date=closes.index[pos], ret=(p1 / p0 - 1.0) * 100))
    bh_df = pd.DataFrame(bh_rows)

    print(f"\n=== Test signal autonome (horizon fixe {HOLD_DAYS}j bourse, stop {STOP_LOSS_PCT*100:.0f}%, cout {ROUNDTRIP_COST_PCT*100:.2f}%) ===\n")
    print(f"Buy&hold de reference (meme horizon, tous tickers, tout l'historique) : moyenne {bh_df['ret'].mean():.2f}%, mediane {bh_df['ret'].median():.2f}%, n={len(bh_df)}\n")

    summary_rows = []
    for rule in rules:
        tdf = pd.DataFrame(all_trades[rule])
        if tdf.empty:
            print(f"{rule:16s}: 0 signal sur tout l'historique")
            summary_rows.append(dict(regle=rule, n_signaux=0))
            continue
        tdf["ret_pct"] = tdf["net_return"] * 100
        win_rate = (tdf["net_return"] > 0).mean() * 100
        print(f"{rule:16s}: {len(tdf):6d} signaux | rendement moyen {tdf['ret_pct'].mean():6.2f}% | "
              f"mediane {tdf['ret_pct'].median():6.2f}% | taux reussite {win_rate:5.1f}% | "
              f"sorties stop {(tdf['exit_reason']=='stop').mean()*100:.1f}%")
        summary_rows.append(dict(regle=rule, n_signaux=len(tdf), rendement_moyen_pct=tdf["ret_pct"].mean(),
                                  rendement_median_pct=tdf["ret_pct"].median(), taux_reussite_pct=win_rate))

    out_path = os.path.join(SCRIPT_DIR, "trader_scanner_standalone_results.json")
    pd.DataFrame(summary_rows).to_json(out_path, orient="records", indent=2)
    print(f"\nResume ecrit dans {out_path}")


if __name__ == "__main__":
    main()
