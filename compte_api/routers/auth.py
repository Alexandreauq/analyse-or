# compte_api/routers/auth.py
# Comptes e-mail + mot de passe : inscription, confirmation de l'adresse,
# connexion, déconnexion, mot de passe oublié, profil (/me).
# Les fonctions utilisateur_public, ouvrir_session et limiteur servent aussi
# à la connexion Google (routers/google.py).
import functools
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.config import Settings
from compte_api.deps import current_user, get_db, get_mailer, get_settings
from compte_api.email_templates import gabarit_bouton
from compte_api.mailer import Mailer
from compte_api.quota import quota_restant
from compte_api.ratelimit import Limiteur, adresse_client
from compte_api.security import (
    hacher_jeton, hash_password, nouveau_jeton, password_needs_rehash, verify_password,
)
from compte_api.sessions import (
    COOKIE_SESSION, creer_session, effacer_cookie, poser_cookie, supprimer_session,
    supprimer_toutes_les_sessions,
)

router = APIRouter()
journal = logging.getLogger("compte_api.auth")

DUREE_JETON_VERIFY = timedelta(hours=24)
DUREE_JETON_RESET = timedelta(hours=1)
MOT_DE_PASSE_MIN = 10
MOT_DE_PASSE_MAX = 128
EMAIL_MAX = 254
_FORME_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_FICHIER_COURANTS = Path(__file__).resolve().parent.parent / "mots_de_passe_courants.txt"

MSG_ECHEC_CONNEXION = "E-mail ou mot de passe incorrect."
MSG_TROP_DE_TENTATIVES = "Trop de tentatives, réessaie dans quelques minutes."
MSG_LIEN_INVALIDE = "Ce lien est invalide ou a expiré."
MSG_OUBLI = "Si un compte existe pour cette adresse, un e-mail vient d'être envoyé."

# Limites : (nom sur app.state, maximum, fenêtre en secondes).
LIMITE_CONNEXION_EMAIL = ("limiteur_connexion_email", 5, 15 * 60)
LIMITE_CONNEXION_IP = ("limiteur_connexion_ip", 5, 15 * 60)
LIMITE_INSCRIPTION_IP = ("limiteur_inscription_ip", 5, 3600)
LIMITE_OUBLI_EMAIL = ("limiteur_oubli_email", 3, 3600)
LIMITE_OUBLI_IP = ("limiteur_oubli_ip", 10, 3600)
LIMITE_RENVOI = ("limiteur_renvoi_verify", 3, 3600)


# ---------------------------------------------------------------- outils

def limiteur(request: Request, limite: tuple[str, int, int]) -> Limiteur:
    """Limiteur partagé par l'application, créé au premier usage (les tests
    peuvent en poser un autre sur app.state avant la requête)."""
    nom, maximum, fenetre = limite
    existant = getattr(request.app.state, nom, None)
    if existant is None:
        existant = Limiteur(maximum, fenetre)
        setattr(request.app.state, nom, existant)
    return existant


def trop_de_tentatives() -> HTTPException:
    return HTTPException(status_code=429, detail=MSG_TROP_DE_TENTATIVES)


def normaliser_email(email: str) -> str:
    return (email or "").strip().lower()


def email_valide(email: str) -> bool:
    return len(email) <= EMAIL_MAX and bool(_FORME_EMAIL.match(email))


def email_masque(email: str) -> str:
    """Pour les journaux : jamais l'adresse complète."""
    local, _, domaine = email.partition("@")
    return f"{local[:1]}***@{domaine}"


@functools.cache
def mots_de_passe_courants() -> frozenset[str]:
    try:
        lignes = _FICHIER_COURANTS.read_text(encoding="utf-8").splitlines()
    except OSError:
        journal.warning("Liste des mots de passe courants introuvable")
        return frozenset()
    return frozenset(l.strip().lower() for l in lignes if l.strip() and not l.startswith("#"))


def erreur_mot_de_passe(mot_de_passe: str, email: str) -> str | None:
    """Message d'erreur si le mot de passe est refusé, sinon None."""
    if len(mot_de_passe) < MOT_DE_PASSE_MIN:
        return f"Le mot de passe doit contenir au moins {MOT_DE_PASSE_MIN} caractères."
    if len(mot_de_passe) > MOT_DE_PASSE_MAX:
        return f"Le mot de passe ne doit pas dépasser {MOT_DE_PASSE_MAX} caractères."
    bas = mot_de_passe.lower()
    if bas in mots_de_passe_courants():
        return "Ce mot de passe est trop courant, choisis-en un autre."
    local = email.partition("@")[0]
    # Une partie locale d'une ou deux lettres se retrouverait dans presque
    # tous les mots de passe : on ne l'applique qu'à partir de 3 caractères.
    if len(local) >= 3 and local in bas:
        return "Le mot de passe ne doit pas contenir ton adresse e-mail."
    return None


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite rend des dates sans fuseau même quand on les écrit en UTC.
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=models.maintenant().tzinfo)


def utilisateur_public(db: DbSession, user: models.User, settings: Settings) -> dict:
    """Objet membre renvoyé par /me, /register, /login et /password/reset."""
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "email_verified": user.email_verified,
        "quota_ia_restant": quota_restant(db, user, settings),
    }


def ouvrir_session(db: DbSession, request: Request, response: Response,
                   user: models.User, settings: Settings) -> None:
    """Note la connexion, ouvre une session et pose le cookie. La session
    que le navigateur avait peut-être déjà (autre compte) est fermée."""
    supprimer_session(db, request.cookies.get(COOKIE_SESSION))
    user.last_login_at = models.maintenant()
    db.commit()
    jeton = creer_session(db, user.id, request.headers.get("user-agent"))
    poser_cookie(response, jeton, settings)


def chercher_par_email(db: DbSession, email: str) -> models.User | None:
    return db.scalar(select(models.User).where(
        models.User.email == email, models.User.deleted_at.is_(None)))


def _creer_jeton_email(db: DbSession, user_id: int, purpose: str, duree: timedelta) -> str:
    """Remplace les liens encore valides du même type : seul le dernier marche."""
    db.execute(delete(models.EmailToken).where(
        models.EmailToken.user_id == user_id, models.EmailToken.purpose == purpose,
        models.EmailToken.used_at.is_(None)))
    clair, hache = nouveau_jeton()
    db.add(models.EmailToken(token_hash=hache, user_id=user_id, purpose=purpose,
                             expires_at=models.maintenant() + duree))
    db.commit()
    return clair


def _consommer_jeton(db: DbSession, clair: str, purpose: str) -> models.User | None:
    """Utilisateur du lien s'il est valide (sans le marquer utilisé)."""
    if not clair:
        return None
    jeton = db.get(models.EmailToken, hacher_jeton(clair))
    if (jeton is None or jeton.purpose != purpose or jeton.used_at is not None
            or _aware(jeton.expires_at) <= models.maintenant()):
        return None
    user = db.get(models.User, jeton.user_id)
    if user is None or user.deleted_at is not None:
        return None
    return user


def _marquer_utilise(db: DbSession, clair: str) -> None:
    jeton = db.get(models.EmailToken, hacher_jeton(clair))
    jeton.used_at = models.maintenant()


def _envoyer(mailer: Mailer, destinataire: str, sujet: str, texte: str, html: str | None = None) -> None:
    # Une panne SMTP ne doit pas faire échouer l'inscription : le membre
    # peut redemander le lien.
    try:
        mailer.envoyer(destinataire, sujet, texte, html)
    except Exception:  # noqa: BLE001
        journal.exception("Envoi d'e-mail impossible (%s)", email_masque(destinataire))


def envoyer_confirmation(db: DbSession, mailer: Mailer, settings: Settings, user: models.User) -> None:
    jeton = _creer_jeton_email(db, user.id, "verify", DUREE_JETON_VERIFY)
    lien = f"{settings.site_url}/?verify={jeton}"
    _envoyer(mailer, user.email, "Confirme ton adresse e-mail", (
        "Bonjour,\n\n"
        "Pour confirmer l'adresse e-mail de ton compte, ouvre ce lien :\n"
        f"{lien}\n\n"
        "Le lien est valable 24 heures. Si tu n'as pas créé de compte, ignore ce message.\n\n"
        "Nomi"
    ), gabarit_bouton(
        "Confirme ton adresse",
        "Un dernier clic pour activer ton compte et débloquer ton portefeuille, les alertes et l'analyse IA.",
        "Confirmer mon adresse", lien,
        expiration="Ce lien expire dans 24 heures.",
        mention_bas="Tu n'as pas créé de compte ? Ignore simplement ce message.",
    ))


def _envoyer_reinitialisation(db: DbSession, mailer: Mailer, settings: Settings, user: models.User) -> None:
    jeton = _creer_jeton_email(db, user.id, "reset", DUREE_JETON_RESET)
    lien = f"{settings.site_url}/?reset={jeton}"
    _envoyer(mailer, user.email, "Réinitialise ton mot de passe", (
        "Bonjour,\n\n"
        "Pour choisir un nouveau mot de passe, ouvre ce lien :\n"
        f"{lien}\n\n"
        "Le lien est valable 1 heure. Si tu n'as rien demandé, ignore ce message : "
        "ton mot de passe actuel reste valable.\n\n"
        "Nomi"
    ), gabarit_bouton(
        "Réinitialise ton mot de passe",
        "Tu as demandé à choisir un nouveau mot de passe. Clique ci-dessous pour en définir un.",
        "Choisir un nouveau mot de passe", lien,
        expiration="Ce lien expire dans 1 heure.",
        mention_bas="Tu n'as rien demandé ? Ignore ce message, ton mot de passe actuel reste valable.",
    ))


@functools.cache
def _hache_leurre() -> str:
    # Vérifié quand le compte n'existe pas, pour que la durée de réponse ne
    # révèle pas l'existence d'une adresse.
    return hash_password("leurre-pour-temps-constant")


# ---------------------------------------------------------------- schémas

class InscriptionIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)
    display_name: str | None = Field(default=None, max_length=200)
    site_web: str | None = Field(default=None, max_length=500)  # piège anti-robot


class JetonIn(BaseModel):
    token: str = Field(max_length=200)


class ConnexionIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


class EmailIn(BaseModel):
    email: str = Field(max_length=320)


class ReinitialisationIn(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=1024)


# ---------------------------------------------------------------- routes

@router.post("/register", status_code=201)
def inscription(donnees: InscriptionIn, request: Request, response: Response,
                db: DbSession = Depends(get_db), settings: Settings = Depends(get_settings),
                mailer: Mailer = Depends(get_mailer)) -> dict:
    if not limiteur(request, LIMITE_INSCRIPTION_IP).autorise(adresse_client(request)):
        raise trop_de_tentatives()
    email = normaliser_email(donnees.email)
    nom = (donnees.display_name or "").strip()[:80] or None
    if (donnees.site_web or "").strip():
        # Robot : même réponse qu'une inscription réussie, rien n'est créé.
        journal.info("Inscription piégée ignorée")
        return {"id": 0, "email": email, "display_name": nom, "role": "member",
                "email_verified": False, "quota_ia_restant": settings.ai_quota_par_jour}
    if not email_valide(email):
        raise HTTPException(status_code=422, detail="Adresse e-mail invalide.")
    erreur = erreur_mot_de_passe(donnees.password, email)
    if erreur:
        raise HTTPException(status_code=422, detail=erreur)
    # Une adresse reste prise tant que la ligne existe (même compte supprimé).
    if db.scalar(select(models.User.id).where(models.User.email == email)) is not None:
        raise HTTPException(status_code=409, detail="Un compte existe déjà avec cette adresse.")
    user = models.User(email=email, password_hash=hash_password(donnees.password), display_name=nom)
    db.add(user)
    db.commit()
    db.refresh(user)
    envoyer_confirmation(db, mailer, settings, user)
    ouvrir_session(db, request, response, user, settings)
    journal.info("Inscription du membre %s", user.id)
    return utilisateur_public(db, user, settings)


@router.post("/verify")
def confirmer_email(donnees: JetonIn, db: DbSession = Depends(get_db)) -> dict:
    user = _consommer_jeton(db, donnees.token, "verify")
    if user is None:
        raise HTTPException(status_code=400, detail=MSG_LIEN_INVALIDE)
    _marquer_utilise(db, donnees.token)
    if user.email_verified_at is None:
        user.email_verified_at = models.maintenant()
    db.commit()
    return {"email_verified": True}


@router.post("/verify/resend", status_code=202)
def renvoyer_confirmation(request: Request, user: models.User = Depends(current_user),
                          db: DbSession = Depends(get_db), settings: Settings = Depends(get_settings),
                          mailer: Mailer = Depends(get_mailer)) -> dict:
    if user.email_verified:
        raise HTTPException(status_code=400, detail="Ton adresse e-mail est déjà confirmée.")
    if not limiteur(request, LIMITE_RENVOI).autorise(str(user.id)):
        raise trop_de_tentatives()
    envoyer_confirmation(db, mailer, settings, user)
    return {"detail": "Un nouveau lien de confirmation vient d'être envoyé."}


@router.post("/login")
def connexion(donnees: ConnexionIn, request: Request, response: Response,
              db: DbSession = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    email = normaliser_email(donnees.email)
    par_email = limiteur(request, LIMITE_CONNEXION_EMAIL)
    par_ip = limiteur(request, LIMITE_CONNEXION_IP)
    ip = adresse_client(request)
    if par_email.depasse(email) or par_ip.depasse(ip):
        raise trop_de_tentatives()
    user = chercher_par_email(db, email) if email_valide(email) else None
    if user is None or not user.password_hash:
        verify_password(_hache_leurre(), donnees.password)
        ok = False
    else:
        ok = verify_password(user.password_hash, donnees.password)
    if not ok:
        par_email.enregistrer(email)
        par_ip.enregistrer(ip)
        journal.info("Échec de connexion (%s)", email_masque(email))
        raise HTTPException(status_code=401, detail=MSG_ECHEC_CONNEXION)
    par_email.reinitialiser(email)
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(donnees.password)
    ouvrir_session(db, request, response, user, settings)
    return utilisateur_public(db, user, settings)


@router.post("/logout", status_code=204)
def deconnexion(request: Request, db: DbSession = Depends(get_db),
                settings: Settings = Depends(get_settings)) -> Response:
    # Sans session, rien à fermer : la réponse reste 204 (idempotent).
    supprimer_session(db, request.cookies.get(COOKIE_SESSION))
    response = Response(status_code=204)
    effacer_cookie(response, settings)
    return response


@router.post("/password/forgot", status_code=202)
def mot_de_passe_oublie(donnees: EmailIn, request: Request, db: DbSession = Depends(get_db),
                        settings: Settings = Depends(get_settings),
                        mailer: Mailer = Depends(get_mailer)) -> dict:
    email = normaliser_email(donnees.email)
    if not email_valide(email):
        raise HTTPException(status_code=422, detail="Adresse e-mail invalide.")
    # Limites par adresse et par IP : identiques que le compte existe ou non.
    if not limiteur(request, LIMITE_OUBLI_EMAIL).autorise(email):
        raise trop_de_tentatives()
    if not limiteur(request, LIMITE_OUBLI_IP).autorise(adresse_client(request)):
        raise trop_de_tentatives()
    user = chercher_par_email(db, email)
    if user is not None:
        # Aussi pour un compte Google seul : il peut ainsi définir un mot de passe.
        _envoyer_reinitialisation(db, mailer, settings, user)
    return {"detail": MSG_OUBLI}


@router.post("/password/reset")
def reinitialiser_mot_de_passe(donnees: ReinitialisationIn, request: Request, response: Response,
                               db: DbSession = Depends(get_db),
                               settings: Settings = Depends(get_settings)) -> dict:
    user = _consommer_jeton(db, donnees.token, "reset")
    if user is None:
        raise HTTPException(status_code=400, detail=MSG_LIEN_INVALIDE)
    # Le lien n'est consommé qu'avec un mot de passe accepté : le membre
    # peut corriger sa saisie sans redemander d'e-mail.
    erreur = erreur_mot_de_passe(donnees.password, user.email)
    if erreur:
        raise HTTPException(status_code=422, detail=erreur)
    _marquer_utilise(db, donnees.token)
    user.password_hash = hash_password(donnees.password)
    if user.email_verified_at is None:
        user.email_verified_at = models.maintenant()  # le lien prouve la possession de l'adresse
    db.commit()
    supprimer_toutes_les_sessions(db, user.id)
    limiteur(request, LIMITE_CONNEXION_EMAIL).reinitialiser(user.email)
    ouvrir_session(db, request, response, user, settings)
    journal.info("Mot de passe réinitialisé pour le membre %s", user.id)
    return utilisateur_public(db, user, settings)


@router.get("/me")
def moi(user: models.User = Depends(current_user), db: DbSession = Depends(get_db),
        settings: Settings = Depends(get_settings)) -> dict:
    return utilisateur_public(db, user, settings)
