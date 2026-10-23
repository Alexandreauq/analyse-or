# gold_bot/risk.py
# Dimensionnement de position par le risque et coupe-circuit journalier
# — voir docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import math

from shared_risk import CircuitBreaker  # noqa: F401 (re-export, voir shared_risk.py)


RISK_PROFILE_PARAMS: dict[int, dict[str, float]] = {
    1: {"risk_pct": 0.02, "threshold_pct": 0.05, "max_trades_per_day": 2},
    2: {"risk_pct": 0.035, "threshold_pct": 0.075, "max_trades_per_day": 3},
    3: {"risk_pct": 0.05, "threshold_pct": 0.10, "max_trades_per_day": 4},
    4: {"risk_pct": 0.075, "threshold_pct": 0.135, "max_trades_per_day": 6},
    5: {"risk_pct": 0.10, "threshold_pct": 0.175, "max_trades_per_day": 8},
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
