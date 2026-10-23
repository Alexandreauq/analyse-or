# Bot IBKR actions — dimensionnement par le risque, coupe-circuit, cotes — Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le budget fixe de `ibkr_bot` (500€/position, cause confirmée de l'absence totale d'ordre réel sur un compte à ~20€) par un dimensionnement par le risque basé sur le vrai solde ; ajouter un coupe-circuit de perte journalière (classe partagée avec `gold_bot`) ; ajouter une vérification des cotes historiques avant chaque entrée, segmentée par indice/secteur.

**Architecture:** `gold_bot.risk.CircuitBreaker` est extrait vers un nouveau module racine `shared_risk.py` (zéro dépendance sur `gold_bot`/`ibkr_bot`), avec une nouvelle méthode `can_open_position_daily` adaptée à un appelant qui ne vérifie qu'UNE FOIS par jour (contrairement à `gold_bot`, en boucle continue). `ibkr_bot/sizing.py` gagne un dimensionnement par le risque ; `ibkr_bot/portfolio.py::select_entries` généralise son garde-fou de solde à un budget variable par signal (au lieu de la constante fixe) ; un nouveau module `ibkr_bot/odds_check.py` porte en Python la logique de taux de réussite/alpha déjà utilisée côté JavaScript (`docs/index.html::buildSignalStatsHtml`). `ibkr_bot/daily.py::run_batch` câble les trois composants dans sa section "Entrées" existante, sans toucher aux sorties ni à la reconciliation.

**Tech Stack:** Python 3, pytest, aucune nouvelle dépendance.

**Spec:** `docs/superpowers/specs/2026-10-23-ibkr-bot-risk-gate-design.md`

## Global Constraints

- `risk_pct = 0.05` (5%), constante simple, PAS de système de profils 1-5 (décision explicite de la spec).
- `STOP_LOSS_PCT = -20.0` (déjà dans `ibkr_bot/portfolio.py:62`) sert de distance de risque — ne pas le modifier.
- `MAX_NEW_ENTRIES_PER_DAY = 3` (déjà dans `ibkr_bot/portfolio.py:56`) reste inchangé et indépendant du nouveau coupe-circuit.
- `gold_bot/risk.py::CircuitBreaker` doit rester accessible et se comporter IDENTIQUEMENT après l'extraction — tous les tests existants de `tests/gold_bot/test_risk.py` et toute l'intégration (`gold_bot/loop.py`, `gold_bot/bot.py`, `gold_bot/api.py`) passent sans modification.
- Aucune des fonctions pures (`sizing.py`, `portfolio.py`, `odds_check.py`) ne doit faire d'appel réseau ou lire un fichier elle-même — les données déjà récupérées par `daily.py` leur sont toujours passées en paramètre (même règle que le reste du dépôt, voir l'en-tête de `portfolio.py`).

---

## Découverte de terrain (déjà vérifiée en préparant ce plan, importante pour les tâches 2 et 8)

`CircuitBreaker.check()`/`can_open_position()` supposent plusieurs vérifications par jour UTC : la première vérification d'un nouveau jour fixe la référence de perte AU SOLDE DE CE MOMENT-LÀ, puis les vérifications SUIVANTES le même jour comparent à cette référence. `ibkr_bot/daily.py::run_batch` ne tourne qu'**une fois par jour** (pas une boucle continue comme `gold_bot/loop.py`) — avec un seul appel par jour, `check()` fixerait systématiquement la référence À LA VALEUR QU'IL COMPARE DANS LE MÊME APPEL : la perte calculée serait toujours 0, le coupe-circuit ne se déclencherait jamais. C'est pourquoi la Tâche 2 ajoute une méthode dédiée, `can_open_position_daily`, qui compare au solde **persisté lors du appel précédent** (un jour plus tôt) avant d'avancer la référence — jamais aux champs `_day`/`_starting_balance` utilisés par `check()`/`can_open_position()`, pour ne jamais interférer avec l'usage de `gold_bot`.

Aussi vérifié : `docs/signal_tracking.json` ne stocke **pas** de champ `sector` sur les positions (voir `indices_score.py:4579-4600`) — seulement `index`. Le secteur ne peut être obtenu que via le ticker, en le recherchant dans `companies_by_ticker` d'aujourd'hui (`docs/indices.json`). La Tâche 5/7 construit donc une table `ticker_to_sector` au moment de l'appel, pas un champ déjà présent sur chaque position.

---

### Task 1: Extraire `CircuitBreaker` vers `shared_risk.py`

**Files:**
- Create: `shared_risk.py` (racine du dépôt, sibling de `gold_bot/`/`ibkr_bot/`)
- Modify: `gold_bot/risk.py:1-9,104-226` (retirer la classe, ré-exporter)
- Test: `tests/test_shared_risk.py` (nouveau, racine — même convention que `tests/test_indices_score.py`)

**Interfaces:**
- Produces: `shared_risk.CircuitBreaker` (classe, interface identique à l'actuelle `gold_bot.risk.CircuitBreaker` : `__init__(threshold_pct=0.10, now_fn=None, persist_path=None, max_trades_per_day=None)`, `.check(current_balance)`, `.can_open_position(current_balance) -> bool`, `.record_trade_opened()`, attributs publics `.threshold_pct`/`.max_trades_per_day`).
- `gold_bot.risk.CircuitBreaker` reste résolvable exactement comme avant (ré-export) — tout code qui fait `import gold_bot.risk as risk; risk.CircuitBreaker(...)` continue de fonctionner sans changement.

- [ ] **Step 1: Créer `shared_risk.py` avec la classe déplacée telle quelle**

Copier la classe `CircuitBreaker` de `gold_bot/risk.py` (lignes 104-226) verbatim, MAIS remplacer les deux appels à `state.load_state`/`state.save_state` (qui venaient de `import gold_bot.state as state`) par des helpers privés locaux, pour que ce module ne dépende ni de `gold_bot` ni de `ibkr_bot` :

```python
# shared_risk.py
# Coupe-circuit journalier partage entre gold_bot et ibkr_bot -- extrait de
# gold_bot/risk.py (2026-10-23) pour que les deux bots utilisent la MEME
# classe plutot que deux implementations paralleles. gold_bot/risk.py
# re-exporte CircuitBreaker depuis ce module ; tout le reste de risk.py
# (dimensionnement MT5, profils de risque) reste propre a gold_bot.
#
# N'importe NI gold_bot.state NI ibkr_bot.state : les deux sont des copies
# conformes l'une de l'autre (meme interface load_state/save_state), et ce
# module ne doit dependre d'AUCUN des deux packages bots -- sinon l'un
# dependrait indirectement de l'autre pour une simple lecture/ecriture JSON
# generique. _load_state/_save_state ci-dessous sont une troisieme copie
# volontaire de cette meme logique (meme choix que
# ibkr_bot/daily.py::_prix_reference_absent, qui duplique plutot que
# d'importer un symbole prive d'un autre module).
import json
import os
from datetime import date, datetime, timezone


def _load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(data: dict, path: str) -> None:
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


class CircuitBreaker:
    """Coupe-circuit journalier : bloque l'ouverture de nouvelles
    positions si la perte cumulée du jour dépasse threshold_pct du solde
    fixé au début de la journée UTC courante. Ne ferme jamais de
    position existante — seul un filtre sur can_open_position().
    `now_fn` est injectable pour les tests (horloge fixe/contrôlable).
    `persist_path`, si fourni, persiste {day, starting_balance,
    tripped_today} — sans lui, l'état reste en mémoire uniquement (perdu
    au redémarrage), ce qui suffit pour les tests mais pas en
    production, où un redémarrage ne doit pas réinitialiser le seuil du
    jour au solde courant.

    `threshold_pct` est un attribut public mutable, en place par
    l'appelant (gold_bot.loop.run_cycle relit le profil de risque actif
    à chaque cycle). Sans le flag `_tripped_today` ci-dessous, relever le
    profil (donc `threshold_pct`) après un déclenchement du coupe-circuit
    le même jour UTC le dé-déclencherait silencieusement, puisque
    can_open_position() recompare toujours la perte au NOUVEAU seuil,
    plus large. `_tripped_today` fige le verdict "déclenché" pour le
    reste de la journée UTC, indépendamment de tout changement ultérieur
    de threshold_pct — il ne se réinitialise qu'au même moment que
    `_starting_balance`/`_day`, au rollover de jour.

    `check()`/`can_open_position()` supposent plusieurs vérifications
    par jour UTC — voir can_open_position_daily() pour un appelant qui
    ne vérifie qu'une fois par jour (ex. un batch quotidien)."""

    def __init__(self, threshold_pct: float = 0.10, now_fn=None, persist_path=None,
                 max_trades_per_day: int | None = None):
        if not 0 < threshold_pct <= 1:
            raise ValueError("threshold_pct doit être compris entre 0 (exclu) et 1 (inclus)")
        self.threshold_pct = threshold_pct
        self.max_trades_per_day = max_trades_per_day
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._persist_path = persist_path
        self._day = None
        self._starting_balance = None
        self._tripped_today = False
        self._trades_opened_today = 0
        if persist_path is not None:
            saved = _load_state(persist_path)
            saved_day = saved.get("circuit_breaker_day")
            saved_balance = saved.get("circuit_breaker_starting_balance")
            if saved_day is not None and saved_balance is not None:
                try:
                    self._day = date.fromisoformat(saved_day)
                    self._starting_balance = float(saved_balance)
                    self._tripped_today = bool(saved.get("circuit_breaker_tripped_today", False))
                    self._trades_opened_today = int(saved.get("circuit_breaker_trades_opened_today", 0))
                except (ValueError, TypeError):
                    self._day = None
                    self._starting_balance = None
                    self._tripped_today = False
                    self._trades_opened_today = 0

    def check(self, current_balance: float) -> None:
        """À appeler avant toute décision — fixe le solde de référence
        du jour s'il n'existe pas encore, si on a changé de jour UTC, OU
        si le solde de référence actuellement retenu n'est pas positif
        alors que le solde courant l'est (corrige un compte pas encore
        financé au premier cycle du jour). Réinitialise aussi le flag de
        déclenchement dans les deux cas."""
        today = self._now_fn().date()
        needs_new_baseline = self._day != today or (
            self._starting_balance is not None and self._starting_balance <= 0 and current_balance > 0
        )
        if needs_new_baseline:
            self._day = today
            self._starting_balance = current_balance
            self._tripped_today = False
            self._trades_opened_today = 0
            self._persist()

    def _persist(self) -> None:
        if self._persist_path is None:
            return
        _save_state(
            {
                "circuit_breaker_day": self._day.isoformat() if self._day is not None else None,
                "circuit_breaker_starting_balance": self._starting_balance,
                "circuit_breaker_tripped_today": self._tripped_today,
                "circuit_breaker_trades_opened_today": self._trades_opened_today,
            },
            self._persist_path,
        )

    def record_trade_opened(self) -> None:
        """À appeler une fois par trade réellement ouvert (une seule
        fois même si l'ouverture se scinde en plusieurs ordres broker).
        Ne vérifie ni ne déclenche le plafond elle-même."""
        self._trades_opened_today += 1
        if self._day is None:
            return
        self._persist()

    def can_open_position(self, current_balance: float) -> bool:
        self.check(current_balance)
        if self._tripped_today:
            return False
        if self.max_trades_per_day is not None and self._trades_opened_today >= self.max_trades_per_day:
            self._tripped_today = True
            self._persist()
            return False
        loss = self._starting_balance - current_balance
        if loss < self._starting_balance * self.threshold_pct:
            return True
        self._tripped_today = True
        self._persist()
        return False
```

Note : le `self._day.isoformat() if self._day is not None else None` dans
`_persist()` est un changement DÉLIBÉRÉ par rapport au code actuel de
`gold_bot/risk.py` (qui fait `self._day.isoformat()` sans garde) — la
Tâche 2 ajoute un appelant (`can_open_position_daily`) qui peut appeler
`_persist()` alors que `_day` n'a jamais été fixé. Sans ce garde, cet
appel lèverait `AttributeError: 'NoneType' object has no attribute
'isoformat'`. Ce changement est sans risque pour l'usage existant de
`gold_bot` : `_day` y est toujours fixé avant tout appel à `_persist()`
(`check()` le fixe avant de s'appeler elle-même ; `record_trade_opened()`
retourne tôt si `_day is None`, sans jamais atteindre `_persist()`).

- [ ] **Step 2: Réduire `gold_bot/risk.py` à un ré-export**

Remplacer les lignes 104-226 de `gold_bot/risk.py` (toute la classe
`CircuitBreaker`) par :

```python
from shared_risk import CircuitBreaker  # noqa: F401 (re-export — voir docstring de module)
```

Placer cette ligne juste après les imports existants en tête de fichier
(ligne 1-8), PAS à l'emplacement actuel de la classe (104), pour que
l'import soit visible immédiatement. Retirer aussi `from datetime import
date, datetime, timezone` (ligne 5) et `import gold_bot.state as state`
(ligne 7) de `gold_bot/risk.py` : plus rien dans ce fichier ne les
utilise une fois la classe partie (vérifié : `resolve_risk_profile`,
`risk_profile_params`, `compute_position_size`, `round_to_volume_step`
n'utilisent que `math`). `gold_bot/risk.py` garde `import math` et sa
docstring de module existante (ligne 1-3), suivie de la ligne de
ré-export, puis `RISK_PROFILE_PARAMS`, `DEFAULT_RISK_PROFILE`,
`MAX_LEVERAGE`, et les 4 fonctions existantes (lignes 10-101),
inchangées.

- [ ] **Step 3: Vérifier que la suite gold_bot existante passe sans modification**

Run: `pytest tests/gold_bot/ -v`
Expected: PASS intégralement (même nombre de tests qu'avant l'extraction) — aucun test de `tests/gold_bot/test_risk.py`, `test_loop.py`, `test_bot.py` ne doit être modifié pour que ça passe. Si un test échoue, c'est que l'extraction a introduit une différence de comportement : revenir au Step 1 et comparer ligne à ligne avec le code original de `gold_bot/risk.py` (déjà cité intégralement dans ce brief) plutôt que de modifier un test.

- [ ] **Step 4: Écrire un test smoke pour `shared_risk.py` lui-même**

```python
# tests/test_shared_risk.py
from datetime import datetime, timezone

import pytest

import shared_risk


def test_can_open_position_blocks_once_loss_exceeds_threshold():
    cb = shared_risk.CircuitBreaker(
        threshold_pct=0.10, now_fn=lambda: datetime(2026, 10, 23, tzinfo=timezone.utc))
    assert cb.can_open_position(10_000.0) is True   # fixe la reference a 10000
    assert cb.can_open_position(8_900.0) is False   # perte de 11% > seuil de 10%


def test_module_has_no_dependency_on_either_bot_package():
    """Garde-fou architectural (spec : ni gold_bot ni ibkr_bot ne doit
    etre importe par ce module partage) -- shared_risk.py definit ses
    propres _load_state/_save_state plutot que d'importer
    gold_bot.state/ibkr_bot.state (voir le commentaire de tete du
    module)."""
    assert not hasattr(shared_risk, "state")
    assert "gold_bot" not in shared_risk.__file__ and "ibkr_bot" not in shared_risk.__file__
```

- [ ] **Step 5: Run le nouveau test**

Run: `pytest tests/test_shared_risk.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add shared_risk.py gold_bot/risk.py tests/test_shared_risk.py
git commit -m "refactor(risk): extrait CircuitBreaker vers shared_risk.py, partage entre gold_bot et ibkr_bot

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `can_open_position_daily` — coupe-circuit pour un appelant une-fois-par-jour

**Files:**
- Modify: `shared_risk.py` (ajouté à la Tâche 1)
- Test: `tests/test_shared_risk.py`

**Interfaces:**
- Consumes: `shared_risk.CircuitBreaker` (Tâche 1).
- Produces: `CircuitBreaker.can_open_position_daily(current_balance: float) -> bool`, consommée par la Tâche 8 (`ibkr_bot/daily.py`).

- [ ] **Step 1: Écrire les tests qui échouent**

```python
def test_can_open_position_daily_passes_on_the_very_first_call():
    """Demarrage a froid : rien a comparer encore, le coupe-circuit ne
    doit jamais bloquer un premier appel sans historique."""
    cb = shared_risk.CircuitBreaker(threshold_pct=0.10)
    assert cb.can_open_position_daily(10_000.0) is True


def test_can_open_position_daily_trips_on_a_drop_since_the_previous_call(tmp_path):
    path = tmp_path / "cb.json"
    cb1 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    assert cb1.can_open_position_daily(10_000.0) is True   # fixe la reference a 10000

    # Nouveau process le lendemain -- nouvelle instance, meme fichier.
    cb2 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    assert cb2.can_open_position_daily(8_900.0) is False   # perte de 11% > seuil de 10%


def test_can_open_position_daily_passes_on_a_drop_under_the_threshold(tmp_path):
    path = tmp_path / "cb.json"
    cb1 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    cb1.can_open_position_daily(10_000.0)

    cb2 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    assert cb2.can_open_position_daily(9_200.0) is True    # perte de 8% < seuil de 10%


def test_can_open_position_daily_advances_the_reference_each_call(tmp_path):
    """Le 3e appel compare au 2e, pas au 1er -- une baisse lente et
    reguliere (moins de 10%/jour) ne doit jamais s'accumuler en un
    declenchement a retardement."""
    path = tmp_path / "cb.json"
    cb1 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    cb1.can_open_position_daily(10_000.0)
    cb2 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    assert cb2.can_open_position_daily(9_500.0) is True    # -5%, ok
    cb3 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    assert cb3.can_open_position_daily(9_100.0) is True    # -4.2% vs 9500, pas vs 10000


def test_can_open_position_daily_never_touches_check_fields(tmp_path):
    """can_open_position_daily ne doit jamais interferer avec
    check()/can_open_position() (champs dedies _last_balance, distincts
    de _day/_starting_balance/_tripped_today)."""
    path = tmp_path / "cb.json"
    cb = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    cb.can_open_position_daily(10_000.0)
    assert cb._day is None
    assert cb._starting_balance is None
    assert cb._tripped_today is False


def test_can_open_position_daily_does_not_crash_without_a_day_ever_set(tmp_path):
    """_persist() est maintenant aussi appelee par can_open_position_daily,
    qui ne fixe jamais _day -- garde-fou ajoute a la Tache 1 (_persist
    doit tolerer _day=None)."""
    path = tmp_path / "cb.json"
    cb = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    cb.can_open_position_daily(10_000.0)  # ne doit pas lever
    saved = json.loads(path.read_text())
    assert saved["circuit_breaker_day"] is None
```

Ajouter `import json` en tête de `tests/test_shared_risk.py` pour le
dernier test.

- [ ] **Step 2: Run pour vérifier l'échec**

Run: `pytest tests/test_shared_risk.py -v`
Expected: les 6 nouveaux tests FAIL avec `AttributeError: 'CircuitBreaker' object has no attribute 'can_open_position_daily'`.

- [ ] **Step 3: Implémenter**

Dans `shared_risk.py`, `__init__` gagne une ligne juste après
`self._trades_opened_today = 0` :

```python
        self._last_balance = None
```

et, dans le bloc `if persist_path is not None:` existant, APRÈS le bloc
`if saved_day is not None and saved_balance is not None:` (donc
inconditionnellement, indépendamment de ce bloc — `_last_balance` est un
champ séparé) :

```python
            saved_last_balance = saved.get("circuit_breaker_last_balance")
            if saved_last_balance is not None:
                try:
                    self._last_balance = float(saved_last_balance)
                except (TypeError, ValueError):
                    self._last_balance = None
```

`_persist()` gagne une clé dans le dict écrit :

```python
    def _persist(self) -> None:
        if self._persist_path is None:
            return
        _save_state(
            {
                "circuit_breaker_day": self._day.isoformat() if self._day is not None else None,
                "circuit_breaker_starting_balance": self._starting_balance,
                "circuit_breaker_tripped_today": self._tripped_today,
                "circuit_breaker_trades_opened_today": self._trades_opened_today,
                "circuit_breaker_last_balance": self._last_balance,
            },
            self._persist_path,
        )
```

Nouvelle méthode, ajoutée après `can_open_position` :

```python
    def can_open_position_daily(self, current_balance: float) -> bool:
        """Coupe-circuit pour un appelant qui ne vérifie qu'UNE FOIS par
        période (ex. ibkr_bot/daily.py, un batch par jour) — check() et
        can_open_position() supposent plusieurs vérifications par jour
        UTC (voir leur docstring) : avec un seul appel par jour, check()
        fixerait systématiquement la référence À LA VALEUR QU'IL COMPARE
        DANS LE MÊME APPEL, rendant le coupe-circuit muet pour toujours.

        Compare `current_balance` à la valeur persistée lors du DERNIER
        appel (champ dédié `_last_balance`, jamais `_starting_balance`/
        `_day` — deux concepts indépendants, pour ne jamais interférer
        avec check()/can_open_position() si jamais utilisés sur le même
        objet), PUIS avance la référence à `current_balance` pour le
        prochain appel. Premier appel jamais vu (démarrage à froid) :
        fixe la référence et laisse passer, rien à comparer encore --
        même convention que _starting_balance au premier cycle de
        check(). Pas de latch `_tripped_today` ici : un seul appel par
        jour, rien à latcher jusqu'au lendemain."""
        if self._last_balance is None:
            self._last_balance = current_balance
            self._persist()
            return True
        loss = self._last_balance - current_balance
        tripped = loss >= self._last_balance * self.threshold_pct
        self._last_balance = current_balance
        self._persist()
        return not tripped
```

- [ ] **Step 4: Run pour vérifier le succès**

Run: `pytest tests/test_shared_risk.py -v`
Expected: PASS (tous les tests, anciens et nouveaux)

- [ ] **Step 5: Vérifier la non-régression gold_bot**

Run: `pytest tests/gold_bot/ -v`
Expected: PASS intégralement (ce changement n'a touché que des champs/méthodes nouveaux, jamais lus par le code existant).

- [ ] **Step 6: Commit**

```bash
git add shared_risk.py tests/test_shared_risk.py
git commit -m "feat(risk): ajoute can_open_position_daily pour un coupe-circuit verifie une fois par jour

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `ibkr_bot/sizing.py` — dimensionnement par le risque

**Files:**
- Modify: `ibkr_bot/sizing.py:22,87-146`
- Test: `tests/ibkr_bot/test_sizing.py`

**Interfaces:**
- Consumes: rien de nouveau (module déjà autonome).
- Produces: `sizing.RISK_PCT` (constante, `0.05`) ; `plan["budget_eur_nominal"]` (nouvelle clé du dict renvoyé par `compute_quantity` ET `compute_quantity_by_risk`) ; `sizing.compute_quantity_by_risk(ticker, index_currency, unit_price_indices, fx_rate, balance_eur, risk_pct, stop_loss_pct) -> dict` — consommées par les Tâches 4 et 6.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `tests/ibkr_bot/test_sizing.py` :

```python
# --- budget_eur_nominal (garde-fou de solde generalise, Tache 4) ------

def test_compute_quantity_stores_the_nominal_eur_budget_used():
    plan = sizing.compute_quantity("MC.PA", "EUR", 415.0, 1.0)
    assert plan["budget_eur_nominal"] == pytest.approx(sizing.BUDGET_EUR)


def test_compute_quantity_stores_a_custom_nominal_eur_budget():
    plan = sizing.compute_quantity("MC.PA", "EUR", 415.0, 1.0, budget_eur=50.0)
    assert plan["budget_eur_nominal"] == pytest.approx(50.0)


def test_compute_quantity_stores_zero_nominal_budget_on_invalid_price():
    plan = sizing.compute_quantity("MC.PA", "EUR", float("nan"), 1.0)
    assert plan["budget_eur_nominal"] == 0.0
    assert plan["motif"] == "prix_ou_taux_invalide"


def test_compute_quantity_stores_the_nominal_budget_even_when_price_exceeds_it():
    """budget_eur_nominal est rempli AVANT le controle de quantite<1
    (meme motif que budget_converti/prix_unitaire_cotation) -- utile
    pour l'audit meme quand le signal est finalement rejete."""
    plan = sizing.compute_quantity("MC.PA", "EUR", 100_000.0, 1.0)
    assert plan["motif"] == "signal_ignore_prix_unitaire_superieur_au_budget"
    assert plan["budget_eur_nominal"] == pytest.approx(sizing.BUDGET_EUR)


# --- compute_quantity_by_risk -----------------------------------------

def test_compute_quantity_by_risk_scales_budget_with_balance_and_stop():
    """20 EUR de solde, risque 5%, stop a 20% -> budget nominal de
    20*0.05/0.20 = 5 EUR (resout la sous-capitalisation : un budget qui
    suit le vrai solde plutot qu'une constante fixe)."""
    plan = sizing.compute_quantity_by_risk(
        "MC.PA", "EUR", 4.0, 1.0, balance_eur=20.0, risk_pct=0.05, stop_loss_pct=-20.0)
    assert plan["budget_eur_nominal"] == pytest.approx(5.0)
    assert plan["quantite"] == 1  # 5 EUR / 4 EUR = 1.25 -> 1


def test_compute_quantity_by_risk_matches_compute_quantity_given_the_same_budget():
    """Sanity check : pour un budget equivalent, compute_quantity_by_risk
    et compute_quantity produisent le meme plan (meme fonction en
    dessous). balance_eur*risk_pct/(abs(stop)/100) = 100*0.05/0.05 =
    100 EUR, exactement le budget_eur passe a compute_quantity."""
    direct = sizing.compute_quantity("MC.PA", "EUR", 415.0, 1.0, budget_eur=100.0)
    via_risk = sizing.compute_quantity_by_risk(
        "MC.PA", "EUR", 415.0, 1.0, balance_eur=100.0, risk_pct=0.05, stop_loss_pct=-5.0)
    assert via_risk["budget_eur_nominal"] == pytest.approx(direct["budget_eur_nominal"])
    assert via_risk["quantite"] == direct["quantite"]


def test_compute_quantity_by_risk_invalid_balance_returns_a_dedicated_reason():
    plan = sizing.compute_quantity_by_risk(
        "MC.PA", "EUR", 415.0, 1.0, balance_eur=0.0, risk_pct=0.05, stop_loss_pct=-20.0)
    assert plan["quantite"] == 0
    assert plan["motif"] == "budget_risque_invalide"


def test_compute_quantity_by_risk_invalid_risk_pct_returns_a_dedicated_reason():
    plan = sizing.compute_quantity_by_risk(
        "MC.PA", "EUR", 415.0, 1.0, balance_eur=1000.0, risk_pct=0.0, stop_loss_pct=-20.0)
    assert plan["motif"] == "budget_risque_invalide"


def test_compute_quantity_by_risk_zero_stop_distance_returns_a_dedicated_reason():
    plan = sizing.compute_quantity_by_risk(
        "MC.PA", "EUR", 415.0, 1.0, balance_eur=1000.0, risk_pct=0.05, stop_loss_pct=0.0)
    assert plan["motif"] == "budget_risque_invalide"
```

- [ ] **Step 2: Run pour vérifier l'échec**

Run: `pytest tests/ibkr_bot/test_sizing.py -v`
Expected: FAIL (`KeyError: 'budget_eur_nominal'` puis `AttributeError:
module 'ibkr_bot.sizing' has no attribute 'compute_quantity_by_risk'`).

- [ ] **Step 3: Implémenter**

Dans `ibkr_bot/sizing.py`, après `BUDGET_EUR = 500.0` (ligne 22) :

```python
RISK_PCT = 0.05             # dimensionnement par le risque (spec 2026-10-23)
```

Dans `compute_quantity` (ligne 87-146), ajouter `"budget_eur_nominal":
0.0,` au dict `plan` initial (ligne 100-110, à côté des autres clés à
0.0), puis juste après la ligne `plan["prix_unitaire_cotation"] =
prix_cotation` (ligne 126) :

```python
    plan["budget_eur_nominal"] = budget_eur
```

Nouvelle fonction, ajoutée en fin de fichier :

```python
def compute_quantity_by_risk(
    ticker: str, index_currency: str, unit_price_indices: float,
    fx_rate: float, balance_eur: float, risk_pct: float, stop_loss_pct: float,
) -> dict:
    """Variante de compute_quantity dont le budget n'est plus une
    constante fixe mais derive du VRAI solde : le budget nominal en EUR
    engage sur ce signal vaut (balance_eur * risk_pct) / (distance de
    stop en %) -- la perte si le stop est touche vaut alors exactement
    balance_eur * risk_pct, quel que soit le solde reel du compte.
    Resout au meme endroit la sous-capitalisation connue (BUDGET_EUR a
    500 EUR sur un compte reel a ~20 EUR, aucun ordre reel jamais passe)
    et l'absence de lien entre le stop-loss (deja utilise en SORTIE,
    voir portfolio.STOP_LOSS_PCT) et la taille de position a l'ENTREE.

    `stop_loss_pct` est une distance en % (ex. -20.0, voir
    portfolio.STOP_LOSS_PCT) -- seule sa valeur absolue compte ici, le
    signe n'a pas a etre coherent avec celui de portfolio.py.
    `budget_risque_invalide` (motif dedie, jamais confondu avec
    prix_ou_taux_invalide qui vient de compute_quantity) si balance_eur,
    risk_pct ou abs(stop_loss_pct) n'est pas un nombre strictement
    positif fini -- division par zero ou budget negatif evites
    explicitement plutot que de laisser l'exception remonter."""
    plan = {
        "ticker": ticker,
        "quantite": 0,
        "devise_cotation": quotation_currency(index_currency, ticker),
        "devise_compte": index_currency,
        "taux_de_change": fx_rate if _is_positive_number(fx_rate) else 0.0,
        "budget_converti": 0.0,
        "prix_unitaire_cotation": 0.0,
        "cout_estime_devise_compte": 0.0,
        "budget_eur_nominal": 0.0,
        "motif": None,
    }
    if not (_is_positive_number(balance_eur) and _is_positive_number(risk_pct)
            and _is_positive_number(abs(stop_loss_pct))):
        plan["motif"] = "budget_risque_invalide"
        return plan
    budget_eur = balance_eur * risk_pct / (abs(stop_loss_pct) / 100)
    return compute_quantity(
        ticker, index_currency, unit_price_indices, fx_rate, budget_eur=budget_eur)
```

- [ ] **Step 4: Run pour vérifier le succès**

Run: `pytest tests/ibkr_bot/test_sizing.py -v`
Expected: PASS (tous les tests, anciens et nouveaux)

- [ ] **Step 5: Commit**

```bash
git add ibkr_bot/sizing.py tests/ibkr_bot/test_sizing.py
git commit -m "feat(ibkr-bot): ajoute compute_quantity_by_risk, dimensionnement par le risque base sur le vrai solde

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `ibkr_bot/portfolio.py::select_entries` — garde-fou de solde généralisé

**Files:**
- Modify: `ibkr_bot/portfolio.py:153-228`
- Test: `tests/ibkr_bot/test_portfolio.py:25-36,654-685`

**Interfaces:**
- Consumes: `plan["budget_eur_nominal"]` (Tâche 3).
- Produces: `select_entries` accepte désormais des plans à budget variable (plus de contrainte "chaque position engage exactement BUDGET_EUR") — consommée telle quelle par la Tâche 8 (aucun changement de signature).

- [ ] **Step 1: Mettre à jour le helper de test `_plan()`**

Dans `tests/ibkr_bot/test_portfolio.py`, ligne 25-36, ajouter un
paramètre et une clé :

```python
def _plan(ticker, quantite=5, devise_compte="EUR", cout=500.0, motif=None,
          budget_eur_nominal=500.0):
    return {
        "ticker": ticker,
        "quantite": quantite,
        "devise_cotation": devise_compte,
        "devise_compte": devise_compte,
        "taux_de_change": 1.0,
        "budget_converti": 500.0,
        "prix_unitaire_cotation": 100.0,
        "cout_estime_devise_compte": cout,
        "budget_eur_nominal": budget_eur_nominal,
        "motif": motif,
    }
```

`budget_eur_nominal=500.0` par défaut == `portfolio.BUDGET_EUR`
(500.0) : tous les tests existants qui n'overrident pas ce paramètre
continuent de tester exactement le même scénario qu'avant (budget fixe)
sans qu'aucun de leurs asserts n'ait besoin de changer.

- [ ] **Step 2: Écrire le nouveau test qui échoue**

Ajouter après `test_select_entries_accepts_a_cost_exactly_equal_to_the_base_cash`
(ligne 676-684) :

```python
def test_select_entries_uses_the_per_signal_risk_based_budget_for_the_balance_guard():
    """Avec un budget variable par signal (dimensionnement par le
    risque, Tache 3), le garde-fou de solde doit sommer le budget REEL
    de chaque signal, pas une constante fixe -- deux signaux a 5 EUR
    chacun doivent passer sur un solde de 10 EUR, la que l'ancien
    comportement (BUDGET_EUR=500 fixe) les aurait tous les deux
    rejetes."""
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 26.9)]
    plans = {
        "A.PA": _plan("A.PA", budget_eur_nominal=5.0),
        "B.PA": _plan("B.PA", budget_eur_nominal=5.0),
    }
    contrats = _contrats(["A.PA", "B.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, contrats, base_cash=10.0)

    assert [r["signal"]["ticker"] for r in retenus] == ["A.PA", "B.PA"]
    assert rejets == []


def test_select_entries_rejects_when_the_per_signal_budget_exceeds_cash():
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 26.9)]
    plans = {
        "A.PA": _plan("A.PA", budget_eur_nominal=8.0),
        "B.PA": _plan("B.PA", budget_eur_nominal=8.0),
    }
    contrats = _contrats(["A.PA", "B.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, contrats, base_cash=10.0)

    assert [r["signal"]["ticker"] for r in retenus] == ["A.PA"]
    assert rejets == [{"ticker": "B.PA", "rang": 2, "score": 26.9,
                       "raison": "solde_insuffisant"}]
```

- [ ] **Step 3: Run pour vérifier l'échec**

Run: `pytest tests/ibkr_bot/test_portfolio.py -v -k risk_based_budget or per_signal_budget`
Expected: FAIL — les deux nouveaux tests échouent (`select_entries`
utilise encore la constante `BUDGET_EUR`, pas `budget_eur_nominal`),
mais aucun test EXISTANT ne doit avoir changé de statut à cette étape
(seul `_plan()` a changé, avec un défaut qui préserve le comportement
actuel).

Run aussi : `pytest tests/ibkr_bot/test_portfolio.py -v` pour confirmer
que les tests existants passent toujours avec le `_plan()` modifié.

- [ ] **Step 4: Implémenter**

Dans `ibkr_bot/portfolio.py`, `select_entries` (lignes 177-238), dans la
boucle `for rang, signal in enumerate(...)`, remplacer :

```python
        if base_cash - engage < BUDGET_EUR:
            rejets.append({**base, "raison": "solde_insuffisant"})
            continue

        engage += BUDGET_EUR
```

par :

```python
        budget_signal = plan.get("budget_eur_nominal", BUDGET_EUR)
        if base_cash - engage < budget_signal:
            rejets.append({**base, "raison": "solde_insuffisant"})
            continue

        engage += budget_signal
```

Mettre à jour le paragraphe "GARDE-FOU DE SOLDE" de la docstring
(lignes 153-165) pour refléter le budget désormais variable :

```
    GARDE-FOU DE SOLDE (spec 9.9, revu 2026-10-23 pour un budget
    variable par signal) : IBKR convertit automatiquement le budget EUR
    vers la devise locale au moment de l'achat (mecanisme IDEAL, spec
    9.1) -- il n'est donc pas necessaire de detenir du cash deja converti
    dans la devise de chaque signal. Le garde-fou compare a la place le
    cash total dans la devise de BASE du compte (`base_cash`,
    typiquement gateway.base_currency_cash()) au budget cumule engage au
    fil du classement. Chaque position retenue engage au plus
    plan["budget_eur_nominal"] (le budget NOMINAL en EUR qui a servi a
    calculer la quantite -- fixe (BUDGET_EUR) ou base sur le risque
    (sizing.compute_quantity_by_risk), selon ce que l'appelant a passe a
    sizing.py) : utiliser ce montant fixe par position, plutot que
    `cout_estime_devise_compte` (dans une devise etrangere, non
    directement comparable a `base_cash` sans un taux de conversion que
    cette fonction n'a pas), reste le choix conservateur et simple
    retenu ici -- seule la SOURCE du montant a change, pas la logique du
    garde-fou.
```

- [ ] **Step 5: Run pour vérifier le succès**

Run: `pytest tests/ibkr_bot/test_portfolio.py -v`
Expected: PASS (tous les tests, anciens et nouveaux)

- [ ] **Step 6: Commit**

```bash
git add ibkr_bot/portfolio.py tests/ibkr_bot/test_portfolio.py
git commit -m "feat(ibkr-bot): select_entries utilise le budget nominal par signal plutot que la constante BUDGET_EUR

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `ibkr_bot/odds_check.py` (nouveau) — vérification des cotes historiques

**Files:**
- Create: `ibkr_bot/odds_check.py`
- Test: `tests/ibkr_bot/test_odds_check.py` (nouveau)

**Interfaces:**
- Consumes: positions de `docs/signal_tracking.json` telles que renvoyées par `ibkr_bot.signals.load_signal_tracking` (champs utilisés : `status`, `ticker`, `index`, `return_pct`, `index_return_pct` — `sector` n'existe PAS sur ces positions, voir "Découverte de terrain" en tête de ce plan).
- Produces: `odds_check.historical_odds_for_signal(positions, ticker_to_sector, index, sector, min_sample_size=20) -> dict` — consommée par la Tâche 7.

- [ ] **Step 1: Écrire les tests qui échouent**

```python
# tests/ibkr_bot/test_odds_check.py
import ibkr_bot.odds_check as odds_check


def _closed(ticker, index, return_pct, index_return_pct):
    return {"ticker": ticker, "status": "closed", "index": index,
            "return_pct": return_pct, "index_return_pct": index_return_pct}


def _open(ticker, index):
    return {"ticker": ticker, "status": "open", "index": index,
            "return_pct": None, "index_return_pct": None}


def test_insufficient_history_when_both_segments_are_below_the_minimum():
    positions = [_closed("A.PA", "CAC40", 5.0, 2.0) for _ in range(5)]
    ticker_to_sector = {"A.PA": "Luxe"}

    verdict = odds_check.historical_odds_for_signal(
        positions, ticker_to_sector, "CAC40", "Luxe", min_sample_size=20)

    assert verdict["verdict"] == "insufficient_history"
    assert verdict["segment_used"] is None


def test_favorable_verdict_when_the_index_segment_has_positive_average_alpha():
    positions = [_closed(f"T{i}.PA", "CAC40", 10.0, 2.0) for i in range(25)]
    ticker_to_sector = {}

    verdict = odds_check.historical_odds_for_signal(
        positions, ticker_to_sector, "CAC40", "", min_sample_size=20)

    assert verdict["verdict"] == "favorable"
    assert verdict["segment_used"] == "index"
    assert verdict["sample_size"] == 25


def test_unfavorable_verdict_when_the_index_segment_has_negative_average_alpha():
    positions = [_closed(f"T{i}.PA", "CAC40", -3.0, 2.0) for i in range(25)]

    verdict = odds_check.historical_odds_for_signal(
        positions, {}, "CAC40", "", min_sample_size=20)

    assert verdict["verdict"] == "unfavorable"


def test_zero_average_alpha_is_unfavorable_not_favorable():
    """La spec est explicite : alpha moyen 'negatif OU NUL' -> defavorable."""
    positions = [_closed(f"T{i}.PA", "CAC40", 2.0, 2.0) for i in range(25)]  # alpha = 0

    verdict = odds_check.historical_odds_for_signal(
        positions, {}, "CAC40", "", min_sample_size=20)

    assert verdict["verdict"] == "unfavorable"


def test_falls_back_to_sector_when_the_index_segment_is_too_small():
    index_positions = [_closed(f"I{i}.PA", "CAC40", 5.0, 2.0) for i in range(3)]
    sector_positions = [_closed(f"S{i}.DE", "DAX", 8.0, 1.0) for i in range(22)]
    ticker_to_sector = {p["ticker"]: "Tech" for p in sector_positions}
    positions = index_positions + sector_positions

    verdict = odds_check.historical_odds_for_signal(
        positions, ticker_to_sector, "CAC40", "Tech", min_sample_size=20)

    assert verdict["verdict"] == "favorable"
    assert verdict["segment_used"] == "sector"
    assert verdict["sample_size"] == 22


def test_open_positions_are_excluded_from_every_segment():
    positions = [_open(f"T{i}.PA", "CAC40") for i in range(25)]

    verdict = odds_check.historical_odds_for_signal(
        positions, {}, "CAC40", "", min_sample_size=20)

    assert verdict["verdict"] == "insufficient_history"
    assert verdict["sample_size"] == 0


def test_blank_sector_never_matches_anything():
    positions = [_closed(f"T{i}.PA", "AUTRE_INDICE", 10.0, 2.0) for i in range(25)]
    ticker_to_sector = {p["ticker"]: "" for p in positions}

    verdict = odds_check.historical_odds_for_signal(
        positions, ticker_to_sector, "CAC40", "", min_sample_size=20)

    assert verdict["verdict"] == "insufficient_history"
```

- [ ] **Step 2: Run pour vérifier l'échec**

Run: `pytest tests/ibkr_bot/test_odds_check.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'ibkr_bot.odds_check'`)

- [ ] **Step 3: Implémenter**

```python
# ibkr_bot/odds_check.py
# Verification des cotes historiques avant une entree, portee en Python
# depuis la seule logique agregee existante (docs/index.html::
# buildSignalStatsHtml, en JavaScript cote client). Segmente par
# indice/secteur (spec 2026-10-23) plutot que sur l'esperance globale,
# deliberement moins negative/plus discriminante -- voir la spec pour
# le raisonnement complet (le diagnostic global connu est legerement
# negatif, segmenter laisse une chance a un sous-ensemble meilleur que
# la moyenne au lieu de neutraliser le bot d'entree de jeu).
#
# N'utilise QUE les positions CLOTUREES (status == "closed") : une
# position encore ouverte n'a pas d'issue reelle a apprendre d'elle --
# contrairement a buildSignalStatsHtml, qui valorise aussi les positions
# ouvertes au cours du jour pour l'AFFICHAGE (un besoin d'UI, pas une
# donnee d'entrainement). Melanger les deux biaiserait la verification
# historique avec des rendements non definitifs.
#
# docs/signal_tracking.json ne stocke PAS de champ "sector" sur les
# positions (seulement "index") -- `ticker_to_sector` est construit par
# l'appelant (ibkr_bot/daily.py) a partir de docs/indices.json du jour.
def historical_odds_for_signal(
    positions: list[dict], ticker_to_sector: dict[str, str],
    index: str, sector: str, min_sample_size: int = 20,
) -> dict:
    """Cotes historiques (alpha moyen vs indice) pour un signal du meme
    indice, ou a defaut du meme secteur si l'echantillon par indice est
    trop petit. Renvoie {"verdict": "insufficient_history" |
    "favorable" | "unfavorable", "segment_used": "index" | "sector" |
    None, "sample_size": int, "avg_alpha_pct": float | None}.

    REGLE DE VERDICT : alpha moyen negatif OU NUL -> "unfavorable"
    (bloque le trade) ; sinon "favorable". Si NI l'indice NI le secteur
    n'atteignent min_sample_size, "insufficient_history" -- ne bloque
    JAMAIS le trade (un echantillon trop petit ne permet de conclure
    dans aucun sens, voir le meme choix pour gold_bot.odds_check)."""
    closed = [
        p for p in positions
        if p.get("status") == "closed"
        and p.get("return_pct") is not None
        and p.get("index_return_pct") is not None
    ]
    by_index = [p for p in closed if p.get("index") == index]
    if len(by_index) >= min_sample_size:
        segment_used, sample = "index", by_index
    else:
        by_sector = (
            [p for p in closed if sector and ticker_to_sector.get(p["ticker"]) == sector]
        )
        if len(by_sector) >= min_sample_size:
            segment_used, sample = "sector", by_sector
        else:
            return {
                "verdict": "insufficient_history", "segment_used": None,
                "sample_size": max(len(by_index), len(by_sector)),
                "avg_alpha_pct": None,
            }

    alphas = [p["return_pct"] - p["index_return_pct"] for p in sample]
    avg_alpha_pct = sum(alphas) / len(alphas)
    verdict = "unfavorable" if avg_alpha_pct <= 0 else "favorable"
    return {
        "verdict": verdict, "segment_used": segment_used,
        "sample_size": len(sample), "avg_alpha_pct": avg_alpha_pct,
    }
```

- [ ] **Step 4: Run pour vérifier le succès**

Run: `pytest tests/ibkr_bot/test_odds_check.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ibkr_bot/odds_check.py tests/ibkr_bot/test_odds_check.py
git commit -m "feat(ibkr-bot): ajoute odds_check.historical_odds_for_signal, verification des cotes segmentee indice/secteur

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Câbler le dimensionnement par le risque dans `daily.py`

**Files:**
- Modify: `ibkr_bot/daily.py:896-900`
- Test: `tests/ibkr_bot/test_daily.py`

**Interfaces:**
- Consumes: `sizing.compute_quantity_by_risk`, `sizing.RISK_PCT` (Tâche 3) ; `portfolio.STOP_LOSS_PCT` (déjà existant, `ibkr_bot/portfolio.py:62`).

- [ ] **Step 1: Lire le test existant qui construit l'environnement de `run_batch`**

Lire `tests/ibkr_bot/test_daily.py`, fixture `env` (autour de la ligne
126-153) et les tests de la section "entrées" existants, pour réutiliser
exactement la même structure de `docs/indices.json`/`FausseGateway` que
les tests déjà présents, plutôt que d'en inventer une nouvelle.

- [ ] **Step 2: Écrire le test qui échoue**

Ajouter un test qui vérifie que la quantité achetée dépend désormais du
solde réel plutôt que d'un budget fixe à 500€ — par exemple, avec un
`base_cash` délibérément petit (reproduisant le cas réel de
sous-capitalisation), vérifier qu'un ordre est tout de même tenté (alors
qu'avec l'ancien `compute_quantity`/`BUDGET_EUR=500`, un solde de 20€
aurait fait rejeter TOUT signal en `solde_insuffisant` avant même
d'atteindre `select_entries`, puisque `budget_converti` -- calculé à
partir de 500€ -- aurait largement dépassé un prix unitaire réaliste
seulement si le prix est cher ; choisir un prix unitaire bas, ex. 4€,
pour qui l'ancien ET le nouveau calcul achèteraient une quantité non
nulle, mais DIFFÉRENTE, rend la différence de comportement observable
sans dépendre d'un rejet). Suivre le style déjà utilisé par les tests
"entrées" de `test_daily.py` (mode `dry_run`, `FausseGateway` dont
`base_currency_cash` renvoie une petite valeur, un seul signal/company
dans `docs/indices.json`) :

```python
def test_run_batch_sizes_the_entry_by_risk_based_budget_not_a_fixed_500(env):
    """Avec un solde de 20 EUR (reproduit le cas reel de
    sous-capitalisation), le budget nominal engage doit etre
    20*RISK_PCT/0.20 = 5 EUR (RISK_PCT=0.05), pas 500 EUR -- la quantite
    achetee pour un prix unitaire de 4 EUR doit donc etre 1 (5/4=1.25),
    jamais 125 (l'ancien calcul, 500/4)."""
    (env["tmp"] / "docs" / "indices.json").write_text(json.dumps({
        "updated": "2026-10-23",
        "index_currency": {"CAC40": "EUR"},
        "companies": [{"ticker": "A.PA", "index": "CAC40", "sector": "Luxe",
                       "score": 50.0, "current_price": 4.0, "stage_label": None}],
    }), encoding="utf-8")
    (env["tmp"] / "docs" / "signal_tracking.json").write_text(json.dumps({
        "positions": [{"id": "A.PA-2026-10-23", "ticker": "A.PA", "name": "A",
                       "index": "CAC40", "status": "open", "entry_date": "2026-10-23",
                       "entry_price": 4.0, "target_exit_price": 5.0}],
    }), encoding="utf-8")

    class FausseGateway:
        DEFAULT_GATEWAY_URL = "http://localhost"
        def connect(self, *a, **k): return True
        def is_authenticated(self, *a, **k): return True
        def brokerage_accounts(self, *a, **k): return []
        def portfolio_accounts(self, *a, **k): return []
        def disconnect(self): pass
        def positions(self, *a, **k): return []
        def base_currency_cash(self, *a, **k): return 20.0
        def exchange_rate(self, *a, **k): return 1.0
        def search_contract(self, *a, **k): return [{"conid": 1, "currency": "EUR"}]
        def contract_info(self, *a, **k): return {"conid": 1, "currency": "EUR"}

    run = daily.run_batch(today="2026-10-23", gw=FausseGateway(),
                          sleep_fn=lambda s: None, paths=env["paths"])

    assert len(run["entrees"]) == 1
    assert run["entrees"][0]["quantite"] == 1
```

Adapter les noms de champs exacts (`run["entrees"][0]["quantite"]`,
format de `journal.build_position_record`, nom de champ `"currency"` vs
`"index_currency"`, etc.) à ce que `tests/ibkr_bot/test_daily.py`
utilise déjà dans ses tests "entrées" existants (lire 2-3 tests voisins
avant d'écrire celui-ci, pour matcher exactement leur convention de
fixture plutôt que de deviner).

- [ ] **Step 3: Run pour vérifier l'échec**

Run: `pytest tests/ibkr_bot/test_daily.py -k risk_based_budget -v`
Expected: FAIL — `run["entrees"][0]["quantite"]` vaut encore une
quantité dimensionnée sur `BUDGET_EUR=500` (donc probablement rejetée
en `solde_insuffisant`, ou une quantité différente de 1), pas encore
câblée sur `compute_quantity_by_risk`.

- [ ] **Step 4: Implémenter**

Dans `ibkr_bot/daily.py`, lignes 896-900, remplacer :

```python
        plans[ticker] = sizing.compute_quantity(
            ticker, signal["currency"], signal["current_price"], taux)
```

par :

```python
        plans[ticker] = sizing.compute_quantity_by_risk(
            ticker, signal["currency"], signal["current_price"], taux,
            balance_eur=base_cash, risk_pct=sizing.RISK_PCT,
            stop_loss_pct=portfolio.STOP_LOSS_PCT)
```

(`base_cash` est déjà disponible à ce point : fetché lignes 883-891,
avant cette boucle.)

- [ ] **Step 5: Run pour vérifier le succès**

Run: `pytest tests/ibkr_bot/test_daily.py -v`
Expected: PASS intégralement — y compris tous les tests "entrées"
existants, qui utilisent probablement un `base_cash`/prix calibrés pour
l'ancien `BUDGET_EUR` fixe. **Si un test existant échoue** parce que la
quantité attendue était calculée pour un budget de 500€ fixe,
recalculer la quantité attendue avec la nouvelle formule
(`base_cash * RISK_PCT / (abs(STOP_LOSS_PCT)/100)`) et mettre à jour
l'assertion — ce n'est pas une régression, c'est le changement de
comportement voulu par ce plan, mais vérifier d'abord qu'aucun autre
aspect du test (ordre des rejets, structure du journal) n'a changé pour
une autre raison avant de toucher l'assertion.

- [ ] **Step 6: Commit**

```bash
git add ibkr_bot/daily.py tests/ibkr_bot/test_daily.py
git commit -m "feat(ibkr-bot): daily.py dimensionne chaque entree par le risque base sur le vrai solde (resout la sous-capitalisation)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Câbler la vérification des cotes dans `daily.py`

**Files:**
- Modify: `ibkr_bot/daily.py:1-37,876-923,976`
- Test: `tests/ibkr_bot/test_daily.py`

**Interfaces:**
- Consumes: `odds_check.historical_odds_for_signal` (Tâche 5) ; `paper_positions` (déjà chargé ligne 876) ; `companies` (déjà construit ligne 763).

- [ ] **Step 1: Écrire le test qui échoue**

```python
def test_run_batch_rejects_a_signal_with_unfavorable_historical_odds(env):
    """25 positions clotures du meme indice, alpha moyen negatif ->
    le signal du jour sur ce meme indice doit etre rejete avant d'etre
    achete, meme s'il passerait tous les autres filtres."""
    historique = [
        {"id": f"H{i}", "ticker": f"H{i}.PA", "name": f"H{i}", "index": "CAC40",
         "status": "closed", "entry_date": "2026-01-01", "entry_price": 10.0,
         "target_exit_price": 11.0, "close_date": "2026-02-01", "close_price": 9.0,
         "close_reason": "stop_loss", "return_pct": -10.0, "index_return_pct": 1.0}
        for i in range(25)
    ]
    aujourdhui = {"id": "A.PA-2026-10-23", "ticker": "A.PA", "name": "A",
                  "index": "CAC40", "status": "open", "entry_date": "2026-10-23",
                  "entry_price": 100.0, "target_exit_price": 110.0}
    (env["tmp"] / "docs" / "signal_tracking.json").write_text(
        json.dumps({"positions": historique + [aujourdhui]}), encoding="utf-8")
    (env["tmp"] / "docs" / "indices.json").write_text(json.dumps({
        "updated": "2026-10-23",
        "index_currency": {"CAC40": "EUR"},
        "companies": [{"ticker": "A.PA", "index": "CAC40", "sector": "Luxe",
                       "score": 50.0, "current_price": 100.0, "stage_label": None}],
    }), encoding="utf-8")

    # ... (meme FausseGateway que la Tache 6, base_currency_cash a une
    # valeur suffisante pour ne pas etre rejete en solde_insuffisant) ...

    run = daily.run_batch(today="2026-10-23", gw=FausseGateway(),
                          sleep_fn=lambda s: None, paths=env["paths"])

    assert run["entrees"] == []
    assert any(r["raison"] == "cotes_historiques_defavorables"
              for r in run["signaux_rejetes"])
```

Adapter au style exact de fixture de `test_daily.py` comme pour la Tâche
6 (réutiliser la même `FausseGateway`, lire les tests voisins pour les
clés exactes de `run["signaux_rejetes"]`).

- [ ] **Step 2: Run pour vérifier l'échec**

Run: `pytest tests/ibkr_bot/test_daily.py -k unfavorable_historical_odds -v`
Expected: FAIL — le signal est acheté (`run["entrees"]` non vide), la
vérification des cotes n'est pas encore câblée.

- [ ] **Step 3: Implémenter**

Ajouter l'import en tête de `ibkr_bot/daily.py` (ligne 29-37, ordre
alphabétique) :

```python
import ibkr_bot.odds_check as odds_check
```

Juste avant `signaux_financables: list[dict] = []` (ligne 923), ajouter :

```python
    ticker_to_sector = {t: c.get("sector", "") for t, c in companies.items()}
```

Dans la boucle `for signal in signaux:` (lignes 924-976), juste avant
`signaux_financables.append(signal)` (dernière ligne de la boucle,
976), insérer :

```python
        verdict = odds_check.historical_odds_for_signal(
            paper_positions, ticker_to_sector, signal["index"], signal["sector"])
        if verdict["verdict"] == "unfavorable":
            run["signaux_rejetes"].append({
                "ticker": ticker, "raison": "cotes_historiques_defavorables",
                "rang": None, "score": signal.get("score"),
            })
            continue
        signaux_financables.append(signal)
```

(remplaçant la ligne `signaux_financables.append(signal)` existante par
ce bloc entier).

- [ ] **Step 4: Run pour vérifier le succès**

Run: `pytest tests/ibkr_bot/test_daily.py -v`
Expected: PASS intégralement

- [ ] **Step 5: Commit**

```bash
git add ibkr_bot/daily.py tests/ibkr_bot/test_daily.py
git commit -m "feat(ibkr-bot): daily.py rejette un signal dont les cotes historiques sont defavorables avant achat

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Câbler le coupe-circuit journalier dans `daily.py`

**Files:**
- Modify: `ibkr_bot/daily.py:1-71,883-976`
- Modify: `.gitignore`
- Test: `tests/ibkr_bot/test_daily.py`

**Interfaces:**
- Consumes: `shared_risk.CircuitBreaker.can_open_position_daily` (Tâche 2).

- [ ] **Step 1: Écrire les tests qui échouent**

```python
def test_run_batch_allows_entries_on_the_very_first_run(env):
    """Demarrage a froid (aucun fichier circuit_breaker_state.json) :
    le coupe-circuit ne doit jamais bloquer le tout premier batch."""
    # ... fixture indices.json/signal_tracking.json avec un signal
    # achetable, meme FausseGateway que les taches precedentes ...
    run = daily.run_batch(today="2026-10-23", gw=FausseGateway(),
                          sleep_fn=lambda s: None, paths=env["paths"])
    assert len(run["entrees"]) == 1


def test_run_batch_blocks_entries_after_a_large_equity_drop_since_the_previous_run(env):
    """Deux batchs consecutifs : le 1er fixe la reference d'equite, le
    2e arrive avec une equite en baisse de plus de 10% -> aucune entree,
    meme si le signal du jour passerait tous les autres filtres."""
    # 1er run : base_currency_cash = 1000.0 -> fixe la reference.
    run1 = daily.run_batch(today="2026-10-22", gw=FausseGatewayEquite(1000.0),
                           sleep_fn=lambda s: None, paths=env["paths"])
    # 2e run, le lendemain : equite tombee a 850 (-15%, > seuil de 10%).
    run2 = daily.run_batch(today="2026-10-23", gw=FausseGatewayEquite(850.0),
                           sleep_fn=lambda s: None, paths=env["paths"])
    assert run2["entrees"] == []
    assert any(r["raison"] == "coupe_circuit_journalier_declenche"
              for r in run2["signaux_rejetes"])
```

Adapter au style exact des fixtures (créer une `FausseGatewayEquite`
paramétrée par la valeur de `base_currency_cash`, réutilisable pour les
deux runs — suivre la convention déjà utilisée par les classes `Fausse*`
de `test_daily.py`).

- [ ] **Step 2: Run pour vérifier l'échec**

Run: `pytest tests/ibkr_bot/test_daily.py -k circuit_breaker -v`
Expected: le 2e test FAIL (l'entrée est encore acceptée, aucune clé
`"circuit_breaker_state"` dans `resolve_paths`/`DEFAULT_PATHS` encore).

- [ ] **Step 3: Implémenter**

Ajouter l'import en tête de `ibkr_bot/daily.py` (avant les imports
`ibkr_bot.*`, ordre alphabétique module top-level) :

```python
import shared_risk
```

Après `GIT_PULL_TIMEOUT_SECONDS = 120` (ligne 49), ajouter :

```python
# Coupe-circuit de perte journaliere (spec 2026-10-23) : seuil de 10%,
# meme ordre de grandeur que le profil de risque "equilibre" (3) de
# gold_bot (threshold_pct=0.10) -- ajustable sans impact sur gold_bot,
# instance et fichier d'etat entierement independants.
DAILY_LOSS_THRESHOLD_PCT = 0.10
CIRCUIT_BREAKER_STATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "circuit_breaker_state.json")
```

Dans `DEFAULT_PATHS` (lignes 51-59), ajouter une entrée :

```python
    "circuit_breaker_state": CIRCUIT_BREAKER_STATE_PATH,
```

Dans `run_batch`, juste après `journal.save_account_snapshot({"base_cash":
base_cash}, chemins["account_snapshot"])` (ligne 890-891), ajouter :

```python
    equity_eur = base_cash + sum(
        (p.get("valeur_actuelle_eur") or 0.0) for p in positions_ouvertes)
    circuit_breaker = shared_risk.CircuitBreaker(
        threshold_pct=DAILY_LOSS_THRESHOLD_PCT,
        persist_path=chemins["circuit_breaker_state"])
    coupe_circuit_ouvert = circuit_breaker.can_open_position_daily(equity_eur)
```

Puis, entourer la boucle `for signal in signaux:` qui construit `plans`/
`contrats` (lignes 896-905) d'un `if`/`else` :

```python
    cache_conid = contracts.load_cache(chemins["conid_cache"])
    plans: dict[str, dict] = {}
    contrats: dict[str, dict] = {}
    if coupe_circuit_ouvert:
        for signal in signaux:
            ticker = signal["ticker"]
            taux = _taux_de_change(gw, base_url, signal["currency"], taux_par_devise, run)
            plans[ticker] = sizing.compute_quantity_by_risk(
                ticker, signal["currency"], signal["current_price"], taux,
                balance_eur=base_cash, risk_pct=sizing.RISK_PCT,
                stop_loss_pct=portfolio.STOP_LOSS_PCT)
            contrats[ticker] = contracts.resolve_conid(
                ticker,
                lambda symbole: gw.search_contract(base_url, symbole),
                lambda conid: gw.contract_info(base_url, conid),
                cache_conid, today)
    else:
        for signal in signaux:
            run["signaux_rejetes"].append({
                "ticker": signal["ticker"], "raison": "coupe_circuit_journalier_declenche",
                "rang": None, "score": signal.get("score"),
            })
    try:
        contracts.save_cache(cache_conid, chemins["conid_cache"])
    except Exception as e:
        print(f"Erreur ecriture du cache de conid : {e}")
```

Et entourer de même la boucle `for signal in signaux:` qui construit
`signaux_financables` (lignes 923-976, déjà modifiée par la Tâche 7)
d'un `if coupe_circuit_ouvert:` — si tripped, `signaux_financables`
reste `[]` (la boucle entière, odds-check inclus, est sautée : inutile
d'évaluer les cotes historiques pour des signaux qu'on n'achètera de
toute façon pas aujourd'hui) :

```python
    ticker_to_sector = {t: c.get("sector", "") for t, c in companies.items()}
    signaux_financables = []
    if coupe_circuit_ouvert:
        for signal in signaux:
            ticker = signal["ticker"]
            raison_indisponible = tickers_indisponibles.get(ticker)
            if raison_indisponible is not None:
                run["signaux_rejetes"].append({
                    "ticker": ticker, "raison": raison_indisponible,
                    "rang": None, "score": signal.get("score"),
                })
                continue
            contrat = contrats.get(ticker) or {}
            if contrat.get("conid") is not None and contrat["conid"] in conids_detenus:
                run["signaux_rejetes"].append({
                    "ticker": ticker, "raison": "deja_detenu_hors_journal",
                    "rang": None, "score": signal.get("score"),
                })
                continue
            plan = plans.get(ticker) or {}
            devise_contrat = contrat.get("currency")
            devise_sizing = plan.get("devise_compte")
            if (devise_contrat and devise_sizing
                    and devise_contrat.upper() != devise_sizing.upper()):
                run["erreurs"].append({
                    "etape": "coherence_devise",
                    "detail": (f"{ticker} : devise du contrat resolu "
                               f"({devise_contrat}) differente de la devise de "
                               f"sizing ({devise_sizing}) — signal rejete par "
                               f"prudence plutot que d'executer dans une devise "
                               f"differente de celle du budget"),
                })
                run["signaux_rejetes"].append({
                    "ticker": ticker, "raison": "devise_incoherente",
                    "rang": None, "score": signal.get("score"),
                })
                continue
            verdict = odds_check.historical_odds_for_signal(
                paper_positions, ticker_to_sector, signal["index"], signal["sector"])
            if verdict["verdict"] == "unfavorable":
                run["signaux_rejetes"].append({
                    "ticker": ticker, "raison": "cotes_historiques_defavorables",
                    "rang": None, "score": signal.get("score"),
                })
                continue
            signaux_financables.append(signal)
```

(Ce bloc reprend le corps EXACT de la boucle déjà présente après la
Tâche 7 — seul l'ajout de `if coupe_circuit_ouvert:` et l'indentation
changent ; recopier le corps actuel du fichier à ce point plutôt que de
retaper ce brief à la main, pour ne perdre aucun détail des Tâches
précédentes.)

Enfin, ajouter au `.gitignore` (à côté de `ibkr_bot/state.json`, ligne
15) :

```
ibkr_bot/circuit_breaker_state.json
```

- [ ] **Step 4: Run pour vérifier le succès**

Run: `pytest tests/ibkr_bot/test_daily.py -v`
Expected: PASS intégralement, y compris tous les tests "entrées"
existants (démarrage à froid : `can_open_position_daily` renvoie
toujours `True` au premier appel, donc aucun test existant basé sur un
`tmp_path` neuf ne doit changer de comportement).

- [ ] **Step 5: Run la suite complète**

Run: `pytest tests/ -v`
Expected: PASS intégralement (`tests/gold_bot/`, `tests/ibkr_bot/`,
`tests/test_shared_risk.py`, `tests/test_indices_score.py`, etc.) —
aucune régression sur `gold_bot` (jamais touché par les Tâches 3-8).

- [ ] **Step 6: Commit**

```bash
git add ibkr_bot/daily.py .gitignore tests/ibkr_bot/test_daily.py
git commit -m "feat(ibkr-bot): ajoute le coupe-circuit de perte journaliere (shared_risk.CircuitBreaker) avant toute nouvelle entree

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
