# compte_api/ratelimit.py
# Limiteur en mémoire à fenêtre glissante, par clé (adresse IP, e-mail...).
# Suffisant pour un seul processus uvicorn ; remis à zéro au redémarrage.
import threading
import time
from collections import defaultdict, deque


class Limiteur:
    def __init__(self, maximum: int, fenetre_secondes: float, horloge=time.monotonic):
        self.maximum = maximum
        self.fenetre = fenetre_secondes
        self._horloge = horloge
        self._evenements: dict[str, deque] = defaultdict(deque)
        self._verrou = threading.Lock()

    def _purger(self, file: deque, now: float) -> None:
        while file and now - file[0] >= self.fenetre:
            file.popleft()

    def depasse(self, cle: str) -> bool:
        """Vrai si la clé a déjà atteint le maximum dans la fenêtre (sans compter)."""
        with self._verrou:
            file = self._evenements[cle]
            self._purger(file, self._horloge())
            return len(file) >= self.maximum

    def enregistrer(self, cle: str) -> None:
        with self._verrou:
            file = self._evenements[cle]
            now = self._horloge()
            self._purger(file, now)
            file.append(now)

    def autorise(self, cle: str) -> bool:
        """Compte une tentative et dit si elle est permise."""
        with self._verrou:
            file = self._evenements[cle]
            now = self._horloge()
            self._purger(file, now)
            if len(file) >= self.maximum:
                return False
            file.append(now)
            return True

    def reinitialiser(self, cle: str) -> None:
        with self._verrou:
            self._evenements.pop(cle, None)


def adresse_client(request) -> str:
    """IP réelle du visiteur : derrière le rewrite Vercel, elle arrive dans
    X-Forwarded-For (premier élément). Fiable ici parce que le service
    n'accepte que les requêtes portant l'en-tête secret du proxy."""
    transmis = request.headers.get("x-forwarded-for", "")
    if transmis:
        return transmis.split(",")[0].strip()
    return request.client.host if request.client else "inconnue"
