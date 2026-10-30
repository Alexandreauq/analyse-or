import pytest
from unittest.mock import patch, MagicMock

import commodity_news
from commodity_news import (
    summarize_commodity_news_item,
    fetch_commodity_news,
    build_factors_summary,
    build_news_summary,
    generate_commodity_analysis,
    build_news_and_analysis_section,
)
from gold_score import FactorResult


@pytest.fixture(autouse=True)
def _no_real_indices_ai_usage_writes(monkeypatch):
    """Meme garde-fou que tests/test_indices_score.py -- commodity_news.py
    importe _record_indices_ai_usage/_indices_ai_budget_exceeded depuis
    indices_score.py (from-import, liaison propre a ce module), donc un
    monkeypatch cote indices_score ne suffirait pas ici : il faut patcher
    les noms tels que lies dans commodity_news lui-meme."""
    monkeypatch.setattr(commodity_news, "_record_indices_ai_usage", lambda *a, **k: None)
    monkeypatch.setattr(commodity_news, "_indices_ai_budget_exceeded", lambda *a, **k: False)


def _fake_anthropic_response(parsed_json_text):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"content": [{"type": "text", "text": parsed_json_text}]}
    return resp


def test_summarize_commodity_news_item_returns_neutral_fallback_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = summarize_commodity_news_item("Titre test", "l'or", "contenu")
    assert result == {"summary": "", "sentiment": 0, "importance": "mineure"}


def test_summarize_commodity_news_item_parses_valid_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    payload = '{"summary": "Résumé test", "sentiment": 1, "importance": "notable"}'
    with patch("commodity_news.requests.post", return_value=_fake_anthropic_response(payload)):
        result = summarize_commodity_news_item("Titre", "l'or", "contenu")
    assert result == {"summary": "Résumé test", "sentiment": 1, "importance": "notable"}


def test_summarize_commodity_news_item_falls_back_to_mineure_on_bad_importance(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    payload = '{"summary": "x", "sentiment": 0, "importance": "catastrophique"}'
    with patch("commodity_news.requests.post", return_value=_fake_anthropic_response(payload)):
        result = summarize_commodity_news_item("Titre", "l'or", "contenu")
    assert result["importance"] == "mineure"


def test_summarize_commodity_news_item_never_raises_on_network_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    with patch("commodity_news.requests.post", side_effect=Exception("network down")):
        result = summarize_commodity_news_item("Titre", "l'or", None)
    assert result == {"summary": "", "sentiment": 0, "importance": "mineure"}


def test_summarize_commodity_news_item_skips_the_call_when_budget_is_exceeded(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(commodity_news, "_indices_ai_budget_exceeded", lambda *a, **k: True)
    with patch("commodity_news.requests.post") as mock_post:
        result = summarize_commodity_news_item("Titre", "l'or", "contenu")
    mock_post.assert_not_called()
    assert result == {"summary": "", "sentiment": 0, "importance": "mineure"}


def test_fetch_commodity_news_reuses_cached_classification(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    rss_resp = MagicMock()
    rss_resp.raise_for_status.return_value = None
    rss_resp.content = b"<rss></rss>"
    parsed_items = [{"title": "T1", "date": "2026-10-25", "link": "https://x/1", "source": "S"}]
    previous = {"https://x/1": {"summary": "déjà vu", "sentiment": 1, "importance": "notable"}}
    with patch("commodity_news.requests.get", return_value=rss_resp), \
         patch("commodity_news.parse_news_rss", return_value=parsed_items), \
         patch("commodity_news.summarize_commodity_news_item") as mock_summarize:
        result = fetch_commodity_news("cours de l'or", "l'or", previous)
    mock_summarize.assert_not_called()
    assert result[0]["summary"] == "déjà vu"
    assert result[0]["sentiment"] == 1
    assert result[0]["importance"] == "notable"


def test_fetch_commodity_news_classifies_new_article(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    rss_resp = MagicMock()
    rss_resp.raise_for_status.return_value = None
    rss_resp.content = b"<rss></rss>"
    parsed_items = [{"title": "T2", "date": "2026-10-25", "link": "https://x/2", "source": "S"}]
    with patch("commodity_news.requests.get", return_value=rss_resp), \
         patch("commodity_news.parse_news_rss", return_value=parsed_items), \
         patch("commodity_news.fetch_article_text", return_value="texte"), \
         patch("commodity_news.summarize_commodity_news_item",
               return_value={"summary": "nouveau", "sentiment": -1, "importance": "majeure"}) as mock_summarize:
        result = fetch_commodity_news("cours de l'or", "l'or", {})
    mock_summarize.assert_called_once_with("T2", "l'or", "texte")
    assert result[0]["summary"] == "nouveau"
    assert result[0]["importance"] == "majeure"


def test_build_factors_summary_formats_each_factor():
    factors = [
        FactorResult("Taux réels US 10 ans", -2.0, 0.30, "2.64% (il y a ~45j : 2.55%)"),
        FactorResult("Dollar (DXY)", -10.0, 0.15, "101.81 (il y a 2 mois : 98.92)"),
    ]
    out = build_factors_summary(factors)
    assert "Taux réels US 10 ans" in out
    assert "poids 30%" in out
    assert "-10.0" in out or "-10,0" in out or "score -10.0" in out


def test_build_factors_summary_skips_malformed_factor_without_raising():
    class Broken:
        name = "Cassé"
    out = build_factors_summary([Broken()])
    assert "Aucun facteur disponible." in out or out == ""


def test_build_news_summary_formats_each_item_with_importance():
    news = [{"title": "Titre 1", "summary": "Résumé 1", "importance": "majeure"}]
    out = build_news_summary(news)
    assert "[majeure]" in out
    assert "Titre 1" in out
    assert "Résumé 1" in out


def test_build_news_summary_handles_empty_list():
    out = build_news_summary([])
    assert "Aucune actualité récente disponible." in out


def test_generate_commodity_analysis_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert generate_commodity_analysis("l'or", "facteurs", "actus") is None


def test_generate_commodity_analysis_skips_the_call_when_budget_is_exceeded(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(commodity_news, "_indices_ai_budget_exceeded", lambda *a, **k: True)
    with patch("commodity_news.requests.post") as mock_post:
        result = generate_commodity_analysis("l'or", "facteurs", "actus")
    mock_post.assert_not_called()
    assert result is None


def test_generate_commodity_analysis_parses_valid_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    payload = '{"analysis_html": "<h3>Contexte</h3><p>Test</p>"}'
    with patch("commodity_news.requests.post", return_value=_fake_anthropic_response(payload)):
        result = generate_commodity_analysis("l'or", "facteurs", "actus")
    assert result == "<h3>Contexte</h3><p>Test</p>"


def test_generate_commodity_analysis_returns_none_on_network_error(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    with patch("commodity_news.requests.post", side_effect=Exception("boom")):
        result = generate_commodity_analysis("l'or", "facteurs", "actus")
    assert result is None
    assert "l'or" in capsys.readouterr().out


def test_build_news_and_analysis_section_skips_network_on_non_daily_run():
    previous_payload = {
        "news": [{"title": "ancien"}],
        "analysis_html": "<p>ancienne analyse</p>",
        "news_classifications": {"https://x/1": {"summary": "s", "sentiment": 0, "importance": "mineure"}},
    }
    with patch("commodity_news.fetch_commodity_news") as mock_fetch, \
         patch("commodity_news.generate_commodity_analysis") as mock_generate:
        news, analysis_html, classifications = build_news_and_analysis_section(
            "l'or", "cours de l'or", [], daily_snapshot=False, previous_payload=previous_payload,
        )
    mock_fetch.assert_not_called()
    mock_generate.assert_not_called()
    assert news == previous_payload["news"]
    assert analysis_html == previous_payload["analysis_html"]
    assert classifications == previous_payload["news_classifications"]


def test_build_news_and_analysis_section_fetches_on_daily_run():
    fake_news = [{"title": "T", "link": "https://x/1", "summary": "s", "sentiment": 1, "importance": "notable"}]
    with patch("commodity_news.fetch_commodity_news", return_value=fake_news) as mock_fetch, \
         patch("commodity_news.generate_commodity_analysis", return_value="<p>analyse</p>") as mock_generate:
        news, analysis_html, classifications = build_news_and_analysis_section(
            "l'or", "cours de l'or", [], daily_snapshot=True, previous_payload={},
        )
    mock_fetch.assert_called_once_with("cours de l'or", "l'or", {})
    assert news == fake_news
    assert analysis_html == "<p>analyse</p>"
    assert classifications == {"https://x/1": {"summary": "s", "sentiment": 1, "importance": "notable"}}


def test_build_news_and_analysis_section_carries_forward_analysis_on_generation_failure():
    fake_news = [{"title": "T", "link": "https://x/1", "summary": "s", "sentiment": 0, "importance": "mineure"}]
    previous_payload = {"analysis_html": "<p>ancienne analyse valide</p>"}
    with patch("commodity_news.fetch_commodity_news", return_value=fake_news), \
         patch("commodity_news.generate_commodity_analysis", return_value=None):
        _, analysis_html, _ = build_news_and_analysis_section(
            "l'or", "cours de l'or", [], daily_snapshot=True, previous_payload=previous_payload,
        )
    assert analysis_html == "<p>ancienne analyse valide</p>"


def test_build_news_and_analysis_section_falls_back_to_previous_on_fetch_error(capsys):
    previous_payload = {
        "news": [{"title": "ancien"}],
        "analysis_html": "<p>ancienne analyse</p>",
        "news_classifications": {"https://x/1": {"summary": "s", "sentiment": 0, "importance": "mineure"}},
    }
    with patch("commodity_news.fetch_commodity_news", side_effect=Exception("rss down")) as mock_fetch, \
         patch("commodity_news.generate_commodity_analysis") as mock_generate:
        news, analysis_html, classifications = build_news_and_analysis_section(
            "l'or", "cours de l'or", [], daily_snapshot=True, previous_payload=previous_payload,
        )
    mock_fetch.assert_called_once()
    mock_generate.assert_not_called()
    assert news == previous_payload["news"]
    assert analysis_html == previous_payload["analysis_html"]
    assert classifications == previous_payload["news_classifications"]
    assert "l'or" in capsys.readouterr().out


def test_build_news_and_analysis_section_passes_previous_classifications_to_fetch():
    previous_payload = {"news_classifications": {"https://x/1": {"summary": "s", "sentiment": 0, "importance": "mineure"}}}
    with patch("commodity_news.fetch_commodity_news", return_value=[]) as mock_fetch, \
         patch("commodity_news.generate_commodity_analysis", return_value=None):
        build_news_and_analysis_section(
            "l'or", "cours de l'or", [], daily_snapshot=True, previous_payload=previous_payload,
        )
    mock_fetch.assert_called_once_with("cours de l'or", "l'or", previous_payload["news_classifications"])
