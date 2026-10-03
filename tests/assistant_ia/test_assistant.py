import pytest

import assistant_ia.assistant as assistant


@pytest.fixture(autouse=True)
def _no_real_usage_writes(monkeypatch):
    """Empeche TOUT test de ce fichier d'ecrire dans le vrai
    usage_today.json du depot (meme risque que les fonctions update_*
    non mockees dans tests/test_indices_score.py, voir
    [[project-nikkei-hangseng-chart]]) — seul le test dedie au suivi
    d'usage remplace ce stub par sa propre verification."""
    monkeypatch.setattr(assistant.usage, "record_usage", lambda *a, **k: None)


class _FakeBlock:
    def __init__(self, type_, **kwargs):
        self.type = type_
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeMessage:
    def __init__(self, content, stop_reason, usage):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage


class _FakeStream:
    """Simule client.messages.stream(...) : un context manager iterable
    sur des evenements de texte, avec .get_final_message() a la fin."""

    def __init__(self, text_events, final_message):
        self._text_events = text_events
        self._final_message = final_message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for texte in self._text_events:
            yield _FakeBlock("text", text=texte)

    def get_final_message(self):
        return self._final_message


class _FakeMessagesEndpoint:
    def __init__(self, scripted_streams):
        self._scripted = list(scripted_streams)
        self.appels = []

    def stream(self, **kwargs):
        # Snapshot la liste `messages` au moment de l'appel : l'implementation
        # mute `messages` en place entre les iterations (append), donc sans
        # cette copie superficielle toutes les entrees de self.appels
        # finiraient par pointer vers le MEME objet liste, dans son etat
        # final — masquant un bug ou rien n'est renvoye apres un tool_use.
        self.appels.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._scripted.pop(0)


class _FakeClient:
    def __init__(self, scripted_streams):
        self.messages = _FakeMessagesEndpoint(scripted_streams)


def test_run_assistant_loop_yields_text_then_fin_when_no_tool_is_called():
    final = _FakeMessage(
        content=[_FakeBlock("text", text="LVMH est solide.")],
        stop_reason="end_turn", usage=_FakeUsage(100, 50))
    client = _FakeClient([_FakeStream(["LVMH est solide."], final)])

    evenements = list(assistant.run_assistant_loop("Pourquoi LVMH ?", [], client=client))

    types = [e["type"] for e in evenements]
    assert types == ["texte", "fin"]
    assert evenements[0]["texte"] == "LVMH est solide."


def test_run_assistant_loop_dispatches_a_tool_call_and_loops(monkeypatch):
    enregistres = []
    monkeypatch.setattr(assistant.usage, "record_usage", lambda i, o, model=None: enregistres.append((i, o)))

    tool_use_block = _FakeBlock("tool_use", id="tu_1", name="fiche_entreprise", input={"ticker": "MC.PA"})
    first_final = _FakeMessage(content=[tool_use_block], stop_reason="tool_use", usage=_FakeUsage(200, 20))
    second_final = _FakeMessage(
        content=[_FakeBlock("text", text="LVMH a un score de 72.")],
        stop_reason="end_turn", usage=_FakeUsage(300, 40))
    client = _FakeClient([
        _FakeStream([], first_final),
        _FakeStream(["LVMH a un score de 72."], second_final),
    ])

    evenements = list(assistant.run_assistant_loop(
        "Note de LVMH ?", [], client=client,
        tool_dispatch=lambda name, inp: {"ticker": "MC.PA", "score": 72.0}))

    deuxieme_appel_messages = client.messages.appels[1]["messages"]
    assert deuxieme_appel_messages[-1]["role"] == "user"
    tool_results = deuxieme_appel_messages[-1]["content"]
    assert len(tool_results) == 1
    assert tool_results[0]["type"] == "tool_result"
    assert tool_results[0]["tool_use_id"] == "tu_1"

    types = [e["type"] for e in evenements]
    assert "texte" in types and "fin" in types

    # Une entree de usage.record_usage par appel API Anthropic (contrainte
    # globale du plan), pas une seule par question.
    assert enregistres == [(200, 20), (300, 40)]


def test_run_assistant_loop_collects_proposer_lien_calls_separately():
    tool_use_1 = _FakeBlock("tool_use", id="tu_1", name="fiche_entreprise", input={"ticker": "MC.PA"})
    tool_use_2 = _FakeBlock(
        "tool_use", id="tu_2", name="proposer_lien",
        input={"cible_type": "ticker", "cible_valeur": "MC.PA", "libelle": "Voir LVMH"})
    first_final = _FakeMessage(
        content=[tool_use_1, tool_use_2], stop_reason="tool_use", usage=_FakeUsage(200, 20))
    second_final = _FakeMessage(
        content=[_FakeBlock("text", text="Voici.")], stop_reason="end_turn", usage=_FakeUsage(100, 10))
    client = _FakeClient([_FakeStream([], first_final), _FakeStream(["Voici."], second_final)])

    def fake_dispatch(name, tool_input):
        if name == "proposer_lien":
            return dict(tool_input, cible_type=tool_input["cible_type"])
        return {"ticker": "MC.PA"}

    evenements = list(assistant.run_assistant_loop("Q", [], client=client, tool_dispatch=fake_dispatch))

    liens_events = [e for e in evenements if e["type"] == "liens"]
    assert len(liens_events) == 1
    assert liens_events[0]["liens"][0]["cible_valeur"] == "MC.PA"


def test_run_assistant_loop_stops_at_the_iteration_cap(monkeypatch):
    enregistres = []
    monkeypatch.setattr(assistant.usage, "record_usage", lambda i, o, model=None: enregistres.append((i, o)))

    # Le premier tour propose aussi un lien : ce lien doit etre collecte
    # et renvoye meme si la boucle se termine via le plafond d'iterations
    # (pas via un stop_reason != "tool_use" normal).
    tool_use_fiche = _FakeBlock("tool_use", id="tu_x", name="fiche_entreprise", input={"ticker": "X"})
    tool_use_lien = _FakeBlock(
        "tool_use", id="tu_lien", name="proposer_lien",
        input={"cible_type": "ticker", "cible_valeur": "X", "libelle": "Voir X"})
    premier_tour = _FakeMessage(
        content=[tool_use_fiche, tool_use_lien], stop_reason="tool_use", usage=_FakeUsage(10, 5))
    tour_suivant = _FakeMessage(content=[tool_use_fiche], stop_reason="tool_use", usage=_FakeUsage(10, 5))
    client = _FakeClient(
        [_FakeStream([], premier_tour)]
        + [_FakeStream([], tour_suivant) for _ in range(assistant.MAX_TOOL_ITERATIONS - 1)]
    )

    def fake_dispatch(name, tool_input):
        if name == "proposer_lien":
            return dict(tool_input)
        return {"ok": True}

    evenements = list(assistant.run_assistant_loop(
        "Q", [], client=client, tool_dispatch=fake_dispatch))

    assert len(client.messages.appels) == assistant.MAX_TOOL_ITERATIONS
    assert evenements[-1]["type"] == "fin"
    assert any(e["type"] == "texte" and "n'ai pas pu" in e["texte"] for e in evenements)

    liens_events = [e for e in evenements if e["type"] == "liens"]
    assert len(liens_events) == 1
    assert liens_events[0]["liens"][0]["cible_valeur"] == "X"

    # Un appel a usage.record_usage par appel API effectue (une fois par
    # iteration du plafond), pas davantage ni moins.
    assert len(enregistres) == assistant.MAX_TOOL_ITERATIONS


def test_run_assistant_loop_records_usage_for_every_api_call(monkeypatch):
    enregistres = []
    monkeypatch.setattr(assistant.usage, "record_usage", lambda i, o, model=None: enregistres.append((i, o)))
    final = _FakeMessage(content=[_FakeBlock("text", text="ok")], stop_reason="end_turn", usage=_FakeUsage(111, 22))
    client = _FakeClient([_FakeStream(["ok"], final)])

    list(assistant.run_assistant_loop("Q", [], client=client))

    assert enregistres == [(111, 22)]


def test_run_assistant_loop_does_not_collect_a_proposer_lien_error():
    tool_use_1 = _FakeBlock("tool_use", id="tu_1", name="fiche_entreprise", input={"ticker": "INCONNU"})
    tool_use_2 = _FakeBlock(
        "tool_use", id="tu_2", name="proposer_lien",
        input={"cible_type": "ticker", "cible_valeur": "INCONNU", "libelle": "Voir ?"})
    first_final = _FakeMessage(
        content=[tool_use_1, tool_use_2], stop_reason="tool_use", usage=_FakeUsage(200, 20))
    second_final = _FakeMessage(
        content=[_FakeBlock("text", text="Je ne trouve pas cette entreprise.")],
        stop_reason="end_turn", usage=_FakeUsage(100, 10))
    client = _FakeClient([_FakeStream([], first_final), _FakeStream(["Je ne trouve pas."], second_final)])

    def fake_dispatch(name, tool_input):
        if name == "proposer_lien":
            return {"erreur": "cible inconnue"}
        return {"erreur": "ticker inconnu"}

    evenements = list(assistant.run_assistant_loop("Q", [], client=client, tool_dispatch=fake_dispatch))

    liens_events = [e for e in evenements if e["type"] == "liens"]
    assert liens_events == []


def test_run_assistant_loop_collects_links_across_multiple_turns():
    tool_use_lien_1 = _FakeBlock(
        "tool_use", id="tu_1", name="proposer_lien",
        input={"cible_type": "ticker", "cible_valeur": "MC.PA", "libelle": "Voir LVMH"})
    tool_use_lien_2 = _FakeBlock(
        "tool_use", id="tu_2", name="proposer_lien",
        input={"cible_type": "ticker", "cible_valeur": "OR.PA", "libelle": "Voir L'Oreal"})
    first_final = _FakeMessage(content=[tool_use_lien_1], stop_reason="tool_use", usage=_FakeUsage(50, 5))
    second_final = _FakeMessage(content=[tool_use_lien_2], stop_reason="tool_use", usage=_FakeUsage(50, 5))
    third_final = _FakeMessage(
        content=[_FakeBlock("text", text="Voici les deux.")],
        stop_reason="end_turn", usage=_FakeUsage(100, 10))
    client = _FakeClient([
        _FakeStream([], first_final),
        _FakeStream([], second_final),
        _FakeStream(["Voici les deux."], third_final),
    ])

    def fake_dispatch(name, tool_input):
        return dict(tool_input)

    evenements = list(assistant.run_assistant_loop("Q", [], client=client, tool_dispatch=fake_dispatch))

    liens_events = [e for e in evenements if e["type"] == "liens"]
    assert len(liens_events) == 1
    valeurs = {lien["cible_valeur"] for lien in liens_events[0]["liens"]}
    assert valeurs == {"MC.PA", "OR.PA"}


def test_run_assistant_loop_overrides_resume_portefeuille_input_with_the_real_manual_positions():
    # Le modele peut appeler resume_portefeuille sans argument (ou avec un
    # argument hallucine) — la boucle doit quand meme transmettre les VRAIES
    # positions manuelles recues en parametre de run_assistant_loop, en les
    # injectant cote serveur plutot que de faire confiance a ce que Claude
    # a envoye (voir finding 1 de la revue finale).
    appels_dispatch = []

    def fake_dispatch(name, tool_input):
        appels_dispatch.append((name, tool_input))
        return {"positions": [], "nombre_total": 0}

    tool_use_block = _FakeBlock(
        "tool_use", id="tu_1", name="resume_portefeuille", input={})
    first_final = _FakeMessage(content=[tool_use_block], stop_reason="tool_use", usage=_FakeUsage(10, 5))
    second_final = _FakeMessage(
        content=[_FakeBlock("text", text="Voici ton portefeuille.")],
        stop_reason="end_turn", usage=_FakeUsage(10, 5))
    client = _FakeClient([_FakeStream([], first_final), _FakeStream(["Voici ton portefeuille."], second_final)])

    positions_reelles = [{"ticker": "MANUELLE.PA", "quantity": 5}]

    list(assistant.run_assistant_loop(
        "Resume mon portefeuille", [], manual_positions=positions_reelles,
        client=client, tool_dispatch=fake_dispatch))

    assert len(appels_dispatch) == 1
    nom, tool_input = appels_dispatch[0]
    assert nom == "resume_portefeuille"
    assert tool_input["positions_manuelles"] == positions_reelles


def test_run_assistant_loop_notes_truncation_on_max_tokens():
    final = _FakeMessage(
        content=[_FakeBlock("text", text="Reponse partielle...")],
        stop_reason="max_tokens", usage=_FakeUsage(16000, 16000))
    client = _FakeClient([_FakeStream(["Reponse partielle..."], final)])

    evenements = list(assistant.run_assistant_loop("Q", [], client=client))

    assert any(
        e["type"] == "texte" and "tronquee" in e["texte"]
        for e in evenements
    )
    assert evenements[-1]["type"] == "fin"


def test_run_assistant_loop_notes_refusal_and_stops():
    final = _FakeMessage(content=[], stop_reason="refusal", usage=_FakeUsage(50, 0))
    client = _FakeClient([_FakeStream([], final)])

    evenements = list(assistant.run_assistant_loop("Q", [], client=client))

    assert any(
        e["type"] == "texte" and "Je ne peux pas repondre" in e["texte"]
        for e in evenements
    )
    assert evenements[-1]["type"] == "fin"


def test_run_assistant_loop_yields_erreur_event_when_the_api_call_raises():
    class _FailingMessagesEndpoint:
        def __init__(self):
            self.appels = []

        def stream(self, **kwargs):
            self.appels.append(kwargs)
            raise RuntimeError("connexion refusee par l'API Anthropic")

    class _FailingClient:
        def __init__(self):
            self.messages = _FailingMessagesEndpoint()

    client = _FailingClient()

    evenements = list(assistant.run_assistant_loop("Q", [], client=client))

    assert evenements[0]["type"] == "erreur"
    assert "connexion refusee" in evenements[0]["detail"]
    assert evenements[-1]["type"] == "fin"
    assert len(evenements) == 2
    assert len(client.messages.appels) == 1
