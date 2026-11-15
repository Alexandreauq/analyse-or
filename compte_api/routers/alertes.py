# compte_api/routers/alertes.py
# Règles d'alerte du membre (score au-dessus / en dessous d'un seuil,
# activation du signal d'entrée), réglage du résumé quotidien par e-mail et
# lien de désinscription en un clic (sans session, signé HMAC).
# La détection et l'envoi vivent dans compte_api/jobs/alertes.py.
import hashlib
import hmac
import html
import os
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.config import Settings
from compte_api.deps import current_user, get_db, get_settings

router = APIRouter()

MAX_REGLES = 50
SEUIL_MIN, SEUIL_MAX = -100.0, 100.0
# Même format que les favoris : un ticker de 1 à 20 caractères.
MOTIF_TICKER = r"^[A-Za-z0-9.\-^=]{1,20}$"
KINDS_SCORE = ("score_above", "score_below")
Kind = Literal["score_above", "score_below", "entry_signal"]


# --- Lien de désinscription signé ---------------------------------------
# Jeton = "<id du membre>.<HMAC-SHA256 hexadécimal>". Sans date d'expiration :
# un lien de désinscription doit marcher même dans un vieil e-mail.

_CONTEXTE_JETON = "compte-alertes-desinscription:"


def _cle_desinscription(settings: Settings) -> bytes:
    return (os.environ.get("COMPTE_UNSUBSCRIBE_SECRET") or settings.proxy_secret).encode("utf-8")


def _signature(user_id: int, settings: Settings) -> str:
    message = f"{_CONTEXTE_JETON}{user_id}".encode("utf-8")
    return hmac.new(_cle_desinscription(settings), message, hashlib.sha256).hexdigest()


def jeton_desinscription(user_id: int, settings: Settings) -> str:
    return f"{user_id}.{_signature(user_id, settings)}"


def lire_jeton_desinscription(jeton: str, settings: Settings) -> int | None:
    """Id du membre si le jeton est authentique, sinon None."""
    if not jeton or len(jeton) > 200 or "." not in jeton:
        return None
    brut_id, signature = jeton.split(".", 1)
    if not brut_id.isdigit() or len(brut_id) > 18:
        return None
    user_id = int(brut_id)
    if not hmac.compare_digest(signature.encode("utf-8"), _signature(user_id, settings).encode("utf-8")):
        return None
    return user_id


def lien_desinscription(user_id: int, settings: Settings) -> str:
    # Le lien passe par le relais du site (/api/compte/...), qui pose
    # l'en-tête secret exigé par le service.
    return f"{settings.site_url}/api/compte/alerts/unsubscribe?token={jeton_desinscription(user_id, settings)}"


# --- Schémas --------------------------------------------------------------

class RegleCreation(BaseModel):
    kind: Kind
    target: str = Field(pattern=MOTIF_TICKER)
    threshold: float | None = Field(default=None, ge=SEUIL_MIN, le=SEUIL_MAX)
    active: bool = True


class RegleModification(BaseModel):
    kind: Kind | None = None
    target: str | None = Field(default=None, pattern=MOTIF_TICKER)
    threshold: float | None = Field(default=None, ge=SEUIL_MIN, le=SEUIL_MAX)
    active: bool | None = None


class ReglageEmail(BaseModel):
    alerts_email_enabled: bool


def _verifier_seuil(kind: str, threshold: float | None) -> float | None:
    if kind in KINDS_SCORE:
        if threshold is None:
            raise HTTPException(status_code=422, detail="Indique un seuil entre -100 et 100.")
        return float(threshold)
    if threshold is not None:
        raise HTTPException(status_code=422, detail="Le signal d'entrée ne prend pas de seuil.")
    return None


def _regle_du_membre(db: DbSession, user: models.User, regle_id: int) -> models.AlertRule:
    regle = db.get(models.AlertRule, regle_id)
    # Même réponse qu'une règle inexistante : on ne révèle rien des autres membres.
    if regle is None or regle.user_id != user.id:
        raise HTTPException(status_code=404, detail="Alerte introuvable.")
    return regle


def _doublon(db: DbSession, user: models.User, kind: str, target: str, threshold, sauf_id=None) -> bool:
    requete = select(models.AlertRule.id).where(
        models.AlertRule.user_id == user.id, models.AlertRule.kind == kind,
        models.AlertRule.target == target,
        models.AlertRule.threshold.is_(None) if threshold is None else models.AlertRule.threshold == threshold,
    )
    if sauf_id is not None:
        requete = requete.where(models.AlertRule.id != sauf_id)
    return db.scalar(requete.limit(1)) is not None


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:  # SQLite rend des dates sans fuseau, écrites en UTC
        dt = dt.replace(tzinfo=models.maintenant().tzinfo)
    return dt.isoformat()


def _dernier_declenchement(db: DbSession, regle_ids: list[int]) -> dict[int, datetime]:
    if not regle_ids:
        return {}
    lignes = db.execute(
        select(models.AlertEvent.rule_id, func.max(models.AlertEvent.triggered_at))
        .where(models.AlertEvent.rule_id.in_(regle_ids))
        .group_by(models.AlertEvent.rule_id)
    ).all()
    return {rule_id: dernier for rule_id, dernier in lignes}


def _en_dict(regle: models.AlertRule, dernier: datetime | None) -> dict:
    return {
        "id": regle.id, "kind": regle.kind, "target": regle.target, "threshold": regle.threshold,
        "active": bool(regle.active), "created_at": _iso(regle.created_at),
        "dernier_declenchement": _iso(dernier),
    }


# --- Routes ----------------------------------------------------------------
# /alerts/email et /alerts/unsubscribe sont déclarées avant /alerts/{id}
# pour ne jamais être prises pour un identifiant.

@router.get("/alerts/email")
def lire_reglage_email(user: models.User = Depends(current_user)) -> dict:
    # email_verified permet à l'interface d'expliquer pourquoi rien ne part.
    return {"alerts_email_enabled": bool(user.alerts_email_enabled), "email_verified": user.email_verified}


@router.put("/alerts/email")
def modifier_reglage_email(corps: ReglageEmail, user: models.User = Depends(current_user),
                           db: DbSession = Depends(get_db)) -> dict:
    user.alerts_email_enabled = corps.alerts_email_enabled
    db.commit()
    return {"alerts_email_enabled": bool(user.alerts_email_enabled), "email_verified": user.email_verified}


_PAGE = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titre}</title>
<style>
  body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
         background: #0a0e1a; color: #e8ecf5; font: 16px/1.5 Inter, system-ui, sans-serif; }}
  main {{ max-width: 28rem; margin: 1rem; padding: 2rem; border: 1px solid #2a3550;
         border-radius: 16px; background: #161d30; text-align: center; }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .75rem; }}
  p {{ color: #9aa6c4; margin: 0 0 1rem; }}
  a {{ color: #8fc29e; }}
</style></head>
<body><main><h1>{titre}</h1><p>{message}</p><p><a href="{site}">Retour au site</a></p></main></body></html>
"""


def _page(settings: Settings, titre: str, message: str, statut: int) -> HTMLResponse:
    contenu = _PAGE.format(titre=html.escape(titre, quote=False), message=html.escape(message, quote=False),
                           site=html.escape(settings.site_url + "/", quote=True))
    return HTMLResponse(contenu, status_code=statut, headers={"Cache-Control": "no-store"})


@router.get("/alerts/unsubscribe", response_class=HTMLResponse)
def desinscription(token: str = Query(default="", max_length=200), db: DbSession = Depends(get_db),
                   settings: Settings = Depends(get_settings)) -> HTMLResponse:
    user_id = lire_jeton_desinscription(token, settings)
    user = db.get(models.User, user_id) if user_id is not None else None
    if user is None:
        return _page(settings, "Lien invalide",
                     "Ce lien de désinscription n'est pas valide. Tu peux couper les e-mails "
                     "d'alerte depuis ton espace, onglet Alertes.", 400)
    user.alerts_email_enabled = False
    db.commit()
    return _page(settings, "Désinscription confirmée",
                 "Tu ne recevras plus d'e-mails d'alerte. Tu peux les réactiver à tout moment "
                 "depuis ton espace, onglet Alertes.", 200)


@router.get("/alerts")
def lister(user: models.User = Depends(current_user), db: DbSession = Depends(get_db)) -> list[dict]:
    regles = db.scalars(select(models.AlertRule).where(models.AlertRule.user_id == user.id)
                        .order_by(models.AlertRule.created_at, models.AlertRule.id)).all()
    derniers = _dernier_declenchement(db, [r.id for r in regles])
    return [_en_dict(r, derniers.get(r.id)) for r in regles]


@router.post("/alerts", status_code=201)
def creer(corps: RegleCreation, user: models.User = Depends(current_user),
          db: DbSession = Depends(get_db)) -> dict:
    seuil = _verifier_seuil(corps.kind, corps.threshold)
    nombre = db.scalar(select(func.count()).select_from(models.AlertRule)
                       .where(models.AlertRule.user_id == user.id))
    if nombre >= MAX_REGLES:
        raise HTTPException(status_code=409, detail=f"Tu as atteint la limite de {MAX_REGLES} alertes.")
    if _doublon(db, user, corps.kind, corps.target, seuil):
        raise HTTPException(status_code=409, detail="Cette alerte existe déjà.")
    regle = models.AlertRule(user_id=user.id, kind=corps.kind, target=corps.target,
                             threshold=seuil, active=corps.active)
    db.add(regle)
    db.commit()
    db.refresh(regle)
    return _en_dict(regle, None)


@router.patch("/alerts/{regle_id}")
def modifier(regle_id: int, corps: RegleModification, user: models.User = Depends(current_user),
             db: DbSession = Depends(get_db)) -> dict:
    regle = _regle_du_membre(db, user, regle_id)
    champs = corps.model_fields_set
    kind = corps.kind if "kind" in champs and corps.kind is not None else regle.kind
    target = corps.target if "target" in champs and corps.target is not None else regle.target
    if "threshold" in champs:
        seuil = corps.threshold
    else:
        # Passer à entry_signal efface le seuil ; sinon on garde l'ancien.
        seuil = None if kind == "entry_signal" else regle.threshold
    seuil = _verifier_seuil(kind, seuil)
    if (kind, target, seuil) != (regle.kind, regle.target, regle.threshold) and \
            _doublon(db, user, kind, target, seuil, sauf_id=regle.id):
        raise HTTPException(status_code=409, detail="Cette alerte existe déjà.")
    regle.kind, regle.target, regle.threshold = kind, target, seuil
    if "active" in champs and corps.active is not None:
        regle.active = corps.active
    db.commit()
    return _en_dict(regle, _dernier_declenchement(db, [regle.id]).get(regle.id))


@router.delete("/alerts/{regle_id}", status_code=204)
def supprimer(regle_id: int, user: models.User = Depends(current_user),
              db: DbSession = Depends(get_db)) -> Response:
    regle = _regle_du_membre(db, user, regle_id)
    db.delete(regle)
    db.commit()
    return Response(status_code=204)
