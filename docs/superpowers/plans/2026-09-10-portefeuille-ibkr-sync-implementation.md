# Portefeuille — synchronisation IBKR (phase 2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Synchroniser automatiquement, une fois par jour, les positions réellement détenues chez Interactive Brokers vers une nouvelle section en lecture seule de l'onglet Portefeuille — sans jamais pouvoir passer un ordre (le jeton Flex Query utilisé est structurellement un jeton de lecture de rapports).

**Architecture:** Un nouveau script `portfolio_sync.py`, exécuté par un cron GitHub Actions quotidien (même modèle que `indices_score.py`/`indices.yml`), interroge le Flex Web Service IBKR en deux appels HTTP (déclenchement puis récupération du rapport XML), extrait les positions ouvertes, les rapproche des ~180 entreprises déjà suivies quand c'est possible, et écrit `docs/real_portfolio.json`. Le frontend (`docs/index.html`) affiche ce fichier dans une nouvelle section de l'écran Portefeuille existant, séparée des positions saisies manuellement.

**Tech Stack:** Python (stdlib `xml.etree.ElementTree`, `requests` — déjà utilisés dans `indices_score.py`, aucune nouvelle dépendance), pytest, GitHub Actions, JS vanilla (aucun changement à `docs/portfolio.js`).

**Spec:** `docs/superpowers/specs/2026-09-10-portefeuille-ibkr-sync-design.md`

## Global Constraints

- **Aucune exécution d'ordre, réelle ou simulée** — le jeton Flex Query utilisé est structurellement incapable de passer un ordre ; aucun code de ce plan ne doit tenter d'appeler une API de trading IBKR.
- Fichier de sortie exact : `docs/real_portfolio.json`, structure `{updated, sync_status, sync_error, positions}` avec `sync_status ∈ {"ok", "error"}`.
- En cas d'échec de synchronisation, le fichier garde ses `positions` précédentes (jamais vidées par un échec) — seuls `updated`/`sync_status`/`sync_error` reflètent la dernière tentative.
- La section frontend est **séparée** des positions manuelles de la phase 1 — aucune fusion, aucun bouton modifier/supprimer sur les positions IBKR (lecture seule).
- Toutes les positions IBKR s'affichent, pas seulement celles qui correspondent aux ~180 entreprises suivies — le rapprochement (`matched_ticker`) n'est qu'un enrichissement optionnel, jamais un filtre.
- Secrets GitHub Actions : `IBKR_FLEX_TOKEN`, `IBKR_FLEX_QUERY_ID` (l'utilisateur les configure lui-même dans les paramètres du dépôt — aucune tâche de ce plan ne peut le faire à sa place).
- Chaque tâche qui modifie `docs/index.html` se termine par un bump de `CACHE_NAME` dans `docs/service-worker.js` (actuellement `"analyse-or-shell-v20"`).
- Déjà sur la branche `portefeuille` — pas besoin d'en créer une nouvelle. Chaque commit se termine par :
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  ```
- **Hypothèse assumée, à vérifier par l'utilisateur en conditions réelles** : ce plan suppose que les réponses d'erreur du Flex Web Service IBKR (`SendRequest` et `GetStatement`) suivent toutes deux le format `<FlexStatementResponse><ErrorCode>...</ErrorCode><ErrorMessage>...</ErrorMessage></FlexStatementResponse>`, observé dans la documentation IBKR publique — aucun compte IBKR réel n'est disponible dans cet environnement pour le confirmer contre une vraie réponse. Si le format réel diffère, `fetch_flex_reference_code`/`fetch_flex_statement` devront être ajustées après le premier essai réel (voir Task 3, dernière étape).

---

## File Structure

- **Create `portfolio_sync.py`** — script racine, même niveau que `indices_score.py`. Fonctions pures (appel HTTP, parsing XML, rapprochement de tickers) + un `main()` fin qui les enchaîne et écrit le JSON.
- **Create `tests/test_portfolio_sync.py`** — pytest, même densité et style de mock (`monkeypatch.setattr(module.requests, "get", ...)`) que `tests/test_indices_score.py`.
- **Create `.github/workflows/portfolio_sync.yml`** — cron quotidien, structure calquée sur `.github/workflows/indices.yml`.
- **Modify `docs/index.html`** — nouvelle fonction de rendu `realPortfolioHtml`, insérée dans `renderPortfolioScreen` (déjà existant, phase 1) ; nouveau fetch paresseux de `real_portfolio.json`.
- **Modify `docs/service-worker.js`** — bump `CACHE_NAME`.

---

### Task 1 : `portfolio_sync.py` — appels Flex Web Service

**Files:**
- Create: `portfolio_sync.py`
- Create: `tests/test_portfolio_sync.py`

**Interfaces:**
- Consumes : rien (première tâche).
- Produces (utilisé par Task 3) :
  - `FLEX_BASE_URL` (constante, `"https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"`)
  - `fetch_flex_reference_code(token: str, query_id: str) -> str` — lève `RuntimeError` si IBKR renvoie une erreur ou une réponse sans `ReferenceCode`.
  - `fetch_flex_statement(token: str, reference_code: str, max_attempts: int = 5, retry_delay_s: float = 3.0) -> str` — renvoie le texte XML du rapport ; lève `RuntimeError` après `max_attempts` tentatives si le rapport reste "en préparation", ou immédiatement si IBKR renvoie une erreur.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/test_portfolio_sync.py` :

```python
import pytest
import portfolio_sync


class _FakeFlexResponse:
    def __init__(self, text):
        self._text = text

    def raise_for_status(self):
        pass

    @property
    def text(self):
        return self._text


def test_fetch_flex_reference_code_returns_reference_code_on_success(monkeypatch):
    monkeypatch.setattr(
        portfolio_sync.requests, "get",
        lambda *a, **k: _FakeFlexResponse(
            '<FlexStatementResponse><ReferenceCode>1234567890</ReferenceCode>'
            '<Url>https://example.com</Url></FlexStatementResponse>'
        ),
    )
    assert portfolio_sync.fetch_flex_reference_code("tok", "qid") == "1234567890"


def test_fetch_flex_reference_code_raises_on_ibkr_error(monkeypatch):
    monkeypatch.setattr(
        portfolio_sync.requests, "get",
        lambda *a, **k: _FakeFlexResponse(
            '<FlexStatementResponse><ErrorCode>1003</ErrorCode>'
            '<ErrorMessage>Statement is not available.</ErrorMessage></FlexStatementResponse>'
        ),
    )
    with pytest.raises(RuntimeError, match="Statement is not available"):
        portfolio_sync.fetch_flex_reference_code("tok", "qid")


def test_fetch_flex_reference_code_raises_when_no_reference_and_no_error(monkeypatch):
    monkeypatch.setattr(
        portfolio_sync.requests, "get",
        lambda *a, **k: _FakeFlexResponse("<FlexStatementResponse></FlexStatementResponse>"),
    )
    with pytest.raises(RuntimeError, match="ReferenceCode"):
        portfolio_sync.fetch_flex_reference_code("tok", "qid")


def test_fetch_flex_statement_returns_xml_on_immediate_success(monkeypatch):
    xml = "<FlexQueryResponse>ok</FlexQueryResponse>"
    monkeypatch.setattr(portfolio_sync.requests, "get", lambda *a, **k: _FakeFlexResponse(xml))
    result = portfolio_sync.fetch_flex_statement("tok", "ref", max_attempts=3, retry_delay_s=0)
    assert result == xml


def test_fetch_flex_statement_retries_while_in_progress_then_succeeds(monkeypatch):
    xml = "<FlexQueryResponse>done</FlexQueryResponse>"
    responses = [
        _FakeFlexResponse("Statement generation in progress. Please try again shortly."),
        _FakeFlexResponse("Statement generation in progress. Please try again shortly."),
        _FakeFlexResponse(xml),
    ]
    calls = {"n": 0}

    def fake_get(*a, **k):
        resp = responses[calls["n"]]
        calls["n"] += 1
        return resp

    monkeypatch.setattr(portfolio_sync.requests, "get", fake_get)
    result = portfolio_sync.fetch_flex_statement("tok", "ref", max_attempts=5, retry_delay_s=0)
    assert result == xml
    assert calls["n"] == 3


def test_fetch_flex_statement_raises_after_max_attempts_still_in_progress(monkeypatch):
    monkeypatch.setattr(
        portfolio_sync.requests, "get",
        lambda *a, **k: _FakeFlexResponse("Statement generation in progress."),
    )
    with pytest.raises(RuntimeError, match="toujours en préparation"):
        portfolio_sync.fetch_flex_statement("tok", "ref", max_attempts=3, retry_delay_s=0)


def test_fetch_flex_statement_raises_on_ibkr_error(monkeypatch):
    monkeypatch.setattr(
        portfolio_sync.requests, "get",
        lambda *a, **k: _FakeFlexResponse(
            '<FlexStatementResponse><ErrorCode>1019</ErrorCode>'
            '<ErrorMessage>Token has expired.</ErrorMessage></FlexStatementResponse>'
        ),
    )
    with pytest.raises(RuntimeError, match="Token has expired"):
        portfolio_sync.fetch_flex_statement("tok", "ref", max_attempts=3, retry_delay_s=0)
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_portfolio_sync.py -v`
Expected: `ModuleNotFoundError: No module named 'portfolio_sync'`

- [ ] **Step 3: Écrire `portfolio_sync.py`**

```python
# portfolio_sync.py
# Synchronisation en lecture seule des positions Interactive Brokers via
# le Flex Web Service — voir
# docs/superpowers/specs/2026-09-10-portefeuille-ibkr-sync-design.md.
# Le jeton utilisé ne donne accès qu'à des rapports Flex configurés côté
# IBKR : il est structurellement incapable de passer un ordre.
import json
import os
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

FLEX_BASE_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"


def fetch_flex_reference_code(token: str, query_id: str) -> str:
    """Étape 1 du Flex Web Service : déclenche la génération du rapport.
    Renvoie le ReferenceCode à passer à fetch_flex_statement. Lève une
    RuntimeError si IBKR renvoie une erreur explicite (jeton expiré/
    révoqué, query_id invalide) ou une réponse sans ReferenceCode ni
    ErrorCode (format inattendu)."""
    resp = requests.get(
        f"{FLEX_BASE_URL}/SendRequest",
        params={"t": token, "q": query_id, "v": "3"},
        timeout=15,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    error_code = root.findtext("ErrorCode")
    if error_code:
        error_message = root.findtext("ErrorMessage") or "erreur IBKR inconnue"
        raise RuntimeError(f"Flex Query SendRequest a échoué ({error_code}) : {error_message}")
    reference_code = root.findtext("ReferenceCode")
    if not reference_code:
        raise RuntimeError("Flex Query SendRequest : réponse sans ReferenceCode ni ErrorCode")
    return reference_code


def fetch_flex_statement(
    token: str, reference_code: str, max_attempts: int = 5, retry_delay_s: float = 3.0
) -> str:
    """Étape 2 : récupère le rapport généré. IBKR peut mettre plusieurs
    secondes à le préparer — dans ce cas la réponse est un texte brut
    contenant "Statement generation in progress" (pas du XML bien
    formé), et on retente après retry_delay_s. Lève une RuntimeError si
    le rapport n'est toujours pas prêt après max_attempts tentatives, ou
    si IBKR renvoie une erreur explicite."""
    for attempt in range(max_attempts):
        resp = requests.get(
            f"{FLEX_BASE_URL}/GetStatement",
            params={"t": token, "q": reference_code, "v": "3"},
            timeout=15,
        )
        resp.raise_for_status()
        text = resp.text
        if "Statement generation in progress" in text:
            if attempt < max_attempts - 1:
                time.sleep(retry_delay_s)
            continue
        root = ET.fromstring(text)
        error_code = root.findtext("ErrorCode")
        if error_code:
            error_message = root.findtext("ErrorMessage") or "erreur IBKR inconnue"
            raise RuntimeError(f"Flex Query GetStatement a échoué ({error_code}) : {error_message}")
        return text
    raise RuntimeError(
        f"Flex Query GetStatement : rapport toujours en préparation après {max_attempts} tentatives"
    )


if __name__ == "__main__":
    pass
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_portfolio_sync.py -v`
Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add portfolio_sync.py tests/test_portfolio_sync.py
git commit -m "$(cat <<'EOF'
feat(portfolio-sync): appels Flex Web Service IBKR (SendRequest/GetStatement)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2 : `portfolio_sync.py` — parsing et rapprochement des tickers

**Files:**
- Modify: `portfolio_sync.py` (ajout)
- Modify: `tests/test_portfolio_sync.py` (ajout)

**Interfaces:**
- Consumes : rien de Task 1 directement (fonctions indépendantes, combinées dans Task 3).
- Produces (utilisé par Task 3) :
  - `parse_open_positions(xml_text: str) -> list[dict]` — chaque dict a les clés `ibkr_symbol, description, currency, quantity, current_price, position_value, cost_basis_price, cost_basis_value, unrealized_pnl`.
  - `match_tickers(positions: list[dict], companies: list[dict]) -> list[dict]` — chaque position d'entrée, avec une clé `matched_ticker` ajoutée (`str` ou `None`). `companies` est une liste de dicts ayant au moins une clé `"ticker"` (le format de `indicesData["companies"]`/`indices.json`).

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `tests/test_portfolio_sync.py` (après les tests de Task 1) :

```python
_SAMPLE_FLEX_XML = """<FlexQueryResponse queryName="Open Positions" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U1234567" fromDate="20260910" toDate="20260910">
      <OpenPositions>
        <OpenPosition symbol="MC" description="LVMH MOET HENNESSY LOUIS VUI" currency="EUR" position="10" markPrice="652.3" positionValue="6523.0" costBasisPrice="600.0" costBasisMoney="6000.0" fifoPnlUnrealized="523.0" side="Long"/>
        <OpenPosition symbol="AAPL" description="APPLE INC" currency="USD" position="5" markPrice="200.0" positionValue="1000.0" costBasisPrice="180.0" costBasisMoney="900.0" fifoPnlUnrealized="100.0" side="Long"/>
      </OpenPositions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""


def test_parse_open_positions_extracts_all_fields():
    result = portfolio_sync.parse_open_positions(_SAMPLE_FLEX_XML)
    assert len(result) == 2
    assert result[0] == {
        "ibkr_symbol": "MC",
        "description": "LVMH MOET HENNESSY LOUIS VUI",
        "currency": "EUR",
        "quantity": 10.0,
        "current_price": 652.3,
        "position_value": 6523.0,
        "cost_basis_price": 600.0,
        "cost_basis_value": 6000.0,
        "unrealized_pnl": 523.0,
    }
    assert result[1]["ibkr_symbol"] == "AAPL"
    assert result[1]["currency"] == "USD"


def test_parse_open_positions_empty_section_returns_empty_list():
    xml = (
        '<FlexQueryResponse><FlexStatements count="1">'
        '<FlexStatement><OpenPositions/></FlexStatement>'
        "</FlexStatements></FlexQueryResponse>"
    )
    assert portfolio_sync.parse_open_positions(xml) == []


def test_match_tickers_matches_cac40_by_stripping_yahoo_suffix():
    positions = [{
        "ibkr_symbol": "MC", "description": "LVMH", "currency": "EUR",
        "quantity": 10.0, "current_price": 652.3, "position_value": 6523.0,
        "cost_basis_price": 600.0, "cost_basis_value": 6000.0, "unrealized_pnl": 523.0,
    }]
    companies = [{"ticker": "MC.PA", "index": "CAC40"}]
    result = portfolio_sync.match_tickers(positions, companies)
    assert result[0]["matched_ticker"] == "MC.PA"


def test_match_tickers_matches_nasdaq_without_suffix():
    positions = [{
        "ibkr_symbol": "AAPL", "description": "APPLE", "currency": "USD",
        "quantity": 5.0, "current_price": 200.0, "position_value": 1000.0,
        "cost_basis_price": 180.0, "cost_basis_value": 900.0, "unrealized_pnl": 100.0,
    }]
    companies = [{"ticker": "AAPL", "index": "NASDAQ"}]
    result = portfolio_sync.match_tickers(positions, companies)
    assert result[0]["matched_ticker"] == "AAPL"


def test_match_tickers_is_case_insensitive():
    positions = [{
        "ibkr_symbol": "aapl", "description": "APPLE", "currency": "USD",
        "quantity": 5.0, "current_price": 200.0, "position_value": 1000.0,
        "cost_basis_price": 180.0, "cost_basis_value": 900.0, "unrealized_pnl": 100.0,
    }]
    companies = [{"ticker": "AAPL", "index": "NASDAQ"}]
    result = portfolio_sync.match_tickers(positions, companies)
    assert result[0]["matched_ticker"] == "AAPL"


def test_match_tickers_none_when_symbol_not_tracked():
    positions = [{
        "ibkr_symbol": "SPY", "description": "SPDR S&P 500 ETF", "currency": "USD",
        "quantity": 1.0, "current_price": 500.0, "position_value": 500.0,
        "cost_basis_price": 480.0, "cost_basis_value": 480.0, "unrealized_pnl": 20.0,
    }]
    companies = [{"ticker": "AAPL", "index": "NASDAQ"}]
    result = portfolio_sync.match_tickers(positions, companies)
    assert result[0]["matched_ticker"] is None


def test_match_tickers_preserves_all_original_fields():
    positions = [{
        "ibkr_symbol": "AAPL", "description": "APPLE", "currency": "USD",
        "quantity": 5.0, "current_price": 200.0, "position_value": 1000.0,
        "cost_basis_price": 180.0, "cost_basis_value": 900.0, "unrealized_pnl": 100.0,
    }]
    result = portfolio_sync.match_tickers(positions, [])
    assert result[0]["ibkr_symbol"] == "AAPL"
    assert result[0]["description"] == "APPLE"
    assert result[0]["matched_ticker"] is None
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_portfolio_sync.py -v -k "parse_open_positions or match_tickers"`
Expected: `AttributeError: module 'portfolio_sync' has no attribute 'parse_open_positions'`

- [ ] **Step 3: Ajouter à `portfolio_sync.py`**

Insérer avant le bloc `if __name__ == "__main__":` final :

```python
def parse_open_positions(xml_text: str) -> list[dict]:
    """Extrait chaque <OpenPosition> du rapport Flex en dict. Une section
    OpenPositions absente ou vide renvoie une liste vide (compte sans
    position ouverte — cas valide, pas une erreur)."""
    root = ET.fromstring(xml_text)
    positions = []
    for el in root.iter("OpenPosition"):
        positions.append({
            "ibkr_symbol": el.get("symbol"),
            "description": el.get("description"),
            "currency": el.get("currency"),
            "quantity": float(el.get("position")),
            "current_price": float(el.get("markPrice")),
            "position_value": float(el.get("positionValue")),
            "cost_basis_price": float(el.get("costBasisPrice")),
            "cost_basis_value": float(el.get("costBasisMoney")),
            "unrealized_pnl": float(el.get("fifoPnlUnrealized")),
        })
    return positions


def match_tickers(positions: list[dict], companies: list[dict]) -> list[dict]:
    """Ajoute matched_ticker à chaque position, par recherche du symbole
    IBKR (insensible à la casse) contre les tickers suivis avec leur
    suffixe Yahoo (.PA/.DE) retiré — AAPL/DOW n'ont pas de suffixe donc
    ne sont pas affectés par le split. None si aucune correspondance :
    pas une erreur, attendu pour tout ce qui n'est pas dans les indices
    suivis (ETF, obligations, actions hors périmètre)."""
    bare_to_ticker = {}
    for c in companies:
        ticker = c["ticker"]
        bare = ticker.split(".")[0].upper()
        bare_to_ticker.setdefault(bare, ticker)
    result = []
    for p in positions:
        symbol = (p.get("ibkr_symbol") or "").upper()
        result.append({**p, "matched_ticker": bare_to_ticker.get(symbol)})
    return result
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_portfolio_sync.py -v`
Expected: 14 tests PASS (7 de Task 1 + 7 nouveaux).

- [ ] **Step 5: Commit**

```bash
git add portfolio_sync.py tests/test_portfolio_sync.py
git commit -m "$(cat <<'EOF'
feat(portfolio-sync): parsing des positions et rapprochement des tickers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3 : `main()`, écriture du JSON, workflow GitHub Actions

**Files:**
- Modify: `portfolio_sync.py` (ajout de `main()` et des chemins de fichiers)
- Modify: `tests/test_portfolio_sync.py` (ajout)
- Create: `.github/workflows/portfolio_sync.yml`

**Interfaces:**
- Consumes : `fetch_flex_reference_code`, `fetch_flex_statement` (Task 1), `parse_open_positions`, `match_tickers` (Task 2).
- Produces : `docs/real_portfolio.json` sur disque, au format décrit dans le Global Constraints. Utilisé par Task 4 (frontend).

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `tests/test_portfolio_sync.py` :

```python
import json


def test_main_writes_error_status_when_credentials_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("IBKR_FLEX_TOKEN", raising=False)
    monkeypatch.delenv("IBKR_FLEX_QUERY_ID", raising=False)
    output_path = tmp_path / "real_portfolio.json"
    monkeypatch.setattr(portfolio_sync, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    portfolio_sync.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["sync_status"] == "error"
    assert "IBKR_FLEX_TOKEN" in written["sync_error"]
    assert written["positions"] == []


def test_main_writes_ok_status_and_matched_positions_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "qid")
    output_path = tmp_path / "real_portfolio.json"
    indices_path = tmp_path / "indices.json"
    indices_path.write_text(
        json.dumps({"companies": [{"ticker": "MC.PA", "index": "CAC40"}]}), encoding="utf-8"
    )
    monkeypatch.setattr(portfolio_sync, "REAL_PORTFOLIO_JSON_PATH", str(output_path))
    monkeypatch.setattr(portfolio_sync, "INDICES_JSON_PATH", str(indices_path))
    monkeypatch.setattr(portfolio_sync, "fetch_flex_reference_code", lambda token, query_id: "ref123")
    monkeypatch.setattr(
        portfolio_sync, "fetch_flex_statement", lambda token, reference_code: _SAMPLE_FLEX_XML
    )

    portfolio_sync.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["sync_status"] == "ok"
    assert written["sync_error"] is None
    assert len(written["positions"]) == 2
    by_symbol = {p["ibkr_symbol"]: p for p in written["positions"]}
    assert by_symbol["MC"]["matched_ticker"] == "MC.PA"
    assert by_symbol["AAPL"]["matched_ticker"] is None  # pas dans indices.json de ce test


def test_main_keeps_previous_positions_and_sets_error_on_fetch_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "qid")
    output_path = tmp_path / "real_portfolio.json"
    output_path.write_text(
        json.dumps({
            "updated": "2026-09-09T07:00:00Z", "sync_status": "ok", "sync_error": None,
            "positions": [{"ibkr_symbol": "MC", "matched_ticker": "MC.PA"}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(portfolio_sync, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    def _raise(token, query_id):
        raise RuntimeError("Token has expired.")

    monkeypatch.setattr(portfolio_sync, "fetch_flex_reference_code", _raise)

    portfolio_sync.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["sync_status"] == "error"
    assert "Token has expired" in written["sync_error"]
    assert written["positions"] == [{"ibkr_symbol": "MC", "matched_ticker": "MC.PA"}]


def test_main_starts_from_empty_state_when_output_file_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("IBKR_FLEX_TOKEN", raising=False)
    output_path = tmp_path / "does_not_exist" / "real_portfolio.json"
    monkeypatch.setattr(portfolio_sync, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    portfolio_sync.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["positions"] == []
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_portfolio_sync.py -v -k test_main`
Expected: `AttributeError: module 'portfolio_sync' has no attribute 'REAL_PORTFOLIO_JSON_PATH'`

- [ ] **Step 3: Ajouter à `portfolio_sync.py`**

Remplacer le bloc final `if __name__ == "__main__": pass` par :

```python
REAL_PORTFOLIO_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "real_portfolio.json"
)

INDICES_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "indices.json"
)


def _load_existing_real_portfolio() -> dict:
    """État de repli si le fichier n'existe pas encore ou est illisible —
    jamais d'exception au démarrage du script."""
    try:
        with open(REAL_PORTFOLIO_JSON_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"updated": None, "sync_status": "ok", "sync_error": None, "positions": []}


def _load_tracked_companies() -> list[dict]:
    """Companies suivies pour le rapprochement des tickers — liste vide
    si indices.json est absent/illisible (le rapprochement échoue alors
    pour toutes les positions, sans bloquer la synchronisation)."""
    try:
        with open(INDICES_JSON_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)["companies"]
    except Exception:
        return []


def _write_real_portfolio(payload: dict) -> None:
    os.makedirs(os.path.dirname(REAL_PORTFOLIO_JSON_PATH), exist_ok=True)
    with open(REAL_PORTFOLIO_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def main():
    token = os.environ.get("IBKR_FLEX_TOKEN")
    query_id = os.environ.get("IBKR_FLEX_QUERY_ID")
    payload = _load_existing_real_portfolio()
    payload["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not token or not query_id:
        payload["sync_status"] = "error"
        payload["sync_error"] = (
            "IBKR_FLEX_TOKEN ou IBKR_FLEX_QUERY_ID manquant dans l'environnement."
        )
        _write_real_portfolio(payload)
        print(f"Erreur synchronisation IBKR : {payload['sync_error']}")
        return

    try:
        reference_code = fetch_flex_reference_code(token, query_id)
        xml_text = fetch_flex_statement(token, reference_code)
        positions = parse_open_positions(xml_text)
        companies = _load_tracked_companies()
        positions = match_tickers(positions, companies)
    except Exception as e:
        payload["sync_status"] = "error"
        payload["sync_error"] = str(e)
        _write_real_portfolio(payload)
        print(f"Erreur synchronisation IBKR : {e}")
        traceback.print_exc()
        return

    payload["sync_status"] = "ok"
    payload["sync_error"] = None
    payload["positions"] = positions
    _write_real_portfolio(payload)
    print(f"Synchronisation IBKR réussie : {len(positions)} position(s).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_portfolio_sync.py -v`
Expected: 18 tests PASS (14 précédents + 4 nouveaux).

- [ ] **Step 5: Créer le workflow GitHub Actions**

Créer `.github/workflows/portfolio_sync.yml` :

```yaml
name: Synchronisation portefeuille IBKR

on:
  schedule:
    - cron: "0 7 * * *"   # une fois par jour, 07h00 UTC (décalé du cron Indices à 06h00)
  workflow_dispatch: {}

concurrency:
  group: portfolio-sync
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - name: Récupérer le dépôt
        uses: actions/checkout@v4

      - name: Installer Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Installer les dépendances
        run: pip install -r requirements.txt

      - name: Synchroniser les positions IBKR
        run: python3 portfolio_sync.py
        env:
          IBKR_FLEX_TOKEN: ${{ secrets.IBKR_FLEX_TOKEN }}
          IBKR_FLEX_QUERY_ID: ${{ secrets.IBKR_FLEX_QUERY_ID }}

      - name: Publier les données mises à jour sur le site
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/real_portfolio.json
          git diff --staged --quiet || git commit -m "Mise à jour de la synchronisation portefeuille IBKR"
          git pull --rebase --autostash
          git push
```

- [ ] **Step 6: Commit**

```bash
git add portfolio_sync.py tests/test_portfolio_sync.py .github/workflows/portfolio_sync.yml
git commit -m "$(cat <<'EOF'
feat(portfolio-sync): main() + workflow cron quotidien

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Note pour l'utilisateur (pas une action à effectuer par l'implémenteur)**

Ce script ne peut pas être testé contre un vrai compte IBKR dans cet
environnement (aucun accès réseau à l'API IBKR, aucun compte
disponible). Une fois les secrets `IBKR_FLEX_TOKEN`/`IBKR_FLEX_QUERY_ID`
configurés par l'utilisateur, il faudra déclencher le workflow
manuellement (`gh workflow run portfolio_sync.yml`) et vérifier le
contenu réel de `docs/real_portfolio.json` — si le format XML réel des
erreurs IBKR diffère de l'hypothèse documentée dans les Global
Constraints, `fetch_flex_reference_code`/`fetch_flex_statement` devront
être corrigées à ce moment (hors de ce plan, qui ne peut pas anticiper
un format non vérifiable ici).

---

### Task 4 : Frontend — section "Positions réelles (IBKR)"

**Files:**
- Modify: `docs/index.html`
- Modify: `docs/service-worker.js`

**Interfaces:**
- Consumes : `docs/real_portfolio.json` (Task 3, format `{updated, sync_status, sync_error, positions}`) ; `indicesData`, `formatPrice`, `formatPriceUsd`, `hasMajorNewsAlert`, `allIndicesBadgesHtml` (déjà existants dans `docs/index.html`) ; `renderPortfolioScreen`, `portfolioOpportunitiesHtml` (phase 1, déjà existants).
- Produces : section complète et fonctionnelle, dernière tâche du plan.

- [ ] **Step 1: Ajouter le chargement paresseux de `real_portfolio.json`**

Repérer, dans `docs/index.html`, la fonction `loadPortfolioScreen` (recherche du texte, elle contient `await loadPortfolioModule();` suivi d'un bloc `if (!indicesData) { ... }`). Juste avant `let indicesLoaded = false;`/`let indicesData = null;` (recherche `let indicesData = null;`), ajouter une nouvelle variable globale juste après :

```javascript
let indicesData = null;
let realPortfolioData = null;
```

Puis, dans le corps de `loadPortfolioScreen`, repérer la fin du bloc `if (!indicesData) { ... }` (juste avant l'appel final à `renderPortfolioScreen();`) et insérer un nouveau bloc entre les deux :

```javascript
  if (!realPortfolioData) {
    try {
      const res = await fetch('real_portfolio.json?t=' + Date.now());
      realPortfolioData = res.ok ? await res.json() : null;
    } catch (e) {
      realPortfolioData = null;
    }
  }
  renderPortfolioScreen();
```

(remplace le `renderPortfolioScreen();` existant à cet endroit — le fetch échoue silencieusement vers `null` plutôt que de bloquer tout l'écran Portefeuille, puisque cette section est indépendante des positions manuelles qui doivent continuer à s'afficher même si la synchronisation IBKR n'a jamais eu lieu.)

- [ ] **Step 2: Ajouter `realPortfolioHtml` et l'insérer dans `renderPortfolioScreen`**

Repérer la fin de `renderPortfolioScreen` (le texte exact `${portfolioPositionsHtml(positions, companiesByTicker, currencyByIndex)}` suivi de `${portfolioOpportunitiesHtml(opportunities)}` sur la ligne suivante, à l'intérieur du template literal assigné à `document.getElementById('portefeuilleContent').innerHTML`). Insérer une ligne entre les deux :

```javascript
    ${portfolioPositionsHtml(positions, companiesByTicker, currencyByIndex)}
    ${realPortfolioHtml(companiesByTicker)}
    ${portfolioOpportunitiesHtml(opportunities)}
```

Puis, juste après la fin de la fonction `portfolioOpportunitiesHtml` existante (repérer son accolade fermante suivie d'une ligne vide), ajouter :

```javascript
function realPortfolioHtml(companiesByTicker) {
  if (!realPortfolioData) {
    return `
      <h2>Positions réelles (IBKR)</h2>
      <div class="empty">Synchronisation IBKR pas encore disponible.</div>`;
  }
  const warningHtml = realPortfolioData.sync_status === 'error'
    ? `<p class="portfolio-form-error">Dernière synchronisation IBKR échouée : ${realPortfolioData.sync_error}</p>`
    : '';
  const positions = realPortfolioData.positions || [];
  if (!positions.length) {
    return `
      <h2>Positions réelles (IBKR)</h2>
      ${warningHtml}
      <div class="empty">Aucune position détenue chez IBKR pour l'instant.</div>`;
  }
  const totals = {};
  positions.forEach(p => {
    totals[p.currency] = totals[p.currency] || { value: 0, pnlAbs: 0 };
    totals[p.currency].value += p.position_value;
    totals[p.currency].pnlAbs += p.unrealized_pnl;
  });
  const fmtCurrency = (v, currency) => currency === 'USD' ? formatPriceUsd(v) : formatPrice(v);
  const totalsHtml = Object.keys(totals).map(currency => `
    <div class="portfolio-total-card">
      <span class="portfolio-total-label">Total ${currency}</span>
      <span class="portfolio-total-value">${fmtCurrency(totals[currency].value, currency)}</span>
      <span class="portfolio-total-pnl" style="color:${totals[currency].pnlAbs >= 0 ? 'var(--gold)' : 'var(--rust)'}">${totals[currency].pnlAbs >= 0 ? '+' : ''}${fmtCurrency(totals[currency].pnlAbs, currency)}</span>
    </div>`).join('');
  const rowsHtml = positions.map(p => {
    const company = p.matched_ticker ? companiesByTicker[p.matched_ticker] : null;
    const fmt = v => fmtCurrency(v, p.currency);
    const badgesHtml = company
      ? `${company.is_financial ? '<span class="financial-tag">Profil financier</span>' : ''}${hasMajorNewsAlert(company) ? '<span class="financial-tag majeure-tag">Actu majeure</span>' : ''}${allIndicesBadgesHtml(company)}`
      : '';
    const nameHtml = company
      ? `<a class="company-name" href="#indices/${company.ticker}">${company.name}</a>`
      : `<span class="company-name">${p.description || p.ibkr_symbol}</span>`;
    return `
      <div class="portfolio-position-row">
        <div class="portfolio-position-main">
          ${nameHtml}
          <span class="company-ticker">${p.ibkr_symbol}${badgesHtml}</span>
          <span class="hero-sub">${p.quantity} × ${fmt(p.current_price)}</span>
        </div>
        <div class="portfolio-position-pnl">
          <span class="price-value">${fmt(p.position_value)}</span>
          <span style="color:${p.unrealized_pnl >= 0 ? 'var(--gold)' : 'var(--rust)'}">${p.unrealized_pnl >= 0 ? '+' : ''}${fmt(p.unrealized_pnl)}</span>
          ${company ? `<span class="company-score" style="color:${company.score >= 0 ? 'var(--gold)' : 'var(--rust)'};">${company.score > 0 ? '+' : ''}${company.score}</span>` : ''}
        </div>
      </div>`;
  }).join('');
  return `
    <h2>Positions réelles (IBKR)</h2>
    ${warningHtml}
    <div class="portfolio-totals">${totalsHtml}</div>
    <div class="portfolio-position-list">${rowsHtml}</div>`;
}
```

Aucune nouvelle classe CSS n'est nécessaire — `realPortfolioHtml` réutilise entièrement `.portfolio-totals`, `.portfolio-total-card`, `.portfolio-total-label`, `.portfolio-total-value`, `.portfolio-total-pnl`, `.portfolio-position-list`, `.portfolio-position-row`, `.portfolio-position-main`, `.portfolio-position-pnl`, `.company-name`, `.company-ticker`, `.hero-sub`, `.price-value`, `.company-score`, `.financial-tag`, `.majeure-tag`, `.portfolio-form-error`, toutes déjà définies dans `docs/index.html` (phase 1). Pas de bouton modifier/supprimer, donc pas de fonction `wire...` à écrire — section entièrement en lecture seule, comme demandé par la spec.

- [ ] **Step 3: Vérification manuelle du diff**

Relire le diff et confirmer :
- `realPortfolioData` est déclarée une seule fois (`let realPortfolioData = null;`), jamais redéclarée plus loin.
- `formatPrice`, `formatPriceUsd`, `hasMajorNewsAlert`, `allIndicesBadgesHtml` existent bien ailleurs dans le fichier avec ces noms exacts (`grep -n "^function formatPrice\b\|^function formatPriceUsd\|^function hasMajorNewsAlert\|^function allIndicesBadgesHtml" docs/index.html`).
- `realPortfolioHtml` est appelée à l'intérieur de `renderPortfolioScreen`, avec `companiesByTicker` (déjà construit plus haut dans cette même fonction) — pas un nouvel argument à calculer.
- Aucune accolade non fermée dans les blocs modifiés.

- [ ] **Step 4: Bump du cache du service worker**

Dans `docs/service-worker.js`, remplacer `"analyse-or-shell-v20"` par `"analyse-or-shell-v21"`.

- [ ] **Step 5: Lancer la suite de tests Python complète**

Run: `python -m pytest tests/ -q`
Expected: tous les tests (indices + portfolio_sync) toujours au vert — cette tâche n'a touché que `docs/index.html`/`docs/service-worker.js`, aucun impact sur les tests Python.

- [ ] **Step 6: Commit**

```bash
git add docs/index.html docs/service-worker.js
git commit -m "$(cat <<'EOF'
feat(portfolio): section "Positions réelles (IBKR)" dans l'onglet Portefeuille

Lecture seule, séparée des positions manuelles de la phase 1 — affiche
toutes les positions du compte IBKR synchronisé, enrichies de score/
alertes/badges quand le ticker correspond à une entreprise suivie.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Fin de plan — utiliser `superpowers:finishing-a-development-branch`**

Comme pour la phase 1, aucune vérification visuelle automatisée n'est
possible dans ce bac à sable. Une fois ce plan exécuté, rappeler à
l'utilisateur qu'avant de fusionner il devra : configurer les secrets
`IBKR_FLEX_TOKEN`/`IBKR_FLEX_QUERY_ID` sur GitHub, déclencher
`portfolio_sync.yml` manuellement une première fois, vérifier le
contenu réel de `docs/real_portfolio.json`, puis recharger la page pour
confirmer que la section "Positions réelles (IBKR)" s'affiche
correctement (voir la note de Task 3, Step 7, sur l'hypothèse de format
d'erreur XML à vérifier à ce moment-là).

---

## Self-Review Notes

- **Spec coverage** — mécanisme Flex Query en 2 étapes (Task 1), extraction + rapprochement de tickers best-effort (Task 2), `main()`/dégradation/cadence quotidienne/workflow (Task 3), section frontend séparée en lecture seule affichant tout (matché ou non) (Task 4). Le "hors périmètre" (exécution d'ordre, fusion avec les positions manuelles, autres courtiers, temps réel) n'est touché par aucune tâche.
- **Type consistency** — le dict `Position` produit par `parse_open_positions`/`match_tickers` (`ibkr_symbol, description, currency, quantity, current_price, position_value, cost_basis_price, cost_basis_value, unrealized_pnl, matched_ticker`) est utilisé identiquement dans `main()` (Task 3) et dans `realPortfolioHtml` côté JS (Task 4, mêmes noms de champs dans le JSON écrit/lu).
- **Limite assumée et documentée** (Global Constraints) : le format exact des réponses d'erreur IBKR n'a pas pu être vérifié contre un vrai compte dans cet environnement — signalé explicitement plutôt que présenté comme certain.
