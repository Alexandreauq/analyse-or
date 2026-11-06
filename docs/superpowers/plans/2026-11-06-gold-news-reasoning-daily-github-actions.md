# Paper-trading quotidien IA or — bascule GitHub Actions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Ce chantier touche du VRAI CODE PYTHON (contrairement au plan frère `2026-11-06-gold-news-reasoning-daily-paper-trading.md`, dont la routine cloud s'est révélée irréalisable) — TDD complet requis. **Ce n'est PAS un chantier à risque réel** : aucun compte réel, aucun fichier `gold_bot/*.py` touché, aucune exécution de trade — mais la rigueur TDD normale du projet s'applique quand même (tests unitaires sur toutes les fonctions pures, cas limites explicites).

**Goal:** Remplacer la routine cloud (RemoteTrigger) du paper-trading quotidien sur l'or — irréalisable, son sandbox bloque tout accès réseau précis — par un workflow GitHub Actions avec du vrai code Python testable.

**Architecture:** Un nouveau module `gold_news_reasoning_daily.py` (racine du dépôt, à côté de `indices_score.py`) sépare le calcul déterministe (testable par `pytest`, sans appel modèle) du jugement qualitatif (2 appels à l'API Claude avec `web_search`, patron HTTP direct identique à `indices_score.py:4116-4421`). Un nouveau workflow `.github/workflows/gold_news_reasoning_daily.yml`, même patron que `.github/workflows/indices.yml`, exécute ce module quotidiennement (jours ouvrés) dans un environnement GitHub Actions à accès réseau complet.

**Tech Stack:** Python (`yfinance`, `requests`, `pandas` — déjà dans `requirements.txt`), GitHub Actions (`actions/checkout`, `actions/setup-python`), secret GitHub déjà existant `secrets.CLAUDE_API_KEY`.

**Spec:** `docs/superpowers/specs/2026-11-06-gold-news-reasoning-daily-paper-trading-design.md` (voir §4.0 pour le contexte du pivot, §4.1-4.4 révisés pour la logique métier exacte, §7 pour la densité de tests attendue)

## Global Constraints

- **Pas de risque réel** : ce module ne touche jamais `gold_bot/`, aucun compte réel, aucune exécution de trade (spec §2).
- Une seule position ouverte à la fois sur XAUUSD (spec §3).
- Prix via `yfinance` (ticker `GC=F` en premier — confirmé fonctionnel le 2026-11-06 — repli `XAUUSD=X`), jamais via `WebSearch` pour une valeur de prix (spec §4.1).
- Stop de sécurité proposé par le modèle, mais CLAMPÉ PAR LE CODE à une distance de 3% à 15% du prix d'entrée (valeur absolue) — jamais appliqué tel quel hors de cette fourchette (spec §4.2).
- `max_close_date` = `entry_date` + 21 jours calendaires, calculée par le CODE (spec §3, §4.2).
- Ordre de priorité à la réévaluation (spec §4.3, révisé) : (1) stop — déterministe, zéro appel modèle ; (2) délai max — déterministe, zéro appel modèle ; (3) réévaluation qualitative — SEULEMENT si ni (1) ni (2) ne s'applique.
- `return_pct` et `buyhold_return_pct` : formules exactes spec §3 — `buyhold_return_pct` TOUJOURS brut, jamais ajusté par `direction`.
- Le modèle NE DOIT JAMAIS modifier le champ `journal` le jour d'une clôture (bug trouvé le 2026-11-06 sur l'ancien système) — ici garanti par construction puisque c'est le CODE qui décide, mais testé explicitement quand même.
- Un échec de l'appel API de réévaluation (§4.3.3) est traité comme `garder` par défaut — jamais de clôture forcée sur un simple échec technique.
- Modèle : `claude-opus-5-5` (jugement directionnel réel, pas une classification — ne pas réutiliser `claude-haiku-5-5` d'`indices_score.py`, qui sert à un usage différent).
- Un seul commit par run, ciblé sur `docs/gold_news_reasoning_tracking.json` uniquement (jamais `git add -A`), aucun commit si le fichier n'a pas changé (spec §4.4).

---

### Task 1 : Fonctions pures (calcul, zéro réseau, zéro appel modèle)

**Files:**
- Create: `gold_news_reasoning_daily.py` (racine du dépôt)
- Test: `tests/test_gold_news_reasoning_daily.py`

**Interfaces:**
- Produces: `normalize_direction_call(appel: str) -> str | None`, `MIN_STOP_PCT = 0.03`, `MAX_STOP_PCT = 0.15`, `MAX_HOLD_DAYS = 21`, `clamp_stop_pct(raw_pct: float) -> float`, `compute_safety_stop_price(entry_price: float, direction: str, raw_stop_pct: float) -> float`, `compute_safety_stop_pct(entry_price: float, stop_price: float, direction: str) -> float`, `compute_return_pct(entry_price: float, close_price: float, direction: str) -> float`, `compute_buyhold_return_pct(entry_price: float, market_close_price: float) -> float`, `compute_max_close_date(entry_date: date) -> date`, `find_stop_breach(direction: str, stop_price: float, ohlc_rows: list[dict]) -> dict | None` (chaque élément de `ohlc_rows` : `{"date": "YYYY-MM-DD", "high": float, "low": float}`, renvoie la ligne entière du premier franchissement chronologique ou `None`).

- [ ] **Step 1: Écrire les tests (rouge)**

```python
# tests/test_gold_news_reasoning_daily.py
from datetime import date

import gold_news_reasoning_daily as g


def test_normalize_direction_call_haussier_variants():
    assert g.normalize_direction_call("haussier") == "achat"
    assert g.normalize_direction_call("leger_haussier") == "achat"
    assert g.normalize_direction_call("HAUSSIER") == "achat"


def test_normalize_direction_call_baissier_variants():
    assert g.normalize_direction_call("baissier") == "vente"
    assert g.normalize_direction_call("leger_baissier") == "vente"
    assert g.normalize_direction_call("Baissier") == "vente"


def test_normalize_direction_call_neutre_variants():
    assert g.normalize_direction_call("neutre") is None
    assert g.normalize_direction_call("neutre_prudent") is None


def test_normalize_direction_call_unrecognized_or_wrong_type_returns_none():
    assert g.normalize_direction_call("incertain") is None
    assert g.normalize_direction_call("") is None
    assert g.normalize_direction_call(None) is None
    assert g.normalize_direction_call(123) is None


def test_clamp_stop_pct_within_range_unchanged():
    assert g.clamp_stop_pct(0.08) == 0.08


def test_clamp_stop_pct_below_min_clamped_up():
    assert g.clamp_stop_pct(0.01) == g.MIN_STOP_PCT


def test_clamp_stop_pct_above_max_clamped_down():
    assert g.clamp_stop_pct(0.25) == g.MAX_STOP_PCT


def test_clamp_stop_pct_negative_input_takes_absolute_value():
    assert g.clamp_stop_pct(-0.10) == 0.10


def test_clamp_stop_pct_exact_boundaries_unchanged():
    assert g.clamp_stop_pct(0.03) == 0.03
    assert g.clamp_stop_pct(0.15) == 0.15


def test_compute_safety_stop_price_achat_is_below_entry():
    stop = g.compute_safety_stop_price(4000.0, "achat", 0.05)
    assert stop == 3800.0


def test_compute_safety_stop_price_vente_is_above_entry():
    stop = g.compute_safety_stop_price(4000.0, "vente", 0.05)
    assert stop == 4200.0


def test_compute_safety_stop_price_clamps_out_of_range_input():
    stop = g.compute_safety_stop_price(4000.0, "achat", 0.50)
    assert stop == 4000.0 * (1 - g.MAX_STOP_PCT)


def test_compute_safety_stop_pct_achat_is_negative():
    pct = g.compute_safety_stop_pct(4000.0, 3800.0, "achat")
    assert round(pct, 4) == -0.05


def test_compute_safety_stop_pct_vente_is_negative():
    pct = g.compute_safety_stop_pct(4000.0, 4200.0, "vente")
    assert round(pct, 4) == -0.0476


def test_compute_return_pct_achat_positive_when_price_rises():
    assert round(g.compute_return_pct(4000.0, 4200.0, "achat"), 4) == 0.05


def test_compute_return_pct_vente_positive_when_price_falls():
    assert round(g.compute_return_pct(4000.0, 3800.0, "vente"), 4) == 0.0526


def test_compute_buyhold_return_pct_is_raw_never_direction_adjusted():
    # Une vente dont le prix baisse gagne (return_pct positif), mais le
    # repere buy&hold reste neutre/BRUT sur le meme mouvement de prix --
    # ici negatif puisque le prix a baisse, peu importe la direction du pari.
    buyhold = g.compute_buyhold_return_pct(4000.0, 3800.0)
    assert round(buyhold, 4) == -0.05


def test_compute_max_close_date_adds_21_days():
    assert g.compute_max_close_date(date(2026, 11, 10)) == date(2026, 12, 1)


def test_find_stop_breach_achat_detects_low_crossing():
    rows = [
        {"date": "2026-11-10", "high": 4100.0, "low": 4050.0},
        {"date": "2026-11-11", "high": 4060.0, "low": 3940.0},
    ]
    breach = g.find_stop_breach("achat", 3950.0, rows)
    assert breach["date"] == "2026-11-11"


def test_find_stop_breach_vente_detects_high_crossing():
    rows = [
        {"date": "2026-11-10", "high": 4150.0, "low": 4050.0},
        {"date": "2026-11-11", "high": 4260.0, "low": 4040.0},
    ]
    breach = g.find_stop_breach("vente", 4250.0, rows)
    assert breach["date"] == "2026-11-11"


def test_find_stop_breach_returns_first_chronological_day_not_most_extreme():
    # Le 11 franchit deja le stop (low 3940 < 3950) ; le 12, non trie en
    # premier dans la liste, descend bien plus bas (3800) mais est APRES
    # le 11 chronologiquement -- doit renvoyer le 11, pas le 12.
    rows = [
        {"date": "2026-11-12", "high": 4000.0, "low": 3800.0},
        {"date": "2026-11-11", "high": 4060.0, "low": 3940.0},
        {"date": "2026-11-10", "high": 4100.0, "low": 4050.0},
    ]
    breach = g.find_stop_breach("achat", 3950.0, rows)
    assert breach["date"] == "2026-11-11"


def test_find_stop_breach_returns_none_when_never_crossed():
    rows = [
        {"date": "2026-11-10", "high": 4100.0, "low": 4050.0},
        {"date": "2026-11-11", "high": 4060.0, "low": 4030.0},
    ]
    assert g.find_stop_breach("achat", 3950.0, rows) is None
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `pytest tests/test_gold_news_reasoning_daily.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'gold_news_reasoning_daily'`

- [ ] **Step 3: Implémenter**

```python
# gold_news_reasoning_daily.py (en-tête + fonctions de ce Task -- les
# imports reseau/API sont ajoutes au Task 2, ne pas les ajouter ici)
"""Test prospectif IA en paper-trading sur l'or (XAUUSD) -- voir
docs/superpowers/specs/2026-11-06-gold-news-reasoning-daily-paper-trading-design.md.
Execute quotidiennement via .github/workflows/gold_news_reasoning_daily.yml
(jours ouvres). Aucun lien avec gold_bot/ -- purement observationnel,
aucun compte reel, aucune execution de trade."""
from datetime import date, timedelta

MIN_STOP_PCT = 0.03
MAX_STOP_PCT = 0.15
MAX_HOLD_DAYS = 21


def normalize_direction_call(appel) -> str | None:
    """Normalise un appel directionnel textuel (haussier/leger_haussier/...,
    baissier/leger_baissier/..., neutre/neutre_prudent/...) en "achat",
    "vente", ou None (neutre, non reconnu, ou type inattendu -- aucune
    position n'est alors ouverte, voir spec #4.2)."""
    if not isinstance(appel, str):
        return None
    normalized = appel.strip().lower()
    if "neutre" in normalized:
        return None
    if "haussier" in normalized:
        return "achat"
    if "baissier" in normalized:
        return "vente"
    return None


def clamp_stop_pct(raw_pct: float) -> float:
    """Ramene une distance de stop (valeur absolue, fraction -- ex. 0.08
    pour 8%) a la fourchette [MIN_STOP_PCT, MAX_STOP_PCT]. Prend la
    valeur absolue d'abord (le modele pourrait renvoyer un signe)."""
    magnitude = abs(raw_pct)
    return max(MIN_STOP_PCT, min(MAX_STOP_PCT, magnitude))


def compute_safety_stop_price(entry_price: float, direction: str, raw_stop_pct: float) -> float:
    """raw_stop_pct est la proposition BRUTE du modele -- clampee ici,
    jamais appliquee telle quelle. Pour un achat, le stop est SOUS le
    prix d'entree ; pour une vente, AU-DESSUS."""
    magnitude = clamp_stop_pct(raw_stop_pct)
    if direction == "achat":
        return entry_price * (1 - magnitude)
    return entry_price * (1 + magnitude)


def compute_safety_stop_pct(entry_price: float, stop_price: float, direction: str) -> float:
    """Toujours une valeur NEGATIVE (perte exprimee en %), quel que soit
    le sens de la position."""
    if direction == "achat":
        return stop_price / entry_price - 1
    return entry_price / stop_price - 1


def compute_return_pct(entry_price: float, close_price: float, direction: str) -> float:
    if direction == "achat":
        return close_price / entry_price - 1
    return entry_price / close_price - 1


def compute_buyhold_return_pct(entry_price: float, market_close_price: float) -> float:
    """TOUJOURS brut (jamais ajuste par direction) -- repere passif
    "aurait-on mieux fait de juste detenir de l'or sur cette periode"."""
    return market_close_price / entry_price - 1


def compute_max_close_date(entry_date: date) -> date:
    return entry_date + timedelta(days=MAX_HOLD_DAYS)


def find_stop_breach(direction: str, stop_price: float, ohlc_rows: list[dict]) -> dict | None:
    """ohlc_rows : liste de {"date": "YYYY-MM-DD", "high": float, "low":
    float} dans N'IMPORTE QUEL ordre -- cette fonction les trie
    elle-meme par date pour garantir de renvoyer le PREMIER jour
    chronologique de franchissement, jamais le plus extreme. Renvoie la
    ligne entiere (pour que l'appelant recupere sa date), ou None si
    aucun franchissement."""
    for row in sorted(ohlc_rows, key=lambda r: r["date"]):
        if direction == "achat" and row["low"] <= stop_price:
            return row
        if direction == "vente" and row["high"] >= stop_price:
            return row
    return None
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `pytest tests/test_gold_news_reasoning_daily.py -v`
Expected: PASS (21 tests)

- [ ] **Step 5: Commit**

```bash
git add gold_news_reasoning_daily.py tests/test_gold_news_reasoning_daily.py
git commit -m "feat(gold-bot): fonctions pures du paper-trading quotidien IA or"
```

---

### Task 2 : Récupération des prix (`yfinance`) et appels à l'API Claude

**Files:**
- Modify: `gold_news_reasoning_daily.py`
- Test: `tests/test_gold_news_reasoning_daily.py`

**Interfaces:**
- Consumes: rien du Task 1 directement (fonctions indépendantes), mais `call_claude_for_new_entry`/`call_claude_for_reeval` seront consommées par le Task 3.
- Produces: `YF_PRIMARY_TICKER = "GC=F"`, `YF_FALLBACK_TICKER = "XAUUSD=X"`, `fetch_daily_ohlc(period: str = "2mo") -> list[dict] | None` (chaque élément : `{"date": "YYYY-MM-DD", "open": float, "high": float, "low": float, "close": float}`, triée par date croissante), `ANTHROPIC_API_URL`, `GOLD_REASONING_MODEL = "claude-opus-5-5"`, `call_claude_for_new_entry(ohlc_rows: list[dict]) -> dict | None` (renvoie `{"appel": str, "stop_pct": float, "confiance": str, "raison": str}` ou `None`), `call_claude_for_reeval(position: dict, ohlc_rows: list[dict]) -> dict | None` (renvoie `{"decision": "garder"|"cloturer", "raison": str}` ou `None`).

- [ ] **Step 1: Écrire les tests (rouge)**

```python
# a ajouter a tests/test_gold_news_reasoning_daily.py
import json
from unittest.mock import MagicMock

import pandas as pd
import pytest


def _fake_yf_dataframe(rows):
    df = pd.DataFrame(
        {"Open": [r["open"] for r in rows], "High": [r["high"] for r in rows],
         "Low": [r["low"] for r in rows], "Close": [r["close"] for r in rows]},
        index=pd.to_datetime([r["date"] for r in rows]),
    )
    return df


def test_fetch_daily_ohlc_success_with_primary_ticker(monkeypatch):
    rows = [
        {"date": "2026-11-05", "open": 4070.0, "high": 4080.0, "low": 4060.0, "close": 4075.0},
        {"date": "2026-11-06", "open": 4075.0, "high": 4090.0, "low": 4070.0, "close": 4080.0},
    ]

    def fake_download(ticker, period=None, interval=None, progress=None):
        assert ticker == g.YF_PRIMARY_TICKER
        return _fake_yf_dataframe(rows)

    monkeypatch.setattr(g.yf, "download", fake_download)
    result = g.fetch_daily_ohlc()
    assert result == rows


def test_fetch_daily_ohlc_falls_back_to_second_ticker_when_primary_empty(monkeypatch):
    rows = [{"date": "2026-11-06", "open": 4075.0, "high": 4090.0, "low": 4070.0, "close": 4080.0}]

    def fake_download(ticker, period=None, interval=None, progress=None):
        if ticker == g.YF_PRIMARY_TICKER:
            return pd.DataFrame()
        return _fake_yf_dataframe(rows)

    monkeypatch.setattr(g.yf, "download", fake_download)
    assert g.fetch_daily_ohlc() == rows


def test_fetch_daily_ohlc_returns_none_when_both_tickers_fail(monkeypatch):
    monkeypatch.setattr(g.yf, "download", lambda *a, **k: pd.DataFrame())
    assert g.fetch_daily_ohlc() is None


def test_fetch_daily_ohlc_returns_none_on_exception(monkeypatch):
    def raising(*a, **k):
        raise ConnectionError("boom")

    monkeypatch.setattr(g.yf, "download", raising)
    assert g.fetch_daily_ohlc() is None


def test_fetch_daily_ohlc_sorted_by_date(monkeypatch):
    rows = [
        {"date": "2026-11-06", "open": 4075.0, "high": 4090.0, "low": 4070.0, "close": 4080.0},
        {"date": "2026-11-05", "open": 4070.0, "high": 4080.0, "low": 4060.0, "close": 4075.0},
    ]
    monkeypatch.setattr(g.yf, "download", lambda *a, **k: _fake_yf_dataframe(rows))
    result = g.fetch_daily_ohlc()
    assert [r["date"] for r in result] == ["2026-11-05", "2026-11-06"]


def _fake_response(status_ok=True, json_body=None, raise_exc=None):
    resp = MagicMock()
    if raise_exc is not None:
        resp.raise_for_status.side_effect = raise_exc
    else:
        resp.raise_for_status.return_value = None
    resp.json.return_value = json_body or {}
    return resp


def test_call_claude_for_new_entry_returns_none_when_api_key_absent(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert g.call_claude_for_new_entry([{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}]) is None


def test_call_claude_for_new_entry_parses_successful_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    body = {"content": [{"type": "text", "text": 'blah {"appel": "haussier", "stop_pct": 0.08, "confiance": "moyenne", "raison": "test"}'}]}
    monkeypatch.setattr(g.requests, "post", lambda *a, **k: _fake_response(json_body=body))
    result = g.call_claude_for_new_entry([{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}])
    assert result == {"appel": "haussier", "stop_pct": 0.08, "confiance": "moyenne", "raison": "test"}


def test_call_claude_for_new_entry_returns_none_on_http_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setattr(g.requests, "post", lambda *a, **k: _fake_response(raise_exc=Exception("500")))
    assert g.call_claude_for_new_entry([{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}]) is None


def test_call_claude_for_new_entry_returns_none_on_unparseable_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    body = {"content": [{"type": "text", "text": "reponse sans JSON exploitable"}]}
    monkeypatch.setattr(g.requests, "post", lambda *a, **k: _fake_response(json_body=body))
    assert g.call_claude_for_new_entry([{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}]) is None


def test_call_claude_for_new_entry_returns_none_on_timeout(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    def raising(*a, **k):
        raise TimeoutError("boom")

    monkeypatch.setattr(g.requests, "post", raising)
    assert g.call_claude_for_new_entry([{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}]) is None


def test_call_claude_for_reeval_returns_none_when_api_key_absent(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    position = {"entry_date": "2026-11-01", "direction": "achat", "entry_price": 4000.0, "raison_ouverture": "x"}
    assert g.call_claude_for_reeval(position, [{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}]) is None


def test_call_claude_for_reeval_parses_successful_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    body = {"content": [{"type": "text", "text": '{"decision": "garder", "raison": "test"}'}]}
    monkeypatch.setattr(g.requests, "post", lambda *a, **k: _fake_response(json_body=body))
    position = {"entry_date": "2026-11-01", "direction": "achat", "entry_price": 4000.0, "raison_ouverture": "x"}
    result = g.call_claude_for_reeval(position, [{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}])
    assert result == {"decision": "garder", "raison": "test"}


def test_call_claude_for_reeval_returns_none_on_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setattr(g.requests, "post", lambda *a, **k: _fake_response(raise_exc=Exception("boom")))
    position = {"entry_date": "2026-11-01", "direction": "achat", "entry_price": 4000.0, "raison_ouverture": "x"}
    assert g.call_claude_for_reeval(position, [{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}]) is None
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `pytest tests/test_gold_news_reasoning_daily.py -v`
Expected: FAIL (`AttributeError: module 'gold_news_reasoning_daily' has no attribute 'yf'` et similaires)

- [ ] **Step 3: Implémenter**

Ajouter en tête de `gold_news_reasoning_daily.py` (après le docstring, avant les fonctions du Task 1) :

```python
import json
import os
import re

import requests
import yfinance as yf
```

Ajouter à la fin du fichier :

```python
YF_PRIMARY_TICKER = "GC=F"
YF_FALLBACK_TICKER = "XAUUSD=X"


def fetch_daily_ohlc(period: str = "2mo") -> list[dict] | None:
    """Recupere les clotures/plus-hauts/plus-bas journaliers de l'or via
    yfinance (GC=F en premier -- confirme fonctionnel le 2026-11-06,
    XAUUSD=X a echoue seul lors du test manuel -- repli quand meme
    conserve par resilience). Renvoie une liste de {"date", "open",
    "high", "low", "close"} triee par date croissante, ou None si les
    deux tickers echouent/renvoient une serie vide -- jamais
    d'exception (dialogue avec un service tiers dont on ne peut pas
    enumerer precisement tous les modes d'echec, meme philosophie que
    indices_score.py:fetch_article_text)."""
    for ticker in (YF_PRIMARY_TICKER, YF_FALLBACK_TICKER):
        try:
            data = yf.download(ticker, period=period, interval="1d", progress=False)
        except Exception:
            continue
        if data is None or len(data) == 0:
            continue
        columns = data.columns
        if getattr(columns, "nlevels", 1) > 1:
            data.columns = columns.get_level_values(0)
        rows = []
        for ts, row in data.iterrows():
            try:
                rows.append({
                    "date": ts.date().isoformat(),
                    "open": float(row["Open"]), "high": float(row["High"]),
                    "low": float(row["Low"]), "close": float(row["Close"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
        if rows:
            return sorted(rows, key=lambda r: r["date"])
    return None


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
GOLD_REASONING_MODEL = "claude-opus-5-5"
GOLD_REASONING_TIMEOUT_SECONDS = 40
GOLD_REASONING_MAX_TOKENS = 4096
GOLD_REASONING_WEB_SEARCH_MAX_USES = 3


def _extract_json_block(text: str, required_key: str) -> dict | None:
    pattern = re.compile(r'\{[^{}]*"' + re.escape(required_key) + r'"[^{}]*\}')
    matches = pattern.findall(text)
    if not matches:
        return None
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _ohlc_summary_lines(ohlc_rows: list[dict], limit: int = 10) -> str:
    recent = ohlc_rows[-limit:]
    return "\n".join(
        f"{r['date']}: O={r['open']:.2f} H={r['high']:.2f} L={r['low']:.2f} C={r['close']:.2f}"
        for r in recent
    )


def call_claude_for_new_entry(ohlc_rows: list[dict]) -> dict | None:
    """Demande a Claude (avec l'outil web_search) un appel directionnel
    sur l'or pour les 3 prochaines semaines. Renvoie {"appel": str,
    "stop_pct": float, "confiance": str, "raison": str} ou None sur
    tout echec (cle API absente, erreur reseau, reponse non
    parseable) -- jamais d'exception propagee."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    prompt = (
        "Tu es un analyste qui raisonne sur l'or (XAUUSD) a partir de "
        "l'actualite recente et du comportement du prix/volume -- PAS "
        "de formule mecanique, un jugement contextuel comme un trader "
        "humain le ferait. Ceci est un test prospectif methodologique "
        "en paper-trading : aucune position n'est reelle.\n\n"
        f"Dernieres cloture/plus-haut/plus-bas journaliers :\n{_ohlc_summary_lines(ohlc_rows)}\n\n"
        "Cherche l'actualite macro/geopolitique la plus pertinente pour "
        "l'or recemment (banques centrales, inflation, risque "
        "geopolitique, flux ETF). Fais un appel directionnel sur "
        "l'horizon des 3 prochaines semaines : haussier, baissier, ou "
        "neutre (nuances possibles : leger_haussier, neutre_prudent, "
        "etc., comme un vrai jugement nuance), avec une confiance "
        "(faible/moyenne/haute) et une raison en une ou deux phrases. "
        "Si haussier ou baissier, propose aussi une distance de stop de "
        "securite en % (entre 3% et 15% du prix d'entree, exprimee en "
        "fraction, ex. 0.08 pour 8%) adaptee a la volatilite recente.\n\n"
        "Termine ta reponse par un bloc JSON seul sur sa derniere "
        "ligne, exactement sous cette forme : "
        '{"appel": "...", "stop_pct": 0.08, "confiance": "faible ou '
        'moyenne ou haute", "raison": "une ou deux phrases"}'
    )
    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": GOLD_REASONING_MODEL,
                "max_tokens": GOLD_REASONING_MAX_TOKENS,
                "output_config": {"effort": "medium"},
                "tools": [{"type": "web_search_20260209", "name": "web_search", "max_uses": GOLD_REASONING_WEB_SEARCH_MAX_USES}],
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=GOLD_REASONING_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        return _extract_json_block(text, "appel")
    except Exception:
        return None


def call_claude_for_reeval(position: dict, ohlc_rows: list[dict]) -> dict | None:
    """Demande a Claude (avec l'outil web_search) s'il faut GARDER ou
    CLOTURER une position ouverte. Renvoie {"decision": "garder" ou
    "cloturer", "raison": str} ou None sur tout echec. L'appelant
    (voir main()) traite None comme "garder" par defaut -- jamais de
    cloture forcee sur un simple echec technique."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    prompt = (
        "Tu es un analyste qui suit une position simulee sur l'or "
        f"(XAUUSD), ouverte le {position['entry_date']} en "
        f"{position['direction']} a {position['entry_price']}, raison "
        f"initiale : {position.get('raison_ouverture', '')}.\n\n"
        f"Dernieres cloture/plus-haut/plus-bas journaliers :\n{_ohlc_summary_lines(ohlc_rows)}\n\n"
        "Cherche l'actualite macro/geopolitique recente pertinente pour "
        "l'or, et decide : faut-il GARDER cette position, ou la "
        "CLOTURER par anticipation (prise de profit ou retournement de "
        "these) ? Donne une raison en une phrase.\n\n"
        "Termine ta reponse par un bloc JSON seul sur sa derniere "
        "ligne, exactement sous cette forme : "
        '{"decision": "garder ou cloturer", "raison": "une phrase"}'
    )
    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": GOLD_REASONING_MODEL,
                "max_tokens": GOLD_REASONING_MAX_TOKENS,
                "output_config": {"effort": "medium"},
                "tools": [{"type": "web_search_20260209", "name": "web_search", "max_uses": GOLD_REASONING_WEB_SEARCH_MAX_USES}],
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=GOLD_REASONING_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        return _extract_json_block(text, "decision")
    except Exception:
        return None
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `pytest tests/test_gold_news_reasoning_daily.py -v`
Expected: PASS (21 + 13 = 34 tests)

- [ ] **Step 5: Commit**

```bash
git add gold_news_reasoning_daily.py tests/test_gold_news_reasoning_daily.py
git commit -m "feat(gold-bot): recuperation yfinance + appels API Claude du paper-trading or"
```

---

### Task 3 : Orchestration (`main()`) et persistance du fichier de suivi

**Files:**
- Modify: `gold_news_reasoning_daily.py`
- Test: `tests/test_gold_news_reasoning_daily.py`

**Interfaces:**
- Consumes (Task 1) : `normalize_direction_call`, `clamp_stop_pct`, `compute_safety_stop_price`, `compute_safety_stop_pct`, `compute_return_pct`, `compute_buyhold_return_pct`, `compute_max_close_date`, `find_stop_breach`.
- Consumes (Task 2) : `fetch_daily_ohlc`, `call_claude_for_new_entry`, `call_claude_for_reeval`.
- Produces: `TRACKING_PATH` (chemin par défaut, surchageable), `_read_tracking(path=TRACKING_PATH) -> dict`, `_write_tracking(data: dict, path=TRACKING_PATH) -> None`, `_find_open_position(tracking: dict) -> dict | None`, `process_open_position(position: dict, ohlc_rows: list[dict], today: date) -> bool`, `process_new_entry(ohlc_rows: list[dict], today: date) -> dict | None`, `main() -> None`.

- [ ] **Step 1: Écrire les tests (rouge)**

```python
# a ajouter a tests/test_gold_news_reasoning_daily.py
from datetime import date as _date


def _ohlc(entries):
    return [{"date": d, "open": o, "high": h, "low": l, "close": c} for d, o, h, l, c in entries]


def test_process_new_entry_opens_achat_position_on_bullish_call(monkeypatch):
    monkeypatch.setattr(g, "call_claude_for_new_entry", lambda rows: {
        "appel": "haussier", "stop_pct": 0.08, "confiance": "moyenne", "raison": "test",
    })
    ohlc = _ohlc([("2026-11-06", 4070, 4090, 4060, 4080)])
    position = g.process_new_entry(ohlc, _date(2026, 11, 6))
    assert position["status"] == "open"
    assert position["direction"] == "achat"
    assert position["entry_price"] == 4080
    assert position["entry_date"] == "2026-11-06"
    assert position["id"] == "XAUUSD-2026-11-06"
    assert position["safety_stop_price"] == 4080 * 0.92
    assert position["max_close_date"] == "2026-11-27"
    assert position["journal"] == []
    assert position["close_date"] is None


def test_process_new_entry_returns_none_on_neutral_call(monkeypatch):
    monkeypatch.setattr(g, "call_claude_for_new_entry", lambda rows: {
        "appel": "neutre_prudent", "confiance": "faible", "raison": "test",
    })
    ohlc = _ohlc([("2026-11-06", 4070, 4090, 4060, 4080)])
    assert g.process_new_entry(ohlc, _date(2026, 11, 6)) is None


def test_process_new_entry_returns_none_when_api_call_fails(monkeypatch):
    monkeypatch.setattr(g, "call_claude_for_new_entry", lambda rows: None)
    ohlc = _ohlc([("2026-11-06", 4070, 4090, 4060, 4080)])
    assert g.process_new_entry(ohlc, _date(2026, 11, 6)) is None


def _open_position(**overrides):
    base = {
        "id": "XAUUSD-2026-10-20", "status": "open",
        "entry_date": "2026-10-20", "entry_price": 4142.6, "direction": "achat",
        "safety_stop_price": 3950.0, "safety_stop_pct": -0.0465,
        "confiance": "moyenne", "raison_ouverture": "test",
        "max_close_date": "2026-11-10",
        "close_date": None, "close_price": None, "close_reason": None,
        "return_pct": None, "buyhold_price_at_close": None, "buyhold_return_pct": None,
        "journal": [],
    }
    base.update(overrides)
    return base


def test_process_open_position_closes_via_stop_without_calling_model(monkeypatch):
    def fail_if_called(*a, **k):
        raise AssertionError("call_claude_for_reeval ne doit pas etre appele si le stop est touche")

    monkeypatch.setattr(g, "call_claude_for_reeval", fail_if_called)
    position = _open_position()
    ohlc = _ohlc([
        ("2026-10-29", 4080, 4085, 4016, 4031),
        ("2026-10-30", 4033, 4038, 3879, 3898.9),
    ])
    changed = g.process_open_position(position, ohlc, _date(2026, 11, 6))
    assert changed is True
    assert position["status"] == "closed"
    assert position["close_date"] == "2026-10-30"
    assert position["close_price"] == 3950.0
    assert position["close_reason"] == "stop_securite"
    assert round(position["return_pct"], 6) == round(3950.0 / 4142.6 - 1, 6)
    assert position["buyhold_price_at_close"] == 3898.9
    assert round(position["buyhold_return_pct"], 6) == round(3898.9 / 4142.6 - 1, 6)
    assert position["journal"] == []


def test_process_open_position_closes_via_delai_max_without_calling_model(monkeypatch):
    def fail_if_called(*a, **k):
        raise AssertionError("call_claude_for_reeval ne doit pas etre appele si le delai max est atteint")

    monkeypatch.setattr(g, "call_claude_for_reeval", fail_if_called)
    position = _open_position(max_close_date="2026-11-05")
    ohlc = _ohlc([("2026-11-06", 4070, 4090, 4060, 4080)])
    changed = g.process_open_position(position, ohlc, _date(2026, 11, 6))
    assert changed is True
    assert position["close_reason"] == "delai_max"
    assert position["close_date"] == "2026-11-06"
    assert position["close_price"] == 4080
    assert position["buyhold_price_at_close"] == 4080
    assert position["journal"] == []


def test_process_open_position_keeps_and_appends_journal_on_garder(monkeypatch):
    monkeypatch.setattr(g, "call_claude_for_reeval", lambda pos, rows: {"decision": "garder", "raison": "these intacte"})
    position = _open_position(max_close_date="2026-12-01")
    ohlc = _ohlc([("2026-11-06", 4070, 4090, 4060, 4080)])
    changed = g.process_open_position(position, ohlc, _date(2026, 11, 6))
    assert changed is True
    assert position["status"] == "open"
    assert len(position["journal"]) == 1
    assert position["journal"][0] == {"date": "2026-11-06", "price": 4080, "decision": "garder", "raison": "these intacte"}


def test_process_open_position_closes_via_ai_anticipated_when_cloturer(monkeypatch):
    monkeypatch.setattr(g, "call_claude_for_reeval", lambda pos, rows: {"decision": "cloturer", "raison": "retournement"})
    position = _open_position(max_close_date="2026-12-01")
    ohlc = _ohlc([("2026-11-06", 4070, 4090, 4060, 4080)])
    changed = g.process_open_position(position, ohlc, _date(2026, 11, 6))
    assert changed is True
    assert position["close_reason"] == "cloture_anticipee_ia"
    assert position["close_price"] == 4080
    assert position["journal"] == []


def test_process_open_position_treats_failed_reeval_call_as_garder(monkeypatch):
    monkeypatch.setattr(g, "call_claude_for_reeval", lambda pos, rows: None)
    position = _open_position(max_close_date="2026-12-01")
    ohlc = _ohlc([("2026-11-06", 4070, 4090, 4060, 4080)])
    changed = g.process_open_position(position, ohlc, _date(2026, 11, 6))
    assert changed is True
    assert position["status"] == "open"
    assert len(position["journal"]) == 1
    assert "echec" in position["journal"][0]["raison"]


def test_main_does_nothing_when_fetch_ohlc_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(g, "fetch_daily_ohlc", lambda *a, **k: None)
    tracking_path = tmp_path / "tracking.json"
    monkeypatch.setattr(g, "TRACKING_PATH", str(tracking_path))
    g.main()
    assert not tracking_path.exists()


def test_main_opens_new_position_when_none_open(monkeypatch, tmp_path):
    ohlc = _ohlc([("2026-11-06", 4070, 4090, 4060, 4080)])
    monkeypatch.setattr(g, "fetch_daily_ohlc", lambda *a, **k: ohlc)
    monkeypatch.setattr(g, "call_claude_for_new_entry", lambda rows: {
        "appel": "haussier", "stop_pct": 0.08, "confiance": "moyenne", "raison": "test",
    })
    tracking_path = tmp_path / "tracking.json"
    tracking_path.write_text(json.dumps({"positions": []}), encoding="utf-8")
    monkeypatch.setattr(g, "TRACKING_PATH", str(tracking_path))
    g.main()
    written = json.loads(tracking_path.read_text(encoding="utf-8"))
    assert len(written["positions"]) == 1
    assert written["positions"][0]["status"] == "open"


def test_main_reopens_same_day_after_stop_closes_old_position(monkeypatch, tmp_path):
    ohlc = _ohlc([
        ("2026-10-29", 4080, 4085, 4016, 4031),
        ("2026-10-30", 4033, 4038, 3879, 3898.9),
    ])
    monkeypatch.setattr(g, "fetch_daily_ohlc", lambda *a, **k: ohlc)
    monkeypatch.setattr(g, "call_claude_for_new_entry", lambda rows: {
        "appel": "baissier", "stop_pct": 0.05, "confiance": "faible", "raison": "nouvelle these",
    })

    def fail_if_called(*a, **k):
        raise AssertionError("pas d'appel de reevaluation attendu, le stop tranche")

    monkeypatch.setattr(g, "call_claude_for_reeval", fail_if_called)
    old_position = _open_position()
    tracking_path = tmp_path / "tracking.json"
    tracking_path.write_text(json.dumps({"positions": [old_position]}), encoding="utf-8")
    monkeypatch.setattr(g, "TRACKING_PATH", str(tracking_path))
    g.main()
    written = json.loads(tracking_path.read_text(encoding="utf-8"))
    assert len(written["positions"]) == 2
    assert written["positions"][0]["status"] == "closed"
    assert written["positions"][0]["close_reason"] == "stop_securite"
    assert written["positions"][0]["journal"] == []
    assert written["positions"][1]["status"] == "open"
    assert written["positions"][1]["direction"] == "vente"
    assert written["positions"][1]["entry_date"] == "2026-10-30"


def test_main_does_nothing_and_writes_no_file_on_neutral_call_with_no_open_position(monkeypatch, tmp_path):
    ohlc = _ohlc([("2026-11-06", 4070, 4090, 4060, 4080)])
    monkeypatch.setattr(g, "fetch_daily_ohlc", lambda *a, **k: ohlc)
    monkeypatch.setattr(g, "call_claude_for_new_entry", lambda rows: {"appel": "neutre", "confiance": "faible", "raison": "test"})
    tracking_path = tmp_path / "tracking.json"
    monkeypatch.setattr(g, "TRACKING_PATH", str(tracking_path))
    g.main()
    assert not tracking_path.exists()
```

(Le test `test_main_reopens_same_day_after_stop_closes_old_position` utilise `monkeypatch` pour figer `date.today()` implicitement via les données OHLC : `main()` doit déterminer `today` à partir de la dernière date connue dans `ohlc_rows`, PAS de `date.today()` directement — voir Step 3, cela rend `main()` testable sans mocker l'horloge système. Ajouter cette règle précisément dans l'implémentation.)

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `pytest tests/test_gold_news_reasoning_daily.py -v`
Expected: FAIL (`AttributeError: module 'gold_news_reasoning_daily' has no attribute 'process_new_entry'` et similaires)

- [ ] **Step 3: Implémenter**

Ajouter à la fin de `gold_news_reasoning_daily.py` :

```python
TRACKING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "gold_news_reasoning_tracking.json")


def _read_tracking(path: str = TRACKING_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {"positions": []}
    if not isinstance(data, dict) or not isinstance(data.get("positions"), list):
        return {"positions": []}
    return data


def _write_tracking(data: dict, path: str = TRACKING_PATH) -> None:
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp_path, path)


def _find_open_position(tracking: dict) -> dict | None:
    for position in tracking["positions"]:
        if position.get("status") == "open":
            return position
    return None


def _rows_since(ohlc_rows: list[dict], since_date: str) -> list[dict]:
    """Lignes dont la date est STRICTEMENT posterieure a since_date --
    since_date est entry_date (pas encore de journal) ou la date de la
    derniere entree de journal (deja evaluee, ne pas la re-evaluer)."""
    return [r for r in ohlc_rows if r["date"] > since_date]


def _close_position(position: dict, close_date: str, close_price: float, close_reason: str, market_close_price: float) -> None:
    position["status"] = "closed"
    position["close_date"] = close_date
    position["close_price"] = close_price
    position["close_reason"] = close_reason
    position["return_pct"] = compute_return_pct(position["entry_price"], close_price, position["direction"])
    position["buyhold_price_at_close"] = market_close_price
    position["buyhold_return_pct"] = compute_buyhold_return_pct(position["entry_price"], market_close_price)


def process_open_position(position: dict, ohlc_rows: list[dict], today: date) -> bool:
    """Reevalue une position ouverte, la mute EN PLACE selon les
    priorites : (1) stop, (2) delai_max -- deterministes, zero appel
    modele -- puis (3) reevaluation qualitative SEULEMENT si ni (1) ni
    (2) ne s'applique. Renvoie True si la position a change (cloturee
    ou journal modifie)."""
    since_date = position["journal"][-1]["date"] if position["journal"] else position["entry_date"]
    new_rows = _rows_since(ohlc_rows, since_date)

    breach = find_stop_breach(position["direction"], position["safety_stop_price"], new_rows)
    if breach is not None:
        _close_position(position, breach["date"], position["safety_stop_price"], "stop_securite", breach["close"])
        return True

    if today.isoformat() >= position["max_close_date"]:
        last_close = ohlc_rows[-1]["close"] if ohlc_rows else position["entry_price"]
        _close_position(position, today.isoformat(), last_close, "delai_max", last_close)
        return True

    verdict = call_claude_for_reeval(position, ohlc_rows)
    last_close = ohlc_rows[-1]["close"] if ohlc_rows else position["entry_price"]
    if verdict is not None and verdict.get("decision") == "cloturer":
        _close_position(position, today.isoformat(), last_close, "cloture_anticipee_ia", last_close)
        return True
    raison = verdict.get("raison") if verdict else "echec de l'appel API, position gardee par defaut"
    position["journal"].append({"date": today.isoformat(), "price": last_close, "decision": "garder", "raison": raison})
    return True


def process_new_entry(ohlc_rows: list[dict], today: date) -> dict | None:
    """Decide d'ouvrir une nouvelle position ou non. Renvoie le nouveau
    dict de position (status="open") ou None si l'appel est
    neutre/non reconnu/l'appel API a echoue."""
    verdict = call_claude_for_new_entry(ohlc_rows)
    if verdict is None:
        return None
    direction = normalize_direction_call(verdict.get("appel", ""))
    if direction is None:
        return None
    entry_price = ohlc_rows[-1]["close"]
    raw_stop_pct = verdict.get("stop_pct", MIN_STOP_PCT)
    if not isinstance(raw_stop_pct, (int, float)) or isinstance(raw_stop_pct, bool):
        raw_stop_pct = MIN_STOP_PCT
    safety_stop_price = compute_safety_stop_price(entry_price, direction, raw_stop_pct)
    safety_stop_pct = compute_safety_stop_pct(entry_price, safety_stop_price, direction)
    entry_date = today.isoformat()
    return {
        "id": f"XAUUSD-{entry_date}", "status": "open",
        "entry_date": entry_date, "entry_price": entry_price, "direction": direction,
        "safety_stop_price": safety_stop_price, "safety_stop_pct": safety_stop_pct,
        "confiance": verdict.get("confiance", ""), "raison_ouverture": verdict.get("raison", ""),
        "max_close_date": compute_max_close_date(today).isoformat(),
        "close_date": None, "close_price": None, "close_reason": None, "return_pct": None,
        "buyhold_price_at_close": None, "buyhold_return_pct": None, "journal": [],
    }


def main() -> None:
    ohlc_rows = fetch_daily_ohlc()
    if not ohlc_rows:
        return  # pas de prix -> aucune action, jamais de repli WebSearch
    today = date.fromisoformat(ohlc_rows[-1]["date"])
    tracking = _read_tracking()
    changed = False

    open_position = _find_open_position(tracking)
    if open_position is not None:
        changed = process_open_position(open_position, ohlc_rows, today) or changed
        open_position = _find_open_position(tracking)

    if open_position is None:
        new_position = process_new_entry(ohlc_rows, today)
        if new_position is not None:
            tracking["positions"].append(new_position)
            changed = True

    if changed:
        _write_tracking(tracking)


if __name__ == "__main__":
    main()
```

(`today` dérivé de `ohlc_rows[-1]["date"]`, pas de `date.today()` — rend `main()` déterministe et testable sans mocker l'horloge, et garantit que "aujourd'hui" correspond toujours à la dernière donnée de marché réellement récupérée.)

- [ ] **Step 4: Vérifier que les tests passent**

Run: `pytest tests/test_gold_news_reasoning_daily.py -v`
Expected: PASS (34 + 13 = 47 tests)

- [ ] **Step 5: Commit**

```bash
git add gold_news_reasoning_daily.py tests/test_gold_news_reasoning_daily.py
git commit -m "feat(gold-bot): orchestration main() du paper-trading quotidien or"
```

---

### Task 4 : Workflow GitHub Actions, validation réelle, retrait de la routine cloud

**Files:**
- Create: `.github/workflows/gold_news_reasoning_daily.yml`

**Interfaces:**
- Consumes : exécute `python3 gold_news_reasoning_daily.py` (Task 3) comme un script autonome.

- [ ] **Step 1 : Créer le workflow**

```yaml
name: Paper-trading quotidien IA or (test prospectif)

on:
  schedule:
    - cron: "0 7 * * 1-5"
  workflow_dispatch: {}

concurrency:
  group: gold-news-reasoning-daily
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

      - name: Paper-trading quotidien sur l'or
        run: python3 gold_news_reasoning_daily.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.CLAUDE_API_KEY }}

      - name: Publier le suivi mis à jour
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          [ -f docs/gold_news_reasoning_tracking.json ] && git add docs/gold_news_reasoning_tracking.json || true
          git diff --staged --quiet || git commit -m "chore(gold-bot): suivi quotidien test prospectif IA or"
          git pull --rebase --autostash
          git push
```

(Le `[ -f ... ] && git add ... || true` est nécessaire — pas un simple `git add` nu — parce que le fichier peut légitimement ne pas encore exister le jour où le tout premier run tombe sur un appel neutre sans position ouverte : `main()` ne l'écrit alors jamais. Même patron que les fichiers optionnels de `.github/workflows/indices.yml`.)

- [ ] **Step 2 : Commit**

```bash
git add .github/workflows/gold_news_reasoning_daily.yml
git commit -m "ci(gold-bot): workflow GitHub Actions du paper-trading quotidien or"
git push
```

- [ ] **Step 3 : Déclencher une exécution manuelle réelle**

```bash
gh workflow run gold_news_reasoning_daily.yml
```

- [ ] **Step 4 : Lire le résultat du run**

```bash
gh run list --workflow=gold_news_reasoning_daily.yml --limit 1
gh run view --log <run-id>
```

Vérifier : aucune erreur Python non gérée, le step "Paper-trading quotidien sur l'or" se termine avec succès, et soit un commit a été poussé (`git log -1 --stat` après `git pull`), soit (si l'appel du jour est neutre) aucun commit n'a été fait et aucune erreur n'est survenue.

- [ ] **Step 5 : Vérifier le fichier produit (si un commit a eu lieu)**

```bash
git pull
cat docs/gold_news_reasoning_tracking.json
```

Confirmer que le schéma correspond exactement à la spec §3.

- [ ] **Step 6 : Désactiver la routine cloud obsolète**

Charger `RemoteTrigger` (`ToolSearch select:RemoteTrigger`), puis :

`{action: "update", trigger_id: "trig_01KzB6rZrwxUW1yoike8KLrP", body: {"enabled": false}}`

Confirmer via `{action: "get", trigger_id: "trig_01KzB6rZrwxUW1yoike8KLrP"}` que `enabled` est bien `false` — un seul mécanisme de paper-trading sur l'or actif à partir de maintenant (le workflow GitHub Actions).

---

## Hors scope de ce plan (à traiter séparément)

- Cadence supra-quotidienne — explicitement différée par l'utilisateur à une itération future (spec §2, §5).
- Toute décision de construire un moteur de trading réel autour de ce raisonnement — uniquement après plusieurs semaines de positions simulées clôturées.
- Track 1 (fenêtre de blackout étendue par l'IA sur le compte FTMO réel) — chantier séparé, déjà terminé et déployé.
- Migration du pari déjà enregistré par le tout premier système (`docs/superpowers/experiments/2026-11-06-gold-news-reasoning-test.json`) — reste en l'état comme archive.
