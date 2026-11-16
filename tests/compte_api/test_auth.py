# Tests des comptes e-mail + mot de passe : inscription, confirmation,
# connexion, déconnexion, mot de passe oublié, réinitialisation, /me.
import re
from datetime import timedelta

import pytest

from compte_api import models
from compte_api.ratelimit import Limiteur
from compte_api.security import hacher_jeton, verify_password
from compte_api.sessions import COOKIE_SESSION

from .conftest import MOT_DE_PASSE, _client

NOUVEAU = "cheval-agrafe-pile-77"


def _jeton(mailer, sorte: str) -> str:
    """Jeton du dernier lien ?verify= ou ?reset= reçu par e-mail."""
    for email in reversed(mailer.envoyes):
        trouve = re.search(rf"http://site\.test/\?{sorte}=([\w-]+)", email.texte)
        if trouve:
            return trouve.group(1)
    raise AssertionError(f"aucun lien {sorte} envoyé")


def _inscrire(client, email="carole@exemple.test", password=NOUVEAU, **extra):
    return client.post("/register", json={"email": email, "password": password, **extra})


def _nb_users(db) -> int:
    db.expire_all()
    return db.query(models.User).count()


# ---------------------------------------------------------------- /register

def test_inscription_nominale(client, db, mailer):
    reponse = _inscrire(client, email="  Carole@Exemple.TEST ", display_name="  Carole ")
    assert reponse.status_code == 201
    corps = reponse.json()
    assert corps["email"] == "carole@exemple.test"
    assert corps["display_name"] == "Carole"
    assert corps["role"] == "member"
    assert corps["email_verified"] is False
    assert corps["quota_ia_restant"] == 10
    assert set(corps) == {"id", "email", "display_name", "role", "email_verified", "quota_ia_restant"}
    # Connecté tout de suite.
    assert COOKIE_SESSION in reponse.cookies
    assert client.get("/me").json()["email"] == "carole@exemple.test"
    # Mot de passe haché, jamais en clair.
    user = db.query(models.User).filter_by(email="carole@exemple.test").one()
    assert user.password_hash != NOUVEAU and verify_password(user.password_hash, NOUVEAU)
    assert user.last_login_at is not None
    # E-mail de confirmation.
    assert len(mailer.envoyes) == 1
    email = mailer.envoyes[0]
    assert email.destinataire == "carole@exemple.test"
    assert "http://site.test/?verify=" in email.texte and "Nomi" in email.texte
    # Jeton stocké haché seulement.
    jeton = _jeton(mailer, "verify")
    assert db.get(models.EmailToken, hacher_jeton(jeton)).purpose == "verify"


def test_inscription_email_deja_pris(client, membre, mailer):
    reponse = _inscrire(client, email="ALICE@exemple.test")
    assert reponse.status_code == 409
    assert reponse.json() == {"detail": "Un compte existe déjà avec cette adresse."}
    assert mailer.envoyes == []


@pytest.mark.parametrize("email", ["", "pas-une-adresse", "a@b", "a b@c.d", "x" * 250 + "@ex.fr"])
def test_inscription_email_invalide(client, db, email):
    reponse = _inscrire(client, email=email)
    assert reponse.status_code == 422
    assert reponse.json()["detail"] == "Adresse e-mail invalide."
    assert _nb_users(db) == 0


@pytest.mark.parametrize("password, message", [
    ("court-123", "au moins 10"),
    ("x" * 129, "dépasser 128"),
    ("motdepasse123", "trop courant"),
    ("AZERTYUIOP", "trop courant"),
    ("carole-2026-secret", "adresse e-mail"),
])
def test_inscription_mot_de_passe_refuse(client, db, password, message):
    reponse = _inscrire(client, password=password)
    assert reponse.status_code == 422
    assert message in reponse.json()["detail"]
    assert _nb_users(db) == 0


def test_inscription_champs_manquants(client):
    assert client.post("/register", json={"email": "x@y.fr"}).status_code == 422
    assert client.post("/register", json={}).json()["detail"] == "Données invalides."


def test_inscription_piege_anti_robot(client, db, mailer):
    reponse = _inscrire(client, site_web="http://spam.example")
    assert reponse.status_code == 201
    assert reponse.json()["email"] == "carole@exemple.test"
    assert COOKIE_SESSION not in reponse.cookies
    assert _nb_users(db) == 0
    assert mailer.envoyes == []


def test_inscription_limitee_par_ip(client):
    for i in range(5):
        assert _inscrire(client, email=f"robot{i}@exemple.test").status_code == 201
    reponse = _inscrire(client, email="robot9@exemple.test")
    assert reponse.status_code == 429
    assert reponse.json() == {"detail": "Trop de tentatives, réessaie dans quelques minutes."}
    # Une autre IP n'est pas concernée.
    assert client.post("/register", json={"email": "humain@exemple.test", "password": NOUVEAU},
                       headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 201


# ---------------------------------------------------------------- /verify

def test_confirmation_nominale_et_usage_unique(client, db, mailer):
    _inscrire(client)
    jeton = _jeton(mailer, "verify")
    reponse = client.post("/verify", json={"token": jeton})
    assert reponse.status_code == 200
    assert client.get("/me").json()["email_verified"] is True
    # Le même lien ne sert qu'une fois.
    deuxieme = client.post("/verify", json={"token": jeton})
    assert deuxieme.status_code == 400
    assert deuxieme.json() == {"detail": "Ce lien est invalide ou a expiré."}


def test_confirmation_sans_session(app, client, mailer):
    # Le lien peut être ouvert sur un autre appareil, sans être connecté.
    _inscrire(client)
    anonyme = _client(app)
    assert anonyme.post("/verify", json={"token": _jeton(mailer, "verify")}).status_code == 200


def test_confirmation_jeton_expire(client, db, mailer):
    _inscrire(client)
    jeton = _jeton(mailer, "verify")
    ligne = db.get(models.EmailToken, hacher_jeton(jeton))
    ligne.expires_at = models.maintenant() - timedelta(seconds=1)
    db.commit()
    assert client.post("/verify", json={"token": jeton}).status_code == 400
    assert client.get("/me").json()["email_verified"] is False


def test_confirmation_jeton_inconnu_ou_mauvais_type(client, membre, mailer):
    assert client.post("/verify", json={"token": "inconnu"}).status_code == 400
    assert client.post("/verify", json={}).status_code == 422
    # Un lien de réinitialisation ne confirme pas une adresse via /verify.
    client.post("/password/forgot", json={"email": membre.email})
    assert client.post("/verify", json={"token": _jeton(mailer, "reset")}).status_code == 400


# ---------------------------------------------------------------- /verify/resend

def test_renvoi_confirmation(connecter, creer_membre, mailer):
    user = creer_membre(email="dan@exemple.test", verifie=False)
    client = connecter(user)
    assert client.post("/verify/resend").status_code == 202
    premier = _jeton(mailer, "verify")
    assert client.post("/verify/resend").status_code == 202
    second = _jeton(mailer, "verify")
    assert mailer.envoyes[-1].destinataire == "dan@exemple.test"
    # Seul le dernier lien reste valable.
    assert client.post("/verify", json={"token": premier}).status_code == 400
    assert client.post("/verify", json={"token": second}).status_code == 200


def test_renvoi_confirmation_limite(connecter, creer_membre):
    client = connecter(creer_membre(verifie=False))
    for _ in range(3):
        assert client.post("/verify/resend").status_code == 202
    assert client.post("/verify/resend").status_code == 429


def test_renvoi_confirmation_sans_session(client):
    assert client.post("/verify/resend").status_code == 401


def test_renvoi_confirmation_deja_confirme(client_membre, mailer):
    assert client_membre.post("/verify/resend").status_code == 400
    assert mailer.envoyes == []


# ---------------------------------------------------------------- /login

def test_connexion_nominale(client, membre, db):
    reponse = client.post("/login", json={"email": " ALICE@exemple.test", "password": MOT_DE_PASSE})
    assert reponse.status_code == 200
    assert reponse.json()["email"] == "alice@exemple.test"
    assert reponse.json()["email_verified"] is True
    assert COOKIE_SESSION in reponse.cookies
    assert client.get("/me").json()["id"] == membre.id
    db.refresh(membre)
    assert membre.last_login_at is not None


@pytest.mark.parametrize("cas", ["mauvais", "inexistant", "google_seul", "supprime"])
def test_connexion_message_unique(client, membre, creer_membre, db, cas):
    email, password = "alice@exemple.test", MOT_DE_PASSE
    if cas == "mauvais":
        password = "pas-le-bon-mot-de-passe"
    elif cas == "inexistant":
        email = "personne@exemple.test"
    elif cas == "google_seul":
        email = creer_membre(email="g@exemple.test", mot_de_passe=None).email
    else:
        membre.deleted_at = models.maintenant()
        db.commit()
    reponse = client.post("/login", json={"email": email, "password": password})
    assert reponse.status_code == 401
    assert reponse.json() == {"detail": "E-mail ou mot de passe incorrect."}
    assert COOKIE_SESSION not in reponse.cookies


def test_connexion_donnees_invalides(client):
    assert client.post("/login", json={"email": "a@b.fr"}).status_code == 422
    assert client.post("/login", json={"email": "a@b.fr", "password": "x" * 2000}).status_code == 422


def test_connexion_bloquee_apres_5_echecs_par_email(client, membre):
    for _ in range(5):
        assert client.post("/login", json={"email": membre.email, "password": "faux-faux-faux"},
                           headers={"X-Forwarded-For": f"198.51.100.{_}"}).status_code == 401
    # Même le bon mot de passe est refusé tant que la fenêtre court.
    reponse = client.post("/login", json={"email": membre.email, "password": MOT_DE_PASSE},
                          headers={"X-Forwarded-For": "198.51.100.99"})
    assert reponse.status_code == 429
    assert reponse.json() == {"detail": "Trop de tentatives, réessaie dans quelques minutes."}


def test_connexion_bloquee_apres_5_echecs_par_ip(client, membre):
    for i in range(5):
        assert client.post("/login", json={"email": f"x{i}@exemple.test", "password": "faux-faux-faux"}
                           ).status_code == 401
    assert client.post("/login", json={"email": membre.email, "password": MOT_DE_PASSE}).status_code == 429


def test_connexion_fenetre_glissante(app, client, membre):
    # Horloge simulée : le blocage se lève au bout de 15 minutes.
    temps = {"t": 0.0}
    app.state.limiteur_connexion_email = Limiteur(5, 900, horloge=lambda: temps["t"])
    app.state.limiteur_connexion_ip = Limiteur(5, 900, horloge=lambda: temps["t"])
    for _ in range(5):
        client.post("/login", json={"email": membre.email, "password": "faux-faux-faux"})
    assert client.post("/login", json={"email": membre.email, "password": MOT_DE_PASSE}).status_code == 429
    temps["t"] = 901
    assert client.post("/login", json={"email": membre.email, "password": MOT_DE_PASSE}).status_code == 200


def test_connexion_rehache_si_necessaire(client, membre, db, monkeypatch):
    import compte_api.routers.auth as auth
    monkeypatch.setattr(auth, "password_needs_rehash", lambda _h: True)
    ancien = membre.password_hash
    assert client.post("/login", json={"email": membre.email, "password": MOT_DE_PASSE}).status_code == 200
    db.refresh(membre)
    assert membre.password_hash != ancien
    assert verify_password(membre.password_hash, MOT_DE_PASSE)


# ---------------------------------------------------------------- /logout

def test_deconnexion(client_membre, db):
    assert client_membre.get("/me").status_code == 200
    reponse = client_membre.post("/logout")
    assert reponse.status_code == 204
    assert db.query(models.Session).count() == 0
    # Cookie effacé côté navigateur (expiration immédiate).
    efface = reponse.headers["set-cookie"]
    assert efface.startswith(f"{COOKIE_SESSION}=") and "Max-Age=0" in efface
    assert client_membre.get("/me").status_code == 401


def test_deconnexion_ne_ferme_que_sa_session(client_membre, client_autre, db):
    client_membre.post("/logout")
    assert client_autre.get("/me").status_code == 200


def test_deconnexion_sans_session(client):
    assert client.post("/logout").status_code == 204


# ---------------------------------------------------------------- /password/forgot

def test_oubli_compte_existant(client, membre, mailer):
    reponse = client.post("/password/forgot", json={"email": " Alice@Exemple.test"})
    assert reponse.status_code == 202
    assert len(mailer.envoyes) == 1
    assert mailer.envoyes[0].destinataire == "alice@exemple.test"
    assert "http://site.test/?reset=" in mailer.envoyes[0].texte


def test_oubli_reponse_identique_si_compte_inconnu(client, membre, mailer):
    connu = client.post("/password/forgot", json={"email": membre.email})
    inconnu = client.post("/password/forgot", json={"email": "personne@exemple.test"})
    assert connu.status_code == inconnu.status_code == 202
    assert connu.json() == inconnu.json()
    assert len(mailer.envoyes) == 1


def test_oubli_compte_google_seul(client, creer_membre, mailer):
    creer_membre(email="g@exemple.test", mot_de_passe=None)
    client.post("/password/forgot", json={"email": "g@exemple.test"})
    assert mailer.envoyes[-1].destinataire == "g@exemple.test"


def test_oubli_donnees_invalides(client):
    assert client.post("/password/forgot", json={}).status_code == 422
    assert client.post("/password/forgot", json={"email": "nimporte"}).status_code == 422


def test_oubli_limite_par_email(client, membre, mailer):
    for _ in range(3):
        assert client.post("/password/forgot", json={"email": membre.email}).status_code == 202
    assert client.post("/password/forgot", json={"email": membre.email}).status_code == 429
    assert len(mailer.envoyes) == 3


# ---------------------------------------------------------------- /password/reset

def test_reinitialisation_nominale(app, client, membre, connecter, db, mailer):
    ailleurs = connecter(membre)  # une autre session ouverte du même membre
    client.post("/password/forgot", json={"email": membre.email})
    reponse = client.post("/password/reset", json={"token": _jeton(mailer, "reset"), "password": NOUVEAU})
    assert reponse.status_code == 200
    assert reponse.json()["email"] == membre.email
    assert COOKIE_SESSION in reponse.cookies
    # Les autres sessions sont fermées, la nouvelle fonctionne.
    assert ailleurs.get("/me").status_code == 401
    assert client.get("/me").status_code == 200
    assert db.query(models.Session).filter_by(user_id=membre.id).count() == 1
    # Ancien mot de passe refusé, nouveau accepté.
    anonyme = _client(app)
    assert anonyme.post("/login", json={"email": membre.email, "password": MOT_DE_PASSE}).status_code == 401
    assert anonyme.post("/login", json={"email": membre.email, "password": NOUVEAU}).status_code == 200


def test_reinitialisation_usage_unique(client, membre, mailer):
    client.post("/password/forgot", json={"email": membre.email})
    jeton = _jeton(mailer, "reset")
    assert client.post("/password/reset", json={"token": jeton, "password": NOUVEAU}).status_code == 200
    reponse = client.post("/password/reset", json={"token": jeton, "password": "autre-phrase-longue-9"})
    assert reponse.status_code == 400


def test_reinitialisation_jeton_expire(client, membre, db, mailer):
    client.post("/password/forgot", json={"email": membre.email})
    jeton = _jeton(mailer, "reset")
    db.get(models.EmailToken, hacher_jeton(jeton)).expires_at = models.maintenant() - timedelta(minutes=1)
    db.commit()
    assert client.post("/password/reset", json={"token": jeton, "password": NOUVEAU}).status_code == 400


def test_reinitialisation_jeton_de_confirmation_refuse(client, mailer):
    _inscrire(client)
    assert client.post("/password/reset", json={"token": _jeton(mailer, "verify"),
                                                "password": NOUVEAU}).status_code == 400


def test_reinitialisation_mot_de_passe_refuse_garde_le_lien(client, membre, mailer):
    client.post("/password/forgot", json={"email": membre.email})
    jeton = _jeton(mailer, "reset")
    assert client.post("/password/reset", json={"token": jeton, "password": "court"}).status_code == 422
    assert client.post("/password/reset", json={"token": jeton, "password": "password123"}).status_code == 422
    assert client.post("/password/reset", json={"token": jeton, "password": "alice-est-la-2026"}).status_code == 422
    assert client.post("/password/reset", json={"token": jeton}).status_code == 422
    assert client.post("/password/reset", json={"token": jeton, "password": NOUVEAU}).status_code == 200


def test_reinitialisation_confirme_l_adresse(client, creer_membre, mailer):
    user = creer_membre(email="eve@exemple.test", verifie=False)
    client.post("/password/forgot", json={"email": user.email})
    reponse = client.post("/password/reset", json={"token": _jeton(mailer, "reset"), "password": NOUVEAU})
    assert reponse.json()["email_verified"] is True


def test_reinitialisation_isolation(client, membre, autre_membre, client_autre, mailer):
    # Le lien d'Alice ne touche ni le mot de passe ni les sessions de Bob.
    client.post("/password/forgot", json={"email": membre.email})
    client.post("/password/reset", json={"token": _jeton(mailer, "reset"), "password": NOUVEAU})
    assert client_autre.get("/me").json()["email"] == "bob@exemple.test"
    assert client.post("/login", json={"email": autre_membre.email, "password": MOT_DE_PASSE}).status_code == 200


# ---------------------------------------------------------------- /me

def test_me_nominal(client_membre, membre):
    assert client_membre.get("/me").json() == {
        "id": membre.id, "email": "alice@exemple.test", "display_name": "Alice",
        "role": "member", "email_verified": True, "quota_ia_restant": 10,
    }


def test_me_sans_session(client):
    reponse = client.get("/me")
    assert reponse.status_code == 401
    assert reponse.json() == {"detail": "Connexion requise."}


def test_me_isolation(client_membre, client_autre, membre, autre_membre):
    assert client_membre.get("/me").json()["id"] == membre.id
    assert client_autre.get("/me").json()["id"] == autre_membre.id


def test_me_admin_quota_illimite(connecter, creer_membre):
    client = connecter(creer_membre(role="admin"))
    assert client.get("/me").json()["quota_ia_restant"] is None
