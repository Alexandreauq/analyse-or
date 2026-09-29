# Gold Bot — Mode Swing Macro Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bascule `gold_bot` du scalping (aucun edge validé) vers un mode swing longue durée (plusieurs jours), déclenché par le score macro-fondamental déjà en production (`gold_score.py`).

**Architecture:** Nouveau module `gold_bot/macro_signal.py` qui lit le payload publié de `gold_score.py` (`docs/score.json`, servi via GitHub Pages) et en dérive un signal d'entrée/sortie. Nouvelle fonction `gold_bot/bot.py::decide_and_act_swing()`, contrepartie longue-uniquement de `decide_and_act()` existante, avec le même contrat de sortie (`steps` réutilisables tels quels par `loop.execute_steps`). `gold_bot/loop.py::run_cycle` appelle la nouvelle fonction à la place de l'ancienne. L'ancien moteur scalping (`confluence.py`, `chart_patterns.py`, `decide_and_act`) reste intact, non supprimé.

**Tech Stack:** Python, pytest, requests (déjà une dépendance du projet via `gold_bot/broker.py` et `gold_score.py`).

**Spec:** `docs/superpowers/specs/2026-09-29-gold-bot-swing-macro-design.md`

## Global Constraints

- Signal long uniquement (pas de vente à découvert) — cohérent avec la méthodologie `gold_score.py`, qui n'a pas d'alerte symétrique baissière.
- Les 5 profils de risque existants (`risk.RISK_PROFILE_PARAMS`) et le coupe-circuit journalier (`risk.CircuitBreaker`) sont réutilisés tels quels, sans modification.
- Aucun changement à `risk.py`, `state.py`, `api.py`, `notify.py`.
- L'ancien moteur scalping (`confluence.py`, `chart_patterns.py`, `bot.decide_and_act`) n'est ni supprimé ni modifié — seulement débranché de `loop.run_cycle`.
- URL publiée du score macro : `https://alexandreauq.github.io/analyse-or/score.json`.
- Constantes : `EXIT_COMPOSITE_THRESHOLD = 0.0`, `EXIT_CFTC_PERCENTILE = 90.0`, `SWING_STOP_BUFFER_PCT = 0.03`, `SWING_TAKE_PROFIT_R_MULTIPLE = 3.0`, `SWING_MAX_HOLDING_DAYS = 30`.

---

## Task 1 : `gold_score.py` — expose `cftc_percentile` dans le payload publié

**Files:**
- Modify: `gold_score.py:817-828` (dict `payload` dans `main()`)

**Interfaces:**
- Produit : le payload JSON écrit dans `score.json`/`docs/score.json` gagne un champ `"cftc_percentile"` au premier niveau, valeur = la variable locale `cftc_percentile` déjà calculée ligne 760 (`cftc_factor, cftc_percentile = factor_positionnement_cftc()`).

- [ ] **Step 1 : Ajouter le champ au payload**

Dans `gold_score.py`, le dict `payload` (actuellement) :

```python
    payload = {
        "date": datetime.today().strftime("%Y-%m-%d"),
        "composite_score": composite,
        "interpretation": interpret(composite),
        "factors": [
            {"name": f.name, "score": f.score, "weight": f.weight, "raw_value": f.raw_value}
            for f in factors
        ],
        "technical": {"note": technical_note, "spot": spot, "ma200": ma200},
        "calendar": calendar,
        "alerts": alerts,
    }
```

devient :

```python
    payload = {
        "date": datetime.today().strftime("%Y-%m-%d"),
        "composite_score": composite,
        "interpretation": interpret(composite),
        "cftc_percentile": cftc_percentile,
        "factors": [
            {"name": f.name, "score": f.score, "weight": f.weight, "raw_value": f.raw_value}
            for f in factors
        ],
        "technical": {"note": technical_note, "spot": spot, "ma200": ma200},
        "calendar": calendar,
        "alerts": alerts,
    }
```

- [ ] **Step 2 : Vérifier manuellement**

Il n'existe aujourd'hui aucun fichier de test pour `gold_score.py` (script à sources partiellement manuelles, non testé unitairement dans ce repo) — ne pas en créer un pour ce seul changement (hors du champ de ce plan). Vérifier à la place par une inspection statique :

Run : `python3 -c "import ast; tree = ast.parse(open('gold_score.py').read()); print('OK - syntaxe valide')"`
Expected: `OK - syntaxe valide`

Puis relire à l'oeil le bloc modifié (`sed -n '815,830p' gold_score.py` ou équivalent) pour confirmer que `"cftc_percentile": cftc_percentile,` apparaît bien entre `"interpretation"` et `"factors"`, et que la virgule de fin de chaque ligne du dict reste correcte (JSON généré par `json.dump`, pas de risque de syntaxe JSON invalide côté Python, mais une virgule manquante casserait le dict Python lui-même — la vérification `ast.parse` ci-dessus le couvre déjà).

- [ ] **Step 3 : Commit**

```bash
git add gold_score.py
git commit -m "feat(gold_score): expose cftc_percentile en champ de premier niveau du payload"
```

---

## Task 2 : `gold_bot/macro_signal.py` (nouveau module)

**Files:**
- Create: `gold_bot/macro_signal.py`
- Test: `tests/gold_bot/test_macro_signal.py`

**Interfaces:**
- Consomme : le payload JSON publié par `gold_score.py` (voir Task 1) — champs `composite_score`, `cftc_percentile`, `technical.ma200`, `alerts` (liste de `{"kind": ..., ...}`).
- Produit : `fetch_macro_payload() -> dict`, `has_entry_alert(payload: dict) -> bool`, `compute_entry_levels(payload: dict, current_price: float) -> dict | None` (clés `entry`/`stop_loss`/`take_profit`), `should_exit(payload: dict, position_open_time_iso: str | None, now: datetime) -> tuple[bool, str]`. Consommées par Task 3 (`gold_bot/bot.py::decide_and_act_swing`) et Task 4 (`gold_bot/loop.py::run_cycle`).

- [ ] **Step 1 : Écrire les tests (échec attendu : le module n'existe pas encore)**

Créer `tests/gold_bot/test_macro_signal.py` :

```python
from datetime import datetime, timezone

import pytest

import gold_bot.macro_signal as macro_signal


def _payload(composite_score=20.0, cftc_percentile=50.0, ma200=4000.0, alerts=None):
    return {
        "composite_score": composite_score,
        "cftc_percentile": cftc_percentile,
        "technical": {"ma200": ma200, "spot": ma200},
        "alerts": alerts if alerts is not None else [],
    }


def test_has_entry_alert_true_when_entree_alert_present():
    payload = _payload(alerts=[{"kind": "entree", "title": "Conditions d'entrée réunies"}])
    assert macro_signal.has_entry_alert(payload) is True


def test_has_entry_alert_false_when_no_entree_alert():
    payload = _payload(alerts=[{"kind": "risque", "title": "Positionnement CFTC en zone extrême"}])
    assert macro_signal.has_entry_alert(payload) is False


def test_has_entry_alert_false_when_alerts_missing():
    payload = {"composite_score": 20.0}
    assert macro_signal.has_entry_alert(payload) is False


def test_compute_entry_levels_nominal():
    payload = _payload(ma200=4000.0)
    levels = macro_signal.compute_entry_levels(payload, current_price=4010.0)
    assert levels["entry"] == 4010.0
    assert levels["stop_loss"] == pytest.approx(4000.0 * 0.97)
    distance = 4010.0 - 4000.0 * 0.97
    assert levels["take_profit"] == pytest.approx(4010.0 + 3.0 * distance)


def test_compute_entry_levels_none_when_ma200_missing():
    payload = _payload(ma200=None)
    assert macro_signal.compute_entry_levels(payload, current_price=4010.0) is None


def test_compute_entry_levels_none_when_stop_distance_not_positive():
    # MM200 très supérieure au prix courant -> stop calculé au-dessus du prix.
    payload = _payload(ma200=5000.0)
    assert macro_signal.compute_entry_levels(payload, current_price=4010.0) is None


def test_should_exit_true_on_composite_at_or_below_threshold():
    payload = _payload(composite_score=0.0, cftc_percentile=50.0)
    should_exit, reason = macro_signal.should_exit(payload, None, datetime(2026, 9, 29, tzinfo=timezone.utc))
    assert should_exit is True
    assert "composite" in reason


def test_should_exit_true_on_cftc_extreme():
    payload = _payload(composite_score=20.0, cftc_percentile=90.0)
    should_exit, reason = macro_signal.should_exit(payload, None, datetime(2026, 9, 29, tzinfo=timezone.utc))
    assert should_exit is True
    assert "CFTC" in reason


def test_should_exit_true_on_max_holding_days_exceeded():
    payload = _payload(composite_score=20.0, cftc_percentile=50.0)
    open_time_iso = "2026-08-01T00:00:00.000Z"
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)  # 59 jours plus tard
    should_exit, reason = macro_signal.should_exit(payload, open_time_iso, now)
    assert should_exit is True
    assert "durée" in reason


def test_should_exit_false_when_no_condition_met():
    payload = _payload(composite_score=20.0, cftc_percentile=50.0)
    open_time_iso = "2026-09-20T00:00:00.000Z"
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)  # 9 jours plus tard
    should_exit, reason = macro_signal.should_exit(payload, open_time_iso, now)
    assert should_exit is False


def test_should_exit_ignores_unparseable_open_time():
    payload = _payload(composite_score=20.0, cftc_percentile=50.0)
    should_exit, reason = macro_signal.should_exit(payload, "pas une date", datetime(2026, 9, 29, tzinfo=timezone.utc))
    assert should_exit is False


def test_fetch_macro_payload_returns_parsed_json(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"composite_score": 20.0}

    captured = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(macro_signal.requests, "get", fake_get)
    payload = macro_signal.fetch_macro_payload()
    assert payload == {"composite_score": 20.0}
    assert captured["url"] == macro_signal.SCORE_JSON_URL
```

- [ ] **Step 2 : Lancer les tests, vérifier l'échec**

Run: `pytest tests/gold_bot/test_macro_signal.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gold_bot.macro_signal'`

- [ ] **Step 3 : Implémenter le module**

Créer `gold_bot/macro_signal.py` :

```python
# gold_bot/macro_signal.py
# Signal swing macro dérivé du score gold_score.py déjà en production
# (taux réels FRED, DXY, CFTC, MM200) -- voir
# docs/superpowers/specs/2026-09-29-gold-bot-swing-macro-design.md.
from datetime import datetime

import requests

SCORE_JSON_URL = "https://alexandreauq.github.io/analyse-or/score.json"
ENTRY_ALERT_KIND = "entree"
# Le composite retombe à/sous 0 -> thèse haussière invalidée (l'entrée
# exigeait > 15, voir gold_score.py::compute_alerts).
EXIT_COMPOSITE_THRESHOLD = 0.0
# Même seuil que CFTC_EXTREME_PERCENTILE dans gold_score.py --
# positionnement spéculatif trop encombré pour rester exposé.
EXIT_CFTC_PERCENTILE = 90.0
# Stop sous la MM200 -- le support que la condition d'entrée de
# gold_score.py exige déjà d'être proche (near_support, écart < 1%).
SWING_STOP_BUFFER_PCT = 0.03
SWING_TAKE_PROFIT_R_MULTIPLE = 3.0
# Filet de sécurité : clôture forcée au-delà, indépendamment du signal.
SWING_MAX_HOLDING_DAYS = 30


def fetch_macro_payload(url: str = SCORE_JSON_URL, timeout: float = 15.0) -> dict:
    """Va chercher le payload JSON publié par gold_score.py (déjà en
    production, rafraîchi environ toutes les heures via GitHub Actions).
    Lève l'exception réseau telle quelle -- même convention que
    gold_bot.broker : l'appelant (gold_bot.loop.run_cycle, via
    _with_retry) gère déjà le retry et la capture d'erreur génériques."""
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def has_entry_alert(payload: dict) -> bool:
    return any(a.get("kind") == ENTRY_ALERT_KIND for a in payload.get("alerts", []))


def compute_entry_levels(payload: dict, current_price: float) -> dict | None:
    """Niveaux d'entrée pour une position swing longue : stop sous la
    MM200, take-profit à SWING_TAKE_PROFIT_R_MULTIPLE fois cette
    distance. Renvoie None si la MM200 est indisponible dans le payload,
    ou si le prix courant a bougé depuis le dernier calcul du score
    (payload rafraîchi à l'heure) au point que le stop théorique tombe
    au-dessus (ou égal) au prix courant -- pas d'entrée sur un stop
    invalide."""
    ma200 = payload.get("technical", {}).get("ma200")
    if ma200 is None:
        return None
    entry = current_price
    stop_loss = ma200 * (1 - SWING_STOP_BUFFER_PCT)
    distance = entry - stop_loss
    if distance <= 0:
        return None
    take_profit = entry + SWING_TAKE_PROFIT_R_MULTIPLE * distance
    return {"entry": entry, "stop_loss": stop_loss, "take_profit": take_profit}


def should_exit(payload: dict, position_open_time_iso: str | None, now: datetime) -> tuple[bool, str]:
    """Évalue les 3 conditions de sortie d'une position swing, dans
    l'ordre -- voir la section 3 du spec pour la justification de
    chacune. `position_open_time_iso` est le champ MetaApi `time` d'une
    position (format ISO -- vérifié le 2026-09-29 via la documentation
    officielle MetaApi, MetatraderPosition.time). Un champ absent ou
    illisible ne fait qu'ignorer la 3e condition (durée de détention),
    jamais lever d'exception ni forcer une sortie par excès de
    prudence."""
    composite_score = payload.get("composite_score")
    if composite_score is not None and composite_score <= EXIT_COMPOSITE_THRESHOLD:
        return True, f"score composite retombé à {composite_score:+.1f} (seuil {EXIT_COMPOSITE_THRESHOLD:+.1f})"

    cftc_percentile = payload.get("cftc_percentile")
    if cftc_percentile is not None and cftc_percentile >= EXIT_CFTC_PERCENTILE:
        return True, f"positionnement CFTC en zone extrême ({cftc_percentile:.0f}e percentile)"

    if position_open_time_iso:
        try:
            open_time = datetime.fromisoformat(position_open_time_iso.replace("Z", "+00:00"))
        except (ValueError, AttributeError, TypeError):
            open_time = None
        if open_time is not None and (now - open_time).days >= SWING_MAX_HOLDING_DAYS:
            return True, f"durée de détention maximale atteinte ({SWING_MAX_HOLDING_DAYS} jours)"

    return False, ""
```

- [ ] **Step 4 : Lancer les tests, vérifier le succès**

Run: `pytest tests/gold_bot/test_macro_signal.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5 : Commit**

```bash
git add gold_bot/macro_signal.py tests/gold_bot/test_macro_signal.py
git commit -m "feat(gold_bot): ajoute macro_signal, dérive entrée/sortie swing du score gold_score.py"
```

---

## Task 3 : `gold_bot/bot.py::decide_and_act_swing`

**Files:**
- Modify: `gold_bot/bot.py` (ajoute la fonction, ajoute l'import `gold_bot.macro_signal`)
- Test: `tests/gold_bot/test_bot.py` (ajoute les cas ci-dessous à la fin du fichier)

**Interfaces:**
- Consomme : `gold_bot.macro_signal.has_entry_alert`, `.compute_entry_levels`, `.should_exit` (Task 2) ; `gold_bot.risk.compute_position_size`, `.round_to_volume_step`, `CircuitBreaker` (existants, inchangés).
- Produit : `decide_and_act_swing(macro_payload, candles, *, contract_size, balance, equity, volume_step, min_volume, max_volume, open_positions, circuit_breaker, symbol="XAUUSD", risk_pct=0.05, now=None) -> dict`, même forme de retour que `decide_and_act` (`{"action": "simulation", "steps": [...]}` ou `{"action": "aucune", "reason": ...}`) — consommée par Task 4 (`gold_bot/loop.py::run_cycle`).

- [ ] **Step 1 : Écrire les tests (échec attendu : la fonction n'existe pas encore)**

Ajouter à la fin de `tests/gold_bot/test_bot.py` :

```python
def _macro_payload(composite_score=20.0, cftc_percentile=50.0, ma200=4000.0, has_entry=True):
    alerts = [{"kind": "entree"}] if has_entry else []
    return {
        "composite_score": composite_score,
        "cftc_percentile": cftc_percentile,
        "technical": {"ma200": ma200, "spot": ma200},
        "alerts": alerts,
    }


def test_decide_and_act_swing_opens_when_entry_alert_and_no_position():
    payload = _macro_payload(has_entry=True, ma200=4000.0)
    candles = [{"time": "2026-09-29 10:00:00", "close": 4010.0}]
    result = bot.decide_and_act_swing(
        payload, candles, contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 1
    step = result["steps"][0]
    assert step["type"] == "ouverture_simulee"
    assert step["direction"] == "achat"
    assert step["entry"] == 4010.0
    assert step["stop_loss"] == pytest.approx(4000.0 * 0.97)


def test_decide_and_act_swing_no_action_when_no_entry_alert():
    payload = _macro_payload(has_entry=False)
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=[],
        circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "pas de signal d'entrée macro"}


def test_decide_and_act_swing_no_action_when_no_candles():
    payload = _macro_payload(has_entry=True)
    result = bot.decide_and_act_swing(
        payload, [], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "aucune bougie disponible pour le prix courant"}


def test_decide_and_act_swing_no_action_when_levels_unavailable():
    payload = _macro_payload(has_entry=True, ma200=None)
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=[],
        circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "niveaux d'entrée indisponibles (MM200 absente ou stop invalide)"}


def test_decide_and_act_swing_holds_open_position_when_no_exit_condition():
    from datetime import datetime, timezone
    payload = _macro_payload(composite_score=20.0, cftc_percentile=50.0)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "time": "2026-09-20T00:00:00.000Z"}]
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing,
        circuit_breaker=_open_circuit_breaker(), now=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    assert result["action"] == "aucune"


def test_decide_and_act_swing_closes_when_composite_degrades():
    from datetime import datetime, timezone
    payload = _macro_payload(composite_score=0.0, cftc_percentile=50.0)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "time": "2026-09-20T00:00:00.000Z"}]
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing,
        circuit_breaker=_open_circuit_breaker(), now=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    assert result == {"action": "simulation", "steps": [{"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}]}


def test_decide_and_act_swing_closes_when_cftc_extreme():
    from datetime import datetime, timezone
    payload = _macro_payload(composite_score=20.0, cftc_percentile=90.0)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "time": "2026-09-20T00:00:00.000Z"}]
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing,
        circuit_breaker=_open_circuit_breaker(), now=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    assert result == {"action": "simulation", "steps": [{"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}]}


def test_decide_and_act_swing_closes_when_max_holding_days_exceeded():
    from datetime import datetime, timezone
    payload = _macro_payload(composite_score=20.0, cftc_percentile=50.0)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "time": "2026-08-01T00:00:00.000Z"}]
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing,
        circuit_breaker=_open_circuit_breaker(), now=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    assert result == {"action": "simulation", "steps": [{"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}]}


def test_decide_and_act_swing_refuses_on_unexpected_position_type():
    payload = _macro_payload(composite_score=20.0, cftc_percentile=50.0)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_SELL", "time": "2026-09-20T00:00:00.000Z"}]
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing,
        circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "position de type inattendu (pas un achat), aucune action par prudence"}


def test_decide_and_act_swing_refuses_when_multiple_positions_open():
    payload = _macro_payload(composite_score=20.0, cftc_percentile=50.0)
    existing = [
        {"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "time": "2026-09-20T00:00:00.000Z"},
        {"id": "2", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "time": "2026-09-21T00:00:00.000Z"},
    ]
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing,
        circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "plusieurs positions ouvertes sur ce symbole, aucune action par prudence"}


def test_decide_and_act_swing_blocked_by_circuit_breaker():
    from datetime import datetime, timezone
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)  # référence de départ du jour (equity=10000)
    payload = _macro_payload(has_entry=True, ma200=4000.0)
    # equity à -11% depuis 10000 -> coupe-circuit déclenché.
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=8900, open_positions=[], circuit_breaker=cb,
    )
    assert result == {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}


def test_decide_and_act_swing_no_action_when_size_below_minimum():
    payload = _macro_payload(has_entry=True, ma200=4000.0)
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=1000.0, max_volume=5000.0, balance=10000, equity=10000, open_positions=[],
        circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}
```

- [ ] **Step 2 : Lancer les tests, vérifier l'échec**

Run: `pytest tests/gold_bot/test_bot.py -v -k decide_and_act_swing`
Expected: FAIL — `AttributeError: module 'gold_bot.bot' has no attribute 'decide_and_act_swing'`

- [ ] **Step 3 : Implémenter la fonction**

Dans `gold_bot/bot.py`, ajouter l'import en tête de fichier (après les imports existants) :

```python
import gold_bot.macro_signal as macro_signal
```

Puis ajouter, après `decide_and_act` (avant `reconcile_positions`) :

```python
def decide_and_act_swing(macro_payload: dict, candles: list[dict], *, contract_size: float,
                          balance: float, equity: float, volume_step: float, min_volume: float,
                          max_volume: float, open_positions: list[dict],
                          circuit_breaker: "risk.CircuitBreaker", symbol: str = "XAUUSD",
                          risk_pct: float = 0.05, now: datetime | None = None) -> dict:
    """Contrepartie swing (position tenue plusieurs jours, longue
    uniquement) de decide_and_act() -- voir
    docs/superpowers/specs/2026-09-29-gold-bot-swing-macro-design.md.
    Déclenchée par le signal macro de gold_score.py (`macro_payload`,
    voir gold_bot.macro_signal), pas par confluence.compute_signal.

    Différences volontaires par rapport à decide_and_act (scalping) :
    pas de garde news_blackout (une position swing est conçue pour
    traverser la volatilité court terme d'une publication macro -- la
    condition d'entrée de gold_score.py exclut déjà l'ouverture d'une
    nouvelle position dans les heures précédant un FOMC, mais rien
    n'impose de clôturer une position déjà ouverte à chaque
    CPI/NFP/FOMC) ; pas de retournement long/court (signal long
    uniquement) ; sortie pilotée par macro_signal.should_exit
    (dégradation du signal macro), pas par un signal neutre/opposé."""
    now = now or datetime.now(timezone.utc)
    circuit_breaker.check(equity)

    matching = [p for p in open_positions if p.get("symbol") == symbol]

    if len(matching) > 1:
        return {"action": "aucune", "reason": "plusieurs positions ouvertes sur ce symbole, aucune action par prudence"}

    if matching:
        position = matching[0]
        if position.get("type") != "POSITION_TYPE_BUY":
            return {"action": "aucune", "reason": "position de type inattendu (pas un achat), aucune action par prudence"}
        exit_now, reason = macro_signal.should_exit(macro_payload, position.get("time"), now)
        if exit_now:
            return {"action": "simulation", "steps": [
                {"type": "clôture_simulee", "position_id": position.get("id"), "symbol": symbol}
            ]}
        return {"action": "aucune", "reason": f"position swing ouverte, aucune condition de sortie ({reason or 'rien à signaler'})"}

    if not macro_signal.has_entry_alert(macro_payload):
        return {"action": "aucune", "reason": "pas de signal d'entrée macro"}

    if not circuit_breaker.can_open_position(equity):
        return {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}

    if not candles:
        return {"action": "aucune", "reason": "aucune bougie disponible pour le prix courant"}
    current_price = candles[-1]["close"]

    levels = macro_signal.compute_entry_levels(macro_payload, current_price)
    if levels is None:
        return {"action": "aucune", "reason": "niveaux d'entrée indisponibles (MM200 absente ou stop invalide)"}

    raw_size = risk.compute_position_size(balance, levels["entry"], levels["stop_loss"], contract_size, risk_pct=risk_pct)
    size = risk.round_to_volume_step(raw_size, volume_step, min_volume, max_volume)
    if size is None:
        return {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}

    return {"action": "simulation", "steps": [{
        "type": "ouverture_simulee", "symbol": symbol, "direction": "achat", "volume": size,
        "entry": levels["entry"], "stop_loss": levels["stop_loss"], "take_profit": levels["take_profit"],
    }]}
```

- [ ] **Step 4 : Lancer les tests, vérifier le succès**

Run: `pytest tests/gold_bot/test_bot.py -v -k decide_and_act_swing`
Expected: PASS (12 tests)

Puis lancer toute la suite du fichier pour s'assurer de n'avoir rien cassé :

Run: `pytest tests/gold_bot/test_bot.py -v`
Expected: PASS (tous les tests, anciens et nouveaux)

- [ ] **Step 5 : Commit**

```bash
git add gold_bot/bot.py tests/gold_bot/test_bot.py
git commit -m "feat(gold_bot): ajoute decide_and_act_swing, orchestration long-uniquement pilotée par le score macro"
```

---

## Task 4 : `gold_bot/loop.py` — bascule `run_cycle` sur le mode swing

**Files:**
- Modify: `gold_bot/loop.py` (import + corps de `run_cycle`)
- Modify: `tests/gold_bot/test_loop.py` (renomme les mocks `decide_and_act` -> `decide_and_act_swing`, ajoute le mock `macro_signal.fetch_macro_payload` partout où il manque, ajoute 2 nouveaux tests)

**Interfaces:**
- Consomme : `gold_bot.macro_signal.fetch_macro_payload` (Task 2), `gold_bot.bot.decide_and_act_swing` (Task 3).
- Produit : `run_cycle` appelle désormais `decide_and_act_swing` — aucun changement de signature publique de `run_cycle` elle-même.

- [ ] **Step 1 : Modifier `gold_bot/loop.py`**

Ajouter l'import, après `import gold_bot.confluence as confluence` :

```python
import gold_bot.macro_signal as macro_signal
```

Dans `run_cycle`, remplacer ce bloc (le corps du `try`, à partir de juste après la garde marché-fermé/données-périmées) :

```python
        account_info = _with_retry(lambda: broker.get_account_information(token, account_id, region))
        balance = account_info["balance"]
        equity = account_info["equity"]
        _save_cache({"balance": balance, "fetched_at": _now_iso()}, LATEST_BALANCE_PATH)
        open_positions = _with_retry(lambda: bot.reconcile_positions(token, account_id, region))
        _save_cache({"positions": open_positions, "fetched_at": _now_iso()}, LATEST_POSITIONS_PATH)
        spec = _with_retry(lambda: broker.get_symbol_specification(token, account_id, symbol, region))
        contract_size = spec["contractSize"]
        decision = bot.decide_and_act(
            candles, contract_size=contract_size, balance=balance, equity=equity,
            volume_step=spec["volumeStep"], min_volume=spec["minVolume"], max_volume=spec["maxVolume"],
            open_positions=open_positions, circuit_breaker=circuit_breaker, symbol=symbol,
            risk_pct=profile_params["risk_pct"],
        )
```

par :

```python
        macro_payload = _with_retry(lambda: macro_signal.fetch_macro_payload())

        account_info = _with_retry(lambda: broker.get_account_information(token, account_id, region))
        balance = account_info["balance"]
        equity = account_info["equity"]
        _save_cache({"balance": balance, "fetched_at": _now_iso()}, LATEST_BALANCE_PATH)
        open_positions = _with_retry(lambda: bot.reconcile_positions(token, account_id, region))
        _save_cache({"positions": open_positions, "fetched_at": _now_iso()}, LATEST_POSITIONS_PATH)
        spec = _with_retry(lambda: broker.get_symbol_specification(token, account_id, symbol, region))
        contract_size = spec["contractSize"]
        decision = bot.decide_and_act_swing(
            macro_payload, candles, contract_size=contract_size, balance=balance, equity=equity,
            volume_step=spec["volumeStep"], min_volume=spec["minVolume"], max_volume=spec["maxVolume"],
            open_positions=open_positions, circuit_breaker=circuit_breaker, symbol=symbol,
            risk_pct=profile_params["risk_pct"], now=now_dt,
        )
```

(Le reste de `run_cycle` — anti-slippage, `execute_steps`, journalisation — ne change pas.)

- [ ] **Step 2 : Lancer la suite de tests loop, constater les échecs attendus**

Run: `pytest tests/gold_bot/test_loop.py -v`
Expected: FAIL sur tous les tests `test_run_cycle_*` qui atteignent le corps du `try` après la garde marché-fermé/données-périmées — soit parce qu'ils mockent encore `loop.bot.decide_and_act` (jamais appelée, donc `decide_and_act_swing` réel s'exécute avec un `macro_payload` non mocké et tente un vrai appel réseau), soit parce que `macro_signal.fetch_macro_payload` n'est mocké nulle part. Les tests `test_execute_steps_*`, `test_log_decision_*`, `test_entry_price_has_drifted_*`, `test_with_retry_*`, `test_circuit_breaker_uses_separate_persist_path_from_kill_switch_state`, ainsi que les 4 tests qui ne dépassent jamais la garde marché-fermé/données-périmées/kill-switch (`test_run_cycle_no_action_when_kill_switch_engaged`, `test_run_cycle_ignores_when_market_closed`, `test_run_cycle_ignores_when_candle_data_is_stale`, `test_run_cycle_logs_error_when_data_fetch_fails`, `test_run_cycle_does_not_write_candles_cache_when_fetch_itself_fails`) restent verts sans modification.

- [ ] **Step 3 : Mettre à jour `tests/gold_bot/test_loop.py`**

Ajouter, juste après la définition de `_FRESH_CANDLE` (ligne 20) :

```python
_NEUTRAL_MACRO_PAYLOAD = {
    "composite_score": 0.0, "cftc_percentile": 50.0,
    "technical": {"ma200": 4000.0, "spot": 4000.0}, "alerts": [],
}
```

Puis, dans **chacun** des tests suivants, appliquer les deux changements ci-dessous :
1. Ajouter `monkeypatch.setattr(loop.macro_signal, "fetch_macro_payload", lambda *a, **k: _NEUTRAL_MACRO_PAYLOAD)` (juste après la ligne `monkeypatch.setattr(loop.confluence, "fetch_gold_candles", ...)` de ce test, ou en tout début de test s'il n'y a pas de mock `fetch_gold_candles` explicite — voir cas particulier plus bas).
2. Partout où le test contient `monkeypatch.setattr(loop.bot, "decide_and_act", ...)`, remplacer `"decide_and_act"` par `"decide_and_act_swing"` (la lambda/fonction de remplacement elle-même ne change pas — `decide_and_act_swing` a une signature différente mais ces mocks remplacent la fonction entière avec `lambda *a, **k: ...`, qui accepte n'importe quels arguments).

Liste exacte des tests concernés (tous dans `tests/gold_bot/test_loop.py`) :

- `test_run_cycle_logs_dry_run_without_executing` — les deux changements.
- `test_run_cycle_applies_active_risk_profile_to_sizing_and_circuit_breaker` — les deux changements.
- `test_run_cycle_defaults_to_profile_3_when_risk_profile_absent_from_state` — les deux changements.
- `test_run_cycle_executes_when_not_dry_run` — les deux changements.
- `test_run_cycle_skips_opening_when_price_has_drifted_too_much` — les deux changements (le mock macro payload s'ajoute avant la définition de `fake_fetch`/`monkeypatch.setattr(loop.confluence, "fetch_gold_candles", fake_fetch)`).
- `test_run_cycle_executes_opening_when_price_has_not_drifted` — les deux changements (même remarque).
- `test_run_cycle_skips_opening_when_slippage_recheck_fetch_fails` — les deux changements (même remarque).
- `test_run_cycle_still_closes_position_when_reopening_leg_has_drifted` — les deux changements (même remarque).
- `test_run_cycle_re_checks_kill_switch_before_executing` — les deux changements.
- `test_run_cycle_recovers_from_a_single_transient_network_blip` — les deux changements.
- `test_run_cycle_logs_error_when_decide_and_act_raises` — les deux changements (ne pas renommer le nom du test lui-même).
- `test_run_cycle_caches_candles_after_successful_fetch` — les deux changements.
- `test_run_cycle_caches_balance_after_successful_fetch` — les deux changements.
- `test_run_cycle_caches_positions_after_successful_fetch` — les deux changements.
- `test_run_cycle_picks_up_risk_profile_change_between_cycles` — les deux changements.
- `test_run_cycle_picks_up_risk_profile_change_via_api_between_cycles` — les deux changements.
- `test_run_cycle_treats_just_completed_candle_as_fresh` — les deux changements.
- `test_run_cycle_continues_when_market_open_and_data_fresh` — les deux changements.
- `test_run_cycle_continues_when_a_dashboard_cache_write_fails` — les deux changements.

Un seul test reçoit uniquement le changement 1 (mock macro payload), **sans** changement 2 (il ne mocke pas `decide_and_act` aujourd'hui car il échoue plus tôt, à `get_symbol_specification`) :

- `test_run_cycle_leaves_earlier_caches_intact_when_a_later_call_fails` — ajouter seulement `monkeypatch.setattr(loop.macro_signal, "fetch_macro_payload", lambda *a, **k: _NEUTRAL_MACRO_PAYLOAD)` juste après le mock `fetch_gold_candles` existant.

Exemple concret complet (avant/après) pour `test_run_cycle_executes_when_not_dry_run` :

Avant :
```python
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
```

Après :
```python
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.macro_signal, "fetch_macro_payload", lambda *a, **k: _NEUTRAL_MACRO_PAYLOAD)
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act_swing", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
```

Appliquer le même schéma à chacun des 18 autres tests listés ci-dessus, en respectant leur contenu existant par ailleurs (ne rien changer d'autre dans ces tests).

- [ ] **Step 4 : Ajouter deux nouveaux tests, à la fin du fichier**

```python
def test_run_cycle_logs_error_when_macro_payload_fetch_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)  # échec persistant -> _with_retry épuise ses tentatives
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(
        loop.macro_signal, "fetch_macro_payload",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("score.json indisponible")),
    )

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result["action"] == "erreur"
    assert "score.json indisponible" in result["reason"]


def test_run_cycle_passes_macro_payload_to_decide_and_act_swing(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_payload = {"composite_score": 20.0, "cftc_percentile": 40.0, "technical": {"ma200": 4000.0, "spot": 4010.0}, "alerts": [{"kind": "entree"}]}
    monkeypatch.setattr(loop.macro_signal, "fetch_macro_payload", lambda *a, **k: fake_payload)
    captured = {}

    def fake_decide_and_act_swing(macro_payload, candles, **kwargs):
        captured["macro_payload"] = macro_payload
        return {"action": "aucune", "reason": "pas de signal d'entrée macro"}

    monkeypatch.setattr(loop.bot, "decide_and_act_swing", fake_decide_and_act_swing)

    loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert captured["macro_payload"] == fake_payload
```

- [ ] **Step 5 : Lancer toute la suite `test_loop.py`, vérifier le succès**

Run: `pytest tests/gold_bot/test_loop.py -v`
Expected: PASS (tous les tests, anciens et nouveaux)

- [ ] **Step 6 : Lancer toute la suite `gold_bot`, vérifier qu'aucune régression n'a été introduite ailleurs**

Run: `pytest tests/gold_bot/ -v`
Expected: PASS (tous les fichiers, y compris `test_confluence.py`, `test_chart_patterns.py`, `test_session_breakout.py`, `test_api.py`, `test_risk.py`, `test_state.py`, `test_notify.py`, `test_broker.py`, `test_backtest.py`, inchangés par ce plan)

- [ ] **Step 7 : Commit**

```bash
git add gold_bot/loop.py tests/gold_bot/test_loop.py
git commit -m "feat(gold_bot): bascule run_cycle sur decide_and_act_swing, piloté par le signal macro"
```
