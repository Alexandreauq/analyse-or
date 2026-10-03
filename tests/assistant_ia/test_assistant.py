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
        self.appels.append(kwargs)
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


def test_run_assistant_loop_dispatches_a_tool_call_and_loops():
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

    assert client.messages.appels[1]["messages"][-1]["role"] == "user"
    types = [e["type"] for e in evenements]
    assert "texte" in types and "fin" in types


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


def test_run_assistant_loop_stops_at_the_iteration_cap():
    tool_use_block = _FakeBlock("tool_use", id="tu_x", name="fiche_entreprise", input={"ticker": "X"})
    boucle_infinie = _FakeMessage(content=[tool_use_block], stop_reason="tool_use", usage=_FakeUsage(10, 5))
    client = _FakeClient([_FakeStream([], boucle_infinie) for _ in range(assistant.MAX_TOOL_ITERATIONS)])

    evenements = list(assistant.run_assistant_loop(
        "Q", [], client=client, tool_dispatch=lambda name, inp: {"ok": True}))

    assert len(client.messages.appels) == assistant.MAX_TOOL_ITERATIONS
    assert evenements[-1]["type"] == "fin"
    assert any(e["type"] == "texte" and "n'ai pas pu" in e["texte"] for e in evenements)


def test_run_assistant_loop_records_usage_for_every_api_call(monkeypatch):
    enregistres = []
    monkeypatch.setattr(assistant.usage, "record_usage", lambda i, o, model=None: enregistres.append((i, o)))
    final = _FakeMessage(content=[_FakeBlock("text", text="ok")], stop_reason="end_turn", usage=_FakeUsage(111, 22))
    client = _FakeClient([_FakeStream(["ok"], final)])

    list(assistant.run_assistant_loop("Q", [], client=client))

    assert enregistres == [(111, 22)]
