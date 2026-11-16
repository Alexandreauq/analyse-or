# compte_api/routers/favoris.py
# Favoris d'un membre : indices (clé type CAC40) et entreprises (ticker type
# AIR.PA, ^GSPC, GC=F). Pas besoin d'adresse confirmée : suivre une valeur
# n'envoie rien par e-mail (les alertes, elles, l'exigent).
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.deps import current_user, get_db

router = APIRouter()

FAVORIS_MAX = 200
# Clé stockée telle quelle (sensible à la casse) : lettres, chiffres et les
# quelques signes présents dans les tickers Yahoo (. - ^ =).
MOTIF_CLE = r"^[A-Za-z0-9.\-^=]{1,20}$"

Kind = Literal["index", "company"]
Cle = Annotated[str, Path(pattern=MOTIF_CLE)]


def _utc(dt: datetime) -> datetime:
    # SQLite rend des dates sans fuseau même quand on les écrit en UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _vers_json(fav: models.Favorite) -> dict:
    return {"kind": fav.kind, "key": fav.key, "created_at": _utc(fav.created_at).isoformat()}


@router.get("/favorites")
def lister(user: models.User = Depends(current_user), db: DbSession = Depends(get_db)) -> list[dict]:
    favoris = db.scalars(
        select(models.Favorite)
        .where(models.Favorite.user_id == user.id)
        .order_by(models.Favorite.created_at.desc(), models.Favorite.kind, models.Favorite.key)
    ).all()
    return [_vers_json(f) for f in favoris]


@router.put("/favorites/{kind}/{key}")
def ajouter(kind: Kind, key: Cle, user: models.User = Depends(current_user),
            db: DbSession = Depends(get_db)) -> dict:
    existant = db.get(models.Favorite, (user.id, kind, key))
    if existant is not None:
        return _vers_json(existant)
    nombre = db.scalar(select(func.count()).select_from(models.Favorite)
                       .where(models.Favorite.user_id == user.id))
    if nombre >= FAVORIS_MAX:
        raise HTTPException(status_code=409, detail=f"Tu as atteint la limite de {FAVORIS_MAX} favoris.")
    fav = models.Favorite(user_id=user.id, kind=kind, key=key, created_at=models.maintenant())
    db.add(fav)
    try:
        db.commit()
    except IntegrityError:
        # Deux ajouts simultanés de la même cible : l'autre a gagné, le
        # résultat est le même (idempotence).
        db.rollback()
        fav = db.get(models.Favorite, (user.id, kind, key))
        if fav is None:
            raise
    return _vers_json(fav)


@router.delete("/favorites/{kind}/{key}", status_code=204)
def retirer(kind: Kind, key: Cle, user: models.User = Depends(current_user),
            db: DbSession = Depends(get_db)) -> Response:
    fav = db.get(models.Favorite, (user.id, kind, key))
    if fav is not None:
        db.delete(fav)
        db.commit()
    return Response(status_code=204)
