# gold_bot/state.py
# État persistant local du bot (interrupteur d'urgence, mode simulation)
# — voir docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import json
import os

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def load_state(path: str = STATE_PATH) -> dict:
    """État de repli si le fichier n'existe pas encore ou est illisible
    — jamais d'exception au démarrage du bot."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"kill_switch": False, "dry_run": True}


def save_state(state: dict, path: str = STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
