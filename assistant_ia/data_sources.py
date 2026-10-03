# assistant_ia/data_sources.py
# Sources de donnees de l'assistant IA : TOUJOURS les fichiers reellement
# PUBLIES du site (jamais un checkout VPS local, qui pourrait etre sur
# un autre commit que ce qui est en ligne — voir spec §3), et TOUJOURS
# les API protegees existantes de gold_bot/ibkr_bot pour leurs statuts/
# positions (jamais une deuxieme lecture directe de leurs fichiers, pour
# ne jamais faire diverger deux chemins de lecture de la meme donnee).
import time

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
