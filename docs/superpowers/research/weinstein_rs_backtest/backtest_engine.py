"""Moteur de backtest Weinstein + force relative + filtre de volume.

RECHERCHE / EXPLORATOIRE - PAS DU CODE DE PRODUCTION.
Non branche sur le site, le bot IBKR ou tout autre systeme live.
Voir README.md dans ce dossier pour le contexte complet et les resultats.

Reconstruction fidele a la specification donnee lors de l'etude du
2026-10-11 sur le filtre de confirmation par le volume, car le script
original de la session de recherche 2026-10-10/11 (qui avait valide la
strategie de base, +99.3% en validation) n'a pas ete retrouve dans le
depot (aucun commit, aucun fichier conserve). Les choix de reconstruction
sont documentes ci-dessous et dans le README.
"""
import os
import numpy as np
import pandas as pd
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Donnees intermediaires (prix/volume hebdo, pickles) : NON committees (voir
# README). A regenerer localement avec build_weekly_grid.py, ou a recuperer
# depuis le VPS (chemin documente dans le README).
DEFAULT_PROCESSED_DIR = os.path.join(SCRIPT_DIR, "data", "processed")

# --- Parametres de la strategie (donnes par l'utilisateur) ---
MA_WEEKS = 30
SLOPE_LOOKBACK = 4
SLOPE_THRESHOLD_PCT = 0.5
RS_LOOKBACK_WEEKS = 13
RS_TOP_PERCENTILE = 0.70  # "top 20-30%" -> choix retenu : top 30% (borne la plus inclusive
                           # de la fourchette donnee). Hypothese de reconstruction documentee
                           # dans le README (§ Hypotheses de reconstruction).
STOP_LOSS_PCT = -0.10
ROUNDTRIP_COST_PCT = 0.0030
POSITION_NOTIONAL = 500.0
MAX_NEW_ENTRIES_PER_WEEK = 3  # "3 entrees/jour" interprete "par semaine" (donnees hebdomadaires)
MAX_NEW_ENTRIES_PER_SECTOR_PER_WEEK = 3
TRAIN_VAL_CUTOFF = "2024-01-01"


def week_id_to_date(wid: str) -> pd.Timestamp:
    y, w = wid.split("-W")
    # lundi de la semaine ISO
    return pd.Timestamp.fromisocalendar(int(y), int(w), 1)


def load_data(processed_dir: str = DEFAULT_PROCESSED_DIR):
    price_wide = pd.read_pickle(os.path.join(processed_dir, "price_wide.pkl"))
    volume_wide = pd.read_pickle(os.path.join(processed_dir, "volume_wide.pkl"))
    sectors = pd.read_pickle(os.path.join(processed_dir, "sectors.pkl"))["sector"]
    return price_wide, volume_wide, sectors


def compute_stage(price_wide: pd.DataFrame):
    """Classification Weinstein par ticker (vectorisee sur toutes les
    colonnes) : MM30 semaines + pente sur 4 semaines, seuil de bruit 0.5%.
    Reproduit la logique de `classify_weinstein_stage` dans indices_score.py
    (meme MA_WEEKS/SLOPE_LOOKBACK/SLOPE_THRESHOLD_PCT), appliquee ici de
    facon vectorisee a tout l'historique plutot qu'au seul point courant."""
    ma30 = price_wide.rolling(MA_WEEKS, min_periods=MA_WEEKS).mean()
    prior_ma = ma30.shift(SLOPE_LOOKBACK)
    slope_pct = (ma30 - prior_ma) / prior_ma.abs() * 100
    above = price_wide > ma30
    below = price_wide < ma30
    rising = slope_pct > SLOPE_THRESHOLD_PCT
    falling = slope_pct < -SLOPE_THRESHOLD_PCT
    valid = ma30.notna() & prior_ma.notna()

    stage = pd.DataFrame(np.full(price_wide.shape, "Neutre", dtype=object),
                          index=price_wide.index, columns=price_wide.columns)
    stage_vals = stage.values
    achat_mask = (valid & above & rising).values
    declin_mask = (valid & below & falling).values
    stage_vals[achat_mask] = "Achat"
    stage_vals[declin_mask] = "Declin"
    # la ou le prix lui-meme est NaN (ticker pas encore cote), on remet None
    nan_price_mask = price_wide.isna().values
    stage_vals[nan_price_mask] = None
    stage = pd.DataFrame(stage_vals, index=price_wide.index, columns=price_wide.columns)
    return stage


def compute_rs_percentile(price_wide: pd.DataFrame):
    """Rendement sur RS_LOOKBACK_WEEKS semaines, converti en percentile
    cross-sectionnel chaque semaine (NaN exclus du classement via
    na_option='keep')."""
    ret = price_wide.pct_change(RS_LOOKBACK_WEEKS)
    pct_rank = ret.rank(axis=1, pct=True, na_option="keep")
    return pct_rank


def compute_volume_ratio(volume_wide: pd.DataFrame, n_weeks: int):
    """Ratio volume de la semaine / moyenne des n_weeks semaines
    PRECEDENTES (semaine courante exclue de sa propre moyenne de
    reference)."""
    baseline = volume_wide.shift(1).rolling(n_weeks, min_periods=n_weeks).mean()
    ratio = volume_wide / baseline
    return ratio


def compute_entry_signal(stage: pd.DataFrame):
    # ATTENTION : ne pas faire `is_achat.shift(1)` puis `~` sur le resultat.
    # shift() introduit un NaN en premiere ligne -> la colonne bool passe en
    # dtype object (melange bool/NaN) -> `~True`/`~False` en Python sur des
    # bool "objet" donnent -2/-1 (tous deux vrais au sens Python), ce qui
    # annule silencieusement le filtre de transition (bug trouve et corrige
    # pendant cette etude : sans ce fix, 100% des semaines "Achat" etaient
    # comptees comme une nouvelle entree, au lieu de la seule semaine de
    # bascule). On travaille donc en tableaux numpy bool purs.
    is_achat = (stage == "Achat").values  # bool pur (aucun NaN, stage n'a jamais None que pour prix manquant)
    prev_achat = np.vstack([np.zeros((1, is_achat.shape[1]), dtype=bool), is_achat[:-1]])
    entry_arr = is_achat & (~prev_achat)
    return pd.DataFrame(entry_arr, index=stage.index, columns=stage.columns)


def simulate(price_wide, stage, entry_signal, pct_rank, vol_ok, sectors):
    """vol_ok : DataFrame bool (True si le filtre volume est satisfait, ou
    si la donnee est insuffisante -> filtre neutre/auto-passe) ou None
    (pas de filtre volume du tout)."""
    week_ids = list(price_wide.index)
    tickers = list(price_wide.columns)
    n_weeks, n_tick = len(week_ids), len(tickers)

    price_arr = price_wide.values.astype(float)
    stage_arr = stage.values
    entry_arr = entry_signal.values
    rank_arr = pct_rank.values.astype(float)
    if vol_ok is not None:
        volok_arr = vol_ok.values
    else:
        volok_arr = None

    sector_of = {t: sectors.get(t, "Inconnu") for t in tickers}

    open_pos = {}  # ticker_idx -> dict(entry_price, entry_week_idx)
    trades = []  # dict entries
    concurrent_count = np.zeros(n_weeks, dtype=int)

    for wi in range(n_weeks):
        # 1) sorties
        for t_idx in list(open_pos.keys()):
            price_now = price_arr[wi, t_idx]
            if np.isnan(price_now):
                continue
            pos = open_pos[t_idx]
            entry_price = pos["entry_price"]
            ret_now = price_now / entry_price - 1.0
            stop_hit = ret_now <= STOP_LOSS_PCT
            stg = stage_arr[wi, t_idx]
            phase_exit = (stg is not None) and (stg != "Achat")
            if stop_hit or phase_exit:
                reason = "stop" if stop_hit else "fin_phase_achat"
                net_ret = ret_now - ROUNDTRIP_COST_PCT
                trades.append({
                    "ticker": tickers[t_idx],
                    "entry_week_idx": pos["entry_week_idx"],
                    "entry_week_id": week_ids[pos["entry_week_idx"]],
                    "exit_week_idx": wi,
                    "exit_week_id": week_ids[wi],
                    "entry_price": entry_price,
                    "exit_price": price_now,
                    "reason": reason,
                    "net_return": net_ret,
                    "pnl_eur": POSITION_NOTIONAL * net_ret,
                })
                del open_pos[t_idx]

        # 2) nouvelles entrees candidates cette semaine
        candidates = []
        for t_idx in range(n_tick):
            if t_idx in open_pos:
                continue
            if not entry_arr[wi, t_idx]:
                continue
            pr = rank_arr[wi, t_idx]
            if np.isnan(pr) or pr < RS_TOP_PERCENTILE:
                continue
            if volok_arr is not None and not volok_arr[wi, t_idx]:
                continue
            candidates.append((pr, t_idx))
        candidates.sort(reverse=True)  # plus forte force relative d'abord

        selected = 0
        sector_count = defaultdict(int)
        for pr, t_idx in candidates:
            if selected >= MAX_NEW_ENTRIES_PER_WEEK:
                break
            sec = sector_of[tickers[t_idx]]
            if sector_count[sec] >= MAX_NEW_ENTRIES_PER_SECTOR_PER_WEEK:
                continue
            price_now = price_arr[wi, t_idx]
            if np.isnan(price_now):
                continue
            open_pos[t_idx] = {"entry_price": price_now, "entry_week_idx": wi}
            sector_count[sec] += 1
            selected += 1

        concurrent_count[wi] = len(open_pos)

    # positions encore ouvertes a la fin -> cloturees au dernier prix dispo (mark-to-market fin de periode)
    last_wi = n_weeks - 1
    for t_idx, pos in list(open_pos.items()):
        price_now = price_arr[last_wi, t_idx]
        if np.isnan(price_now):
            col = price_arr[:, t_idx]
            valid_idx = np.where(~np.isnan(col))[0]
            if len(valid_idx) == 0:
                continue
            price_now = col[valid_idx[-1]]
        entry_price = pos["entry_price"]
        ret_now = price_now / entry_price - 1.0
        net_ret = ret_now - ROUNDTRIP_COST_PCT
        trades.append({
            "ticker": tickers[t_idx],
            "entry_week_idx": pos["entry_week_idx"],
            "entry_week_id": week_ids[pos["entry_week_idx"]],
            "exit_week_idx": last_wi,
            "exit_week_id": week_ids[last_wi],
            "entry_price": entry_price,
            "exit_price": price_now,
            "reason": "fin_periode_ouverte",
            "net_return": net_ret,
            "pnl_eur": POSITION_NOTIONAL * net_ret,
        })

    trades_df = pd.DataFrame(trades)
    return trades_df, concurrent_count, week_ids
