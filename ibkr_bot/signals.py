# ibkr_bot/signals.py
# Lecture des nouveaux signaux "entree" du jour et rapprochement du score
# composite. Ce module NE REIMPLEMENTE PAS la detection de nouveaute : le
# paper-trading (indices_score.update_signal_tracking) l'a deja faite, et
# les positions "open" ouvertes aujourd'hui dans docs/signal_tracking.json
# sont par construction exactement les nouveaux signaux du jour, deja
# dedoublonnes (voir spec 4.6). Lecture seule : ce module n'ecrit jamais
# dans docs/.
import json
import math
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDICES_PATH = os.path.join(_REPO_ROOT, "docs", "indices.json")
SIGNAL_TRACKING_PATH = os.path.join(_REPO_ROOT, "docs", "signal_tracking.json")

# Perimetre v1 : Nikkei 225 et Hang Seng sont volontairement exclus du
# passage au reel (JPY/HKD + session asiatique hors de la fenetre du
# batch) — ils continuent d'etre scores et paper-trades (voir spec 2).
INDICES_IN_SCOPE = ("CAC40", "DAX", "NASDAQ", "DOW", "FTSE", "SMI", "IBEX35", "FTSEMIB")


def _is_missing(value) -> bool:
    """True si une valeur numerique est absente ou NaN. Meme garde que
    indices_score._is_missing : un NaN qui passe silencieusement rend
    toutes les comparaisons de prix fausses sans lever d'exception."""
    try:
        return math.isnan(value)
    except TypeError:
        return value is None


def load_indices(path: str = INDICES_PATH) -> dict:
    """Contenu de docs/indices.json. {} si le fichier est absent ou
    corrompu — jamais d'exception."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_signal_tracking(path: str = SIGNAL_TRACKING_PATH) -> list[dict]:
    """Positions de paper-trading (ouvertes et cloturees). [] si le
    fichier est absent ou corrompu — jamais d'exception."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    positions = data.get("positions", [])
    return positions if isinstance(positions, list) else []


def indices_are_fresh(indices: dict, today: str) -> bool:
    """Garde de fraicheur non negociable (spec 4.5) : trader sur les
    scores d'hier reviendrait a ouvrir des positions sur des signaux deja
    consommes par le paper-trading."""
    return indices.get("updated") == today


def collect_new_signals(
    indices: dict, positions: list[dict], today: str,
) -> tuple[list[dict], list[dict]]:
    """Nouveaux signaux du jour, enrichis du score composite, de la devise
    de l'indice et du prix courant. Renvoie (signaux, rejets) — chaque
    rejet porte son motif, pour que le journal du batch (Plan B) rende
    visible tout signal ecarte plutot que de le perdre silencieusement."""
    if not indices_are_fresh(indices, today):
        return [], [{"ticker": None, "raison": "donnees_perimees"}]

    currencies = indices.get("index_currency", {})
    companies_by_ticker = {c["ticker"]: c for c in indices.get("companies", [])}

    found: list[dict] = []
    rejets: list[dict] = []
    for position in positions:
        if position.get("status") != "open" or position.get("entry_date") != today:
            continue
        ticker = position["ticker"]
        if position.get("index") not in INDICES_IN_SCOPE:
            rejets.append({"ticker": ticker, "raison": "index_hors_perimetre"})
            continue
        company = companies_by_ticker.get(ticker)
        if company is None or _is_missing(company.get("score")):
            rejets.append({"ticker": ticker, "raison": "score_indisponible"})
            continue
        if (_is_missing(company.get("current_price"))
                or _is_missing(position.get("target_exit_price"))
                or _is_missing(position.get("entry_price"))):
            rejets.append({"ticker": ticker, "raison": "prix_indisponible"})
            continue
        found.append({
            "id": position["id"],
            "ticker": ticker,
            "name": position.get("name", ""),
            "index": position["index"],
            "currency": currencies.get(position["index"], ""),
            "sector": company.get("sector", ""),
            "entry_date": position["entry_date"],
            "paper_entry_price": position["entry_price"],
            "target_exit_price": position["target_exit_price"],
            "score": company["score"],
            "current_price": company["current_price"],
        })
    return found, rejets
