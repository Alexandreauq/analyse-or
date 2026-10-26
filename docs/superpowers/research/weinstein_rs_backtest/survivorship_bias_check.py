"""Verifie, pour un echantillon connu de sorties d'indice depuis 2020-10,
si ces entreprises sont REELLEMENT absentes de l'univers de backtest
(docs/indices.json + docs/price_history.json), ou si elles sont encore
suivies sous une etiquette d'indice plus large deja incluse dans l'univers
complet (walk_forward_eval.py n'exclut aucun ticker par indice -- il prend
TOUT ce qui existe dans docs/price_history.json).

RECHERCHE / EXPLORATOIRE - PAS DU CODE DE PRODUCTION. Voir README.md et
survivorship_bias_check_README.md dans ce dossier.

Sources des sorties d'indice (dates verifiees manuellement le 2026-10-26) :
- CAC40 : https://fr.wikipedia.org/wiki/CAC_40 (tableau "Historique de la
  composition", seul tableau trouve avec des dates fiables pour les 8
  indices du perimetre).
- DOW : https://en.wikipedia.org/wiki/Historical_components_of_the_Dow_Jones_Industrial_Average
- FTSE100 : PAS RETENU -- ni la page Wikipedia (liste "Past constituents"
  non datee) ni une recherche web (resultats fragmentes, dates
  contradictoires d'un article a l'autre) n'ont donne une liste fiable.
  Reste un angle mort explicite (voir README), pas suppose neutre.
"""
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))

# Sorties CAC40 depuis 2020-10 (ticker IBKR/yfinance, date de sortie, raison si connue).
CAC40_EXITS = [
    ("ATO.PA", "2021-09-17", "remplace par Eurofins Scientific"),
    ("VIV.PA", "2023-06-19", "remplace par Edenred (1re sortie)"),
    ("WLN.PA", "2023-12-18", "remplace par Vivendi"),
    ("ALO.PA", "2024-03-18", "remplace par Accor"),
    ("VIV.PA", "2024-12-23", "remplace par Bureau Veritas (2e sortie, apres retour en 2023-12)"),
    ("TEP.PA", "2025-09-22", "remplace par Euronext"),
    ("EDEN.PA", "2025-12-22", "remplace par Eiffage"),
]

# Sorties DOW Jones depuis 2020-10 (ticker, date, raison).
DOW_EXITS = [
    ("WBA", "2024-02-26", "remplace par Amazon"),
    ("INTC", "2024-11-08", "remplace par Nvidia"),
    ("DOW", "2024-11-08", "remplace par Sherwin-Williams"),
    ("VZ", "2026-06-29", "remplace par Alphabet"),
]


def load_universe():
    with open(os.path.join(REPO, "docs", "indices.json"), encoding="utf-8") as f:
        idx = json.load(f)
    ticker_to_indices = {}
    for c in idx["companies"]:
        ticker_to_indices.setdefault(c["ticker"], set()).add(c.get("index"))

    with open(os.path.join(REPO, "docs", "price_history.json"), encoding="utf-8") as f:
        ph = json.load(f)
    tickers_with_price_history = set(row["ticker"] for row in ph)
    return ticker_to_indices, tickers_with_price_history


def check_exits(exits: list[tuple[str, str, str]], label: str,
                 ticker_to_indices: dict, tickers_with_price_history: set):
    print(f"\n=== {label} : {len(exits)} sorties verifiees depuis 2020-10 ===")
    seen_tickers = set()
    for ticker, date, raison in exits:
        if ticker in seen_tickers:
            continue  # VIV.PA apparait deux fois (sortie, retour, re-sortie) ; une seule verification suffit.
        seen_tickers.add(ticker)
        indices_couvrant = ticker_to_indices.get(ticker, set())
        a_un_historique = ticker in tickers_with_price_history
        if not indices_couvrant:
            statut = "ABSENT de tout indice suivi"
        else:
            statut = f"toujours suivi sous {sorted(indices_couvrant)}"
        print(f"  {ticker:10s} sortie {date} ({raison})")
        print(f"    -> {statut} ; historique de prix present : {a_un_historique}")


if __name__ == "__main__":
    ticker_to_indices, tickers_with_price_history = load_universe()
    check_exits(CAC40_EXITS, "CAC40", ticker_to_indices, tickers_with_price_history)
    check_exits(DOW_EXITS, "DOW", ticker_to_indices, tickers_with_price_history)
