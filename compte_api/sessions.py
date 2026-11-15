# compte_api/sessions.py
# Ouverture et fermeture des sessions de connexion. Le cookie contient le
# jeton clair, la table `sessions` son hachage.
from datetime import timedelta

from fastapi import Response
from sqlalchemy import delete
from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.config import Settings
from compte_api.security import hacher_jeton, nouveau_jeton

COOKIE_SESSION = "compte_session"
DUREE_SESSION = timedelta(days=30)


def creer_session(db: DbSession, user_id: int, user_agent: str | None = None) -> str:
    """Enregistre une session et renvoie le jeton clair à poser en cookie."""
    clair, hache = nouveau_jeton()
    now = models.maintenant()
    db.add(models.Session(
        id_hash=hache, user_id=user_id, created_at=now, last_seen_at=now,
        expires_at=now + DUREE_SESSION, user_agent=(user_agent or "")[:255] or None,
    ))
    db.commit()
    return clair


def poser_cookie(response: Response, jeton: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_SESSION, jeton, max_age=int(DUREE_SESSION.total_seconds()),
        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/",
    )


def effacer_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(COOKIE_SESSION, path="/", httponly=True,
                           secure=settings.cookie_secure, samesite="lax")


def supprimer_session(db: DbSession, jeton: str | None) -> None:
    if not jeton:
        return
    db.execute(delete(models.Session).where(models.Session.id_hash == hacher_jeton(jeton)))
    db.commit()


def supprimer_toutes_les_sessions(db: DbSession, user_id: int) -> None:
    db.execute(delete(models.Session).where(models.Session.user_id == user_id))
    db.commit()
