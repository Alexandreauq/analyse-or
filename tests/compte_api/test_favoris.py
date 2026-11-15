# Tests des favoris (GET/PUT/DELETE /favorites).
from datetime import datetime, timedelta, timezone

import pytest

from compte_api import models
from compte_api.routers.favoris import FAVORIS_MAX


def _cles(reponse):
    return [(f["kind"], f["key"]) for f in reponse.json()]


# --- Cas nominaux ----------------------------------------------------------

def test_liste_vide_au_depart(client_membre):
    r = client_membre.get("/favorites")
    assert r.status_code == 200
    assert r.json() == []


def test_ajout_puis_liste(client_membre):
    r = client_membre.put("/favorites/company/AIR.PA")
    assert r.status_code == 200
    corps = r.json()
    assert corps["kind"] == "company" and corps["key"] == "AIR.PA"
    assert datetime.fromisoformat(corps["created_at"]).tzinfo is not None

    r = client_membre.get("/favorites")
    assert r.status_code == 200
    assert r.json() == [corps]


@pytest.mark.parametrize("kind,key", [
    ("index", "CAC40"), ("company", "^GSPC"), ("company", "GC=F"),
    ("company", "BRK-B"), ("company", "a"), ("company", "X" * 20),
])
def test_cles_acceptees(client_membre, kind, key):
    r = client_membre.put(f"/favorites/{kind}/{key}")
    assert r.status_code == 200, r.text
    assert r.json()["key"] == key
    assert _cles(client_membre.get("/favorites")) == [(kind, key)]


def test_cle_sensible_a_la_casse(client_membre):
    client_membre.put("/favorites/company/air.pa")
    client_membre.put("/favorites/company/AIR.PA")
    assert sorted(_cles(client_membre.get("/favorites"))) == [("company", "AIR.PA"), ("company", "air.pa")]


def test_meme_cle_deux_types(client_membre):
    client_membre.put("/favorites/index/DAX")
    client_membre.put("/favorites/company/DAX")
    assert len(client_membre.get("/favorites").json()) == 2


def test_suppression(client_membre):
    client_membre.put("/favorites/index/CAC40")
    client_membre.put("/favorites/company/MC.PA")
    r = client_membre.delete("/favorites/index/CAC40")
    assert r.status_code == 204
    assert r.content == b""
    assert _cles(client_membre.get("/favorites")) == [("company", "MC.PA")]


# --- Idempotence -----------------------------------------------------------

def test_ajout_idempotent(client_membre):
    premier = client_membre.put("/favorites/company/OR.PA").json()
    r = client_membre.put("/favorites/company/OR.PA")
    assert r.status_code == 200
    # Même objet, date d'origine conservée.
    assert r.json() == premier
    assert len(client_membre.get("/favorites").json()) == 1


def test_suppression_idempotente(client_membre):
    assert client_membre.delete("/favorites/company/INCONNU").status_code == 204
    client_membre.put("/favorites/company/OR.PA")
    assert client_membre.delete("/favorites/company/OR.PA").status_code == 204
    assert client_membre.delete("/favorites/company/OR.PA").status_code == 204
    assert client_membre.get("/favorites").json() == []


# --- Ordre -----------------------------------------------------------------

def test_ordre_du_plus_recent_au_plus_ancien(client_membre, db, membre):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, key in enumerate(["ANCIEN", "MILIEU", "RECENT"]):
        db.add(models.Favorite(user_id=membre.id, kind="company", key=key,
                               created_at=base + timedelta(days=i)))
    db.commit()
    assert [k for _, k in _cles(client_membre.get("/favorites"))] == ["RECENT", "MILIEU", "ANCIEN"]


def test_ordre_par_ajouts_successifs(client_membre):
    for key in ["A1", "B2", "C3"]:
        client_membre.put(f"/favorites/company/{key}")
    assert [k for _, k in _cles(client_membre.get("/favorites"))] == ["C3", "B2", "A1"]


# --- Limite ----------------------------------------------------------------

def _remplir(db, user, n):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.add_all(models.Favorite(user_id=user.id, kind="company", key=f"T{i}",
                               created_at=base + timedelta(seconds=i)) for i in range(n))
    db.commit()


def test_limite_des_favoris(client_membre, db, membre):
    _remplir(db, membre, FAVORIS_MAX - 1)
    assert client_membre.put("/favorites/index/CAC40").status_code == 200  # le 200e passe
    r = client_membre.put("/favorites/index/DAX")
    assert r.status_code == 409
    assert r.json() == {"detail": "Tu as atteint la limite de 200 favoris."}
    assert len(client_membre.get("/favorites").json()) == FAVORIS_MAX


def test_limite_ne_bloque_pas_un_favori_existant(client_membre, db, membre):
    _remplir(db, membre, FAVORIS_MAX)
    # Ré-ajouter un favori déjà présent reste idempotent, même à la limite.
    assert client_membre.put("/favorites/company/T0").status_code == 200
    # Après un retrait, on peut de nouveau en ajouter un.
    assert client_membre.delete("/favorites/company/T0").status_code == 204
    assert client_membre.put("/favorites/index/CAC40").status_code == 200


def test_limite_par_membre(client_membre, client_autre, db, membre):
    _remplir(db, membre, FAVORIS_MAX)
    assert client_autre.put("/favorites/index/CAC40").status_code == 200


# --- Sans session ----------------------------------------------------------

@pytest.mark.parametrize("methode,chemin", [
    ("get", "/favorites"),
    ("put", "/favorites/company/AIR.PA"),
    ("delete", "/favorites/company/AIR.PA"),
])
def test_sans_session_401(client, methode, chemin):
    r = getattr(client, methode)(chemin)
    assert r.status_code == 401
    assert r.json() == {"detail": "Connexion requise."}


def test_adresse_non_confirmee_acceptee(connecter, creer_membre):
    c = connecter(creer_membre(email="pas-confirme@exemple.test", verifie=False))
    assert c.put("/favorites/index/CAC40").status_code == 200
    assert _cles(c.get("/favorites")) == [("index", "CAC40")]


def test_ecriture_sans_en_tete_csrf_refusee(client_membre):
    r = client_membre.put("/favorites/index/CAC40", headers={"X-Requested-With": ""})
    assert r.status_code == 403
    assert client_membre.get("/favorites").json() == []


# --- Données invalides -----------------------------------------------------

@pytest.mark.parametrize("methode", ["put", "delete"])
@pytest.mark.parametrize("kind", ["etf", "Index", "COMPANY", "x"])
def test_kind_invalide_422(client_membre, methode, kind):
    r = getattr(client_membre, methode)(f"/favorites/{kind}/CAC40")
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)


@pytest.mark.parametrize("methode", ["put", "delete"])
@pytest.mark.parametrize("key", ["X" * 21, "AIR PA", "AIR_PA", "a$b", "é", "a*b", "a,b"])
def test_cle_invalide_422(client_membre, methode, key):
    r = getattr(client_membre, methode)(f"/favorites/company/{key}")
    assert r.status_code == 422, (key, r.status_code)
    assert isinstance(r.json()["detail"], str)
    assert client_membre.get("/favorites").json() == []


def test_cle_avec_barre_oblique_jamais_enregistree(client_membre):
    # Une barre oblique change le chemin : aucune route ne correspond (404),
    # rien n'est enregistré.
    assert client_membre.put("/favorites/company/a%2Fb").status_code in (404, 422)
    assert client_membre.get("/favorites").json() == []


# --- Isolation -------------------------------------------------------------

def test_isolation_lecture(client_membre, client_autre):
    client_membre.put("/favorites/company/AIR.PA")
    client_autre.put("/favorites/index/DAX")
    assert _cles(client_membre.get("/favorites")) == [("company", "AIR.PA")]
    assert _cles(client_autre.get("/favorites")) == [("index", "DAX")]


def test_isolation_suppression(client_membre, client_autre):
    client_membre.put("/favorites/company/AIR.PA")
    # Bob « supprime » le même favori : sans effet sur celui d'Alice.
    assert client_autre.delete("/favorites/company/AIR.PA").status_code == 204
    assert _cles(client_membre.get("/favorites")) == [("company", "AIR.PA")]


def test_isolation_meme_cible(client_membre, client_autre):
    client_membre.put("/favorites/company/AIR.PA")
    client_autre.put("/favorites/company/AIR.PA")
    client_autre.delete("/favorites/company/AIR.PA")
    assert _cles(client_membre.get("/favorites")) == [("company", "AIR.PA")]
    assert client_autre.get("/favorites").json() == []
