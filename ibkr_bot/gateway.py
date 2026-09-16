# ibkr_bot/gateway.py
# Client TWS API (via ib_async) d'IB Gateway — remplace le Client Portal
# Web API Gateway (bloque par un bug d'authentification IBKR non
# resolu, voir docs/superpowers/specs/2026-09-16-ibkr-tws-gateway-migration-design.md).
# SEUL MODULE DU PAQUET QUI PARLE A IBKR.
#
# Endpoints verifies le 2026-09-16 contre le code source de ib_async
# (github.com/ib-api-reloaded/ib_async, fichiers ib.py/objects.py/
# contract.py/order.py lus directement). Ne pas modifier les noms de
# methodes ib_async ni les champs lus sans re-verification contre le
# code source reel.
#
# ORDRE REEL : place_market_order() est le SEUL chemin par lequel de
# l'argent reel peut bouger. Un test structurel (voir
# tests/ibkr_bot/test_order_routes_are_isolated.py) verifie qu'aucun
# autre module de ibkr_bot/ ne reference ib_async.placeOrder / MarketOrder.
import re

from ib_async import IB, Stock, Forex, MarketOrder

DEFAULT_GATEWAY_URL = "127.0.0.1:4002"  # port paper par defaut ; le
# deploiement reel passe 127.0.0.1:4001 via IBKR_GATEWAY_URL (.env).
DEFAULT_CLIENT_ID = 7
CONNECT_TIMEOUT = 15
DISCONNECT_FLUSH_SECONDS = 1  # recommandation ib_async pour les
# connexions de courte duree : laisser le temps aux derniers messages de
# partir avant de couper le socket.

_HOST_PORT_RE = re.compile(r"^(?:https?://)?([^:/]+):(\d+)/?$")

# Connexion unique, partagee par toutes les fonctions de ce module —
# ouverte par connect() (appele depuis daily.py::preflight()), fermee
# par disconnect() (appele depuis daily.py::_terminer()). Jamais de
# connexion permanente en arriere-plan (spec 2026-09-16, point 6) : une
# seule ouverture/fermeture par batch quotidien.
_ib: IB | None = None


def _parse_host_port(base_url: str) -> tuple[str, int]:
    m = _HOST_PORT_RE.match(base_url.strip())
    if not m:
        raise ValueError(f"base_url attendu sous la forme host:port, recu {base_url!r}")
    return m.group(1), int(m.group(2))


def connect(base_url: str = DEFAULT_GATEWAY_URL, *, client_id: int = DEFAULT_CLIENT_ID,
           account_id: str = "", timeout: float = CONNECT_TIMEOUT) -> bool:
    """Ouvre la connexion TWS API vers IB Gateway (une fois par batch —
    voir daily.py::preflight()). Idempotent : un appel alors qu'une
    connexion est deja active ne fait rien. Ne leve jamais : renvoie
    False sur tout echec, a charge de l'appelant (preflight) de
    reessayer."""
    global _ib
    if _ib is not None and _ib.isConnected():
        return True
    try:
        host, port = _parse_host_port(base_url)
        candidate = IB()
        candidate.connect(host, port, clientId=client_id, timeout=timeout,
                          account=account_id, raiseSyncErrors=True)
    except Exception:
        return False
    _ib = candidate
    return True


def disconnect() -> None:
    """Ferme la connexion TWS API en fin de batch (daily.py::_terminer()).
    Ne leve jamais, et ne fait rien si aucune connexion n'est active —
    tous les chemins de sortie de run_batch (kill_switch, hors jour de
    bourse, donnees perimees...) passent par _terminer() meme quand
    connect() n'a jamais ete appele."""
    global _ib
    if _ib is None:
        return
    try:
        if _ib.isConnected():
            # Delai de purge recommande par ib_async avant de couper une
            # connexion de courte duree (voir recherche de ce plan) :
            # laisse le temps aux derniers messages en vol de partir.
            _ib.sleep(DISCONNECT_FLUSH_SECONDS)
            _ib.disconnect()
    except Exception:
        pass
    finally:
        _ib = None


def _require_ib() -> IB:
    if _ib is None or not _ib.isConnected():
        raise ConnectionError("gateway.connect() n'a pas ete appele ou la connexion est fermee")
    return _ib


# --- session ---------------------------------------------------------

def is_authenticated(base_url: str = DEFAULT_GATEWAY_URL) -> bool:
    """Preflight du batch : True seulement si la connexion TWS API est
    active ET que managedAccounts() renvoie au moins un compte — meme
    esprit que l'ancien authenticated+connected du CPAPI. Ne leve
    jamais."""
    if _ib is None or not _ib.isConnected():
        return False
    try:
        comptes = _ib.managedAccounts()
    except Exception:
        return False
    return bool(comptes)


def auth_status(base_url: str = DEFAULT_GATEWAY_URL) -> dict:
    """Forme inchangee pour les appelants existants (aucun n'utilise
    "competing" aujourd'hui, mais la cle est gardee pour compatibilite
    de forme)."""
    return {"authenticated": is_authenticated(base_url),
            "connected": _ib is not None and _ib.isConnected(),
            "competing": False}


def tickle(base_url: str = DEFAULT_GATEWAY_URL) -> dict:
    """La TWS API maintient la connexion vivante via son propre
    heartbeat interne — pas d'appel explicite de maintien de session
    necessaire, contrairement au CPAPI. Simple reflet de l'etat."""
    return {"session": "ib_async", "connected": _ib is not None and _ib.isConnected()}


def reauthenticate(base_url: str = DEFAULT_GATEWAY_URL) -> dict:
    """No-op documente : la TWS API n'a pas d'equivalent de
    /iserver/reauthenticate. Une session expiree exige une vraie
    reconnexion socket, geree par IBC/systemd (redemarrage du
    conteneur), pas par un appel applicatif depuis ce module."""
    return {
        "authenticated": is_authenticated(base_url),
        "detail": "no-op : la reconnexion TWS API passe par IBC/systemd, pas par un appel applicatif",
    }


# --- comptes et soldes -------------------------------------------------

def brokerage_accounts(base_url: str = DEFAULT_GATEWAY_URL) -> dict:
    """Deja peuple a la connexion (Tache 1) — plus de bootstrap reseau a
    faire ici, contrairement au CPAPI. Garde pour compatibilite de nom."""
    ib = _require_ib()
    return {"accounts": list(ib.managedAccounts())}


def portfolio_accounts(base_url: str = DEFAULT_GATEWAY_URL) -> list[dict]:
    """Idem brokerage_accounts, forme liste pour compatibilite avec
    l'ancien retour CPAPI de /portfolio/accounts."""
    ib = _require_ib()
    return [{"accountId": compte} for compte in ib.managedAccounts()]


def ledger(base_url: str, account_id: str) -> dict:
    """Soldes indexes par devise reelle, plus l'agregat "BASE" — meme
    forme que l'ancienne lecture CPAPI de /portfolio/{accountId}/ledger.
    Tag "CashBalance" (par devise) et "TotalCashValue" avec
    currency == "BASE" (agregat) — voir docstring de la Tache 2 sur le
    statut de verification de cette derniere convention."""
    ib = _require_ib()
    soldes: dict[str, dict] = {}
    for valeur in ib.accountValues(account_id):
        if valeur.tag == "CashBalance":
            try:
                soldes[valeur.currency] = {"cashbalance": float(valeur.value)}
            except (TypeError, ValueError):
                continue
        elif valeur.tag == "TotalCashValue" and valeur.currency == "BASE":
            try:
                soldes["BASE"] = {"cashbalance": float(valeur.value)}
            except (TypeError, ValueError):
                continue
    return soldes


def cash_by_currency(base_url: str, account_id: str) -> dict[str, float]:
    """Identique a l'ancienne implementation CPAPI : exclut "BASE"."""
    resultat: dict[str, float] = {}
    for devise, entree in ledger(base_url, account_id).items():
        if devise == "BASE":
            continue
        resultat[devise] = entree["cashbalance"]
    return resultat


def base_currency_cash(base_url: str, account_id: str) -> float:
    """Identique a l'ancienne implementation CPAPI : 0.0 si absent."""
    entree = ledger(base_url, account_id).get("BASE")
    return entree["cashbalance"] if entree else 0.0


# --- positions ---------------------------------------------------------

def positions(base_url: str, account_id: str) -> list[dict]:
    """Traduit les Position (NamedTuple ib_async) en dicts au format
    deja consomme par portfolio.reconcile() — "conid" et "position"
    sont les deux seules cles lues par reconcile(), verifie dans
    ibkr_bot/portfolio.py pendant la redaction de ce plan. Deja peuple
    a la connexion (Tache 1) : pas de pagination a gerer, contrairement
    au CPAPI."""
    ib = _require_ib()
    return [
        {"conid": p.contract.conId, "position": p.position,
         "avgCost": p.avgCost, "account": p.account}
        for p in ib.positions(account_id)
    ]


# --- contrats et change ----------------------------------------------

def search_contract(base_url: str, symbol: str) -> list[dict]:
    """Contrat sous-specifie (exchange="SMART", devise vide) : IBKR
    renvoie tous les contrats correspondants toutes places confondues,
    comme le faisait /iserver/secdef/search cote CPAPI. Traduit chaque
    ContractDetails dans la forme deja filtree par
    contracts.py::resolve_conid() — "description" porte le code de
    bourse (primaryExchange, PAS exchange qui vaut souvent "SMART"),
    "sections" simule la forme CPAPI pour que
    _a_une_section_action() continue de fonctionner sans modification.

    [A VERIFIER EN TACHE] : que reqContractDetails avec un contrat
    sous-specifie renvoie bien plusieurs candidats par place/devise pour
    un symbole multi-cote, comme le faisait la recherche CPAPI — non
    verifiable sans connexion IBKR reelle dans cet environnement. Voir
    task-4-report.md."""
    ib = _require_ib()
    try:
        details_list = ib.reqContractDetails(Stock(symbol, "SMART", ""))
    except Exception:
        return []
    return [
        {"symbol": d.contract.symbol, "conid": d.contract.conId,
         "description": d.contract.primaryExchange,
         "sections": [{"secType": d.contract.secType}]}
        for d in details_list
    ]


def contract_info(base_url: str, conid) -> dict:
    """currency + listingExchange (= primaryExchange cote ib_async, PAS
    exchange qui vaut souvent "SMART" pour un contrat route)."""
    from ib_async import Contract
    ib = _require_ib()
    try:
        details_list = ib.reqContractDetails(Contract(conId=int(conid)))
    except Exception:
        return {}
    if not details_list:
        return {}
    contract = details_list[0].contract
    return {"currency": contract.currency, "listingExchange": contract.primaryExchange}


def exchange_rate(base_url: str, source: str, target: str) -> float:
    """Pas d'appel RPC direct cote TWS API : demande de donnees de
    marche sur un contrat Forex, lecture du prix resultant. sleep(2)
    laisse le temps au premier tick d'arriver (pattern standard
    ib_async pour une lecture ponctuelle plutot qu'un flux)."""
    if source == target:
        return 1.0
    ib = _require_ib()
    fx = Forex(f"{source}{target}")
    ticker = ib.reqMktData(fx, "", False, False)
    ib.sleep(2)
    try:
        prix = ticker.marketPrice()
    finally:
        ib.cancelMktData(fx)
    return float(prix)
