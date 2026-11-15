# Tests des alertes e-mail : routes /alerts, lien de désinscription,
# détection des franchissements et résumé quotidien (sans réseau).
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from compte_api import models
from compte_api.jobs import alertes as job
from compte_api.mailer import ConsoleMailer
from compte_api.routers.alertes import MAX_REGLES, jeton_desinscription
from .conftest import SECRET_PROXY

# --- Aides -------------------------------------------------------------------


def _entreprise(ticker, score, entree=False, nom=None):
    alertes = [{"kind": "entree", "title": "Conditions d'entrée réunies"}] if entree \
        else [{"kind": "info", "title": "Pas de signal actif"}]
    return {"ticker": ticker, "name": nom or ticker, "score": score, "alerts": alertes}


def _indices(*entreprises):
    return {"updated": "2026-11-15", "companies": list(entreprises)}


def _regle(db, user, kind, target, threshold=None, active=True):
    regle = models.AlertRule(user_id=user.id, kind=kind, target=target, threshold=threshold, active=active)
    db.add(regle)
    db.commit()
    db.refresh(regle)
    return regle


def _evenements(db, regle=None):
    requete = select(models.AlertEvent).order_by(models.AlertEvent.id)
    if regle is not None:
        requete = requete.where(models.AlertEvent.rule_id == regle.id)
    db.expire_all()
    return db.scalars(requete).all()


# --- Routes ------------------------------------------------------------------


def test_creer_lister_modifier_supprimer(client_membre):
    r = client_membre.post("/alerts", json={"kind": "score_above", "target": "MC.PA", "threshold": 20})
    assert r.status_code == 201
    regle = r.json()
    assert regle["kind"] == "score_above" and regle["target"] == "MC.PA" and regle["threshold"] == 20
    assert regle["active"] is True and regle["dernier_declenchement"] is None and regle["created_at"]

    r = client_membre.post("/alerts", json={"kind": "entry_signal", "target": "^GSPC"})
    assert r.status_code == 201 and r.json()["threshold"] is None

    liste = client_membre.get("/alerts").json()
    assert [x["target"] for x in liste] == ["MC.PA", "^GSPC"]

    r = client_membre.patch(f"/alerts/{regle['id']}", json={"active": False, "threshold": 30})
    assert r.status_code == 200
    assert r.json()["active"] is False and r.json()["threshold"] == 30

    assert client_membre.delete(f"/alerts/{regle['id']}").status_code == 204
    assert [x["target"] for x in client_membre.get("/alerts").json()] == ["^GSPC"]


def test_dernier_declenchement(client_membre, db, membre):
    r = client_membre.post("/alerts", json={"kind": "entry_signal", "target": "AAPL"}).json()
    quand = datetime(2026, 11, 14, 7, 30, tzinfo=timezone.utc)
    db.add(models.AlertEvent(rule_id=r["id"], triggered_at=quand, value=12.0))
    db.commit()
    assert client_membre.get("/alerts").json()[0]["dernier_declenchement"].startswith("2026-11-14T07:30")


@pytest.mark.parametrize("methode,chemin,corps", [
    ("get", "/alerts", None),
    ("post", "/alerts", {"kind": "entry_signal", "target": "AAPL"}),
    ("patch", "/alerts/1", {"active": False}),
    ("delete", "/alerts/1", None),
    ("get", "/alerts/email", None),
    ("put", "/alerts/email", {"alerts_email_enabled": False}),
])
def test_sans_session_401(client, methode, chemin, corps):
    kwargs = {"json": corps} if corps is not None else {}
    assert getattr(client, methode)(chemin, **kwargs).status_code == 401


@pytest.mark.parametrize("corps", [
    {"kind": "score_above", "target": "MC.PA"},                       # seuil manquant
    {"kind": "score_below", "target": "MC.PA", "threshold": 150},     # seuil hors bornes
    {"kind": "score_above", "target": "MC.PA", "threshold": -101},
    {"kind": "entry_signal", "target": "MC.PA", "threshold": 10},     # seuil interdit
    {"kind": "price_above", "target": "MC.PA", "threshold": 10},      # type inconnu
    {"kind": "entry_signal", "target": "MC PA"},                      # caractère interdit
    {"kind": "entry_signal", "target": ""},
    {"kind": "entry_signal", "target": "A" * 21},
    {"kind": "entry_signal"},
])
def test_creation_invalide_422(client_membre, corps):
    r = client_membre.post("/alerts", json=corps)
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)


def test_modification_invalide_422(client_membre):
    regle = client_membre.post("/alerts", json={"kind": "entry_signal", "target": "AAPL"}).json()
    # Passer à un type à seuil sans donner de seuil.
    assert client_membre.patch(f"/alerts/{regle['id']}", json={"kind": "score_above"}).status_code == 422
    assert client_membre.patch(f"/alerts/{regle['id']}", json={"threshold": 500}).status_code == 422
    # Passer d'un type à seuil au signal d'entrée efface le seuil.
    autre = client_membre.post("/alerts", json={"kind": "score_below", "target": "AAPL", "threshold": -5}).json()
    r = client_membre.patch(f"/alerts/{autre['id']}", json={"kind": "entry_signal"})
    assert r.status_code == 409  # même règle que la première : doublon
    r = client_membre.patch(f"/alerts/{autre['id']}", json={"kind": "entry_signal", "target": "MSFT"})
    assert r.status_code == 200 and r.json()["threshold"] is None


def test_doublon_refuse(client_membre):
    corps = {"kind": "score_above", "target": "MC.PA", "threshold": 20}
    assert client_membre.post("/alerts", json=corps).status_code == 201
    assert client_membre.post("/alerts", json=corps).status_code == 409


def test_isolation_404(client_membre, client_autre):
    regle = client_membre.post("/alerts", json={"kind": "entry_signal", "target": "AAPL"}).json()
    assert client_autre.get("/alerts").json() == []
    assert client_autre.patch(f"/alerts/{regle['id']}", json={"active": False}).status_code == 404
    assert client_autre.delete(f"/alerts/{regle['id']}").status_code == 404
    assert client_membre.get("/alerts").json()[0]["active"] is True
    assert client_membre.delete("/alerts/99999").status_code == 404


def test_limite_des_50(client_membre, db, membre):
    for i in range(MAX_REGLES):
        db.add(models.AlertRule(user_id=membre.id, kind="entry_signal", target=f"T{i}"))
    db.commit()
    r = client_membre.post("/alerts", json={"kind": "entry_signal", "target": "AAPL"})
    assert r.status_code == 409
    assert "50" in r.json()["detail"]


def test_reglage_email(client_membre, client_autre):
    assert client_membre.get("/alerts/email").json() == {"alerts_email_enabled": True, "email_verified": True}
    r = client_membre.put("/alerts/email", json={"alerts_email_enabled": False})
    assert r.status_code == 200 and r.json()["alerts_email_enabled"] is False
    assert client_membre.get("/alerts/email").json()["alerts_email_enabled"] is False
    assert client_autre.get("/alerts/email").json()["alerts_email_enabled"] is True
    assert client_membre.put("/alerts/email", json={"alerts_email_enabled": "peut-être"}).status_code == 422


def test_reglage_email_adresse_non_confirmee(creer_membre, connecter):
    client = connecter(creer_membre(verifie=False))
    assert client.get("/alerts/email").json() == {"alerts_email_enabled": True, "email_verified": False}


# --- Désinscription ------------------------------------------------------------


def test_desinscription_lien_ne_change_rien(client, db, membre, settings):
    # Les antivirus de messagerie ouvrent les liens : un GET n'agit pas.
    jeton = jeton_desinscription(membre.id, settings)
    r = client.get("/alerts/unsubscribe", params={"token": jeton})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert '<form method="post"' in r.text and f"?token={jeton}" in r.text
    db.expire_all()
    assert db.get(models.User, membre.id).alerts_email_enabled is True


def test_desinscription_valide(client, db, membre, autre_membre, settings):
    r = client.post("/alerts/unsubscribe", params={"token": jeton_desinscription(membre.id, settings)})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Tu ne recevras plus d'e-mails d'alerte." in r.text
    db.expire_all()
    assert db.get(models.User, membre.id).alerts_email_enabled is False
    assert db.get(models.User, autre_membre.id).alerts_email_enabled is True


def test_desinscription_par_formulaire_sans_en_tete_csrf(app, db, membre, settings):
    # Un formulaire HTML ne pose pas X-Requested-With : la route est exemptée.
    client = TestClient(app)
    client.headers.update({"X-Compte-Proxy": SECRET_PROXY})
    r = client.post("/alerts/unsubscribe", params={"token": jeton_desinscription(membre.id, settings)})
    assert r.status_code == 200
    db.expire_all()
    assert db.get(models.User, membre.id).alerts_email_enabled is False
    # Les autres routes gardent l'exigence.
    assert client.post("/logout").status_code == 403


@pytest.mark.parametrize("methode", ["get", "post"])
@pytest.mark.parametrize("jeton", ["", "abc", "1.", "1.deadbeef", "x.y", "-1.00"])
def test_desinscription_jeton_invalide(client, db, membre, jeton, methode):
    r = getattr(client, methode)("/alerts/unsubscribe", params={"token": jeton})
    assert r.status_code == 400
    assert "text/html" in r.headers["content-type"]
    assert "<form" not in r.text
    db.expire_all()
    assert db.get(models.User, membre.id).alerts_email_enabled is True


def test_desinscription_jeton_falsifie(client, db, membre, settings):
    jeton = jeton_desinscription(membre.id, settings)
    falsifie = jeton[:-1] + ("0" if jeton[-1] != "0" else "1")
    assert client.post("/alerts/unsubscribe", params={"token": falsifie}).status_code == 400
    db.expire_all()
    assert db.get(models.User, membre.id).alerts_email_enabled is True


def test_desinscription_signature_d_un_autre_membre(client, db, membre, autre_membre, settings):
    # L'id du membre avec la signature d'un autre : refusé.
    signature = jeton_desinscription(autre_membre.id, settings).split(".", 1)[1]
    r = client.post("/alerts/unsubscribe", params={"token": f"{membre.id}.{signature}"})
    assert r.status_code == 400
    # Le jeton d'un autre membre ne désinscrit que cet autre membre.
    assert client.post("/alerts/unsubscribe",
                       params={"token": jeton_desinscription(autre_membre.id, settings)}).status_code == 200
    db.expire_all()
    assert db.get(models.User, membre.id).alerts_email_enabled is True
    assert db.get(models.User, autre_membre.id).alerts_email_enabled is False


def test_desinscription_secret_dedie(client, membre, settings, monkeypatch):
    jeton_repli = jeton_desinscription(membre.id, settings)
    monkeypatch.setenv("COMPTE_UNSUBSCRIBE_SECRET", "secret-dedie")
    assert jeton_desinscription(membre.id, settings) != jeton_repli
    assert client.post("/alerts/unsubscribe", params={"token": jeton_repli}).status_code == 400
    assert client.post("/alerts/unsubscribe",
                       params={"token": jeton_desinscription(membre.id, settings)}).status_code == 200


# --- Détection : fonctions pures -------------------------------------------------


def test_franchit_score():
    E = job.Etat
    assert job.franchit("score_above", 20, E(15, False), E(25, False))
    assert not job.franchit("score_above", 20, E(25, False), E(30, False))   # déjà au-dessus
    assert not job.franchit("score_above", 20, E(25, False), E(15, False))
    assert job.franchit("score_below", 0, E(5, False), E(-3, False))
    assert not job.franchit("score_below", 0, E(-5, False), E(-8, False))
    assert not job.franchit("score_above", 20, E(None, False), E(25, False))
    assert not job.franchit("score_above", 20, E(15, False), E(None, False))
    assert job.franchit("entry_signal", None, E(1, False), E(1, True))
    assert not job.franchit("entry_signal", None, E(1, True), E(1, True))
    assert not job.franchit("entry_signal", None, E(1, True), E(1, False))


def test_signal_entree_meme_definition_que_le_site():
    assert job.signal_entree_actif({"alerts": [{"kind": "watch"}, {"kind": "entree"}]})
    assert not job.signal_entree_actif({"alerts": [{"kind": "info"}, {"kind": "watch"}]})
    assert not job.signal_entree_actif({})
    assert not job.signal_entree_actif({"alerts": None})


# --- Détection : avec la base ------------------------------------------------------


def test_premier_passage_silencieux(db, membre):
    _regle(db, membre, "score_above", "MC.PA", 20)
    assert job.detecter(db, _indices(_entreprise("MC.PA", 30))) == 0
    instantane = db.scalar(select(models.AlertSnapshot).where(models.AlertSnapshot.target == "MC.PA"))
    assert instantane.score == 30 and instantane.entry_signal is False


def test_franchissement_vers_le_haut_puis_pas_de_repetition(db, membre):
    regle = _regle(db, membre, "score_above", "MC.PA", 20)
    job.detecter(db, _indices(_entreprise("MC.PA", 15)))
    assert job.detecter(db, _indices(_entreprise("MC.PA", 25))) == 1
    assert [e.value for e in _evenements(db, regle)] == [25]
    # La valeur reste au-dessus : rien de nouveau.
    assert job.detecter(db, _indices(_entreprise("MC.PA", 28))) == 0
    # Redescend puis remonte : nouveau franchissement.
    job.detecter(db, _indices(_entreprise("MC.PA", 10)))
    assert job.detecter(db, _indices(_entreprise("MC.PA", 21))) == 1
    assert len(_evenements(db, regle)) == 2


def test_franchissement_vers_le_bas(db, membre):
    bas = _regle(db, membre, "score_below", "AAPL", 0)
    haut = _regle(db, membre, "score_above", "AAPL", 0)
    job.detecter(db, _indices(_entreprise("AAPL", 4)))
    assert job.detecter(db, _indices(_entreprise("AAPL", -2))) == 1
    assert len(_evenements(db, bas)) == 1 and _evenements(db, haut) == []
    assert job.detecter(db, _indices(_entreprise("AAPL", -6))) == 0
    assert job.detecter(db, _indices(_entreprise("AAPL", 3))) == 1
    assert len(_evenements(db, haut)) == 1


def test_signal_entree(db, membre):
    regle = _regle(db, membre, "entry_signal", "MSFT")
    job.detecter(db, _indices(_entreprise("MSFT", 10)))
    assert job.detecter(db, _indices(_entreprise("MSFT", 11, entree=True))) == 1
    assert job.detecter(db, _indices(_entreprise("MSFT", 12, entree=True))) == 0
    job.detecter(db, _indices(_entreprise("MSFT", 12)))
    assert job.detecter(db, _indices(_entreprise("MSFT", 12, entree=True))) == 1
    assert [e.value for e in _evenements(db, regle)] == [11, 12]


def test_ticker_absent_du_fichier(db, membre):
    regle = _regle(db, membre, "score_above", "MC.PA", 20)
    job.detecter(db, _indices(_entreprise("MC.PA", 15)))
    assert job.detecter(db, _indices(_entreprise("AAPL", 50))) == 0
    instantane = db.scalar(select(models.AlertSnapshot).where(models.AlertSnapshot.target == "MC.PA"))
    assert instantane.score == 15  # instantané inchangé
    # Retour du ticker : comparé au dernier état connu.
    assert job.detecter(db, _indices(_entreprise("MC.PA", 25))) == 1
    assert len(_evenements(db, regle)) == 1


def test_regle_creee_apres_l_instantane(db, membre, autre_membre):
    _regle(db, autre_membre, "entry_signal", "MC.PA")
    job.detecter(db, _indices(_entreprise("MC.PA", 15)))
    nouvelle = _regle(db, membre, "score_above", "MC.PA", 20)
    assert job.detecter(db, _indices(_entreprise("MC.PA", 25))) == 1
    assert len(_evenements(db, nouvelle)) == 1


def test_regle_inactive_ignoree(db, membre):
    regle = _regle(db, membre, "score_above", "MC.PA", 20, active=False)
    job.detecter(db, _indices(_entreprise("MC.PA", 15)))
    job.detecter(db, _indices(_entreprise("MC.PA", 25)))
    assert _evenements(db, regle) == []


def test_score_absent_garde_le_dernier_connu(db, membre):
    regle = _regle(db, membre, "score_above", "MC.PA", 20)
    job.detecter(db, _indices(_entreprise("MC.PA", 15)))
    assert job.detecter(db, _indices(_entreprise("MC.PA", None))) == 0
    assert job.detecter(db, _indices(_entreprise("MC.PA", 25))) == 1
    assert len(_evenements(db, regle)) == 1


def test_charger_indices_fichier_local(tmp_path):
    chemin = tmp_path / "indices.json"
    chemin.write_text(json.dumps(_indices(_entreprise("MC.PA", 1))), encoding="utf-8")
    assert job.charger_indices(str(chemin))["companies"][0]["ticker"] == "MC.PA"


# --- Résumé ------------------------------------------------------------------------


def _evenement(db, regle, valeur=25.0, il_y_a=timedelta(hours=1)):
    evenement = models.AlertEvent(rule_id=regle.id, value=valeur,
                                  triggered_at=datetime.now(timezone.utc) - il_y_a)
    db.add(evenement)
    db.commit()
    return evenement


def test_rediger_resume():
    lignes = [job.LigneResume("MC.PA", "LVMH", "score_above", 20, 25.34),
              job.LigneResume("^GSPC", None, "entry_signal", None, None)]
    sujet, texte = job.rediger_resume(lignes, "Alice", "http://site.test", "http://site.test/desinscr")
    assert sujet == "Tes 2 alertes du jour"
    assert texte.startswith("Bonjour Alice,")
    assert "LVMH (MC.PA) : score passé au-dessus de 20,0 (score : 25,3)" in texte
    assert "http://site.test/#indices/MC.PA" in texte
    assert "- ^GSPC : signal d'entrée activé" in texte
    assert "http://site.test/#indices/%5EGSPC" in texte
    assert "http://site.test/desinscr" in texte


def test_resume_un_seul_email_par_membre(db, membre, autre_membre, mailer, settings):
    r1 = _regle(db, membre, "score_above", "MC.PA", 20)
    r2 = _regle(db, membre, "entry_signal", "AAPL")
    r3 = _regle(db, autre_membre, "score_below", "MC.PA", 0)
    e1, e2, e3 = _evenement(db, r1), _evenement(db, r2, 8.0), _evenement(db, r3, -4.0)

    envoyes, echecs = job.envoyer_resumes(db, mailer, settings, {"MC.PA": "LVMH"})
    assert (envoyes, echecs) == (2, 0)
    assert sorted(m.destinataire for m in mailer.envoyes) == ["alice@exemple.test", "bob@exemple.test"]
    mail_alice = next(m for m in mailer.envoyes if m.destinataire == "alice@exemple.test")
    assert "LVMH (MC.PA)" in mail_alice.texte and "AAPL" in mail_alice.texte
    assert f"/api/compte/alerts/unsubscribe?token={jeton_desinscription(membre.id, settings)}" in mail_alice.texte
    db.expire_all()
    assert all(db.get(models.AlertEvent, e.id).emailed_at is not None for e in (e1, e2, e3))
    # Second passage : plus rien à envoyer.
    assert job.envoyer_resumes(db, mailer, settings) == (0, 0)
    assert len(mailer.envoyes) == 2


def test_resume_exclut_non_confirmes_et_desinscrits(db, creer_membre, mailer, settings):
    non_confirme = creer_membre(verifie=False)
    desinscrit = creer_membre()
    desinscrit.alerts_email_enabled = False
    db.commit()
    e1 = _evenement(db, _regle(db, non_confirme, "entry_signal", "AAPL"))
    e2 = _evenement(db, _regle(db, desinscrit, "entry_signal", "AAPL"))
    assert job.envoyer_resumes(db, mailer, settings) == (0, 0)
    assert mailer.envoyes == []
    db.expire_all()
    assert db.get(models.AlertEvent, e1.id).emailed_at is None
    assert db.get(models.AlertEvent, e2.id).emailed_at is None


def test_resume_ignore_les_evenements_trop_anciens(db, membre, mailer, settings):
    _evenement(db, _regle(db, membre, "entry_signal", "AAPL"), il_y_a=timedelta(days=30))
    assert job.envoyer_resumes(db, mailer, settings) == (0, 0)


class _MailerCapricieux(ConsoleMailer):
    """Échoue pour une adresse donnée, envoie normalement aux autres."""

    def __init__(self, adresse_en_echec):
        super().__init__(afficher=False)
        self.adresse_en_echec = adresse_en_echec

    def envoyer(self, destinataire, sujet, texte):
        if destinataire == self.adresse_en_echec:
            raise OSError("SMTP indisponible")
        super().envoyer(destinataire, sujet, texte)


def test_resume_echec_isole(db, membre, autre_membre, settings):
    e_alice = _evenement(db, _regle(db, membre, "entry_signal", "AAPL"))
    e_bob = _evenement(db, _regle(db, autre_membre, "entry_signal", "AAPL"))
    mailer = _MailerCapricieux("alice@exemple.test")
    assert job.envoyer_resumes(db, mailer, settings) == (1, 1)
    assert [m.destinataire for m in mailer.envoyes] == ["bob@exemple.test"]
    db.expire_all()
    assert db.get(models.AlertEvent, e_alice.id).emailed_at is None
    assert db.get(models.AlertEvent, e_bob.id).emailed_at is not None


def test_main_detecter_puis_resume(tmp_path, app, db, membre, settings, monkeypatch, capsys):
    # Chaîne complète en ligne de commande, sur un fichier local.
    _regle(db, membre, "score_above", "MC.PA", 20)
    fichier = tmp_path / "indices.json"
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("COMPTE_SITE_URL", "http://site.test")
    monkeypatch.setenv("COMPTE_MAILER", "console")
    monkeypatch.setenv("COMPTE_INDICES_URL", str(fichier))
    fichier.write_text(json.dumps(_indices(_entreprise("MC.PA", 15, nom="LVMH"))), encoding="utf-8")
    assert job.main(["detecter"]) == 0
    fichier.write_text(json.dumps(_indices(_entreprise("MC.PA", 25, nom="LVMH"))), encoding="utf-8")
    assert job.main(["detecter"]) == 0
    assert job.main(["resume"]) == 0
    assert "LVMH (MC.PA)" in capsys.readouterr().out
    assert job.main(["inconnu"]) == 2
