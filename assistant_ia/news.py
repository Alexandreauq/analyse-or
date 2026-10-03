# assistant_ia/news.py
# Actualités pour l'assistant IA (spec 2026-10-03-ai-news-context-design.md).
# Ce module transforme la réponse brute de Marketaux en articles sûrs et
# fiables : pertinence (seuil de score sur l'entité), fraîcheur (fenêtre
# dans la requête), déduplication, plafond, troncature, et seules les
# URLs http(s) sont gardées. Il ne lit jamais le réseau directement : la
# récupération passe par data_sources.fetch_marketaux_news, injectable.
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import assistant_ia.data_sources as data_sources

MIN_MATCH_SCORE = 5.0
MAX_ARTICLES = 5
RESUME_MAX_CHARS = 300
TTL_ENTREPRISE_SECONDS = 2 * 3600
TTL_MARCHE_SECONDS = 3600
FENETRE_ENTREPRISE = timedelta(hours=48)
FENETRE_MARCHE = timedelta(hours=24)
PAYS_UNIVERS = "fr,de,es,it,gb,ch,jp,hk,us"


def _formate_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def params_entreprise(ticker: str, now: datetime) -> dict:
    return {
        "symbols": ticker,
        "published_after": _formate_date(now - FENETRE_ENTREPRISE),
        "min_match_score": MIN_MATCH_SCORE,
    }


def params_marche(now: datetime) -> dict:
    return {
        "countries": PAYS_UNIVERS,
        "published_after": _formate_date(now - FENETRE_MARCHE),
    }


def _url_ok(url) -> bool:
    if not isinstance(url, str):
        return False
    analyse = urlparse(url)
    return analyse.scheme in ("http", "https") and bool(analyse.netloc)


def _score_entite(article: dict, ticker: str) -> float | None:
    meilleur = None
    for entite in article.get("entities") or []:
        if not isinstance(entite, dict) or entite.get("symbol") != ticker:
            continue
        score = entite.get("match_score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            meilleur = score if meilleur is None else max(meilleur, score)
    return meilleur


def _champ_texte(article: dict, cle: str) -> str:
    valeur = article.get(cle)
    return valeur if isinstance(valeur, str) else ""


def normalise(payload, ticker: str | None = None) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    vus_urls: set[str] = set()
    vus_titres: set[str] = set()
    retenus: list[dict] = []
    for article in payload["data"]:
        if not isinstance(article, dict):
            continue
        url = article.get("url")
        titre = _champ_texte(article, "title").strip()
        if not _url_ok(url) or not titre:
            continue
        if ticker is not None:
            score = _score_entite(article, ticker)
            if score is None or score < MIN_MATCH_SCORE:
                continue
        cle_titre = re.sub(r"\W+", " ", titre.lower()).strip()
        if url in vus_urls or cle_titre in vus_titres:
            continue
        vus_urls.add(url)
        vus_titres.add(cle_titre)
        resume = (_champ_texte(article, "description") or _champ_texte(article, "snippet"))[:RESUME_MAX_CHARS]
        retenus.append({
            "titre": titre,
            "source": _champ_texte(article, "source"),
            "date": _champ_texte(article, "published_at"),
            "resume_court": resume,
            "url": url,
        })
    retenus.sort(key=lambda a: a["date"], reverse=True)
    return retenus[:MAX_ARTICLES]


def actualites_entreprise(
    ticker: str, *, news_source=data_sources.fetch_marketaux_news, now: datetime | None = None,
) -> dict:
    maintenant = now or datetime.now(timezone.utc)
    brut = news_source(params_entreprise(ticker, maintenant), TTL_ENTREPRISE_SECONDS, f"entreprise:{ticker}")
    if "erreur" in brut:
        return brut
    return {"ticker": ticker, "articles": normalise(brut, ticker=ticker)}


def actualites_marche(
    *, news_source=data_sources.fetch_marketaux_news, now: datetime | None = None,
) -> dict:
    maintenant = now or datetime.now(timezone.utc)
    brut = news_source(params_marche(maintenant), TTL_MARCHE_SECONDS, "marche")
    if "erreur" in brut:
        return brut
    return {"articles": normalise(brut)}
