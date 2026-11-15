# Fixtures partagées par tous les tests du service compte : application sur
# une base SQLite temporaire, e-mails gardés en mémoire, membres connectés.
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from compte_api import models
from compte_api.app import create_app
from compte_api.config import Settings
from compte_api.mailer import ConsoleMailer
from compte_api.security import hash_password
from compte_api.sessions import COOKIE_SESSION, creer_session

SECRET_PROXY = "secret-de-test"
MOT_DE_PASSE = "motdepasse-solide-42"
EN_TETES = {"X-Compte-Proxy": SECRET_PROXY, "X-Requested-With": "compte"}


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=f"sqlite:///{tmp_path / 'compte.db'}", proxy_secret=SECRET_PROXY,
                    site_url="http://site.test")


@pytest.fixture
def mailer():
    return ConsoleMailer(afficher=False)


@pytest.fixture
def app(settings, mailer):
    return create_app(settings, mailer=mailer, creer_tables=True)


@pytest.fixture
def db(app):
    session = app.state.sessionmaker()
    yield session
    session.close()


def _client(app) -> TestClient:
    client = TestClient(app)
    client.headers.update(EN_TETES)
    return client


@pytest.fixture
def client(app):
    """Client anonyme, avec les en-têtes du proxy et anti-CSRF."""
    return _client(app)


@pytest.fixture
def creer_membre(db):
    """Fabrique : creer_membre(email=..., verifie=True, role='member', mot_de_passe=...)."""
    compteur = {"n": 0}

    def fabrique(email=None, verifie=True, role="member", mot_de_passe=MOT_DE_PASSE, display_name=None):
        compteur["n"] += 1
        user = models.User(
            email=(email or f"membre{compteur['n']}@exemple.test").lower(),
            password_hash=hash_password(mot_de_passe) if mot_de_passe else None,
            email_verified_at=datetime.now(timezone.utc) if verifie else None,
            role=role, display_name=display_name,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    return fabrique


def _connecter(app, db, user) -> TestClient:
    client = _client(app)
    client.cookies.set(COOKIE_SESSION, creer_session(db, user.id, "pytest"))
    return client


@pytest.fixture
def membre(creer_membre):
    return creer_membre(email="alice@exemple.test", display_name="Alice")


@pytest.fixture
def client_membre(app, db, membre):
    """Client déjà connecté en tant que `membre` (adresse confirmée)."""
    return _connecter(app, db, membre)


@pytest.fixture
def autre_membre(creer_membre):
    return creer_membre(email="bob@exemple.test", display_name="Bob")


@pytest.fixture
def client_autre(app, db, autre_membre):
    """Second client connecté, pour vérifier qu'un membre ne voit jamais les données d'un autre."""
    return _connecter(app, db, autre_membre)


@pytest.fixture
def connecter(app, db):
    """connecter(user) -> client connecté en tant que cet utilisateur."""
    return lambda user: _connecter(app, db, user)
