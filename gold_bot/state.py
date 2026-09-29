# gold_bot/state.py
# État persistant local du bot (interrupteur d'urgence, mode simulation)
# — voir docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import json
import os
import time

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
# Verrou volontairement portable (fichier créé en O_CREAT|O_EXCL) plutôt
# que fcntl.flock (indisponible sous Windows, utilisé pour développer ce
# projet) -- voir update_state ci-dessous.
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_SECONDS = 0.05
_LOCK_STALE_AFTER_SECONDS = 10.0


class _StateLock:
    """Verrou mutuel exclusif basé sur un fichier, pour protéger un
    read-modify-write complet de state.json contre deux écritures
    concurrentes -- voir update_state. Auto-nettoyant : un verrou plus
    vieux que _LOCK_STALE_AFTER_SECONDS (ex : processus mort en le tenant)
    est considéré abandonné et repris de force plutôt que de bloquer
    indéfiniment toute future écriture."""

    def __init__(self, path: str):
        self._lock_path = f"{path}.lock"
        self._fd = None

    def __enter__(self):
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while True:
            try:
                self._fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self._lock_path) > _LOCK_STALE_AFTER_SECONDS:
                        os.remove(self._lock_path)
                        continue
                except OSError:
                    continue  # le verrou a disparu entre-temps (l'autre écriture vient de finir)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Verrou d'état introuvable après {_LOCK_TIMEOUT_SECONDS}s : {self._lock_path}")
            time.sleep(_LOCK_POLL_SECONDS)

    def __exit__(self, exc_type, exc, tb):
        if self._fd is not None:
            os.close(self._fd)
        try:
            os.remove(self._lock_path)
        except OSError:
            pass


def load_state(path: str = STATE_PATH) -> dict:
    """État de repli si le fichier n'existe pas encore, est illisible,
    ou contient du JSON qui parse mais n'est pas le dict attendu — les
    champs kill_switch/dry_run/risk_profile sont toujours présents en
    sortie, jamais None ou absents. Jamais d'exception au démarrage
    du bot. risk_profile n'est délibérément pas validé ici (type/plage)
    — voir risk.risk_profile_params() qui gère tout profil invalide
    par un repli explicite plutôt qu'une exception."""
    defaults = {"kill_switch": False, "dry_run": True, "risk_profile": 3}
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
    merged["risk_profile"] = merged.get("risk_profile", 3)
    return merged


def save_state(state: dict, path: str = STATE_PATH) -> None:
    """Écriture atomique (fichier temporaire puis renommage) pour
    qu'un crash en pleine écriture ne puisse jamais laisser un fichier
    tronqué que load_state lirait comme un état valide mais faux."""
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def update_state(fields: dict, path: str = STATE_PATH) -> dict:
    """Modifie un sous-ensemble de champs de l'état, protégé par un
    verrou exclusif couvrant tout le cycle lire-modifier-écrire. Ajouté
    le 2026-09-29 : gold_bot.api faisait ce cycle lui-même pour /kill,
    /resume et /profile sans aucun verrou -- deux requêtes admin quasi
    simultanées (ex : double clic sur l'arrêt d'urgence, ou /profile
    juste après /kill) pouvaient faire écraser silencieusement un
    kill_switch=True tout juste posé par la seconde écriture, partie
    d'un état lu avant la première. gold_bot.loop ne fait que LIRE l'état
    (jamais écrire) donc n'a pas besoin de ce verrou. Renvoie l'état
    complet après fusion, pour que l'appelant n'ait pas à recharger."""
    with _StateLock(path):
        current = load_state(path)
        current.update(fields)
        save_state(current, path)
        return current
