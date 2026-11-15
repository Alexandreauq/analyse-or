# Tests de « Continuer avec Google » avec un faux fournisseur injecté sur
# app.state.google_client : aucun appel réseau réel.
import base64
import dataclasses
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from compte_api import models
from compte_api.routers.google import COOKIE_GOOGLE, ErreurGoogle
from compte_api.security import verify_password
from compte_api.sessions import COOKIE_SESSION

from .conftest import MOT_DE_PASSE

ACCUEIL = "http://site.test/"
ECHEC = "http://site.test/?compte_erreur=google"
RETOUR = "http://site.test/api/compte/google/callback"


@pytest.fixture
def settings(settings):
    return dataclasses.replace(settings, google_client_id="id-client-test",
                               google_client_secret="secret-client-test")


class FauxGoogle:
    """Imite le serveur de Google : un seul code valable, une identité fixe."""

    def __init__(self):
        self.profil = {"sub": "g-123", "email": "Carole@Gmail.test", "email_verified": True,
                       "name": "Carole G"}
        self.echanges = []
        self.panne = False

    def echanger_code(self, code, code_verifier, redirect_uri):
        self.echanges.append((code, code_verifier, redirect_uri))
        if self.panne or code != "bon-code":
            raise ErreurGoogle("code refusé")
        return "jeton-acces"

    def identite(self, access_token):
        assert access_token == "jeton-acces"
        return dict(self.profil)


@pytest.fixture
def google(app):
    faux = FauxGoogle()
    app.state.google_client = faux
    return faux


def _demarrer(client) -> dict:
    reponse = client.get("/google/start", follow_redirects=False)
    assert reponse.status_code == 302
    return parse_qs(urlparse(reponse.headers["location"]).query)


def _retour(client, code="bon-code", state=None):
    params = _demarrer(client)
    return client.get("/google/callback", params={"code": code, "state": state or params["state"][0]},
                      follow_redirects=False)


# ---------------------------------------------------------------- /google/start

def test_start_redirige_vers_google_avec_pkce(client):
    reponse = client.get("/google/start", follow_redirects=False)
    assert reponse.status_code == 302
    url = urlparse(reponse.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert params["client_id"] == "id-client-test"
    assert params["redirect_uri"] == RETOUR
    assert params["response_type"] == "code"
    assert params["code_challenge_method"] == "S256"
    assert "openid" in params["scope"] and "email" in params["scope"]
    # Cookie court HttpOnly : state + code_verifier, défi = SHA-256 du verifier.
    entete = reponse.headers["set-cookie"]
    assert entete.startswith(f"{COOKIE_GOOGLE}=") and "HttpOnly" in entete and "Max-Age=600" in entete
    state, verifier = client.cookies[COOKIE_GOOGLE].strip('"').split(".")
    assert state == params["state"]
    defi = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert params["code_challenge"] == defi
    assert 43 <= len(verifier) <= 128


def test_start_state_different_a_chaque_fois(client):
    assert _demarrer(client)["state"] != _demarrer(client)["state"]


def test_start_indisponible_sans_identifiant(app, client):
    app.state.settings = dataclasses.replace(app.state.settings, google_client_id="")
    reponse = client.get("/google/start", follow_redirects=False)
    assert reponse.status_code == 503
    assert reponse.json() == {"detail": "Connexion Google indisponible."}


# ---------------------------------------------------------------- /google/callback

def test_creation_compte_google_seul(client, google, db):
    reponse = _retour(client)
    assert reponse.status_code == 302
    assert reponse.headers["location"] == ACCUEIL
    assert COOKIE_SESSION in reponse.cookies
    moi = client.get("/me").json()
    assert moi["email"] == "carole@gmail.test"
    assert moi["display_name"] == "Carole G"
    assert moi["email_verified"] is True
    user = db.query(models.User).filter_by(email="carole@gmail.test").one()
    assert user.google_sub == "g-123" and user.password_hash is None
    # Le code a été échangé avec le verifier du cookie et la bonne URL de retour.
    code, verifier, redirect_uri = google.echanges[-1]
    assert code == "bon-code" and redirect_uri == RETOUR and len(verifier) >= 43


def test_connexion_google_sub_connu(client, google, creer_membre, db):
    user = creer_membre(email="ancienne@exemple.test")
    user.google_sub = "g-123"
    db.commit()
    reponse = _retour(client)
    assert reponse.headers["location"] == ACCUEIL
    # L'adresse Google a changé, mais le compte est retrouvé par son sub.
    assert client.get("/me").json()["id"] == user.id
    assert db.query(models.User).count() == 1


def test_liaison_compte_existant_par_email_verifie(client, google, creer_membre, db):
    user = creer_membre(email="carole@gmail.test", verifie=False)
    reponse = _retour(client)
    assert reponse.headers["location"] == ACCUEIL
    assert client.get("/me").json()["id"] == user.id
    db.refresh(user)
    assert user.google_sub == "g-123"
    assert user.email_verified_at is not None  # Google garantit l'adresse
    assert verify_password(user.password_hash, MOT_DE_PASSE)  # mot de passe conservé
    assert db.query(models.User).count() == 1


def test_pas_de_liaison_si_email_non_verifie_chez_google(client, google, creer_membre, db):
    user = creer_membre(email="carole@gmail.test")
    google.profil["email_verified"] = False
    reponse = _retour(client)
    assert reponse.headers["location"] == ECHEC
    assert COOKIE_SESSION not in reponse.cookies
    db.refresh(user)
    assert user.google_sub is None
    assert client.get("/me").status_code == 401


def test_pas_de_liaison_sur_un_compte_deja_lie_ailleurs(client, google, creer_membre, db):
    user = creer_membre(email="carole@gmail.test")
    user.google_sub = "g-autre"
    db.commit()
    assert _retour(client).headers["location"] == ECHEC
    db.refresh(user)
    assert user.google_sub == "g-autre"


def test_compte_supprime_refuse(client, google, creer_membre, db):
    user = creer_membre(email="x@exemple.test")
    user.google_sub, user.deleted_at = "g-123", models.maintenant()
    db.commit()
    assert _retour(client).headers["location"] == ECHEC


def test_state_different_refuse(client, google):
    reponse = _retour(client, state="state-forge")
    assert reponse.status_code == 302
    assert reponse.headers["location"] == ECHEC
    assert COOKIE_SESSION not in reponse.cookies
    assert google.echanges == []  # aucun échange tenté


def test_sans_cookie_de_state_refuse(client, google):
    reponse = client.get("/google/callback", params={"code": "bon-code", "state": "abc"},
                         follow_redirects=False)
    assert reponse.headers["location"] == ECHEC
    assert google.echanges == []


def test_parametres_manquants_refuses(client, google):
    _demarrer(client)
    reponse = client.get("/google/callback", follow_redirects=False)
    assert reponse.headers["location"] == ECHEC


def test_state_a_usage_unique(client, google):
    params = _demarrer(client)
    premier = client.get("/google/callback", params={"code": "bon-code", "state": params["state"][0]},
                         follow_redirects=False)
    assert premier.headers["location"] == ACCUEIL
    # Le cookie de state est effacé : rejouer le même retour échoue.
    rejoue = client.get("/google/callback", params={"code": "bon-code", "state": params["state"][0]},
                        follow_redirects=False)
    assert rejoue.headers["location"] == ECHEC


def test_code_refuse_par_google(client, google, db):
    assert _retour(client, code="mauvais-code").headers["location"] == ECHEC
    assert db.query(models.User).count() == 0


def test_identite_incomplete(client, google, db):
    google.profil = {"sub": "", "email": "x@y.fr", "email_verified": True}
    assert _retour(client).headers["location"] == ECHEC
    google.profil = {"sub": "g-9", "email": "pas-une-adresse", "email_verified": True}
    assert _retour(client).headers["location"] == ECHEC
    assert db.query(models.User).count() == 0


def test_isolation_deux_comptes_google(app, google, db):
    from .conftest import _client
    a, b = _client(app), _client(app)
    _retour(a)
    google.profil = {"sub": "g-456", "email": "dan@gmail.test", "email_verified": True, "name": "Dan"}
    _retour(b)
    assert a.get("/me").json()["email"] == "carole@gmail.test"
    assert b.get("/me").json()["email"] == "dan@gmail.test"
