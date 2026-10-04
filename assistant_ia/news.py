# assistant_ia/news.py
# Actualités pour l'assistant IA (spec 2026-10-03-ai-news-context-design.md).
# Deux sources gratuites, normalisées ici en un même format d'article :
# - Finnhub : actualités rattachées à un ticker américain (clé requise) ;
# - GDELT : articles de presse mondiale, recherchés par mots-clés (sans clé).
# Règles de fiabilité : fraîcheur (48 h entreprise, 24 h marché), pertinence
# (Finnhub : le symbole doit figurer dans les tickers de l'article ; GDELT
# pour une entreprise : son nom doit figurer dans le titre), déduplication,
# plafond, URLs http(s) seulement. Aucune lecture réseau directe : les
# sources sont injectables.
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import assistant_ia.data_sources as data_sources

MAX_ARTICLES = 5
RESUME_MAX_CHARS = 300
TTL_ENTREPRISE_SECONDS = 2 * 3600
TTL_MARCHE_SECONDS = 3600
FENETRE_ENTREPRISE = timedelta(hours=48)
FENETRE_MARCHE = timedelta(hours=24)
REQUETE_MARCHE = (
    '(gold OR "gold price" OR "Federal Reserve" OR "interest rates" '
    'OR inflation OR "stock market")'
)


def _url_ok(url) -> bool:
    if not isinstance(url, str):
        return False
    analyse = urlparse(url)
    return analyse.scheme in ("http", "https") and bool(analyse.netloc)


def _texte(article: dict, cle: str) -> str:
    valeur = article.get(cle)
    return valeur.strip() if isinstance(valeur, str) else ""


def _date_unix(valeur) -> datetime | None:
    if isinstance(valeur, (int, float)) and not isinstance(valeur, bool):
        return datetime.fromtimestamp(valeur, timezone.utc)
    return None


def _date_gdelt(valeur) -> datetime | None:
    # Format GDELT : "20261004T120000Z"
    if not isinstance(valeur, str):
        return None
    try:
        return datetime.strptime(valeur, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _article(titre: str, source: str, date: datetime, resume: str, url: str) -> dict:
    return {
        "titre": titre, "source": source, "date": date,
        "resume_court": resume[:RESUME_MAX_CHARS], "url": url,
    }


def articles_finnhub(payload, symbole: str) -> list[dict]:
    """Articles Finnhub (liste JSON) dont le champ `related` contient le symbole."""
    if not isinstance(payload, list):
        return []
    retenus = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        titre = _texte(item, "headline")
        date = _date_unix(item.get("datetime"))
        tickers = {t.strip().upper() for t in _texte(item, "related").split(",")}
        if not _url_ok(url) or not titre or date is None or symbole.upper() not in tickers:
            continue
        retenus.append(_article(titre, _texte(item, "source"), date, _texte(item, "summary"), url))
    return retenus


def articles_gdelt(payload, nom: str | None = None) -> list[dict]:
    """Articles GDELT (`{"articles": [...]}`). Si `nom` est fourni (entreprise),
    seuls les titres qui le citent sont gardés. GDELT ne fournit pas de résumé."""
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        return []
    retenus = []
    for item in payload["articles"]:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        titre = _texte(item, "title")
        date = _date_gdelt(item.get("seendate"))
        if not _url_ok(url) or not titre or date is None:
            continue
        if nom is not None and nom.lower() not in titre.lower():
            continue
        retenus.append(_article(titre, _texte(item, "domain"), date, "", url))
    return retenus


def fusionne(listes: list[list[dict]], debut: datetime) -> list[dict]:
    """Fusionne plusieurs sources : garde ce qui est depuis `debut`, supprime
    les doublons (URL ou titre normalisé), trie du plus récent au plus ancien,
    plafonne. Renvoie le format public (date en ISO, sans objet datetime)."""
    vus_urls: set[str] = set()
    vus_titres: set[str] = set()
    tous = [a for liste in listes for a in liste if a["date"] >= debut]
    tous.sort(key=lambda a: a["date"], reverse=True)
    retenus = []
    for a in tous:
        cle_titre = re.sub(r"\W+", " ", a["titre"].lower()).strip()
        if a["url"] in vus_urls or cle_titre in vus_titres:
            continue
        vus_urls.add(a["url"])
        vus_titres.add(cle_titre)
        retenus.append({
            "titre": a["titre"], "source": a["source"],
            "date": a["date"].isoformat(timespec="minutes"),
            "resume_court": a["resume_court"], "url": a["url"],
        })
        if len(retenus) == MAX_ARTICLES:
            break
    return retenus


def _est_symbole_americain(ticker: str) -> bool:
    # Finnhub (offre gratuite) couvre les symboles américains ; un suffixe
    # de place (MC.PA, SAP.DE...) n'y est pas disponible.
    return "." not in ticker


def actualites_entreprise(
    ticker: str, nom: str | None, *,
    finnhub_source=data_sources.fetch_finnhub_company_news,
    gdelt_source=data_sources.fetch_gdelt_articles,
    now: datetime | None = None,
) -> dict:
    maintenant = now or datetime.now(timezone.utc)
    debut = maintenant - FENETRE_ENTREPRISE
    listes, erreurs = [], []

    if _est_symbole_americain(ticker):
        brut = finnhub_source(ticker, debut.date().isoformat(), maintenant.date().isoformat())
        if isinstance(brut, dict):
            erreurs.append(brut)
        else:
            listes.append(articles_finnhub(brut, ticker))

    if nom:
        brut = gdelt_source(f'"{nom}"', debut, TTL_ENTREPRISE_SECONDS, f"entreprise:{ticker}")
        if "erreur" in brut:
            erreurs.append(brut)
        else:
            listes.append(articles_gdelt(brut, nom=nom))

    if not listes and erreurs:
        return erreurs[0]
    return {"ticker": ticker, "articles": fusionne(listes, debut)}


def actualites_marche(
    *, gdelt_source=data_sources.fetch_gdelt_articles, now: datetime | None = None,
) -> dict:
    maintenant = now or datetime.now(timezone.utc)
    debut = maintenant - FENETRE_MARCHE
    brut = gdelt_source(REQUETE_MARCHE, debut, TTL_MARCHE_SECONDS, "marche")
    if "erreur" in brut:
        return brut
    return {"articles": fusionne([articles_gdelt(brut)], debut)}
