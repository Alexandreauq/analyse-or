# docs/superpowers/research/gold_bot_validate_fib_trend_filter_nolookahead/walk_forward_filter_nolookahead.py
# RECHERCHE / EXPLORATOIRE -- PAS DU CODE DE PRODUCTION.
#
# Reprend EXACTEMENT la validation walk-forward de
# ../gold_bot_validate_fib_trend_filter/walk_forward_filter.py (meme
# fenetres annuelles 2021-2026, meme grille de seuils, meme fitness
# train = R moyen par trade, meme evaluation FTMO finale) mais corrige
# un biais de lookahead trouve en revue de code final avant deploiement :
#
#   diagnose_and_improve.tag_trend_strength() tague chaque trade avec
#   strength_by_day.get(trade["entry_time"][:10], 0.0) -- la force de
#   tendance du jour calendaire DE L'ENTREE DU TRADE LUI-MEME. Mais
#   diagnose_and_improve.daily_trend_strength() calcule la force de ce
#   jour avec by_day[d][-1]["close"] -- la bougie DE CLOTURE de ce
#   meme jour (~22h). Un trade ouvert a 14h le jour J est donc tague
#   avec une donnee qui n'existe pas encore au moment de la decision
#   reelle (lookahead). En production, seule la force du dernier jour
#   calendaire COMPLET precedent est disponible.
#
# Ce script ne modifie PAS diagnose_and_improve.py (deja utilise par
# d'autres forks, correction production faite separement, hors scope
# ici) -- il reutilise daily_trend_strength() SANS changement (le calcul
# de la force par jour n'est pas le bug, c'est son USAGE qui l'est), et
# n'importe tag_trend_strength() que pour le run de sanity-check "avant"
# (reproduire le chiffre original tainted). La fonction corrigee
# tag_trend_strength_nolookahead() est ecrite ici, c'est le seul
# morceau de logique nouveau de tout ce fork.
from __future__ import annotations

import bisect
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DEEPEN_WINNERS = os.path.join(HERE, "..", "gold_bot_deepen_winners")
DEEPEN_FIB = os.path.join(HERE, "..", "gold_bot_deepen_fibonacci")
FIB_DIR = os.path.join(HERE, "..", "gold_bot_fibonacci_seasonality")
SR_DIR = os.path.join(HERE, "..", "gold_bot_sr_breakout")
FTMO_CHECK_DIR = os.path.join(HERE, "..", "gold_bot_ftmo_check")
REPO_ROOT = os.path.join(HERE, "..", "..", "..", "..")

for p in (DEEPEN_WINNERS, DEEPEN_FIB, FIB_DIR, SR_DIR, FTMO_CHECK_DIR, REPO_ROOT):
    sys.path.insert(0, p)

import fibonacci_two_leg as f2l  # noqa: E402
import generate_breakout_trades as srb  # noqa: E402
from diagnose_and_improve import daily_trend_strength, tag_trend_strength  # noqa: E402 -- reutilise sans reimplementer
from ftmo_pass_rate import simulate_challenge_from_quantized, _effective_distances, VERIFICATION_TARGET_PCT  # noqa: E402

CANDLES_CACHE = srb.CANDLES_CACHE
ISC = 10_000.0
RISK_PCTS = (0.01, 0.02)

WINDOW = 40
FIB_PCT = 0.618
TARGET_R = 1.5
TREND_WINDOW_DAYS = 20  # identique a daily_trend_strength(window_days=20) dans walk_forward_filter.py

THRESHOLD_GRID = [0.05, 0.06, 0.08, 0.10, 0.12, 0.15]
TEST_YEARS = [2021, 2022, 2023, 2024, 2025, 2026]


# ---------------------------------------------------------------------------
# Correction du lookahead : au lieu de strength_by_day.get(entry_day, 0.0)
# (force du jour DU TRADE, calculee avec la cloture du soir -- pas encore
# connue au moment de l'entree), on prend la force du dernier jour
# calendaire STRICTEMENT AVANT l'entree qui figure dans strength_by_day.
# Si ce jour precedent n'a lui-meme pas encore window_days jours complets
# d'historique derriere lui (placeholder 0.0 de daily_trend_strength,
# pas une vraie force), ou s'il n'y a tout simplement aucun jour avant,
# le trade est "non mesurable" -> exclu du jeu filtre, jamais defaulte
# a 0.0 (ce qui le ferait passer le filtre a tort). Memes mecanique que
# le correctif production (bloquer plutot que presumer).
# ---------------------------------------------------------------------------
def tag_trend_strength_nolookahead(trades: list[dict], strength_by_day: dict[str, float],
                                    window_days: int = TREND_WINDOW_DAYS) -> tuple[list[dict], int]:
    days_sorted = sorted(strength_by_day.keys())
    out = []
    excluded_unmeasurable = 0
    for t in trades:
        entry_day = t["entry_time"][:10]
        pos = bisect.bisect_left(days_sorted, entry_day)
        prev_pos = pos - 1
        if prev_pos < 0 or prev_pos < window_days:
            # Pas de jour calendaire complet precedent, ou ce jour
            # precedent est lui-meme dans la zone placeholder (moins de
            # window_days jours d'historique derriere lui) -> non mesurable.
            excluded_unmeasurable += 1
            continue
        s = strength_by_day[days_sorted[prev_pos]]
        out.append({**t, "_trend_strength": s})
    return out, excluded_unmeasurable


def mean_r(trades: list[dict]) -> float | None:
    if not trades:
        return None
    return sum(t["r_final"] for t in trades) / len(trades)


def trades_in_range(tagged: list[dict], start: datetime | None, end: datetime | None) -> list[dict]:
    out = []
    for t in tagged:
        et = datetime.fromisoformat(t["entry_time"])
        if start is not None and et < start:
            continue
        if end is not None and et >= end:
            continue
        out.append(t)
    return out


def filter_by_threshold(tagged: list[dict], threshold: float) -> list[dict]:
    return [t for t in tagged if abs(t["_trend_strength"]) <= threshold]


def load_trades_with_dt(raw: list[dict]) -> list[dict]:
    trades = [{**t, "entry_time": datetime.fromisoformat(t["entry_time"]),
               "exit_time": datetime.fromisoformat(t["exit_time"])} for t in raw]
    trades.sort(key=lambda t: t["entry_time"])
    return trades


def evaluate_ftmo(trades_dt: list[dict], risk_pct: float, isc: float = ISC) -> dict:
    n = len(trades_dt)
    if n == 0:
        return {"n": 0, "pass_rate": 0.0, "median_days": None}
    distances = _effective_distances(trades_dt)
    reussites = 0
    full_days = []
    for start_idx in range(n):
        ch = simulate_challenge_from_quantized(trades_dt, distances, start_idx, risk_pct, isc)
        if ch["issue"] != "objectif_atteint":
            continue
        vs = start_idx + ch["n_trades"]
        if vs >= n:
            continue
        ve = simulate_challenge_from_quantized(trades_dt, distances, vs, risk_pct, isc, profit_target_pct=VERIFICATION_TARGET_PCT)
        if ve["issue"] != "objectif_atteint":
            continue
        reussites += 1
        cd = (trades_dt[start_idx + ch["n_trades"] - 1]["exit_time"] - trades_dt[start_idx]["entry_time"]).days
        vd = (trades_dt[vs + ve["n_trades"] - 1]["exit_time"] - trades_dt[vs]["entry_time"]).days
        full_days.append(cd + vd)
    result = {"n": n, "pass_rate": round(100 * reussites / n, 1), "median_days": None}
    if full_days:
        result["median_days"] = statistics.median(full_days)
    return result


def run_walk_forward(tagged: list[dict], label: str) -> dict:
    """Meme methodologie walk-forward que walk_forward_filter.py, factorisee
    pour pouvoir etre rejouee a l'identique sur le tag "avant" (lookahead,
    sanity-check) et le tag "apres" (corrige, resultat a rapporter)."""
    results = {"per_window": {}, "grid_tested": THRESHOLD_GRID}
    chosen_thresholds = []
    generalizes_count = 0
    print(f"\n=== [{label}] Walk-forward du seuil de filtre de tendance (6 fenetres annuelles) ===")
    for test_year in TEST_YEARS:
        train_end = datetime(test_year, 1, 1, tzinfo=timezone.utc)
        test_end = datetime(test_year + 1, 1, 1, tzinfo=timezone.utc)
        train_trades = trades_in_range(tagged, None, train_end)
        test_trades = trades_in_range(tagged, train_end, test_end)

        best_threshold = None
        best_fitness = None
        train_fitness_by_threshold = {}
        for th in THRESHOLD_GRID:
            kept = filter_by_threshold(train_trades, th)
            m = mean_r(kept)
            train_fitness_by_threshold[th] = {"n": len(kept), "mean_r": round(m, 4) if m is not None else None}
            if m is None:
                continue
            if best_fitness is None or m > best_fitness:
                best_fitness = m
                best_threshold = th
        chosen_thresholds.append(best_threshold)

        test_unfiltered_r = mean_r(test_trades)
        test_filtered = filter_by_threshold(test_trades, best_threshold) if best_threshold is not None else test_trades
        test_filtered_r = mean_r(test_filtered)
        improves = (test_filtered_r is not None and test_unfiltered_r is not None
                    and test_filtered_r > test_unfiltered_r)
        if improves:
            generalizes_count += 1

        print(f"\n-- [{label}] Test {test_year} -- train: {len(train_trades)} jambes, test: {len(test_trades)} jambes --")
        print(f"   Seuil gagnant sur train : {best_threshold:.0%}" if best_threshold is not None else "   Aucun seuil retenu (train vide)")
        print(f"   R moyen test SANS filtre : {round(test_unfiltered_r, 4) if test_unfiltered_r is not None else None}"
              f" (n={len(test_trades)})")
        print(f"   R moyen test AVEC filtre ({best_threshold:.0%}) : {round(test_filtered_r, 4) if test_filtered_r is not None else None}"
              f" (n={len(test_filtered)})")
        print(f"   Ameliore le test ? {'OUI' if improves else 'non'}")

        results["per_window"][test_year] = {
            "train_n": len(train_trades), "test_n": len(test_trades),
            "train_fitness_by_threshold": {str(k): v for k, v in train_fitness_by_threshold.items()},
            "chosen_threshold": best_threshold,
            "test_unfiltered_mean_r": round(test_unfiltered_r, 4) if test_unfiltered_r is not None else None,
            "test_filtered_mean_r": round(test_filtered_r, 4) if test_filtered_r is not None else None,
            "test_filtered_n": len(test_filtered),
            "improves_on_test": improves,
        }

    counts = Counter(chosen_thresholds)
    print(f"\n=== [{label}] Stabilite du seuil sur les 6 fenetres ===")
    for th, c in counts.most_common():
        print(f"  {th:.0%} : choisi par {c}/6 fenetres" if th is not None else f"  aucun : {c}/6 fenetres")
    most_common_threshold, most_common_count = counts.most_common(1)[0]
    print(f"\n[{label}] Seuil le plus frequent : {most_common_threshold:.0%} ({most_common_count}/6)")
    print(f"[{label}] Generalisation reelle (ameliore le R moyen test vs sans filtre) : {generalizes_count}/6 annees")

    results["stability"] = {str(k): v for k, v in counts.items()}
    results["most_common_threshold"] = most_common_threshold
    results["most_common_count"] = most_common_count
    results["generalizes_count"] = generalizes_count

    print(f"\n=== [{label}] Evaluation FTMO finale (seuil={most_common_threshold:.0%}, integralite des 8.5 ans) ===")
    final_filtered_raw = [
        {k: v for k, v in t.items() if k != "_trend_strength"}
        for t in tagged if abs(t["_trend_strength"]) <= most_common_threshold
    ]
    print(f"[{label}] {len(final_filtered_raw)} jambes retenues sur {len(tagged)} taguees")
    final_filtered_dt = load_trades_with_dt(final_filtered_raw)

    results["final_eval"] = {}
    for rp in RISK_PCTS:
        res = evaluate_ftmo(final_filtered_dt, rp)
        print(f"  [{label}] risk_pct={rp:.0%} -> {res['pass_rate']}% reussite / {res['median_days']}j median (n={res['n']})")
        results["final_eval"][str(rp)] = res

    # Comparaison a 5% fixe (seuil annonce par le chantier original) meme si
    # ce n'est pas le plus frequent ici, pour une comparaison directe chiffre
    # a chiffre avec le README du fork precedent.
    fixed5_raw = [
        {k: v for k, v in t.items() if k != "_trend_strength"}
        for t in tagged if abs(t["_trend_strength"]) <= 0.05
    ]
    fixed5_dt = load_trades_with_dt(fixed5_raw)
    results["fixed_5pct_eval"] = {"n_tagged_kept": len(fixed5_raw)}
    print(f"\n=== [{label}] Pour comparaison directe : seuil fixe a 5% (chiffre annonce par le fork precedent) ===")
    for rp in RISK_PCTS:
        res = evaluate_ftmo(fixed5_dt, rp)
        print(f"  [{label}] (seuil fixe 5%) risk_pct={rp:.0%} -> {res['pass_rate']}% reussite / {res['median_days']}j median (n={res['n']})")
        results["fixed_5pct_eval"][str(rp)] = res

    return results


def main():
    with open(CANDLES_CACHE, encoding="utf-8") as fh:
        candles = json.load(fh)
    print(f"{len(candles)} bougies 5min chargees")

    print(f"Generation des trades Fibonacci deux jambes (window={WINDOW}, fib_pct={FIB_PCT}, target_r={TARGET_R})...")
    trades_raw = f2l.generate_fibonacci_two_leg_trades(candles, window=WINDOW, fib_pct=FIB_PCT, target_r=TARGET_R)
    print(f"{len(trades_raw)} jambes generees au total")

    strength = daily_trend_strength(candles, window_days=TREND_WINDOW_DAYS)

    all_results: dict = {"n_trades_raw": len(trades_raw)}

    # --- "AVANT" : sanity-check, meme tag AVEC lookahead que le fork
    # precedent -- doit reproduire ~25.4%/29j a risk_pct=2% avec le seuil
    # 5% choisi par le fork precedent, a partir des memes donnees en cache.
    tagged_before = tag_trend_strength(trades_raw, strength)
    print(f"\n[AVANT/lookahead] {len(tagged_before)} jambes taguees (aucune exclusion, tag = jour de l'entree lui-meme)")
    results_before = run_walk_forward(tagged_before, "AVANT (lookahead, sanity-check)")
    all_results["before_lookahead"] = results_before

    # --- "APRES" : tag corrige, dernier jour calendaire COMPLET avant
    # l'entree, jamais le jour de l'entree lui-meme. Trades non mesurables
    # (avant que window_days jours complets d'historique existent) exclus.
    tagged_after, excluded = tag_trend_strength_nolookahead(trades_raw, strength, window_days=TREND_WINDOW_DAYS)
    print(f"\n[APRES/corrige] {len(tagged_after)} jambes taguees, {excluded} exclues (non mesurables, pas de jour complet precedent)")
    all_results["n_excluded_unmeasurable"] = excluded
    results_after = run_walk_forward(tagged_after, "APRES (corrige, sans lookahead)")
    all_results["after_nolookahead"] = results_after

    # --- Resume comparatif ---
    print("\n" + "=" * 78)
    print("RESUME COMPARATIF")
    print("=" * 78)
    b = results_before
    a = results_after
    print(f"Seuil le plus frequent -- AVANT: {b['most_common_threshold']:.0%} ({b['most_common_count']}/6)"
          f"  |  APRES: {a['most_common_threshold']:.0%} ({a['most_common_count']}/6)")
    print(f"Generalisation hors-echantillon -- AVANT: {b['generalizes_count']}/6  |  APRES: {a['generalizes_count']}/6")
    for rp in RISK_PCTS:
        rb = b["final_eval"][str(rp)]
        ra = a["final_eval"][str(rp)]
        print(f"risk_pct={rp:.0%} (seuil le + frequent de chaque run) -- "
              f"AVANT: {rb['pass_rate']}%/{rb['median_days']}j (n={rb['n']})  |  "
              f"APRES: {ra['pass_rate']}%/{ra['median_days']}j (n={ra['n']})")

    out_path = os.path.join(HERE, "walk_forward_filter_nolookahead_results.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(all_results, fh, indent=2, default=str)
    print(f"\nEcrit dans {out_path}")


if __name__ == "__main__":
    main()
