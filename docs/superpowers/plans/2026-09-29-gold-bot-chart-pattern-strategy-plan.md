# Remplacement du moteur de signal du bot Or par le suivi de tendance — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le moteur de signal contre-tendance du bot Or (trop passif en production réelle) par un moteur de suivi de tendance basé sur les figures chartistes déjà construites et testées côté site (`docs/chart_patterns.js`).

**Architecture:** Un nouveau module autonome `gold_bot/chart_patterns.py`, portage fidèle de `detectChartPatterns` (7 figures : Double Top/Bottom, Tête-Épaule ×2, Triangles ×3), puis `gold_bot/confluence.py::compute_signal` réécrit pour s'appuyer dessus au lieu de l'ancien moteur candlestick/RSI/MACD/Bollinger (retiré, code mort). Entrée/stop/cible dérivés de `breakoutPrice`/`patternHeight` (mesure de la figure). Aucun changement à `bot.py`/`loop.py`/`backtest.py` (consomment `compute_signal` génériquement).

**Tech Stack:** Python 3, pytest — même conventions que le reste de `gold_bot/`.

**Spec:** `docs/superpowers/specs/2026-09-29-gold-bot-chart-pattern-strategy-design.md`

## Global Constraints

- Remplacement complet, pas de coexistence des deux moteurs — décision explicite de l'utilisateur.
- Aucun changement à `gold_bot/bot.py`, `gold_bot/loop.py`, `gold_bot/backtest.py`, ni à aucun garde-fou (`risk.py`, coupe-circuit, plafond de levier, arrondi au pas broker, garde marché fermé, dry_run/kill_switch) — seule la détection du signal (`compute_signal`) change.
- `SCALP_TAKEPROFIT_RISK_MULTIPLE = 1.5` reste EXACTEMENT inchangée (seuil du filtre `meets_minimum_risk_reward`, toujours actif).
- `is_news_blackout`, `SCALP_NEWS_BLACKOUT_MINUTES`, `SCALP_HIGH_IMPACT_EVENTS_UTC` restent EXACTEMENT inchangés.
- `detect_pivots`, `classify_trend`, `fetch_gold_candles`, `_parse_float` restent EXACTEMENT inchangés (juste appelés avec un `k` différent pour `detect_pivots`).
- Le portage de `gold_bot/chart_patterns.py` doit être un comportement identique à `docs/chart_patterns.js::detectChartPatterns` — mêmes 9 cas de test que `docs/chart_patterns.test.js`, aucune divergence.
- Pas de déploiement en production dans ce plan — la validation par backtest réel et le déploiement sur le VPS (spec §5) sont faits par le contrôleur après la fin des tâches, pas une tâche de ce plan.

---

## Task 1 : `gold_bot/chart_patterns.py` — portage du moteur de figures chartistes

**Files:**
- Create: `gold_bot/chart_patterns.py`
- Test: `tests/gold_bot/test_chart_patterns.py`

**Interfaces:**
- Consumes : rien (module autonome, aucune dépendance vers `gold_bot.confluence`).
- Produces : `CHARTPATTERN_PIVOT_K = 5`, `CHARTPATTERN_HEIGHT_TOLERANCE = 1.0`, `detect_chart_patterns(pivots: list[dict], trend: str, price: float) -> dict | None` — utilisée par la Task 2. `pivots` a la forme `[{"index": int, "type": "high"|"low", "price": float}, ...]` (même forme que `gold_bot.confluence.detect_pivots`, déjà existante, inchangée par ce plan). Le dict retourné a les clés `name`, `direction` ("haussier"/"baissier"), `kind` ("retournement"/"continuation"), `breakoutPrice`, `extremityPrice`, `patternHeight`.

- [ ] **Step 1 : Écrire les tests des figures de retournement**

Crée `tests/gold_bot/test_chart_patterns.py` avec ce contenu (les 4 premiers cas — Double Top, Double Top rejeté, Double Bottom, Tête-Épaule — transcrits fidèlement depuis `docs/chart_patterns.test.js`) :

```python
import gold_bot.chart_patterns as chart_patterns


def test_double_top_detected():
    pivots = [
        {"index": 0, "type": "high", "price": 110},
        {"index": 5, "type": "low", "price": 100},
        {"index": 10, "type": "high", "price": 110.5},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "haussier", 99)
    assert result == {
        "name": "Double Top", "direction": "baissier", "kind": "retournement",
        "breakoutPrice": 100, "extremityPrice": 110.5, "patternHeight": 10.5,
    }


def test_double_top_rejected_when_heights_too_far_apart():
    # Identique au test précédent mais h2=112 au lieu de 110.5 : écart de
    # 2$ > CHARTPATTERN_HEIGHT_TOLERANCE (1$) -> aucune figure détectée.
    pivots = [
        {"index": 0, "type": "high", "price": 110},
        {"index": 5, "type": "low", "price": 100},
        {"index": 10, "type": "high", "price": 112},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "haussier", 99)
    assert result is None


def test_double_bottom_detected():
    pivots = [
        {"index": 0, "type": "low", "price": 90},
        {"index": 5, "type": "high", "price": 100},
        {"index": 10, "type": "low", "price": 90.5},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "baissier", 101)
    assert result == {
        "name": "Double Bottom", "direction": "haussier", "kind": "retournement",
        "breakoutPrice": 100, "extremityPrice": 90, "patternHeight": 10,
    }


def test_tete_epaule_detected():
    pivots = [
        {"index": 0, "type": "high", "price": 100},   # épaule 1
        {"index": 5, "type": "low", "price": 95},
        {"index": 10, "type": "high", "price": 110},  # tête
        {"index": 15, "type": "low", "price": 96},
        {"index": 20, "type": "high", "price": 100.5},  # épaule 2
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "haussier", 95)
    assert result == {
        "name": "Tête-Épaule", "direction": "baissier", "kind": "retournement",
        "breakoutPrice": 96, "extremityPrice": 110, "patternHeight": 14,
    }
```

- [ ] **Step 2 : Vérifier que les tests échouent**

Run: `python -m pytest tests/gold_bot/test_chart_patterns.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'gold_bot.chart_patterns'`

- [ ] **Step 3 : Implémenter le module avec les figures de retournement**

Crée `gold_bot/chart_patterns.py` :

```python
# gold_bot/chart_patterns.py
# Détection de figures chartistes (Double Top/Bottom, Tête-Épaule,
# Triangles) — portage fidèle de docs/chart_patterns.js::detectChartPatterns
# (même comportement, mêmes 9 cas de test). Module autonome, sans
# dépendance vers gold_bot.confluence, comme son équivalent JS n'a pas
# de dépendance vers docs/scalping.js.

CHARTPATTERN_PIVOT_K = 5  # plus grand que l'ancien K=3 (tendance/S-R
                          # ponctuel) : une figure chartiste s'étale sur
                          # bien plus de bougies qu'un niveau ponctuel.
CHARTPATTERN_HEIGHT_TOLERANCE = 1.0  # $ de tolérance pour juger deux
                                      # sommets/creux "de hauteur comparable"


def _height_close(a: float, b: float) -> bool:
    return abs(a - b) <= CHARTPATTERN_HEIGHT_TOLERANCE


def detect_chart_patterns(pivots: list[dict], trend: str, price: float) -> dict | None:
    """Détecte une des 7 figures chartistes reconnues à partir de `pivots`
    (déjà calculés par l'appelant avec K=CHARTPATTERN_PIVOT_K), `trend`
    (classify_trend, gold_bot.confluence) et `price` (dernière clôture,
    pour juger si une cassure est confirmée). Renvoie la première figure
    trouvée (ordre : retournement d'abord, puis continuation) ou None.
    Ne lève jamais d'exception — pivots insuffisants renvoie simplement None."""
    highs = [p for p in pivots if p["type"] == "high"]
    lows = [p for p in pivots if p["type"] == "low"]

    # --- Double Top (retournement baissier) ---
    if trend == "haussier" and len(highs) >= 2:
        h1, h2 = highs[-2:]
        between = [l for l in lows if h1["index"] < l["index"] < h2["index"]]
        if between and _height_close(h1["price"], h2["price"]):
            neckline = between[-1]["price"]
            if price < neckline:
                return {
                    "name": "Double Top", "direction": "baissier", "kind": "retournement",
                    "breakoutPrice": neckline, "extremityPrice": max(h1["price"], h2["price"]),
                    "patternHeight": max(h1["price"], h2["price"]) - neckline,
                }

    # --- Double Bottom (retournement haussier) ---
    if trend == "baissier" and len(lows) >= 2:
        l1, l2 = lows[-2:]
        between = [h for h in highs if l1["index"] < h["index"] < l2["index"]]
        if between and _height_close(l1["price"], l2["price"]):
            neckline = between[-1]["price"]
            if price > neckline:
                return {
                    "name": "Double Bottom", "direction": "haussier", "kind": "retournement",
                    "breakoutPrice": neckline, "extremityPrice": min(l1["price"], l2["price"]),
                    "patternHeight": neckline - min(l1["price"], l2["price"]),
                }

    # --- Tête-Épaule (retournement baissier) ---
    if trend == "haussier" and len(highs) >= 3:
        s1, head, s2 = highs[-3:]
        troughs = [l for l in lows if s1["index"] < l["index"] < s2["index"]]
        if len(troughs) >= 2 and _height_close(s1["price"], s2["price"]) \
                and head["price"] > s1["price"] and head["price"] > s2["price"]:
            neckline = troughs[-1]["price"]
            if price < neckline:
                return {
                    "name": "Tête-Épaule", "direction": "baissier", "kind": "retournement",
                    "breakoutPrice": neckline, "extremityPrice": head["price"],
                    "patternHeight": head["price"] - neckline,
                }

    # --- Tête-Épaule inversée (retournement haussier) ---
    if trend == "baissier" and len(lows) >= 3:
        s1, head, s2 = lows[-3:]
        peaks = [h for h in highs if s1["index"] < h["index"] < s2["index"]]
        if len(peaks) >= 2 and _height_close(s1["price"], s2["price"]) \
                and head["price"] < s1["price"] and head["price"] < s2["price"]:
            neckline = peaks[-1]["price"]
            if price > neckline:
                return {
                    "name": "Tête-Épaule inversée", "direction": "haussier", "kind": "retournement",
                    "breakoutPrice": neckline, "extremityPrice": head["price"],
                    "patternHeight": neckline - head["price"],
                }

    return None
```

- [ ] **Step 4 : Vérifier que les 4 tests passent**

Run: `python -m pytest tests/gold_bot/test_chart_patterns.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5 : Écrire les tests des figures de continuation**

Ajoute à la fin de `tests/gold_bot/test_chart_patterns.py` :

```python
def test_tete_epaule_inversee_detected():
    pivots = [
        {"index": 0, "type": "low", "price": 100},
        {"index": 5, "type": "high", "price": 105},
        {"index": 10, "type": "low", "price": 90},
        {"index": 15, "type": "high", "price": 104},
        {"index": 20, "type": "low", "price": 100.5},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "baissier", 105)
    assert result == {
        "name": "Tête-Épaule inversée", "direction": "haussier", "kind": "retournement",
        "breakoutPrice": 104, "extremityPrice": 90, "patternHeight": 14,
    }


def test_triangle_ascendant_detected():
    pivots = [
        {"index": 0, "type": "high", "price": 110},
        {"index": 5, "type": "low", "price": 95},
        {"index": 10, "type": "high", "price": 110.5},
        {"index": 15, "type": "low", "price": 100},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "haussier", 111)
    assert result == {
        "name": "Triangle ascendant", "direction": "haussier", "kind": "continuation",
        "breakoutPrice": 110.5, "extremityPrice": 95, "patternHeight": 15.5,
    }


def test_triangle_descendant_detected():
    pivots = [
        {"index": 0, "type": "low", "price": 90},
        {"index": 5, "type": "high", "price": 105},
        {"index": 10, "type": "low", "price": 90.5},
        {"index": 15, "type": "high", "price": 100},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "baissier", 90)
    assert result == {
        "name": "Triangle descendant", "direction": "baissier", "kind": "continuation",
        "breakoutPrice": 90.5, "extremityPrice": 105, "patternHeight": 14.5,
    }


def test_triangle_symetrique_breakout_haussier():
    pivots = [
        {"index": 0, "type": "high", "price": 110},
        {"index": 5, "type": "low", "price": 90},
        {"index": 10, "type": "high", "price": 105},
        {"index": 15, "type": "low", "price": 95},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "haussier", 106)
    assert result == {
        "name": "Triangle symétrique", "direction": "haussier", "kind": "continuation",
        "breakoutPrice": 105, "extremityPrice": 90, "patternHeight": 20,
    }


def test_triangle_symetrique_breakout_baissier():
    # Mêmes pivots que le test précédent, mais tendance et prix inversés
    # -> cassure dans l'autre sens (le triangle symétrique ne présume pas
    # du sens avant la cassure effective, cf. spec chart_patterns.js).
    pivots = [
        {"index": 0, "type": "high", "price": 110},
        {"index": 5, "type": "low", "price": 90},
        {"index": 10, "type": "high", "price": 105},
        {"index": 15, "type": "low", "price": 95},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "baissier", 89)
    assert result == {
        "name": "Triangle symétrique", "direction": "baissier", "kind": "continuation",
        "breakoutPrice": 95, "extremityPrice": 110, "patternHeight": 20,
    }
```

- [ ] **Step 6 : Vérifier que les 5 nouveaux tests échouent**

Run: `python -m pytest tests/gold_bot/test_chart_patterns.py -v`
Expected: FAIL (5 nouveaux tests — les figures de continuation n'existent pas encore dans `detect_chart_patterns`)

- [ ] **Step 7 : Implémenter les figures de continuation**

Ajoute à `gold_bot/chart_patterns.py`, juste avant le `return None` final de `detect_chart_patterns` :

```python
    # --- Triangle ascendant (continuation haussière) ---
    if trend == "haussier" and len(highs) >= 2 and len(lows) >= 2:
        h1, h2 = highs[-2:]
        l1, l2 = lows[-2:]
        if _height_close(h1["price"], h2["price"]) and l2["price"] > l1["price"]:
            resistance = h2["price"]
            if price > resistance:
                return {
                    "name": "Triangle ascendant", "direction": "haussier", "kind": "continuation",
                    "breakoutPrice": resistance, "extremityPrice": l1["price"],
                    "patternHeight": resistance - l1["price"],
                }

    # --- Triangle descendant (continuation baissière) ---
    if trend == "baissier" and len(highs) >= 2 and len(lows) >= 2:
        h1, h2 = highs[-2:]
        l1, l2 = lows[-2:]
        if _height_close(l1["price"], l2["price"]) and h2["price"] < h1["price"]:
            support = l2["price"]
            if price < support:
                return {
                    "name": "Triangle descendant", "direction": "baissier", "kind": "continuation",
                    "breakoutPrice": support, "extremityPrice": h1["price"],
                    "patternHeight": h1["price"] - support,
                }

    # --- Triangle symétrique (continuation, sens déterminé par la cassure) ---
    if len(highs) >= 2 and len(lows) >= 2:
        h1, h2 = highs[-2:]
        l1, l2 = lows[-2:]
        converging = h2["price"] < h1["price"] and l2["price"] > l1["price"]
        if converging:
            if trend == "haussier" and price > h2["price"]:
                return {
                    "name": "Triangle symétrique", "direction": "haussier", "kind": "continuation",
                    "breakoutPrice": h2["price"], "extremityPrice": l1["price"],
                    "patternHeight": h1["price"] - l1["price"],
                }
            if trend == "baissier" and price < l2["price"]:
                return {
                    "name": "Triangle symétrique", "direction": "baissier", "kind": "continuation",
                    "breakoutPrice": l2["price"], "extremityPrice": h1["price"],
                    "patternHeight": h1["price"] - l1["price"],
                }
```

Le Step 3 n'a implémenté que Double Top, Double Bottom, et Tête-Épaule — la figure "Tête-Épaule inversée" (dont le test a été ajouté au Step 5) n'existe pas encore dans `detect_chart_patterns`. Ajoute-la maintenant, juste après le bloc `# --- Tête-Épaule (retournement baissier) ---` existant et avant les 3 blocs "Triangle" ci-dessus :

```python
    # --- Tête-Épaule inversée (retournement haussier) ---
    if trend == "baissier" and len(lows) >= 3:
        s1, head, s2 = lows[-3:]
        peaks = [h for h in highs if s1["index"] < h["index"] < s2["index"]]
        if len(peaks) >= 2 and _height_close(s1["price"], s2["price"]) \
                and head["price"] < s1["price"] and head["price"] < s2["price"]:
            neckline = peaks[-1]["price"]
            if price > neckline:
                return {
                    "name": "Tête-Épaule inversée", "direction": "haussier", "kind": "retournement",
                    "breakoutPrice": neckline, "extremityPrice": head["price"],
                    "patternHeight": neckline - head["price"],
                }
```

- [ ] **Step 8 : Vérifier que les 9 tests passent**

Run: `python -m pytest tests/gold_bot/test_chart_patterns.py -v`
Expected: PASS (9 tests au total)

- [ ] **Step 9 : Commit**

```bash
git add gold_bot/chart_patterns.py tests/gold_bot/test_chart_patterns.py
git commit -m "Ajoute le moteur de figures chartistes (portage de docs/chart_patterns.js)"
```

---

## Task 2 : `gold_bot/confluence.py` — remplacement de `compute_signal`

**Files:**
- Modify: `gold_bot/confluence.py`
- Modify: `tests/gold_bot/test_confluence.py`

**Interfaces:**
- Consumes : `gold_bot.chart_patterns.detect_chart_patterns(pivots, trend, price) -> dict | None` (Task 1). `detect_pivots(candles, k)`, `classify_trend(pivots)`, `is_news_blackout(date)`, `meets_minimum_risk_reward(entry_price, stop_loss, take_profit, direction)` — tous déjà existants dans ce même fichier, INCHANGÉS par cette tâche.
- Produces : `compute_signal(candles) -> dict` avec la forme inchangée `{"status", "price", "entry", "stop_loss", "take_profit", "trend", "pattern"}` — consommée sans changement par `gold_bot/bot.py::decide_and_act` et `gold_bot/backtest.py::simulate_trades`/`_open_trade_from_signal` (aucune modification requise dans ces deux fichiers, vérifié dans la spec §3.3).

**Note d'implémentation importante** : cette tâche RETIRE du code (fonctions ET constantes) et RETIRE des tests en plus d'en ajouter — lis d'abord tout `gold_bot/confluence.py` et tout `tests/gold_bot/test_confluence.py` en entier avant de commencer, pour repérer précisément les blocs à retirer (les noms exacts sont listés ci-dessous, mais leurs bornes de lignes exactes peuvent avoir légèrement dérivé).

- [ ] **Step 1 : Écrire les nouveaux tests d'intégration de `compute_signal`**

Dans `tests/gold_bot/test_confluence.py`, la fonction `_candle(open_, high, low, close)` (helper existant, construit un dict de bougie avec un horodatage fixe "2026-01-05 12:00:00", loin de tout évènement macro) doit être CONSERVÉE — elle est réutilisée ci-dessous. Ajoute ces tests juste après la fonction `_candle` existante (avant les tests `test_match_candlestick_pattern_*` qui seront retirés au Step 4) :

```python
def test_compute_signal_achat_from_bullish_pattern(monkeypatch):
    candles = [_candle(100, 101, 99, 100.5) for _ in range(confluence.SCALP_MIN_CANDLES)]
    fake_pattern = {
        "name": "Triangle ascendant", "direction": "haussier", "kind": "continuation",
        "breakoutPrice": 100.0, "extremityPrice": 90.0, "patternHeight": 10.0,
    }
    monkeypatch.setattr(confluence.chart_patterns, "detect_chart_patterns", lambda pivots, trend, price: fake_pattern)
    result = confluence.compute_signal(candles)
    assert result["status"] == "achat"
    assert result["entry"] == candles[-1]["close"]
    assert result["stop_loss"] == pytest.approx(100.0 - confluence.CHARTPATTERN_STOP_BUFFER)
    assert result["take_profit"] == pytest.approx(110.0)
    assert result["pattern"] == fake_pattern


def test_compute_signal_vente_from_bearish_pattern(monkeypatch):
    candles = [_candle(100, 101, 99, 100.5) for _ in range(confluence.SCALP_MIN_CANDLES)]
    fake_pattern = {
        "name": "Triangle descendant", "direction": "baissier", "kind": "continuation",
        "breakoutPrice": 101.0, "extremityPrice": 110.0, "patternHeight": 9.0,
    }
    monkeypatch.setattr(confluence.chart_patterns, "detect_chart_patterns", lambda pivots, trend, price: fake_pattern)
    result = confluence.compute_signal(candles)
    assert result["status"] == "vente"
    assert result["entry"] == candles[-1]["close"]
    assert result["stop_loss"] == pytest.approx(101.0 + confluence.CHARTPATTERN_STOP_BUFFER)
    assert result["take_profit"] == pytest.approx(92.0)


def test_compute_signal_neutre_when_no_pattern_found(monkeypatch):
    candles = [_candle(100, 101, 99, 100.5) for _ in range(confluence.SCALP_MIN_CANDLES)]
    monkeypatch.setattr(confluence.chart_patterns, "detect_chart_patterns", lambda pivots, trend, price: None)
    result = confluence.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None
    assert result["stop_loss"] is None
    assert result["take_profit"] is None


def test_compute_signal_neutre_when_ratio_insufficient(monkeypatch):
    candles = [_candle(100, 101, 99, 100.5) for _ in range(confluence.SCALP_MIN_CANDLES)]
    fake_pattern = {
        "name": "Triangle ascendant", "direction": "haussier", "kind": "continuation",
        "breakoutPrice": 100.0, "extremityPrice": 99.5, "patternHeight": 0.5,
    }
    monkeypatch.setattr(confluence.chart_patterns, "detect_chart_patterns", lambda pivots, trend, price: fake_pattern)
    result = confluence.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None
```

Le test existant `test_compute_signal_neutre_when_too_few_candles` (utilise `_candle` avec seulement 5 bougies) reste VALIDE SANS MODIFICATION — `SCALP_MIN_CANDLES` passe de son ancienne valeur à `2*5+1=11` au Step 5, donc 5 reste bien "trop peu". Ne le touche pas.

Le test existant `test_compute_signal_neutre_during_news_blackout` doit être REMPLACÉ (il dépend de `_build_bearish_then_hammer_candles`, retirée au Step 4) par cette version simplifiée, à mettre à la même place dans le fichier :

```python
def test_compute_signal_neutre_during_news_blackout():
    candles = [_candle(100, 101, 99, 100.5) for _ in range(confluence.SCALP_MIN_CANDLES)]
    candles[-1]["time"] = "2026-09-16 18:00:00"  # décision FOMC du 16/09/2026
    result = confluence.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None
    assert result["stop_loss"] is None
    assert result["take_profit"] is None
```

- [ ] **Step 2 : Vérifier que les nouveaux tests échouent**

Run: `python -m pytest tests/gold_bot/test_confluence.py -k "achat_from_bullish_pattern or vente_from_bearish_pattern or no_pattern_found or ratio_insufficient" -v`
Expected: FAIL (attribut `chart_patterns` n'existe pas encore sur le module `confluence`, `CHARTPATTERN_STOP_BUFFER` n'existe pas encore)

- [ ] **Step 3 : Retirer les constantes de l'ancien moteur, ajouter celles du nouveau**

Dans `gold_bot/confluence.py`, le bloc de constantes (actuellement autour des lignes 244-254) :
```python
SCALP_PIVOT_K = 3
SCALP_RSI_PERIOD = 14
SCALP_MACD_FAST = 12
SCALP_MACD_SLOW = 26
SCALP_MACD_SIGNAL = 9
SCALP_BOLLINGER_PERIOD = 20
SCALP_BOLLINGER_MULT = 2
SCALP_TAKEPROFIT_RISK_MULTIPLE = 1.5
SCALP_LEVEL_PROXIMITY = 0.5
SCALP_MIN_CANDLES = max(SCALP_BOLLINGER_PERIOD, SCALP_MACD_SLOW + SCALP_MACD_SIGNAL) + 1
SCALP_STOP_BUFFER = SCALP_LEVEL_PROXIMITY * 3
```
devient :
```python
import gold_bot.chart_patterns as chart_patterns

SCALP_TAKEPROFIT_RISK_MULTIPLE = 1.5  # INCHANGÉ : seuil minimum du filtre R:R (meets_minimum_risk_reward)
CHARTPATTERN_STOP_BUFFER = chart_patterns.CHARTPATTERN_HEIGHT_TOLERANCE  # réutilise l'échelle existante ($1), pas un nouveau nombre magique
SCALP_MIN_CANDLES = 2 * chart_patterns.CHARTPATTERN_PIVOT_K + 1  # plancher minimal pour qu'un seul pivot soit détectable ; le vrai filtrage (assez de pivots pour une figure complète) est géré par detect_chart_patterns elle-même
```
(Place l'import `gold_bot.chart_patterns` avec les autres imports en haut du fichier si le style du fichier regroupe tous les imports en tête plutôt qu'à ce point précis — suis la convention déjà en place dans ce fichier pour les autres imports.)

- [ ] **Step 4 : Retirer les fonctions de l'ancien moteur**

Dans `gold_bot/confluence.py`, retire entièrement ces fonctions (plus utilisées par personne une fois le Step 6 fait) : `current_levels`, `compute_rsi`, `_ema_last`, `_ema_series`, `compute_macd`, `compute_bollinger`, `_body_size`, `_is_bullish`, `_upper_wick`, `_lower_wick`, `match_candlestick_pattern`.

Dans `tests/gold_bot/test_confluence.py`, retire entièrement leurs tests correspondants : `test_current_levels_picks_nearest_unbroken_pivots`, `test_current_levels_null_when_no_pivot_on_one_side`, `test_compute_rsi_all_gains_is_100`, `test_compute_rsi_all_losses_is_0`, `test_compute_rsi_balanced_alternating_is_50`, `test_compute_macd_constant_offset_on_linear_series`, `test_compute_bollinger_zero_variance`, `test_compute_bollinger_with_variance`, `test_match_candlestick_pattern_marteau`, `test_match_candlestick_pattern_etoile_filante`, `test_match_candlestick_pattern_englobante_haussiere`, `test_match_candlestick_pattern_englobante_baissiere`, `test_match_candlestick_pattern_penetrante`, `test_match_candlestick_pattern_nuage_noir`, `test_match_candlestick_pattern_etoile_du_matin`, `test_match_candlestick_pattern_etoile_du_soir`, `test_match_candlestick_pattern_none_when_no_match`.

Retire aussi entièrement les fonctions `_build_bearish_then_hammer_candles` et `_build_bullish_then_shooting_star_candles` (fixtures de bougies de l'ancien moteur, plus utilisées) et tous les tests qui les utilisaient : `test_compute_signal_full_achat_scenario`, `test_compute_signal_neutre_when_achat_bollinger_not_touched`, `test_compute_signal_achat_when_price_within_bollinger_tolerance`, `test_compute_signal_neutre_when_achat_ratio_insufficient`, `test_compute_signal_full_vente_scenario`, `test_compute_signal_neutre_when_vente_bollinger_not_touched`, `test_compute_signal_vente_when_price_within_bollinger_tolerance`, `test_compute_signal_neutre_when_vente_ratio_insufficient`, `test_compute_signal_neutre_when_no_confluence`.

**Ne retire PAS** : `_candle` (réutilisée par les nouveaux tests du Step 1 et par `test_compute_signal_neutre_when_too_few_candles`), `test_compute_signal_neutre_when_too_few_candles` (reste valide tel quel), `test_fetch_gold_candles_*`, `test_detect_pivots_*`, `test_classify_trend_*`, `test_meets_minimum_risk_reward_*`, `test_is_news_blackout_*` (tous inchangés).

- [ ] **Step 5 : Remplacer `compute_signal`**

Dans `gold_bot/confluence.py`, remplace le corps entier de `compute_signal` par :

```python
def compute_signal(candles: list[dict]) -> dict:
    """Moteur de suivi de tendance par figures chartistes (portage de
    docs/chart_patterns.js::detectChartPatterns, voir gold_bot/chart_patterns.py)
    — remplace le moteur contre-tendance sur chandelier utilisé jusqu'au
    2026-09-29, retiré de ce fichier (confirmé trop passif en production
    réelle : aucun signal en ~36h avec dry_run désactivé, cohérent avec
    le backtest du 27/09 : ~4 trades/90 jours). Ne lève jamais
    d'exception -- `candles` trop court ou aucune figure détectée renvoie
    neutre avec tous les prix à None, de même qu'en pleine fenêtre de
    black-out macro (voir is_news_blackout)."""
    price = candles[-1]["close"] if candles else None
    if not price or len(candles) < SCALP_MIN_CANDLES:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}
    as_of = datetime.fromisoformat(candles[-1]["time"].replace(" ", "T")).replace(tzinfo=timezone.utc)
    if is_news_blackout(as_of):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}

    pivots = detect_pivots(candles, chart_patterns.CHARTPATTERN_PIVOT_K)
    trend = classify_trend(pivots)
    pattern = chart_patterns.detect_chart_patterns(pivots, trend, price)
    if pattern is None:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": trend, "pattern": None}

    direction = "achat" if pattern["direction"] == "haussier" else "vente"
    breakout = pattern["breakoutPrice"]
    height = pattern["patternHeight"]
    if direction == "achat":
        stop_loss = breakout - CHARTPATTERN_STOP_BUFFER
        take_profit = breakout + height
    else:
        stop_loss = breakout + CHARTPATTERN_STOP_BUFFER
        take_profit = breakout - height

    if not meets_minimum_risk_reward(price, stop_loss, take_profit, direction):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": trend, "pattern": None}

    return {"status": direction, "price": price, "entry": price, "stop_loss": stop_loss,
            "take_profit": take_profit, "trend": trend, "pattern": pattern}
```

- [ ] **Step 6 : Vérifier que tous les nouveaux tests passent**

Run: `python -m pytest tests/gold_bot/test_confluence.py -k "achat_from_bullish_pattern or vente_from_bearish_pattern or no_pattern_found or ratio_insufficient or news_blackout or too_few_candles" -v`
Expected: PASS (6 tests)

- [ ] **Step 7 : Faire tourner toute la suite `gold_bot`**

Run: `python -m pytest tests/gold_bot/ -v`
Expected: PASS intégral — aucun test résiduel ne référence une fonction/constante retirée (si `pytest` échoue à la COLLECTE avec une `NameError`/`AttributeError` plutôt qu'un échec d'assertion, un test du Step 4 a été oublié ou une fonction du Step 4 est encore référencée ailleurs — chercher avec `grep -rn "compute_rsi\|compute_macd\|compute_bollinger\|match_candlestick_pattern\|current_levels\|SCALP_PIVOT_K\|SCALP_RSI_PERIOD\|SCALP_MACD_\|SCALP_BOLLINGER_\|SCALP_LEVEL_PROXIMITY\|SCALP_STOP_BUFFER" gold_bot/ tests/gold_bot/` — ne doit plus rien trouver).

- [ ] **Step 8 : Commit**

```bash
git add gold_bot/confluence.py tests/gold_bot/test_confluence.py
git commit -m "Remplace le moteur de signal du bot Or par le suivi de tendance (figures chartistes)"
```
