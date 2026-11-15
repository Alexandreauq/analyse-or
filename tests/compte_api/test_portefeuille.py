# Tests du portefeuille sur le compte (/positions) et de l'import des
# données du navigateur (/import).
from datetime import datetime, timedelta, timezone

import pytest

from compte_api import models
from compte_api.routers import portefeuille

AUJOURDHUI = datetime.now(timezone.utc).date()
DEMAIN = (AUJOURDHUI + timedelta(days=1)).isoformat()
ACHAT = {"ticker": "MC.PA", "quantity": 10, "buy_price": 650.5, "buy_date": "2026-01-15"}


@pytest.fixture
def ajouter(client_membre):
    def fabrique(**champs):
        r = client_membre.post("/positions", json={**ACHAT, **champs})
        assert r.status_code == 201, r.text
        return r.json()
    return fabrique


# --- GET /positions -------------------------------------------------------

def test_lister_vide(client_membre):
    r = client_membre.get("/positions")
    assert r.status_code == 200
    assert r.json() == []


def test_lister_par_statut(client_membre, ajouter):
    ouverte = ajouter()
    fermee = ajouter(ticker="AIR.PA")
    client_membre.patch(f"/positions/{fermee['id']}/close", json={"sell_price": 160, "sell_date": "2026-02-01"})
    toutes = client_membre.get("/positions").json()
    assert [p["id"] for p in toutes] == [ouverte["id"], fermee["id"]]
    assert [p["id"] for p in client_membre.get("/positions?statut=ouvertes").json()] == [ouverte["id"]]
    clos = client_membre.get("/positions?statut=cloturees").json()
    assert [p["id"] for p in clos] == [fermee["id"]]
    assert clos[0]["sell_date"] == "2026-02-01" and clos[0]["sell_price"] == 160


def test_lister_statut_inconnu(client_membre):
    assert client_membre.get("/positions?statut=vendues").status_code == 422


def test_lister_sans_session(client):
    assert client.get("/positions").status_code == 401


def test_lister_isolation(client_membre, client_autre, ajouter):
    ajouter()
    assert client_autre.get("/positions").json() == []


# --- POST /positions ------------------------------------------------------

def test_ajouter(client_membre):
    r = client_membre.post("/positions", json=ACHAT)
    assert r.status_code == 201
    p = r.json()
    assert {k: p[k] for k in ACHAT} == ACHAT
    assert p["sell_price"] is None and p["sell_date"] is None
    assert isinstance(p["id"], int) and p["created_at"]


def test_ajouter_aujourdhui_accepte(client_membre):
    r = client_membre.post("/positions", json={**ACHAT, "buy_date": AUJOURDHUI.isoformat()})
    assert r.status_code == 201


def test_ajouter_sans_session(client):
    assert client.post("/positions", json=ACHAT).status_code == 401


@pytest.mark.parametrize("champs, message", [
    ({"quantity": 0}, "quantité"),
    ({"quantity": -3}, "quantité"),
    ({"quantity": "10"}, "quantité"),
    ({"quantity": True}, "quantité"),
    ({"buy_price": 0}, "prix d'achat"),
    ({"buy_price": None}, "prix d'achat"),
    ({"buy_date": "15/01/2026"}, "AAAA-MM-JJ"),
    ({"buy_date": "2026-02-30"}, "n'existe pas"),
    ({"buy_date": DEMAIN}, "futur"),
    ({"ticker": ""}, "Ticker"),
    ({"ticker": "MC PA<script>"}, "Ticker"),
])
def test_ajouter_invalide(client_membre, champs, message):
    r = client_membre.post("/positions", json={**ACHAT, **champs})
    assert r.status_code == 422
    assert message in r.json()["detail"]
    assert client_membre.get("/positions").json() == []


def test_ajouter_corps_non_objet(client_membre):
    assert client_membre.post("/positions", json=[ACHAT]).status_code == 422


def test_ajouter_limite(client_membre, db, membre, monkeypatch):
    monkeypatch.setattr(portefeuille, "POSITIONS_MAX", 2)
    client_membre.post("/positions", json=ACHAT)
    client_membre.post("/positions", json=ACHAT)
    r = client_membre.post("/positions", json=ACHAT)
    assert r.status_code == 409


def test_limite_par_defaut():
    assert portefeuille.POSITIONS_MAX == 500


# --- PATCH /positions/{id} ------------------------------------------------

def test_modifier(client_membre, ajouter):
    p = ajouter()
    r = client_membre.patch(f"/positions/{p['id']}", json={"quantity": 12.5, "buy_date": "2026-01-10"})
    assert r.status_code == 200
    assert r.json()["quantity"] == 12.5 and r.json()["buy_date"] == "2026-01-10"
    assert r.json()["buy_price"] == ACHAT["buy_price"]


def test_modifier_invalide(client_membre, ajouter):
    p = ajouter()
    for corps in ({"quantity": -1}, {"buy_price": "abc"}, {"buy_date": DEMAIN}, {},
                  {"user_id": 99}, {"sell_price": 10}):
        assert client_membre.patch(f"/positions/{p['id']}", json=corps).status_code == 422, corps


def test_modifier_cloturee_garde_coherence(client_membre, ajouter):
    p = ajouter()
    client_membre.patch(f"/positions/{p['id']}/close", json={"sell_price": 700, "sell_date": "2026-03-01"})
    r = client_membre.patch(f"/positions/{p['id']}", json={"buy_date": "2026-04-01"})
    assert r.status_code == 422
    r = client_membre.patch(f"/positions/{p['id']}", json={"sell_price": 710})
    assert r.status_code == 200 and r.json()["sell_price"] == 710


def test_modifier_sans_session(client, ajouter):
    p = ajouter()
    assert client.patch(f"/positions/{p['id']}", json={"quantity": 1}).status_code == 401


def test_modifier_isolation(client_autre, ajouter):
    p = ajouter()
    r = client_autre.patch(f"/positions/{p['id']}", json={"quantity": 1})
    assert r.status_code == 404


def test_modifier_inexistante(client_membre):
    assert client_membre.patch("/positions/9999", json={"quantity": 1}).status_code == 404


# --- PATCH /positions/{id}/close ------------------------------------------

def test_cloturer(client_membre, ajouter):
    p = ajouter()
    r = client_membre.patch(f"/positions/{p['id']}/close", json={"sell_price": 700, "sell_date": "2026-03-01"})
    assert r.status_code == 200
    assert r.json()["sell_price"] == 700 and r.json()["sell_date"] == "2026-03-01"


def test_cloturer_le_jour_de_l_achat(client_membre, ajouter):
    p = ajouter()
    r = client_membre.patch(f"/positions/{p['id']}/close", json={"sell_price": 1, "sell_date": ACHAT["buy_date"]})
    assert r.status_code == 200


def test_cloturer_deja_cloturee(client_membre, ajouter):
    p = ajouter()
    vente = {"sell_price": 700, "sell_date": "2026-03-01"}
    client_membre.patch(f"/positions/{p['id']}/close", json=vente)
    assert client_membre.patch(f"/positions/{p['id']}/close", json=vente).status_code == 409


@pytest.mark.parametrize("vente", [
    {"sell_price": 0, "sell_date": "2026-03-01"},
    {"sell_price": 700},
    {"sell_price": 700, "sell_date": "2026-01-14"},
    {"sell_price": 700, "sell_date": DEMAIN},
])
def test_cloturer_invalide(client_membre, ajouter, vente):
    p = ajouter()
    assert client_membre.patch(f"/positions/{p['id']}/close", json=vente).status_code == 422
    assert client_membre.get("/positions?statut=ouvertes").json()[0]["id"] == p["id"]


def test_cloturer_sans_session(client, ajouter):
    p = ajouter()
    r = client.patch(f"/positions/{p['id']}/close", json={"sell_price": 700, "sell_date": "2026-03-01"})
    assert r.status_code == 401


def test_cloturer_isolation(client_autre, ajouter):
    p = ajouter()
    r = client_autre.patch(f"/positions/{p['id']}/close", json={"sell_price": 700, "sell_date": "2026-03-01"})
    assert r.status_code == 404


# --- DELETE /positions/{id} -----------------------------------------------

def test_supprimer(client_membre, ajouter):
    p = ajouter()
    r = client_membre.delete(f"/positions/{p['id']}")
    assert r.status_code == 204
    assert client_membre.get("/positions").json() == []
    assert client_membre.delete(f"/positions/{p['id']}").status_code == 404


def test_supprimer_sans_session(client, ajouter):
    p = ajouter()
    assert client.delete(f"/positions/{p['id']}").status_code == 401


def test_supprimer_id_invalide(client_membre):
    assert client_membre.delete("/positions/abc").status_code == 422


def test_supprimer_isolation(client_membre, client_autre, ajouter):
    p = ajouter()
    assert client_autre.delete(f"/positions/{p['id']}").status_code == 404
    assert len(client_membre.get("/positions").json()) == 1


# --- POST /import ---------------------------------------------------------

def _local(**champs):
    # Position telle que stockée par docs/portfolio.js (id local pos-...).
    return {"id": "pos-1700000000000-abc123", **ACHAT, **champs}


def test_import_nominal(client_membre):
    corps = {
        "positions": [_local(), _local(ticker="AIR.PA", quantity=3)],
        "positions_cloturees": [_local(ticker="OR.PA", sell_price=400, sell_date="2026-02-01")],
        "historique_ia": [{"date": "2026-11-01T10:00:00.000Z", "question": "Et l'or ?", "reponse": "Haussier."}],
        "reglages": {"theme": "light", "niveau_reponse": "technique"},
    }
    r = client_membre.post("/import", json=corps)
    assert r.status_code == 200
    assert r.json() == {
        "importees": {"positions": 2, "positions_cloturees": 1, "historique_ia": 1, "reglages": 2},
        "ignorees": {"positions": 0, "positions_cloturees": 0, "historique_ia": 0, "reglages": 0},
    }
    toutes = client_membre.get("/positions").json()
    assert len(toutes) == 3
    clos = client_membre.get("/positions?statut=cloturees").json()
    assert clos[0]["ticker"] == "OR.PA" and clos[0]["sell_price"] == 400
    assert client_membre.get("/settings").json() == {"theme": "light", "niveau_reponse": "technique"}


def test_import_historique_en_base(client_membre, db, membre):
    client_membre.post("/import", json={"historique_ia": [
        {"date": "2026-11-01T10:00:00.000Z", "question": "Q", "reponse": "R"}]})
    echanges = db.query(models.AiExchange).filter_by(user_id=membre.id).all()
    assert len(echanges) == 1
    assert echanges[0].answer == "R"
    assert echanges[0].created_at.replace(tzinfo=None) == datetime(2026, 11, 1, 10, 0)


def test_import_doublons_positions(client_membre, ajouter):
    ajouter()  # déjà en base : MC.PA, 10, 2026-01-15
    corps = {
        "positions": [_local(), _local(ticker="AIR.PA"), _local(ticker="AIR.PA", buy_price=1)],
        "positions_cloturees": [_local(ticker="AIR.PA", sell_price=2, sell_date="2026-02-01"),
                                _local(quantity=11, sell_price=2, sell_date="2026-02-01")],
    }
    r = client_membre.post("/import", json=corps).json()
    # MC.PA déjà en base, AIR.PA en double dans le lot (même prix ou non, et
    # même entre ouvertes et clôturées).
    assert r["importees"]["positions"] == 1 and r["ignorees"]["positions"] == 2
    assert r["importees"]["positions_cloturees"] == 1 and r["ignorees"]["positions_cloturees"] == 1
    assert len(client_membre.get("/positions").json()) == 3


def test_import_deux_fois_ne_duplique_pas(client_membre):
    corps = {"positions": [_local()], "historique_ia": [{"date": "2026-11-01T10:00:00Z", "question": "Q", "reponse": "R"}]}
    client_membre.post("/import", json=corps)
    r = client_membre.post("/import", json=corps).json()
    assert r["importees"]["positions"] == 0 and r["ignorees"]["positions"] == 1
    assert r["importees"]["historique_ia"] == 0 and r["ignorees"]["historique_ia"] == 1


def test_import_entrees_invalides_ignorees(client_membre):
    corps = {
        "positions": [_local(), "texte", None, _local(quantity=0), _local(buy_date=DEMAIN),
                      _local(ticker="<b>"), _local(buy_price="12")],
        "positions_cloturees": [_local(ticker="X1", sell_price=5),  # sans date de vente
                                _local(ticker="X2", sell_price=5, sell_date="2025-01-01")],  # avant l'achat
        "historique_ia": [{"date": "pas une date", "question": "Q", "reponse": "R"},
                          {"date": "2026-11-01T10:00:00Z", "question": "", "reponse": "R"},
                          {"date": "2026-11-01T10:00:00Z", "question": "Q"},
                          {"date": "2099-01-01T00:00:00Z", "question": "Q", "reponse": "R"},
                          42],
        "reglages": {"theme": "violet", "inconnue": 1, "niveau_reponse": "grand_public"},
    }
    r = client_membre.post("/import", json=corps)
    assert r.status_code == 200
    assert r.json() == {
        "importees": {"positions": 1, "positions_cloturees": 0, "historique_ia": 0, "reglages": 1},
        "ignorees": {"positions": 6, "positions_cloturees": 2, "historique_ia": 5, "reglages": 2},
    }


def test_import_champs_absents_ou_mauvais_type(client_membre):
    r = client_membre.post("/import", json={"positions": "x", "reglages": [1]})
    assert r.status_code == 200
    assert sum(r.json()["importees"].values()) == 0
    assert client_membre.post("/import", json=[1, 2]).status_code == 422


def test_import_fusion_reglages(client_membre):
    client_membre.put("/settings", json={"theme": "dark", "filtres_sauvegardes": [{"name": "A", "state": {}}]})
    client_membre.post("/import", json={"reglages": {"theme": "light", "niveau_reponse": "grand_public"}})
    assert client_membre.get("/settings").json() == {
        "theme": "light", "filtres_sauvegardes": [{"name": "A", "state": {}}], "niveau_reponse": "grand_public"}


def test_import_reglages_trop_gros_ignores(client_membre):
    gros = [{"name": f"F{i}", "state": {"search": "x" * 400}} for i in range(45)]
    r = client_membre.post("/import", json={"reglages": {"theme": "light", "filtres_sauvegardes": gros}}).json()
    assert r["importees"]["reglages"] == 1 and r["ignorees"]["reglages"] == 1
    assert client_membre.get("/settings").json() == {"theme": "light"}


def test_import_limite_historique(client_membre, db, membre):
    debut = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # 90 échanges déjà en base (1er janvier -> 31 mars).
    for i in range(90):
        db.add(models.AiExchange(user_id=membre.id, question=f"ancien {i}", answer="r",
                                 created_at=debut + timedelta(days=i)))
    db.commit()
    # 20 importés : 15 plus récents que tout, 5 plus anciens que tout.
    lot = [{"date": (debut + timedelta(days=200 + i)).isoformat(), "question": f"recent {i}", "reponse": "r"}
           for i in range(15)]
    lot += [{"date": (debut - timedelta(days=1 + i)).isoformat(), "question": f"vieux {i}", "reponse": "r"}
            for i in range(5)]
    r = client_membre.post("/import", json={"historique_ia": lot}).json()
    assert r["importees"]["historique_ia"] == 15 and r["ignorees"]["historique_ia"] == 5
    db.expire_all()
    questions = [e.question for e in db.query(models.AiExchange).filter_by(user_id=membre.id)]
    assert len(questions) == 100
    assert sum(q.startswith("recent") for q in questions) == 15
    assert not any(q.startswith("vieux") for q in questions)
    # Les 5 plus anciens échanges en base sont sortis du lot des 100.
    assert not any(q in questions for q in (f"ancien {i}" for i in range(5)))


def test_import_doublon_historique_dans_le_lot(client_membre):
    e = {"date": "2026-11-01T10:00:00.000Z", "question": "Q", "reponse": "R"}
    r = client_membre.post("/import", json={"historique_ia": [e, dict(e, reponse="autre"),
                                                              dict(e, date="2026-11-01T11:00:00Z")]}).json()
    assert r["importees"]["historique_ia"] == 2 and r["ignorees"]["historique_ia"] == 1


def test_import_respecte_limite_positions(client_membre, monkeypatch):
    monkeypatch.setattr(portefeuille, "POSITIONS_MAX", 2)
    lot = [_local(ticker=f"T{i}") for i in range(4)]
    r = client_membre.post("/import", json={"positions": lot}).json()
    assert r["importees"]["positions"] == 2 and r["ignorees"]["positions"] == 2


def test_import_sans_session(client):
    assert client.post("/import", json={"positions": [ACHAT]}).status_code == 401


def test_import_isolation(client_membre, client_autre, autre_membre, db):
    client_autre.post("/positions", json=ACHAT)
    db.add(models.AiExchange(user_id=autre_membre.id, question="Q", answer="R",
                             created_at=datetime(2026, 11, 1, 10, tzinfo=timezone.utc)))
    db.commit()
    corps = {"positions": [_local()],
             "historique_ia": [{"date": "2026-11-01T10:00:00Z", "question": "Q", "reponse": "R"}],
             "reglages": {"theme": "light"}}
    r = client_membre.post("/import", json=corps).json()
    # Les données de l'autre membre ne comptent pas comme doublons...
    assert r["importees"] == {"positions": 1, "positions_cloturees": 0, "historique_ia": 1, "reglages": 1}
    # ... et ne sont pas touchées.
    assert len(client_autre.get("/positions").json()) == 1
    assert client_autre.get("/settings").json() == {}
    assert db.query(models.AiExchange).filter_by(user_id=autre_membre.id).count() == 1
