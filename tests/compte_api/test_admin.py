# Tests du dashboard d'administration (GET /admin/stats, GET /admin/users).
# Spec : docs/superpowers/specs/2026-11-16-admin-dashboard-design.md
from datetime import timedelta

from compte_api import models
from compte_api.sessions import creer_session


def _marquer_last_seen(db, user, il_y_a):
    """Crée (si besoin) une session pour `user` et fixe last_seen_at à il y a `il_y_a`."""
    creer_session(db, user.id, "pytest")
    for session in db.query(models.Session).filter_by(user_id=user.id):
        session.last_seen_at = models.maintenant() - il_y_a
    db.commit()


# --- Accès --------------------------------------------------------------

def test_stats_sans_session_401(client):
    r = client.get("/admin/stats")
    assert r.status_code == 401


def test_stats_non_admin_403(client_membre):
    r = client_membre.get("/admin/stats")
    assert r.status_code == 403


# --- /admin/stats ---------------------------------------------------------

def test_stats_avec_seulement_l_admin(creer_membre, connecter):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    r = client_admin.get("/admin/stats")
    assert r.status_code == 200
    # L'admin vient de se connecter : sa propre session compte comme "connecté".
    assert r.json() == {"total_membres": 1, "verifies": 1, "connectes_maintenant": 1}


def test_stats_plusieurs_membres(creer_membre, connecter):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    creer_membre(verifie=True)
    creer_membre(verifie=False)
    r = client_admin.get("/admin/stats")
    assert r.json()["total_membres"] == 3
    assert r.json()["verifies"] == 2
    assert r.json()["connectes_maintenant"] == 1  # les deux autres n'ont pas de session


def test_stats_membre_supprime_jamais_compte(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    supprime = creer_membre()
    supprime.deleted_at = models.maintenant()
    db.commit()
    _marquer_last_seen(db, supprime, timedelta(minutes=1))  # même connecté, ne doit pas compter
    r = client_admin.get("/admin/stats")
    assert r.json() == {"total_membres": 1, "verifies": 1, "connectes_maintenant": 1}


def test_stats_connecte_maintenant_a_10_minutes(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    membre = creer_membre()
    _marquer_last_seen(db, membre, timedelta(minutes=10))
    r = client_admin.get("/admin/stats")
    assert r.json()["connectes_maintenant"] == 2


def test_stats_pas_connecte_a_20_minutes(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    membre = creer_membre()
    _marquer_last_seen(db, membre, timedelta(minutes=20))
    r = client_admin.get("/admin/stats")
    assert r.json()["connectes_maintenant"] == 1
