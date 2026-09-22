# gold_bot/risk.py
# Dimensionnement de position par le risque et coupe-circuit journalier
# — voir docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import math
from datetime import date, datetime, timezone

import gold_bot.state as state


RISK_PROFILE_PARAMS: dict[int, dict[str, float]] = {
    1: {"risk_pct": 0.02, "threshold_pct": 0.05},
    2: {"risk_pct": 0.035, "threshold_pct": 0.075},
    3: {"risk_pct": 0.05, "threshold_pct": 0.10},
    4: {"risk_pct": 0.075, "threshold_pct": 0.135},
    5: {"risk_pct": 0.10, "threshold_pct": 0.175},
}
DEFAULT_RISK_PROFILE = 3
# Levier max réellement disponible sur le compte Vantage/MT5 utilisé
# (1:500 sur XAUUSD, confirmé par l'utilisateur) — un backstop contre un
# bug (aucun plafond n'existait avant), pas une mesure de réduction du
# risque : l'utilisateur trade volontairement jusqu'à ce levier, donc ce
# plafond ne bloque que les cas où le dimensionnement par le risque
# demanderait PLUS que ce que le compte peut réellement supporter (stop
# extrêmement serré), jamais un usage normal du levier autorisé.
MAX_LEVERAGE = 500


def resolve_risk_profile(profile) -> int:
    """Résout un profil de risque candidat vers un entier 1-5 valide.
    Tout profil invalide (absent, None, hors plage, non-entier, ou
    booléen — bool est une sous-classe d'int en Python, exclue
    explicitement pour ne jamais résoudre True/False vers un profil
    numérique) replie sur DEFAULT_RISK_PROFILE, jamais d'exception —
    même convention que sector_risk_profile() dans indices_score.py.
    Extrait de risk_profile_params() (qui délègue ici) pour que
    api.py puisse afficher le profil RÉSOLU/effectif dans /status,
    plutôt que la valeur brute potentiellement invalide stockée dans
    state.json."""
    if isinstance(profile, bool) or not isinstance(profile, int) or profile not in RISK_PROFILE_PARAMS:
        return DEFAULT_RISK_PROFILE
    return profile


def risk_profile_params(profile) -> dict[str, float]:
    """Résout un profil de risque (1-5) vers ses paramètres risk_pct/
    threshold_pct — voir resolve_risk_profile() pour la règle de
    validation/repli exacte."""
    return RISK_PROFILE_PARAMS[resolve_risk_profile(profile)]


def compute_position_size(balance: float, entry: float, stop_loss: float,
                           contract_size: float, risk_pct: float = 0.05,
                           max_leverage: float = MAX_LEVERAGE) -> float:
    """Dimensionnement par le risque : la perte si le stop-loss est
    touché vaut risk_pct * balance — pas la valeur notionnelle engagée.
    Lève ValueError si entry == stop_loss (risque nul, division par zéro
    évitée explicitement plutôt que renvoyer une taille infinie).

    Un stop très serré fait exploser la taille brute par le risque (le
    risque en $ reste risk_pct*balance, mais le notionnel engagé pour
    l'atteindre n'a aucune limite) — trouvé en production : un stop à
    3.80$ pouvait demander ~950k$ de notionnel sur un compte à 10k$. La
    taille est donc plafonnée pour que le notionnel (volume *
    contract_size * entry) ne dépasse jamais balance * max_leverage : au
    -delà, le trade accepte un risque en $ réel inférieur à
    risk_pct*balance plutôt que de dépasser ce que le compte peut
    réellement supporter."""
    if balance <= 0:
        raise ValueError("Le solde doit être strictement positif")
    if contract_size <= 0:
        raise ValueError("La taille de contrat doit être strictement positive")
    if not 0 < risk_pct <= 1:
        raise ValueError("risk_pct doit être compris entre 0 (exclu) et 1 (inclus)")
    distance = abs(entry - stop_loss)
    if distance == 0:
        raise ValueError("La distance entrée→stop-loss ne peut pas être nulle")
    risk_amount = balance * risk_pct
    size = risk_amount / (distance * contract_size)
    max_size = (balance * max_leverage) / (contract_size * entry)
    return min(size, max_size)


def round_to_volume_step(size: float, volume_step: float, min_volume: float, max_volume: float) -> float | None:
    """Arrondit `size` au pas du broker (`volumeStep`, tel que renvoyé
    par broker.get_symbol_specification) — toujours vers le BAS, jamais
    vers le haut (arrondir au-dessus dépasserait le risque voulu,
    risk_pct*balance). Renvoie None si le résultat tombe sous
    `min_volume` : le compte est trop petit pour ce stop à ce niveau de
    risque, plutôt qu'une taille que le broker rejetterait de toute
    façon avec une erreur générique — trouvé en production : le volume
    calculé pouvait tomber sous le lot minimum sans que ce cas précis
    soit jamais diagnostiqué. Plafonne aussi à `max_volume` (filet de
    sécurité supplémentaire, rarement atteint en pratique vu le plafond
    de levier de compute_position_size)."""
    if volume_step <= 0:
        raise ValueError("Le pas de volume doit être strictement positif")
    steps = math.floor(round(size / volume_step, 8))
    rounded = round(steps * volume_step, 8)
    if rounded < min_volume:
        return None
    return min(rounded, max_volume)


class CircuitBreaker:
    """Coupe-circuit journalier : bloque l'ouverture de nouvelles
    positions si la perte cumulée du jour dépasse threshold_pct du solde
    fixé au début de la journée UTC courante. Ne ferme jamais de
    position existante — seul un filtre sur can_open_position().
    `now_fn` est injectable pour les tests (horloge fixe/contrôlable).
    `persist_path`, si fourni, persiste {day, starting_balance,
    tripped_today} via gold_bot.state — sans lui, l'état reste en
    mémoire uniquement (perdu au redémarrage), ce qui suffit pour les
    tests mais pas en production, où un redémarrage ne doit pas
    réinitialiser le seuil du jour au solde courant.

    `threshold_pct` est un attribut public mutable en place par
    gold_bot.loop.run_cycle() (le profil de risque actif est relu à
    chaque cycle). Sans le flag `_tripped_today` ci-dessous, relever le
    profil (donc `threshold_pct`) après un déclenchement du coupe-circuit
    le même jour UTC le dé-déclencherait silencieusement, puisque
    can_open_position() recompare toujours la perte au NOUVEAU seuil,
    plus large. `_tripped_today` fige le verdict "déclenché" pour le
    reste de la journée UTC, indépendamment de tout changement ultérieur
    de threshold_pct — il ne se réinitialise qu'au même moment que
    `_starting_balance`/`_day`, au rollover de jour."""

    def __init__(self, threshold_pct: float = 0.10, now_fn=None, persist_path=None):
        if not 0 < threshold_pct <= 1:
            raise ValueError("threshold_pct doit être compris entre 0 (exclu) et 1 (inclus)")
        self.threshold_pct = threshold_pct
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._persist_path = persist_path
        self._day = None
        self._starting_balance = None
        self._tripped_today = False
        if persist_path is not None:
            saved = state.load_state(persist_path)
            saved_day = saved.get("circuit_breaker_day")
            saved_balance = saved.get("circuit_breaker_starting_balance")
            if saved_day is not None and saved_balance is not None:
                try:
                    self._day = date.fromisoformat(saved_day)
                    self._starting_balance = float(saved_balance)
                    self._tripped_today = bool(saved.get("circuit_breaker_tripped_today", False))
                except (ValueError, TypeError):
                    self._day = None
                    self._starting_balance = None
                    self._tripped_today = False

    def check(self, current_balance: float) -> None:
        """À appeler avant toute décision — fixe le solde de référence
        du jour s'il n'existe pas encore ou si on a changé de jour UTC,
        et réinitialise le flag de déclenchement pour le nouveau jour."""
        today = self._now_fn().date()
        if self._day != today:
            self._day = today
            self._starting_balance = current_balance
            self._tripped_today = False
            self._persist()

    def _persist(self) -> None:
        if self._persist_path is None:
            return
        state.save_state(
            {
                "circuit_breaker_day": self._day.isoformat(),
                "circuit_breaker_starting_balance": self._starting_balance,
                "circuit_breaker_tripped_today": self._tripped_today,
            },
            self._persist_path,
        )

    def can_open_position(self, current_balance: float) -> bool:
        self.check(current_balance)
        # Une fois déclenché un jour donné, reste déclenché pour le
        # reste de ce jour UTC — même si threshold_pct est relevé
        # entre-temps (profil de risque augmenté en cours de journée).
        # Voir la docstring de la classe.
        if self._tripped_today:
            return False
        loss = self._starting_balance - current_balance
        if loss < self._starting_balance * self.threshold_pct:
            return True
        self._tripped_today = True
        self._persist()
        return False
