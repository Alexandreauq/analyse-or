# gold_bot/risk.py
# Dimensionnement de position par le risque et coupe-circuit journalier
# — voir docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
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


def risk_profile_params(profile) -> dict[str, float]:
    """Résout un profil de risque (1-5) vers ses paramètres risk_pct/
    threshold_pct. Tout profil invalide (absent, None, hors plage,
    non-entier, ou booléen — bool est une sous-classe d'int en Python,
    exclue explicitement pour ne jamais résoudre True/False vers un
    profil numérique) replie sur DEFAULT_RISK_PROFILE, jamais
    d'exception — même convention que sector_risk_profile() dans
    indices_score.py."""
    if isinstance(profile, bool) or not isinstance(profile, int) or profile not in RISK_PROFILE_PARAMS:
        profile = DEFAULT_RISK_PROFILE
    return RISK_PROFILE_PARAMS[profile]


def compute_position_size(balance: float, entry: float, stop_loss: float,
                           contract_size: float, risk_pct: float = 0.05) -> float:
    """Dimensionnement par le risque : la perte si le stop-loss est
    touché vaut risk_pct * balance — pas la valeur notionnelle engagée.
    Lève ValueError si entry == stop_loss (risque nul, division par zéro
    évitée explicitement plutôt que renvoyer une taille infinie)."""
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
    return risk_amount / (distance * contract_size)


class CircuitBreaker:
    """Coupe-circuit journalier : bloque l'ouverture de nouvelles
    positions si la perte cumulée du jour dépasse threshold_pct du solde
    fixé au début de la journée UTC courante. Ne ferme jamais de
    position existante — seul un filtre sur can_open_position().
    `now_fn` est injectable pour les tests (horloge fixe/contrôlable).
    `persist_path`, si fourni, persiste {day, starting_balance} via
    gold_bot.state — sans lui, l'état reste en mémoire uniquement (perdu
    au redémarrage), ce qui suffit pour les tests mais pas en
    production, où un redémarrage ne doit pas réinitialiser le seuil du
    jour au solde courant."""

    def __init__(self, threshold_pct: float = 0.10, now_fn=None, persist_path=None):
        if not 0 < threshold_pct <= 1:
            raise ValueError("threshold_pct doit être compris entre 0 (exclu) et 1 (inclus)")
        self.threshold_pct = threshold_pct
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._persist_path = persist_path
        self._day = None
        self._starting_balance = None
        if persist_path is not None:
            saved = state.load_state(persist_path)
            saved_day = saved.get("circuit_breaker_day")
            saved_balance = saved.get("circuit_breaker_starting_balance")
            if saved_day is not None and saved_balance is not None:
                try:
                    self._day = date.fromisoformat(saved_day)
                    self._starting_balance = float(saved_balance)
                except (ValueError, TypeError):
                    self._day = None
                    self._starting_balance = None

    def check(self, current_balance: float) -> None:
        """À appeler avant toute décision — fixe le solde de référence
        du jour s'il n'existe pas encore ou si on a changé de jour UTC."""
        today = self._now_fn().date()
        if self._day != today:
            self._day = today
            self._starting_balance = current_balance
            self._persist()

    def _persist(self) -> None:
        if self._persist_path is None:
            return
        state.save_state(
            {"circuit_breaker_day": self._day.isoformat(), "circuit_breaker_starting_balance": self._starting_balance},
            self._persist_path,
        )

    def can_open_position(self, current_balance: float) -> bool:
        self.check(current_balance)
        loss = self._starting_balance - current_balance
        return loss < self._starting_balance * self.threshold_pct
