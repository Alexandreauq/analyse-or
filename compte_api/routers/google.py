# compte_api/routers/google.py
# « Continuer avec Google » : flux OAuth « authorization code » avec PKCE
# (S256) et `state` aléatoire. Le `state` et le `code_verifier` attendent le
# retour de Google dans un cookie court HttpOnly (10 min).
# Les deux appels réseau (échange du code, identité) passent par un petit
# client remplaçable, request.app.state.google_client : les tests y posent
# un faux Google, jamais d'appel réel.
import base64
import hashlib
import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.config import Settings
from compte_api.deps import get_db, get_settings
from compte_api.routers.auth import email_valide, normaliser_email, ouvrir_session
from compte_api.security import egal_temps_constant

router = APIRouter()
journal = logging.getLogger("compte_api.google")

URL_AUTORISATION = "https://accounts.google.com/o/oauth2/v2/auth"
URL_JETON = "https://oauth2.googleapis.com/token"
URL_IDENTITE = "https://openidconnect.googleapis.com/v1/userinfo"
COOKIE_GOOGLE = "compte_google"
DUREE_COOKIE_GOOGLE = 10 * 60


class ErreurGoogle(Exception):
    pass


class ClientGoogle:
    """Appels au serveur de Google. Remplaçable par un faux dans les tests
    (mêmes méthodes : echanger_code et identite)."""

    def __init__(self, client_id: str, client_secret: str, timeout: float = 10.0):
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout

    def echanger_code(self, code: str, code_verifier: str, redirect_uri: str) -> str:
        """Échange le code contre un jeton d'accès."""
        reponse = httpx.post(URL_JETON, timeout=self.timeout, data={
            "grant_type": "authorization_code", "code": code, "code_verifier": code_verifier,
            "redirect_uri": redirect_uri, "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        if reponse.status_code != 200:
            raise ErreurGoogle(f"échange du code refusé ({reponse.status_code})")
        jeton = reponse.json().get("access_token")
        if not jeton:
            raise ErreurGoogle("pas de jeton d'accès")
        return jeton

    def identite(self, access_token: str) -> dict:
        """{sub, email, email_verified, name} du compte Google."""
        reponse = httpx.get(URL_IDENTITE, timeout=self.timeout,
                            headers={"Authorization": f"Bearer {access_token}"})
        if reponse.status_code != 200:
            raise ErreurGoogle(f"identité refusée ({reponse.status_code})")
        return reponse.json()


def _client_google(request: Request, settings: Settings):
    client = getattr(request.app.state, "google_client", None)
    if client is None:
        client = ClientGoogle(settings.google_client_id, settings.google_client_secret)
        request.app.state.google_client = client
    return client


def url_retour(settings: Settings) -> str:
    """URL de retour à déclarer dans la console Google Cloud."""
    return f"{settings.site_url}/api/compte/google/callback"


def _defi_pkce(verifier: str) -> str:
    empreinte = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(empreinte).rstrip(b"=").decode("ascii")


def _vrai(valeur) -> bool:
    # Google renvoie un booléen, mais certaines réponses l'écrivent en texte.
    return valeur is True or (isinstance(valeur, str) and valeur.lower() == "true")


@router.get("/google/start")
def google_start(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Connexion Google indisponible.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)  # 86 caractères (43 à 128 exigés)
    parametres = {
        "client_id": settings.google_client_id,
        "redirect_uri": url_retour(settings),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": _defi_pkce(verifier),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    response = RedirectResponse(f"{URL_AUTORISATION}?{urlencode(parametres)}", status_code=302)
    # SameSite=Lax : le cookie revient bien sur la redirection de Google
    # (navigation de premier niveau en GET). Le point ne figure jamais dans
    # un jeton token_urlsafe : séparateur sûr.
    response.set_cookie(COOKIE_GOOGLE, f"{state}.{verifier}", max_age=DUREE_COOKIE_GOOGLE,
                        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")
    return response


def _trouver_ou_creer(db: DbSession, profil: dict) -> models.User:
    """Connexion, liaison ou création selon l'identité Google."""
    sub = str(profil.get("sub") or "")
    email = normaliser_email(str(profil.get("email") or ""))
    verifie = _vrai(profil.get("email_verified"))
    if not sub or not email_valide(email):
        raise ErreurGoogle("identité incomplète")

    # 1. Compte Google déjà connu.
    user = db.scalar(select(models.User).where(models.User.google_sub == sub))
    if user is not None:
        if user.deleted_at is not None:
            raise ErreurGoogle("compte supprimé")
        return user

    existant = db.scalar(select(models.User).where(models.User.email == email))
    if existant is not None:
        # 2. Même adresse, garantie par Google : on lie les deux.
        if not verifie or existant.deleted_at is not None or existant.google_sub:
            raise ErreurGoogle("liaison impossible")
        existant.google_sub = sub
        if existant.email_verified_at is None:
            # Adresse jamais confirmée : le mot de passe a pu être choisi par
            # quelqu'un d'autre qui a inscrit cette adresse en premier. On
            # l'efface et on ferme ses sessions, sinon cette personne
            # garderait l'accès au compte du vrai titulaire.
            existant.password_hash = None
            db.execute(delete(models.Session).where(models.Session.user_id == existant.id))
            existant.email_verified_at = models.maintenant()
        db.commit()
        journal.info("Compte Google lié au membre %s", existant.id)
        return existant

    # 3. Nouveau compte Google seul (sans mot de passe).
    nom = str(profil.get("name") or "").strip()[:80] or None
    user = models.User(email=email, google_sub=sub, password_hash=None, display_name=nom,
                       email_verified_at=models.maintenant() if verifie else None)
    db.add(user)
    db.commit()
    db.refresh(user)
    journal.info("Inscription Google du membre %s", user.id)
    return user


@router.get("/google/callback")
def google_callback(request: Request, code: str = "", state: str = "",
                    db: DbSession = Depends(get_db),
                    settings: Settings = Depends(get_settings)) -> RedirectResponse:
    echec = RedirectResponse(f"{settings.site_url}/?compte_erreur=google", status_code=302)
    echec.delete_cookie(COOKIE_GOOGLE, path="/")
    attendu_state, _, verifier = request.cookies.get(COOKIE_GOOGLE, "").partition(".")
    if (not code or not state or not attendu_state or not verifier
            or not egal_temps_constant(state, attendu_state)):
        journal.info("Retour Google refusé (state absent ou différent)")
        return echec
    try:
        client = _client_google(request, settings)
        jeton = client.echanger_code(code, verifier, url_retour(settings))
        user = _trouver_ou_creer(db, client.identite(jeton))
    except (ErreurGoogle, httpx.HTTPError, ValueError, IntegrityError) as e:
        db.rollback()
        journal.warning("Connexion Google en échec : %s", e)
        return echec
    response = RedirectResponse(f"{settings.site_url}/", status_code=302)
    response.delete_cookie(COOKIE_GOOGLE, path="/")
    ouvrir_session(db, request, response, user, settings)
    return response
