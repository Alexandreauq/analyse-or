# Migration du bot actions IBKR vers IB Gateway / TWS API — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le transport HTTP du Client Portal Gateway (bloqué par
un bug d'authentification IBKR non résolu) par IB Gateway + la TWS API
(librairie `ib_async`) dans `ibkr_bot/gateway.py`, sans toucher à la
logique métier du reste du bot, puis déployer IB Gateway + IBC + Xvfb sur
le VPS en parallèle du Client Portal Gateway existant.

**Architecture:** `gateway.py` garde exactement les mêmes noms de
fonctions et les mêmes formes de retour (dicts/listes au format déjà
consommé par `contracts.py`/`portfolio.py`/`daily.py`) ; seul l'intérieur
change, d'appels HTTP `requests` à des appels `ib_async` sur une connexion
socket persistante. Deux fonctions nouvelles (`connect`/`disconnect`)
gèrent le cycle de vie de cette connexion, ouverte une fois en début de
batch et fermée à la fin — `daily.py` reçoit deux points d'appel
supplémentaires pour ça (dans `preflight()` et `_terminer()`), seule
dérogation volontaire à « rien ne change hors gateway.py » (justifiée en
Contraintes globales).

**Tech Stack:** Python, `ib_async` (fork maintenu de `ib_insync`), pytest
avec mocks (jamais de connexion réseau réelle en test), Docker +
`gnzsnz/ib-gateway-docker` (IB Gateway + IBC + VNC) sur le VPS Hetzner
existant.

**Spec:** `docs/superpowers/specs/2026-09-16-ibkr-tws-gateway-migration-design.md`

## Global Constraints

- Les noms de fonctions et les formes de retour EXISTANTS de
  `ibkr_bot/gateway.py` ne changent pas — `contracts.py`, `portfolio.py`,
  `sizing.py`, `signals.py` ne sont modifiés par AUCUNE tâche de ce plan.
- `ibkr_bot/daily.py` reçoit exactement deux modifications, toutes deux
  circonscrites au cycle de vie de la connexion (pas à la logique
  métier) : un appel à `gateway.connect(...)` dans `preflight()`, un
  appel à `gateway.disconnect()` dans `_terminer()`. Aucune autre ligne de
  `daily.py` ne change dans ce plan.
- Modèle de connexion : une connexion ouverte au début du batch quotidien,
  fermée à la fin — jamais de connexion permanente en arrière-plan (spec
  §2 point 6).
- `tickle()` devient un simple `ib.isConnected()` ; `reauthenticate()`
  devient un no-op documenté (spec §4).
- Aucun test de ce plan n'ouvre de connexion réseau réelle : `ib_async`'s
  `IB` est mocké partout, même principe que le monkeypatch de
  `requests.post`/`requests.get` déjà utilisé dans
  `tests/ibkr_bot/test_gateway.py`.
- Compte réel : **U28849893** (compte réel, pas paper — préfixe "U").
  Port TWS API d'IB Gateway : **4001 (réel)** / **4002 (paper)** — ports
  différents de TWS Desktop (7496/7497), ne jamais les confondre.
- Toute correspondance `ib_async` non vérifiée directement dans le code
  source de la librairie pendant la rédaction de ce plan est marquée
  **[À VÉRIFIER EN TÂCHE]** ci-dessous — l'implémenteur doit la confirmer
  contre une connexion réelle (paper) avant de considérer la tâche
  terminée, jamais la deviner.
- Le garde-fou structurel qui empêche tout module hors `gateway.py`
  d'envoyer un ordre réel (`test_no_other_plan_a_module_references_the_order_routes`
  ou équivalent) doit continuer d'exister et de protéger les nouveaux
  points d'entrée `ib_async` (`placeOrder`, etc.), pas seulement l'ancien
  chemin REST — voir Tâche 8.

---

## Décision de conception : le cycle de vie de connexion dans `daily.py`

**[Ruling, tranché à la rédaction de ce plan — pas un point ouvert pour l'implémenteur.]**

Le Client Portal Gateway est sans état : chaque fonction de `gateway.py`
fait une requête HTTP indépendante avec son propre `base_url`. La TWS API
via `ib_async` est **avec état** : on ouvre un objet `IB()` connecté une
fois, et toutes les fonctions suivantes l'utilisent. La spec dit que
« l'interface publique de `gateway.py` ne change pas » et que
« `daily.py` ne change pas » — ces deux exigences sont incompatibles à la
lettre avec une librairie qui exige un objet de connexion vivant. Deux
options existaient :

1. **Connexion implicite** : chaque fonction de `gateway.py` se
   connecte toute seule au premier appel (singleton paresseux), et se
   déconnecte via un hook `atexit`. Aucune modification de `daily.py`.
   Rejeté : une déconnexion `atexit` est invisible, non testable
   directement, et retarde arbitrairement le closing d'une connexion
   qu'on veut fermer explicitement à la fin du batch (spec §2 point 6).
2. **Connexion explicite, bornée à `preflight()`/`_terminer()`** : ces
   deux fonctions sont déjà, dans le `daily.py` actuel, les points du
   batch qui parlent d'authentification et de fin de batch — `preflight()`
   existe UNIQUEMENT pour vérifier que le Gateway est utilisable,
   `_terminer()` est déjà documentée comme « Sortie unique du batch ».
   Y ajouter connect/disconnect est donc une extension naturelle de leur
   responsabilité actuelle, pas un nouveau souci métier.

**Choix retenu : option 2.** C'est la seule dérogation à « `daily.py` ne
change pas », strictement limitée à 2 lignes dans 2 fonctions déjà
responsables du cycle de session.

---

### Task 1: Connexion, authentification, cycle de vie (`gateway.py`)

**Files:**
- Modify: `ibkr_bot/gateway.py` (réécriture complète du fichier)
- Modify: `requirements.txt` (ajoute `ib_async`)
- Test: `tests/ibkr_bot/test_gateway.py` (réécriture complète)

**Interfaces:**
- Produces: `gateway.connect(base_url, *, client_id=None, account_id="", timeout=15) -> bool`
  (nouvelle fonction), `gateway.disconnect() -> None` (nouvelle
  fonction), `gateway.is_authenticated(base_url) -> bool` (forme
  inchangée), `gateway.auth_status(base_url) -> dict` (forme inchangée :
  `{"authenticated": bool, "connected": bool, "competing": bool}`),
  `gateway.tickle(base_url) -> dict`, `gateway.reauthenticate(base_url) -> dict`.
- Consumes: rien d'une tâche antérieure (première tâche du plan).

**Contexte** — l'ancien `gateway.py` fait des requêtes HTTP indépendantes
vers un Gateway REST local. Le nouveau parle à IB Gateway via un socket
persistant, avec la librairie `ib_async` (classe `IB`, méthodes
`connect(host, port, clientId, timeout, readonly, account, raiseSyncErrors)`,
`isConnected()`, `disconnect()`, `managedAccounts()`). Ports IB Gateway :
**4001 réel / 4002 paper** (PAS 7496/7497, qui sont les ports de TWS
Desktop — vérifié contre plusieurs sources indépendantes pendant la
recherche de ce plan).

`base_url` reste le premier paramètre de chaque fonction existante (aucun
appelant ne change), mais il est maintenant interprété comme une chaîne
`"host:port"` (avec un éventuel préfixe `http://`/`https://` toléré et
ignoré, pour rester visuellement proche de l'ancienne valeur) plutôt que
comme une URL REST. C'est `connect()` qui le parse ; toutes les autres
fonctions l'ignorent une fois la connexion établie (elles utilisent
uniquement l'objet `IB` déjà connecté), mais le gardent en paramètre pour
ne rien changer à leur signature.

- [ ] **Step 1: Ajouter la dépendance**

Dans `requirements.txt`, ajouter une ligne :
```
ib_async
```

- [ ] **Step 2: Écrire les tests de connexion (ils échoueront, le code n'existe pas encore)**

```python
# tests/ibkr_bot/test_gateway.py
import pytest

import ibkr_bot.gateway as gateway

BASE = "127.0.0.1:4002"


class _FakeIB:
    """Faux ib_async.IB — enregistre les appels, ne fait jamais de reseau.
    Meme esprit que _FakeIbkrResponse dans l'ancienne suite de tests."""

    def __init__(self):
        self.connected = False
        self.connect_calls = []
        self.disconnect_calls = 0
        self.managed_accounts_result = ["U28849893"]
        self.sleep_calls = []

    def connect(self, host, port, clientId=1, timeout=4, readonly=False,
                account="", raiseSyncErrors=False, **kwargs):
        self.connect_calls.append({
            "host": host, "port": port, "clientId": clientId,
            "timeout": timeout, "account": account,
            "raiseSyncErrors": raiseSyncErrors,
        })
        self.connected = True

    def isConnected(self):
        return self.connected

    def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False

    def managedAccounts(self):
        return self.managed_accounts_result

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)


@pytest.fixture
def fake_ib(monkeypatch):
    instance = _FakeIB()
    monkeypatch.setattr(gateway, "IB", lambda: instance)
    # gateway.py garde un singleton module-level ; le reinitialiser entre
    # deux tests evite qu'un test reutilise la connexion du precedent.
    monkeypatch.setattr(gateway, "_ib", None)
    return instance


def test_connect_parses_host_and_port_from_base_url(fake_ib):
    ok = gateway.connect(BASE, account_id="U28849893")
    assert ok is True
    assert fake_ib.connect_calls == [{
        "host": "127.0.0.1", "port": 4002, "clientId": gateway.DEFAULT_CLIENT_ID,
        "timeout": gateway.CONNECT_TIMEOUT, "account": "U28849893",
        "raiseSyncErrors": True,
    }]


def test_connect_tolerates_an_http_prefix(fake_ib):
    gateway.connect("https://127.0.0.1:4001")
    assert fake_ib.connect_calls[0]["host"] == "127.0.0.1"
    assert fake_ib.connect_calls[0]["port"] == 4001


def test_connect_accepts_a_custom_client_id(fake_ib):
    gateway.connect(BASE, client_id=42)
    assert fake_ib.connect_calls[0]["clientId"] == 42


def test_connect_returns_false_instead_of_raising(monkeypatch, fake_ib):
    def boom(*a, **k):
        raise ConnectionRefusedError("gateway pas encore pret")

    monkeypatch.setattr(fake_ib, "connect", boom)
    assert gateway.connect(BASE) is False


def test_connect_is_idempotent_if_already_connected(fake_ib):
    gateway.connect(BASE)
    gateway.connect(BASE)
    assert len(fake_ib.connect_calls) == 1


def test_disconnect_is_safe_even_if_never_connected(fake_ib):
    gateway.disconnect()  # ne doit jamais lever
    assert fake_ib.disconnect_calls == 0


def test_disconnect_calls_ib_disconnect_when_connected(fake_ib):
    gateway.connect(BASE)
    gateway.disconnect()
    assert fake_ib.disconnect_calls == 1
    # Un flush delay est recommande par ib_async avant de couper une
    # connexion de courte duree (voir doc du projet) : on l'exerce ici.
    assert fake_ib.sleep_calls == [gateway.DISCONNECT_FLUSH_SECONDS]


def test_is_authenticated_true_when_connected_and_accounts_present(fake_ib):
    gateway.connect(BASE)
    assert gateway.is_authenticated(BASE) is True


def test_is_authenticated_false_when_not_connected(fake_ib):
    assert gateway.is_authenticated(BASE) is False


def test_is_authenticated_false_when_connected_but_no_managed_accounts(fake_ib):
    gateway.connect(BASE)
    fake_ib.managed_accounts_result = []
    assert gateway.is_authenticated(BASE) is False


def test_is_authenticated_returns_false_instead_of_raising(fake_ib, monkeypatch):
    gateway.connect(BASE)
    monkeypatch.setattr(fake_ib, "managedAccounts",
                         lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert gateway.is_authenticated(BASE) is False


def test_auth_status_reflects_is_authenticated(fake_ib):
    gateway.connect(BASE)
    status = gateway.auth_status(BASE)
    assert status == {"authenticated": True, "connected": True, "competing": False}


def test_tickle_is_a_plain_isconnected_check(fake_ib):
    gateway.connect(BASE)
    assert gateway.tickle(BASE) == {"session": "ib_async", "connected": True}


def test_reauthenticate_is_a_documented_no_op(fake_ib):
    gateway.connect(BASE)
    result = gateway.reauthenticate(BASE)
    assert result == {
        "authenticated": True,
        "detail": "no-op : la reconnexion TWS API passe par IBC/systemd, pas par un appel applicatif",
    }
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/ibkr_bot/test_gateway.py -v`
Expected: FAIL — `AttributeError: module 'ibkr_bot.gateway' has no attribute 'IB'` (ou similaire), le code n'existe pas encore.

- [ ] **Step 4: Écrire l'implémentation**

Remplacer intégralement `ibkr_bot/gateway.py` par (début du fichier —
les fonctions des tâches 2 à 5 s'ajoutent ensuite dans CE MÊME fichier,
ne pas créer de nouveaux modules) :

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/ibkr_bot/test_gateway.py -v`
Expected: PASS (uniquement les tests de connexion ecrits ci-dessus — les
autres fonctions du module n'existent pas encore, elles arrivent aux
taches suivantes).

- [ ] **Step 6: Commit**

```bash
git add ibkr_bot/gateway.py requirements.txt tests/ibkr_bot/test_gateway.py
git commit -m "feat(ibkr_bot): connexion TWS API via ib_async (gateway.py 1/5)"
```

---

### Task 2: Comptes et soldes (`brokerage_accounts`, `portfolio_accounts`, `ledger`, `cash_by_currency`, `base_currency_cash`)

**Files:**
- Modify: `ibkr_bot/gateway.py`
- Test: `tests/ibkr_bot/test_gateway.py`

**Interfaces:**
- Consumes: `_require_ib()`, `_ib` (Tâche 1).
- Produces: `brokerage_accounts(base_url) -> dict`, `portfolio_accounts(base_url) -> list[dict]`,
  `ledger(base_url, account_id) -> dict` (forme inchangée : dict indexé
  par devise, `{"BASE": {"cashbalance": ...}, "EUR": {"cashbalance": ...}, ...}`),
  `cash_by_currency(base_url, account_id) -> dict[str, float]` (forme
  inchangée), `base_currency_cash(base_url, account_id) -> float` (forme
  inchangée).

**Contexte** — `ib.accountValues(account) -> list[AccountValue]` où
`AccountValue` est un NamedTuple `(account, tag, value, currency,
modelCode)`. Le tag `"CashBalance"` porte le solde par devise réelle
(`currency` = code ISO). Le tag `"TotalCashValue"` avec `currency ==
"BASE"` porte l'agrégat en devise de base du compte — **[À VÉRIFIER EN
TÂCHE]** : la convention `currency == "BASE"` pour la ligne agrégée est
une convention communautaire largement répandue mais pas citée mot pour
mot dans la documentation officielle consultée pendant la recherche de
ce plan. Avant de considérer cette tâche terminée, vérifier avec une
connexion paper réelle que `[v for v in ib.accountValues() if v.tag ==
"TotalCashValue"]` contient bien une entrée `currency == "BASE"` — si ce
n'est pas le cas, adapter `base_currency_cash()` en conséquence (ex. sommer
les entrées `CashBalance` converties, ou chercher un autre tag) et noter
l'écart dans le rapport de tâche.

`ib.accountValues()` est déjà peuplé automatiquement à la connexion
(voir Tâche 1, `raiseSyncErrors=True` fait échouer `connect()` plutôt que
renvoyer un cache vide en silence) — pas besoin d'un appel de
« bootstrap » séparé comme l'exigeait le CPAPI (`portfolio_accounts`
avant `positions`). `brokerage_accounts()`/`portfolio_accounts()` sont
donc gardées pour compatibilité de nom et de forme, mais deviennent de
simples lectures de `ib.managedAccounts()` — elles ne déclenchent plus
rien côté réseau.

- [ ] **Step 1: Écrire les tests**

Ajouter à `tests/ibkr_bot/test_gateway.py` (étendre `_FakeIB` avec un
attribut `account_values_result: list` et une méthode `accountValues(self, account="")`) :

```python
class _FakeAccountValue:
    def __init__(self, account, tag, value, currency, modelCode=""):
        self.account = account
        self.tag = tag
        self.value = value
        self.currency = currency
        self.modelCode = modelCode


# Dans _FakeIB.__init__, ajouter :
#     self.account_values_result = []
# Et ajouter la methode :
#     def accountValues(self, account=""):
#         return self.account_values_result


def test_brokerage_accounts_reflects_managed_accounts(fake_ib):
    gateway.connect(BASE)
    assert gateway.brokerage_accounts(BASE) == {"accounts": ["U28849893"]}


def test_portfolio_accounts_reflects_managed_accounts(fake_ib):
    gateway.connect(BASE)
    assert gateway.portfolio_accounts(BASE) == [{"accountId": "U28849893"}]


def test_ledger_indexes_cash_balances_by_currency_plus_base(fake_ib):
    gateway.connect(BASE)
    fake_ib.account_values_result = [
        _FakeAccountValue("U28849893", "CashBalance", "1234.56", "EUR"),
        _FakeAccountValue("U28849893", "CashBalance", "500.00", "GBP"),
        _FakeAccountValue("U28849893", "TotalCashValue", "1800.00", "BASE"),
        _FakeAccountValue("U28849893", "NetLiquidation", "9999.00", "BASE"),  # doit etre ignore
    ]
    result = gateway.ledger(BASE, "U28849893")
    assert result == {
        "EUR": {"cashbalance": 1234.56},
        "GBP": {"cashbalance": 500.00},
        "BASE": {"cashbalance": 1800.00},
    }


def test_cash_by_currency_excludes_the_base_aggregate(fake_ib):
    gateway.connect(BASE)
    fake_ib.account_values_result = [
        _FakeAccountValue("U28849893", "CashBalance", "1234.56", "EUR"),
        _FakeAccountValue("U28849893", "TotalCashValue", "1800.00", "BASE"),
    ]
    assert gateway.cash_by_currency(BASE, "U28849893") == {"EUR": 1234.56}


def test_base_currency_cash_reads_the_base_aggregate(fake_ib):
    gateway.connect(BASE)
    fake_ib.account_values_result = [
        _FakeAccountValue("U28849893", "TotalCashValue", "1800.00", "BASE"),
    ]
    assert gateway.base_currency_cash(BASE, "U28849893") == 1800.00


def test_base_currency_cash_defaults_to_zero_when_absent(fake_ib):
    gateway.connect(BASE)
    fake_ib.account_values_result = []
    assert gateway.base_currency_cash(BASE, "U28849893") == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ibkr_bot/test_gateway.py -v -k "brokerage_accounts or portfolio_accounts or ledger or cash_by_currency or base_currency_cash"`
Expected: FAIL — fonctions inexistantes.

- [ ] **Step 3: Implémenter**

Ajouter à `ibkr_bot/gateway.py` :

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ibkr_bot/test_gateway.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ibkr_bot/gateway.py tests/ibkr_bot/test_gateway.py
git commit -m "feat(ibkr_bot): comptes et soldes via ib_async (gateway.py 2/5)"
```

---

### Task 3: Positions (`positions`)

**Files:**
- Modify: `ibkr_bot/gateway.py`
- Test: `tests/ibkr_bot/test_gateway.py`

**Interfaces:**
- Consumes: `_require_ib()` (Tâche 1).
- Produces: `positions(base_url, account_id) -> list[dict]`, chaque dict
  portant au moins `"conid"` (int) et `"position"` (quantité, float ou
  int) — ce sont les DEUX SEULES clés lues par `portfolio.reconcile()`
  (`ibkr_bot/portfolio.py:267,283`, vérifié en lisant le fichier pendant
  la rédaction de ce plan). Forme de retour inchangée.

**Contexte** — `ib.positions(account) -> list[Position]`, où `Position`
est un NamedTuple `(account, contract, position, avgCost)` — déjà peuplé
à la connexion (Tâche 1, `raiseSyncErrors=True`). Heureuse coïncidence :
le champ s'appelle déjà `position` côté `ib_async`, comme côté CPAPI — pas
de risque de confusion de nom pendant la traduction.

- [ ] **Step 1: Écrire les tests**

```python
class _FakeContract:
    def __init__(self, conId):
        self.conId = conId


class _FakePosition:
    def __init__(self, account, conId, position, avgCost=0.0):
        self.account = account
        self.contract = _FakeContract(conId)
        self.position = position
        self.avgCost = avgCost


# Dans _FakeIB.__init__, ajouter :
#     self.positions_result = []
# Et ajouter la methode :
#     def positions(self, account=""):
#         return self.positions_result


def test_positions_translates_ib_async_position_objects(fake_ib):
    gateway.connect(BASE)
    fake_ib.positions_result = [
        _FakePosition("U28849893", 265598, 10, avgCost=150.25),
        _FakePosition("U28849893", 999999, 0, avgCost=0.0),
    ]
    result = gateway.positions(BASE, "U28849893")
    assert result == [
        {"conid": 265598, "position": 10, "avgCost": 150.25, "account": "U28849893"},
        {"conid": 999999, "position": 0, "avgCost": 0.0, "account": "U28849893"},
    ]


def test_positions_empty_when_no_positions_held(fake_ib):
    gateway.connect(BASE)
    fake_ib.positions_result = []
    assert gateway.positions(BASE, "U28849893") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ibkr_bot/test_gateway.py -v -k positions`
Expected: FAIL — `positions` n'existe pas encore.

- [ ] **Step 3: Implémenter**

```python
# --- positions -----------------------------------------------------

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ibkr_bot/test_gateway.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ibkr_bot/gateway.py tests/ibkr_bot/test_gateway.py
git commit -m "feat(ibkr_bot): positions via ib_async (gateway.py 3/5)"
```

---

### Task 4: Résolution de contrats et taux de change (`search_contract`, `contract_info`, `exchange_rate`)

**Files:**
- Modify: `ibkr_bot/gateway.py`
- Test: `tests/ibkr_bot/test_gateway.py`

**Interfaces:**
- Consumes: `_require_ib()` (Tâche 1).
- Produces: `search_contract(base_url, symbol) -> list[dict]`,
  `contract_info(base_url, conid) -> dict`, `exchange_rate(base_url, source, target) -> float`
  — formes inchangées, **et compatibles avec le filtrage EXISTANT de
  `ibkr_bot/contracts.py::resolve_conid()`**, qui n'est modifié par
  AUCUNE tâche de ce plan (contrainte globale).

**Contexte — c'est la tâche la plus délicate du plan.**
`contracts.py::resolve_conid()` (lu intégralement pendant la rédaction de
ce plan, `ibkr_bot/contracts.py:108-220`) filtre les candidats renvoyés
par `search_contract()` sur exactement ces clés :
- `candidat.get("symbol")` — doit égaler le symbole recherché.
- `candidat.get("sections")` — liste de dicts, doit contenir au moins un
  `{"secType": "STK"}` (fonction `_a_une_section_action`).
- `candidat.get("description")` — comparé à la liste des bourses
  attendues (`venue["exchanges"]`) ; c'est ce champ, pas un champ nommé
  "exchange", qui porte le code de bourse côté CPAPI.
- `candidat.get("conid")` — l'identifiant à résoudre.

Puis pour chaque candidat retenu, `info_fn(conid)` (= `contract_info`)
doit renvoyer un dict avec `"currency"` et `"listingExchange"` (avec repli
sur `candidat.get("description")` si absent).

La TWS API n'a pas d'équivalent direct de la recherche libre par symbole
du CPAPI (`/iserver/secdef/search`, qui renvoie plusieurs candidats
ambigus tous marchés confondus pour un symbole nu). L'approche retenue :
interroger `ib.reqContractDetails(Stock(symbol, "SMART", ""))` avec un
contrat **sous-spécifié** (symbole seul, `exchange="SMART"`, devise
vide) — IBKR renvoie alors, comme pour la recherche CPAPI, la liste de
tous les contrats correspondants sur toutes les places/devises connues.
Chaque `ContractDetails` de la liste est ensuite traduit en dict au
format déjà attendu par `resolve_conid()` :

```python
{
    "symbol": details.contract.symbol,
    "conid": details.contract.conId,
    "description": details.contract.primaryExchange,  # PAS .exchange, qui vaut souvent "SMART"
    "sections": [{"secType": details.contract.secType}],
}
```

**[À VÉRIFIER EN TÂCHE]** : que `ib.reqContractDetails(Stock(symbol, "SMART", ""))`
avec un contrat sous-spécifié renvoie bien plusieurs `ContractDetails`
(un par place/devise) pour un symbole coté sur plusieurs marchés,
exactement comme le fait la recherche CPAPI — c'est l'hypothèse
structurante de cette tâche. Vérifier avec un symbole réellement
multi-coté (ex. royalties/ADR connus) sur une connexion paper avant de
considérer cette tâche terminée. Si `reqContractDetails` ne renvoie qu'un
seul résultat par appel (une candidature déjà désambiguïsée), il faudra
itérer explicitement sur `venue["exchanges"]` côté `search_contract` — un
changement d'approche à documenter dans le rapport de tâche si nécessaire
(mais ne PAS changer `contracts.py` pour s'adapter : c'est `gateway.py`
qui doit produire la forme attendue).

Pour `contract_info(conid)` : `ib.reqContractDetails(Contract(conId=conid))`
puis lecture de `.contract.currency` et `.contract.primaryExchange` (pas
`.exchange`, qui vaut souvent `"SMART"` pour un contrat routé — piège
documenté dans la recherche de ce plan).

Pour `exchange_rate(source, target)` : pas d'appel RPC direct côté TWS
API — le pattern standard de la communauté est de demander des données de
marché sur un contrat `Forex` et de lire le prix résultant :
```python
fx = Forex(f"{source}{target}")
ticker = ib.reqMktData(fx, "", False, False)
ib.sleep(2)
rate = ticker.marketPrice()
ib.cancelMktData(fx)
```

- [ ] **Step 1: Écrire les tests**

```python
class _FakeContractFull:
    def __init__(self, symbol, conId, currency, primaryExchange, secType="STK"):
        self.symbol = symbol
        self.conId = conId
        self.currency = currency
        self.primaryExchange = primaryExchange
        self.secType = secType


class _FakeContractDetails:
    def __init__(self, contract):
        self.contract = contract


class _FakeTicker:
    def __init__(self, price):
        self._price = price

    def marketPrice(self):
        return self._price


# Dans _FakeIB.__init__, ajouter :
#     self.contract_details_result = []
#     self.mkt_data_result = None
#     self.cancel_mkt_data_calls = []
# Et ajouter les methodes :
#     def reqContractDetails(self, contract):
#         return self.contract_details_result
#     def reqMktData(self, contract, *a, **k):
#         return self.mkt_data_result
#     def cancelMktData(self, contract):
#         self.cancel_mkt_data_calls.append(contract)


def test_search_contract_translates_contract_details_list(fake_ib):
    gateway.connect(BASE)
    fake_ib.contract_details_result = [
        _FakeContractDetails(_FakeContractFull("SAP", 12345, "EUR", "IBIS")),
        _FakeContractDetails(_FakeContractFull("SAP", 67890, "USD", "NYSE")),
    ]
    result = gateway.search_contract(BASE, "SAP")
    assert result == [
        {"symbol": "SAP", "conid": 12345, "description": "IBIS",
         "sections": [{"secType": "STK"}]},
        {"symbol": "SAP", "conid": 67890, "description": "NYSE",
         "sections": [{"secType": "STK"}]},
    ]


def test_search_contract_returns_empty_list_on_no_match(fake_ib):
    gateway.connect(BASE)
    fake_ib.contract_details_result = []
    assert gateway.search_contract(BASE, "INCONNU") == []


def test_contract_info_reads_currency_and_primary_exchange(fake_ib):
    gateway.connect(BASE)
    fake_ib.contract_details_result = [
        _FakeContractDetails(_FakeContractFull("SAP", 12345, "EUR", "IBIS")),
    ]
    assert gateway.contract_info(BASE, 12345) == {
        "currency": "EUR", "listingExchange": "IBIS",
    }


def test_contract_info_empty_dict_when_not_found(fake_ib):
    gateway.connect(BASE)
    fake_ib.contract_details_result = []
    assert gateway.contract_info(BASE, 999) == {}


def test_exchange_rate_reads_forex_market_price(fake_ib):
    gateway.connect(BASE)
    fake_ib.mkt_data_result = _FakeTicker(0.86)
    rate = gateway.exchange_rate(BASE, "EUR", "GBP")
    assert rate == 0.86
    assert fake_ib.cancel_mkt_data_calls  # nettoyage de l'abonnement


def test_exchange_rate_same_currency_is_one_without_network_call(fake_ib):
    gateway.connect(BASE)
    assert gateway.exchange_rate(BASE, "EUR", "EUR") == 1.0
    assert fake_ib.mkt_data_result is None  # aucun appel reqMktData necessaire
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ibkr_bot/test_gateway.py -v -k "search_contract or contract_info or exchange_rate"`
Expected: FAIL.

- [ ] **Step 3: Implémenter**

```python
# --- contrats et change ----------------------------------------------

def search_contract(base_url: str, symbol: str) -> list[dict]:
    """Contrat sous-specifie (exchange="SMART", devise vide) : IBKR
    renvoie tous les contrats correspondants toutes places confondues,
    comme le faisait /iserver/secdef/search cote CPAPI. Traduit chaque
    ContractDetails dans la forme deja filtree par
    contracts.py::resolve_conid() — "description" porte le code de
    bourse (primaryExchange, PAS exchange qui vaut souvent "SMART"),
    "sections" simule la forme CPAPI pour que
    _a_une_section_action() continue de fonctionner sans modification."""
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ibkr_bot/test_gateway.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ibkr_bot/gateway.py tests/ibkr_bot/test_gateway.py
git commit -m "feat(ibkr_bot): resolution de contrats et taux de change via ib_async (gateway.py 4/5)"
```

---

### Task 5: Ordres (`place_market_order`, `confirm_reply`, `order_status`) — REVUE MAXIMALEMENT SCRUTÉE

**Files:**
- Modify: `ibkr_bot/gateway.py`
- Test: `tests/ibkr_bot/test_gateway.py`

**Interfaces:**
- Consumes: `_require_ib()` (Tâche 1).
- Produces: `place_market_order(base_url, account_id, conid, side, quantity) -> list[dict]`,
  `confirm_reply(base_url, reply_id, confirmed=True) -> list[dict]`,
  `order_status(base_url, order_id) -> dict` — formes inchangées (liste
  d'un seul dict `{"order_id": ...}` pour une exécution réussie ; dict
  avec `"avgPrice"`/`"commission"` pour le statut).

**⚠️ C'est le seul chemin du dépôt par lequel de l'argent réel peut
bouger. Revue de tâche maximalement scrutinisée, comme pour la Tâche 5
originale du Plan A (`broker.py`) et la Tâche 4 originale du Plan B
(`_place_order`).**

**Contexte — divergence structurante par rapport au CPAPI, à ne PAS
essayer de faire disparaître silencieusement :**

Le CPAPI peut répondre à un ordre par une « question de confirmation »
(marché fermé, taille inhabituelle...), que `daily.py::_resoudre_confirmations`
résout en boucle via `confirm_reply()`. **La TWS API n'a pas
d'équivalent programmatique de ce mécanisme.** Les mêmes avertissements
existent côté IB Gateway, mais sous forme de **popups graphiques**
(Global Configuration → API → Precautions) — inutilisables dans un
Gateway headless sans personne pour cliquer. La seule solution est de
**pré-cocher ces cases côté configuration IBC** (`BypassOrderPrecautions=yes`
et les réglages `Bypass*` frères — voir Tâche 7, déploiement) : avec cette
configuration, IB Gateway n'émet jamais de popup, et `placeOrder()` va
directement à l'exécution.

**Conséquence pour ce module : `place_market_order()` ne renvoie JAMAIS
une "question" — toujours directement une confirmation d'ordre**, à
condition que le déploiement (Tâche 7) ait bien configuré les
`Bypass*`. `confirm_reply()` est gardée dans l'interface pour ne pas
toucher à `daily.py::_resoudre_confirmations` (contrainte globale), mais
elle **ne doit jamais être atteinte en pratique** — si elle l'est, c'est
que la configuration Bypass a une lacune, ce qui doit être signalé
bruyamment plutôt que silencieusement absorbé. Elle lève donc une
exception explicite plutôt que de faire semblant de répondre à une
question qu'elle ne sait pas traiter :

```python
raise NotImplementedError(
    "confirm_reply() a ete appelee : la configuration IBC "
    "BypassOrderPrecautions (et reglages Bypass* freres) n'empeche pas "
    "toutes les popups de confirmation IB Gateway. A corriger cote "
    "deploiement (deploy/README-ibkr.md), pas cote code.")
```

Cette exception remonte jusqu'à `daily.py::_place_order`'s `try/except`
autour de `_resoudre_confirmations` (`ibkr_bot/daily.py:296-301`, lu
pendant la rédaction de ce plan), qui la transforme proprement en statut
`"erreur"` journalisé — **aucun ordre n'est perdu silencieusement**, le
comportement de repli existant de `daily.py` (inchangé) suffit.

`order_status(order_id)` : puisque `place_market_order` et
`order_status` sont appelés dans la MÊME connexion/le même processus
batch (jamais entre deux runs différents — `daily.py` ouvre et ferme la
connexion à chaque batch), on garde le `Trade` renvoyé par `placeOrder()`
dans un cache module-level indexé par `order_id`, plutôt que de refaire
une requête réseau.

- [ ] **Step 1: Écrire les tests**

```python
class _FakeOrderStatus:
    def __init__(self, status="Filled", avgFillPrice=0.0, orderId=0):
        self.status = status
        self.avgFillPrice = avgFillPrice
        self.orderId = orderId


class _FakeOrder:
    def __init__(self, orderId):
        self.orderId = orderId


class _FakeTrade:
    def __init__(self, orderId, avgFillPrice=0.0, status="Filled"):
        self.order = _FakeOrder(orderId)
        self.orderStatus = _FakeOrderStatus(status=status, avgFillPrice=avgFillPrice, orderId=orderId)


# Dans _FakeIB.__init__, ajouter :
#     self.place_order_result = None
#     self.place_order_calls = []
# Et ajouter la methode :
#     def placeOrder(self, contract, order):
#         self.place_order_calls.append((contract, order))
#         return self.place_order_result


def test_place_market_order_returns_a_single_confirmation(fake_ib):
    gateway.connect(BASE)
    fake_ib.place_order_result = _FakeTrade(orderId=555, avgFillPrice=0.0, status="Submitted")
    result = gateway.place_market_order(BASE, "U28849893", 265598, "BUY", 10)
    assert result == [{"order_id": "555"}]
    contract, order = fake_ib.place_order_calls[0]
    assert contract.conId == 265598
    assert order.action == "BUY"
    assert order.totalQuantity == 10
    assert order.tif == "DAY"


def test_place_market_order_rejects_invalid_side():
    with pytest.raises(ValueError):
        gateway.place_market_order(BASE, "U28849893", 265598, "HOLD", 10)


def test_place_market_order_rejects_invalid_quantity():
    with pytest.raises(ValueError):
        gateway.place_market_order(BASE, "U28849893", 265598, "BUY", 0)


def test_confirm_reply_raises_loudly_instead_of_pretending_to_answer(fake_ib):
    gateway.connect(BASE)
    with pytest.raises(NotImplementedError):
        gateway.confirm_reply(BASE, "some-reply-id")


def test_order_status_reads_the_cached_trade_for_this_order_id(fake_ib):
    gateway.connect(BASE)
    fake_ib.place_order_result = _FakeTrade(orderId=555, avgFillPrice=123.45, status="Filled")
    gateway.place_market_order(BASE, "U28849893", 265598, "BUY", 10)
    status = gateway.order_status(BASE, "555")
    assert status == {"order_status": "Filled", "avgPrice": 123.45, "commission": None}


def test_order_status_empty_dict_when_order_id_unknown(fake_ib):
    gateway.connect(BASE)
    assert gateway.order_status(BASE, "unknown-id") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ibkr_bot/test_gateway.py -v -k "place_market_order or confirm_reply or order_status"`
Expected: FAIL.

- [ ] **Step 3: Implémenter**

```python
# --- ordres ----------------------------------------------------------
# ATTENTION : les fonctions ci-dessous sont le seul chemin par lequel de
# l'argent reel peut bouger. Dans ce plan, elles sont couvertes
# UNIQUEMENT par des tests avec IB mocke (voir
# tests/ibkr_bot/test_order_routes_are_isolated.py, Tache 8, qui
# verifie qu'aucun autre module de ibkr_bot/ ne les appelle).

_trades_by_order_id: dict[str, "object"] = {}


def place_market_order(base_url: str, account_id: str, conid: int,
                       side: str, quantity: int) -> list[dict]:
    """Ordre au marche (MKT), valable le jour (DAY) — meme forme de
    retour que l'ancien CPAPI : [{"order_id": "..."}]. Ne renvoie JAMAIS
    de "question" de confirmation : ce mecanisme n'existe pas cote TWS
    API, les popups d'avertissement equivalentes sont pre-desactivees
    cote configuration IBC (BypassOrderPrecautions et freres, Tache 7).
    Si cette hypothese s'avere fausse en production, l'ordre resterait
    simplement bloque en attente au lieu de renvoyer une confirmation —
    un signal visible (batch qui timeout), pas un ordre perdu en
    silence."""
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side doit valoir 'BUY' ou 'SELL', recu {side!r}")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        raise ValueError(f"quantite doit etre un entier >= 1, recue {quantity!r}")
    ib = _require_ib()
    contract = Contract(conId=int(conid))
    order = MarketOrder(side, quantity)
    order.tif = "DAY"
    trade = ib.placeOrder(contract, order)
    order_id = str(trade.order.orderId)
    _trades_by_order_id[order_id] = trade
    return [{"order_id": order_id}]


def confirm_reply(base_url: str, reply_id: str, confirmed: bool = True) -> list[dict]:
    """Ne doit jamais etre atteinte en pratique (voir docstring de la
    Tache 5 du plan) : leve bruyamment plutot que de repondre a une
    question que la TWS API ne pose structurellement jamais."""
    raise NotImplementedError(
        "confirm_reply() a ete appelee : la configuration IBC "
        "BypassOrderPrecautions (et reglages Bypass* freres) n'empeche pas "
        "toutes les popups de confirmation IB Gateway. A corriger cote "
        "deploiement (deploy/README-ibkr.md), pas cote code.")


def order_status(base_url: str, order_id: str) -> dict:
    """Lit le Trade mis en cache par place_market_order() dans la MEME
    connexion (le batch ouvre/ferme une connexion par jour, jamais de
    suivi d'ordre entre deux runs) — evite une requete reseau separee.
    {} si cet order_id n'a pas ete vu dans cette session."""
    trade = _trades_by_order_id.get(str(order_id))
    if trade is None:
        return {}
    return {"order_status": trade.orderStatus.status,
            "avgPrice": trade.orderStatus.avgFillPrice or None,
            "commission": None}
```

Ajouter `Contract` à l'import en tête de fichier (déjà utilisé en Tâche
4 via un import local dans `contract_info` — le remonter en haut du
fichier avec les autres imports `ib_async` pour éviter la duplication) :
```python
from ib_async import IB, Stock, Forex, MarketOrder, Contract
```
(et supprimer l'import local `from ib_async import Contract` ajouté dans
`contract_info` à la Tâche 4).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ibkr_bot/test_gateway.py -v`
Expected: PASS — l'intégralité de `test_gateway.py`.

- [ ] **Step 5: Commit**

```bash
git add ibkr_bot/gateway.py tests/ibkr_bot/test_gateway.py
git commit -m "feat(ibkr_bot): ordres via ib_async (gateway.py 5/5)"
```

---

### Task 6: Cycle de connexion dans `daily.py` (`preflight`/`_terminer`)

**Files:**
- Modify: `ibkr_bot/daily.py:100-127` (`preflight`), `ibkr_bot/daily.py:434-454` (`_terminer`)
- Test: `tests/ibkr_bot/test_daily.py`

**Interfaces:**
- Consumes: `gateway.connect(base_url, *, client_id, account_id) -> bool`,
  `gateway.disconnect() -> None` (Tâche 1).
- Produces: aucune interface nouvelle — `preflight()` et `_terminer()`
  gardent leurs signatures actuelles.

**Contexte** — voir la section « Décision de conception » en tête de ce
plan. Seule tâche qui touche `daily.py`.

- [ ] **Step 1: Lire les tests existants de `preflight`/`_terminer`**

Ouvrir `tests/ibkr_bot/test_daily.py` et repérer les tests actuels de
`preflight()` (ils utilisent probablement un faux `gw` avec un
`is_authenticated` contrôlable) et de `_terminer()`. Ce plan ne les
réécrit pas intégralement : il ajoute les assertions ci-dessous à côté
des tests existants, et adapte le faux `gw` pour exposer aussi
`connect`/`disconnect` (sinon `AttributeError` dès l'appel).

- [ ] **Step 2: Écrire les tests additionnels (ils échoueront, le code n'appelle pas encore connect/disconnect)**

```python
def test_preflight_calls_connect_before_checking_is_authenticated():
    appels = []

    class FauxGw:
        DEFAULT_GATEWAY_URL = "127.0.0.1:4002"

        def connect(self, base_url, **kwargs):
            appels.append(("connect", base_url))
            return True

        def is_authenticated(self, base_url):
            return True

        def brokerage_accounts(self, base_url):
            return {}

        def portfolio_accounts(self, base_url):
            return []

    resultat = daily.preflight("127.0.0.1:4002", gw=FauxGw(), sleep_fn=lambda s: None)
    assert resultat["ok"] is True
    assert appels[0] == ("connect", "127.0.0.1:4002")


def test_preflight_retries_connect_on_failure_like_is_authenticated():
    tentatives = []

    class FauxGw:
        DEFAULT_GATEWAY_URL = "127.0.0.1:4002"

        def connect(self, base_url, **kwargs):
            tentatives.append(1)
            return len(tentatives) >= 2  # echoue la 1ere fois, reussit la 2eme

        def is_authenticated(self, base_url):
            return True

        def brokerage_accounts(self, base_url):
            return {}

        def portfolio_accounts(self, base_url):
            return []

    resultat = daily.preflight("127.0.0.1:4002", gw=FauxGw(), sleep_fn=lambda s: None,
                               attempts=3, delay_s=0)
    assert resultat["ok"] is True
    assert resultat["tentatives"] == 2


def test_terminer_calls_disconnect(tmp_path):
    appels = []

    class FauxGw:
        def disconnect(self):
            appels.append("disconnect")

    import ibkr_bot.journal as journal
    import ibkr_bot.notify as notify

    chemins = {
        "journal": str(tmp_path / "journal.jsonl"),
        "state": str(tmp_path / "state.json"),
        "positions": str(tmp_path / "positions.json"),
        "conid_cache": str(tmp_path / "conid_cache.json"),
        "indices": str(tmp_path / "indices.json"),
        "tracking": str(tmp_path / "tracking.json"),
        "account_snapshot": str(tmp_path / "account.json"),
    }
    monkeypatch_notify_no_op(notify)  # aide existante du fichier de test, ou inline si absente
    run = daily._nouveau_run("2026-09-16", "dry_run")
    daily._terminer(run, chemins, gw=FauxGw())
    assert appels == ["disconnect"]
```

Note pour l'implémenteur : si `monkeypatch_notify_no_op` n'existe pas
dans `test_daily.py`, remplacer cet appel par un `monkeypatch.setattr`
direct sur `notify.send_daily_summary`/`notify.send_gateway_alert` — lire
comment les tests existants de `_terminer()` gèrent déjà cette
dépendance et suivre la même convention plutôt que d'introduire un
nouveau motif.

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/ibkr_bot/test_daily.py -v -k "preflight or terminer"`
Expected: FAIL — `preflight()`/`_terminer()` n'acceptent pas encore ces
comportements, ou le faux `gw` n'a pas `connect`/`disconnect` reconnus.

- [ ] **Step 4: Modifier `preflight()` et `_terminer()`**

Dans `ibkr_bot/daily.py`, modifier la boucle de `preflight()` (garder le
docstring existant, ajouter seulement l'appel à `connect`) :

```python
def preflight(base_url: str, *, gw=gateway, sleep_fn=time.sleep,
              attempts: int = PREFLIGHT_ATTEMPTS,
              delay_s: float = PREFLIGHT_DELAY_SECONDS,
              account_id: str | None = None) -> dict:
    """[docstring existant inchange]"""
    detail = ""
    for tentative in range(1, attempts + 1):
        try:
            gw.connect(base_url, account_id=account_id or "")
            if gw.is_authenticated(base_url):
                gw.brokerage_accounts(base_url)
                gw.portfolio_accounts(base_url)
                return {"ok": True, "tentatives": tentative,
                        "detail": "authentifie, session amorcee"}
            detail = "session non authentifiee"
        except Exception as e:
            detail = f"amorcage de session impossible : {e}"
        if tentative < attempts:
            sleep_fn(delay_s)
    return {"ok": False, "tentatives": attempts, "detail": detail}
```

Dans `run_batch()`, passer `account_id` à l'appel de `preflight()`
existant (`ibkr_bot/daily.py:533`) :
```python
run["preflight"] = preflight(base_url, gw=gw, sleep_fn=sleep_fn, account_id=account_id)
```

Dans `_terminer()`, ajouter la déconnexion (garder tout le reste
identique) :

```python
def _terminer(run: dict, chemins: dict, *, alerte_gateway: bool = False, gw=gateway) -> dict:
    """[docstring existant inchange]"""
    try:
        gw.disconnect()
    except Exception:
        # Une deconnexion ratee ne doit jamais faire perdre la trace du
        # batch (meme raisonnement que journal.append_run juste en
        # dessous : le compte-rendu prime toujours sur le nettoyage).
        pass
    if not journal.append_run(run, chemins["journal"]):
        run["erreurs"].append({
            "etape": "journalisation",
            "detail": f"echec d'ecriture dans {chemins['journal']}",
        })
    if alerte_gateway:
        notify.send_gateway_alert(run["preflight"]["tentatives"], run["date"])
    else:
        notify.send_daily_summary(run, run["date"])
    return run
```

Et propager `gw` aux deux appels internes de `_terminer()` déjà présents
dans `run_batch()` (chercher `_terminer(run, chemins` dans le fichier —
il y a plusieurs points de sortie, ex. lignes ~493, ~517, ~530, ~536,
566 au moment de la rédaction de ce plan ; ajouter `, gw=gw` à CHACUN
d'eux, pas seulement au dernier).

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/ibkr_bot/test_daily.py -v`
Expected: PASS — l'intégralité de la suite `test_daily.py` (pas
seulement les nouveaux tests : vérifier qu'aucun test existant n'est
cassé par l'ajout du paramètre `gw=gw`/`account_id` aux appels internes).

- [ ] **Step 6: Commit**

```bash
git add ibkr_bot/daily.py tests/ibkr_bot/test_daily.py
git commit -m "feat(ibkr_bot): cycle connect/disconnect TWS API dans preflight/_terminer"
```

---

### Task 7: Déploiement VPS — IB Gateway + IBC + Docker (en parallèle du Client Portal Gateway)

**Files:**
- Create: `deploy/docker-compose-ibkr-tws.yml`
- Create: `deploy/README-ibkr-tws.md`
- Modify: `deploy/README-ibkr.md` (ajoute une section pointant vers le nouveau fichier)
- Opérations SSH directes sur le VPS (178.105.186.220) — pas de test pytest pour cette tâche, c'est de l'infrastructure.

**Contexte** — installation en parallèle (spec §2 point 7) : le Client
Portal Gateway existant (`ibkr-gateway.service`) n'est pas touché tant
que la nouvelle voie n'est pas validée en dry_run sur plusieurs jours.
Utilise `gnzsnz/ib-gateway-docker` (image Docker maintenue, wrappe IB
Gateway + IBC + Xvfb + VNC), pas une installation manuelle d'IBC (projet
archivé depuis le 2026-09-01, voir spec §1).

- [ ] **Step 1: Installer Docker sur le VPS (si absent)**

```bash
ssh root@178.105.186.220 "which docker || (curl -fsSL https://get.docker.com | sh)"
ssh root@178.105.186.220 "systemctl enable --now docker && systemctl is-active docker"
```

- [ ] **Step 2: Écrire le fichier `docker-compose-ibkr-tws.yml`**

```yaml
# deploy/docker-compose-ibkr-tws.yml
# IB Gateway + IBC (login automatise) + VNC (pour valider le 2FA
# manuellement chaque jour) — remplace a terme ibkr-gateway.service
# (Client Portal Gateway), installe EN PARALLELE tant que la bascule
# n'est pas validee (spec 2026-09-16, section 2 point 7).
services:
  ib-gateway:
    image: ghcr.io/gnzsnz/ib-gateway:latest
    container_name: ibkr-tws-gateway
    restart: unless-stopped
    environment:
      TWS_USERID_FILE: /run/secrets/ibkr_user
      TWS_PASSWORD_FILE: /run/secrets/ibkr_password
      TRADING_MODE: paper  # bascule a "live" une fois la connectivite validee (spec 8, point ouvert)
      VNC_SERVER_PASSWORD_FILE: /run/secrets/vnc_password
      READ_ONLY_API: "no"
    secrets:
      - ibkr_user
      - ibkr_password
      - vnc_password
    ports:
      - "127.0.0.1:4001:4003"  # port reel (host:conteneur)
      - "127.0.0.1:4002:4004"  # port paper (host:conteneur)
      - "127.0.0.1:5900:5900"  # VNC — jamais expose au-dela de localhost, meme regle que le port 5000 du CPAPI (tunnel SSH obligatoire)

secrets:
  ibkr_user:
    file: /home/ibkrbot/secrets/ibkr_user.txt
  ibkr_password:
    file: /home/ibkrbot/secrets/ibkr_password.txt
  vnc_password:
    file: /home/ibkrbot/secrets/vnc_password.txt
```

- [ ] **Step 3: Créer les fichiers de secrets sur le VPS (jamais commités)**

```bash
ssh root@178.105.186.220 "mkdir -p /home/ibkrbot/secrets && chown ibkrbot:ibkrbot /home/ibkrbot/secrets && chmod 700 /home/ibkrbot/secrets"
# Puis, INTERACTIVEMENT avec l'utilisateur (identifiants IBKR reels,
# jamais colles en clair dans l'historique de commandes) :
#   echo -n "<identifiant IBKR>" > /home/ibkrbot/secrets/ibkr_user.txt
#   echo -n "<mot de passe IBKR>" > /home/ibkrbot/secrets/ibkr_password.txt
#   echo -n "<mot de passe VNC choisi>" > /home/ibkrbot/secrets/vnc_password.txt
#   chmod 600 /home/ibkrbot/secrets/*.txt
```

- [ ] **Step 4: Configurer les réglages IBC via variables d'environnement du conteneur**

Compléter la section `environment:` de `docker-compose-ibkr-tws.yml`
avec les réglages qui pré-désactivent les popups de confirmation d'ordre
(indispensable pour la Tâche 5 — `place_market_order()` suppose qu'elles
n'apparaissent jamais) :

```yaml
      BYPASS_WARNING: "yes"
```

Note pour l'implémenteur : `gnzsnz/ib-gateway-docker` expose les
réglages `Bypass*` d'IBC sous des noms de variables d'environnement
propres à l'image (pas nécessairement `BYPASS_WARNING` tel quel) —
**[À VÉRIFIER EN TÂCHE]** contre le README réel du projet
(`github.com/gnzsnz/ib-gateway-docker`) au moment du déploiement,
puisque ce plan a été écrit sans connexion Docker Hub/GHCR active pour
lister les tags/variables exacts de la dernière version. Vérifier
spécifiquement le nom de variable équivalent à `BypassOrderPrecautions`
et ses réglages frères (§9 de la recherche de ce plan) avant de
démarrer le conteneur en environnement réel.

- [ ] **Step 5: Démarrer le conteneur et valider la connectivité TWS API**

```bash
ssh root@178.105.186.220 "cd /home/ibkrbot/analyse-or/deploy && docker compose -f docker-compose-ibkr-tws.yml up -d"
ssh root@178.105.186.220 "docker logs -f ibkr-tws-gateway"  # surveiller le demarrage
```

Le login IBKR + 2FA doit être validé manuellement une première fois via
VNC : `ssh -L 5900:127.0.0.1:5900 root@178.105.186.220`, puis un client
VNC (ex. TigerVNC, RealVNC) pointé sur `127.0.0.1:5900` avec le mot de
passe créé à l'étape 3.

Une fois connecté, vérifier depuis le VPS (pas besoin de tunnel
supplémentaire, `ib_async` n'est pas encore installé côté système —
utiliser un script Python ponctuel dans le venv du bot) :
```bash
ssh root@178.105.186.220 "sudo -u ibkrbot /home/ibkrbot/analyse-or/.venv/bin/python -c \"
from ib_async import IB
ib = IB()
ib.connect('127.0.0.1', 4002, clientId=99, timeout=15)
print('connecte:', ib.isConnected())
print('comptes:', ib.managedAccounts())
ib.disconnect()
\""
```
Attendu : `connecte: True` et `comptes: ['...']` non vide.

- [ ] **Step 6: Documenter dans `deploy/README-ibkr-tws.md`**

Rédiger un README au même niveau de détail que `deploy/README-ibkr.md`
existant (procédure d'installation, où sont les secrets, comment
valider le login via VNC chaque jour, comment basculer `TRADING_MODE` de
`paper` à `live`, comment décommissionner l'ancien Client Portal Gateway
une fois validé). Ajouter dans `deploy/README-ibkr.md` une note en tête
pointant vers ce nouveau fichier et expliquant que le Client Portal
Gateway est en cours de remplacement.

- [ ] **Step 7: Mettre à jour `.env` (une fois la Tâche 6 déployée et validée en dry_run)**

Cette étape n'est PAS exécutée immédiatement à la fin de cette tâche —
elle attend la validation dry_run de plusieurs jours (spec §7 point 5).
La documenter dans `deploy/README-ibkr-tws.md` comme dernière étape de
la migration :
```
IBKR_GATEWAY_URL=127.0.0.1:4002   # puis 127.0.0.1:4001 une fois passe en reel
IBKR_ACCOUNT_ID=U28849893
```

- [ ] **Step 8: Commit (fichiers versionnés uniquement — jamais les secrets)**

```bash
git add deploy/docker-compose-ibkr-tws.yml deploy/README-ibkr-tws.md deploy/README-ibkr.md
git commit -m "deploy: IB Gateway + IBC via Docker, en parallele du Client Portal Gateway"
```

---

### Task 8: Garde-fou structurel — mettre à jour le test qui isole le chemin d'ordre réel

**Files:**
- Modify (probablement renommer) : le test qui scanne le dépôt pour des
  références au chemin d'envoi d'ordre hors `gateway.py` (rechercher
  `test_no_other_plan_a_module_references_the_order_routes` ou
  équivalent — mentionné dans l'en-tête de commentaire de l'ancien
  `gateway.py:11-13`, à localiser précisément en début de tâche avec
  `grep -rn "order_routes\|references_the_order" tests/`).

**Interfaces:**
- Consumes: rien de nouveau — relit `ibkr_bot/gateway.py` (Tâches 1-5)
  et le reste du dépôt.

**Contexte** — ce test protège la garantie structurelle « aucun module
hors `gateway.py` ne peut envoyer un ordre réel ». Il scannait
probablement le dépôt pour des littéraux du type
`"/iserver/account/.../orders"` ou pour l'usage direct de
`requests.post`/`gateway.place_market_order` hors de `gateway.py`
lui-même. Avec la migration, les nouveaux points sensibles sont
`ib_async.MarketOrder` et `IB.placeOrder` — un module qui les
importerait/appellerait directement, en contournant `gateway.py`,
échapperait à la protection actuelle si le test ne scanne que les
anciens littéraux REST.

- [ ] **Step 1: Localiser et lire le test existant**

```bash
grep -rn "order_routes\|references_the_order\|placeOrder\|MarketOrder" tests/
```
Lire le fichier trouvé en entier avant de le modifier.

- [ ] **Step 2: Étendre le test pour couvrir les nouveaux littéraux ib_async**

Ajouter au test existant (sans supprimer ses vérifications actuelles —
les deux motifs de littéraux peuvent coexister tant que l'ancien
`gateway.py` REST n'est pas physiquement supprimé du dépôt) une
vérification qu'aucun fichier de `ibkr_bot/` autre que `gateway.py` ne
contient les chaînes `"placeOrder"` ou `"MarketOrder"` ni n'importe
`ib_async` directement — même mécanique que la vérification existante
(probablement un parcours de fichiers + recherche de motif, ou une
analyse d'import). Adapter la forme exacte du test à ce qui existe déjà
plutôt que de réinventer une approche différente.

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/ -v -k "order_routes or references_the_order"` (ajuster
le nom réel trouvé à l'étape 1).
Expected: PASS — confirme qu'aucun autre module des Tâches 1-6 n'a
introduit d'appel direct à `ib_async` hors `gateway.py`.

- [ ] **Step 4: Run the full test suite**

Run: `pytest tests/ -v`
Expected: PASS intégralement — dernière vérification avant la revue de
branche finale.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test(ibkr_bot): etend le garde-fou d'isolation du chemin d'ordre reel a ib_async"
```

---

## Points ouverts non couverts par ce plan (hors périmètre)

- Bascule effective de `dry_run: true` → `false` et de `TRADING_MODE:
  paper` → `live` — après plusieurs jours de validation dry_run réussie
  (spec §7 point 5), décision opérateur manuelle via SSH, pas une tâche
  de ce plan.
- Décommissionnement du Client Portal Gateway (`ibkr-gateway.service`,
  `conf.yaml`, etc.) — après validation complète de la nouvelle voie
  (spec §7 point 6).
- Migration d'IBC vers un fork actif si le besoin de correctifs se
  présente un jour (projet archivé depuis le 2026-09-01, voir §1 de la
  spec et Tâche 7) — non nécessaire tant que la dernière version figée
  fonctionne.
