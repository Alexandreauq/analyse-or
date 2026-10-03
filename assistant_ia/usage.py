# assistant_ia/usage.py
# Plafond de depense quotidien de l'assistant IA : un garde-fou, pas une
# limite d'usage normal (voir spec §8) — protege contre un bug cote
# client qui boucle les appels, jamais contre un usage legitime. Les
# prix sont en dur (pas d'appel reseau pour les connaitre, ils bougent
# rarement et une panne de lookup ne doit jamais empecher de savoir ce
# qu'on a deja depense aujourd'hui).
import json
import os
from datetime import datetime, timezone

MODEL_OPUS_5_5 = "claude-opus-5-5"
DAILY_BUDGET_USD = 5.0
PRICE_PER_MTOK_USD = {
    MODEL_OPUS_5_5: {"input": 4.0, "output": 20.0},
}
USAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage_today.json")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_usage(path: str = USAGE_PATH) -> dict:
    """Dernier total de depense connu. Fichier absent/corrompu -> total a
    zero pour aujourd'hui, jamais d'exception (meme contrat que
    ibkr_bot.journal.load_account_snapshot)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {"date": _today(), "total_usd": 0.0}
    if not isinstance(data, dict) or data.get("date") != _today():
        return {"date": _today(), "total_usd": 0.0}
    total = data.get("total_usd")
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        return {"date": _today(), "total_usd": 0.0}
    return {"date": _today(), "total_usd": float(total)}


def _save_usage(data: dict, path: str = USAGE_PATH) -> None:
    """Ecriture atomique, degrade silencieusement sur echec (meme
    philosophie que journal.append_run : une panne disque sur ce journal
    ne doit jamais faire planter une requete reelle). Un echec d'ecriture
    signifie seulement que le total pourrait etre legerement sous-estime
    au prochain redemarrage, jamais une raison de bloquer la requete en
    cours."""
    try:
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"Erreur ecriture journal d'usage IA : {e}")


def record_usage(
    input_tokens: int, output_tokens: int, model: str = MODEL_OPUS_5_5,
    path: str = USAGE_PATH,
) -> float:
    """Ajoute le cout d'un appel au total du jour (reinitialise
    automatiquement si le fichier date d'un autre jour UTC) et renvoie
    le nouveau total en dollars."""
    prices = PRICE_PER_MTOK_USD[model]
    cout = (input_tokens / 1_000_000) * prices["input"] + (output_tokens / 1_000_000) * prices["output"]
    current = load_usage(path)
    nouveau_total = current["total_usd"] + cout
    _save_usage({"date": _today(), "total_usd": nouveau_total}, path)
    return nouveau_total


def budget_exceeded(path: str = USAGE_PATH, daily_budget_usd: float = DAILY_BUDGET_USD) -> bool:
    """True si le total du jour atteint ou depasse le plafond. Fichier
    absent -> False (aucune depense connue aujourd'hui)."""
    return load_usage(path)["total_usd"] >= daily_budget_usd
