# assistant_ia/data_sources.py
# Sources de donnees de l'assistant IA : TOUJOURS les fichiers reellement
# PUBLIES du site (jamais un checkout VPS local, qui pourrait etre sur
# un autre commit que ce qui est en ligne — voir spec §3), et TOUJOURS
# les API protegees existantes de gold_bot/ibkr_bot pour leurs statuts/
# positions (jamais une deuxieme lecture directe de leurs fichiers, pour
# ne jamais faire diverger deux chemins de lecture de la meme donnee).
import os
import time
from datetime import datetime

import requests

SITE_BASE_URL = "https://alexandreauq.github.io/analyse-or/"
INDICES_JSON_URL = SITE_BASE_URL + "indices.json"
CACHE_TTL_SECONDS = 600
REQUEST_TIMEOUT_SECONDS = 15

BOT_DASHBOARDS = {
    "or": {"url": "https://goldbot.fr:8443/dashboard", "token_env": "BOT_API_TOKEN"},
    "actions": {"url": "https://goldbot.fr:8444/dashboard", "token_env": "IBKR_BOT_API_TOKEN"},
}

# Cache module-level par defaut : partage entre toutes les requetes de ce
# processus (le service tourne en un seul process uvicorn), jamais
# recree par appel. Les tests passent leur propre `cache={}` pour rester
# isoles les uns des autres.
_default_cache: dict = {}


def fetch_indices_data(
    http_get=requests.get, now_fn=time.time, cache: dict | None = None,
) -> dict:
    """Contenu de indices.json tel que PUBLIE (pas un checkout local),
    mis en cache en memoire 10 minutes — la donnee ne change qu'une fois
    par jour (run quotidien du workflow indices.yml), un cache court
    evite juste de re-fetcher a chaque question sans jamais servir une
    donnee vraiment perimee."""
    if cache is None:
        cache = _default_cache
    maintenant = now_fn()
    entree = cache.get("indices")
    if entree is not None and (maintenant - entree[0]) < CACHE_TTL_SECONDS:
        return entree[1]
    reponse = http_get(INDICES_JSON_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    reponse.raise_for_status()
    donnees = reponse.json()
    cache["indices"] = (maintenant, donnees)
    return donnees


def fetch_bot_dashboard(nom: str, http_get=requests.get) -> dict:
    """Appelle /dashboard du bot Or ("or") ou Bot Actions ("actions")
    avec son jeton dedie (configure dans le .env de CE service, copie du
    jeton que le bot cible valide lui-meme). Degrade vers
    {"erreur": "..."} sur toute panne reseau/HTTP plutot que de lever —
    c'est a l'outil appelant (Task 3) de decider quoi en dire a Claude."""
    import os

    if nom not in BOT_DASHBOARDS:
        raise ValueError(f"nom de bot inconnu : {nom!r} (attendu : 'or' ou 'actions')")
    config = BOT_DASHBOARDS[nom]
    token = os.environ.get(config["token_env"], "")
    try:
        reponse = http_get(
            config["url"], headers={"X-Bot-Token": token}, timeout=REQUEST_TIMEOUT_SECONDS)
        reponse.raise_for_status()
        return reponse.json()
    except Exception as e:
        return {"erreur": f"impossible de contacter le bot {nom} : {e}"}


FINNHUB_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
FINNHUB_TTL_SECONDS = 2 * 3600
_news_cache: dict = {}


def _appel_actualites(url, params, headers, ttl_seconds, cache_key, *, http_get, now_fn, cache):
    """Appel GET mis en cache, commun aux deux sources d'actualites. Ne leve
    jamais. Seul le nom de la classe d'exception remonte : le texte d'une
    exception peut contenir l'URL, et un token dans l'URL ne doit jamais
    atteindre Claude. Une erreur n'est jamais mise en cache."""
    if cache is None:
        cache = _news_cache
    maintenant = now_fn()
    entree = cache.get(cache_key)
    if entree is not None and (maintenant - entree[0]) < ttl_seconds:
        return entree[1]
    try:
        reponse = http_get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        reponse.raise_for_status()
        donnees = reponse.json()
    except Exception as e:
        return {"erreur": f"actualites indisponibles ({type(e).__name__})"}
    cache[cache_key] = (maintenant, donnees)
    return donnees


def fetch_finnhub_company_news(
    symbole: str, debut_iso: str, fin_iso: str,
    *, http_get=requests.get, now_fn=time.time, cache: dict | None = None,
) -> dict | list:
    """Actualites Finnhub rattachees au symbole (liste) ou {"erreur": str}.
    Le jeton part dans l'en-tete X-Finnhub-Token, jamais dans l'URL."""
    token = os.environ.get("FINNHUB_API_TOKEN", "")
    if not token:
        return {"erreur": "FINNHUB_API_TOKEN absent de l'environnement"}
    return _appel_actualites(
        FINNHUB_COMPANY_NEWS_URL, {"symbol": symbole, "from": debut_iso, "to": fin_iso},
        {"X-Finnhub-Token": token}, FINNHUB_TTL_SECONDS, f"finnhub:{symbole}:{debut_iso}",
        http_get=http_get, now_fn=now_fn, cache=cache)


def fetch_gdelt_articles(
    requete: str, debut: datetime, ttl_seconds: int, cache_key: str,
    *, http_get=requests.get, now_fn=time.time, cache: dict | None = None,
) -> dict:
    """Articles GDELT (`{"articles": [...]}`) ou {"erreur": str}. Source sans
    cle. GDELT limite a une requete toutes les 5 secondes : un refus arrive
    en texte brut, non JSON, donc renvoye comme erreur et non mis en cache."""
    params = {
        "query": requete, "mode": "artlist", "format": "json", "maxrecords": 25,
        "sort": "datetimedesc", "startdatetime": debut.strftime("%Y%m%d%H%M%S"),
    }
    return _appel_actualites(
        GDELT_DOC_URL, params, None, ttl_seconds, cache_key,
        http_get=http_get, now_fn=now_fn, cache=cache)
