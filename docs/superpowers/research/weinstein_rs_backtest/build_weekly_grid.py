"""Etape 1 : construit une grille hebdomadaire uniforme (prix + volume)
alignee sur les semaines calendaires ISO, a partir de :
  - docs/price_history.json (prix, deja quasi-hebdomadaire mais avec un
    jour d'ancrage qui glisse legerement d'une semaine a l'autre)
  - les CSV quotidiens de volume telecharges sur le VPS (voir README pour
    le chemin exact et comment les recuperer)

RECHERCHE / EXPLORATOIRE - PAS DU CODE DE PRODUCTION. Voir README.md.

Sauvegarde price_wide.pkl, volume_wide.pkl et sectors.pkl dans le dossier
de sortie (index = week_id "YYYY-Www" trie chronologiquement,
colonnes = tickers). Ces pickles ne sont PAS committes (donnees
derivees volumineuses) : a regenerer localement.

Usage: python build_weekly_grid.py [raw_volume_dir] [out_dir]
"""
import json
import os
import sys
import pandas as pd
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
DEFAULT_RAW_VOL_DIR = os.path.join(SCRIPT_DIR, "data", "volume_raw")
DEFAULT_OUT_DIR = os.path.join(SCRIPT_DIR, "data", "processed")

RAW_VOL_DIR = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RAW_VOL_DIR
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT_DIR
os.makedirs(OUT_DIR, exist_ok=True)

EXCLUDE_TICKERS = {
    # tickers indiciels / benchmarks, pas des actions de l'univers
    "^AEX", "^DJI", "^FCHI", "^FTSE", "^GDAXI", "^GSPC", "^HSI", "^IBEX",
    "^N225", "^NDX", "^SBF120", "^SSMI", "^STOXX50E", "FTSEMIB.MI",
    "MT.PA",  # ancien ticker ArcelorMittal, remplace par MT.AS
    # convention de devise non fiable dans price_history.json (memoire utilisateur)
    "IHG.L", "MTLN.L", "CPG.L",
}


def week_id_of(d: pd.Timestamp) -> str:
    iso = d.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def week_sort_key(wid: str):
    y, w = wid.split("-W")
    return (int(y), int(w))


def main():
    print("Chargement price_history.json...")
    with open(os.path.join(REPO, "docs", "price_history.json"), encoding="utf-8") as f:
        ph = json.load(f)
    pdf = pd.DataFrame(ph)
    pdf["date"] = pd.to_datetime(pdf["date"])
    pdf = pdf[~pdf["ticker"].isin(EXCLUDE_TICKERS)]
    pdf["week_id"] = pdf["date"].apply(week_id_of)
    # si plusieurs lignes pour le meme (ticker, week_id), garder la derniere par date
    pdf = pdf.sort_values("date")
    pdf = pdf.drop_duplicates(subset=["ticker", "week_id"], keep="last")

    price_wide = pdf.pivot(index="week_id", columns="ticker", values="price")
    week_ids_sorted = sorted(price_wide.index, key=week_sort_key)
    price_wide = price_wide.loc[week_ids_sorted]
    print("price_wide:", price_wide.shape, "premiere semaine", week_ids_sorted[0], "derniere", week_ids_sorted[-1])

    tickers = [t for t in price_wide.columns]
    print("Univers:", len(tickers), "tickers")

    print("Chargement + resample hebdo (somme) des CSV de volume quotidien...")
    vol_cols = {}
    missing_vol = []
    for i, t in enumerate(tickers):
        fname = t.replace("^", "_IDX_") + ".csv"
        fpath = os.path.join(RAW_VOL_DIR, fname)
        if not os.path.exists(fpath):
            missing_vol.append(t)
            continue
        vdf = pd.read_csv(fpath, parse_dates=["date"])
        if vdf.empty:
            missing_vol.append(t)
            continue
        vdf["week_id"] = vdf["date"].apply(week_id_of)
        weekly = vdf.groupby("week_id")["volume"].sum()
        vol_cols[t] = weekly
        if (i + 1) % 200 == 0:
            print(f"  ...{i+1}/{len(tickers)} tickers traites")

    volume_wide = pd.DataFrame(vol_cols)
    volume_wide = volume_wide.reindex(index=week_ids_sorted)
    # aligner les colonnes sur l'univers prix (certains tickers sans volume -> colonnes NaN)
    for t in tickers:
        if t not in volume_wide.columns:
            volume_wide[t] = np.nan
    volume_wide = volume_wide[tickers]

    print("volume_wide:", volume_wide.shape)
    print("Tickers sans aucune donnee de volume:", len(missing_vol), missing_vol[:20])

    price_wide.to_pickle(os.path.join(OUT_DIR, "price_wide.pkl"))
    volume_wide.to_pickle(os.path.join(OUT_DIR, "volume_wide.pkl"))

    # sector map
    with open(os.path.join(REPO, "docs", "indices.json"), encoding="utf-8") as f:
        idx = json.load(f)
    sector_map = {c["ticker"]: c.get("sector") or "Inconnu" for c in idx["companies"]}
    sectors = pd.Series({t: sector_map.get(t, "Inconnu") for t in tickers}, name="sector")
    sectors.to_frame().to_pickle(os.path.join(OUT_DIR, "sectors.pkl"))
    print("Secteurs inconnus:", (sectors == "Inconnu").sum(), list(sectors[sectors == "Inconnu"].index)[:20])

    print("OK -> ", OUT_DIR)


if __name__ == "__main__":
    main()
