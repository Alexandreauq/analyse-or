# Tests des préférences du membre (GET/PUT /settings).
import json

from compte_api import models


REGLAGES = {
    "theme": "light",
    "niveau_reponse": "grand_public",
    "filtres_sauvegardes": [{"name": "Rendement", "state": {"scoreMin": 20, "sector": "Énergie"}}],
}


def test_settings_vide_par_defaut(client_membre):
    r = client_membre.get("/settings")
    assert r.status_code == 200
    assert r.json() == {}


def test_settings_put_puis_get(client_membre, db, membre):
    r = client_membre.put("/settings", json=REGLAGES)
    assert r.status_code == 200
    assert r.json() == REGLAGES
    assert client_membre.get("/settings").json() == REGLAGES
    ligne = db.get(models.UserSettings, membre.id)
    assert json.loads(ligne.json) == REGLAGES


def test_settings_put_remplace_tout(client_membre):
    client_membre.put("/settings", json=REGLAGES)
    client_membre.put("/settings", json={"theme": "dark"})
    assert client_membre.get("/settings").json() == {"theme": "dark"}


def test_settings_sans_session(client):
    assert client.get("/settings").status_code == 401
    assert client.put("/settings", json={"theme": "dark"}).status_code == 401


def test_settings_cle_inconnue(client_membre):
    r = client_membre.put("/settings", json={"theme": "dark", "couleur": "rose"})
    assert r.status_code == 422
    assert "inconnu" in r.json()["detail"]


def test_settings_valeurs_invalides(client_membre):
    for corps in ({"theme": "violet"}, {"niveau_reponse": "expert"},
                  {"filtres_sauvegardes": "pas une liste"},
                  {"filtres_sauvegardes": [{"state": {}}]},
                  {"filtres_sauvegardes": [{"name": "x", "state": "y"}]},
                  ["theme", "dark"]):
        r = client_membre.put("/settings", json=corps)
        assert r.status_code == 422, corps
    assert client_membre.get("/settings").json() == {}


def test_settings_trop_volumineux(client_membre):
    gros = [{"name": f"Filtre {i}", "state": {"search": "x" * 400}} for i in range(45)]
    r = client_membre.put("/settings", json={"filtres_sauvegardes": gros})
    assert r.status_code == 422
    assert "16 Ko" in r.json()["detail"]


def test_settings_sans_en_tete_csrf(client_membre):
    r = client_membre.put("/settings", json={"theme": "dark"}, headers={"X-Requested-With": ""})
    assert r.status_code == 403


def test_settings_isolation(client_membre, client_autre):
    client_membre.put("/settings", json=REGLAGES)
    assert client_autre.get("/settings").json() == {}
    client_autre.put("/settings", json={"theme": "dark"})
    assert client_membre.get("/settings").json() == REGLAGES
