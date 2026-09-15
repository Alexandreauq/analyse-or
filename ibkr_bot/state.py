# ibkr_bot/state.py
# État persistant local du bot actions IBKR (interrupteur d'urgence,
# mode simulation). Copie conforme de gold_bot/state.py — même interface,
# même sémantique, chemin par défaut différent. L'interrupteur est un
# fichier modifié en SSH, pas une route API (voir spec 5.2).
import json
import os

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def load_state(path: str = STATE_PATH) -> dict:
    """État de repli si le fichier n'existe pas encore, est illisible,
    ou contient du JSON qui parse mais n'est pas le dict attendu — les
    champs kill_switch/dry_run sont toujours présents et de type bool en
    sortie, jamais None ou absents. Jamais d'exception au démarrage du
    batch. dry_run vaut True par défaut (voir spec 5.3)."""
    defaults = {"kill_switch": False, "dry_run": True}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return dict(defaults)
    if not isinstance(data, dict):
        return dict(defaults)
    merged = dict(defaults)
    merged.update(data)
    merged["kill_switch"] = bool(merged.get("kill_switch", False))
    merged["dry_run"] = bool(merged.get("dry_run", True))
    return merged


def save_state(state: dict, path: str = STATE_PATH) -> None:
    """Écriture atomique (fichier temporaire puis renommage) pour qu'un
    crash en pleine écriture ne puisse jamais laisser un fichier tronqué
    que load_state lirait comme un état valide mais faux."""
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
