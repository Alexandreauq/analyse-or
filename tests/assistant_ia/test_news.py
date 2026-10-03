# tests/assistant_ia/test_news.py
from datetime import datetime, timezone

import assistant_ia.news as news

MAINTENANT = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)


def _article(url, titre, published_at, entities=None, description="Resume."):
    return {
        "url": url, "title": titre, "published_at": published_at,
        "source": "Source X", "description": description,
        "entities": entities or [],
    }


def _entite(symbol, score):
    return {"symbol": symbol, "match_score": score}


def test_params_entreprise_contains_symbol_window_and_score_threshold():
    params = news.params_entreprise("MC.PA", MAINTENANT)

    assert params == {
        "symbols": "MC.PA",
        "published_after": "2026-10-01T12:00:00",  # 48 h avant
        "min_match_score": news.MIN_MATCH_SCORE,
    }


def test_params_marche_contains_countries_and_24h_window():
    params = news.params_marche(MAINTENANT)

    assert params == {
        "countries": news.PAYS_UNIVERS,
        "published_after": "2026-10-02T12:00:00",  # 24 h avant
    }


def test_normalise_keeps_only_articles_whose_entity_matches_the_ticker_above_threshold():
    payload = {"data": [
        _article("https://a.test/1", "LVMH bondit", "2026-10-03T08:00:00Z",
                 entities=[_entite("MC.PA", 9.0)]),
        _article("https://a.test/2", "Autre sujet", "2026-10-03T07:00:00Z",
                 entities=[_entite("MC.PA", 2.0)]),  # score trop faible
        _article("https://a.test/3", "Pas LVMH", "2026-10-03T06:00:00Z",
                 entities=[_entite("KER.PA", 9.0)]),  # mauvais ticker
    ]}

    articles = news.normalise(payload, ticker="MC.PA")

    assert [a["url"] for a in articles] == ["https://a.test/1"]


def test_normalise_deduplicates_by_url_and_by_normalised_title():
    payload = {"data": [
        _article("https://a.test/1", "LVMH bondit !", "2026-10-03T08:00:00Z", entities=[_entite("MC.PA", 9.0)]),
        _article("https://a.test/1", "LVMH bondit !", "2026-10-03T08:00:00Z", entities=[_entite("MC.PA", 9.0)]),
        _article("https://b.test/9", "LVMH bondit", "2026-10-03T07:00:00Z", entities=[_entite("MC.PA", 9.0)]),
    ]}

    articles = news.normalise(payload, ticker="MC.PA")

    assert len(articles) == 1


def test_normalise_sorts_newest_first_and_caps_at_five_articles():
    payload = {"data": [
        _article(f"https://a.test/{i}", f"Titre unique {i}", f"2026-10-03T0{i}:00:00Z",
                 entities=[_entite("MC.PA", 9.0)])
        for i in range(1, 8)
    ]}

    articles = news.normalise(payload, ticker="MC.PA")

    assert len(articles) == news.MAX_ARTICLES
    assert articles[0]["url"] == "https://a.test/7"  # le plus récent


def test_normalise_truncates_the_summary_to_300_characters():
    long = "x" * 1000
    payload = {"data": [
        _article("https://a.test/1", "Titre", "2026-10-03T08:00:00Z",
                 entities=[_entite("MC.PA", 9.0)], description=long),
    ]}

    articles = news.normalise(payload, ticker="MC.PA")

    assert len(articles[0]["resume_court"]) == news.RESUME_MAX_CHARS


def test_normalise_drops_non_http_urls():
    payload = {"data": [
        _article("javascript:alert(1)", "Titre", "2026-10-03T08:00:00Z", entities=[_entite("MC.PA", 9.0)]),
        _article("https://a.test/ok", "Titre bis", "2026-10-03T07:00:00Z", entities=[_entite("MC.PA", 9.0)]),
    ]}

    articles = news.normalise(payload, ticker="MC.PA")

    assert [a["url"] for a in articles] == ["https://a.test/ok"]


def test_normalise_market_mode_does_not_require_an_entity_match():
    payload = {"data": [
        _article("https://a.test/m", "La BCE maintient ses taux", "2026-10-03T08:00:00Z", entities=[]),
    ]}

    articles = news.normalise(payload, ticker=None)

    assert len(articles) == 1


def test_normalise_survives_non_string_fields():
    payload = {"data": [
        {"url": "https://a.example/1", "title": 123, "description": "x",
         "published_at": "2026-10-03T10:00:00"},
        {"url": "https://a.example/2", "title": "Titre valide",
         "published_at": "2026-10-03T09:00:00"},
    ]}

    resultat = news.normalise(payload)

    assert [a["titre"] for a in resultat] == ["Titre valide"]

    payload_description = {"data": [
        {"url": "https://a.example/3", "title": "Autre titre", "description": ["x"]},
    ]}

    resultat_description = news.normalise(payload_description)

    assert resultat_description[0]["resume_court"] == ""


def test_normalise_degrades_to_empty_list_on_malformed_payload():
    assert news.normalise(None, ticker="MC.PA") == []
    assert news.normalise({"data": "pas une liste"}, ticker="MC.PA") == []


def test_actualites_entreprise_returns_articles_for_the_ticker():
    appels = []

    def fake_source(params, ttl_seconds, cache_key):
        appels.append((params, ttl_seconds, cache_key))
        return {"data": [_article("https://a.test/1", "LVMH bondit", "2026-10-03T08:00:00Z",
                                  entities=[_entite("MC.PA", 9.0)])]}

    resultat = news.actualites_entreprise("MC.PA", news_source=fake_source, now=MAINTENANT)

    assert resultat["ticker"] == "MC.PA"
    assert len(resultat["articles"]) == 1
    assert appels[0][1] == news.TTL_ENTREPRISE_SECONDS
    assert appels[0][2] == "entreprise:MC.PA"


def test_actualites_entreprise_propagates_a_source_error():
    resultat = news.actualites_entreprise(
        "MC.PA", news_source=lambda p, t, k: {"erreur": "indisponible"}, now=MAINTENANT)

    assert resultat == {"erreur": "indisponible"}


def test_actualites_marche_uses_the_market_ttl_and_no_symbol():
    appels = []

    def fake_source(params, ttl_seconds, cache_key):
        appels.append((params, ttl_seconds, cache_key))
        return {"data": []}

    resultat = news.actualites_marche(news_source=fake_source, now=MAINTENANT)

    assert resultat == {"articles": []}
    assert appels[0][1] == news.TTL_MARCHE_SECONDS
    assert "symbols" not in appels[0][0]
    assert appels[0][2] == "marche"
