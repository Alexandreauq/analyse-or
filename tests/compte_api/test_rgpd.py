# Droits RGPD : nom affiché, export, suppression du compte.
import json
from datetime import date, timedelta

from compte_api import models

from .conftest import MOT_DE_PASSE


def _supprimer(client, mot_de_passe=None):
    corps = {} if mot_de_passe is None else {"password": mot_de_passe}
    return client.request("DELETE", "/account", json=corps)


def _remplir(db, user):
    db.add_all([
        models.Favorite(user_id=user.id, kind="company", key="AIR.PA"),
        models.Position(user_id=user.id, ticker="AIR.PA", quantity=2, buy_price=150, buy_date=date(2026, 10, 1)),
        models.Search(user_id=user.id, query="airbus"),
        models.AiExchange(user_id=user.id, question="Q ?", answer="R."),
        models.AiUsage(user_id=user.id, day=date(2026, 11, 15), count=3),
        models.UserSettings(user_id=user.id, json='{"theme": "dark"}'),
    ])
    regle = models.AlertRule(user_id=user.id, kind="score_above", target="AIR.PA", threshold=50)
    db.add(regle)
    db.commit()
    db.add(models.AlertEvent(rule_id=regle.id, value=55))
    db.commit()


def test_modifier_nom_affiche(client_membre):
    reponse = client_membre.patch("/me", json={"display_name": "  Alice M.  "})
    assert reponse.status_code == 200
    assert reponse.json()["display_name"] == "Alice M."
    assert client_membre.patch("/me", json={"display_name": ""}).json()["display_name"] is None
    assert client_membre.patch("/me", json={"display_name": "x" * 81}).status_code == 422


def test_profil_et_export_exigent_une_session(client):
    assert client.patch("/me", json={"display_name": "x"}).status_code == 401
    assert client.get("/export").status_code == 401
    assert _supprimer(client, MOT_DE_PASSE).status_code == 401


def test_export_complet_sans_secret(client_membre, membre, db):
    _remplir(db, membre)
    reponse = client_membre.get("/export")
    assert reponse.status_code == 200
    assert "attachment" in reponse.headers["content-disposition"]
    donnees = reponse.json()
    assert donnees["compte"]["email"] == "alice@exemple.test"
    assert donnees["favoris"][0]["key"] == "AIR.PA"
    assert donnees["positions"][0]["buy_date"] == "2026-10-01"
    assert donnees["recherches"][0]["query"] == "airbus"
    assert donnees["echanges_ia"][0]["answer"] == "R."
    assert donnees["regles_alerte"][0]["threshold"] == 50
    assert donnees["alertes_declenchees"][0]["value"] == 55
    assert donnees["reglages"] == {"theme": "dark"}
    brut = reponse.text
    assert membre.password_hash not in brut
    assert "id_hash" not in brut and "password_hash" not in brut


def test_export_isole_entre_membres(client_membre, membre, autre_membre, db):
    _remplir(db, autre_membre)
    donnees = client_membre.get("/export").json()
    assert donnees["favoris"] == [] and donnees["positions"] == [] and donnees["regles_alerte"] == []


def test_suppression_avec_mot_de_passe(client_membre, membre, autre_membre, db, mailer):
    _remplir(db, membre)
    _remplir(db, autre_membre)
    id_membre, id_autre = membre.id, autre_membre.id
    reponse = _supprimer(client_membre, MOT_DE_PASSE)
    assert reponse.status_code == 204
    assert "compte_session" in reponse.headers.get("set-cookie", "")
    db.expunge_all()
    assert db.get(models.User, id_membre) is None
    for modele in (models.Favorite, models.Position, models.Search, models.AiExchange, models.AiUsage,
                   models.UserSettings, models.AlertRule, models.Session):
        assert db.query(modele).filter_by(user_id=id_membre).count() == 0
    assert db.query(models.AlertEvent).count() == 1  # celui de l'autre membre
    assert db.get(models.User, id_autre) is not None
    assert mailer.envoyes[-1].destinataire == "alice@exemple.test"
    assert client_membre.get("/export").status_code == 401


def test_suppression_refusee_mauvais_mot_de_passe(client_membre, membre, db):
    assert _supprimer(client_membre, "mauvais-mot-de-passe").status_code == 403
    assert _supprimer(client_membre).status_code == 403
    db.expire_all()
    assert db.get(models.User, membre.id) is not None


def test_suppression_limitee(client_membre):
    for _ in range(5):
        _supprimer(client_membre, "mauvais-mot-de-passe")
    assert _supprimer(client_membre, MOT_DE_PASSE).status_code == 429


def test_compte_google_seul_session_recente(connecter, creer_membre, db):
    user = creer_membre(mot_de_passe=None)
    client = connecter(user)
    assert _supprimer(client).status_code == 204


def test_compte_google_seul_session_ancienne(connecter, creer_membre, db):
    user = creer_membre(mot_de_passe=None)
    client = connecter(user)
    for session in db.query(models.Session).filter_by(user_id=user.id):
        session.created_at = models.maintenant() - timedelta(minutes=20)
    db.commit()
    reponse = _supprimer(client)
    assert reponse.status_code == 403
    assert "Google" in reponse.json()["detail"]
