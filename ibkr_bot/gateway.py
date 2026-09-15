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


# --- portefeuille ----------------------------------------------------

def ledger(base_url: str, account_id: str) -> dict:
    """GET /portfolio/{accountId}/ledger — soldes indexes par devise.
    La cle "BASE" est un agregat dans la devise de base du compte, pas
    une devise reelle."""
    data = _get(base_url, f"/portfolio/{account_id}/ledger")
    return data if isinstance(data, dict) else {}


def cash_by_currency(base_url: str, account_id: str) -> dict[str, float]:
    """Solde cash disponible par devise reelle. Alimente le garde-fou de
    solde (spec 9.9) : ne retenir que les signaux financables, plutot que
    de declencher une serie de rejets bruyants en fin de classement."""
    soldes: dict[str, float] = {}
    for devise, entree in ledger(base_url, account_id).items():
        if devise == "BASE" or not isinstance(entree, dict):
            continue
        solde = entree.get("cashbalance")
        if isinstance(solde, (int, float)) and not isinstance(solde, bool):
            soldes[devise] = float(solde)
    return soldes


def positions(base_url: str, account_id: str) -> list[dict]:
    """GET /portfolio/{accountId}/positions/{pageId} — toutes les
    positions du compte, pagination suivie jusqu'a une page incomplete.
    Renvoie les positions BRUTES du compte : c'est portfolio.reconcile()
    qui distingue celles du bot de celles de l'utilisateur."""
    toutes: list[dict] = []
    page = 0
    while True:
        lot = _get(base_url, f"/portfolio/{account_id}/positions/{page}")
        if not isinstance(lot, list):
            break
        toutes.extend(lot)
        if len(lot) < POSITIONS_PAGE_SIZE:
            break
        page += 1
    return toutes


# --- ordres ----------------------------------------------------------
# ATTENTION : les deux fonctions ci-dessous sont le seul chemin par
# lequel de l'argent reel peut bouger. Dans ce Plan A, elles ne sont
# appelees QUE par les tests (avec requests.post monkeypatche). Le test
# test_no_other_plan_a_module_references_the_order_routes le verifie.

def place_market_order(base_url: str, account_id: str, conid: int,
                       side: str, quantity: int) -> list[dict]:
    """POST /iserver/account/{accountId}/orders — ordre au marche (MKT),
    valable le jour (DAY), passe dans la fenetre 14:30-15:30 UTC ou
    toutes les places du perimetre sont ouvertes (spec 4.8, 4.5).

    La reponse peut etre une confirmation d'ordre OU une question a
    confirmer via confirm_reply() ; l'appelant (Plan B) doit traiter les
    deux formes."""
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side doit valoir 'BUY' ou 'SELL', recu {side!r}")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        raise ValueError(f"quantite doit etre un entier >= 1, recue {quantity!r}")
    data = _post(base_url, f"/iserver/account/{account_id}/orders", {
        "orders": [{
            "conid": conid,
            "orderType": "MKT",
            "side": side,
            "quantity": quantity,
            "tif": "DAY",
            "acctId": account_id,
        }]
    })
    return data if isinstance(data, list) else [data]


def confirm_reply(base_url: str, reply_id: str, confirmed: bool = True) -> list[dict]:
    """POST /iserver/reply/{replyId} — repond a une question de
    confirmation renvoyee par place_market_order()."""
    data = _post(base_url, f"/iserver/reply/{reply_id}", {"confirmed": confirmed})
    return data if isinstance(data, list) else [data]


def order_status(base_url: str, order_id: str) -> dict:
    """GET /iserver/account/order/status/{orderId} — etat d'un ordre
    soumis (porte notamment order_status et avgPrice une fois execute)."""
    data = _get(base_url, f"/iserver/account/order/status/{order_id}")
    return data if isinstance(data, dict) else {}
