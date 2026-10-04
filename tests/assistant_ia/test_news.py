# tests/assistant_ia/test_news.py
from datetime import datetime, timedelta, timezone

import assistant_ia.news as news

MAINTENANT = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)


def _unix(dt):
    return int(dt.timestamp())


def _fin(url, headline, quand, related="AAPL", summary="Resume."):
    return {"url": url, "headline": headline, "datetime": _unix(quand),
            "related": related, "source": "Reuters", "summary": summary}


def _gdelt(url, titre, quand, domain="lemonde.fr"):
    return {"url": url, "title": titre, "seendate": quand.strftime("%Y%m%dT%H%M%SZ"),
            "domain": domain}


# --- Finnhub -----------------------------------------------------------------

def test_articles_finnhub_keeps_only_articles_tagged_with_the_symbol():
    payload = [
        _fin("https://a.com/1", "Apple publie", MAINTENANT, related="AAPL,MSFT"),
        _fin("https://a.com/2", "Autre sujet", MAINTENANT, related="NVDA"),
    ]

    titres = [a["titre"] for a in news.articles_finnhub(payload, "AAPL")]

    assert titres == ["Apple publie"]


def test_articles_finnhub_drops_non_http_urls_and_missing_dates():
    payload = [
        _fin("javascript:alert(1)", "Lien piégé", MAINTENANT),
        {"url": "https://a.com/3", "headline": "Sans date", "related": "AAPL"},
    ]

    assert news.articles_finnhub(payload, "AAPL") == []


def test_articles_finnhub_degrades_on_malformed_payload():
    assert news.articles_finnhub({"erreur": "x"}, "AAPL") == []
    assert news.articles_finnhub(["pas", "un", "dict"], "AAPL") == []


# --- GDELT -------------------------------------------------------------------

def test_articles_gdelt_for_a_company_requires_the_name_in_the_title():
    payload = {"articles": [
        _gdelt("https://a.com/1", "Safran signe un contrat", MAINTENANT),
        _gdelt("https://a.com/2", "Airbus livre ses avions", MAINTENANT),
    ]}

    titres = [a["titre"] for a in news.articles_gdelt(payload, nom="Safran")]

    assert titres == ["Safran signe un contrat"]


def test_articles_gdelt_market_mode_keeps_every_title():
    payload = {"articles": [_gdelt("https://a.com/1", "Or en hausse", MAINTENANT)]}

    assert len(news.articles_gdelt(payload)) == 1


def test_articles_gdelt_survives_non_string_fields_and_bad_dates():
    payload = {"articles": [
        {"url": "https://a.com/1", "title": 42, "seendate": "20261003T120000Z"},
        {"url": "https://a.com/2", "title": "Titre", "seendate": "pas une date"},
        "pas un dict",
    ]}

    assert news.articles_gdelt(payload) == []


def test_articles_gdelt_degrades_on_malformed_payload():
    assert news.articles_gdelt({"articles": "x"}) == []
    assert news.articles_gdelt(None) == []


# --- Fusion ------------------------------------------------------------------

def test_fusionne_drops_articles_older_than_the_window():
    vieux = news.articles_gdelt({"articles": [
        _gdelt("https://a.com/vieux", "Ancien", MAINTENANT - timedelta(hours=60))]})

    assert news.fusionne([vieux], MAINTENANT - timedelta(hours=48)) == []


def test_fusionne_deduplicates_across_sources_by_url_and_normalised_title():
    finnhub = news.articles_finnhub([_fin("https://a.com/1", "Apple publie", MAINTENANT)], "AAPL")
    gdelt = news.articles_gdelt({"articles": [
        _gdelt("https://a.com/1", "Apple publie", MAINTENANT),
        _gdelt("https://b.com/2", "  APPLE publie !! ", MAINTENANT),
        _gdelt("https://c.com/3", "Autre", MAINTENANT),
    ]}, nom=None)

    titres = [a["titre"] for a in news.fusionne([finnhub, gdelt], MAINTENANT - timedelta(hours=48))]

    assert len(titres) == 2
    assert set(titres) == {"Apple publie", "Autre"}


def test_fusionne_sorts_newest_first_and_caps_at_five_articles():
    articles = news.articles_gdelt({"articles": [
        _gdelt(f"https://a.com/{i}", f"Titre numéro {i}", MAINTENANT - timedelta(minutes=i))
        for i in range(8)
    ]})

    retenus = news.fusionne([articles], MAINTENANT - timedelta(hours=1))

    assert len(retenus) == news.MAX_ARTICLES
    assert retenus[0]["titre"] == "Titre numéro 0"


def test_fusionne_truncates_the_summary_to_300_characters():
    articles = news.articles_finnhub(
        [_fin("https://a.com/1", "Titre", MAINTENANT, summary="x" * 500)], "AAPL")

    assert len(news.fusionne([articles], MAINTENANT - timedelta(hours=48))[0]["resume_court"]) == 300


# --- Fonctions publiques -----------------------------------------------------

def test_actualites_entreprise_combines_finnhub_and_gdelt_for_a_us_ticker():
    finnhub = lambda sym, debut, fin: [_fin("https://a.com/1", "Apple publie", MAINTENANT)]
    gdelt = lambda q, debut, ttl, cle: {"articles": [
        _gdelt("https://b.com/2", "Apple Inc. sous pression", MAINTENANT)]}

    resultat = news.actualites_entreprise(
        "AAPL", "Apple", finnhub_source=finnhub, gdelt_source=gdelt, now=MAINTENANT)

    assert resultat["ticker"] == "AAPL"
    assert {a["titre"] for a in resultat["articles"]} == {"Apple publie", "Apple Inc. sous pression"}


def test_actualites_entreprise_skips_finnhub_for_a_european_ticker():
    appels_finnhub = []

    def finnhub(*args):
        appels_finnhub.append(args)
        return []

    gdelt = lambda q, debut, ttl, cle: {"articles": [
        _gdelt("https://a.com/1", "LVMH record", MAINTENANT)]}

    resultat = news.actualites_entreprise(
        "MC.PA", "LVMH", finnhub_source=finnhub, gdelt_source=gdelt, now=MAINTENANT)

    assert appels_finnhub == []
    assert [a["titre"] for a in resultat["articles"]] == ["LVMH record"]


def test_actualites_entreprise_skips_gdelt_when_the_name_is_unknown():
    gdelt_appels = []

    def gdelt(*args):
        gdelt_appels.append(args)
        return {"articles": []}

    news.actualites_entreprise("MC.PA", None, finnhub_source=lambda *a: [],
                               gdelt_source=gdelt, now=MAINTENANT)

    assert gdelt_appels == []


def test_actualites_entreprise_returns_the_error_when_every_source_fails():
    erreur = {"erreur": "actualites indisponibles (RuntimeError)"}

    resultat = news.actualites_entreprise(
        "MC.PA", "LVMH", finnhub_source=lambda *a: erreur,
        gdelt_source=lambda *a: erreur, now=MAINTENANT)

    assert resultat == erreur


def test_actualites_entreprise_keeps_partial_results_when_one_source_fails():
    erreur = {"erreur": "actualites indisponibles (RuntimeError)"}
    gdelt = lambda q, debut, ttl, cle: {"articles": [
        _gdelt("https://a.com/1", "LVMH record", MAINTENANT)]}

    resultat = news.actualites_entreprise(
        "AAPL", "LVMH", finnhub_source=lambda *a: erreur,
        gdelt_source=gdelt, now=MAINTENANT)

    assert [a["titre"] for a in resultat["articles"]] == ["LVMH record"]


def test_actualites_marche_uses_the_24h_window_and_the_market_query():
    appels = []

    def gdelt(requete, debut, ttl, cle):
        appels.append((requete, debut, ttl, cle))
        return {"articles": []}

    resultat = news.actualites_marche(gdelt_source=gdelt, now=MAINTENANT)

    requete, debut, ttl, cle = appels[0]
    assert requete == news.REQUETE_MARCHE
    assert debut == MAINTENANT - timedelta(hours=24)
    assert ttl == news.TTL_MARCHE_SECONDS
    assert resultat == {"articles": []}


def test_actualites_marche_propagates_a_source_error():
    erreur = {"erreur": "actualites indisponibles (RuntimeError)"}

    assert news.actualites_marche(gdelt_source=lambda *a: erreur, now=MAINTENANT) == erreur
