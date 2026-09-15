# ibkr_bot/portfolio.py
# Plafond de 10 positions, classement des signaux par score composite,
# selection des entrees sous contrainte de solde. (Les regles de sortie
# et la reconciliation sont ajoutees par la tache suivante.)
#
# Ce module DECIDE, il ne passe aucun ordre et ne touche pas au reseau —
# meme decoupage que gold_bot (un seul module parle au courtier).
import json
import math
import os

MAX_POSITIONS = 10  # positions ouvertes PAR LE BOT, pas sur le compte (spec 3.4 / 9.5)

POSITIONS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "positions.json")


def _is_missing(value) -> bool:
    """True si une valeur numerique est absente ou NaN."""
    try:
        return math.isnan(value)
    except TypeError:
        return value is None


def load_positions(path: str = POSITIONS_PATH) -> list[dict]:
    """Positions ouvertes par le bot. [] si le fichier est absent ou
    corrompu — jamais d'exception."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    positions = data.get("positions", [])
    return positions if isinstance(positions, list) else []


def save_positions(positions: list[dict], path: str = POSITIONS_PATH) -> None:
    """Ecriture atomique, meme motif que state.save_state."""
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump({"positions": positions}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def rank_signals(signals: list[dict]) -> list[dict]:
    """Signaux du jour classes par score composite decroissant (spec
    3.5). Egalite departagee par le ticker, pour que deux executions du
    meme batch prennent exactement les memes decisions."""
    return sorted(signals, key=lambda s: (-s["score"], s["ticker"]))


def free_slots(open_positions: list[dict]) -> int:
    """Places libres sous le plafond de 10 positions du bot."""
    return max(0, MAX_POSITIONS - len(open_positions))


def select_entries(
    signals: list[dict], open_positions: list[dict],
    plans: dict[str, dict], cash_by_currency: dict[str, float],
) -> tuple[list[dict], list[dict]]:
    """Signaux du jour effectivement retenus a l'achat, et rejets motives.

    ORDRE DES FILTRES, qui est lui-meme une regle de la spec :
    deja detenu -> plan absent -> quantite nulle -> plafond -> solde.
    Le cas 0 action passe AVANT le plafond parce que "la place ainsi
    liberee reste disponible pour le signal suivant du classement"
    (spec 3.3) : inverser les deux perdrait un signal financable au
    profit d'un signal inachetable.
    """
    tickers_detenus = {p["ticker"] for p in open_positions}
    places = free_slots(open_positions)
    soldes = dict(cash_by_currency)

    retenus: list[dict] = []
    rejets: list[dict] = []
    for rang, signal in enumerate(rank_signals(signals), start=1):
        ticker = signal["ticker"]
        base = {"ticker": ticker, "rang": rang, "score": signal["score"]}

        if ticker in tickers_detenus:
            rejets.append({**base, "raison": "deja_en_portefeuille"})
            continue

        plan = plans.get(ticker)
        if plan is None:
            rejets.append({**base, "raison": "plan_indisponible"})
            continue

        if plan["quantite"] < 1:
            rejets.append({**base, "raison": plan["motif"] or "plan_indisponible"})
            continue

        if places < 1:
            rejets.append({**base, "raison": "signal_ignore_plafond_atteint"})
            continue

        devise = plan["devise_compte"]
        cout = plan["cout_estime_devise_compte"]
        if soldes.get(devise, 0.0) < cout:
            rejets.append({**base, "raison": "solde_insuffisant"})
            continue

        soldes[devise] = soldes.get(devise, 0.0) - cout
        places -= 1
        tickers_detenus.add(ticker)
        retenus.append({"signal": signal, "plan": plan, "rang": rang})

    return retenus, rejets
