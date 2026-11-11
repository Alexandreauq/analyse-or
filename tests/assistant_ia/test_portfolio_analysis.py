import json

import pytest

import assistant_ia.portfolio_analysis as portfolio_analysis


def test_parse_resultat_extracts_the_last_json_block():
    texte = (
        "Du texte de raisonnement avant.\n"
        "```json\n"
        '{"resume_global": "ok", "par_ligne": [], "points_attention": []}\n'
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
    assert resultat["points_attention"] == []
    assert resultat["comparaison_precedente"] is None


def test_parse_resultat_normalizes_reco_case_and_whitespace():
    texte = (
        '```json\n{"resume_global": "ok", "par_ligne": '
        '[{"ticker": "MC.PA", "reco": " Garder ", "justification": "x"}]}\n```'
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["par_ligne"][0]["reco"] == "garder"


def test_parse_resultat_replaces_an_invalid_reco_value_with_a_verifier():
    texte = (
        '```json\n{"resume_global": "ok", "par_ligne": '
        '[{"ticker": "MC.PA", "reco": "acheter_plus", "justification": "x"}]}\n```'
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["par_ligne"][0]["reco"] == "a_verifier"


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
