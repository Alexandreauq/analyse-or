# Tests de l'historique (recherches, échanges IA) et du relais /ai/ask.
# L'assistant est toujours simulé : aucun appel réseau.
import json
from dataclasses import replace

import pytest

from compte_api import models
from compte_api.quota import consommer_quota, quota_restant
from compte_api.relais_ia import ErreurAssistant, FluxAssistant
from compte_api.routers.historique import get_relais


# --- Faux assistant -------------------------------------------------------

def _sse(evenement: dict) -> bytes:
    return f"data: {json.dumps(evenement, ensure_ascii=False)}\n\n".encode("utf-8")


class FauxRelais:
    """Remplace l'appel HTTP : rejoue des morceaux SSE ou lève une erreur."""

    def __init__(self, morceaux=None, erreur=None, coupure=False):
        self.morceaux = morceaux if morceaux is not None else [
            _sse({"type": "texte", "texte": "Bon"}),
            # Un événement coupé entre deux morceaux réseau.
            _sse({"type": "texte", "texte": "jour !"})[:12],
            _sse({"type": "texte", "texte": "jour !"})[12:],
            _sse({"type": "liens", "liens": []}),
            _sse({"type": "fin"}),
        ]
        self.erreur = erreur
        self.coupure = coupure
        self.appels = []
        self.ferme = False

    async def __call__(self, url, jeton, user_id, payload):
        self.appels.append({"url": url, "jeton": jeton, "user_id": user_id, "payload": payload})
        if self.erreur:
            raise self.erreur

        async def morceaux():
            for m in self.morceaux:
                yield m
            if self.coupure:
                raise ConnectionError("coupure")

        async def fermer():
            self.ferme = True

        return FluxAssistant(morceaux=morceaux(), fermer=fermer)


@pytest.fixture
def settings(settings):
    # Assistant configuré (surcharge la fixture du conftest).
    return replace(settings, assistant_ia_url="http://assistant.test", assistant_ia_token="jeton-service",
                   ai_quota_par_jour=3)


@pytest.fixture
def relais(app):
    faux = FauxRelais()
    app.dependency_overrides[get_relais] = lambda: faux
    yield faux
    app.dependency_overrides.clear()


def _restant(db, user, settings):
    db.expire_all()
    return quota_restant(db, user, settings)


# --- Recherches -----------------------------------------------------------

def test_recherches_ajout_et_liste_plus_recentes_d_abord(client_membre):
    assert client_membre.post("/searches", json={"query": "  LVMH  "}).status_code == 201
    r = client_membre.post("/searches", json={"query": "CAC40"})
    assert r.status_code == 201
    assert set(r.json()) == {"id", "query", "created_at"}

    liste = client_membre.get("/searches").json()
    assert [x["query"] for x in liste] == ["CAC40", "LVMH"]


def test_recherche_identique_a_la_derniere_pas_de_doublon(client_membre):
    premiere = client_membre.post("/searches", json={"query": "or"}).json()
    r = client_membre.post("/searches", json={"query": " or "})
    assert r.status_code == 200
    assert r.json()["id"] == premiere["id"]
    client_membre.post("/searches", json={"query": "argent"})
    client_membre.post("/searches", json={"query": "or"})
    assert [x["query"] for x in client_membre.get("/searches").json()] == ["or", "argent", "or"]


@pytest.mark.parametrize("corps", [{"query": ""}, {"query": "   "}, {"query": "x" * 201}, {}, {"query": 3}])
def test_recherche_invalide_422(client_membre, corps):
    assert client_membre.post("/searches", json=corps).status_code == 422


def test_recherche_200_caracteres_acceptee(client_membre):
    assert client_membre.post("/searches", json={"query": "x" * 200}).status_code == 201


def test_recherches_purge_des_100(client_membre, db, membre):
    for i in range(105):
        client_membre.post("/searches", json={"query": f"q{i}"})
    liste = client_membre.get("/searches").json()
    assert len(liste) == 100
    assert liste[0]["query"] == "q104" and liste[-1]["query"] == "q5"
    assert db.query(models.Search).filter_by(user_id=membre.id).count() == 100


def test_recherches_effacer_une_et_tout(client_membre):
    a = client_membre.post("/searches", json={"query": "a"}).json()
    client_membre.post("/searches", json={"query": "b"})
    assert client_membre.delete(f"/searches/{a['id']}").status_code == 204
    assert [x["query"] for x in client_membre.get("/searches").json()] == ["b"]
    assert client_membre.delete(f"/searches/{a['id']}").status_code == 404
    assert client_membre.delete("/searches").status_code == 204
    assert client_membre.get("/searches").json() == []


def test_recherches_sans_session_401(client):
    assert client.get("/searches").status_code == 401
    assert client.post("/searches", json={"query": "a"}).status_code == 401
    assert client.delete("/searches").status_code == 401
    assert client.delete("/searches/1").status_code == 401


def test_recherches_isolation(client_membre, client_autre):
    a = client_membre.post("/searches", json={"query": "secret"}).json()
    client_autre.post("/searches", json={"query": "autre"})
    assert [x["query"] for x in client_autre.get("/searches").json()] == ["autre"]
    # Un membre ne peut ni voir ni effacer la recherche d'un autre.
    assert client_autre.delete(f"/searches/{a['id']}").status_code == 404
    client_autre.delete("/searches")
    assert [x["query"] for x in client_membre.get("/searches").json()] == ["secret"]


# --- Historique IA --------------------------------------------------------

def _echange(db, user, question="Q", answer="R"):
    e = models.AiExchange(user_id=user.id, question=question, answer=answer)
    db.add(e)
    db.commit()
    return e


def test_historique_ia_liste_et_effacement(client_membre, db, membre):
    a = _echange(db, membre, "Q1", "R1")
    _echange(db, membre, "Q2", "R2")
    liste = client_membre.get("/ai/history").json()
    assert [(x["question"], x["answer"]) for x in liste] == [("Q2", "R2"), ("Q1", "R1")]
    assert set(liste[0]) == {"id", "question", "answer", "created_at"}

    assert client_membre.delete(f"/ai/history/{a.id}").status_code == 204
    assert [x["question"] for x in client_membre.get("/ai/history").json()] == ["Q2"]
    assert client_membre.delete(f"/ai/history/{a.id}").status_code == 404
    assert client_membre.delete("/ai/history").status_code == 204
    assert client_membre.get("/ai/history").json() == []


def test_historique_ia_limite_a_100(client_membre, db, membre):
    for i in range(105):
        db.add(models.AiExchange(user_id=membre.id, question=f"Q{i}", answer="R"))
    db.commit()
    liste = client_membre.get("/ai/history").json()
    assert len(liste) == 100 and liste[0]["question"] == "Q104"


def test_historique_ia_id_invalide_422(client_membre):
    assert client_membre.delete("/ai/history/abc").status_code == 422


def test_historique_ia_sans_session_401(client):
    assert client.get("/ai/history").status_code == 401
    assert client.delete("/ai/history").status_code == 401
    assert client.delete("/ai/history/1").status_code == 401


def test_historique_ia_isolation(client_membre, client_autre, db, membre):
    e = _echange(db, membre, "privé", "réponse")
    assert client_autre.get("/ai/history").json() == []
    assert client_autre.delete(f"/ai/history/{e.id}").status_code == 404
    client_autre.delete("/ai/history")
    assert len(client_membre.get("/ai/history").json()) == 1


# --- Relais /ai/ask -------------------------------------------------------

def test_ask_transmet_le_flux_et_enregistre_l_echange(client_membre, relais, db, membre, settings):
    r = client_membre.post("/ai/ask", json={
        "question": "Salut ?", "history": [{"role": "user", "content": "avant"}],
        "manual_positions": [{"ticker": "MC.PA"}], "niveau": "grand_public", "contexte": "Accueil"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.content == b"".join(relais.morceaux)  # flux transmis octet pour octet
    assert r.headers["X-Quota-Restant"] == "2"
    assert relais.ferme

    appel = relais.appels[0]
    assert appel["url"] == "http://assistant.test" and appel["jeton"] == "jeton-service"
    assert appel["user_id"] == membre.id
    assert appel["payload"] == {"question": "Salut ?", "history": [{"role": "user", "content": "avant"}],
                                "manual_positions": [{"ticker": "MC.PA"}], "niveau": "grand_public",
                                "contexte": "Accueil"}

    historique = client_membre.get("/ai/history").json()
    assert [(x["question"], x["answer"]) for x in historique] == [("Salut ?", "Bonjour !")]
    assert _restant(db, membre, settings) == 2


def test_ask_tronque_l_historique_envoye(client_membre, relais):
    historique = [{"role": "user", "content": str(i)} for i in range(30)]
    client_membre.post("/ai/ask", json={"question": "Q", "history": historique})
    envoye = relais.appels[0]["payload"]["history"]
    assert len(envoye) == 12 and envoye[-1]["content"] == "29"


def test_ask_quota_epuise_429(client_membre, relais, db, membre, settings):
    for _ in range(3):
        consommer_quota(db, membre, settings)
    r = client_membre.post("/ai/ask", json={"question": "Q"})
    assert r.status_code == 429
    assert r.json() == {"detail": "Tu as utilisé tes 3 questions du jour. Reviens demain !", "quota_restant": 0}
    assert r.headers["X-Quota-Restant"] == "0"
    assert relais.appels == []  # l'assistant n'est jamais appelé


def test_ask_consomme_le_quota_a_chaque_question(client_membre, relais):
    codes = [client_membre.post("/ai/ask", json={"question": "Q"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


@pytest.mark.parametrize("erreur, statut", [
    (ErreurAssistant(429, "Plafond quotidien de l'assistant atteint, reessaie demain"), 429),
    (ErreurAssistant(503, "L'assistant est très sollicité en ce moment : réessaie dans un instant."), 503),
    (ErreurAssistant(503, "Je n'ai pas pu contacter l'assistant, réessaie dans un instant."), 503),
    (ErreurAssistant(502, "L'assistant ne peut pas répondre pour le moment, réessaie dans un instant."), 502),
])
def test_ask_echec_avant_contenu_rend_le_quota(client_membre, relais, db, membre, settings, erreur, statut):
    relais.erreur = erreur
    r = client_membre.post("/ai/ask", json={"question": "Q"})
    assert r.status_code == statut
    assert r.json() == {"detail": erreur.detail, "quota_restant": 3}
    assert _restant(db, membre, settings) == 3
    assert client_membre.get("/ai/history").json() == []


def test_ask_erreur_inattendue_du_relais_rend_le_quota(client_membre, relais, db, membre, settings):
    relais.erreur = RuntimeError("boum")
    r = client_membre.post("/ai/ask", json={"question": "Q"})
    assert r.status_code == 502
    assert _restant(db, membre, settings) == 3


def test_ask_flux_sans_texte_rend_le_quota(client_membre, relais, db, membre, settings):
    relais.morceaux = [_sse({"type": "erreur", "detail": "API indisponible"}), _sse({"type": "fin"})]
    r = client_membre.post("/ai/ask", json={"question": "Q"})
    assert r.status_code == 200
    assert _restant(db, membre, settings) == 3
    assert client_membre.get("/ai/history").json() == []


def test_ask_coupure_en_cours_de_flux(client_membre, relais, db, membre, settings):
    relais.morceaux = [_sse({"type": "texte", "texte": "Début"})]
    relais.coupure = True
    r = client_membre.post("/ai/ask", json={"question": "Q"})
    evenements = [json.loads(l[6:]) for l in r.text.split("\n\n") if l.startswith("data: ")]
    assert evenements[-2]["type"] == "erreur" and evenements[-1] == {"type": "fin"}
    # Réponse commencée : le quota reste consommé, mais l'échange incomplet
    # n'est pas enregistré.
    assert _restant(db, membre, settings) == 2
    assert client_membre.get("/ai/history").json() == []
    assert relais.ferme


def test_ask_admin_sans_quota(creer_membre, connecter, relais, settings):
    admin = connecter(creer_membre(role="admin"))
    for _ in range(settings.ai_quota_par_jour + 2):
        r = admin.post("/ai/ask", json={"question": "Q"})
        assert r.status_code == 200
        assert "X-Quota-Restant" not in r.headers


def test_ask_assistant_non_configure_503(app, client_membre, relais, db, membre, settings):
    app.state.settings = replace(settings, assistant_ia_url="")
    r = client_membre.post("/ai/ask", json={"question": "Q"})
    assert r.status_code == 503
    assert r.json() == {"detail": "Assistant indisponible."}
    assert relais.appels == []
    assert _restant(db, membre, settings) == 3


def test_ask_sans_session_401(client, relais):
    assert client.post("/ai/ask", json={"question": "Q"}).status_code == 401
    assert relais.appels == []


def test_ask_adresse_non_confirmee_403(creer_membre, connecter, relais):
    non_confirme = connecter(creer_membre(verifie=False))
    r = non_confirme.post("/ai/ask", json={"question": "Q"})
    assert r.status_code == 403
    assert relais.appels == []


@pytest.mark.parametrize("corps", [
    {}, {"question": ""}, {"question": "   "}, {"question": "x" * 501},
    {"question": "Q", "history": "pas une liste"},
    {"question": "Q", "history": [{"role": "system", "content": "x"}]},
    {"question": "Q", "contexte": "x" * 201},
])
def test_ask_donnees_invalides_422(client_membre, relais, db, membre, settings, corps):
    assert client_membre.post("/ai/ask", json=corps).status_code == 422
    assert relais.appels == []
    assert _restant(db, membre, settings) == 3


def test_ask_isolation_echange_et_quota(client_membre, client_autre, relais, db, autre_membre, settings):
    client_membre.post("/ai/ask", json={"question": "Q"})
    assert client_autre.get("/ai/history").json() == []
    assert _restant(db, autre_membre, settings) == 3
    client_autre.post("/ai/ask", json={"question": "Q2"})
    assert relais.appels[1]["user_id"] == autre_membre.id


def test_ask_purge_des_100_echanges(client_membre, relais, db, membre, settings):
    for i in range(100):
        db.add(models.AiExchange(user_id=membre.id, question=f"Q{i}", answer="R"))
    db.commit()
    client_membre.post("/ai/ask", json={"question": "nouvelle"})
    db.expire_all()
    assert db.query(models.AiExchange).filter_by(user_id=membre.id).count() == 100
    assert client_membre.get("/ai/history").json()[0]["question"] == "nouvelle"


# --- Relais HTTP réel, sur un transport simulé (httpx.MockTransport) -------

def _relais_reel(gestionnaire):
    import asyncio

    import httpx

    from compte_api.relais_ia import relais_httpx

    async def appel():
        flux = await relais_httpx("http://assistant.test/", "jeton-service", 7, {"question": "Q"},
                                  transport=httpx.MockTransport(gestionnaire))
        try:
            return b"".join([m async for m in flux.morceaux])
        finally:
            await flux.fermer()
    return asyncio.run(appel())


def test_relais_httpx_envoie_jeton_et_membre_et_rend_le_flux():
    import httpx
    recu = {}

    def gestionnaire(requete):
        recu.update(url=str(requete.url), jeton=requete.headers["X-Bot-Token"],
                    membre=requete.headers["X-Compte-User"], corps=json.loads(requete.content))
        async def corps():  # flux réel (un contenu en octets serait déjà lu)
            yield _sse({"type": "texte", "texte": "ok"})
        return httpx.Response(200, content=corps(), headers={"content-type": "text/event-stream"})

    assert _relais_reel(gestionnaire) == _sse({"type": "texte", "texte": "ok"})
    assert recu == {"url": "http://assistant.test/ask", "jeton": "jeton-service", "membre": "7",
                    "corps": {"question": "Q"}}


@pytest.mark.parametrize("statut, corps, attendu", [
    (429, {"detail": "Trop de questions"}, (429, "Trop de questions")),
    (503, {"detail": "Saturé"}, (503, "Saturé")),
    (401, {"detail": "Jeton invalide ou absent"}, (502, None)),
    (500, None, (502, None)),
])
def test_relais_httpx_erreur_http_avant_contenu(statut, corps, attendu):
    import httpx

    from compte_api.relais_ia import MESSAGE_ERREUR

    with pytest.raises(ErreurAssistant) as e:
        _relais_reel(lambda r: httpx.Response(statut, json=corps) if corps else httpx.Response(statut))
    assert (e.value.statut, e.value.detail) == (attendu[0], attendu[1] or MESSAGE_ERREUR)


def test_relais_httpx_assistant_injoignable_503():
    import httpx

    def gestionnaire(requete):
        raise httpx.ConnectError("refusé", request=requete)

    with pytest.raises(ErreurAssistant) as e:
        _relais_reel(gestionnaire)
    assert e.value.statut == 503


def test_historique_ia_trie_par_date_et_non_par_ordre_d_ajout(client_membre, db, membre):
    # Un échange importé (plus ancien) arrive après un échange récent : la
    # liste doit rester du plus récent au plus ancien.
    from datetime import datetime, timedelta
    maintenant = datetime.utcnow().replace(microsecond=0)
    db.add(models.AiExchange(user_id=membre.id, question="recente", answer="r", created_at=maintenant))
    db.commit()
    db.add(models.AiExchange(user_id=membre.id, question="ancienne", answer="r",
                             created_at=maintenant - timedelta(days=3)))
    db.add(models.AiExchange(user_id=membre.id, question="moyenne", answer="r",
                             created_at=maintenant - timedelta(days=1)))
    db.commit()
    assert [e["question"] for e in client_membre.get("/ai/history").json()] == ["recente", "moyenne", "ancienne"]
