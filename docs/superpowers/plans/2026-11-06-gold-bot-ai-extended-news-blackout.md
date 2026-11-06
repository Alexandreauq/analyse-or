# Fenêtre de blackout étendue par l'IA — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter un jugement IA, déclenché uniquement dans la fenêtre grise qui suit la fin d'un blackout calendaire (`confluence.is_news_blackout`), qui peut étendre la prudence au-delà de la fenêtre fixe quand le marché n'a pas réellement digéré l'actualité — corrige le trou qui a laissé passer la vente perdante du 2026-11-06 à 13h55 UTC, 10 minutes après la fin du blackout NFP.

**Architecture:** Trois pièces indépendantes assemblées en série : (1) `confluence.minutes_since_blackout_ended` détecte la fenêtre grise de façon pure (pas d'IA, pas d'effet de bord) ; (2) `gold_bot/ai_news_watch.py::judge_post_news_calm` appelle Claude avec le même patron fail-open/cooldown/log que `gold_bot/ai_shadow.py::shadow_judge_entry` ; (3) `decide_and_act_fibonacci` branche les deux, juste après le garde-fou réactif existant `confluence.is_volatility_shock`.

**Tech Stack:** Python, `anthropic` SDK (déjà une dépendance via `gold_bot/ai_shadow.py`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-06-gold-bot-ai-extended-news-blackout-design.md`

## Global Constraints

- `POST_NEWS_GRACE_MINUTES = 60` (fenêtre grise après la fin d'un blackout SCALP, 15min).
- `PRESS_CONFERENCE_POST_NEWS_GRACE_MINUTES = 80` (fenêtre grise après la fin d'un blackout press-conférence, 100min).
- Ne s'applique qu'à `decide_and_act_fibonacci` — pas à `decide_and_act_ichimoku`/`decide_and_act_vwap`, non-régression stricte exigée sur les deux.
- Ne modifie JAMAIS `confluence.SCALP_NEWS_BLACKOUT_MINUTES`/`PRESS_CONFERENCE_BLACKOUT_MINUTES`/`is_news_blackout`/`is_volatility_shock` eux-mêmes.
- Fail-open : un verdict `None` (API absente, erreur, timeout, non-parseable) ou `"calme"` laisse le trade se faire normalement ; seul `"encore_agite"` bloque.
- Jamais d'exception propagée hors de `judge_post_news_calm` — même garantie que `shadow_judge_entry`.

---

### Task 1 : `confluence.minutes_since_blackout_ended`

**Files:**
- Modify: `gold_bot/confluence.py` (ajouter après `is_volatility_shock`, avant `is_market_closed`)
- Test: `tests/gold_bot/test_confluence.py` (ajouter après les tests `is_news_blackout`/`is_volatility_shock` existants)

**Interfaces:**
- Consumes : `confluence.is_news_blackout` (déjà existante, inchangée), `confluence.SCALP_HIGH_IMPACT_EVENTS_UTC`/`SCALP_NEWS_BLACKOUT_MINUTES`/`PRESS_CONFERENCE_EVENTS_UTC`/`PRESS_CONFERENCE_BLACKOUT_MINUTES` (déjà existantes, inchangées).
- Produces : `confluence.POST_NEWS_GRACE_MINUTES = 60`, `confluence.PRESS_CONFERENCE_POST_NEWS_GRACE_MINUTES = 80`, `confluence.minutes_since_blackout_ended(date: datetime) -> tuple[float, float] | None`.

- [ ] **Step 1: Write the failing tests**

```python
# a ajouter a tests/gold_bot/test_confluence.py

def test_minutes_since_blackout_ended_none_during_active_blackout():
    from datetime import datetime, timezone
    # CPI du 11/09/2026, 12h30 UTC -- exactement dans la fenetre (voir
    # test_is_news_blackout_exactly_at_event existant).
    assert confluence.minutes_since_blackout_ended(datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)) is None


def test_minutes_since_blackout_ended_none_before_any_known_event():
    from datetime import datetime, timezone
    # Avant le tout premier evenement connu (NFP du 09/01/2026).
    assert confluence.minutes_since_blackout_ended(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)) is None


def test_minutes_since_blackout_ended_correct_right_after_scalp_window():
    from datetime import datetime, timezone
    # NFP du 06/11/2026, 13h30 UTC -- fenetre SCALP (15min) finit a
    # 13h45 UTC. 13h55 UTC = 10 minutes plus tard -- exactement le cas
    # reel du 2026-11-06.
    result = confluence.minutes_since_blackout_ended(datetime(2026, 11, 6, 13, 55, tzinfo=timezone.utc))
    assert result == (10.0, confluence.POST_NEWS_GRACE_MINUTES)


def test_minutes_since_blackout_ended_correct_right_after_press_conference_window():
    from datetime import datetime, timezone
    # FOMC du 28/10/2026, 18h00 UTC -- fenetre press-conference (100min)
    # finit a 19h40 UTC. 20h00 UTC = 20 minutes plus tard.
    result = confluence.minutes_since_blackout_ended(datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc))
    assert result == (20.0, confluence.PRESS_CONFERENCE_POST_NEWS_GRACE_MINUTES)


def test_minutes_since_blackout_ended_picks_the_most_recent_window():
    from datetime import datetime, timezone
    # BCE du 29/10/2026 13h15 UTC (press-conference, fenetre finit a
    # 14h55 UTC le 29/10) est BIEN avant le NFP du 06/11/2026 13h30 UTC
    # (SCALP, fenetre finit a 13h45 UTC). A 14h00 UTC le 06/11 (15min
    # apres la fin de la fenetre NFP), le NFP est la fenetre la PLUS
    # RECENTE des deux -- doit etre choisi, pas la BCE (bien plus
    # ancienne malgre un type different).
    result = confluence.minutes_since_blackout_ended(datetime(2026, 11, 6, 14, 0, tzinfo=timezone.utc))
    assert result == (15.0, confluence.POST_NEWS_GRACE_MINUTES)


def test_minutes_since_blackout_ended_still_returns_a_value_long_after_the_grace_period():
    from datetime import datetime, timezone
    # 6h15 apres la fin de la fenetre NFP du 06/11 (13h45 UTC) -- bien
    # au-dela de POST_NEWS_GRACE_MINUTES(60), mais la fonction renvoie
    # quand meme une valeur : c'est a L'APPELANT de comparer elapsed a
    # grace, pas a cette fonction de trancher.
    result = confluence.minutes_since_blackout_ended(datetime(2026, 11, 6, 20, 0, tzinfo=timezone.utc))
    assert result == (375.0, confluence.POST_NEWS_GRACE_MINUTES)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_confluence.py -k minutes_since_blackout -v`
Expected: FAIL avec `AttributeError: module 'gold_bot.confluence' has no attribute 'minutes_since_blackout_ended'` (et `POST_NEWS_GRACE_MINUTES` inconnue)

- [ ] **Step 3: Implement `minutes_since_blackout_ended`**

Dans `gold_bot/confluence.py`, juste après `is_volatility_shock` (avant `is_market_closed`) :

```python
POST_NEWS_GRACE_MINUTES = 60  # fenetre grise apres la fin d'une fenetre SCALP (15min)
PRESS_CONFERENCE_POST_NEWS_GRACE_MINUTES = 80  # apres la fin d'une fenetre press-conference (100min)


def minutes_since_blackout_ended(date: datetime) -> tuple[float, float] | None:
    """(minutes ecoulees depuis la fin de la derniere fenetre de
    blackout calendaire la plus recente avant `date`, grace applicable
    en minutes pour CE type d'evenement) -- ou None si `date` est
    encore DANS une fenetre de blackout (is_news_blackout(date) est
    True -- rien a faire, le garde-fou existant bloque deja), ou si
    aucun evenement qualifiant (fenetre deja terminee) n'est trouve
    avant `date` dans les listes connues. Ne tranche JAMAIS si la
    fenetre grise est depassee -- renvoie une valeur meme tres au-dela
    de la grace applicable, c'est a l'appelant de comparer."""
    if is_news_blackout(date):
        return None
    best: tuple[float, float] | None = None
    for iso in SCALP_HIGH_IMPACT_EVENTS_UTC:
        event = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        window_end = event + timedelta(minutes=SCALP_NEWS_BLACKOUT_MINUTES)
        if window_end <= date:
            elapsed = (date - window_end).total_seconds() / 60
            if best is None or elapsed < best[0]:
                best = (elapsed, POST_NEWS_GRACE_MINUTES)
    for iso in PRESS_CONFERENCE_EVENTS_UTC:
        event = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        window_end = event + timedelta(minutes=PRESS_CONFERENCE_BLACKOUT_MINUTES)
        if window_end <= date:
            elapsed = (date - window_end).total_seconds() / 60
            if best is None or elapsed < best[0]:
                best = (elapsed, PRESS_CONFERENCE_POST_NEWS_GRACE_MINUTES)
    return best
```

Ajouter `timedelta` à l'import existant `from datetime import datetime, timezone` (devient `from datetime import datetime, timedelta, timezone`) en haut de `gold_bot/confluence.py` si absent (vérifier avant d'éditer : `timedelta` n'est aujourd'hui pas importé dans ce fichier).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_confluence.py -k minutes_since_blackout -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full confluence test suite to confirm no regression**

Run: `pytest tests/gold_bot/test_confluence.py -v`
Expected: PASS intégralement (tous les tests existants + les 6 nouveaux)

- [ ] **Step 6: Commit**

```bash
git add gold_bot/confluence.py tests/gold_bot/test_confluence.py
git commit -m "feat(gold_bot): ajoute minutes_since_blackout_ended (fenetre grise post-blackout)"
```

---

### Task 2 : `gold_bot/ai_news_watch.py::judge_post_news_calm`

**Files:**
- Create: `gold_bot/ai_news_watch.py`
- Test: `tests/gold_bot/test_ai_news_watch.py`

**Interfaces:**
- Consumes : rien de la Task 1 (fonction indépendante — reçoit `minutes_since_blackout` en paramètre, ne recalcule rien elle-même). Reproduit le patron de `gold_bot/ai_shadow.py::shadow_judge_entry` (déjà lu en entier : `MODEL`, `REQUEST_TIMEOUT_SECONDS`, `WEB_SEARCH_MAX_USES`, cooldown via `threading.Lock`, `max_retries=0`, `max_tokens=4096`, `output_config={"effort": "medium"}`, extraction JSON via regex, jamais d'exception propagée).
- Produces : `ai_news_watch.judge_post_news_calm(symbol: str, candles: list[dict], minutes_since_blackout: float, *, log_path: str = ai_news_watch.WATCH_LOG_PATH, now: datetime | None = None) -> dict | None` (verdict `{"verdict": "calme" ou "encore_agite", "confiance": ..., "raison": ...}` ou `None`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/gold_bot/test_ai_news_watch.py (nouveau fichier)
import json
from datetime import datetime, timedelta, timezone

import anthropic
import pytest

import gold_bot.ai_news_watch as ai_news_watch


@pytest.fixture(autouse=True)
def _reset_watch_cooldown():
    """Meme raison que le fixture equivalent de test_ai_shadow.py :
    _last_call_at/_last_verdict sont des dicts au niveau module,
    partages entre tous les tests de ce fichier."""
    ai_news_watch._last_call_at.clear()
    ai_news_watch._last_verdict.clear()
    yield
    ai_news_watch._last_call_at.clear()
    ai_news_watch._last_verdict.clear()


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response=None, error=None):
        self.messages = _FakeMessages(response=response, error=error)

    def __call__(self, api_key, timeout, max_retries=None):
        return self


def _candles():
    return [
        {"time": "2026-11-06 13:45:00", "open": 4056.68, "high": 4058.40, "low": 4025.09, "close": 4049.66},
        {"time": "2026-11-06 13:50:00", "open": 4049.57, "high": 4054.94, "low": 4044.59, "close": 4052.85},
    ]


def test_judge_post_news_calm_returns_none_and_logs_when_api_key_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    result = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path)
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 1
    assert entries[0]["error"] == "ANTHROPIC_API_KEY absent"


def test_judge_post_news_calm_parses_verdict_and_logs_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "encore_agite", "confiance": "haute", "raison": "le marche digere encore le NFP"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    result = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path)
    assert result == {"verdict": "encore_agite", "confiance": "haute", "raison": "le marche digere encore le NFP"}
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["symbol"] == "XAUUSD"
    assert entries[0]["minutes_since_blackout"] == 10.0
    assert entries[0]["verdict"] == result


def test_judge_post_news_calm_returns_none_and_logs_when_verdict_unparseable(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock("reponse sans JSON exploitable")]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    result = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path)
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["error"] == "verdict JSON non trouve ou invalide dans la reponse"


def test_judge_post_news_calm_never_raises_and_logs_on_client_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    fake_client = _FakeAnthropicClient(error=TimeoutError("boom"))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    result = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path)
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert "TimeoutError" in entries[0]["error"]


def test_judge_post_news_calm_skips_second_call_within_cooldown_reusing_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "encore_agite", "confiance": "haute", "raison": "test"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 11, 6, 13, 55, 0, tzinfo=timezone.utc)
    first = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path, now=t0)
    second = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 11.0, log_path=log_path, now=t0 + timedelta(minutes=1))
    assert first is not None
    assert second == first
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 1  # un seul appel reel malgre les deux invocations


def test_judge_post_news_calm_calls_again_after_cooldown_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "calme", "confiance": "haute", "raison": "test"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 11, 6, 13, 55, 0, tzinfo=timezone.utc)
    first = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path, now=t0)
    second = ai_news_watch.judge_post_news_calm(
        "XAUUSD", _candles(), 10.0, log_path=log_path, now=t0 + ai_news_watch.WATCH_COOLDOWN + timedelta(seconds=1),
    )
    assert first is not None and second is not None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 2


def test_judge_post_news_calm_cooldown_is_per_symbol(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "calme", "confiance": "haute", "raison": "test"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 11, 6, 13, 55, 0, tzinfo=timezone.utc)
    xau = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path, now=t0)
    xag = ai_news_watch.judge_post_news_calm("XAGUSD", _candles(), 10.0, log_path=log_path, now=t0 + timedelta(minutes=1))
    assert xau is not None and xag is not None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_ai_news_watch.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'gold_bot.ai_news_watch'`

- [ ] **Step 3: Implement `gold_bot/ai_news_watch.py`**

```python
# gold_bot/ai_news_watch.py
# Jugement IA sur le CALME du marche apres la fin d'une fenetre de
# blackout calendaire (voir gold_bot/confluence.py::minutes_since_blackout_ended)
# -- responsabilite distincte du veto d'entree gold_bot/ai_shadow.py
# (qui juge un TRADE precis, pas l'etat general du marche). Branche
# pour de vrai depuis sa creation (decide_and_act_fibonacci) : seul un
# verdict "encore_agite" etend le blackout, "calme"/None (echec reel,
# API absente, timeout, verdict non parseable) laissent le trade se
# faire normalement -- JAMAIS de raccourcissement d'une fenetre de
# blackout deja active, uniquement une extension apres sa fin. Trouve
# le 2026-11-06 : la vente perdante de 13h55 UTC ce jour-la est passee
# 10 minutes apres la fin du blackout NFP (fenetre fixe de 15min),
# pendant que le marche digerait encore le choc -- meme patron
# fail-open/cooldown/log que ai_shadow.shadow_judge_entry (voir ce
# fichier), jamais d'exception propagee, cooldown pour eviter un cout
# repete si plusieurs cycles tombent dans la meme fenetre grise.
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

WATCH_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_news_watch_log.jsonl")
MODEL = "claude-opus-5-5"
MAX_CANDLES_IN_PROMPT = 12
REQUEST_TIMEOUT_SECONDS = 40.0
WEB_SEARCH_MAX_USES = 2
WATCH_COOLDOWN = timedelta(hours=24)

_last_call_at: dict[str, datetime] = {}  # cle symbol seul -- un seul evenement de marche a la fois par symbole
_last_verdict: dict[str, dict] = {}
_cooldown_lock = threading.Lock()

_VERDICT_JSON_RE = re.compile(r"\{[^{}]*\"verdict\"[^{}]*\}")


def _describe_candles(candles: list[dict], limit: int = MAX_CANDLES_IN_PROMPT) -> str:
    recent = candles[-limit:]
    lines = []
    for c in recent:
        try:
            lines.append(
                f"{c['time']}: O={c['open']:.2f} H={c['high']:.2f} L={c['low']:.2f} C={c['close']:.2f}"
            )
        except (KeyError, TypeError, ValueError):
            continue
    return "\n".join(lines)


def _build_prompt(symbol: str, candles: list[dict], minutes_since_blackout: float) -> str:
    return (
        f"Une fenetre de blackout calendaire (publication macro a fort impact) sur {symbol} "
        f"s'est terminee il y a {minutes_since_blackout:.0f} minutes.\n\n"
        f"Dernieres bougies 5min (plus recente en dernier) :\n{_describe_candles(candles)}\n\n"
        f"Cherche s'il y a une actualite recente qui pourrait expliquer un mouvement de prix "
        f"inhabituel, et juge si le marche semble avoir reellement digere cette actualite, ou "
        f"s'il montre encore des signes de bruit/volatilite anormale qui justifieraient de "
        f"prolonger la prudence au-dela de la fenetre fixe deja ecoulee.\n\n"
        f"Ceci est une VRAIE decision : si tu juges que le marche est encore agite (verdict "
        f'"encore_agite"), un signal d\'entree reel sera bloque. Si tu juges que le calme est '
        f"revenu (verdict \"calme\"), ou si tu n'as rien trouve de pertinent, le trade se fera "
        f"normalement -- ne t'oppose pas par exces de prudence sans raison concrete.\n\n"
        f"Termine ta reponse par un bloc JSON seul sur sa derniere ligne, exactement sous "
        f'cette forme : {{"verdict": "calme ou encore_agite", "confiance": "faible ou '
        f'moyenne ou haute", "raison": "une phrase"}}'
    )


def _parse_verdict(text: str) -> dict | None:
    matches = _VERDICT_JSON_RE.findall(text)
    if not matches:
        return None
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _append_log(entry: dict, path: str = WATCH_LOG_PATH) -> None:
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def judge_post_news_calm(symbol: str, candles: list[dict], minutes_since_blackout: float, *,
                          log_path: str = WATCH_LOG_PATH, now: datetime | None = None) -> dict | None:
    """Appelle Claude pour juger si le marche a reellement digere
    l'actualite dont la fenetre de blackout calendaire vient de se
    terminer (voir confluence.minutes_since_blackout_ended). Renvoie
    {"verdict": "calme" ou "encore_agite", "confiance", "raison"} si
    l'appel reussit et produit un verdict exploitable, None dans tous
    les autres cas (API absente, erreur, timeout, verdict non
    parseable -- jamais d'exception propagee). Pendant le cooldown
    (WATCH_COOLDOWN), renvoie le dernier verdict REEL obtenu pour ce
    symbole plutot que None, meme raison que
    ai_shadow.shadow_judge_entry (eviter qu'un verdict "encore_agite"
    soit annule en fail-open au cycle suivant). Cooldown par symbole
    seul (pas de direction/niveau ici, contrairement au veto d'entree :
    un seul evenement de marche affecte tout le symbole a la fois, pas
    un trade precis)."""
    now = now or datetime.now(timezone.utc)
    with _cooldown_lock:
        last = _last_call_at.get(symbol)
        if last is not None and now - last < WATCH_COOLDOWN:
            return _last_verdict.get(symbol)
        _last_call_at[symbol] = now
        _last_verdict.pop(symbol, None)

    entry: dict = {
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": symbol,
        "minutes_since_blackout": minutes_since_blackout,
    }
    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            entry["error"] = "ANTHROPIC_API_KEY absent"
            _append_log(entry, log_path)
            return None

        client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            output_config={"effort": "medium"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES}],
            messages=[{"role": "user", "content": _build_prompt(symbol, candles, minutes_since_blackout)}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        verdict = _parse_verdict(text)
        entry["raw_text"] = text
        entry["verdict"] = verdict
        if verdict is None:
            entry["error"] = "verdict JSON non trouve ou invalide dans la reponse"
        else:
            with _cooldown_lock:
                _last_verdict[symbol] = verdict
        _append_log(entry, log_path)
        return verdict
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {e}"
        _append_log(entry, log_path)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_ai_news_watch.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add gold_bot/ai_news_watch.py tests/gold_bot/test_ai_news_watch.py
git commit -m "feat(gold_bot): ajoute ai_news_watch.judge_post_news_calm"
```

---

### Task 3 : Branchement dans `decide_and_act_fibonacci`

**Files:**
- Modify: `gold_bot/bot.py` (import en haut du fichier + `decide_and_act_fibonacci`, ligne 450-451 actuelles — juste après le check `confluence.is_volatility_shock`)
- Test: `tests/gold_bot/test_bot.py`

**Interfaces:**
- Consumes : `confluence.minutes_since_blackout_ended` (Task 1), `ai_news_watch.judge_post_news_calm` (Task 2).
- Produces : comportement étendu de `decide_and_act_fibonacci` — nouvelle valeur de retour possible `{"action": "aucune", "reason": "prudence post-actualite (IA)"}`.

- [ ] **Step 1: Lire l'état actuel exact avant d'écrire les tests**

Vérifier dans `gold_bot/bot.py` que les lignes suivantes sont toujours identiques à cette référence (le fichier a été modifié plusieurs fois le 2026-11-06, relire avant de modifier) :

```python
    if state["hour_utc"] >= fibonacci_reversion.SESSION_END_HOUR_UTC:
        return {"action": "aucune", "reason": "trop tard dans la session pour ouvrir"}
    if confluence.is_volatility_shock(candles) is not False:
        return {"action": "aucune", "reason": "volatilite anormale (actualite probable), signal ignore par prudence"}
    if state["level"] is None or state["bias"] is None:
        return {"action": "aucune", "reason": "aucun niveau de retracement disponible"}
```

Si le texte diverge, adapter le point d'insertion à la structure réelle trouvée (la contrainte : toujours après `is_volatility_shock`, toujours avant `state["level"] is None`), ne jamais copier-coller à l'aveugle sur un texte différent.

- [ ] **Step 2: Write the failing tests**

```python
# a ajouter a tests/gold_bot/test_bot.py, pres des autres tests
# decide_and_act_fibonacci existants (reutilise _fib_state/_open_circuit_breaker
# deja definis dans ce fichier)

def test_decide_and_act_fibonacci_blocked_by_ai_when_still_agitated_after_blackout(monkeypatch):
    # Reproduction DIRECTE de l'incident reel du 2026-11-06 : NFP a
    # 13h30 UTC, fenetre fixe (15min) finit a 13h45 UTC, candle a 13h55
    # UTC (comme le vrai trade perdant) -- 10 minutes dans la fenetre
    # grise (60min). Avec l'IA qui juge "encore_agite", ce trade doit
    # maintenant etre bloque, contrairement au comportement actuel.
    monkeypatch.setattr(bot.ai_news_watch, "judge_post_news_calm", lambda *a, **k: {"verdict": "encore_agite", "confiance": "haute", "raison": "test"})
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-11-06 13:55:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "prudence post-actualite (IA)"}


def test_decide_and_act_fibonacci_proceeds_when_ai_says_calm_after_blackout(monkeypatch):
    monkeypatch.setattr(bot.ai_news_watch, "judge_post_news_calm", lambda *a, **k: {"verdict": "calme", "confiance": "haute", "raison": "test"})
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-11-06 13:55:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"


def test_decide_and_act_fibonacci_proceeds_when_ai_news_watch_fails_open(monkeypatch):
    monkeypatch.setattr(bot.ai_news_watch, "judge_post_news_calm", lambda *a, **k: None)
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-11-06 13:55:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"


def test_decide_and_act_fibonacci_does_not_call_ai_news_watch_outside_grace_window(monkeypatch):
    # Garde-fou de cout : hors de toute fenetre grise, judge_post_news_calm
    # ne doit JAMAIS etre appelee -- une bougie datee loin de tout
    # evenement connu (ex. un dimanche calme) suffit a le prouver.
    monkeypatch.setattr(
        bot.ai_news_watch, "judge_post_news_calm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("judge_post_news_calm ne doit pas etre appele hors fenetre grise")))
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-09-13 12:30:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_bot.py -k ai_news_watch -v`
Expected: FAIL — les 2 premiers tests échouent car `judge_post_news_calm` n'est jamais appelée (`result["action"]` vaut `"simulation"` quel que soit le mock), le 4e échoue probablement aussi avec `AttributeError: module 'gold_bot.bot' has no attribute 'ai_news_watch'` tant que l'import n'existe pas.

- [ ] **Step 4: Brancher dans `decide_and_act_fibonacci`**

Ajouter l'import en haut de `gold_bot/bot.py`, à côté des imports `gold_bot` existants (ordre alphabétique déjà respecté dans ce fichier — `ai_news_watch` < `ai_shadow` alphabétiquement, donc juste AVANT `import gold_bot.ai_shadow as ai_shadow`, qui est aujourd'hui le premier import `gold_bot.*` du fichier) :

```python
import gold_bot.ai_news_watch as ai_news_watch
```

Dans `decide_and_act_fibonacci`, remplacer :

```python
    if confluence.is_volatility_shock(candles) is not False:
        return {"action": "aucune", "reason": "volatilite anormale (actualite probable), signal ignore par prudence"}
    if state["level"] is None or state["bias"] is None:
```

par :

```python
    if confluence.is_volatility_shock(candles) is not False:
        return {"action": "aucune", "reason": "volatilite anormale (actualite probable), signal ignore par prudence"}
    # Fenetre grise post-blackout (voir gold_bot/ai_news_watch.py) --
    # trouve le 2026-11-06 : le blackout calendaire fixe (is_news_blackout
    # ci-dessus) peut finir avant que le marche ait reellement digere
    # l'actualite. Ne se declenche QUE dans cette fenetre grise, jamais
    # a chaque cycle -- garde de cout identique au veto d'entree.
    grace_info = confluence.minutes_since_blackout_ended(as_of)
    if grace_info is not None:
        elapsed, grace = grace_info
        if elapsed <= grace:
            news_verdict = ai_news_watch.judge_post_news_calm(symbol, candles, elapsed)
            if news_verdict is not None and news_verdict.get("verdict") == "encore_agite":
                return {"action": "aucune", "reason": "prudence post-actualite (IA)"}
    if state["level"] is None or state["bias"] is None:
```

(`as_of` est déjà calculé plus haut dans la fonction, ligne 399 actuelle — ne pas le recalculer.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_bot.py -k "ai_news_watch or fibonacci" -v`
Expected: PASS intégralement (4 nouveaux tests + tous les tests `fibonacci` existants, aucune régression)

- [ ] **Step 6: Run the entire project test suite**

Run: `pytest -q`
Expected: PASS intégralement, aucune régression sur VWAP/Ichimoku/le reste du projet.

- [ ] **Step 7: Commit**

```bash
git add gold_bot/bot.py tests/gold_bot/test_bot.py
git commit -m "feat(gold_bot): branche la fenetre de blackout etendue par l'IA sur decide_and_act_fibonacci"
```

---

## Hors scope de ce plan (à traiter séparément)

- `decide_and_act_ichimoku`/`decide_and_act_vwap` ne sont pas étendus (voir spec, section 3).
- Déploiement sur le VPS FTMO — une fois ce plan testé et relu, suivre la même procédure que les déploiements du 2026-11-06 (push + pull + imports + redémarrage des services, vérifiée avant chaque déploiement de ce jour).
- Track 2 (test prospectif IA raisonnement actu/prix sur l'or) — plan séparé, voir `docs/superpowers/specs/2026-11-06-gold-news-reasoning-forward-test-design.md`.
