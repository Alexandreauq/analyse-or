# compte_api/routers/admin.py
# Vue d'administration en lecture seule (spec
# docs/superpowers/specs/2026-11-16-admin-dashboard-design.md). Réservée au
# rôle admin (compte_api/deps.py::current_admin) : pas d'édition, juste des
# compteurs et la liste des membres.
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.deps import current_admin, get_db

router = APIRouter(prefix="/admin")

# Marge au-dessus du rafraîchissement de session (10 min,
# compte_api/deps.py::_RAFRAICHISSEMENT_SESSION) pour ne pas rater une
# activité qui vient d'avoir lieu juste avant le dernier rafraîchissement.
FENETRE_CONNECTE = timedelta(minutes=15)


def _aware(dt: datetime) -> datetime:
    # SQLite rend des dates sans fuseau même quand on les écrit en UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.get("/stats")
def stats(_admin: models.User = Depends(current_admin), db: DbSession = Depends(get_db)) -> dict:
    seuil = models.maintenant() - FENETRE_CONNECTE
    total_membres = db.scalar(
        select(func.count()).select_from(models.User).where(models.User.deleted_at.is_(None))
    )
    verifies = db.scalar(
        select(func.count()).select_from(models.User)
        .where(models.User.deleted_at.is_(None), models.User.email_verified_at.is_not(None))
    )
    connectes_maintenant = db.scalar(
        select(func.count(func.distinct(models.Session.user_id)))
        .select_from(models.Session)
        .join(models.User, models.User.id == models.Session.user_id)
        .where(models.User.deleted_at.is_(None), models.Session.last_seen_at >= seuil)
    )
    return {
        "total_membres": total_membres,
        "verifies": verifies,
        "connectes_maintenant": connectes_maintenant,
    }
