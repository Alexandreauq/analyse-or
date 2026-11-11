import json

import pytest

import assistant_ia.portfolio_analysis as portfolio_analysis


def test_parse_resultat_extracts_the_last_json_block():
    texte = (
        "Du texte de raisonnement avant.\n"
        "```json\n"
        '{"resume_global": "ok", "par_ligne": []}\n'
        "```\n"
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["resume_global"] == "ok"
    assert resultat["par_ligne"] == []


def test_parse_resultat_returns_none_when_no_json_block():
    assert portfolio_analysis._parse_resultat("juste du texte, pas de bloc") is None


def test_parse_resultat_returns_none_on_malformed_json():
    texte = "```json\n{\"resume_global\": \"ok\", \n```"
    assert portfolio_analysis._parse_resultat(texte) is None


def test_parse_resultat_returns_none_when_required_keys_missing():
    texte = '```json\n{"points_attention": []}\n```'
    assert portfolio_analysis._parse_resultat(texte) is None


def test_parse_resultat_fills_in_missing_optional_keys():
    texte = '```json\n{"resume_global": "ok", "par_ligne": []}\n```'
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["comparaison_precedente"] is None
    assert resultat["sante_globale"] is None


def test_parse_resultat_keeps_a_valid_sante_globale():
    texte = '```json\n{"resume_global": "ok", "par_ligne": [], "sante_globale": 42}\n```'
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["sante_globale"] == 42


def test_parse_resultat_replaces_a_non_integer_sante_globale_with_none():
    texte = '```json\n{"resume_global": "ok", "par_ligne": [], "sante_globale": "haute"}\n```'
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["sante_globale"] is None


def test_parse_resultat_replaces_a_boolean_sante_globale_with_none():
    # bool est un sous-type de int en Python -- piege deja connu dans ce
    # fichier (voir les gardes isinstance(x, (int, float)) and not
    # isinstance(x, bool) ailleurs) : True/False ne doivent jamais passer
    # pour un score valide.
    texte = '```json\n{"resume_global": "ok", "par_ligne": [], "sante_globale": true}\n```'
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["sante_globale"] is None


def test_parse_resultat_normalizes_reco_case_and_whitespace():
    texte = (
        '```json\n{"resume_global": "ok", "par_ligne": '
        '[{"ticker": "MC.PA", "reco": " Garder ", "analyse": "x"}]}\n```'
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["par_ligne"][0]["reco"] == "garder"


def test_parse_resultat_replaces_an_invalid_reco_value_with_a_verifier():
    texte = (
        '```json\n{"resume_global": "ok", "par_ligne": '
        '[{"ticker": "MC.PA", "reco": "acheter_plus", "analyse": "x"}]}\n```'
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["par_ligne"][0]["reco"] == "a_verifier"


def test_parse_resultat_removes_non_dict_par_ligne_entries():
    texte = (
        '```json\n{"resume_global": "ok", "par_ligne": '
        '[{"ticker": "MC.PA", "reco": "garder", "analyse": "x"}, "entree parasite"]}\n```'
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["par_ligne"] == [{"ticker": "MC.PA", "reco": "garder", "analyse": "x"}]
    assert "entree parasite" not in resultat["par_ligne"]


def test_parse_resultat_uses_the_last_block_when_several_are_present():
    texte = (
        '```json\n{"resume_global": "premier", "par_ligne": []}\n```\n'
        "texte intermediaire\n"
        '```json\n{"resume_global": "dernier", "par_ligne": []}\n```'
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["resume_global"] == "dernier"


def _indices_source_factory(companies):
    return lambda: {"companies": companies}


def test_enrichit_positions_attaches_the_fiche_for_a_known_ticker():
    companies = [{"ticker": "MC.PA", "name": "LVMH", "index": "CAC40",
                  "sector": "Consumer Cyclical", "score": 42, "alerts": []}]
    positions = [{"ticker": "MC.PA", "quantity": 3, "buy_price": 700.0, "buy_date": "2026-01-01"}]

    enrichies = portfolio_analysis._enrichit_positions(
        positions, indices_source=_indices_source_factory(companies))

    assert enrichies[0]["position"] == positions[0]
    assert enrichies[0]["fiche"]["name"] == "LVMH"


def test_enrichit_positions_keeps_the_position_when_ticker_is_unknown():
    positions = [{"ticker": "INCONNU.XX", "quantity": 1, "buy_price": 10.0, "buy_date": "2026-01-01"}]

    enrichies = portfolio_analysis._enrichit_positions(
        positions, indices_source=_indices_source_factory([]))

    assert enrichies[0]["position"] == positions[0]
    assert "erreur" in enrichies[0]["fiche"]


def test_lit_derniere_analyse_returns_none_when_file_absent(tmp_path):
    chemin = str(tmp_path / "absent.jsonl")
    assert portfolio_analysis._lit_derniere_analyse(chemin) is None


def test_lit_derniere_analyse_returns_the_last_valid_line(tmp_path):
    chemin = tmp_path / "historique.jsonl"
    chemin.write_text(
        json.dumps({"horodatage": "2026-11-01T00:00:00Z", "resultat": {"resume_global": "premiere"}}) + "\n"
        + json.dumps({"horodatage": "2026-11-05T00:00:00Z", "resultat": {"resume_global": "derniere"}}) + "\n",
        encoding="utf-8",
    )
    derniere = portfolio_analysis._lit_derniere_analyse(str(chemin))
    assert derniere["resultat"]["resume_global"] == "derniere"


def test_lit_derniere_analyse_skips_a_corrupted_last_line(tmp_path):
    chemin = tmp_path / "historique.jsonl"
    chemin.write_text(
        json.dumps({"horodatage": "2026-11-01T00:00:00Z", "resultat": {"resume_global": "valide"}}) + "\n"
        + "{ligne corrompue\n",
        encoding="utf-8",
    )
    derniere = portfolio_analysis._lit_derniere_analyse(str(chemin))
    assert derniere["resultat"]["resume_global"] == "valide"


def test_last_analysis_timestamp_returns_none_without_history(tmp_path):
    chemin = str(tmp_path / "absent.jsonl")
    assert portfolio_analysis.last_analysis_timestamp(chemin) is None


def test_last_analysis_timestamp_returns_the_last_horodatage(tmp_path):
    chemin = tmp_path / "historique.jsonl"
    chemin.write_text(
        json.dumps({"horodatage": "2026-11-05T00:00:00Z", "resultat": {}}) + "\n", encoding="utf-8")
    assert portfolio_analysis.last_analysis_timestamp(str(chemin)) == "2026-11-05T00:00:00Z"


def test_append_history_adds_a_line_to_the_file(tmp_path):
    chemin = tmp_path / "historique.jsonl"
    assert portfolio_analysis._append_history({"horodatage": "2026-11-11T00:00:00Z"}, str(chemin)) is True
    contenu = chemin.read_text(encoding="utf-8")
    assert json.loads(contenu.strip())["horodatage"] == "2026-11-11T00:00:00Z"


def test_append_history_returns_false_without_raising_when_write_fails(tmp_path):
    # Un dossier a la place du fichier fait echouer open(..., "a") -- cas
    # improbable en production, mais verifie le contrat "jamais lever".
    chemin = tmp_path / "un_dossier"
    chemin.mkdir()
    assert portfolio_analysis._append_history({"horodatage": "x"}, str(chemin)) is False


def test_build_prompt_includes_each_position_ticker_and_quantity():
    positions_enrichies = [{
        "position": {"ticker": "MC.PA", "quantity": 3, "buy_price": 700.0, "buy_date": "2026-01-01"},
        "fiche": {"name": "LVMH", "index": "CAC40", "sector": "Consumer Cyclical", "score": 42, "alerts": []},
    }]
    prompt = portfolio_analysis._build_prompt(positions_enrichies, {}, None)
    assert "MC.PA" in prompt
    assert "quantite=3" in prompt


def test_build_prompt_marks_unknown_ticker_data_as_unavailable():
    positions_enrichies = [{
        "position": {"ticker": "INCONNU.XX", "quantity": 1, "buy_price": 10.0, "buy_date": "2026-01-01"},
        "fiche": {"erreur": "ticker introuvable"},
    }]
    prompt = portfolio_analysis._build_prompt(positions_enrichies, {}, None)
    assert "donnee indice indisponible" in prompt


def test_build_prompt_tells_the_model_to_output_null_comparison_without_history():
    prompt = portfolio_analysis._build_prompt([], {}, None)
    assert "comparaison_precedente" in prompt
    assert "null" in prompt


def test_build_prompt_includes_the_previous_summary_when_history_exists():
    derniere_analyse = {
        "horodatage": "2026-11-05T00:00:00Z",
        "resultat": {"resume_global": "portefeuille equilibre, aucune alerte"},
    }
    prompt = portfolio_analysis._build_prompt([], {}, derniere_analyse)
    assert "portefeuille equilibre, aucune alerte" in prompt


def test_build_prompt_requests_a_json_code_block():
    prompt = portfolio_analysis._build_prompt([], {}, None)
    assert "```json" in prompt
    assert '"par_ligne"' in prompt


def test_build_prompt_requests_sante_globale_and_analyse_not_justification():
    prompt = portfolio_analysis._build_prompt([], {}, None)
    assert "sante_globale" in prompt
    assert '"analyse"' in prompt
    assert '"justification"' not in prompt
    assert '"points_attention"' not in prompt


def test_build_prompt_includes_attribution_pnl_per_position():
    metriques = {"attribution": [
        {"ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "pnlAbs": 123.456, "pnlPct": 5.67},
        {"ticker": "AAPL", "name": "Apple", "index": "SP500", "pnlAbs": -40.0, "pnlPct": -2.04},
    ]}
    prompt = portfolio_analysis._build_prompt([], metriques, None)
    assert "Attribution de performance : MC.PA : P&L +123.46 (+5.7%)" in prompt
    assert "Attribution de performance : AAPL : P&L -40.00 (-2.0%)" in prompt


def test_build_prompt_includes_the_current_price_when_the_fiche_has_one():
    positions_enrichies = [{
        "position": {"ticker": "MC.PA", "quantity": 3, "buy_price": 700.0, "buy_date": "2026-01-01"},
        "fiche": {"name": "LVMH", "index": "CAC40", "sector": "Consumer Cyclical", "score": 42,
                  "alerts": [], "current_price": 655.4},
    }]
    prompt = portfolio_analysis._build_prompt(positions_enrichies, {}, None)
    assert "prix actuel=655.4" in prompt


def test_build_prompt_omits_the_current_price_when_the_fiche_has_none():
    positions_enrichies = [{
        "position": {"ticker": "MC.PA", "quantity": 3, "buy_price": 700.0, "buy_date": "2026-01-01"},
        "fiche": {"name": "LVMH", "index": "CAC40", "sector": "Consumer Cyclical", "score": 42,
                  "alerts": [], "current_price": None},
    }]
    prompt = portfolio_analysis._build_prompt(positions_enrichies, {}, None)
    assert "prix actuel=" not in prompt


def test_build_prompt_treats_an_info_only_alert_as_no_active_alert():
    positions_enrichies = [{
        "position": {"ticker": "MC.PA", "quantity": 3, "buy_price": 700.0, "buy_date": "2026-01-01"},
        "fiche": {"name": "LVMH", "index": "CAC40", "sector": "Consumer Cyclical", "score": 42,
                  "alerts": [{"kind": "info", "title": "Pas de signal actif"}]},
    }]
    prompt = portfolio_analysis._build_prompt(positions_enrichies, {}, None)
    assert "alertes actives=aucune" in prompt
    assert "alertes actives=info" not in prompt


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=200):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage = usage or _FakeUsage()


class _FakeMessages:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response=None, error=None):
        self.messages = _FakeMessages(response=response, error=error)


_PAYLOAD_SIMPLE = {
    "positions": [{"ticker": "MC.PA", "quantity": 3, "buy_price": 700.0, "buy_date": "2026-01-01"}],
    "totals": {}, "concentration": {}, "health": {}, "attribution": [],
    "dividends_received": {}, "projected_dividend_income": {},
}


def _reponse_valide(resume="ok"):
    texte = (
        f'```json\n{{"resume_global": "{resume}", "par_ligne": '
        '[{"ticker": "MC.PA", "reco": "garder", "justification": "stable"}]}\n```'
    )
    return _FakeResponse([_FakeTextBlock(texte)])


def test_run_portfolio_analysis_returns_disponible_true_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage", lambda *a, **k: None)
    client = _FakeAnthropicClient(response=_reponse_valide())
    historique = str(tmp_path / "historique.jsonl")

    resultat = portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=lambda: {"companies": []},
        history_path=historique)

    assert resultat["disponible"] is True
    assert resultat["resultat"]["resume_global"] == "ok"
    assert resultat["resultat"]["horodatage"]


def test_run_portfolio_analysis_appends_to_history_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage", lambda *a, **k: None)
    client = _FakeAnthropicClient(response=_reponse_valide())
    historique = str(tmp_path / "historique.jsonl")

    portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=lambda: {"companies": []},
        history_path=historique)

    assert portfolio_analysis.last_analysis_timestamp(historique) is not None


def test_run_portfolio_analysis_returns_disponible_false_on_api_error(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage", lambda *a, **k: None)
    client = _FakeAnthropicClient(error=RuntimeError("timeout"))
    historique = str(tmp_path / "historique.jsonl")

    resultat = portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=lambda: {"companies": []},
        history_path=historique)

    assert resultat["disponible"] is False
    assert "RuntimeError" in resultat["erreur"]
    assert portfolio_analysis.last_analysis_timestamp(historique) is None


def test_run_portfolio_analysis_returns_disponible_false_when_response_not_parseable(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage", lambda *a, **k: None)
    client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock("pas de JSON ici")]))
    historique = str(tmp_path / "historique.jsonl")

    resultat = portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=lambda: {"companies": []},
        history_path=historique)

    assert resultat["disponible"] is False
    assert portfolio_analysis.last_analysis_timestamp(historique) is None


def test_run_portfolio_analysis_includes_previous_summary_in_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage", lambda *a, **k: None)
    historique = tmp_path / "historique.jsonl"
    historique.write_text(
        json.dumps({"horodatage": "2026-11-05T00:00:00Z",
                    "resultat": {"resume_global": "analyse precedente unique"}}) + "\n",
        encoding="utf-8")
    client = _FakeAnthropicClient(response=_reponse_valide())

    portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=lambda: {"companies": []},
        history_path=str(historique))

    prompt_envoye = client.messages.last_kwargs["messages"][0]["content"]
    assert "analyse precedente unique" in prompt_envoye


def test_run_portfolio_analysis_calls_record_usage_with_the_response_tokens(tmp_path, monkeypatch):
    enregistres = []
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage",
                         lambda i, o, model=None: enregistres.append((i, o, model)))
    client = _FakeAnthropicClient(response=_reponse_valide())
    historique = str(tmp_path / "historique.jsonl")

    portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=lambda: {"companies": []},
        history_path=historique)

    assert enregistres == [(100, 200, portfolio_analysis.MODEL)]


def test_run_portfolio_analysis_returns_disponible_false_when_indices_source_raises(tmp_path, monkeypatch):
    # Panne reseau sur indices.json : l'erreur doit etre capturee ici (contrat
    # "jamais propagee"), pas remontee a la route FastAPI.
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage", lambda *a, **k: None)
    client = _FakeAnthropicClient(response=_reponse_valide())
    historique = str(tmp_path / "historique.jsonl")

    def indices_en_panne():
        raise RuntimeError("panne reseau")

    resultat = portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=indices_en_panne,
        history_path=historique)

    assert resultat["disponible"] is False
    assert resultat["erreur"] == "RuntimeError: panne reseau"
    assert resultat["resultat"] is None
    assert client.messages.last_kwargs is None
    assert portfolio_analysis.last_analysis_timestamp(historique) is None
