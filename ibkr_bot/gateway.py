# ibkr_bot/gateway.py
# Client HTTP du IBKR Client Portal Web API Gateway (paquet Java lance en
# service systemd, a l'ecoute sur 127.0.0.1 uniquement — voir spec 4.4).
# SEUL MODULE DU PAQUET QUI PARLE A IBKR.
#
# Endpoints verifies le 2026-09-14 contre la documentation publique du
# Client Portal Web API et contre le client open-source Voyz/ibind, qui
# les exerce en production. Ne pas modifier les chemins, les methodes ni
# les noms de champs sans re-verification contre la doc reelle.
#
# PLAN A : place_market_order() et confirm_reply() sont ecrites et
# testees (mockees), mais AUCUN autre module de ibkr_bot/ ne les appelle
# — un test structurel le verifie. Le chemin reel arrive au Plan B.
import urllib3

import requests

DEFAULT_GATEWAY_URL = "https://127.0.0.1:5000"
API_PREFIX = "/v1/api"
TIMEOUT = 15
POSITIONS_PAGE_SIZE = 100

# Le Gateway presente un certificat auto-signe. verify=False est sur ici
# et seulement ici : la connexion ne quitte jamais la machine.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def build_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{API_PREFIX}/{path.lstrip('/')}"


def _get(base_url: str, path: str, params: dict | None = None):
    resp = requests.get(
        build_url(base_url, path), params=params, timeout=TIMEOUT, verify=False,
    )
    resp.raise_for_status()
    return resp.json()


def _post(base_url: str, path: str, payload: dict | None = None):
    resp = requests.post(
        build_url(base_url, path), json=payload, timeout=TIMEOUT, verify=False,
    )
    resp.raise_for_status()
    return resp.json()


# --- session ---------------------------------------------------------

def auth_status(base_url: str = DEFAULT_GATEWAY_URL) -> dict:
    """POST /iserver/auth/status -> {"authenticated", "connected",
    "competing"}."""
    return _post(base_url, "/iserver/auth/status")


def is_authenticated(base_url: str = DEFAULT_GATEWAY_URL) -> bool:
    """Preflight du batch : True seulement si la session est a la fois
    authentifiee et connectee. Ne leve jamais — un Gateway eteint ou une
    reponse inattendue donnent False, que l'appelant traduira en
    'gateway_indisponible'."""
    try:
        status = auth_status(base_url)
    except Exception:
        return False
    if not isinstance(status, dict):
        return False
    return bool(status.get("authenticated")) and bool(status.get("connected"))


def tickle(base_url: str = DEFAULT_GATEWAY_URL) -> dict:
    """POST /tickle — maintient la session vivante."""
    return _post(base_url, "/tickle")


def reauthenticate(base_url: str = DEFAULT_GATEWAY_URL) -> dict:
    """POST /iserver/reauthenticate — relance la session courtage. Ne
    remplace pas une validation 2FA manuelle quand elle est exigee."""
    return _post(base_url, "/iserver/reauthenticate")


def brokerage_accounts(base_url: str = DEFAULT_GATEWAY_URL) -> dict:
    """GET /iserver/accounts — doit avoir ete appele au moins une fois
    dans la session avant tout passage d'ordre."""
    return _get(base_url, "/iserver/accounts")


# --- contrats et change ----------------------------------------------

def search_contract(base_url: str, symbol: str) -> list[dict]:
    """GET /iserver/secdef/search — recherche d'actions par symbole.
    Renvoie toujours une liste : le Gateway substitue parfois un objet
    d'erreur a la liste attendue."""
    data = _get(base_url, "/iserver/secdef/search",
                {"symbol": symbol, "secType": "STK"})
    return data if isinstance(data, list) else []


def contract_info(base_url: str, conid) -> dict:
    """GET /iserver/secdef/info — details du contrat (porte notamment
    currency et listingExchange, les deux champs qui permettent de
    refuser un contrat ambigu plutot que de le deviner)."""
    data = _get(base_url, "/iserver/secdef/info",
                {"conid": str(conid), "secType": "STK"})
    if isinstance(data, list):
        return data[0] if data else {}
    return data if isinstance(data, dict) else {}


def exchange_rate(base_url: str, source: str, target: str) -> float:
    """GET /iserver/exchangerate -> {"rate": ...}. Taux `source` ->
    `target` (EUR -> GBP renvoie des GBP par EUR)."""
    if source == target:
        return 1.0
    return float(_get(base_url, "/iserver/exchangerate",
                      {"source": source, "target": target})["rate"])
