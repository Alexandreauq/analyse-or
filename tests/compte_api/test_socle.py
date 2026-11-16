# Tests du socle : garde du proxy, protection CSRF, sessions, sécurité,
# limiteur, e-mails, suppression en cascade.
from datetime import date, timedelta

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from compte_api import models
from compte_api.config import Settings
from compte_api.deps import current_admin, current_user, current_user_verified
from compte_api.ratelimit import Limiteur
from compte_api.security import hacher_jeton, hash_password, nouveau_jeton, verify_password
from compte_api.sessions import COOKIE_SESSION

from .conftest import EN_TETES, SECRET_PROXY


@pytest.fixture
def app_test(app):
    """Ajoute des routes de test qui exercent les dépendances du socle."""
    router = APIRouter()

    @router.get("/_test/moi")
    def moi(user=Depends(current_user)):
        return {"email": user.email}

    @router.get("/_test/verifie")
    def verifie(user=Depends(current_user_verified)):
        return {"ok": True}

    @router.get("/_test/admin")
    def admin(user=Depends(current_admin)):
        return {"ok": True}

    @router.post("/_test/ecrire")
    def ecrire():
        return {"ok": True}

    app.include_router(router)
    return app


def test_sante(client):
    assert client.get("/sante").json() == {"ok": True}


def test_refuse_sans_secret_du_proxy(app):
    brut = TestClient(app)
    assert brut.get("/sante").status_code == 403
    assert brut.get("/sante", headers={"X-Compte-Proxy": "faux"}).status_code == 403
    assert brut.get("/sante", headers={"X-Compte-Proxy": SECRET_PROXY}).status_code == 200


def test_mutation_exige_en_tete_csrf(app_test):
    brut = TestClient(app_test)
    assert brut.post("/_test/ecrire", headers={"X-Compte-Proxy": SECRET_PROXY}).status_code == 403
    assert brut.post("/_test/ecrire", headers=EN_TETES).status_code == 200


def test_current_user_sans_cookie(app_test, client):
    reponse = client.get("/_test/moi")
    assert reponse.status_code == 401
    assert reponse.json() == {"detail": "Connexion requise."}


def test_current_user_connecte(app_test, client_membre):
    assert client_membre.get("/_test/moi").json() == {"email": "alice@exemple.test"}


def test_session_expiree_refusee(app_test, client_membre, db):
    for session in db.query(models.Session).all():
        session.expires_at = models.maintenant() - timedelta(seconds=1)
    db.commit()
    assert client_membre.get("/_test/moi").status_code == 401


def test_jeton_inconnu_refuse(app_test, client):
    client.cookies.set(COOKIE_SESSION, "inconnu")
    assert client.get("/_test/moi").status_code == 401


def test_compte_supprime_refuse(app_test, client_membre, membre, db):
    db.get(models.User, membre.id).deleted_at = models.maintenant()
    db.commit()
    assert client_membre.get("/_test/moi").status_code == 401


def test_adresse_non_confirmee(app_test, connecter, creer_membre):
    client = connecter(creer_membre(verifie=False))
    assert client.get("/_test/moi").status_code == 200
    assert client.get("/_test/verifie").status_code == 403


def test_admin(app_test, connecter, creer_membre, client_membre):
    assert client_membre.get("/_test/admin").status_code == 403
    assert connecter(creer_membre(role="admin")).get("/_test/admin").status_code == 200


def test_validation_renvoie_un_message_en_francais(app, client):
    router = APIRouter()

    @router.post("/_test/nombre")
    def nombre(n: int):
        return {"n": n}

    app.include_router(router)
    reponse = client.post("/_test/nombre", params={"n": "abc"})
    assert reponse.status_code == 422
    assert reponse.json()["detail"] == "Données invalides."


def test_mots_de_passe():
    hache = hash_password("un-mot-de-passe")
    assert verify_password(hache, "un-mot-de-passe")
    assert not verify_password(hache, "autre")
    assert not verify_password(None, "x")
    assert not verify_password("pas-un-hash", "x")


def test_jetons_haches():
    clair, hache = nouveau_jeton()
    assert hacher_jeton(clair) == hache
    assert clair not in hache and len(hache) == 64


def test_limiteur():
    t = {"now": 0.0}
    limiteur = Limiteur(2, 60, horloge=lambda: t["now"])
    assert limiteur.autorise("ip") and limiteur.autorise("ip")
    assert not limiteur.autorise("ip")
    assert limiteur.autorise("autre")
    t["now"] = 61
    assert limiteur.autorise("ip")


def test_mailer_console(mailer):
    mailer.envoyer("a@exemple.test", "Sujet", "Texte")
    assert mailer.envoyes[0].sujet == "Sujet"


def test_suppression_en_cascade(db, creer_membre):
    user = creer_membre()
    db.add_all([
        models.Favorite(user_id=user.id, kind="company", key="AIR.PA"),
        models.Position(user_id=user.id, ticker="AIR.PA", quantity=1, buy_price=100, buy_date=date(2026, 1, 2)),
    ])
    db.commit()
    db.delete(user)
    db.commit()
    assert db.query(models.Favorite).count() == 0
    assert db.query(models.Position).count() == 0


def test_migration_identique_aux_modeles(settings, monkeypatch):
    """La migration Alembic doit créer exactement les tables de models.py."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from compte_api.db import Base, make_engine

    # `settings` pointe vers une base vide (SQLite temporaire ou PostgreSQL de test).
    url = settings.database_url
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[2] / "compte_api" / "alembic.ini"))
    command.upgrade(config, "head")
    engine = make_engine(url)
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    engine.dispose()
    assert tables == set(Base.metadata.tables)


def test_settings_depuis_environnement():
    s = Settings.from_env({"DATABASE_URL": "sqlite:///x.db", "COMPTE_COOKIE_SECURE": "true",
                           "AI_QUOTA_PAR_JOUR": "5", "COMPTE_SITE_URL": "https://site/"})
    assert s.cookie_secure is True and s.ai_quota_par_jour == 5 and s.site_url == "https://site"
    assert Settings.from_env({"AI_QUOTA_PAR_JOUR": "abc"}).ai_quota_par_jour == 10


# --- Revue sécurité (T9) ----------------------------------------------------------


@pytest.mark.parametrize("reglages, manque", [
    (dict(proxy_secret="dev-secret", cookie_secure=True, mailer="smtp"), "COMPTE_PROXY_SECRET"),
    (dict(proxy_secret="court", cookie_secure=True, mailer="smtp"), "COMPTE_PROXY_SECRET"),
    (dict(proxy_secret="x" * 40, cookie_secure=False, mailer="smtp"), "COMPTE_COOKIE_SECURE"),
    (dict(proxy_secret="x" * 40, cookie_secure=True, mailer="console"), "COMPTE_MAILER"),
])
def test_production_refuse_un_reglage_de_dev(reglages, manque):
    s = Settings(site_url="https://site.exemple.fr", **reglages)
    with pytest.raises(RuntimeError, match=manque):
        s.verifier_production()


def test_production_bien_reglee_et_local_acceptes():
    Settings(site_url="https://site.exemple.fr", proxy_secret="x" * 40, cookie_secure=True,
             mailer="smtp").verifier_production()
    Settings().verifier_production()  # développement local : tout est permis


def test_reponses_jamais_mises_en_cache(client):
    r = client.get("/sante")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_connexion_ferme_la_session_precedente_du_navigateur(client, db, creer_membre):
    from .conftest import MOT_DE_PASSE
    creer_membre(email="a@exemple.test")
    creer_membre(email="b@exemple.test")
    assert client.post("/login", json={"email": "a@exemple.test", "password": MOT_DE_PASSE}).status_code == 200
    ancien = client.cookies.get(COOKIE_SESSION)
    assert client.post("/login", json={"email": "b@exemple.test", "password": MOT_DE_PASSE}).status_code == 200
    db.expire_all()
    assert db.get(models.Session, hacher_jeton(ancien)) is None
    assert client.get("/me").json()["email"] == "b@exemple.test"
