# Profils de risque (1-5) pour le bot Or — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajoute 5 profils de risque numérotés (1 = très prudent, 5 =
agressif) au bot Or, réglant uniquement `risk_pct` (taille de position)
et `threshold_pct` (seuil du coupe-circuit journalier) — le moteur de
confluence reste strictement identique quel que soit le profil actif.
Un seul profil actif à la fois sur le compte réel existant, changeable
via un nouvel endpoint HTTPS authentifié.

**Architecture:** Un champ `risk_profile` (entier 1-5, défaut 3) dans
`gold_bot/state.json`, résolu à chaque cycle (pas seulement au
démarrage — même contrat que `dry_run`/`kill_switch`) par une nouvelle
fonction `risk_profile_params()` dans `risk.py`, qui alimente
`compute_position_size` et mute `CircuitBreaker.threshold_pct` en place
(l'objet `CircuitBreaker` reste unique par process, il porte l'état du
solde de départ journalier — seul son seuil change). Changeable via
`POST /profile` (même jeton `BOT_API_TOKEN` que `/dashboard`).

**Tech Stack:** Python 3, pytest, FastAPI (déjà en place dans
`gold_bot/`, aucune nouvelle dépendance).

**Spec:** `docs/superpowers/specs/2026-09-15-gold-bot-risk-profiles-design.md`

## Global Constraints

- Échelle des 5 profils, valeurs exactes (spec §"Échelle") :
  - 1 : `risk_pct=0.02`, `threshold_pct=0.05`
  - 2 : `risk_pct=0.035`, `threshold_pct=0.075`
  - 3 (défaut) : `risk_pct=0.05`, `threshold_pct=0.10`
  - 4 : `risk_pct=0.075`, `threshold_pct=0.135`
  - 5 : `risk_pct=0.10`, `threshold_pct=0.175`
- `DEFAULT_RISK_PROFILE = 3` — tout profil invalide (absent, `None`,
  hors de `[1, 5]`, non-entier, **ou booléen** — `bool` est une
  sous-classe d'`int` en Python, `True == 1` résoudrait silencieusement
  vers le profil 1 si non exclu explicitement) replie sur ce défaut,
  jamais d'exception.
- Le moteur de confluence (`gold_bot/confluence.py`, constantes
  `SCALP_*`) ne doit **jamais** être touché par ce plan — tous les
  profils reçoivent exactement les mêmes signaux d'entrée/sortie.
- Aucune nouvelle dépendance.
- Tests lancés depuis la racine du dépôt via `python -m pytest`.
- Chaque fichier modifié doit suivre exactement les conventions déjà en
  place dans ce même fichier (docstrings en tête de module/fonction en
  français, style de test avec `monkeypatch`, etc.) — ce plan modifie
  des fichiers existants, pas des nouveaux modules.
- `gold_bot/api.py` documente aujourd'hui dans son en-tête l'invariant
  « seul `kill_switch` peut être modifié, jamais `dry_run` ». Ce plan
  ajoute une deuxième route de mutation (`risk_profile`) — l'en-tête
  doit être mis à jour pour refléter ce changement d'invariant, pas
  laissé obsolète.

---

### Task 1 : `gold_bot/state.py` — nouveau champ `risk_profile`

**Files:**
- Modify: `gold_bot/state.py`
- Modify: `tests/gold_bot/test_state.py`

**Interfaces:**
- Produces: `load_state()`/`save_state()` gèrent désormais une 3e clé
  `risk_profile` (entier, défaut `3`) en plus de `kill_switch`/`dry_run`
  — même contrat de robustesse (fichier absent/corrompu/non-dict →
  défauts, jamais d'exception). Consommé par la Task 3 (`loop.py`) via
  `current_state.get("risk_profile")`.

- [ ] **Step 1 : Modifier `gold_bot/state.py`**

Dans `load_state()`, ajoute `risk_profile` aux défauts et au merge.
Contrairement à `kill_switch`/`dry_run`, ne force PAS de coercition de
type sur `risk_profile` ici (`bool()` n'aurait aucun sens pour un
entier 1-5) — la validation de plage/type revient à
`risk.risk_profile_params()` (Task 2), qui replie déjà proprement sur
n'importe quelle valeur invalide. `state.py` reste une couche de
stockage dumb, pas de validation dupliquée.

```python
def load_state(path: str = STATE_PATH) -> dict:
    """État de repli si le fichier n'existe pas encore, est illisible,
    ou contient du JSON qui parse mais n'est pas le dict attendu — les
    champs kill_switch/dry_run/risk_profile sont toujours présents en
    sortie, jamais None ou absents. Jamais d'exception au démarrage
    du bot. risk_profile n'est délibérément pas validé ici (type/plage)
    — voir risk.risk_profile_params() qui gère tout profil invalide
    par un repli explicite plutôt qu'une exception."""
    defaults = {"kill_switch": False, "dry_run": True, "risk_profile": 3}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return dict(defaults)
    if not isinstance(data, dict):
        return dict(defaults)
    merged = dict(defaults)
    merged.update(data)
    merged["kill_switch"] = bool(merged.get("kill_switch", False))
    merged["dry_run"] = bool(merged.get("dry_run", True))
    merged["risk_profile"] = merged.get("risk_profile", 3)
    return merged
```

- [ ] **Step 2 : Mettre à jour les 7 tests existants de `tests/gold_bot/test_state.py`**

Chaque assertion `result == {"kill_switch": ..., "dry_run": ...}`
existante doit gagner `"risk_profile": 3` (ou la valeur explicitement
écrite dans le test si le test l'a fournie — aucun test existant ne
fournit `risk_profile`, donc toujours `3` par défaut). Exemple pour les
2 premiers (répéter le même ajout dans les 7) :

```python
def test_load_state_returns_fallback_when_file_absent(tmp_path):
    path = str(tmp_path / "does_not_exist" / "state.json")
    result = state.load_state(path)
    assert result == {"kill_switch": False, "dry_run": True, "risk_profile": 3}


def test_load_state_returns_fallback_on_corrupted_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": False, "dry_run": True, "risk_profile": 3}
```

Applique le même ajout de `"risk_profile": 3` aux 5 autres tests du
fichier (`test_save_state_then_load_state_round_trips` gagne
`risk_profile: 3` à la fois dans l'appel `save_state` et dans
l'assertion ; `test_load_state_merges_partial_data_with_defaults`,
`test_load_state_returns_fallback_when_json_is_not_a_dict`,
`test_save_state_is_atomic_no_tmp_file_left_behind`,
`test_save_state_with_bare_relative_filename_does_not_crash`).

- [ ] **Step 3 : Ajouter un nouveau test pour le merge partiel de `risk_profile`**

```python
def test_load_state_merges_custom_risk_profile(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"risk_profile": 5}), encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": False, "dry_run": True, "risk_profile": 5}
```

- [ ] **Step 4 : Lancer les tests**

Run: `python -m pytest tests/gold_bot/test_state.py -v`
Expected: tous PASS (8 tests : 7 mis à jour + 1 nouveau).

- [ ] **Step 5 : Commit**

```bash
git add gold_bot/state.py tests/gold_bot/test_state.py
git commit -m "feat(gold_bot): ajoute le champ risk_profile a l'etat local"
```

---

### Task 2 : `gold_bot/risk.py` — resolver de profil

**Files:**
- Modify: `gold_bot/risk.py`
- Modify: `tests/gold_bot/test_risk.py`

**Interfaces:**
- Consumes: rien de nouveau (fichier existant, aucune dépendance sur
  Task 1).
- Produces:
  - `RISK_PROFILE_PARAMS: dict[int, dict[str, float]]` — les 5 profils,
    valeurs exactes des Global Constraints.
  - `DEFAULT_RISK_PROFILE: int = 3`
  - `risk_profile_params(profile) -> dict[str, float]` — résout un
    profil (int 1-5) vers `{"risk_pct": float, "threshold_pct": float}`,
    repli sur `DEFAULT_RISK_PROFILE` pour toute valeur invalide (voir
    Global Constraints, y compris le cas booléen). Consommé par la
    Task 3 (`loop.py`).

- [ ] **Step 1 : Écrire les tests (RED)**

Ajoute à `tests/gold_bot/test_risk.py` (à la suite des tests existants,
même style) :

```python
def test_risk_profile_params_profile_1_is_most_conservative():
    assert risk.risk_profile_params(1) == {"risk_pct": 0.02, "threshold_pct": 0.05}


def test_risk_profile_params_profile_3_matches_current_defaults():
    assert risk.risk_profile_params(3) == {"risk_pct": 0.05, "threshold_pct": 0.10}


def test_risk_profile_params_profile_5_is_most_aggressive():
    assert risk.risk_profile_params(5) == {"risk_pct": 0.10, "threshold_pct": 0.175}


def test_risk_profile_params_all_five_profiles_present():
    assert set(risk.RISK_PROFILE_PARAMS.keys()) == {1, 2, 3, 4, 5}


def test_risk_profile_params_falls_back_to_default_when_out_of_range():
    assert risk.risk_profile_params(6) == risk.RISK_PROFILE_PARAMS[3]
    assert risk.risk_profile_params(0) == risk.RISK_PROFILE_PARAMS[3]
    assert risk.risk_profile_params(-1) == risk.RISK_PROFILE_PARAMS[3]


def test_risk_profile_params_falls_back_to_default_when_none():
    assert risk.risk_profile_params(None) == risk.RISK_PROFILE_PARAMS[3]


def test_risk_profile_params_falls_back_to_default_when_not_int():
    assert risk.risk_profile_params("3") == risk.RISK_PROFILE_PARAMS[3]
    assert risk.risk_profile_params(3.0) == risk.RISK_PROFILE_PARAMS[3]


def test_risk_profile_params_falls_back_to_default_for_bool():
    # bool est une sous-classe d'int en Python (True == 1, False == 0) —
    # doit être explicitement exclu pour ne jamais résoudre un booléen
    # mal formé vers un vrai profil numérique.
    assert risk.risk_profile_params(True) == risk.RISK_PROFILE_PARAMS[3]
    assert risk.risk_profile_params(False) == risk.RISK_PROFILE_PARAMS[3]
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/gold_bot/test_risk.py -v -k risk_profile_params`
Expected: FAIL (`AttributeError: module 'gold_bot.risk' has no attribute 'risk_profile_params'`)

- [ ] **Step 3 : Implémenter dans `gold_bot/risk.py`**

Ajoute juste après les imports, avant `compute_position_size` :

```python
RISK_PROFILE_PARAMS: dict[int, dict[str, float]] = {
    1: {"risk_pct": 0.02, "threshold_pct": 0.05},
    2: {"risk_pct": 0.035, "threshold_pct": 0.075},
    3: {"risk_pct": 0.05, "threshold_pct": 0.10},
    4: {"risk_pct": 0.075, "threshold_pct": 0.135},
    5: {"risk_pct": 0.10, "threshold_pct": 0.175},
}
DEFAULT_RISK_PROFILE = 3


def risk_profile_params(profile) -> dict[str, float]:
    """Résout un profil de risque (1-5) vers ses paramètres risk_pct/
    threshold_pct. Tout profil invalide (absent, None, hors plage,
    non-entier, ou booléen — bool est une sous-classe d'int en Python,
    exclue explicitement pour ne jamais résoudre True/False vers un
    profil numérique) replie sur DEFAULT_RISK_PROFILE, jamais
    d'exception — même convention que sector_risk_profile() dans
    indices_score.py."""
    if isinstance(profile, bool) or not isinstance(profile, int) or profile not in RISK_PROFILE_PARAMS:
        profile = DEFAULT_RISK_PROFILE
    return RISK_PROFILE_PARAMS[profile]
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/gold_bot/test_risk.py -v`
Expected: tous PASS (tests existants + 8 nouveaux).

- [ ] **Step 5 : Commit**

```bash
git add gold_bot/risk.py tests/gold_bot/test_risk.py
git commit -m "feat(gold_bot): ajoute le resolver de profils de risque risk_profile_params"
```

---

### Task 3 : `gold_bot/bot.py` + `gold_bot/loop.py` — câblage du profil actif

**Files:**
- Modify: `gold_bot/bot.py`
- Modify: `gold_bot/loop.py`
- Modify: `tests/gold_bot/test_bot.py`
- Modify: `tests/gold_bot/test_loop.py`

**Interfaces:**
- Consumes: `risk.risk_profile_params(profile) -> dict` (Task 2),
  `state.load_state(...)["risk_profile"]` (Task 1, déjà lu via
  `current_state.get("risk_profile")` pour rester robuste aux tests
  existants dont les fixtures `load_state` ne fournissent pas cette
  clé).
- Produces: `bot.decide_and_act(..., risk_pct: float = 0.05)` — nouveau
  paramètre mot-clé optionnel, défaut inchangé (`0.05`, la valeur du
  profil 3) pour ne casser aucun appelant existant qui ne le fournit
  pas.

- [ ] **Step 1 : Écrire le test pour `bot.py` (RED)**

Ajoute à `tests/gold_bot/test_bot.py`, à la suite de
`test_decide_and_act_opens_when_no_existing_position` :

```python
def test_decide_and_act_uses_explicit_risk_pct_when_provided(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    result = bot.decide_and_act(
        [], contract_size=100, balance=10000, open_positions=[],
        circuit_breaker=_open_circuit_breaker(), risk_pct=0.10,
    )
    step = result["steps"][0]
    assert step["volume"] == pytest.approx(2.0)  # (10000*0.10) / (5*100)
```

- [ ] **Step 2 : Vérifier que le test échoue**

Run: `python -m pytest tests/gold_bot/test_bot.py -v -k uses_explicit_risk_pct`
Expected: FAIL (`TypeError: decide_and_act() got an unexpected keyword argument 'risk_pct'`)

- [ ] **Step 3 : Modifier `gold_bot/bot.py`**

Change la signature de `decide_and_act` (ligne ~14-16) :

```python
def decide_and_act(candles: list[dict], *, contract_size: float, balance: float,
                    open_positions: list[dict], circuit_breaker: "risk.CircuitBreaker",
                    symbol: str = "XAUUSD", risk_pct: float = 0.05) -> dict:
```

Et l'appel à `compute_position_size` (ligne ~88) :

```python
    size = risk.compute_position_size(balance, signal["entry"], signal["stop_loss"], contract_size, risk_pct=risk_pct)
```

Complète aussi le docstring de la fonction pour mentionner que
`risk_pct` détermine la taille de position selon le profil de risque
actif (résolu par l'appelant — `decide_and_act` elle-même ne connaît
pas la notion de "profil", juste un `risk_pct` déjà résolu).

- [ ] **Step 4 : Vérifier que le test passe**

Run: `python -m pytest tests/gold_bot/test_bot.py -v`
Expected: tous PASS (tests existants inchangés + le nouveau).

- [ ] **Step 5 : Écrire les tests pour `loop.py` (RED)**

Ajoute à `tests/gold_bot/test_loop.py`, à la suite de
`test_run_cycle_logs_dry_run_without_executing` :

```python
def test_run_cycle_applies_active_risk_profile_to_sizing_and_circuit_breaker(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True, "risk_profile": 5})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [{"close": 2100}])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    captured = {}

    def fake_decide_and_act(*args, **kwargs):
        captured["risk_pct"] = kwargs.get("risk_pct")
        return {"action": "aucune", "reason": "signal neutre"}

    monkeypatch.setattr(loop.bot, "decide_and_act", fake_decide_and_act)

    cb = loop.risk.CircuitBreaker()
    loop.run_cycle("tok", "acc", "td-key", cb)

    assert captured["risk_pct"] == 0.10
    assert cb.threshold_pct == 0.175


def test_run_cycle_defaults_to_profile_3_when_risk_profile_absent_from_state(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [{"close": 2100}])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    captured = {}

    def fake_decide_and_act(*args, **kwargs):
        captured["risk_pct"] = kwargs.get("risk_pct")
        return {"action": "aucune", "reason": "signal neutre"}

    monkeypatch.setattr(loop.bot, "decide_and_act", fake_decide_and_act)

    cb = loop.risk.CircuitBreaker()
    loop.run_cycle("tok", "acc", "td-key", cb)

    assert captured["risk_pct"] == 0.05
    assert cb.threshold_pct == 0.10
```

- [ ] **Step 6 : Vérifier que ces 2 tests échouent**

Run: `python -m pytest tests/gold_bot/test_loop.py -v -k risk_profile`
Expected: FAIL (`AssertionError` — `captured["risk_pct"]` est `None`,
`cb.threshold_pct` reste à sa valeur par défaut de construction `0.10`
au lieu d'avoir été explicitement mis à jour par le cycle).

- [ ] **Step 7 : Modifier `gold_bot/loop.py`**

Dans `run_cycle` (ligne ~137-155), juste après la vérification du
kill switch et avant le bloc `try:`, ajoute la résolution du profil et
mute `circuit_breaker.threshold_pct` en place :

```python
    current_state = state.load_state(state.STATE_PATH)
    if current_state["kill_switch"]:
        decision = {"action": "ignore", "reason": "interrupteur d'urgence activé"}
        _log_decision(decision, path=DECISIONS_LOG_PATH)
        return decision

    # Résolu à chaque cycle (pas seulement au démarrage du process) —
    # même contrat que dry_run/kill_switch : un changement de profil via
    # POST /profile doit prendre effet au cycle suivant, sans redémarrage
    # du service. circuit_breaker est un objet unique et persistant sur
    # toute la durée du while True de main() (il porte l'état du solde
    # de départ journalier) — on mute son threshold_pct en place plutôt
    # que de recréer l'objet, ce qui perdrait cet état.
    profile_params = risk.risk_profile_params(current_state.get("risk_profile"))
    circuit_breaker.threshold_pct = profile_params["threshold_pct"]

    try:
        candles = _with_retry(lambda: confluence.fetch_gold_candles(twelve_data_api_key))
        _save_cache({"candles": candles, "fetched_at": _now_iso()}, LATEST_CANDLES_PATH)
        balance = _with_retry(lambda: broker.get_account_balance(token, account_id, region))
        _save_cache({"balance": balance, "fetched_at": _now_iso()}, LATEST_BALANCE_PATH)
        open_positions = _with_retry(lambda: bot.reconcile_positions(token, account_id, region))
        _save_cache({"positions": open_positions, "fetched_at": _now_iso()}, LATEST_POSITIONS_PATH)
        spec = _with_retry(lambda: broker.get_symbol_specification(token, account_id, symbol, region))
        contract_size = spec["contractSize"]
        decision = bot.decide_and_act(
            candles, contract_size=contract_size, balance=balance,
            open_positions=open_positions, circuit_breaker=circuit_breaker, symbol=symbol,
            risk_pct=profile_params["risk_pct"],
        )
```

(Le reste de `run_cycle` — bloc `except`, ré-vérification avant
exécution, etc. — reste inchangé.)

- [ ] **Step 8 : Vérifier que tous les tests passent**

Run: `python -m pytest tests/gold_bot/ -v`
Expected: tous PASS (aucune régression sur les tests existants de
`test_loop.py`/`test_bot.py`/`test_risk.py`/`test_state.py` — en
particulier les tests dont la fixture `load_state` ne fournit pas
`risk_profile` doivent continuer à passer, `current_state.get(...)`
retournant `None` qui replie proprement sur le profil 3 via
`risk_profile_params`).

- [ ] **Step 9 : Commit**

```bash
git add gold_bot/bot.py gold_bot/loop.py tests/gold_bot/test_bot.py tests/gold_bot/test_loop.py
git commit -m "feat(gold_bot): applique le profil de risque actif a la taille de position et au coupe-circuit"
```

---

### Task 4 : `gold_bot/api.py` — endpoint `POST /profile` + exposition dans `/status`

**Files:**
- Modify: `gold_bot/api.py`
- Modify: `tests/gold_bot/test_api.py`

**Interfaces:**
- Consumes: `state.load_state`/`state.save_state` (Task 1, déjà
  importés dans `api.py`), `risk.RISK_PROFILE_PARAMS` (Task 2 — pour
  valider la plage acceptée par le endpoint).
- Produces: `GET /status` inclut désormais `risk_profile` dans sa
  réponse ; `POST /profile` (nouvelle route, payload JSON
  `{"profile": <int>}`, authentifiée par le même en-tête `X-Bot-Token`
  que les autres routes de mutation) écrit `risk_profile` dans
  `state.json` et le renvoie.

- [ ] **Step 1 : Écrire les tests (RED)**

Ajoute à `tests/gold_bot/test_api.py`, à la suite de
`test_resume_requires_valid_token` :

```python
def test_status_includes_risk_profile(client):
    response = client.get("/status")
    assert response.json()["risk_profile"] == 3


def test_set_profile_requires_valid_token(client):
    response = client.post("/profile", json={"profile": 5}, headers={"X-Bot-Token": "wrong-token"})
    assert response.status_code == 401


def test_set_profile_without_token_header_is_rejected(client):
    response = client.post("/profile", json={"profile": 5})
    assert response.status_code == 401


def test_set_profile_updates_state_and_is_reflected_in_status(client):
    response = client.post("/profile", json={"profile": 5}, headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 200
    assert response.json()["risk_profile"] == 5
    assert client.get("/status").json()["risk_profile"] == 5


def test_set_profile_rejects_out_of_range_value(client):
    response = client.post("/profile", json={"profile": 6}, headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 422


def test_set_profile_rejects_non_integer_value(client):
    response = client.post("/profile", json={"profile": "5"}, headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 422


def test_set_profile_rejects_missing_profile_key(client):
    response = client.post("/profile", json={}, headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 422
```

- [ ] **Step 2 : Vérifier que ces tests échouent**

Run: `python -m pytest tests/gold_bot/test_api.py -v -k "risk_profile or set_profile"`
Expected: FAIL (`test_status_includes_risk_profile` échoue avec
`KeyError`/`None != 3` ; les tests `set_profile` échouent avec
`404 Not Found`, la route n'existe pas encore).

- [ ] **Step 3 : Modifier `gold_bot/api.py`**

Ajoute l'import de `risk` en tête de fichier (après `import
gold_bot.state as state`) :

```python
import gold_bot.risk as risk
```

Modifie `get_status()` pour inclure `risk_profile` :

```python
@app.get("/status")
def get_status():
    current = state.load_state(state.STATE_PATH)
    circuit_breaker_state = state.load_state(CIRCUIT_BREAKER_STATE_PATH)
    return {
        "kill_switch": current["kill_switch"],
        "dry_run": current["dry_run"],
        "risk_profile": current["risk_profile"],
        "circuit_breaker_day": circuit_breaker_state.get("circuit_breaker_day"),
    }
```

Ajoute la nouvelle route, après `resume()` :

```python
@app.post("/profile")
def set_profile(payload: dict, x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    profile = payload.get("profile")
    if isinstance(profile, bool) or not isinstance(profile, int) or profile not in risk.RISK_PROFILE_PARAMS:
        raise HTTPException(status_code=422, detail="profile doit être un entier entre 1 et 5")
    current = state.load_state(state.STATE_PATH)
    current["risk_profile"] = profile
    state.save_state(current, state.STATE_PATH)
    return {"risk_profile": profile}
```

Met à jour le commentaire d'en-tête du fichier (lignes 2-4) pour
refléter le nouvel invariant — remplace :

```
# Point d'accès HTTPS du bot : deux routes de lecture (/status,
# /dashboard) et deux routes de mutation (/kill, /resume — seules
# routes qui changent un état, et seul kill_switch peut être modifié,
# jamais dry_run).
```

par :

```
# Point d'accès HTTPS du bot : deux routes de lecture (/status,
# /dashboard) et trois routes de mutation (/kill, /resume — seules
# routes qui changent kill_switch, jamais dry_run — et /profile, qui
# change risk_profile, un réglage de routine plutôt qu'un interrupteur
# d'urgence, voir
# docs/superpowers/specs/2026-09-15-gold-bot-risk-profiles-design.md).
```

- [ ] **Step 4 : Vérifier que tous les tests passent**

Run: `python -m pytest tests/gold_bot/test_api.py -v`
Expected: tous PASS (tests existants inchangés + 6 nouveaux).

- [ ] **Step 5 : Lancer la suite complète**

Run: `python -m pytest -q`
Expected: tous PASS, aucune régression dans tout le dépôt.

- [ ] **Step 6 : Commit**

```bash
git add gold_bot/api.py tests/gold_bot/test_api.py
git commit -m "feat(gold_bot): expose et permet de changer le profil de risque via POST /profile"
```
