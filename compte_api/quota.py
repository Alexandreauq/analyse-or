# compte_api/quota.py
# Quota quotidien de questions à l'assistant IA, par membre (jour UTC).
# Partagé par /me (questions restantes) et /ai/ask (consommation).
from datetime import datetime, timezone

from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.config import Settings


def jour_utc() -> "datetime.date":
    return datetime.now(timezone.utc).date()


def _utilisation(db: DbSession, user_id: int, jour) -> models.AiUsage | None:
    return db.get(models.AiUsage, (user_id, jour))


def quota_restant(db: DbSession, user: models.User, settings: Settings, jour=None) -> int | None:
    """Questions restantes aujourd'hui ; None = illimité (administrateur)."""
    if user.role == "admin":
        return None
    usage = _utilisation(db, user.id, jour or jour_utc())
    return max(0, settings.ai_quota_par_jour - (usage.count if usage else 0))


def consommer_quota(db: DbSession, user: models.User, settings: Settings, jour=None) -> bool:
    """Compte une question si le quota le permet. Faux si le quota est épuisé."""
    if user.role == "admin":
        return True
    jour = jour or jour_utc()
    usage = _utilisation(db, user.id, jour)
    if usage is None:
        usage = models.AiUsage(user_id=user.id, day=jour, count=0)
        db.add(usage)
    if usage.count >= settings.ai_quota_par_jour:
        db.rollback()
        return False
    usage.count += 1
    db.commit()
    return True


def rendre_quota(db: DbSession, user: models.User, jour=None) -> None:
    """Annule une consommation (ex. l'assistant n'a jamais répondu)."""
    if user.role == "admin":
        return
    usage = _utilisation(db, user.id, jour or jour_utc())
    if usage is not None and usage.count > 0:
        usage.count -= 1
        db.commit()
