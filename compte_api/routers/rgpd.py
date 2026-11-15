# compte_api/routers/rgpd.py
# Droits du membre sur ses données (spec §10) : correction du nom affiché,
# export de toutes ses données, suppression définitive du compte.
import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.config import Settings
from compte_api.deps import current_user, get_db, get_mailer, get_settings
from compte_api.mailer import Mailer
from compte_api.routers.auth import (
    email_masque, limiteur, trop_de_tentatives, utilisateur_public,
)
from compte_api.security import hacher_jeton, verify_password
from compte_api.sessions import COOKIE_SESSION, effacer_cookie

router = APIRouter()
journal = logging.getLogger("compte_api.rgpd")

# Un compte Google seul n'a pas de mot de passe à redemander : on exige alors
# une connexion Google récente (session ouverte il y a moins de 15 minutes).
FRAICHEUR_SESSION_GOOGLE = timedelta(minutes=15)
LIMITE_SUPPRESSION = ("limiteur_suppression_compte", 5, 15 * 60)
NOM_AFFICHE_MAX = 80


class ProfilIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=NOM_AFFICHE_MAX)


class SuppressionIn(BaseModel):
    password: str | None = Field(default=None, max_length=128)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=models.maintenant().tzinfo)


def _iso(valeur):
    return valeur.isoformat() if hasattr(valeur, "isoformat") else valeur


def _lignes(db: DbSession, modele, colonnes: list[str], *conditions, ordre=None) -> list[dict]:
    requete = select(modele).where(*conditions)
    if ordre is not None:
        requete = requete.order_by(ordre)
    return [{c: _iso(getattr(ligne, c)) for c in colonnes} for ligne in db.scalars(requete)]


@router.patch("/me")
def modifier_profil(donnees: ProfilIn, user: models.User = Depends(current_user),
                    db: DbSession = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    nom = (donnees.display_name or "").strip() or None
    user = db.get(models.User, user.id)
    user.display_name = nom
    db.commit()
    return utilisateur_public(db, user, settings)


@router.get("/export")
def exporter(user: models.User = Depends(current_user), db: DbSession = Depends(get_db)) -> Response:
    """Toutes les données du membre, en JSON téléchargeable. Jamais les
    empreintes de mot de passe ni les jetons de session ou d'e-mail."""
    regles = db.scalars(select(models.AlertRule).where(models.AlertRule.user_id == user.id)).all()
    ids_regles = [r.id for r in regles]
    reglages = db.get(models.UserSettings, user.id)
    donnees = {
        "exporte_le": models.maintenant().isoformat(),
        "compte": {
            "email": user.email,
            "display_name": user.display_name,
            "email_verified": user.email_verified,
            "connexion_google": user.google_sub is not None,
            "role": user.role,
            "alerts_email_enabled": user.alerts_email_enabled,
            "created_at": _iso(user.created_at),
            "last_login_at": _iso(user.last_login_at),
        },
        "sessions": _lignes(db, models.Session, ["created_at", "expires_at", "last_seen_at", "user_agent"],
                            models.Session.user_id == user.id, ordre=models.Session.created_at),
        "favoris": _lignes(db, models.Favorite, ["kind", "key", "created_at"],
                           models.Favorite.user_id == user.id, ordre=models.Favorite.created_at),
        "positions": _lignes(db, models.Position,
                             ["ticker", "quantity", "buy_price", "buy_date", "sell_price", "sell_date", "created_at"],
                             models.Position.user_id == user.id, ordre=models.Position.created_at),
        "recherches": _lignes(db, models.Search, ["query", "created_at"],
                              models.Search.user_id == user.id, ordre=models.Search.created_at),
        "echanges_ia": _lignes(db, models.AiExchange, ["question", "answer", "created_at"],
                               models.AiExchange.user_id == user.id, ordre=models.AiExchange.created_at),
        "utilisation_ia": _lignes(db, models.AiUsage, ["day", "count"],
                                  models.AiUsage.user_id == user.id, ordre=models.AiUsage.day),
        "regles_alerte": [{c: _iso(getattr(r, c)) for c in ["kind", "target", "threshold", "active", "created_at"]}
                          for r in regles],
        "alertes_declenchees": _lignes(db, models.AlertEvent, ["rule_id", "triggered_at", "value", "emailed_at"],
                                       models.AlertEvent.rule_id.in_(ids_regles),
                                       ordre=models.AlertEvent.triggered_at) if ids_regles else [],
        "reglages": json.loads(reglages.json) if reglages and reglages.json else {},
    }
    nom_fichier = f"ai-investment-mes-donnees-{models.maintenant().date().isoformat()}.json"
    return Response(
        json.dumps(donnees, ensure_ascii=False, indent=2), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{nom_fichier}"'},
    )


@router.delete("/account", status_code=204)
def supprimer_compte(donnees: SuppressionIn, request: Request, response: Response,
                     user: models.User = Depends(current_user), db: DbSession = Depends(get_db),
                     settings: Settings = Depends(get_settings), mailer: Mailer = Depends(get_mailer)) -> Response:
    """Suppression définitive : la ligne du membre est effacée et toutes ses
    données partent avec (ON DELETE CASCADE sur chaque table)."""
    if not limiteur(request, LIMITE_SUPPRESSION).autorise(f"user:{user.id}"):
        raise trop_de_tentatives()
    if user.password_hash:
        if not verify_password(user.password_hash, donnees.password or ""):
            raise HTTPException(status_code=403, detail="Mot de passe incorrect.")
    else:
        jeton = request.cookies.get(COOKIE_SESSION, "")
        session = db.get(models.Session, hacher_jeton(jeton)) if jeton else None
        if session is None or models.maintenant() - _aware(session.created_at) > FRAICHEUR_SESSION_GOOGLE:
            raise HTTPException(status_code=403,
                                detail="Par sécurité, reconnecte-toi avec Google puis réessaie dans les 15 minutes.")

    email = user.email
    db.delete(db.get(models.User, user.id))
    db.commit()
    try:
        mailer.envoyer(
            email, "Ton compte AI Investment a été supprimé",
            "Bonjour,\n\nTon compte AI Investment et toutes ses données (favoris, portefeuille, "
            "historique, alertes) viennent d'être supprimés définitivement.\n\n"
            "Si tu n'es pas à l'origine de cette suppression, réponds à cet e-mail.\n\n— AI Investment",
        )
    except Exception:
        journal.warning("E-mail de confirmation de suppression non envoyé pour %s", email_masque(email))
    effacer_cookie(response, settings)
    response.status_code = 204
    return response
