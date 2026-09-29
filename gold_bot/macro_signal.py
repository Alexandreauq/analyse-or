# gold_bot/macro_signal.py
# Signal swing macro dérivé du score gold_score.py déjà en production
# (taux réels FRED, DXY, CFTC, MM200) -- voir
# docs/superpowers/specs/2026-09-29-gold-bot-swing-macro-design.md.
from datetime import datetime

import requests

SCORE_JSON_URL = "https://alexandreauq.github.io/analyse-or/score.json"
ENTRY_ALERT_KIND = "entree"
# Le composite retombe à/sous 0 -> thèse haussière invalidée (l'entrée
# exigeait > 15, voir gold_score.py::compute_alerts).
EXIT_COMPOSITE_THRESHOLD = 0.0
# Même seuil que CFTC_EXTREME_PERCENTILE dans gold_score.py --
# positionnement spéculatif trop encombré pour rester exposé.
EXIT_CFTC_PERCENTILE = 90.0
# Stop sous la MM200 -- le support que la condition d'entrée de
# gold_score.py exige déjà d'être proche (near_support, écart < 1%).
SWING_STOP_BUFFER_PCT = 0.03
SWING_TAKE_PROFIT_R_MULTIPLE = 3.0
# Filet de sécurité : clôture forcée au-delà, indépendamment du signal.
SWING_MAX_HOLDING_DAYS = 30


def fetch_macro_payload(url: str = SCORE_JSON_URL, timeout: float = 15.0) -> dict:
    """Va chercher le payload JSON publié par gold_score.py (déjà en
    production, rafraîchi environ toutes les heures via GitHub Actions).
    Lève l'exception réseau telle quelle -- même convention que
    gold_bot.broker : l'appelant (gold_bot.loop.run_cycle, via
    _with_retry) gère déjà le retry et la capture d'erreur génériques."""
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def has_entry_alert(payload: dict) -> bool:
    return any(a.get("kind") == ENTRY_ALERT_KIND for a in payload.get("alerts", []))


def compute_entry_levels(payload: dict, current_price: float) -> dict | None:
    """Niveaux d'entrée pour une position swing longue : stop sous la
    MM200, take-profit à SWING_TAKE_PROFIT_R_MULTIPLE fois cette
    distance. Renvoie None si la MM200 est indisponible dans le payload,
    ou si le prix courant a bougé depuis le dernier calcul du score
    (payload rafraîchi à l'heure) au point que le stop théorique tombe
    au-dessus (ou égal) au prix courant -- pas d'entrée sur un stop
    invalide."""
    ma200 = payload.get("technical", {}).get("ma200")
    if ma200 is None:
        return None
    entry = current_price
    stop_loss = ma200 * (1 - SWING_STOP_BUFFER_PCT)
    distance = entry - stop_loss
    if distance <= 0:
        return None
    take_profit = entry + SWING_TAKE_PROFIT_R_MULTIPLE * distance
    return {"entry": entry, "stop_loss": stop_loss, "take_profit": take_profit}


def should_exit(payload: dict, position_open_time_iso: str | None, now: datetime) -> tuple[bool, str]:
    """Évalue les 3 conditions de sortie d'une position swing, dans
    l'ordre -- voir la section 3 du spec pour la justification de
    chacune. `position_open_time_iso` est le champ MetaApi `time` d'une
    position (format ISO -- vérifié le 2026-09-29 via la documentation
    officielle MetaApi, MetatraderPosition.time). Un champ absent ou
    illisible ne fait qu'ignorer la 3e condition (durée de détention),
    jamais lever d'exception ni forcer une sortie par excès de
    prudence."""
    composite_score = payload.get("composite_score")
    if composite_score is not None and composite_score <= EXIT_COMPOSITE_THRESHOLD:
        return True, f"score composite retombé à {composite_score:+.1f} (seuil {EXIT_COMPOSITE_THRESHOLD:+.1f})"

    cftc_percentile = payload.get("cftc_percentile")
    if cftc_percentile is not None and cftc_percentile >= EXIT_CFTC_PERCENTILE:
        return True, f"positionnement CFTC en zone extrême ({cftc_percentile:.0f}e percentile)"

    if position_open_time_iso:
        try:
            open_time = datetime.fromisoformat(position_open_time_iso.replace("Z", "+00:00"))
        except (ValueError, AttributeError, TypeError):
            open_time = None
        if open_time is not None and (now - open_time).days >= SWING_MAX_HOLDING_DAYS:
            return True, f"durée de détention maximale atteinte ({SWING_MAX_HOLDING_DAYS} jours)"

    return False, ""
