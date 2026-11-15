# compte_api/deps.py
# Dépendances FastAPI partagées par tous les routeurs.
from collections.abc import Iterator
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.config import Settings
from compte_api.mailer import Mailer
from compte_api.security import hacher_jeton
from compte_api.sessions import COOKIE_SESSION

# last_seen_at n'est réécrit qu'au-delà de cet écart, pour ne pas faire une
# écriture en base à chaque requête.
_RAFRAICHISSEMENT_SESSION = timedelta(minutes=10)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_mailer(request: Request) -> Mailer:
    return request.app.state.mailer


def get_db(request: Request) -> Iterator[DbSession]:
    db = request.app.state.sessionmaker()
    try:
        yield db
    finally:
        db.close()


def _aware(dt):
    # SQLite rend des dates sans fuseau même quand on les écrit en UTC.
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=models.maintenant().tzinfo)


def current_user(request: Request, db: DbSession = Depends(get_db)) -> models.User:
    jeton = request.cookies.get(COOKIE_SESSION)
    if not jeton:
        raise HTTPException(status_code=401, detail="Connexion requise.")
    session = db.get(models.Session, hacher_jeton(jeton))
    now = models.maintenant()
    if session is None or _aware(session.expires_at) <= now:
        raise HTTPException(status_code=401, detail="Connexion requise.")
    user = db.scalar(select(models.User).where(
        models.User.id == session.user_id, models.User.deleted_at.is_(None)))
    if user is None:
        raise HTTPException(status_code=401, detail="Connexion requise.")
    if now - _aware(session.last_seen_at) > _RAFRAICHISSEMENT_SESSION:
        session.last_seen_at = now
        db.commit()
    return user


def current_user_verified(user: models.User = Depends(current_user)) -> models.User:
    if not user.email_verified:
        raise HTTPException(status_code=403, detail="Confirme d'abord ton adresse e-mail (lien reçu par e-mail).")
    return user


def current_admin(user: models.User = Depends(current_user)) -> models.User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Réservé à l'administrateur.")
    return user
