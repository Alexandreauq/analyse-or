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


# --- Accès (route /admin/users) -------------------------------------------

def test_users_sans_session_401(client):
    r = client.get("/admin/users")
    assert r.status_code == 401


def test_users_non_admin_403(client_membre):
    r = client_membre.get("/admin/users")
    assert r.status_code == 403


# --- /admin/users -----------------------------------------------------------

def test_users_champs_et_role(creer_membre, connecter):
    admin = creer_membre(role="admin", email="admin@exemple.test", display_name="Admin")
    client_admin = connecter(admin)
    r = client_admin.get("/admin/users")
    assert r.status_code == 200
    corps = r.json()
    assert len(corps) == 1
    ligne = corps[0]
    assert ligne["email"] == "admin@exemple.test"
    assert ligne["display_name"] == "Admin"
    assert ligne["role"] == "admin"
    assert ligne["email_verified"] is True
    assert ligne["connecte"] is True  # l'admin vient de se connecter
    assert ligne["last_login_at"] is None  # creer_membre ne pose pas last_login_at
    from datetime import datetime
    assert datetime.fromisoformat(ligne["created_at"]).tzinfo is not None


def test_users_tri_par_date_decroissante(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    plus_vieux = creer_membre(email="vieux@exemple.test")
    plus_vieux.created_at = models.maintenant() - timedelta(days=2)
    plus_recent = creer_membre(email="recent@exemple.test")
    plus_recent.created_at = models.maintenant() - timedelta(hours=1)
    db.commit()
    r = client_admin.get("/admin/users")
    emails = [m["email"] for m in r.json()]
    # admin.created_at n'a pas été modifié : il reste "maintenant", donc le
    # plus récent des trois.
    assert emails == [admin.email, "recent@exemple.test", "vieux@exemple.test"]


def test_users_membre_supprime_absent(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    supprime = creer_membre(email="supprime@exemple.test")
    supprime.deleted_at = models.maintenant()
    db.commit()
    r = client_admin.get("/admin/users")
    assert [m["email"] for m in r.json()] == [admin.email]


def test_users_connecte_a_10_minutes(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    membre = creer_membre(email="membre@exemple.test")
    _marquer_last_seen(db, membre, timedelta(minutes=10))
    r = client_admin.get("/admin/users")
    ligne = next(m for m in r.json() if m["email"] == "membre@exemple.test")
    assert ligne["connecte"] is True


def test_users_pas_connecte_a_20_minutes(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    membre = creer_membre(email="membre@exemple.test")
    _marquer_last_seen(db, membre, timedelta(minutes=20))
    r = client_admin.get("/admin/users")
    ligne = next(m for m in r.json() if m["email"] == "membre@exemple.test")
    assert ligne["connecte"] is False
