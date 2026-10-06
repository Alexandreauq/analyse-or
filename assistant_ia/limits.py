# assistant_ia/limits.py
# Garde-fous de charge de l'assistant : limite de questions par visiteur
# (fenêtre glissante) et plafond de requêtes simultanées vers Claude. Pure
# logique, sans réseau, testable. Les valeurs réelles viennent de api.py.
import threading
import time
from collections import deque


class RateLimiter:
    """Au plus `maximum` appels par `fenetre_s` secondes, par clé (ex. adresse IP)."""

    def __init__(self, maximum: int, fenetre_s: float, now_fn=time.monotonic, max_cles: int = 10000):
        self.maximum = maximum
        self.fenetre_s = fenetre_s
        self.now_fn = now_fn
        self.max_cles = max_cles
        self._historique: dict[str, deque] = {}
        self._verrou = threading.Lock()

    def autorise(self, cle: str) -> bool:
        with self._verrou:
            maintenant = self.now_fn()
            if len(self._historique) > self.max_cles:
                # Protection mémoire : on repart de zéro plutôt que de grossir sans fin.
                self._historique.clear()
            appels = self._historique.setdefault(cle, deque())
            while appels and maintenant - appels[0] >= self.fenetre_s:
                appels.popleft()
            if len(appels) >= self.maximum:
                return False
            appels.append(maintenant)
            return True


class ConcurrencyLimiter:
    """Au plus `maximum` requêtes en cours. Refus immédiat (jamais d'attente en file)."""

    def __init__(self, maximum: int):
        self.maximum = maximum
        self._en_cours = 0
        self._verrou = threading.Lock()

    def tente(self) -> bool:
        with self._verrou:
            if self._en_cours >= self.maximum:
                return False
            self._en_cours += 1
            return True

    def libere(self) -> None:
        with self._verrou:
            self._en_cours = max(0, self._en_cours - 1)

    @property
    def en_cours(self) -> int:
        with self._verrou:
            return self._en_cours
