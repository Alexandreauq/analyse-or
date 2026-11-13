# Décision IA discrétionnaire sur l'entrée Fibonacci (FTMO) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer, uniquement pour `decide_and_act_fibonacci` (compte FTMO réel, XAUUSD), le veto binaire actuel de `ai_shadow.py` par une vraie décision directionnelle : un nouveau module décide achat/vente/rien sans connaître le sens que les règles auraient choisi, et module le TP/SL de l'entrée.

**Architecture:** Nouveau module `gold_bot/ai_discretion.py` (même patron fail-closed/cooldown/log que `gold_bot/ai_shadow.py`, mais bloquant par conception et jamais ancré sur le sens des règles). Branché dans `gold_bot/bot.py::decide_and_act_fibonacci` à la place de l'appel actuel à `ai_shadow.shadow_judge_entry(..., real_decision=True)`. `gold_bot/ftmo_guard.py` gagne une propriété publique `daily_floor` pour exposer le contexte de risque FTMO à l'IA. `decide_and_act_ichimoku` et son veto `ai_shadow` restent intacts.

**Tech Stack:** Python, `anthropic` SDK (`claude-opus-5-5`), pytest + `monkeypatch` (aucun appel réseau réel en test).

**Spec:** `docs/superpowers/specs/2026-11-13-gold-bot-ai-discretionary-fibonacci-entry-design.md`

## Global Constraints

- Modèle `claude-opus-5-5`, effort `"high"`, `web_search_20260209` (`max_uses=2`), `max_tokens=1536`.
- `tp_multiplicateur` borné à **[0.6, 2.0]**, `sl_multiplicateur` borné à **[0.7, 1.3]**.
- R:R final (après bornage) doit être **≥ 1.0**, sinon le trade est rejeté (`"rejete_garde_fou_rr"`), aucun ajustement automatique supplémentaire.
- Cooldown **4 heures** par `(symbol, level arrondi à 2 décimales)` — anti-rafale, pas anti-coût.
- Le sens (`direction`/`bias`) et les prix absolus `stop_loss`/`take_profit` que les règles auraient choisis ne sont **jamais** transmis à l'IA, sous aucune forme, dans le prompt.
- Toute défaillance de l'appel IA ⇒ **aucun trade ce cycle** (fail-closed), jamais de repli sur le sens des règles, jamais d'exception propagée hors du module.
- Réflexe "confiance faible → taille divisée par deux" conservé, en lisant `ai_shadow.LOW_CONFIDENCE_SIZE_FACTOR` (pas dupliqué).
- `decide_and_act_ichimoku` et son appel à `ai_shadow.shadow_judge_entry` : **aucune modification**.

---

### Task 1: Fonctions pures de `gold_bot/ai_discretion.py` — bornage, rejet R:R, parsing, prompt

**Files:**
- Create: `gold_bot/ai_discretion.py`
- Test: `tests/gold_bot/test_ai_discretion.py`

**Interfaces:**
- Consumes: rien (fonctions pures, pas de dépendance aux tâches suivantes).
- Produces : `clamp_multipliers(tp_multiplicateur: float, sl_multiplicateur: float) -> tuple[float, float]`, `resolve_risk_reward(baseline_take_profit_distance: float, baseline_stop_loss_distance: float, tp_multiplicateur, sl_multiplicateur) -> dict` (clés `take_profit_distance`, `stop_loss_distance`, `risk_reward`, `accepted`), `_parse_discretion_verdict(text: str) -> dict | None`, `_describe_candles(candles, limit=MAX_CANDLES_IN_PROMPT) -> str`, `_build_prompt(symbol, *, level, close, high, low, atr, trend_strength, trend_strength_short, hour_utc, candles, baseline_stop_loss_distance, baseline_take_profit_distance, ftmo_buffer) -> str` — **aucun paramètre `direction`/`bias`, par construction**. Constantes `MODEL`, `MAX_CANDLES_IN_PROMPT`, `REQUEST_TIMEOUT_SECONDS`, `WEB_SEARCH_MAX_USES`, `MAX_TOKENS`, `DISCRETION_COOLDOWN`, `TP_MULTIPLIER_BOUNDS`, `SL_MULTIPLIER_BOUNDS`, `MIN_RISK_REWARD`, `DISCRETION_LOG_PATH`. La Tâche 2 consomme toutes ces fonctions/constantes.

- [ ] **Step 1: Écrire les fichiers avec les tests ET l'implémentation (fonctions pures, pas de TDD rouge/vert séquentiel utile ici — voir Step 2 pour la vérification)**

Créer `gold_bot/ai_discretion.py` :

```python
# gold_bot/ai_discretion.py
# Decision IA discretionnaire sur l'entree Fibonacci (compte FTMO reel)
# -- voir docs/superpowers/specs/2026-11-13-gold-bot-ai-discretionary-
# fibonacci-entry-design.md. Remplace le veto binaire de ai_shadow.py
# (qui ne peut qu'accepter/bloquer le sens deja choisi par les regles) :
# ici l'IA choisit elle-meme achat/vente/rien, SANS JAMAIS recevoir le
# sens que les regles auraient choisi (voir _build_prompt, qui n'a
# aucun parametre direction/bias) -- c'est ce qui aurait pu empecher
# l'incident du 2026-11-06 (vente a contre-tendance dans un marche en V).
# decide_and_act_ichimoku garde son propre veto ai_shadow, inchange :
# ce module ne concerne que decide_and_act_fibonacci.
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

DISCRETION_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_discretion_log.jsonl")
MODEL = "claude-opus-5-5"
MAX_CANDLES_IN_PROMPT = 12
REQUEST_TIMEOUT_SECONDS = 40.0
WEB_SEARCH_MAX_USES = 2
MAX_TOKENS = 1536
# Anti-rafale (eviter un enchainement rapide de decisions si le marche
# touche plusieurs niveaux coup sur coup) -- PAS un garde-fou de cout
# comme les 24h de ai_shadow.SHADOW_COOLDOWN : le declencheur Fibonacci
# est deja naturellement rare (~4-10x/semaine).
DISCRETION_COOLDOWN = timedelta(hours=4)

# Bornes dures, appliquees apres coup, independantes de ce que dit l'IA
# -- voir la spec, section "Garde-fous".
TP_MULTIPLIER_BOUNDS = (0.6, 2.0)
SL_MULTIPLIER_BOUNDS = (0.7, 1.3)
MIN_RISK_REWARD = 1.0

_VALID_ACTIONS = {"achat", "vente", "rien"}
_ACTION_JSON_RE = re.compile(r"\{[^{}]*\"action\"[^{}]*\}")

_last_call_at: dict[tuple, datetime] = {}
_cooldown_lock = threading.Lock()


def clamp_multipliers(tp_multiplicateur: float, sl_multiplicateur: float) -> tuple[float, float]:
    """Borne les deux multiplicateurs independamment a leurs bornes
    respectives -- TP_MULTIPLIER_BOUNDS/SL_MULTIPLIER_BOUNDS."""
    tp = min(max(tp_multiplicateur, TP_MULTIPLIER_BOUNDS[0]), TP_MULTIPLIER_BOUNDS[1])
    sl = min(max(sl_multiplicateur, SL_MULTIPLIER_BOUNDS[0]), SL_MULTIPLIER_BOUNDS[1])
    return tp, sl


def resolve_risk_reward(baseline_take_profit_distance: float, baseline_stop_loss_distance: float,
                         tp_multiplicateur: float, sl_multiplicateur: float) -> dict:
    """Applique les multiplicateurs (bornes via clamp_multipliers) aux
    distances de reference des regles, calcule le R:R resultant. Renvoie
    toujours un dict complet, meme quand "accepted" est False -- la
    distance ajustee reste utile pour le journal meme quand le trade est
    rejete (comparaison a posteriori)."""
    tp_mult, sl_mult = clamp_multipliers(tp_multiplicateur, sl_multiplicateur)
    take_profit_distance = baseline_take_profit_distance * tp_mult
    stop_loss_distance = baseline_stop_loss_distance * sl_mult
    risk_reward = take_profit_distance / stop_loss_distance if stop_loss_distance > 0 else 0.0
    return {
        "take_profit_distance": take_profit_distance,
        "stop_loss_distance": stop_loss_distance,
        "risk_reward": risk_reward,
        "accepted": risk_reward >= MIN_RISK_REWARD,
    }


def _parse_discretion_verdict(text: str) -> dict | None:
    """Meme convention que ai_shadow._parse_verdict (regex sur le
    dernier bloc JSON contenant la cle attendue, normalisation
    case/espaces) -- mais la cle pivot est "action", pas "verdict", et
    un "achat"/"vente" sans tp_multiplicateur/sl_multiplicateur
    numerique (ou avec un booleen, piege Python ou bool est une
    sous-classe de int) est traite comme invalide : on ne devine jamais
    une valeur manquante."""
    matches = _ACTION_JSON_RE.findall(text)
    if not matches:
        return None
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    for key in ("action", "confiance"):
        value = parsed.get(key)
        if isinstance(value, str):
            parsed[key] = value.strip().lower()
    action = parsed.get("action")
    if action not in _VALID_ACTIONS:
        return None
    if action in ("achat", "vente"):
        for mult_key in ("tp_multiplicateur", "sl_multiplicateur"):
            value = parsed.get(mult_key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
    return parsed


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


def _format_buffer_pct(value: float | None) -> str:
    return f"{value * 100:.2f}%" if value is not None else "inconnu"


def _build_prompt(symbol: str, *, level: float, close: float, high: float, low: float, atr: float,
                   trend_strength: float | None, trend_strength_short: float | None, hour_utc: int,
                   candles: list[dict], baseline_stop_loss_distance: float,
                   baseline_take_profit_distance: float, ftmo_buffer: dict) -> str:
    """AUCUN parametre direction/bias -- garantie structurelle que le
    sens que les regles auraient choisi ne peut pas fuiter dans le
    prompt (voir test_build_prompt_signature_has_no_direction_or_bias_
    parameter). Les distances TP/SL de reference sont signees sans
    direction (des valeurs absolues de distance), donc ne fuitent rien
    non plus."""
    trend_line = (
        f"force de tendance glissante 20 jours={trend_strength}, " if trend_strength is not None else ""
    )
    trend_short_line = (
        f"force de tendance glissante 5 jours={trend_strength_short}, "
        if trend_strength_short is not None else ""
    )
    return (
        f"Un moteur de trading mecanique sur l'or ({symbol}) vient de detecter qu'un "
        f"niveau de retracement de Fibonacci a ete touche et rejete. Contrairement a "
        f"d'habitude, ce n'est PAS un signal avec un sens deja determine : c'est a TOI "
        f"de decider s'il faut acheter, vendre, ou ne rien faire du tout, uniquement a "
        f"partir du contexte brut ci-dessous.\n\n"
        f"Contexte chiffre : niveau={level}, cloture={close}, plus haut={high}, "
        f"plus bas={low}, ATR={atr}, {trend_line}{trend_short_line}heure UTC={hour_utc}.\n\n"
        f"Dernieres bougies 5min (plus recente en dernier) :\n{_describe_candles(candles)}\n\n"
        f"Marge restante avant les planchers de perte FTMO : "
        f"{_format_buffer_pct(ftmo_buffer.get('daily_pct_remaining'))} (journalier), "
        f"{_format_buffer_pct(ftmo_buffer.get('overall_pct_remaining'))} (global). "
        f"Plus cette marge est faible, plus la prudence doit l'etre.\n\n"
        f"Un trade mecanique prendrait un stop a une distance de reference de "
        f"{baseline_stop_loss_distance:.2f} et un objectif a {baseline_take_profit_distance:.2f} "
        f"(sans indication de sens). Tu peux ajuster ces distances via des multiplicateurs "
        f"si le contexte le justifie (volatilite, actualite).\n\n"
        f"Cherche s'il y a une actualite majeure en cours ou tres recente qui pourrait "
        f"expliquer ce mouvement de prix (decision de banque centrale, donnee macro, "
        f"evenement geopolitique).\n\n"
        f"Ceci est une VRAIE decision : le trade qui sera reellement ouvert est celui que "
        f"TU choisis. Si tu ne vois rien d'exploitable, choisis \"rien\" sans etat d'ame.\n\n"
        f"Termine ta reponse par un bloc JSON seul sur sa derniere ligne, exactement sous "
        f'cette forme : {{"action": "achat ou vente ou rien", "confiance": "faible ou '
        f'moyenne ou haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, '
        f'"raison": "une phrase"}}'
    )
```

Créer `tests/gold_bot/test_ai_discretion.py` :

```python
import inspect

import pytest

import gold_bot.ai_discretion as ai_discretion


def test_clamp_multipliers_passes_through_values_within_bounds():
    assert ai_discretion.clamp_multipliers(1.2, 1.1) == (1.2, 1.1)


def test_clamp_multipliers_clamps_tp_above_upper_bound():
    assert ai_discretion.clamp_multipliers(3.0, 1.0) == (2.0, 1.0)


def test_clamp_multipliers_clamps_tp_below_lower_bound():
    assert ai_discretion.clamp_multipliers(0.1, 1.0) == (0.6, 1.0)


def test_clamp_multipliers_clamps_sl_above_upper_bound():
    assert ai_discretion.clamp_multipliers(1.0, 5.0) == (1.0, 1.3)


def test_clamp_multipliers_clamps_sl_below_lower_bound():
    assert ai_discretion.clamp_multipliers(1.0, 0.01) == (1.0, 0.7)


def test_resolve_risk_reward_accepts_at_exact_floor():
    result = ai_discretion.resolve_risk_reward(10.0, 10.0, 1.0, 1.0)
    assert result["take_profit_distance"] == 10.0
    assert result["stop_loss_distance"] == 10.0
    assert result["risk_reward"] == 1.0
    assert result["accepted"] is True


def test_resolve_risk_reward_rejects_when_rr_below_floor_after_clamping():
    # tp_multiplicateur=0.6 et sl_multiplicateur=1.3 sont deja dans les
    # bornes (pas d'effet du bornage lui-meme) : take_profit_distance =
    # 10*0.6=6.0, stop_loss_distance = 20*1.3=26.0, rr=6/26=0.2308 < 1.0.
    result = ai_discretion.resolve_risk_reward(10.0, 20.0, 0.6, 1.3)
    assert result["take_profit_distance"] == 6.0
    assert result["stop_loss_distance"] == 26.0
    assert result["risk_reward"] == pytest.approx(6.0 / 26.0)
    assert result["accepted"] is False


def test_resolve_risk_reward_rejection_uses_clamped_values_not_raw_ones():
    # tp_multiplicateur=10.0 (hors bornes, sera clampe a 2.0) et
    # sl_multiplicateur=0.01 (hors bornes, clampe a 0.7) : sans bornage,
    # le R:R brut serait enorme et accepte a tort -- avec bornage,
    # take_profit_distance=10*2.0=20.0, stop_loss_distance=10*0.7=7.0,
    # rr=20/7=2.857 >= 1.0 -> accepte quand meme ici (cas positif pour
    # verifier que le bornage s'applique AVANT le calcul du ratio, pas
    # apres).
    result = ai_discretion.resolve_risk_reward(10.0, 10.0, 10.0, 0.01)
    assert result["take_profit_distance"] == 20.0
    assert result["stop_loss_distance"] == 7.0
    assert result["accepted"] is True


def test_parse_discretion_verdict_valid_achat():
    text = 'blabla\n{"action": "achat", "confiance": "haute", "tp_multiplicateur": 1.2, "sl_multiplicateur": 0.9, "raison": "x"}'
    result = ai_discretion._parse_discretion_verdict(text)
    assert result == {"action": "achat", "confiance": "haute", "tp_multiplicateur": 1.2,
                       "sl_multiplicateur": 0.9, "raison": "x"}


def test_parse_discretion_verdict_valid_vente():
    text = '{"action": "vente", "confiance": "faible", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "y"}'
    result = ai_discretion._parse_discretion_verdict(text)
    assert result["action"] == "vente"


def test_parse_discretion_verdict_valid_rien_without_multipliers():
    text = '{"action": "rien", "confiance": "faible", "raison": "rien de clair"}'
    result = ai_discretion._parse_discretion_verdict(text)
    assert result == {"action": "rien", "confiance": "faible", "raison": "rien de clair"}


def test_parse_discretion_verdict_rejects_invalid_action_value():
    text = '{"action": "hold", "confiance": "haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "x"}'
    assert ai_discretion._parse_discretion_verdict(text) is None


def test_parse_discretion_verdict_rejects_achat_missing_multipliers():
    text = '{"action": "achat", "confiance": "haute", "raison": "x"}'
    assert ai_discretion._parse_discretion_verdict(text) is None


def test_parse_discretion_verdict_rejects_achat_with_boolean_multiplier():
    text = '{"action": "achat", "confiance": "haute", "tp_multiplicateur": true, "sl_multiplicateur": 1.0, "raison": "x"}'
    assert ai_discretion._parse_discretion_verdict(text) is None


def test_parse_discretion_verdict_rejects_when_no_json_block_found():
    assert ai_discretion._parse_discretion_verdict("reponse sans bloc JSON") is None


def test_parse_discretion_verdict_normalizes_case_and_whitespace():
    text = '{"action": " ACHAT ", "confiance": " Haute ", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "x"}'
    result = ai_discretion._parse_discretion_verdict(text)
    assert result["action"] == "achat"
    assert result["confiance"] == "haute"


def _prompt_kwargs(**overrides):
    base = dict(
        level=2650.5, close=2654.0, high=2655.0, low=2649.0, atr=1.2,
        trend_strength=0.03, trend_strength_short=0.01, hour_utc=12,
        candles=[{"time": "2026-10-28 12:00:00", "open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0}],
        baseline_stop_loss_distance=11.0, baseline_take_profit_distance=16.5,
        ftmo_buffer={"daily_pct_remaining": 0.021, "overall_pct_remaining": 0.045},
    )
    base.update(overrides)
    return base


def test_build_prompt_signature_has_no_direction_or_bias_parameter():
    params = inspect.signature(ai_discretion._build_prompt).parameters
    assert "direction" not in params
    assert "bias" not in params


def test_build_prompt_includes_ftmo_buffer_values():
    prompt = ai_discretion._build_prompt("XAUUSD", **_prompt_kwargs())
    assert "2.10%" in prompt
    assert "4.50%" in prompt


def test_build_prompt_renders_unknown_when_buffer_values_are_none():
    prompt = ai_discretion._build_prompt(
        "XAUUSD", **_prompt_kwargs(ftmo_buffer={"daily_pct_remaining": None, "overall_pct_remaining": None}))
    assert "inconnu" in prompt


def test_build_prompt_includes_baseline_distances():
    prompt = ai_discretion._build_prompt("XAUUSD", **_prompt_kwargs())
    assert "11.00" in prompt
    assert "16.50" in prompt


def test_build_prompt_omits_trend_lines_when_none():
    prompt = ai_discretion._build_prompt(
        "XAUUSD", **_prompt_kwargs(trend_strength=None, trend_strength_short=None))
    assert "force de tendance" not in prompt
```

- [ ] **Step 2: Lancer les tests et vérifier qu'ils passent tous**

Run: `pytest tests/gold_bot/test_ai_discretion.py -v`
Expected: tous les tests PASS (18 tests).

- [ ] **Step 3: Commit**

```bash
git add gold_bot/ai_discretion.py tests/gold_bot/test_ai_discretion.py
git commit -m "feat(gold-bot): fonctions pures ai_discretion -- bornage TP/SL, rejet R:R, parsing, prompt sans ancrage"
```

---

### Task 2: `judge_fibonacci_entry` — appel réseau, cooldown, fail-closed, journalisation

**Files:**
- Modify: `gold_bot/ai_discretion.py` (ajoute `judge_fibonacci_entry` et `_append_log`)
- Modify: `tests/gold_bot/test_ai_discretion.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes : `_build_prompt`, `_parse_discretion_verdict`, `resolve_risk_reward`, `DISCRETION_LOG_PATH`, `DISCRETION_COOLDOWN`, `MODEL`, `MAX_TOKENS`, `WEB_SEARCH_MAX_USES`, `REQUEST_TIMEOUT_SECONDS` (Tâche 1).
- Produces : `judge_fibonacci_entry(symbol: str, state: dict, candles: list[dict], *, baseline_stop_loss_distance: float, baseline_take_profit_distance: float, ftmo_buffer: dict, log_path: str = DISCRETION_LOG_PATH, now: datetime | None = None) -> dict | None`. Renvoie un dict `{"action", "confiance", "stop_loss_distance", "take_profit_distance", "raison"}` **uniquement** quand un trade doit réellement s'ouvrir ; `None` dans tous les autres cas (échec API, verdict invalide, action `"rien"`, rejet garde-fou R:R, cooldown actif). La Tâche 4 consomme cette fonction telle quelle.

- [ ] **Step 1: Ajouter le fixture de reset de cooldown et les tests (ils doivent échouer, `judge_fibonacci_entry` n'existe pas encore)**

Ajouter en tête de `tests/gold_bot/test_ai_discretion.py` (avant les imports déjà présents, en les complétant) :

```python
import json
from datetime import datetime, timedelta, timezone

import anthropic
import pytest

import gold_bot.ai_discretion as ai_discretion
import gold_bot.ftmo_guard as ftmo_guard


@pytest.fixture(autouse=True)
def _reset_discretion_cooldown():
    ai_discretion._last_call_at.clear()
    yield
    ai_discretion._last_call_at.clear()


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
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        return self


def _state(**overrides):
    base = {"level": 2650.5, "close": 2654.0, "high": 2655.0, "low": 2649.0, "atr": 1.2,
            "trend_strength": 0.03, "trend_strength_short": 0.01, "hour_utc": 12, "bias": "achat"}
    base.update(overrides)
    return base


def _candles():
    return [
        {"time": "2026-10-28 12:00:00", "open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0},
        {"time": "2026-10-28 12:05:00", "open": 2651.0, "high": 2655.0, "low": 2650.0, "close": 2654.0},
    ]


def _buffer():
    return {"daily_pct_remaining": 0.02, "overall_pct_remaining": 0.05}
```

(Garder les fonctions déjà écrites à la Tâche 1 — `_prompt_kwargs`, les tests `test_clamp_*`/`test_resolve_*`/`test_parse_*`/`test_build_prompt_*` — inchangées, après ces ajouts.)

Ajouter à `tests/gold_bot/test_ai_discretion.py` :

```python
def test_judge_fibonacci_entry_returns_verdict_on_successful_achat_call(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "achat", "confiance": "haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "rejet net"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )

    assert result == {"action": "achat", "confiance": "haute", "stop_loss_distance": 10.0,
                       "take_profit_distance": 15.0, "raison": "rejet net"}
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["action_reelle"] == "execute"


def test_judge_fibonacci_entry_returns_verdict_on_successful_vente_call(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "vente", "confiance": "moyenne", "tp_multiplicateur": 1.2, "sl_multiplicateur": 0.8, "raison": "x"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result["action"] == "vente"
    assert result["take_profit_distance"] == 18.0  # 15.0 * 1.2
    assert result["stop_loss_distance"] == 8.0  # 10.0 * 0.8


def test_judge_fibonacci_entry_returns_none_when_action_is_rien(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "rien", "confiance": "faible", "raison": "rien exploitable"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["action_reelle"] == "aucune_ia"


def test_judge_fibonacci_entry_returns_none_and_logs_when_rr_guardrail_rejects(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    # baseline tp=10, sl=20 ; multiplicateurs 0.6/1.3 (deja dans les bornes) ->
    # tp_distance=6.0, sl_distance=26.0, rr=0.23 < 1.0 -> rejete.
    response_text = '{"action": "achat", "confiance": "haute", "tp_multiplicateur": 0.6, "sl_multiplicateur": 1.3, "raison": "x"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=20.0, baseline_take_profit_distance=10.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["action_reelle"] == "rejete_garde_fou_rr"


def test_judge_fibonacci_entry_returns_none_when_api_key_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["error"] == "ANTHROPIC_API_KEY absent"
    assert entries[0]["action_reelle"] == "erreur"


def test_judge_fibonacci_entry_returns_none_and_logs_on_client_exception(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    fake_client = _FakeAnthropicClient(error=RuntimeError("boom reseau"))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["error"] == "RuntimeError: boom reseau"
    assert entries[0]["action_reelle"] == "erreur"


def test_judge_fibonacci_entry_returns_none_when_verdict_unparseable(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock("reponse sans bloc JSON")]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["error"] == "verdict JSON non trouve ou invalide dans la reponse"


def test_judge_fibonacci_entry_uses_max_retries_zero_and_expected_request_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "achat", "confiance": "haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "x"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert fake_client.max_retries == 0
    assert fake_client.timeout == ai_discretion.REQUEST_TIMEOUT_SECONDS
    kwargs = fake_client.messages.last_kwargs
    assert kwargs["model"] == ai_discretion.MODEL
    assert kwargs["max_tokens"] == ai_discretion.MAX_TOKENS
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["tools"] == [{"type": "web_search_20260209", "name": "web_search",
                                 "max_uses": ai_discretion.WEB_SEARCH_MAX_USES}]


def test_judge_fibonacci_entry_respects_cooldown_for_same_symbol_and_level(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "achat", "confiance": "haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "x"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=timezone.utc)

    ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path, now=t0,
    )
    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path, now=t0 + timedelta(hours=1),
    )
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 1


def test_judge_fibonacci_entry_does_not_share_cooldown_across_different_levels(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "achat", "confiance": "haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "x"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=timezone.utc)

    ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(level=2650.5), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path, now=t0,
    )
    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(level=2700.0), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path, now=t0 + timedelta(minutes=1),
    )
    assert result is not None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 2


def test_judge_fibonacci_entry_logs_regle_contrefactuelle_for_comparison(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "vente", "confiance": "haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "x"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(bias="achat"), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    # L'IA a choisi "vente" alors que la regle (bias) aurait choisi
    # "achat" -- le journal garde les deux pour comparaison a posteriori,
    # la vraie decision executee est bien celle de l'IA (verifiee par
    # un autre test, ce champ n'affecte jamais ce qui est journalise ici).
    assert entries[0]["regle_contrefactuelle"]["direction"] == "achat"
    assert entries[0]["verdict"]["action"] == "vente"


def test_judge_fibonacci_entry_never_raises_on_unexpected_exception(monkeypatch, tmp_path):
    log_path = str(tmp_path / "ai_discretion_log.jsonl")

    def _boom(*args, **kwargs):
        raise ValueError("inattendu")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setattr(ai_discretion, "_build_prompt", _boom)
    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result is None
```

- [ ] **Step 2: Lancer les tests et vérifier qu'ils échouent** (`judge_fibonacci_entry` n'existe pas)

Run: `pytest tests/gold_bot/test_ai_discretion.py -v`
Expected: FAIL avec `AttributeError: module 'gold_bot.ai_discretion' has no attribute 'judge_fibonacci_entry'`.

- [ ] **Step 3: Implémenter `judge_fibonacci_entry` et `_append_log`**

Ajouter à `gold_bot/ai_discretion.py` (à la suite de `_build_prompt`) :

```python
def _append_log(entry: dict, path: str = DISCRETION_LOG_PATH) -> None:
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def judge_fibonacci_entry(
    symbol: str, state: dict, candles: list[dict], *,
    baseline_stop_loss_distance: float, baseline_take_profit_distance: float,
    ftmo_buffer: dict, log_path: str = DISCRETION_LOG_PATH, now: datetime | None = None,
) -> dict | None:
    """Decide elle-meme le sens d'entree (achat/vente/rien) sur un
    signal Fibonacci deja confirme par les regles -- SANS jamais
    recevoir le sens que les regles auraient choisi (voir _build_prompt,
    aucun parametre direction/bias). Renvoie un dict {"action",
    "confiance", "stop_loss_distance", "take_profit_distance", "raison"}
    UNIQUEMENT quand un trade doit reellement s'ouvrir (appel reussi,
    verdict valide, action != "rien", garde-fou R:R accepte) ; None dans
    tous les autres cas (echec API, verdict invalide, action "rien",
    garde-fou R:R rejete, cooldown actif) -- l'appelant traite toujours
    None comme "aucun trade ce cycle", sans jamais se rabattre sur le
    sens des regles. Jamais d'exception propagee."""
    now = now or datetime.now(timezone.utc)
    level = state.get("level")
    cooldown_key = (symbol, round(level, 2) if level is not None else None)
    with _cooldown_lock:
        last = _last_call_at.get(cooldown_key)
        if last is not None and now - last < DISCRETION_COOLDOWN:
            return None
        _last_call_at[cooldown_key] = now

    entry: dict = {
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": symbol,
        "state": state,
        "ftmo_buffer": ftmo_buffer,
        "regle_contrefactuelle": {
            "direction": state.get("bias"),
            "stop_loss_distance": baseline_stop_loss_distance,
            "take_profit_distance": baseline_take_profit_distance,
        },
    }
    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            entry["error"] = "ANTHROPIC_API_KEY absent"
            entry["action_reelle"] = "erreur"
            _append_log(entry, log_path)
            return None

        client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            output_config={"effort": "high"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES}],
            messages=[{"role": "user", "content": _build_prompt(
                symbol,
                level=state.get("level"), close=state.get("close"), high=state.get("high"),
                low=state.get("low"), atr=state.get("atr"),
                trend_strength=state.get("trend_strength"), trend_strength_short=state.get("trend_strength_short"),
                hour_utc=state.get("hour_utc"), candles=candles,
                baseline_stop_loss_distance=baseline_stop_loss_distance,
                baseline_take_profit_distance=baseline_take_profit_distance, ftmo_buffer=ftmo_buffer,
            )}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        verdict = _parse_discretion_verdict(text)
        entry["raw_text"] = text
        entry["verdict"] = verdict
        if verdict is None:
            entry["error"] = "verdict JSON non trouve ou invalide dans la reponse"
            entry["action_reelle"] = "erreur"
            _append_log(entry, log_path)
            return None

        if verdict["action"] == "rien":
            entry["action_reelle"] = "aucune_ia"
            _append_log(entry, log_path)
            return None

        resolved = resolve_risk_reward(
            baseline_take_profit_distance, baseline_stop_loss_distance,
            verdict.get("tp_multiplicateur"), verdict.get("sl_multiplicateur"),
        )
        entry["resolved"] = resolved
        if not resolved["accepted"]:
            entry["action_reelle"] = "rejete_garde_fou_rr"
            _append_log(entry, log_path)
            return None

        entry["action_reelle"] = "execute"
        _append_log(entry, log_path)
        return {
            "action": verdict["action"],
            "confiance": verdict.get("confiance"),
            "stop_loss_distance": resolved["stop_loss_distance"],
            "take_profit_distance": resolved["take_profit_distance"],
            "raison": verdict.get("raison"),
        }
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {e}"
        entry["action_reelle"] = "erreur"
        _append_log(entry, log_path)
        return None
```

Ajouter `gold_bot/ai_discretion_log.jsonl` à `.gitignore`, juste après la ligne `gold_bot/ai_shadow_log.jsonl`.

- [ ] **Step 4: Lancer les tests et vérifier qu'ils passent**

Run: `pytest tests/gold_bot/test_ai_discretion.py -v`
Expected: PASS (31 tests au total avec la Tâche 1).

- [ ] **Step 5: Commit**

```bash
git add gold_bot/ai_discretion.py tests/gold_bot/test_ai_discretion.py .gitignore
git commit -m "feat(gold-bot): judge_fibonacci_entry -- appel reel, cooldown 4h, fail-closed, journal"
```

---

### Task 3: `FtmoGuard.daily_floor` + `compute_ftmo_buffer`

**Files:**
- Modify: `gold_bot/ftmo_guard.py:158-164`
- Modify: `gold_bot/ai_discretion.py`
- Modify: `tests/gold_bot/test_ftmo_guard.py`
- Modify: `tests/gold_bot/test_ai_discretion.py`

**Interfaces:**
- Consumes : `FtmoGuard.overall_floor` (propriété publique existante), `FtmoGuard.isc` (attribut public existant).
- Produces : `FtmoGuard.daily_floor` (nouvelle propriété publique, `float | None`), `ai_discretion.compute_ftmo_buffer(ftmo_guard_instance, equity: float) -> dict` (clés `daily_pct_remaining`, `overall_pct_remaining`, chacune `float | None`). La Tâche 4 consomme les deux.

- [ ] **Step 1: Écrire les tests pour `FtmoGuard.daily_floor` (doivent échouer)**

Ajouter à `tests/gold_bot/test_ftmo_guard.py` (vérifier en tête du fichier que `datetime`, `timezone`, `pytest` et `ftmo_guard` sont déjà importés — sinon les ajouter, même convention que le reste du fichier) :

```python
def test_daily_floor_is_none_before_first_check():
    guard = ftmo_guard.FtmoGuard(isc=10000)
    assert guard.daily_floor is None


def test_daily_floor_returns_value_after_check():
    guard = ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc))
    guard.check(balance=10000, equity=10000)
    assert guard.daily_floor == pytest.approx(9600.0)
```

- [ ] **Step 2: Lancer les tests et vérifier qu'ils échouent**

Run: `pytest tests/gold_bot/test_ftmo_guard.py -v -k daily_floor`
Expected: FAIL avec `AttributeError: 'FtmoGuard' object has no attribute 'daily_floor'`.

- [ ] **Step 3: Ajouter la propriété**

Dans `gold_bot/ftmo_guard.py`, juste après la propriété `target_equity` (ligne 164) :

```python
    @property
    def daily_floor(self) -> float | None:
        return self._daily_floor
```

- [ ] **Step 4: Lancer les tests et vérifier qu'ils passent**

Run: `pytest tests/gold_bot/test_ftmo_guard.py -v -k daily_floor`
Expected: PASS.

- [ ] **Step 5: Écrire les tests pour `compute_ftmo_buffer` (doivent échouer)**

Ajouter à `tests/gold_bot/test_ai_discretion.py` (`import gold_bot.ftmo_guard as ftmo_guard` déjà ajouté à la Tâche 2, Step 1) :

```python
def _guarded(equity_at_check=10000.0, balance_at_check=10000.0):
    guard = ftmo_guard.FtmoGuard(isc=10000.0, now_fn=lambda: datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc))
    guard.check(balance=balance_at_check, equity=equity_at_check)
    return guard


def test_compute_ftmo_buffer_returns_fractions_of_isc():
    guard = _guarded()  # daily_floor=9600.0, overall_floor=9100.0
    result = ai_discretion.compute_ftmo_buffer(guard, equity=9800.0)
    assert result == {"daily_pct_remaining": pytest.approx(0.02), "overall_pct_remaining": pytest.approx(0.07)}


def test_compute_ftmo_buffer_clamps_at_zero_when_equity_below_daily_floor():
    guard = _guarded()  # daily_floor=9600.0, overall_floor=9100.0
    result = ai_discretion.compute_ftmo_buffer(guard, equity=9500.0)
    assert result["daily_pct_remaining"] == 0.0
    assert result["overall_pct_remaining"] == pytest.approx(0.04)


def test_compute_ftmo_buffer_clamps_at_zero_when_equity_below_overall_floor():
    guard = _guarded()
    result = ai_discretion.compute_ftmo_buffer(guard, equity=9000.0)
    assert result == {"daily_pct_remaining": 0.0, "overall_pct_remaining": 0.0}


def test_compute_ftmo_buffer_daily_remaining_is_none_when_daily_floor_not_yet_set():
    guard = ftmo_guard.FtmoGuard(isc=10000.0)  # check() jamais appele
    result = ai_discretion.compute_ftmo_buffer(guard, equity=9800.0)
    assert result["daily_pct_remaining"] is None
    assert result["overall_pct_remaining"] == pytest.approx(0.07)
```

- [ ] **Step 6: Lancer les tests et vérifier qu'ils échouent**

Run: `pytest tests/gold_bot/test_ai_discretion.py -v -k compute_ftmo_buffer`
Expected: FAIL avec `AttributeError: module 'gold_bot.ai_discretion' has no attribute 'compute_ftmo_buffer'`.

- [ ] **Step 7: Implémenter `compute_ftmo_buffer`**

Ajouter à `gold_bot/ai_discretion.py` (après `judge_fibonacci_entry`) :

```python
def compute_ftmo_buffer(ftmo_guard_instance, equity: float) -> dict:
    """Marge restante avant les deux planchers FTMO, exprimee en
    fraction de l'ISC (meme base que daily_loss_pct/overall_loss_pct du
    FtmoGuard) -- jamais negative (si deja franchi, le coupe-circuit de
    loop.py a deja arrete le cycle avant d'atteindre ce calcul ; le
    clamp est une defense supplementaire, pas un cas reel attendu).
    `daily_pct_remaining` est None si `ftmo_guard_instance.check()` n'a
    encore jamais ete appele (daily_floor pas encore rebase)."""
    daily_floor = ftmo_guard_instance.daily_floor
    overall_floor = ftmo_guard_instance.overall_floor
    isc = ftmo_guard_instance.isc
    daily_pct_remaining = max(0.0, (equity - daily_floor) / isc) if daily_floor is not None else None
    overall_pct_remaining = max(0.0, (equity - overall_floor) / isc)
    return {"daily_pct_remaining": daily_pct_remaining, "overall_pct_remaining": overall_pct_remaining}
```

- [ ] **Step 8: Lancer les tests et vérifier qu'ils passent**

Run: `pytest tests/gold_bot/test_ai_discretion.py tests/gold_bot/test_ftmo_guard.py -v`
Expected: PASS (tous les tests des Tâches 1-3).

- [ ] **Step 9: Commit**

```bash
git add gold_bot/ftmo_guard.py gold_bot/ai_discretion.py tests/gold_bot/test_ftmo_guard.py tests/gold_bot/test_ai_discretion.py
git commit -m "feat(gold-bot): FtmoGuard.daily_floor public + compute_ftmo_buffer"
```

---

### Task 4: Branchement dans `decide_and_act_fibonacci` + `loop.py` + non-régression Ichimoku

**Files:**
- Modify: `gold_bot/bot.py:1-16` (imports), `gold_bot/bot.py:202-206` et `:362-366` (signatures), `gold_bot/bot.py:494-567` (corps)
- Modify: `gold_bot/loop.py:405-410` (appel partagé `decide_and_act_vwap`/`decide_and_act_fibonacci`)
- Modify: `tests/gold_bot/test_bot.py`

**Interfaces:**
- Consumes : `ai_discretion.judge_fibonacci_entry`, `ai_discretion.compute_ftmo_buffer` (Tâches 2-3), `ai_shadow.LOW_CONFIDENCE_SIZE_FACTOR` (existant, inchangé).
- Produces : rien pour une tâche suivante (dernière tâche du plan).

- [ ] **Step 1: Lire le fichier actuel pour confirmer qu'aucune modification externe n'a eu lieu depuis l'écriture de ce plan**

Run: `grep -n "ai_shadow.shadow_judge_entry" gold_bot/bot.py`
Expected : deux occurrences (une dans `decide_and_act_fibonacci`, une dans `decide_and_act_ichimoku`). Si ce n'est pas le cas, arrêter et relire le fichier en entier avant de continuer — ce plan suppose le code exact lu le 2026-11-13.

- [ ] **Step 2: Modifier les imports de `gold_bot/bot.py`**

Dans le bloc d'imports (après `import gold_bot.ai_news_watch as ai_news_watch`, actuellement ligne 9), insérer AVANT cette ligne :

```python
import gold_bot.ai_discretion as ai_discretion
```

(`ai_discretion` précède `ai_news_watch` alphabétiquement — l'ordre alphabétique est déjà la convention de ce bloc.)

Après `import gold_bot.fibonacci_reversion as fibonacci_reversion` (actuellement ligne 13), insérer AVANT `import gold_bot.ichimoku_reversion as ichimoku_reversion` :

```python
import gold_bot.ftmo_guard as ftmo_guard
```

- [ ] **Step 3: Modifier les signatures de `decide_and_act_vwap` et `decide_and_act_fibonacci`**

Dans `gold_bot/bot.py:202-206`, remplacer :

```python
def decide_and_act_vwap(candles: list[dict], *, contract_size: float, balance: float, equity: float,
                         volume_step: float, min_volume: float, max_volume: float,
                         open_positions: list[dict], circuit_breaker: "risk.CircuitBreaker",
                         symbol: str = "XAUUSD", risk_pct: float = 0.05,
                         historical_candles: list[dict] | None = None) -> dict:
```

par :

```python
def decide_and_act_vwap(candles: list[dict], *, contract_size: float, balance: float, equity: float,
                         volume_step: float, min_volume: float, max_volume: float,
                         open_positions: list[dict], circuit_breaker: "risk.CircuitBreaker",
                         symbol: str = "XAUUSD", risk_pct: float = 0.05,
                         historical_candles: list[dict] | None = None,
                         ftmo_guard_instance: "ftmo_guard.FtmoGuard | None" = None) -> dict:
```

(`ftmo_guard_instance` n'est jamais utilisé par `decide_and_act_vwap` -- ajouté uniquement parce que `gold_bot/loop.py:405-410` appelle `decide_and_act_vwap`/`decide_and_act_fibonacci` via le même dict de kwargs, selon que `ftmo_guard_instance is not None` ou non -- voir Step 6.)

Dans `gold_bot/bot.py:362-366`, remplacer :

```python
def decide_and_act_fibonacci(candles: list[dict], *, contract_size: float, balance: float, equity: float,
                              volume_step: float, min_volume: float, max_volume: float,
                              open_positions: list[dict], circuit_breaker: "risk.CircuitBreaker",
                              symbol: str = "XAUUSD", risk_pct: float = 0.05,
                              historical_candles: list[dict] | None = None) -> dict:
```

par :

```python
def decide_and_act_fibonacci(candles: list[dict], *, contract_size: float, balance: float, equity: float,
                              volume_step: float, min_volume: float, max_volume: float,
                              open_positions: list[dict], circuit_breaker: "risk.CircuitBreaker",
                              symbol: str = "XAUUSD", risk_pct: float = 0.05,
                              historical_candles: list[dict] | None = None,
                              ftmo_guard_instance: "ftmo_guard.FtmoGuard | None" = None) -> dict:
```

- [ ] **Step 4: Remplacer le corps de `decide_and_act_fibonacci` à partir du calcul de taille**

Dans `gold_bot/bot.py`, remplacer le bloc qui va de (inclus) :

```python
    if direction is None:
        return {"action": "aucune", "reason": "pas de signal d'entree"}

    entry_price = state["close"]
    distance = abs(entry_price - stop_loss)
    if distance < entry_price * fibonacci_reversion.MIN_DISTANCE_PCT:
        return {"action": "aucune", "reason": "distance entree-stop trop faible"}

    if not circuit_breaker.can_open_position(equity):
        return {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}

    raw_size = risk.compute_position_size(balance, entry_price, stop_loss, contract_size, risk_pct=risk_pct)
    total_size = risk.round_to_volume_step(raw_size, volume_step, min_volume, max_volume)
    if total_size is None:
        return {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}

    # Veto IA branche pour de vrai depuis le 2026-11-06 (voir
    # gold_bot/ai_shadow.py) -- meme design fail-open que
    # decide_and_act_ichimoku : seul un verdict explicite "opposerait"
    # bloque, un verdict None (API absente, timeout, erreur, verdict non
    # parseable) OU "confirme" laisse le trade se faire normalement. Place
    # ici, apres tous les garde-fous (distance, coupe-circuit, taille),
    # pour qu'il ne se declenche QUE quand un vrai trade va reellement
    # s'ouvrir -- pas a chaque cycle ou la direction est simplement
    # determinee, ce qui peut rester vrai des heures sans jamais aboutir
    # a un trade (cause de l'incident de cout du 2026-10-29). D'apres
    # l'activite reelle, un trade ne s'ouvre que ~4 fois par semaine --
    # donc ce declencheur est naturellement rare, en plus du cooldown.
    # Decision utilisateur explicite (2026-11-06, en connaissance de
    # cause : le veto etait reste en mode fantome par prudence depuis le
    # 2026-10-28, aucune preuve de fiabilite sur plusieurs semaines en
    # conditions reelles -- promu malgre tout, sur demande).
    verdict = ai_shadow.shadow_judge_entry(
        symbol, direction, state, candles,
        engine_label="rebond de retracement de Fibonacci", real_decision=True,
    )
    if verdict is not None and verdict.get("verdict") == "opposerait":
        return {"action": "aucune", "reason": "veto IA (opposition)"}

    if verdict is not None and verdict.get("confiance") == "faible":
        # Incident du 2026-11-10 : l'IA avait elle-meme recommande de
        # reduire la taille faute de pouvoir verifier l'actualite --
        # jamais applique avant ce correctif (voir ai_shadow.py).
        reduced_size = risk.round_to_volume_step(
            total_size * ai_shadow.LOW_CONFIDENCE_SIZE_FACTOR, volume_step, min_volume, max_volume)
        if reduced_size is None:
            return {"action": "aucune", "reason": "taille reduite (confiance IA faible) sous le minimum du broker"}
        total_size = reduced_size

    partial_volume = None
    if fibonacci_reversion.PARTIAL_TP_PCT > 0:
        partial_volume = risk.round_to_volume_step(
            total_size * fibonacci_reversion.PARTIAL_TP_PCT, volume_step, min_volume, max_volume)

    if partial_volume is None:
        return {"action": "simulation", "steps": [{
            "type": "ouverture_simulee", "symbol": symbol, "direction": direction, "volume": total_size,
            "entry": entry_price, "stop_loss": stop_loss, "take_profit": None,
        }]}

    runner_volume = round(total_size - partial_volume, 2)
    partial_target = (entry_price + fibonacci_reversion.TARGET_R * distance if direction == "achat"
                       else entry_price - fibonacci_reversion.TARGET_R * distance)

    steps = [{
        "type": "ouverture_simulee", "symbol": symbol, "direction": direction, "volume": partial_volume,
        "entry": entry_price, "stop_loss": stop_loss, "take_profit": partial_target,
    }]
    if runner_volume >= volume_step:
        steps.append({
            "type": "ouverture_simulee", "symbol": symbol, "direction": direction, "volume": runner_volume,
            "entry": entry_price, "stop_loss": stop_loss, "take_profit": None,
        })
    return {"action": "simulation", "steps": steps}
```

par (jusqu'à, et incluant, le `return` final de la fonction) :

```python
    if direction is None:
        return {"action": "aucune", "reason": "pas de signal d'entree"}

    entry_price = state["close"]
    distance = abs(entry_price - stop_loss)
    if distance < entry_price * fibonacci_reversion.MIN_DISTANCE_PCT:
        return {"action": "aucune", "reason": "distance entree-stop trop faible"}

    if not circuit_breaker.can_open_position(equity):
        return {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}

    # Decision IA discretionnaire branchee ici depuis ce chantier (voir
    # docs/superpowers/specs/2026-11-13-gold-bot-ai-discretionary-
    # fibonacci-entry-design.md), a la place de l'ancien veto ai_shadow.
    # AVANT le calcul de taille (contrairement a l'ancien veto) : le
    # sens et les distances TP/SL reellement utilises peuvent differer
    # de ceux que les regles viennent de calculer ci-dessus -- l'IA
    # decide elle-meme, sans jamais connaitre `direction`/`stop_loss`
    # (voir ai_discretion._build_prompt, aucun parametre direction/bias).
    # Place apres les deux garde-fous gratuits ci-dessus (distance,
    # coupe-circuit) pour ne declencher l'appel reel que quand un trade
    # a reellement une chance de s'ouvrir -- meme raison que l'ancien
    # veto (incident de cout du 2026-10-29).
    baseline_take_profit_distance = fibonacci_reversion.TARGET_R * distance
    ftmo_buffer = (
        ai_discretion.compute_ftmo_buffer(ftmo_guard_instance, equity)
        if ftmo_guard_instance is not None
        else {"daily_pct_remaining": None, "overall_pct_remaining": None}
    )
    ai_verdict = ai_discretion.judge_fibonacci_entry(
        symbol, state, candles,
        baseline_stop_loss_distance=distance, baseline_take_profit_distance=baseline_take_profit_distance,
        ftmo_buffer=ftmo_buffer,
    )
    if ai_verdict is None:
        return {"action": "aucune", "reason": "aucune decision IA (echec, rejet ou rien a faire)"}

    direction = ai_verdict["action"]
    distance = ai_verdict["stop_loss_distance"]
    take_profit_distance = ai_verdict["take_profit_distance"]
    stop_loss = entry_price - distance if direction == "achat" else entry_price + distance

    raw_size = risk.compute_position_size(balance, entry_price, stop_loss, contract_size, risk_pct=risk_pct)
    total_size = risk.round_to_volume_step(raw_size, volume_step, min_volume, max_volume)
    if total_size is None:
        return {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}

    if ai_verdict.get("confiance") == "faible":
        # Incident du 2026-11-10 : meme reflexe que l'ancien veto IA
        # (ai_shadow.py), porte ici tel quel -- independant des
        # multiplicateurs TP/SL, deux leviers de risque distincts.
        reduced_size = risk.round_to_volume_step(
            total_size * ai_shadow.LOW_CONFIDENCE_SIZE_FACTOR, volume_step, min_volume, max_volume)
        if reduced_size is None:
            return {"action": "aucune", "reason": "taille reduite (confiance IA faible) sous le minimum du broker"}
        total_size = reduced_size

    partial_volume = None
    if fibonacci_reversion.PARTIAL_TP_PCT > 0:
        partial_volume = risk.round_to_volume_step(
            total_size * fibonacci_reversion.PARTIAL_TP_PCT, volume_step, min_volume, max_volume)

    if partial_volume is None:
        return {"action": "simulation", "steps": [{
            "type": "ouverture_simulee", "symbol": symbol, "direction": direction, "volume": total_size,
            "entry": entry_price, "stop_loss": stop_loss, "take_profit": None,
        }]}

    runner_volume = round(total_size - partial_volume, 2)
    partial_target = (entry_price + take_profit_distance if direction == "achat"
                       else entry_price - take_profit_distance)

    steps = [{
        "type": "ouverture_simulee", "symbol": symbol, "direction": direction, "volume": partial_volume,
        "entry": entry_price, "stop_loss": stop_loss, "take_profit": partial_target,
    }]
    if runner_volume >= volume_step:
        steps.append({
            "type": "ouverture_simulee", "symbol": symbol, "direction": direction, "volume": runner_volume,
            "entry": entry_price, "stop_loss": stop_loss, "take_profit": None,
        })
    return {"action": "simulation", "steps": steps}
```

**Important** : `ai_shadow` reste importé dans `gold_bot/bot.py` (utilisé par `decide_and_act_ichimoku`, inchangé, et par `ai_shadow.LOW_CONFIDENCE_SIZE_FACTOR` ci-dessus) -- ne pas retirer l'import.

- [ ] **Step 5: Ne PAS modifier `decide_and_act_ichimoku`**

Vérifier que `gold_bot/bot.py` autour de la ligne 669 (`verdict = ai_shadow.shadow_judge_entry(symbol, direction, state_for_ai, candles, engine_label="croisement Tenkan/Kijun confirme par le nuage (Ichimoku)", real_decision=True)`) est strictement inchangé après les Steps 2-4.

Run: `git diff gold_bot/bot.py | grep -A3 -B3 "Tenkan/Kijun"`
Expected : aucune ligne `-`/`+` dans ce voisinage (le diff ne touche pas `decide_and_act_ichimoku`).

- [ ] **Step 6: Brancher `ftmo_guard_instance` dans `gold_bot/loop.py`**

Dans `gold_bot/loop.py:405-410`, remplacer :

```python
            decision = engine(
                candles, contract_size=contract_size, balance=balance, equity=equity,
                volume_step=spec["volumeStep"], min_volume=spec["minVolume"], max_volume=spec["maxVolume"],
                open_positions=open_positions, circuit_breaker=circuit_breaker, symbol=symbol,
                risk_pct=profile_params["risk_pct"], historical_candles=historical_candles,
            )
```

par :

```python
            decision = engine(
                candles, contract_size=contract_size, balance=balance, equity=equity,
                volume_step=spec["volumeStep"], min_volume=spec["minVolume"], max_volume=spec["maxVolume"],
                open_positions=open_positions, circuit_breaker=circuit_breaker, symbol=symbol,
                risk_pct=profile_params["risk_pct"], historical_candles=historical_candles,
                ftmo_guard_instance=ftmo_guard_instance,
            )
```

- [ ] **Step 7: Mettre à jour `tests/gold_bot/test_bot.py` — nouveau fixture par défaut + suppression/remplacement des tests obsolètes**

Ajouter, juste après le fixture `_default_ai_shadow_veto` (lignes 24-34), un nouveau fixture autouse :

```python
@pytest.fixture(autouse=True)
def _default_ai_discretion_judge(monkeypatch):
    """Les fiches "happy path" de decide_and_act_fibonacci (deux jambes,
    TP/SL exacts...) datent d'avant ce chantier et ne testent pas le
    jugement IA lui-meme (voir tests/gold_bot/test_ai_discretion.py pour
    ca) -- ce defaut "confirme" systematiquement, dans le MEME sens et
    aux MEMES distances que la regle (multiplicateurs 1.0/1.0 implicites,
    en renvoyant directement les distances de reference), pour que ces
    fiches restent inchangees. Un test qui veut exercer
    judge_fibonacci_entry lui-meme l'ecrase avec son propre monkeypatch."""
    def _confirm_rule(symbol, state, candles, *, baseline_stop_loss_distance,
                       baseline_take_profit_distance, ftmo_buffer, log_path=None, now=None):
        return {
            "action": state.get("bias"), "confiance": "haute",
            "stop_loss_distance": baseline_stop_loss_distance,
            "take_profit_distance": baseline_take_profit_distance,
            "raison": "defaut de test",
        }
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry", _confirm_rule)
```

Mettre à jour la docstring de `_default_ai_shadow_veto` (lignes 24-34) : remplacer la dernière phrase `"Un test qui veut exercer shadow_judge_entry directement l'ecrase avec son propre monkeypatch (voir tests/gold_bot/test_ai_shadow.py)."` par `"Ne concerne plus que decide_and_act_ichimoku depuis ce chantier -- decide_and_act_fibonacci utilise desormais ai_discretion.judge_fibonacci_entry (voir _default_ai_discretion_judge ci-dessous). Un test qui veut exercer shadow_judge_entry directement l'ecrase avec son propre monkeypatch (voir tests/gold_bot/test_ai_shadow.py)."`

**Supprimer** les 10 tests suivants (obsolètes, testent un contrat qui n'existe plus pour Fibonacci) :
- `test_decide_and_act_fibonacci_calls_ai_shadow_veto_only_when_a_real_trade_opens`
- `test_decide_and_act_fibonacci_blocks_trade_when_ai_opposes`
- `test_decide_and_act_fibonacci_blocks_vente_trade_when_ai_opposes`
- `test_decide_and_act_fibonacci_proceeds_when_ai_verdict_is_malformed`
- `test_decide_and_act_fibonacci_proceeds_when_ai_confirms`
- `test_decide_and_act_fibonacci_halves_size_when_ai_confidence_is_low`
- `test_decide_and_act_fibonacci_does_not_reduce_size_for_moyenne_confidence`
- `test_decide_and_act_fibonacci_returns_aucune_when_halved_size_below_minimum`
- `test_decide_and_act_fibonacci_proceeds_when_ai_returns_none_fail_open`
- `test_decide_and_act_fibonacci_calls_ai_shadow_with_real_decision_true`

**Ajouter**, à la fin de `tests/gold_bot/test_bot.py`, leurs remplaçants :

```python
def test_decide_and_act_fibonacci_calls_ai_discretion_only_when_a_real_trade_would_open(monkeypatch):
    calls = []
    def _spy(*a, **k):
        calls.append(k)
        return {"action": "achat", "confiance": "haute", "stop_loss_distance": 11.0,
                "take_profit_distance": 16.5, "raison": "x"}
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry", _spy)
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert len(calls) == 1


def test_decide_and_act_fibonacci_follows_ai_chosen_direction_even_when_opposite_of_rule(monkeypatch):
    # La regle (bias="achat") proposerait un achat -- l'IA choisit
    # "vente" a la place : c'est exactement l'autonomie de sens que ce
    # chantier ajoute (voir la spec, section "Autonomie du sens").
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "vente", "confiance": "haute",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    partial, runner = result["steps"]
    assert partial["direction"] == "vente"
    assert runner["direction"] == "vente"
    # stop_loss/take_profit calcules par rapport a entry_price=1981.0
    # avec la distance DE L'IA (11.0/16.5), pas celle de la regle.
    assert partial["stop_loss"] == pytest.approx(1981.0 + 11.0)
    assert partial["take_profit"] == pytest.approx(1981.0 - 16.5)


def test_decide_and_act_fibonacci_no_action_when_ai_discretion_returns_none(monkeypatch):
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry", lambda *a, **k: None)
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "aucune decision IA (echec, rejet ou rien a faire)"}


def test_decide_and_act_fibonacci_applies_ai_tp_sl_distances_to_executed_legs(monkeypatch):
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "haute",
                                           "stop_loss_distance": 5.0, "take_profit_distance": 50.0, "raison": "x"})
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    partial, _ = result["steps"]
    assert partial["stop_loss"] == pytest.approx(1981.0 - 5.0)
    assert partial["take_profit"] == pytest.approx(1981.0 + 50.0)


def test_decide_and_act_fibonacci_halves_size_when_ai_confidence_is_low(monkeypatch):
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)

    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "haute",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    baseline = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    baseline_volume = _fib_total_volume(baseline)

    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "faible",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    reduced = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    reduced_volume = _fib_total_volume(reduced)

    expected = bot.risk.round_to_volume_step(
        baseline_volume * bot.ai_shadow.LOW_CONFIDENCE_SIZE_FACTOR, 0.01, 0.01, 500)
    assert reduced_volume == expected
    assert reduced_volume < baseline_volume


def test_decide_and_act_fibonacci_does_not_reduce_size_for_moyenne_confidence(monkeypatch):
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)

    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "haute",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    baseline = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )

    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "moyenne",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    moyenne = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert _fib_total_volume(moyenne) == _fib_total_volume(baseline)


def test_decide_and_act_fibonacci_returns_aucune_when_halved_size_below_minimum(monkeypatch):
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "haute",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    baseline = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    baseline_volume = _fib_total_volume(baseline)
    min_volume = round(baseline_volume * 0.75, 2)

    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "faible",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=min_volume, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "taille reduite (confiance IA faible) sous le minimum du broker"}


def test_decide_and_act_fibonacci_passes_ftmo_buffer_computed_from_ftmo_guard_instance(monkeypatch):
    calls = []
    def _spy(*a, **k):
        calls.append(k)
        return {"action": "achat", "confiance": "haute", "stop_loss_distance": 11.0,
                "take_profit_distance": 16.5, "raison": "x"}
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry", _spy)
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    guard = bot.ftmo_guard.FtmoGuard(isc=10000.0)
    guard.check(balance=10000.0, equity=10000.0)
    bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=9800, open_positions=[], circuit_breaker=_open_circuit_breaker(),
        ftmo_guard_instance=guard,
    )
    assert calls[0]["ftmo_buffer"] == {"daily_pct_remaining": pytest.approx(0.02),
                                        "overall_pct_remaining": pytest.approx(0.07)}


def test_decide_and_act_ichimoku_still_uses_ai_shadow_unaffected_by_this_change(monkeypatch):
    """Non-regression explicite : ce chantier ne touche que
    decide_and_act_fibonacci. decide_and_act_ichimoku doit continuer a
    utiliser ai_shadow.shadow_judge_entry exactement comme avant."""
    calls = []
    monkeypatch.setattr(bot.ai_shadow, "shadow_judge_entry", lambda *a, **k: calls.append(k) or None)
    state = {"tenkan": 2650.0, "kijun": 2645.0, "senkou_a": 2640.0, "senkou_b": 2635.0,
              "close": 2651.0, "high": 2652.0, "low": 2649.0, "atr": 2.0, "hour_utc": 10}
    monkeypatch.setattr(bot.ichimoku_reversion, "latest_state", lambda candles: state)
    monkeypatch.setattr(bot.ichimoku_reversion, "signal", lambda s: "achat")
    bot.decide_and_act_ichimoku(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(), symbol="XAGUSD",
    )
    assert len(calls) == 1
    assert calls[0]["real_decision"] is True
    assert calls[0]["engine_label"] == "croisement Tenkan/Kijun confirme par le nuage (Ichimoku)"
```

**Note pour l'implémenteur** : le test `test_decide_and_act_ichimoku_still_uses_ai_shadow_unaffected_by_this_change` ci-dessus construit un `state`/mock minimal pour `ichimoku_reversion.latest_state`/`signal` à partir de ce qui est documenté dans ce plan -- si le véritable `decide_and_act_ichimoku` exige d'autres champs dans `state` ou un contrat différent pour atteindre le point d'appel du veto, ajuster ce mock en lisant `gold_bot/ichimoku_reversion.py` et les tests existants (`tests/gold_bot/test_bot.py`, section `decide_and_act_ichimoku`) avant de le finaliser — ne pas deviner les noms de champs.

- [ ] **Step 8: Lancer toute la suite `gold_bot` et vérifier qu'elle passe**

Run: `pytest tests/gold_bot/ -v`
Expected: PASS, aucune régression sur `decide_and_act_ichimoku`, `decide_and_act_vwap`, ou le reste du bot.

- [ ] **Step 9: Lancer toute la suite de tests du projet**

Run: `pytest tests/ -q`
Expected: PASS (aucune régression ailleurs dans le dépôt).

- [ ] **Step 10: Commit**

```bash
git add gold_bot/bot.py gold_bot/loop.py tests/gold_bot/test_bot.py
git commit -m "feat(gold-bot): decide_and_act_fibonacci utilise ai_discretion -- decision directionnelle reelle, veto binaire retire"
```

---

## Auto-review (fait par l'auteur du plan avant livraison)

**Couverture de la spec** : Contexte/Objectif/Portée → motivent les tâches, pas de tâche dédiée nécessaire. Architecture → Tâches 2, 4. Contrat d'appel IA → Tâche 1 (`_build_prompt`) + Tâche 2 (modèle/effort/outils). Garde-fous → Tâche 1 (`clamp_multipliers`/`resolve_risk_reward`) + Tâche 4 (réflexe confiance→taille). Gestion d'erreur → Tâche 2. Cooldown → Tâche 2. Journalisation → Tâche 2. Intégration avec l'existant → Tâche 4 (Step 5, test de non-régression Ichimoku explicite). Déploiement → aucune action de code (séquencement documenté dans la spec, pas dans le plan). Tests requis → couverts un à un dans les Tâches 1-4.

**Type consistency vérifiée** : `judge_fibonacci_entry` (Tâche 2) et son usage en Tâche 4 utilisent les mêmes clés de retour (`action`, `confiance`, `stop_loss_distance`, `take_profit_distance`, `raison`) ; `compute_ftmo_buffer` (Tâche 3) et son usage en Tâche 4 utilisent les mêmes clés (`daily_pct_remaining`, `overall_pct_remaining`) ; `_build_prompt` (Tâche 1) et son appel dans `judge_fibonacci_entry` (Tâche 2) utilisent les mêmes noms de paramètres.

**Aucun placeholder** : chaque étape de code contient l'implémentation ou le test complet, pas de "TODO"/"ajouter la logique"/description sans code.
