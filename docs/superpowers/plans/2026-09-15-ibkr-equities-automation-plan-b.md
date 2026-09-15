# Automatisation IBKR actions — Plan B : orchestrateur, journal, emails et déploiement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Câbler le « cerveau » déjà fusionné (Plan A) à l'exécution réelle : un batch quotidien `python -m ibkr_bot.daily` qui vérifie le Gateway, la fraîcheur des données, réconcilie, exécute sorties puis entrées via `gateway.place_market_order`, journalise tout dans `real_trading_log.jsonl` / `positions.json`, envoie un résumé quotidien par email — et se déploie sur le VPS Hetzner en services/timer systemd sous un utilisateur dédié `ibkrbot`.

**Architecture:** Trois nouveaux modules dans le paquet existant `ibkr_bot/` — `journal.py` (écriture du journal réel et des positions, aucune décision), `notify.py` (deux emails : le résumé quotidien et l'alerte immédiate « Gateway non authentifié »), et `daily.py` (l'orchestrateur, **seul endroit du dépôt d'où un ordre actions réel peut partir**). `daily.py` n'ajoute aucune logique de décision : il enchaîne les vraies fonctions de Plan A (`signals.collect_new_signals` → `sizing.compute_quantity` → `contracts.resolve_conid` → `portfolio.select_entries` / `portfolio.positions_to_close` / `portfolio.reconcile`) et se contente d'exécuter et de journaliser. Le déploiement reprend à l'identique les conventions de `gold_bot` : `deploy/ibkr-gateway.service` (Gateway Java sur 127.0.0.1), `deploy/ibkr-bot-daily.service` + `.timer` (`Type=oneshot`, 14:45 UTC), utilisateur `ibkrbot` séparé de `goldbot`, `.env` en `chmod 600`, interrupteur d'urgence par fichier en SSH.

**Tech Stack:** Python 3.12, `requests` et `python-dateutil` (déjà dans `requirements.txt`), `smtplib` de la bibliothèque standard, `subprocess` de la bibliothèque standard (pour le `git pull`), pytest avec `monkeypatch`/`tmp_path`. Aucune nouvelle dépendance PyPI. systemd pour le déploiement.

**Spec:** `docs/superpowers/specs/2026-09-14-ibkr-equities-automation-design.md`

**Plan précédent (déjà fusionné dans `main`) :** `docs/superpowers/plans/2026-09-14-ibkr-equities-automation-plan-a.md`

## Global Constraints

- **REVUE LA PLUS STRICTE POSSIBLE sur les tâches 3, 4 et 5 (`ibkr_bot/daily.py`).** C'est l'équivalent, pour ce plan, de ce qu'étaient `sizing.py`/`gateway.py`/`contracts.py` pour le Plan A : `daily.py` est le **seul** module de tout le dépôt qui appelle `gateway.place_market_order` / `gateway.confirm_reply`, donc le seul endroit où un bug déplace de l'argent réel. Relire à la main chaque garde `dry_run`/`kill_switch`, chaque unité de prix et chaque `side`/`quantity` envoyé avant d'approuver ces tâches. La tâche 4 en particulier ne doit être approuvée qu'après relecture ligne à ligne.
- **Défense en profondeur sur l'interrupteur (spec 4.3, point 2) :** l'état (`kill_switch`, `dry_run`) est rechargé depuis le disque **juste avant chaque envoi d'ordre**, même si `run_batch` l'a déjà vérifié au démarrage — exactement ce que fait `gold_bot/loop.py:execute_steps`. Un batch peut durer 20 minutes (préflight) : l'état vérifié au début est potentiellement périmé. Ne jamais remplacer ce rechargement par un booléen passé en paramètre.
- **`dry_run: true` par défaut** (spec 5.3). En `dry_run`, **aucun appel à `gateway.place_market_order` ni `gateway.confirm_reply` ne doit partir**, et pourtant le journal doit être complet (c'est le test le plus important de toute la suite, spec 7).
- **Le garde-fou pence/livre doit survivre intact dans `daily.py` (spec 4.7, point 1).** `sizing.compute_quantity` sépare `devise_cotation` (GBp pour `.L` — c'est l'unité dans laquelle IBKR cote, exécute, et renvoie `avgPrice`) de `devise_compte` (GBP — l'unité de `docs/indices.json`, et la **seule** dans laquelle les règles de sortie comparent quoi que ce soit). Toute valeur venant d'IBKR (`avgPrice`) doit repasser par `sizing.from_quotation_price(valeur, ticker)` avant d'être écrite dans `positions.json` ou comparée à un prix de `docs/indices.json`. Une erreur ici est un facteur 100 silencieux. Tests dédiés obligatoires (tâches 1 et 5).
- **Garde de fraîcheur non négociable (spec 4.5) :** si `docs/indices.json`.`updated` ne vaut pas la date du jour (UTC), **aucun ordre n'est passé — ni entrée ni sortie**. Statut journalisé : `donnees_perimees`.
- **Préflight du Gateway (spec 5.5) :** 3 tentatives espacées de **600 secondes**. Si l'échec persiste : abandon complet du batch (aucun ordre), journalisation `gateway_indisponible`, et **email d'alerte immédiat** (pas seulement le résumé quotidien). Les signaux du jour sont perdus, les sorties simplement décalées au lendemain.
- **Un ordre en échec n'interrompt jamais le batch** (spec 5.5) — même principe que `gold_bot/loop.py:execute_steps`. Chaque échec est journalisé individuellement avec sa cause et le batch continue. **Aucune reprise automatique d'un ordre échoué**, ni dans le batch, ni le lendemain.
- **Aucune écriture (journal, cache, snapshot) ne doit jamais interrompre le batch** — même contrat que `gold_bot/loop.py:_log_decision` et `_save_cache`. Ces fonctions attrapent, impriment, et renvoient `False`. Exception : l'échec d'écriture de `positions.json` doit en plus apparaître dans `run["erreurs"]` et donc dans l'email, parce que c'est un état, pas un log.
- **`daily.py` n'ajoute aucune règle de trading.** Le plafond de 10, le classement par score, le garde-fou de solde, les règles de sortie et la réconciliation sont **déjà implémentés et testés dans `ibkr_bot/portfolio.py`** — les appeler, jamais les réécrire ni les contourner.
- **Lire le VRAI code de Plan A, pas sa description.** Au moins une signature a changé après l'écriture du plan A : `portfolio.select_entries(signals, open_positions, plans, contrats, base_cash)` prend bien `contrats` et `base_cash` (et non `cash_by_currency`). Toutes les signatures utilisées dans ce plan ont été relues dans le code fusionné le 2026-09-15.
- **Le test structurel « seul `gateway.py` parle aux routes d'ordre » ne doit pas être affaibli** (tâche 4). Il est élargi de façon **étroite et explicite** : `daily.py` a le droit de mentionner les **noms de fonction** `place_market_order` / `confirm_reply` (c'est son rôle : appeler `gateway.py`), mais les **routes HTTP** (`/iserver/account/`, `/iserver/reply/`) restent interdites dans tout le paquet hors `gateway.py`, `daily.py` comprise. Aucun autre module que `gateway.py` et `daily.py` ne doit pouvoir mentionner ces noms.
- **`docs/signal_tracking.json`, `docs/indices.json` et `indices_score.py` restent en LECTURE SEULE** (spec 1, 4.9). Ce plan n'écrit jamais dedans.
- **Vocabulaire des motifs en français**, repris de la spec 4.9 et déjà produit par Plan A : `signal_ignore_plafond_atteint`, `signal_ignore_prix_unitaire_superieur_au_budget`, `contrat_non_resolu`, `donnees_perimees`, `cloturee_hors_bot`, `gateway_indisponible`, `erreur_execution`, `solde_insuffisant`, `deja_en_portefeuille`, `prix_ou_taux_invalide`. Trois motifs sont **ajoutés par ce plan** et n'existaient pas en Plan A : `deja_detenu_hors_journal` (tâche 5, garde d'idempotence), `vendu_aujourd_hui` et `prix_reference_absent` (tâche 5). Aucun autre motif ne doit être inventé en cours de route.
- **Aucun appel réseau réel dans les tests**, jamais — ni HTTP, ni SMTP, ni `git`. Conventions établies : faux objet réponse (`tests/gold_bot/test_broker.py`, `tests/ibkr_bot/test_gateway.py`), `monkeypatch`, `tmp_path`. Un test qui toucherait un vrai Gateway ou un vrai SMTP est un échec de la tâche.
- **Périmètre v1 : 8 indices** (`CAC40, DAX, NASDAQ, DOW, FTSE, SMI, IBEX35, FTSEMIB`), 4 devises (EUR/USD/GBP/CHF), **budget 500 € par position**, **plafond 10 positions du bot**, **ordre au marché (MKT)**, **batch à 14:45 UTC**.
- **Pas de `ibkr_bot/api.py`, pas de service HTTPS, pas de `IBKR_BOT_API_TOKEN`** (spec 5.2 / 9.4, décidé après l'incident `BOT_API_TOKEN` du 2026-09-14). L'interrupteur d'urgence est `ibkr_bot/state.json`, modifié en SSH, point.
- **Hors périmètre de ce plan :** tout ce que Plan A a déjà livré (`state.py`, `signals.py`, `sizing.py`, `contracts.py`, `gateway.py`, `portfolio.py` — ne pas les restructurer ; seules des modifications additives strictement nécessaires sont permises, et ce plan n'en prévoit aucune), tout affichage web / tableau de bord (spec 2), le passage effectif de `dry_run` à `False` (étape manuelle post-déploiement, spec 5.3), et la vérification des coûts d'abonnement de données de marché (spec 6 / 9.2, étape manuelle pré-lancement).
- Lancer les tests depuis la racine du dépôt : `python -m pytest`. La suite compte 723 tests au démarrage de ce plan ; elle doit rester verte à chaque commit.

---

### Task 1 : `ibkr_bot/journal.py` — journal réel, positions et instantané de compte

**Files:**
- Create: `ibkr_bot/journal.py`
- Test: `tests/ibkr_bot/test_journal.py`

**Interfaces:**
- Consumes: `ibkr_bot.portfolio.save_positions(positions, path)`, `ibkr_bot.portfolio.POSITIONS_PATH`, `ibkr_bot.sizing.from_quotation_price(prix, ticker)`, `ibkr_bot.portfolio.deadline_date(entry_date)` (tous existants, Plan A).
- Produces :
  - `REAL_TRADING_LOG_PATH: str`, `LATEST_ACCOUNT_PATH: str`
  - `now_iso() -> str` (`"%Y-%m-%dT%H:%M:%SZ"`, UTC)
  - `append_run(run: dict, path: str = REAL_TRADING_LOG_PATH) -> bool`
  - `read_runs(path: str = REAL_TRADING_LOG_PATH, day: str | None = None) -> list[dict]`
  - `build_order_record(*, ticker, conid, sens, quantite, devise_compte="", devise_cotation="", taux_de_change=None, budget_converti=None, prix_reference_sizing=None, prix_paper=None, execution=None, close_reason=None, rang=None) -> dict`
  - `build_position_record(signal: dict, plan: dict, contrat: dict, quantite: int, prix_execution_reference: float, today: str) -> dict`
  - `save_positions_safely(positions: list[dict], path: str = portfolio.POSITIONS_PATH) -> bool`
  - `save_account_snapshot(data: dict, path: str = LATEST_ACCOUNT_PATH) -> bool`

Ce module **stocke et met en forme, il ne décide de rien**. Il ne réinvente pas le stockage des positions (`portfolio.load_positions`/`save_positions` existent déjà) : il décide **quoi** écrire et **avec quels champs**, et garantit que toute écriture ratée n'interrompt jamais le batch.

**Schéma canonique d'une ligne du journal** (`run`), défini ici une fois pour toutes — les tâches 2 à 5 s'y réfèrent sans le redéfinir (spec 4.9) :

```python
{
  "timestamp": "2026-09-15T14:45:03Z",   # ajouté par append_run si absent
  "date": "2026-09-15",                  # date UTC du batch
  "mode": "dry_run" | "reel",
  "statut": "termine" | "kill_switch" | "donnees_perimees"
            | "gateway_indisponible" | "reconciliation_impossible",
  "git_pull": {"ok": bool, "detail": str},
  "preflight": {"ok": bool, "tentatives": int, "detail": str},
  "reconciliation": {"actives": int, "cloturees_hors_bot": [str],
                     "anomalies_quantite": [dict], "ignorees": int},
  "sorties": [ <order record> ],          # avec "close_reason"
  "entrees": [ <order record> ],          # avec "rang"
  "signaux_rejetes": [ {"ticker": str, "raison": str, "rang": int|None,
                        "score": float|None} ],
  "anomalies": [ {"type": str, "ticker": str|None, "detail": str} ],
  "erreurs": [ {"etape": str, "detail": str} ],
}
```

**Schéma canonique d'un `order record`** (les champs exigés par la spec 4.9, un par un) :

```python
{
  "ticker": "III.L", "conid": 12345, "sens": "BUY" | "SELL", "quantite": 6,
  "devise_compte": "GBP",            # devise dans laquelle on RAPPORTE (lisibilité)
  "devise_cotation": "GBp",          # devise dans laquelle IBKR COTE et EXÉCUTE
  "taux_de_change": 0.86,            # EUR -> devise_compte, tel que rendu par gateway.exchange_rate
  "budget_converti": 43000.0,        # budget en devise de COTATION (sortie de sizing)
  "prix_reference_sizing": 3.42,     # prix de docs/indices.json, en devise de COMPTE
  "prix_execution": 3.45,            # prix obtenu, ramené en devise de COMPTE
  "prix_execution_cotation": 345.0,  # prix obtenu, brut IBKR, en devise de COTATION
  "prix_execution_estime": False,    # True si avgPrice indisponible (repli sur la référence)
  "commission": None,                # si disponible dans la réponse IBKR
  "prix_paper": 3.40,                # prix retenu par le paper-trading pour le MÊME signal
  "ecart_paper_pct": 1.4706,         # (execution - paper) / paper * 100
  "order_id": "1234", "statut": "execute" | "simule" | "erreur",
  "detail": None, "close_reason": None, "rang": 1,
}
```

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `tests/ibkr_bot/test_journal.py` :

```python
import json
import os

import pytest

import ibkr_bot.journal as journal
import ibkr_bot.portfolio as portfolio


def test_now_iso_is_a_utc_timestamp():
    valeur = journal.now_iso()
    assert valeur.endswith("Z")
    assert len(valeur) == 20  # 2026-09-15T14:45:03Z


def test_append_run_writes_one_json_line_and_adds_a_timestamp(tmp_path):
    path = str(tmp_path / "real_trading_log.jsonl")

    assert journal.append_run({"date": "2026-09-15", "statut": "termine"}, path) is True

    with open(path, encoding="utf-8") as fh:
        lignes = fh.read().splitlines()
    assert len(lignes) == 1
    entree = json.loads(lignes[0])
    assert entree["date"] == "2026-09-15"
    assert entree["timestamp"].endswith("Z")


def test_append_run_keeps_an_explicit_timestamp(tmp_path):
    path = str(tmp_path / "log.jsonl")
    journal.append_run({"timestamp": "2026-09-15T14:45:03Z", "statut": "termine"}, path)
    with open(path, encoding="utf-8") as fh:
        assert json.loads(fh.read())["timestamp"] == "2026-09-15T14:45:03Z"


def test_append_run_appends_without_truncating(tmp_path):
    path = str(tmp_path / "log.jsonl")
    journal.append_run({"date": "2026-09-14"}, path)
    journal.append_run({"date": "2026-09-15"}, path)
    with open(path, encoding="utf-8") as fh:
        lignes = fh.read().splitlines()
    assert [json.loads(l)["date"] for l in lignes] == ["2026-09-14", "2026-09-15"]


def test_append_run_returns_false_instead_of_raising(tmp_path):
    """Une panne d'ecriture du journal ne doit JAMAIS interrompre le batch
    (meme contrat que gold_bot.loop._log_decision) : le repertoire parent
    est ici un fichier, donc l'ouverture echoue forcement."""
    fichier = tmp_path / "pas_un_repertoire"
    fichier.write_text("x", encoding="utf-8")
    path = str(fichier / "log.jsonl")

    assert journal.append_run({"date": "2026-09-15"}, path) is False


def test_append_run_never_raises_on_non_serializable_content(tmp_path):
    path = str(tmp_path / "log.jsonl")
    assert journal.append_run({"date": "2026-09-15", "objet": object()}, path) is False


def test_read_runs_filters_on_the_day_and_skips_corrupt_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text(
        '{"timestamp": "2026-09-14T14:45:00Z", "statut": "termine"}\n'
        "pas du json\n"
        "\n"
        '{"timestamp": "2026-09-15T14:45:00Z", "statut": "kill_switch"}\n',
        encoding="utf-8",
    )
    runs = journal.read_runs(str(path), day="2026-09-15")
    assert [r["statut"] for r in runs] == ["kill_switch"]


def test_read_runs_returns_empty_list_when_the_file_is_absent(tmp_path):
    assert journal.read_runs(str(tmp_path / "absent.jsonl"), day="2026-09-15") == []


def test_build_order_record_carries_every_field_the_spec_requires():
    record = journal.build_order_record(
        ticker="MC.PA", conid=17275, sens="BUY", quantite=5,
        devise_compte="EUR", devise_cotation="EUR", taux_de_change=1.0,
        budget_converti=500.0, prix_reference_sizing=90.0, prix_paper=88.0,
        execution={"statut": "execute", "order_id": "1234",
                   "prix_execution_cotation": 90.5, "prix_execution_estime": False,
                   "commission": 1.25, "detail": None},
        rang=1,
    )
    assert record["ticker"] == "MC.PA"
    assert record["conid"] == 17275
    assert record["sens"] == "BUY"
    assert record["quantite"] == 5
    assert record["devise_compte"] == "EUR"
    assert record["taux_de_change"] == 1.0
    assert record["budget_converti"] == 500.0
    assert record["prix_reference_sizing"] == 90.0
    assert record["prix_execution"] == 90.5
    assert record["commission"] == 1.25
    assert record["prix_paper"] == 88.0
    assert record["order_id"] == "1234"
    assert record["statut"] == "execute"
    assert record["rang"] == 1


def test_build_order_record_converts_a_london_fill_price_from_pence_to_pounds():
    """GARDE-FOU PENCE/LIVRE (spec 4.7) : IBKR renvoie avgPrice en PENCE
    pour un ticker .L. Le champ `prix_execution` est celui qu'on compare a
    docs/indices.json (en LIVRES) et qu'on rapporte a l'utilisateur : il
    doit valoir 100x moins que le brut IBKR, jamais autant."""
    record = journal.build_order_record(
        ticker="III.L", conid=98765, sens="BUY", quantite=145,
        devise_compte="GBP", devise_cotation="GBp", taux_de_change=0.86,
        budget_converti=43000.0, prix_reference_sizing=2.95, prix_paper=2.93,
        execution={"statut": "execute", "order_id": "9", "prix_execution_cotation": 296.0,
                   "prix_execution_estime": False, "commission": None, "detail": None},
    )
    assert record["prix_execution_cotation"] == 296.0
    assert record["prix_execution"] == pytest.approx(2.96)
    assert record["devise_cotation"] == "GBp"
    assert record["devise_compte"] == "GBP"


def test_build_order_record_computes_the_gap_against_the_paper_price():
    record = journal.build_order_record(
        ticker="ADBE", conid=202070, sens="BUY", quantite=1,
        devise_compte="USD", devise_cotation="USD", prix_paper=500.0,
        execution={"statut": "execute", "order_id": "2", "prix_execution_cotation": 510.0,
                   "prix_execution_estime": False, "commission": None, "detail": None},
    )
    assert record["ecart_paper_pct"] == pytest.approx(2.0)


def test_build_order_record_leaves_the_gap_none_without_a_paper_price():
    record = journal.build_order_record(
        ticker="MC.PA", conid=1, sens="SELL", quantite=5, devise_compte="EUR",
        devise_cotation="EUR", close_reason="stop_loss",
        execution={"statut": "execute", "order_id": "3", "prix_execution_cotation": 70.0,
                   "prix_execution_estime": False, "commission": None, "detail": None},
    )
    assert record["prix_paper"] is None
    assert record["ecart_paper_pct"] is None
    assert record["close_reason"] == "stop_loss"


def test_build_order_record_on_a_failed_order_keeps_the_cause_and_no_price():
    record = journal.build_order_record(
        ticker="SAP.DE", conid=1234, sens="BUY", quantite=3, devise_compte="EUR",
        devise_cotation="EUR",
        execution={"statut": "erreur", "order_id": None, "prix_execution_cotation": None,
                   "prix_execution_estime": False, "commission": None,
                   "detail": "400 Client Error"},
    )
    assert record["statut"] == "erreur"
    assert record["prix_execution"] is None
    assert record["prix_execution_cotation"] is None
    assert record["ecart_paper_pct"] is None
    assert record["detail"] == "400 Client Error"
    assert record["quantite"] == 3


def test_build_order_record_without_an_execution_is_a_simulation():
    record = journal.build_order_record(
        ticker="MC.PA", conid=17275, sens="BUY", quantite=5,
        devise_compte="EUR", devise_cotation="EUR",
    )
    assert record["statut"] == "simule"
    assert record["order_id"] is None
    assert record["prix_execution"] is None


def test_build_position_record_produces_exactly_what_portfolio_needs():
    """Contrat inter-modules : la position ecrite ici est relue telle quelle
    par portfolio.exit_reason et portfolio.reconcile — on les appelle donc
    POUR DE VRAI sur le resultat plutot que d'assert des noms de cles."""
    signal = {"id": "MC.PA-2026-09-15", "ticker": "MC.PA", "name": "LVMH",
              "index": "CAC40", "currency": "EUR", "entry_date": "2026-09-15",
              "paper_entry_price": 88.0, "target_exit_price": 120.0,
              "score": 55.2, "current_price": 90.0}
    plan = {"ticker": "MC.PA", "quantite": 5, "devise_cotation": "EUR",
            "devise_compte": "EUR", "taux_de_change": 1.0, "budget_converti": 500.0,
            "prix_unitaire_cotation": 90.0, "cout_estime_devise_compte": 450.0,
            "motif": None}
    contrat = {"ticker": "MC.PA", "conid": 17275, "exchange": "SBF",
               "currency": "EUR", "motif": None, "detail": "resolu"}

    position = journal.build_position_record(
        signal, plan, contrat, quantite=5, prix_execution_reference=90.5,
        today="2026-09-15")

    assert position["id"] == "MC.PA-2026-09-15"
    assert position["conid"] == 17275
    assert position["quantite"] == 5
    assert position["prix_execution_reference"] == 90.5
    assert position["date_entree"] == "2026-09-15"
    assert position["date_limite"] == "2027-03-15"
    assert position["target_exit_price"] == 120.0
    assert position["devise"] == "EUR"

    # Relue par les VRAIES fonctions de Plan A, sans adaptation :
    company = {"ticker": "MC.PA", "current_price": 70.0}
    assert portfolio.exit_reason(position, company, "2026-09-16") == "stop_loss"
    reconciliation = portfolio.reconcile(
        [position], [{"conid": 17275, "position": 5.0}])
    assert reconciliation["actives"][0]["id"] == "MC.PA-2026-09-15"
    assert reconciliation["anomalies_quantite"] == []


def test_save_positions_safely_writes_a_file_portfolio_can_reload(tmp_path):
    path = str(tmp_path / "positions.json")
    positions = [{"id": "MC.PA-2026-09-15", "ticker": "MC.PA", "conid": 17275,
                  "quantite": 5}]

    assert journal.save_positions_safely(positions, path) is True
    assert portfolio.load_positions(path) == positions


def test_save_positions_safely_returns_false_instead_of_raising(tmp_path):
    fichier = tmp_path / "pas_un_repertoire"
    fichier.write_text("x", encoding="utf-8")
    assert journal.save_positions_safely([], str(fichier / "positions.json")) is False


def test_save_account_snapshot_writes_json_and_never_raises(tmp_path):
    path = str(tmp_path / "latest_account.json")
    assert journal.save_account_snapshot({"base_cash": 8000.0}, path) is True
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["base_cash"] == 8000.0

    fichier = tmp_path / "fichier"
    fichier.write_text("x", encoding="utf-8")
    assert journal.save_account_snapshot({}, str(fichier / "x.json")) is False
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_journal.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ibkr_bot.journal'`

- [ ] **Step 3 : Écrire l'implémentation**

Créer `ibkr_bot/journal.py` :

```python
# ibkr_bot/journal.py
# Journalisation du batch reel : une ligne JSON append-only par execution
# dans real_trading_log.jsonl, l'etat de travail des positions ouvertes
# PAR LE BOT dans positions.json, et un instantane des soldes du compte.
#
# Ce module STOCKE et MET EN FORME, il ne decide de rien. Il ne reinvente
# pas non plus le stockage des positions : portfolio.save_positions fait
# deja l'ecriture atomique (Plan A). Son role ici est de decider QUOI
# ecrire, AVEC QUELS CHAMPS (spec 4.9), et de garantir qu'une ecriture
# ratee n'interrompt jamais le batch (meme contrat que
# gold_bot.loop._log_decision / _save_cache).
#
# docs/signal_tracking.json n'est JAMAIS ecrit ici (spec 4.9) : le
# paper-trading doit rester une donnee propre, sans slippage ni frais,
# pour continuer a mesurer la qualite du SIGNAL independamment de la
# qualite de l'EXECUTION.
import json
import os
from datetime import datetime, timezone

import ibkr_bot.portfolio as portfolio
import ibkr_bot.sizing as sizing

REAL_TRADING_LOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "real_trading_log.jsonl")
LATEST_ACCOUNT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "latest_account.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_run(run: dict, path: str = REAL_TRADING_LOG_PATH) -> bool:
    """Ajoute une ligne JSON au journal append-only. Renvoie False (sans
    jamais lever) si l'ecriture echoue : une panne disque sur le journal
    ne doit pas annuler un batch dont les ordres sont deja partis."""
    record = dict(run)
    record.setdefault("timestamp", now_iso())
    try:
        ligne = json.dumps(record, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(ligne + "\n")
        return True
    except Exception as e:
        print(f"Erreur journalisation batch IBKR : {e}")
        return False


def read_runs(path: str = REAL_TRADING_LOG_PATH, day: str | None = None) -> list[dict]:
    """Lignes du journal dont l'horodatage tombe le jour UTC demande
    (aujourd'hui par defaut). Fichier absent/illisible ou lignes
    corrompues -> ignores silencieusement, jamais d'exception. Meme
    contrat que gold_bot.notify.read_todays_decisions."""
    if day is None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    runs: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for ligne in fh:
                ligne = ligne.strip()
                if not ligne:
                    continue
                try:
                    entree = json.loads(ligne)
                except json.JSONDecodeError:
                    continue
                if isinstance(entree, dict) and entree.get("timestamp", "").startswith(day):
                    runs.append(entree)
    except Exception:
        return []
    return runs


def build_order_record(
    *, ticker: str, conid, sens: str, quantite: int,
    devise_compte: str = "", devise_cotation: str = "",
    taux_de_change=None, budget_converti=None, prix_reference_sizing=None,
    prix_paper=None, execution: dict | None = None,
    close_reason: str | None = None, rang: int | None = None,
) -> dict:
    """Une ligne d'ordre du journal, avec TOUS les champs exiges par la
    spec 4.9.

    GARDE-FOU PENCE/LIVRE, centralise ici et nulle part ailleurs :
    `execution["prix_execution_cotation"]` est le prix brut renvoye par
    IBKR, dans la devise de COTATION (GBp pour un ticker .L).
    `prix_execution` en est la traduction en devise de COMPTE (GBP), via
    sizing.from_quotation_price — c'est la seule unite comparable a
    docs/indices.json et la seule lisible dans un email. Les deux sont
    conserves : le brut pour l'audit, le converti pour la comparaison.
    """
    execution = execution or {}
    prix_cotation = execution.get("prix_execution_cotation")
    prix_compte = (sizing.from_quotation_price(prix_cotation, ticker)
                   if isinstance(prix_cotation, (int, float))
                   and not isinstance(prix_cotation, bool) else None)

    ecart = None
    if (isinstance(prix_compte, (int, float))
            and isinstance(prix_paper, (int, float))
            and not isinstance(prix_paper, bool) and prix_paper > 0):
        ecart = round((prix_compte - prix_paper) / prix_paper * 100, 4)

    return {
        "ticker": ticker,
        "conid": conid,
        "sens": sens,
        "quantite": quantite,
        "devise_compte": devise_compte,
        "devise_cotation": devise_cotation,
        "taux_de_change": taux_de_change,
        "budget_converti": budget_converti,
        "prix_reference_sizing": prix_reference_sizing,
        "prix_execution": prix_compte,
        "prix_execution_cotation": prix_cotation,
        "prix_execution_estime": bool(execution.get("prix_execution_estime", False)),
        "commission": execution.get("commission"),
        "prix_paper": prix_paper,
        "ecart_paper_pct": ecart,
        "order_id": execution.get("order_id"),
        "statut": execution.get("statut", "simule"),
        "detail": execution.get("detail"),
        "close_reason": close_reason,
        "rang": rang,
    }


def build_position_record(signal: dict, plan: dict, contrat: dict, quantite: int,
                          prix_execution_reference: float, today: str) -> dict:
    """Etat de travail d'une position ouverte par le bot (spec 4.9).

    `prix_execution_reference` est le prix d'execution reel, deja ramene
    en devise de COMPTE (livres pour le LSE, pas pence) : c'est la
    reference du stop-loss, comparee par portfolio.exit_reason au
    current_price de docs/indices.json, qui est lui aussi en livres.
    `target_exit_price` est repris VERBATIM du paper-trading et jamais
    recalcule (spec 3.6).
    """
    return {
        "id": signal["id"],
        "ticker": signal["ticker"],
        "name": signal.get("name", ""),
        "index": signal.get("index", ""),
        "conid": contrat["conid"],
        "devise": plan["devise_compte"],
        "quantite": quantite,
        "prix_execution_reference": prix_execution_reference,
        "paper_entry_price": signal.get("paper_entry_price"),
        "date_entree": today,
        "target_exit_price": signal["target_exit_price"],
        "date_limite": portfolio.deadline_date(today),
    }


def save_positions_safely(positions: list[dict],
                          path: str = portfolio.POSITIONS_PATH) -> bool:
    """portfolio.save_positions (ecriture atomique, Plan A), mais qui
    renvoie False au lieu de lever. positions.json est un ETAT, pas un
    log : l'appelant doit remonter un echec ici dans run["erreurs"] et
    donc dans l'email, contrairement a append_run."""
    try:
        portfolio.save_positions(positions, path)
        return True
    except Exception as e:
        print(f"Erreur ecriture positions.json : {e}")
        return False


def save_account_snapshot(data: dict, path: str = LATEST_ACCOUNT_PATH) -> bool:
    """Dernier instantane des soldes du compte (spec 4.3). Purement
    informatif : un echec n'interrompt jamais le batch."""
    record = dict(data)
    record.setdefault("fetched_at", now_iso())
    try:
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        print(f"Erreur ecriture instantane du compte : {e}")
        return False
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_journal.py -v`
Expected: PASS (tous)

Puis la suite complète, qui doit rester verte :
Run: `python -m pytest`
Expected: PASS

- [ ] **Step 5 : Commit**

```bash
git add ibkr_bot/journal.py tests/ibkr_bot/test_journal.py
git commit -m "feat(ibkr_bot): journal reel, positions et instantane de compte (Plan B, tache 1)"
```

---

### Task 2 : `ibkr_bot/notify.py` — résumé quotidien et alerte Gateway

**Files:**
- Create: `ibkr_bot/notify.py`
- Test: `tests/ibkr_bot/test_notify.py`

**Interfaces:**
- Consumes: le schéma `run` et `journal.read_runs(path, day)` de la tâche 1.
- Produces :
  - `SMTP_HOST: str`, `SMTP_PORT: int`
  - `build_summary_email_html(run: dict, day: str) -> str`
  - `send_daily_summary(run: dict, day: str | None = None) -> bool`
  - `build_gateway_alert_html(tentatives: int, day: str) -> str`
  - `send_gateway_alert(tentatives: int, day: str | None = None) -> bool`
  - `main() -> None` (relit la dernière ligne du journal du jour et renvoie le résumé — utile pour un renvoi manuel)

Deux types d'email, un seul module et une seule plomberie SMTP (`_send`), parce qu'ils partagent exactement la même configuration (spec 4.9 / 5.5). Ils se distinguent par leur **objet** et leur **déclencheur** : le résumé part à la fin de chaque batch qui s'est déroulé ; l'alerte part **immédiatement** et **uniquement** quand le préflight a échoué 3 fois, parce que c'est le seul cas où l'utilisateur doit agir le jour même (réauthentifier le Gateway).

Décision d'implémentation (à conserver) : **quand l'alerte Gateway part, le résumé quotidien ne part pas** — le corps de l'alerte dit déjà « aucun ordre n'a été passé aujourd'hui », un second email vide serait du bruit dans une boîte où l'on veut que l'alerte se voie.

Conventions SMTP reprises telles quelles de `gold_bot/notify.py` : `SMTP_USER`/`SMTP_PASSWORD`/`MAIL_TO` lus dans l'environnement, `smtp.gmail.com:587`, STARTTLS, jamais d'exception (on imprime et on renvoie `False`).

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `tests/ibkr_bot/test_notify.py` :

```python
import ibkr_bot.notify as notify

RUN = {
    "timestamp": "2026-09-15T14:45:03Z",
    "date": "2026-09-15",
    "mode": "reel",
    "statut": "termine",
    "git_pull": {"ok": True, "detail": ""},
    "preflight": {"ok": True, "tentatives": 1, "detail": "authentifie"},
    "reconciliation": {"actives": 2, "cloturees_hors_bot": ["OLD.PA-2026-03-01"],
                       "anomalies_quantite": [], "ignorees": 3},
    "sorties": [{"ticker": "SAP.DE", "conid": 111, "sens": "SELL", "quantite": 2,
                 "devise_compte": "EUR", "prix_execution": 210.0,
                 "close_reason": "stop_loss", "statut": "execute", "detail": None,
                 "order_id": "77", "prix_paper": None, "ecart_paper_pct": None}],
    "entrees": [{"ticker": "MC.PA", "conid": 17275, "sens": "BUY", "quantite": 5,
                 "devise_compte": "EUR", "prix_execution": 90.5, "prix_paper": 88.0,
                 "ecart_paper_pct": 2.8409, "statut": "execute", "detail": None,
                 "order_id": "78", "rang": 1, "close_reason": None}],
    "signaux_rejetes": [{"ticker": "ADBE", "raison": "signal_ignore_plafond_atteint",
                         "rang": 4, "score": 40.0}],
    "anomalies": [{"type": "prix_reference_absent", "ticker": "III.L",
                   "detail": "position inclosable tant que le prix n'est pas restaure"}],
    "erreurs": [{"etape": "taux_de_change", "detail": "timeout"}],
}


class _FakeSMTP:
    """Faux serveur SMTP — meme convention de faux objet que
    tests/gold_bot/test_broker.py / tests/ibkr_bot/test_gateway.py.
    Aucun test n'ouvre jamais de vraie connexion."""

    instances = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sent = []
        self.logged_in = None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        self.logged_in = (user, password)

    def sendmail(self, sender, to, message):
        self.sent.append({"from": sender, "to": to, "message": message})


def _configure_smtp(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "motdepasse")
    monkeypatch.setenv("MAIL_TO", "moi@example.com")
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)


def test_summary_lists_buys_sells_rejections_anomalies_and_errors():
    html = notify.build_summary_email_html(RUN, "2026-09-15")
    assert "MC.PA" in html          # achat
    assert "SAP.DE" in html         # vente
    assert "stop_loss" in html      # motif de vente
    assert "ADBE" in html           # signal ignore
    assert "signal_ignore_plafond_atteint" in html
    assert "prix_reference_absent" in html
    assert "timeout" in html        # erreur
    assert "2026-09-15" in html


def test_summary_shows_the_gateway_state_and_the_mode():
    html = notify.build_summary_email_html(RUN, "2026-09-15")
    assert "Gateway" in html
    assert "reel" in html.lower() or "réel" in html.lower()


def test_summary_flags_dry_run_prominently():
    run = {**RUN, "mode": "dry_run"}
    html = notify.build_summary_email_html(run, "2026-09-15")
    assert "dry_run" in html.lower() or "simulation" in html.lower()


def test_summary_reports_a_stale_data_batch_without_crashing():
    run = {"date": "2026-09-15", "mode": "dry_run", "statut": "donnees_perimees",
           "sorties": [], "entrees": [], "signaux_rejetes": [], "erreurs": [],
           "anomalies": []}
    html = notify.build_summary_email_html(run, "2026-09-15")
    assert "donnees_perimees" in html or "périmées" in html


def test_summary_handles_a_minimal_run_without_any_key():
    """Le resume ne doit jamais lever sur une ligne de journal partielle :
    un email manquant, c'est la perte de la seule visibilite quotidienne."""
    html = notify.build_summary_email_html({}, "2026-09-15")
    assert "2026-09-15" in html


def test_summary_escapes_html_in_tickers_and_details():
    run = {**RUN, "erreurs": [{"etape": "x", "detail": "<script>alert(1)</script>"}]}
    html = notify.build_summary_email_html(run, "2026-09-15")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_send_daily_summary_sends_through_smtp(monkeypatch):
    _configure_smtp(monkeypatch)

    assert notify.send_daily_summary(RUN, "2026-09-15") is True

    serveur = _FakeSMTP.instances[0]
    assert (serveur.host, serveur.port) == (notify.SMTP_HOST, notify.SMTP_PORT)
    assert serveur.logged_in == ("bot@example.com", "motdepasse")
    assert serveur.sent[0]["to"] == ["moi@example.com"]
    assert "MC.PA" in serveur.sent[0]["message"]


def test_send_daily_summary_returns_false_without_credentials(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)

    assert notify.send_daily_summary(RUN, "2026-09-15") is False
    assert _FakeSMTP.instances == []


def test_send_daily_summary_returns_false_instead_of_raising(monkeypatch):
    _configure_smtp(monkeypatch)

    def boom(host, port):
        raise OSError("smtp injoignable")

    monkeypatch.setattr(notify.smtplib, "SMTP", boom)
    assert notify.send_daily_summary(RUN, "2026-09-15") is False


def test_gateway_alert_says_how_many_attempts_and_that_nothing_was_traded():
    html = notify.build_gateway_alert_html(3, "2026-09-15")
    assert "3" in html
    assert "aucun ordre" in html.lower()
    assert "2026-09-15" in html


def test_send_gateway_alert_uses_a_distinct_subject(monkeypatch):
    _configure_smtp(monkeypatch)

    assert notify.send_gateway_alert(3, "2026-09-15") is True
    message_alerte = _FakeSMTP.instances[0].sent[0]["message"]

    _FakeSMTP.instances = []
    notify.send_daily_summary(RUN, "2026-09-15")
    message_resume = _FakeSMTP.instances[0].sent[0]["message"]

    def _sujet(message):
        for ligne in message.splitlines():
            if ligne.startswith("Subject:"):
                return ligne
        return ""

    assert _sujet(message_alerte) != _sujet(message_resume)
    assert "ALERTE" in _sujet(message_alerte)


def test_send_gateway_alert_returns_false_instead_of_raising(monkeypatch):
    _configure_smtp(monkeypatch)
    monkeypatch.setattr(notify.smtplib, "SMTP",
                        lambda host, port: (_ for _ in ()).throw(OSError("ko")))
    assert notify.send_gateway_alert(3, "2026-09-15") is False


def test_main_resends_the_last_run_of_the_day(monkeypatch, tmp_path):
    _configure_smtp(monkeypatch)
    log = tmp_path / "real_trading_log.jsonl"
    log.write_text(
        '{"timestamp": "2026-09-15T14:45:00Z", "date": "2026-09-15",'
        ' "statut": "termine", "entrees": [{"ticker": "MC.PA", "quantite": 5,'
        ' "statut": "execute", "devise_compte": "EUR", "prix_execution": 90.5}]}\n',
        encoding="utf-8")
    monkeypatch.setattr(notify.journal, "REAL_TRADING_LOG_PATH", str(log))

    notify.main(day="2026-09-15")

    assert "MC.PA" in _FakeSMTP.instances[0].sent[0]["message"]
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_notify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ibkr_bot.notify'`

- [ ] **Step 3 : Écrire l'implémentation**

Créer `ibkr_bot/notify.py` :

```python
# ibkr_bot/notify.py
# Deux emails, une seule plomberie SMTP (spec 4.9 / 5.5) :
#   1. le RESUME QUOTIDIEN, envoye a la fin de chaque batch qui s'est
#      deroule (achats, ventes, signaux ignores et pourquoi, anomalies,
#      erreurs, etat du Gateway) — une fois par jour, jamais par trade ;
#   2. l'ALERTE IMMEDIATE "Gateway non authentifie", envoyee UNIQUEMENT
#      quand le preflight a echoue ses 3 tentatives. C'est le seul cas ou
#      l'utilisateur doit agir le jour meme (reauthentifier le Gateway,
#      2FA comprise), donc le seul qui merite un email distinct plutot
#      qu'une ligne dans le resume.
#
# Quand l'alerte part, le resume ne part pas : le corps de l'alerte dit
# deja qu'aucun ordre n'a ete passe, un second email vide noierait
# l'alerte.
#
# Memes conventions SMTP que gold_bot/notify.py (qui reutilise deja les
# identifiants SMTP des alertes Indices) : SMTP_USER / SMTP_PASSWORD /
# MAIL_TO dans l'environnement, jamais d'exception vers l'appelant.
import html
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import ibkr_bot.journal as journal

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

_FOND = "background:#15161c"
_CARTE = ("margin:0 0 8px;padding:10px 14px;background:#1b1d25;"
          "font-family:Arial,sans-serif;border-left:3px solid ")


def _e(valeur) -> str:
    return html.escape(str(valeur))


def _carte(couleur: str, texte: str) -> str:
    return (f'<p style="{_CARTE}{couleur};">'
            f'<span style="color:#edeef3;font-size:13px;">{texte}</span></p>')


def _page(titre: str, corps: str) -> str:
    return f"""
    <html><body style="{_FOND};margin:0;padding:0;">
      <div style="max-width:560px;margin:0 auto;padding:32px 24px;font-family:Arial,Helvetica,sans-serif;">
        <p style="color:#8a90a3;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;margin:0 0 10px;">
          {titre}
        </p>
        {corps}
      </div>
    </body></html>
    """


def _prix(record: dict) -> str:
    prix = record.get("prix_execution")
    if not isinstance(prix, (int, float)) or isinstance(prix, bool):
        return "prix indisponible"
    estime = " (estimé)" if record.get("prix_execution_estime") else ""
    return f'{prix:.4g} {_e(record.get("devise_compte", ""))}{estime}'


def build_summary_email_html(run: dict, day: str) -> str:
    """Resume quotidien. Ne doit JAMAIS lever, meme sur une ligne de
    journal partielle : un email manquant, c'est la perte de la seule
    visibilite quotidienne sur un bot qui manie de l'argent reel — d'ou
    les .get() partout plutot que des acces directs."""
    mode = run.get("mode", "?")
    statut = run.get("statut", "?")
    preflight = run.get("preflight") or {}
    etat_gateway = ("authentifié" if preflight.get("ok")
                    else f'NON authentifié ({preflight.get("tentatives", 0)} tentative(s))')

    corps = (
        f'<p style="color:#edeef3;font-size:14px;margin:0 0 16px;">'
        f'Mode <b>{_e(mode)}</b> — statut <b>{_e(statut)}</b> — '
        f'Gateway : {_e(etat_gateway)}.</p>'
    )
    if mode == "dry_run":
        corps += _carte("#7a6a2a",
                        "Mode simulation (dry_run) : aucun ordre réel n'a été envoyé.")

    entrees = run.get("entrees") or []
    sorties = run.get("sorties") or []
    rejets = run.get("signaux_rejetes") or []
    anomalies = run.get("anomalies") or []
    erreurs = run.get("erreurs") or []

    corps += (f'<p style="color:#8a90a3;font-size:12px;margin:16px 0 8px;">'
              f'{len(entrees)} achat(s), {len(sorties)} vente(s), '
              f'{len(rejets)} signal(aux) ignoré(s), {len(erreurs)} erreur(s).</p>')

    for record in sorties:
        couleur = "#3f6f4a" if record.get("statut") == "execute" else "#a35540"
        corps += _carte(couleur, (
            f'VENTE {_e(record.get("ticker"))} × {_e(record.get("quantite"))} — '
            f'{_e(record.get("close_reason"))} — {_prix(record)} — '
            f'{_e(record.get("statut"))}'
            + (f' — {_e(record.get("detail"))}' if record.get("detail") else "")))

    for record in entrees:
        couleur = "#3f6f4a" if record.get("statut") == "execute" else "#a35540"
        ecart = record.get("ecart_paper_pct")
        ecart_txt = (f' — écart paper {ecart:+.2f} %'
                     if isinstance(ecart, (int, float)) and not isinstance(ecart, bool)
                     else "")
        corps += _carte(couleur, (
            f'ACHAT {_e(record.get("ticker"))} × {_e(record.get("quantite"))} '
            f'(rang {_e(record.get("rang"))}) — {_prix(record)}{ecart_txt} — '
            f'{_e(record.get("statut"))}'
            + (f' — {_e(record.get("detail"))}' if record.get("detail") else "")))

    for rejet in rejets:
        corps += _carte("#4a4d5a", (
            f'Signal ignoré — {_e(rejet.get("ticker"))} : '
            f'{_e(rejet.get("raison"))}'
            + (f' (rang {_e(rejet.get("rang"))})' if rejet.get("rang") else "")))

    reconciliation = run.get("reconciliation") or {}
    if reconciliation:
        hors_bot = reconciliation.get("cloturees_hors_bot") or []
        corps += _carte("#4a4d5a", (
            f'Réconciliation — {_e(reconciliation.get("actives", 0))} position(s) '
            f'active(s), {len(hors_bot)} clôturée(s) hors bot, '
            f'{_e(reconciliation.get("ignorees", 0))} position(s) du compte ignorée(s).'))
        for anomalie in reconciliation.get("anomalies_quantite") or []:
            corps += _carte("#a35540", (
                f'Anomalie de quantité — {_e(anomalie.get("ticker"))} : '
                f'journal {_e(anomalie.get("quantite_locale"))} vs IBKR '
                f'{_e(anomalie.get("quantite_ibkr"))} (IBKR fait foi).'))

    for anomalie in anomalies:
        corps += _carte("#a35540", (
            f'Anomalie — {_e(anomalie.get("type"))} '
            f'{_e(anomalie.get("ticker"))} : {_e(anomalie.get("detail"))}'))

    for erreur in erreurs:
        corps += _carte("#a35540", (
            f'Erreur — {_e(erreur.get("etape"))} : {_e(erreur.get("detail"))}'))

    return _page(f"Bot Actions IBKR — Résumé du {_e(day)}", corps)


def build_gateway_alert_html(tentatives: int, day: str) -> str:
    corps = (
        _carte("#a35540",
               f'Le Gateway IBKR n\'est pas authentifié après {_e(tentatives)} '
               f'tentative(s) espacées de 10 minutes. Le batch du '
               f'{_e(day)} a été abandonné : <b>aucun ordre n\'a été passé</b>, '
               f'ni entrée ni sortie.')
        + _carte("#4a4d5a",
                 "Les signaux d\'entrée du jour sont perdus (pas de file "
                 "d\'attente, règle 3.5). Les sorties éligibles sont simplement "
                 "décalées au batch de demain.")
        + _carte("#4a4d5a",
                 "Action attendue aujourd\'hui : rouvrir l\'interface "
                 "d\'authentification locale du Gateway et se reconnecter "
                 "(login + 2FA). Voir deploy/README-ibkr.md, section "
                 "« Authentifier le Gateway ».")
    )
    return _page(f"Bot Actions IBKR — ALERTE Gateway du {_e(day)}", corps)


def _send(subject: str, body_html: str) -> bool:
    """Plomberie SMTP commune aux deux emails. Ignore silencieusement (avec
    un message) si SMTP_USER/SMTP_PASSWORD ne sont pas configures — meme
    contrat que gold_bot.notify.send_daily_summary, jamais d'exception."""
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    mail_to = os.environ.get("MAIL_TO") or smtp_user
    if not smtp_user or not smtp_password:
        print("\n(Envoi email Bot Actions IBKR ignoré : SMTP_USER / SMTP_PASSWORD non configurés.)")
        return False
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            msg = MIMEMultipart("mixed")
            msg["Subject"] = subject
            msg["From"] = smtp_user
            msg["To"] = mail_to
            msg.attach(MIMEText(body_html, "html"))
            server.sendmail(smtp_user, [mail_to], msg.as_string())
        print(f"Email Bot Actions IBKR envoyé : {subject}")
        return True
    except Exception as e:
        print(f"Erreur envoi email Bot Actions IBKR : {e}")
        return False


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def send_daily_summary(run: dict, day: str | None = None) -> bool:
    day = day or _today()
    return _send(f"Bot Actions IBKR — résumé du {day}",
                 build_summary_email_html(run, day))


def send_gateway_alert(tentatives: int, day: str | None = None) -> bool:
    day = day or _today()
    return _send(f"Bot Actions IBKR — ALERTE : Gateway non authentifié ({day})",
                 build_gateway_alert_html(tentatives, day))


def main(day: str | None = None) -> None:
    """Renvoi manuel du resume du jour depuis le journal (le batch
    l'envoie deja lui-meme a la fin de chaque execution — cette entree
    sert a le renvoyer apres coup, par exemple si le SMTP etait tombe)."""
    day = day or _today()
    runs = journal.read_runs(journal.REAL_TRADING_LOG_PATH, day=day)
    if not runs:
        print(f"Aucun batch journalisé le {day}.")
        return
    send_daily_summary(runs[-1], day)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_notify.py -v`
Expected: PASS (tous)

Run: `python -m pytest`
Expected: PASS

- [ ] **Step 5 : Commit**

```bash
git add ibkr_bot/notify.py tests/ibkr_bot/test_notify.py
git commit -m "feat(ibkr_bot): resume quotidien et alerte Gateway par email (Plan B, tache 2)"
```

---

### Task 3 : `ibkr_bot/daily.py` — configuration, `git pull`, préflight et gardes d'abandon

> **REVUE STRICTE.** Cette tâche pose les trois gardes qui protègent tout le reste : l'interrupteur d'urgence, la fraîcheur des données, et le préflight du Gateway. Un batch qui franchit ces gardes à tort est un batch qui trade sur des données d'hier ou sans session valide.

**Files:**
- Create: `ibkr_bot/daily.py`
- Test: `tests/ibkr_bot/test_daily.py`

**Interfaces:**
- Consumes: `ibkr_bot.state.load_state/STATE_PATH`, `ibkr_bot.signals.load_indices/indices_are_fresh/INDICES_PATH/SIGNAL_TRACKING_PATH`, `ibkr_bot.portfolio.POSITIONS_PATH`, `ibkr_bot.contracts.CONID_CACHE_PATH`, `ibkr_bot.gateway.is_authenticated/brokerage_accounts/portfolio_accounts/DEFAULT_GATEWAY_URL`, `ibkr_bot.journal.*` (tâche 1), `ibkr_bot.notify.send_daily_summary/send_gateway_alert` (tâche 2).
- Produces :
  - `PREFLIGHT_ATTEMPTS = 3`, `PREFLIGHT_DELAY_SECONDS = 600`, `REPO_DIR: str`, `DEFAULT_PATHS: dict`
  - `resolve_paths(paths: dict | None) -> dict`
  - `pull_repo(repo_dir: str = REPO_DIR, run_fn=subprocess.run) -> dict` → `{"ok": bool, "detail": str}`
  - `preflight(base_url: str, *, gw=gateway, sleep_fn=time.sleep, attempts=PREFLIGHT_ATTEMPTS, delay_s=PREFLIGHT_DELAY_SECONDS) -> dict` → `{"ok": bool, "tentatives": int, "detail": str}`
  - `run_batch(today: str | None = None, *, gw=gateway, sleep_fn=time.sleep, base_url: str | None = None, account_id: str | None = None, repo_dir: str = REPO_DIR, paths: dict | None = None) -> dict` (dans cette tâche : s'arrête juste après les gardes, avec `sorties`/`entrees` vides — les tâches 4 et 5 remplissent le milieu)

**Ordre des gardes, qui est lui-même une décision (à conserver et à commenter dans le code) :**

1. `kill_switch` — aucun appel réseau du tout, on s'arrête net.
2. `git pull` — non bloquant : un échec est journalisé, et c'est le garde de fraîcheur juste après qui décidera.
3. **fraîcheur des données** — **avant** le préflight, délibérément : si `docs/indices.json` n'est pas du jour, le batch ne fera rien de toute façon (spec 4.5, ni entrée ni sortie), donc brûler jusqu'à 20 minutes de tentatives de réauthentification serait inutile, et surtout l'email d'alerte Gateway dirait « réauthentifie-toi » alors que le vrai problème est en amont (workflow `indices.yml` en échec).
4. **préflight du Gateway** — 3 tentatives / 600 s, incluant l'amorçage de session (`brokerage_accounts` + `portfolio_accounts`), que le CPAPI exige avant que les routes portefeuille et ordres ne renvoient des données réelles (voir les docstrings de `gateway.py` : sans cet appel, `/portfolio/...` renvoie **silencieusement du vide**, ce qui est le sens dangereux — « le bot ne détient rien » justifierait à tort un rachat).

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `tests/ibkr_bot/test_daily.py` :

```python
import json

import pytest

import ibkr_bot.daily as daily

TODAY = "2026-09-15"


class FakeGateway:
    """Faux gateway : enregistre chaque appel et ne touche jamais au
    reseau. `ordres` reste vide tant qu'aucun ordre n'est envoye — c'est
    l'assertion centrale des tests dry_run/kill_switch."""

    def __init__(self, authentifie=True, positions_ibkr=None, base_cash=10000.0):
        self.authentifie = authentifie
        self.appels = []
        self.ordres = []
        self.confirmations = []
        self.positions_ibkr = positions_ibkr if positions_ibkr is not None else []
        self.base_cash = base_cash
        self.reponse_ordre = [{"order_id": "1234", "order_status": "Submitted"}]
        self.statuts_ordre = {}
        self.echecs = set()

    # --- session ---
    def is_authenticated(self, base_url):
        self.appels.append("is_authenticated")
        return self.authentifie

    def brokerage_accounts(self, base_url):
        self.appels.append("brokerage_accounts")
        return {"accounts": ["U1234567"]}

    def portfolio_accounts(self, base_url):
        self.appels.append("portfolio_accounts")
        return [{"accountId": "U1234567"}]

    # --- donnees ---
    def positions(self, base_url, account_id):
        self.appels.append("positions")
        return list(self.positions_ibkr)

    def base_currency_cash(self, base_url, account_id):
        self.appels.append("base_currency_cash")
        return self.base_cash

    def cash_by_currency(self, base_url, account_id):
        return {"EUR": self.base_cash}

    def exchange_rate(self, base_url, source, target):
        return 1.0 if source == target else {"USD": 1.08, "GBP": 0.86, "CHF": 0.94}[target]

    def search_contract(self, base_url, symbol):
        return [{"conid": SEARCH_CONIDS[symbol], "symbol": symbol,
                 "description": SEARCH_EXCHANGES[symbol],
                 "sections": [{"secType": "STK"}]}]

    def contract_info(self, base_url, conid):
        return INFOS[str(conid)]

    # --- ordres ---
    def place_market_order(self, base_url, account_id, conid, side, quantity):
        self.ordres.append({"conid": conid, "side": side, "quantity": quantity})
        if conid in self.echecs:
            raise RuntimeError("400 Client Error: rejet IBKR")
        return list(self.reponse_ordre)

    def confirm_reply(self, base_url, reply_id, confirmed=True):
        self.confirmations.append(reply_id)
        return [{"order_id": "1234", "order_status": "Submitted"}]

    def order_status(self, base_url, order_id):
        return self.statuts_ordre.get(order_id, {"order_status": "Filled",
                                                 "avgPrice": "90.50"})


SEARCH_CONIDS = {"MC": 17275, "ADBE": 202070, "SAP": 40000, "III": 98765}
SEARCH_EXCHANGES = {"MC": "SBF", "ADBE": "NASDAQ", "SAP": "IBIS", "III": "LSE"}
INFOS = {
    "17275": {"conid": 17275, "currency": "EUR", "listingExchange": "SBF"},
    "202070": {"conid": 202070, "currency": "USD", "listingExchange": "NASDAQ"},
    "40000": {"conid": 40000, "currency": "EUR", "listingExchange": "IBIS"},
    "98765": {"conid": 98765, "currency": "GBP", "listingExchange": "LSE"},
}

INDICES = {
    "updated": TODAY,
    "index_currency": {"CAC40": "EUR", "DAX": "EUR", "NASDAQ": "USD",
                       "DOW": "USD", "FTSE": "GBP", "SMI": "CHF",
                       "IBEX35": "EUR", "FTSEMIB": "EUR"},
    "companies": [
        {"ticker": "MC.PA", "index": "CAC40", "score": 55.2, "current_price": 90.0},
        {"ticker": "ADBE", "index": "NASDAQ", "score": 40.0, "current_price": 400.0},
        {"ticker": "SAP.DE", "index": "DAX", "score": 10.0, "current_price": 100.0},
        {"ticker": "III.L", "index": "FTSE", "score": 30.0, "current_price": 2.95},
    ],
}

TRACKING = {"positions": [
    {"id": "MC.PA-2026-09-15", "ticker": "MC.PA", "name": "LVMH", "index": "CAC40",
     "status": "open", "entry_date": TODAY, "entry_price": 88.0,
     "target_exit_price": 120.0},
    {"id": "ADBE-2026-09-15", "ticker": "ADBE", "name": "Adobe", "index": "NASDAQ",
     "status": "open", "entry_date": TODAY, "entry_price": 395.0,
     "target_exit_price": 600.0},
]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Tout le batch confine dans tmp_path : aucun fichier du depot n'est
    lu ni ecrit, et les deux emails sont captures plutot qu'envoyes."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "indices.json").write_text(
        json.dumps(INDICES), encoding="utf-8")
    (tmp_path / "docs" / "signal_tracking.json").write_text(
        json.dumps(TRACKING), encoding="utf-8")
    (tmp_path / "state.json").write_text(
        json.dumps({"kill_switch": False, "dry_run": True}), encoding="utf-8")

    emails = {"resumes": [], "alertes": []}
    monkeypatch.setattr(daily.notify, "send_daily_summary",
                        lambda run, day=None: emails["resumes"].append((run, day)) or True)
    monkeypatch.setattr(daily.notify, "send_gateway_alert",
                        lambda tentatives, day=None: emails["alertes"].append((tentatives, day)) or True)
    monkeypatch.setattr(daily, "pull_repo", lambda *a, **k: {"ok": True, "detail": "à jour"})

    paths = {
        "state": str(tmp_path / "state.json"),
        "positions": str(tmp_path / "positions.json"),
        "journal": str(tmp_path / "real_trading_log.jsonl"),
        "conid_cache": str(tmp_path / "conid_cache.json"),
        "indices": str(tmp_path / "docs" / "indices.json"),
        "tracking": str(tmp_path / "docs" / "signal_tracking.json"),
        "account_snapshot": str(tmp_path / "latest_account.json"),
    }
    return {"tmp": tmp_path, "paths": paths, "emails": emails}


def ecrire_etat(env, **champs):
    with open(env["paths"]["state"], "w", encoding="utf-8") as fh:
        json.dump({"kill_switch": False, "dry_run": True, **champs}, fh)


def lire_journal(env):
    with open(env["paths"]["journal"], encoding="utf-8") as fh:
        return [json.loads(l) for l in fh.read().splitlines() if l.strip()]


# --- pull_repo -------------------------------------------------------

def test_pull_repo_runs_git_pull_in_the_repo_directory():
    appels = []

    class _Resultat:
        returncode = 0
        stdout = "Already up to date."
        stderr = ""

    def fake_run(cmd, **kwargs):
        appels.append((cmd, kwargs))
        return _Resultat()

    resultat = daily.pull_repo("/home/ibkrbot/analyse-or", run_fn=fake_run)

    assert resultat["ok"] is True
    cmd, kwargs = appels[0]
    assert cmd[:2] == ["git", "pull"]
    assert kwargs["cwd"] == "/home/ibkrbot/analyse-or"
    assert kwargs["timeout"] > 0


def test_pull_repo_reports_a_non_zero_exit_without_raising():
    class _Resultat:
        returncode = 1
        stdout = ""
        stderr = "fatal: could not read from remote"

    resultat = daily.pull_repo("/x", run_fn=lambda *a, **k: _Resultat())
    assert resultat["ok"] is False
    assert "remote" in resultat["detail"]


def test_pull_repo_reports_an_exception_without_raising():
    def boom(*args, **kwargs):
        raise OSError("git introuvable")

    resultat = daily.pull_repo("/x", run_fn=boom)
    assert resultat["ok"] is False
    assert "git introuvable" in resultat["detail"]


# --- preflight -------------------------------------------------------

def test_preflight_succeeds_on_the_first_attempt_without_sleeping():
    gw = FakeGateway(authentifie=True)
    dodos = []

    resultat = daily.preflight("https://127.0.0.1:5000", gw=gw,
                               sleep_fn=dodos.append)

    assert resultat["ok"] is True
    assert resultat["tentatives"] == 1
    assert dodos == []


def test_preflight_primes_the_cpapi_session_before_declaring_success():
    """Le CPAPI exige /iserver/accounts ET /portfolio/accounts au moins une
    fois par session : sans eux, /portfolio/... renvoie silencieusement du
    VIDE, ce qui ferait croire a la reconciliation que le bot ne detient
    rien et justifierait un rachat."""
    gw = FakeGateway(authentifie=True)
    daily.preflight("https://127.0.0.1:5000", gw=gw, sleep_fn=lambda s: None)
    assert "brokerage_accounts" in gw.appels
    assert "portfolio_accounts" in gw.appels


def test_preflight_retries_three_times_ten_minutes_apart_then_gives_up():
    gw = FakeGateway(authentifie=False)
    dodos = []

    resultat = daily.preflight("https://127.0.0.1:5000", gw=gw,
                               sleep_fn=dodos.append)

    assert resultat["ok"] is False
    assert resultat["tentatives"] == 3
    assert dodos == [600, 600]  # 2 attentes entre 3 tentatives, pas 3


def test_preflight_counts_a_session_priming_failure_as_a_failed_attempt():
    class _Gw(FakeGateway):
        def portfolio_accounts(self, base_url):
            raise RuntimeError("503 Service Unavailable")

    gw = _Gw(authentifie=True)
    resultat = daily.preflight("https://127.0.0.1:5000", gw=gw, sleep_fn=lambda s: None)

    assert resultat["ok"] is False
    assert resultat["tentatives"] == 3
    assert "503" in resultat["detail"]


def test_preflight_succeeds_on_a_later_attempt():
    class _Gw(FakeGateway):
        def is_authenticated(self, base_url):
            self.appels.append("is_authenticated")
            return self.appels.count("is_authenticated") >= 2

    gw = _Gw(authentifie=False)
    dodos = []
    resultat = daily.preflight("https://127.0.0.1:5000", gw=gw, sleep_fn=dodos.append)

    assert resultat["ok"] is True
    assert resultat["tentatives"] == 2
    assert dodos == [600]


# --- gardes de run_batch ---------------------------------------------

def test_kill_switch_stops_everything_before_any_gateway_call(env):
    """Spec 7 : kill_switch: true -> aucune action, ni entree ni sortie."""
    ecrire_etat(env, kill_switch=True)
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "kill_switch"
    assert gw.appels == []
    assert gw.ordres == []
    assert run["sorties"] == [] and run["entrees"] == []
    assert lire_journal(env)[0]["statut"] == "kill_switch"
    assert len(env["emails"]["resumes"]) == 1


def test_stale_indices_json_stops_the_batch_entirely(env):
    """Spec 4.5 : donnees d'hier -> aucun ordre, ni entree ni sortie."""
    perime = {**INDICES, "updated": "2026-09-14"}
    with open(env["paths"]["indices"], "w", encoding="utf-8") as fh:
        json.dump(perime, fh)
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "donnees_perimees"
    assert gw.ordres == []
    assert lire_journal(env)[0]["statut"] == "donnees_perimees"
    assert len(env["emails"]["resumes"]) == 1
    assert env["emails"]["alertes"] == []


def test_stale_data_is_checked_before_the_preflight_retries(env):
    """Bruler 20 minutes de reauthentification un jour ou le batch ne fera
    rien de toute facon serait absurde — et l'email d'alerte dirait
    'reauthentifie-toi' alors que le vrai probleme est indices.yml."""
    with open(env["paths"]["indices"], "w", encoding="utf-8") as fh:
        json.dump({**INDICES, "updated": "2026-09-14"}, fh)
    gw = FakeGateway(authentifie=False)
    dodos = []

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=dodos.append,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "donnees_perimees"
    assert dodos == []
    assert "is_authenticated" not in gw.appels


def test_a_missing_indices_file_is_treated_as_stale_data(env):
    import os
    os.remove(env["paths"]["indices"])
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "donnees_perimees"
    assert gw.ordres == []


def test_persistent_gateway_failure_abandons_the_batch_and_alerts(env):
    """Spec 5.5 / 7 : 3 tentatives puis abandon + email d'alerte immediat."""
    gw = FakeGateway(authentifie=False)
    dodos = []

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=dodos.append,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "gateway_indisponible"
    assert run["preflight"]["tentatives"] == 3
    assert dodos == [600, 600]
    assert gw.ordres == []
    assert lire_journal(env)[0]["statut"] == "gateway_indisponible"
    assert env["emails"]["alertes"] == [(3, TODAY)]
    assert env["emails"]["resumes"] == []   # pas de doublon vide


def test_a_failed_git_pull_is_recorded_but_not_fatal(env, monkeypatch):
    monkeypatch.setattr(daily, "pull_repo",
                        lambda *a, **k: {"ok": False, "detail": "réseau indisponible"})
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["git_pull"]["ok"] is False
    assert run["statut"] != "kill_switch"
    # Le garde de fraicheur, pas le git pull, decide s'il y a lieu d'agir :
    # les donnees du depot local sont ici encore du jour.
    assert run["statut"] != "donnees_perimees"


def test_the_run_records_the_mode_read_from_the_state_file(env):
    ecrire_etat(env, dry_run=True)
    run = daily.run_batch(TODAY, gw=FakeGateway(), sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])
    assert run["mode"] == "dry_run"

    ecrire_etat(env, dry_run=False)
    run = daily.run_batch(TODAY, gw=FakeGateway(), sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])
    assert run["mode"] == "reel"


def test_resolve_paths_merges_over_the_defaults():
    resolus = daily.resolve_paths({"journal": "/tmp/x.jsonl"})
    assert resolus["journal"] == "/tmp/x.jsonl"
    assert resolus["state"] == daily.DEFAULT_PATHS["state"]
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_daily.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ibkr_bot.daily'`

- [ ] **Step 3 : Écrire l'implémentation**

Créer `ibkr_bot/daily.py` :

```python
# ibkr_bot/daily.py
# ORCHESTRATEUR DU BATCH QUOTIDIEN — point d'entree : python -m ibkr_bot.daily
#
# CE MODULE EST LE SEUL DE TOUT LE DEPOT D'OU UN ORDRE ACTIONS REEL PEUT
# PARTIR (via ibkr_bot.gateway). Il n'ajoute AUCUNE regle de trading : le
# plafond de 10, le classement par score, le garde-fou de solde, les
# regles de sortie et la reconciliation sont deja implementes et testes
# dans portfolio.py (Plan A) — ici on les appelle, on execute, on
# journalise. Toute regle metier ajoutee ici serait au mauvais endroit.
#
# DEFENSE EN PROFONDEUR (spec 4.3, point 2) : l'etat (kill_switch,
# dry_run) est RECHARGE DEPUIS LE DISQUE juste avant chaque envoi d'ordre,
# meme si run_batch l'a deja verifie au demarrage — exactement ce que fait
# gold_bot/loop.py:execute_steps. Un batch peut durer 20 minutes
# (preflight) : l'etat lu au debut est potentiellement perime.
import os
import subprocess
import time
from datetime import datetime, timezone

import ibkr_bot.contracts as contracts
import ibkr_bot.gateway as gateway
import ibkr_bot.journal as journal
import ibkr_bot.notify as notify
import ibkr_bot.portfolio as portfolio
import ibkr_bot.signals as signals
import ibkr_bot.sizing as sizing
import ibkr_bot.state as state

# Preflight : 3 tentatives espacees de 10 minutes (spec 5.5). Le batch
# demarre a 14:45 UTC et reste donc au plus tard a 15:05 UTC, encore dans
# la plage d'ouverture commune aux 8 places (14:30-15:30 UTC, spec 4.5).
PREFLIGHT_ATTEMPTS = 3
PREFLIGHT_DELAY_SECONDS = 600

# Racine du clone local : le bot lit docs/indices.json et
# docs/signal_tracking.json de CE clone, apres un git pull (spec 4.5).
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GIT_PULL_TIMEOUT_SECONDS = 120

DEFAULT_PATHS = {
    "state": state.STATE_PATH,
    "positions": portfolio.POSITIONS_PATH,
    "journal": journal.REAL_TRADING_LOG_PATH,
    "conid_cache": contracts.CONID_CACHE_PATH,
    "indices": signals.INDICES_PATH,
    "tracking": signals.SIGNAL_TRACKING_PATH,
    "account_snapshot": journal.LATEST_ACCOUNT_PATH,
}


def resolve_paths(paths: dict | None = None) -> dict:
    """Tous les fichiers touches par le batch, en un seul endroit — les
    tests les redirigent en bloc vers un tmp_path. Les fonctions de Plan A
    figent leur chemin par defaut a l'import (argument par defaut), donc
    monkeypatcher leurs constantes de module serait sans effet : les
    chemins doivent etre passes explicitement."""
    resolus = dict(DEFAULT_PATHS)
    resolus.update(paths or {})
    return resolus


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def pull_repo(repo_dir: str = REPO_DIR, run_fn=subprocess.run) -> dict:
    """git pull sur le clone local (spec 4.5). Non bloquant : un echec est
    journalise, et c'est le garde de fraicheur juste apres qui decidera
    s'il y a lieu d'agir — un pull rate sur des donnees deja a jour n'est
    pas un probleme, un pull rate sur des donnees d'hier sera attrape par
    `donnees_perimees`.

    Note d'exploitation : le code deja importe en memoire n'est pas
    remplace par ce pull ; un changement de code ne prend effet qu'au
    batch suivant. C'est exactement le modele de deploy.sh (git pull +
    redemarrage) et c'est voulu — on ne veut pas qu'un batch change de
    version en plein vol."""
    try:
        resultat = run_fn(["git", "pull", "--ff-only"], cwd=repo_dir,
                          capture_output=True, text=True,
                          timeout=GIT_PULL_TIMEOUT_SECONDS)
    except Exception as e:
        return {"ok": False, "detail": f"git pull impossible : {e}"}
    if getattr(resultat, "returncode", 1) != 0:
        return {"ok": False,
                "detail": (getattr(resultat, "stderr", "") or "").strip()[:500]}
    return {"ok": True, "detail": (getattr(resultat, "stdout", "") or "").strip()[:200]}


def preflight(base_url: str, *, gw=gateway, sleep_fn=time.sleep,
              attempts: int = PREFLIGHT_ATTEMPTS,
              delay_s: float = PREFLIGHT_DELAY_SECONDS) -> dict:
    """Le Gateway est-il utilisable ? (spec 5.5)

    "Utilisable" ne veut pas seulement dire "authentifie" : le CPAPI exige
    que /iserver/accounts (brokerage_accounts) ET /portfolio/accounts
    (portfolio_accounts) aient ete appeles au moins une fois dans la
    session avant que les routes portefeuille et ordres ne renvoient des
    donnees reelles. Sans cet amorcage, /portfolio/... renvoie
    SILENCIEUSEMENT DU VIDE — le sens dangereux : la reconciliation
    conclurait que le bot ne detient rien et justifierait un rachat. Un
    echec d'amorcage compte donc comme une tentative ratee, au meme titre
    qu'une session expiree."""
    detail = ""
    for tentative in range(1, attempts + 1):
        try:
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


def _nouveau_run(today: str, mode: str) -> dict:
    return {
        "timestamp": journal.now_iso(),
        "date": today,
        "mode": mode,
        "statut": "termine",
        "git_pull": {"ok": None, "detail": "non tente"},
        "preflight": {"ok": None, "tentatives": 0, "detail": "non tente"},
        "reconciliation": {},
        "sorties": [],
        "entrees": [],
        "signaux_rejetes": [],
        "anomalies": [],
        "erreurs": [],
    }


def _terminer(run: dict, chemins: dict, *, alerte_gateway: bool = False) -> dict:
    """Sortie unique du batch : journal puis email. Le journal AVANT
    l'email pour qu'une panne SMTP ne fasse jamais perdre la trace d'un
    batch qui a reellement passe des ordres."""
    journal.append_run(run, chemins["journal"])
    if alerte_gateway:
        # Le corps de l'alerte dit deja qu'aucun ordre n'a ete passe : un
        # resume vide en plus ne ferait que noyer l'alerte.
        notify.send_gateway_alert(run["preflight"]["tentatives"], run["date"])
    else:
        notify.send_daily_summary(run, run["date"])
    return run


def run_batch(today: str | None = None, *, gw=gateway, sleep_fn=time.sleep,
              base_url: str | None = None, account_id: str | None = None,
              repo_dir: str = REPO_DIR, paths: dict | None = None) -> dict:
    """Un batch quotidien complet. Renvoie la ligne de journal produite.

    ORDRE DES GARDES, qui est lui-meme une decision :
      1. kill_switch  -> aucun appel reseau du tout ;
      2. git pull     -> non bloquant ;
      3. fraicheur    -> AVANT le preflight : si les donnees ne sont pas
         du jour, le batch ne fera rien de toute facon (spec 4.5), donc
         bruler 20 minutes de reauthentification serait inutile — et
         l'email d'alerte Gateway donnerait un mauvais diagnostic ;
      4. preflight    -> 3 tentatives / 10 min, abandon + alerte.
    """
    chemins = resolve_paths(paths)
    today = today or _today()
    base_url = base_url or os.environ.get("IBKR_GATEWAY_URL",
                                          gateway.DEFAULT_GATEWAY_URL)
    account_id = account_id or os.environ.get("IBKR_ACCOUNT_ID", "")

    etat = state.load_state(chemins["state"])
    run = _nouveau_run(today, "dry_run" if etat["dry_run"] else "reel")

    # 1. Interrupteur d'urgence : on ne touche meme pas au reseau.
    if etat["kill_switch"]:
        run["statut"] = "kill_switch"
        return _terminer(run, chemins)

    # 2. Donnees du jour : git pull sur le clone local (spec 4.5).
    run["git_pull"] = pull_repo(repo_dir)

    # 3. Garde de fraicheur, non negociable (spec 4.5).
    indices = signals.load_indices(chemins["indices"])
    if not signals.indices_are_fresh(indices, today):
        run["statut"] = "donnees_perimees"
        run["signaux_rejetes"].append({
            "ticker": None, "raison": "donnees_perimees",
            "rang": None, "score": None,
        })
        return _terminer(run, chemins)

    # 4. Preflight du Gateway (spec 5.5).
    run["preflight"] = preflight(base_url, gw=gw, sleep_fn=sleep_fn)
    if not run["preflight"]["ok"]:
        run["statut"] = "gateway_indisponible"
        return _terminer(run, chemins, alerte_gateway=True)

    # Les taches 4 et 5 inserent ici : reconciliation, sorties, entrees.
    return _terminer(run, chemins)


def main() -> None:
    run_batch()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_daily.py -v`
Expected: PASS (tous)

Run: `python -m pytest`
Expected: PASS — y compris `tests/ibkr_bot/test_gateway.py::test_no_other_plan_a_module_references_the_order_routes`, qui passe encore parce que `daily.py` ne mentionne aucune route d'ordre à ce stade.

- [ ] **Step 5 : Commit**

```bash
git add ibkr_bot/daily.py tests/ibkr_bot/test_daily.py
git commit -m "feat(ibkr_bot): daily.py — git pull, preflight et gardes d'abandon (Plan B, tache 3)"
```

---

### Task 4 : `daily._place_order` — le seul chemin d'envoi d'ordre réel

> **REVUE LA PLUS STRICTE POSSIBLE. C'est la tâche la plus dangereuse des deux plans.** Chaque ligne de cette fonction peut déplacer de l'argent réel. À relire ligne à ligne : le rechargement d'état, le `side`, la `quantity`, la boucle de confirmation (et sa borne), l'unité du prix récupéré. Ne pas approuver sans avoir vérifié à la main que `_place_order` ne peut **pas** appeler `gw.place_market_order` quand `dry_run` ou `kill_switch` est vrai sur disque.

**Files:**
- Create: (rien)
- Modify: `ibkr_bot/daily.py` (ajout de `_place_order`, `_resoudre_confirmations`, `_prix_execute`)
- Modify: `tests/ibkr_bot/test_gateway.py:453-477` (élargissement **étroit** du garde structurel)
- Test: `tests/ibkr_bot/test_daily.py` (ajouts)

**Interfaces:**
- Consumes: `gateway.place_market_order(base_url, account_id, conid, side, quantity) -> list[dict]`, `gateway.confirm_reply(base_url, reply_id, confirmed=True) -> list[dict]`, `gateway.order_status(base_url, order_id) -> dict`, `state.load_state(path)`.
- Produces: `MAX_CONFIRMATIONS = 5`, `_place_order(gw, base_url, account_id, *, ticker, conid, side, quantity, prix_reference_cotation, state_path) -> dict` renvoyant exactement la forme que `journal.build_order_record` attend en `execution` :
  `{"statut": "execute"|"simule"|"erreur", "order_id": str|None, "prix_execution_cotation": float|None, "prix_execution_estime": bool, "commission": float|None, "detail": str|None}`.

**Trois comportements réels du CPAPI à traiter, vérifiés dans `gateway.py` et ses tests :**

1. `place_market_order` renvoie **une liste**, dont les entrées sont soit une confirmation d'ordre (`{"order_id": ..., "order_status": ...}`), soit **une question à confirmer** (`{"id": "<replyId>", "message": [...]}`). Il faut boucler sur `confirm_reply` — avec une borne, parce qu'une chaîne de questions sans fin bloquerait le batch.
2. `order_status` porte `avgPrice` **en chaîne** (`"415.20"` dans les fixtures existantes) et **dans la devise de cotation** (GBp pour le LSE).
3. `avgPrice` peut manquer si l'ordre n'est pas encore exécuté. Repli : le prix de référence du sizing, avec `prix_execution_estime: True` — jamais `None` silencieux, parce que ce prix devient la référence du stop-loss.

- [ ] **Step 1 : Élargir le garde structurel (de façon étroite) et vérifier qu'il passe toujours**

Remplacer `test_no_other_plan_a_module_references_the_order_routes` dans `tests/ibkr_bot/test_gateway.py` (lignes 453-477) par :

```python
def test_only_gateway_and_daily_may_reference_the_order_functions():
    """Garantie structurelle, elargie AU PLUS ETROIT pour le Plan B.

    - Les NOMS de fonction (place_market_order, confirm_reply) : definis
      par gateway.py, et appeles par daily.py — le seul appelant legitime
      (spec 4.3, point 2 : "daily.py est le seul endroit ou un ordre reel
      part"). Tout AUTRE module du paquet qui les mentionne est un
      contournement du garde.
    - Les ROUTES HTTP : interdites PARTOUT hors gateway.py, DAILY.PY
      COMPRISE. daily.py appelle gateway.py, elle ne reimplemente jamais
      l'appel HTTP — sinon la garantie "un seul module parle a IBKR"
      (spec 4.3, point 1) ne vaudrait plus rien.

    rglob (recursif) plutot que glob : un futur sous-paquet (ex.
    ibkr_bot/steps/) doit etre scanne lui aussi."""
    import pathlib

    package_dir = pathlib.Path(gateway.__file__).parent
    noms_de_fonction = ("place_market_order", "confirm_reply")
    routes = ("/iserver/account/", "/iserver/reply/")
    appelants_autorises = {"gateway.py", "daily.py"}

    fautifs = []
    for source in sorted(package_dir.rglob("*.py")):
        texte = source.read_text(encoding="utf-8")
        if source.name != "gateway.py":
            for route in routes:
                if route in texte:
                    fautifs.append(f"{source.name} construit la route {route!r}")
        if source.name not in appelants_autorises:
            for nom in noms_de_fonction:
                if nom in texte:
                    fautifs.append(f"{source.name} mentionne {nom!r}")

    assert fautifs == [], (
        "Seuls gateway.py (definition) et daily.py (appel) peuvent "
        "reference les fonctions de passage d'ordre, et seul gateway.py "
        "peut construire leurs routes HTTP : " + "; ".join(fautifs)
    )
```

Run: `python -m pytest tests/ibkr_bot/test_gateway.py -v`
Expected: PASS — le garde est plus strict qu'avant (il interdit maintenant aussi `/iserver/reply/`), et `daily.py` ne mentionne encore rien.

- [ ] **Step 2 : Écrire les tests qui échouent**

Ajouter à la fin de `tests/ibkr_bot/test_daily.py` :

```python
# --- _place_order : le seul chemin d'ordre reel ----------------------

def _etat(tmp_path, **champs):
    import json as _json
    chemin = tmp_path / "state.json"
    chemin.write_text(_json.dumps({"kill_switch": False, "dry_run": True, **champs}),
                      encoding="utf-8")
    return str(chemin)


def test_place_order_sends_nothing_in_dry_run(tmp_path):
    gw = FakeGateway()
    resultat = daily._place_order(
        gw, "https://127.0.0.1:5000", "U1", ticker="MC.PA", conid=17275,
        side="BUY", quantity=5, prix_reference_cotation=90.0,
        state_path=_etat(tmp_path, dry_run=True))

    assert gw.ordres == []
    assert gw.confirmations == []
    assert resultat["statut"] == "simule"
    assert resultat["order_id"] is None


def test_place_order_sends_nothing_when_the_kill_switch_is_on(tmp_path):
    gw = FakeGateway()
    resultat = daily._place_order(
        gw, "https://127.0.0.1:5000", "U1", ticker="MC.PA", conid=17275,
        side="BUY", quantity=5, prix_reference_cotation=90.0,
        state_path=_etat(tmp_path, dry_run=False, kill_switch=True))

    assert gw.ordres == []
    assert resultat["statut"] == "simule"


def test_place_order_rereads_the_state_from_disk_at_each_call(tmp_path):
    """DEFENSE EN PROFONDEUR : l'etat lu au demarrage du batch a pu
    changer pendant les 20 minutes de preflight. La valeur qui compte est
    celle du disque AU MOMENT de l'envoi."""
    gw = FakeGateway()
    chemin = _etat(tmp_path, dry_run=False)

    premier = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=chemin)
    assert premier["statut"] == "execute"
    assert len(gw.ordres) == 1

    _etat(tmp_path, dry_run=False, kill_switch=True)
    second = daily._place_order(
        gw, "u", "U1", ticker="ADBE", conid=202070, side="BUY", quantity=1,
        prix_reference_cotation=400.0, state_path=chemin)
    assert second["statut"] == "simule"
    assert len(gw.ordres) == 1   # aucun ordre supplementaire


def test_place_order_sends_the_right_side_conid_and_quantity(tmp_path):
    gw = FakeGateway()
    daily._place_order(gw, "u", "U1", ticker="SAP.DE", conid=40000, side="SELL",
                       quantity=2, prix_reference_cotation=100.0,
                       state_path=_etat(tmp_path, dry_run=False))

    assert gw.ordres == [{"conid": 40000, "side": "SELL", "quantity": 2}]


def test_place_order_answers_a_confirmation_question(tmp_path):
    gw = FakeGateway()
    gw.reponse_ordre = [{"id": "e1f2-0001", "message": ["Confirmez l'ordre au marché"]}]

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert gw.confirmations == ["e1f2-0001"]
    assert resultat["statut"] == "execute"
    assert resultat["order_id"] == "1234"


def test_place_order_gives_up_on_an_endless_confirmation_chain(tmp_path):
    class _Gw(FakeGateway):
        def confirm_reply(self, base_url, reply_id, confirmed=True):
            self.confirmations.append(reply_id)
            return [{"id": f"question-{len(self.confirmations)}", "message": ["encore"]}]

    gw = _Gw()
    gw.reponse_ordre = [{"id": "q0", "message": ["?"]}]

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert len(gw.confirmations) == daily.MAX_CONFIRMATIONS
    assert resultat["statut"] == "erreur"
    assert "confirmation" in resultat["detail"].lower()


def test_place_order_reads_the_average_fill_price(tmp_path):
    gw = FakeGateway()
    gw.statuts_ordre = {"1234": {"order_status": "Filled", "avgPrice": "91.25",
                                 "commission": "1.10"}}

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["prix_execution_cotation"] == pytest.approx(91.25)
    assert resultat["prix_execution_estime"] is False
    assert resultat["commission"] == pytest.approx(1.10)


def test_place_order_falls_back_to_the_reference_price_when_avgprice_is_missing(tmp_path):
    """Ce prix devient la reference du stop-loss : il ne doit jamais rester
    None en silence. Le repli est signale par prix_execution_estime."""
    gw = FakeGateway()
    gw.statuts_ordre = {"1234": {"order_status": "Submitted"}}

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["prix_execution_cotation"] == pytest.approx(90.0)
    assert resultat["prix_execution_estime"] is True
    assert resultat["statut"] == "execute"


def test_place_order_falls_back_when_order_status_raises(tmp_path):
    class _Gw(FakeGateway):
        def order_status(self, base_url, order_id):
            raise RuntimeError("504 Gateway Timeout")

    gw = _Gw()
    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    # L'ordre est parti : on ne doit SURTOUT pas le declarer en echec.
    assert resultat["statut"] == "execute"
    assert resultat["prix_execution_estime"] is True


def test_place_order_reports_a_rejected_order_without_raising(tmp_path):
    gw = FakeGateway()
    gw.echecs = {17275}

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["statut"] == "erreur"
    assert "rejet IBKR" in resultat["detail"]
    assert resultat["order_id"] is None


def test_place_order_reports_a_response_without_any_order_id(tmp_path):
    gw = FakeGateway()
    gw.reponse_ordre = [{"error": "no trading permission"}]

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["statut"] == "erreur"
    assert "no trading permission" in resultat["detail"]
```

- [ ] **Step 3 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_daily.py -k place_order -v`
Expected: FAIL — `AttributeError: module 'ibkr_bot.daily' has no attribute '_place_order'`

- [ ] **Step 4 : Écrire l'implémentation**

Ajouter dans `ibkr_bot/daily.py`, juste après `preflight` :

```python
# Borne de la boucle de confirmation : le CPAPI peut repondre a un ordre
# par une question (marche ferme, ordre au marche hors seance, taille
# inhabituelle...), et la reponse a une question peut elle-meme etre une
# question. Sans borne, une chaine sans fin bloquerait le batch dans la
# fenetre de marche.
MAX_CONFIRMATIONS = 5


def _nombre(valeur):
    """float(valeur) ou None — le CPAPI renvoie avgPrice et commission
    tantot en nombre, tantot en CHAINE ("415.20" dans les fixtures de
    tests/ibkr_bot/test_gateway.py)."""
    if isinstance(valeur, bool) or valeur is None:
        return None
    try:
        nombre = float(valeur)
    except (TypeError, ValueError):
        return None
    return None if nombre != nombre else nombre  # NaN -> None


def _premier_order_id(reponse) -> str | None:
    for entree in reponse or []:
        if isinstance(entree, dict) and entree.get("order_id"):
            return str(entree["order_id"])
    return None


def _premiere_question(reponse) -> str | None:
    """replyId d'une question de confirmation, ou None. Une question porte
    `id` + `message` ; une confirmation d'ordre porte `order_id`."""
    for entree in reponse or []:
        if (isinstance(entree, dict) and entree.get("id")
                and not entree.get("order_id")):
            return str(entree["id"])
    return None


def _resoudre_confirmations(gw, base_url: str, reponse) -> tuple[list, int, str | None]:
    """Repond aux eventuelles questions de confirmation, au plus
    MAX_CONFIRMATIONS fois. Renvoie (derniere reponse, nombre de
    confirmations, erreur)."""
    confirmations = 0
    while confirmations < MAX_CONFIRMATIONS:
        reply_id = _premiere_question(reponse)
        if reply_id is None:
            return reponse, confirmations, None
        reponse = gw.confirm_reply(base_url, reply_id)
        confirmations += 1
    if _premiere_question(reponse) is not None:
        return reponse, confirmations, (
            f"chaine de confirmation non resolue apres {confirmations} reponses")
    return reponse, confirmations, None


def _place_order(gw, base_url: str, account_id: str, *, ticker: str, conid,
                 side: str, quantity: int, prix_reference_cotation,
                 state_path: str) -> dict:
    """LE SEUL ENDROIT DU DEPOT D'OU UN ORDRE ACTIONS REEL PART.

    DEFENSE EN PROFONDEUR (spec 4.3 point 2, copie conforme de
    gold_bot.loop.execute_steps) : l'etat est RELU SUR LE DISQUE ici, a
    chaque appel, meme si run_batch l'a deja verifie au demarrage. Entre
    les deux, il a pu se passer 20 minutes de preflight et plusieurs
    autres ordres. Ne jamais remplacer cette relecture par un booleen
    passe en parametre.

    `prix_reference_cotation` est le prix de reference du sizing, DANS LA
    DEVISE DE COTATION (pence pour le LSE) : il sert de repli si IBKR ne
    donne pas encore de prix moyen d'execution. Le resultat renvoye est
    lui aussi en devise de cotation — c'est journal.build_order_record
    qui le ramene en devise de compte.
    """
    etat = state.load_state(state_path)
    if etat["kill_switch"] or etat["dry_run"]:
        return {"statut": "simule", "order_id": None,
                "prix_execution_cotation": None, "prix_execution_estime": False,
                "commission": None,
                "detail": "dry_run ou kill_switch actif : aucun ordre envoye"}

    try:
        reponse = gw.place_market_order(base_url, account_id, conid, side, quantity)
    except Exception as e:
        return {"statut": "erreur", "order_id": None,
                "prix_execution_cotation": None, "prix_execution_estime": False,
                "commission": None, "detail": f"{ticker} : {e}"}

    try:
        reponse, _, erreur = _resoudre_confirmations(gw, base_url, reponse)
    except Exception as e:
        return {"statut": "erreur", "order_id": None,
                "prix_execution_cotation": None, "prix_execution_estime": False,
                "commission": None, "detail": f"{ticker} : confirmation refusee : {e}"}
    if erreur is not None:
        return {"statut": "erreur", "order_id": None,
                "prix_execution_cotation": None, "prix_execution_estime": False,
                "commission": None, "detail": f"{ticker} : {erreur}"}

    order_id = _premier_order_id(reponse)
    if order_id is None:
        return {"statut": "erreur", "order_id": None,
                "prix_execution_cotation": None, "prix_execution_estime": False,
                "commission": None,
                "detail": f"{ticker} : reponse sans order_id : {reponse}"}

    # L'ORDRE EST PARTI. A partir d'ici, plus aucune erreur ne doit faire
    # renvoyer "erreur" : le declarer en echec alors qu'il est execute
    # ferait croire au lendemain que la position n'existe pas.
    prix, estime, commission = prix_reference_cotation, True, None
    try:
        statut = gw.order_status(base_url, order_id)
        prix_moyen = _nombre(statut.get("avgPrice"))
        if prix_moyen is not None and prix_moyen > 0:
            prix, estime = prix_moyen, False
        commission = _nombre(statut.get("commission"))
    except Exception as e:
        print(f"Prix d'execution indisponible pour {ticker} ({order_id}) : {e}")

    return {"statut": "execute", "order_id": order_id,
            "prix_execution_cotation": prix, "prix_execution_estime": estime,
            "commission": commission, "detail": None}
```

- [ ] **Step 5 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_daily.py -v`
Expected: PASS (tous)

Run: `python -m pytest tests/ibkr_bot/test_gateway.py -v`
Expected: PASS — `daily.py` mentionne maintenant `place_market_order`/`confirm_reply`, ce que le garde élargi autorise, mais ne construit aucune route HTTP, ce qu'il interdit toujours.

Run: `python -m pytest`
Expected: PASS

- [ ] **Step 6 : Commit**

```bash
git add ibkr_bot/daily.py tests/ibkr_bot/test_daily.py tests/ibkr_bot/test_gateway.py
git commit -m "feat(ibkr_bot): envoi d'ordre reel avec defense en profondeur (Plan B, tache 4)"
```

---

### Task 5 : `daily.run_batch` — réconciliation, sorties, entrées

> **REVUE LA PLUS STRICTE POSSIBLE.** C'est ici que les 6 modules découplés de Plan A sont recousus pour la première fois : chaque nom de champ et chaque ordre d'appel doit correspondre au **vrai** code de Plan A. La revue finale de Plan A avait trouvé un vrai défaut de conception (le modèle de financement) que les revues de tâches isolées ne pouvaient pas voir, précisément parce que les modules n'ont aucun import croisé.

**Files:**
- Modify: `ibkr_bot/daily.py` (corps de `run_batch` + helpers d'étape)
- Test: `tests/ibkr_bot/test_daily.py` (ajouts)

**Interfaces:**
- Consumes: `portfolio.reconcile(local, ibkr) -> {"actives","cloturees_hors_bot","anomalies_quantite","ignorees"}`, `portfolio.positions_to_close(open_positions, companies_by_ticker, today) -> [{"position","close_reason","current_price"}]`, `portfolio.select_entries(signals, open_positions, plans, contrats, base_cash) -> (retenus, rejets)` où `retenus = [{"signal","plan","rang"}]`, `portfolio.load_positions(path)`, `signals.collect_new_signals(indices, positions, today) -> (signaux, rejets)`, `signals.load_signal_tracking(path)`, `sizing.compute_quantity(ticker, index_currency, unit_price_indices, fx_rate) -> plan`, `sizing.to_quotation_price(prix, ticker)`, `contracts.resolve_conid(ticker, search_fn, info_fn, cache, today)`, `contracts.load_cache/save_cache`, `journal.build_order_record/build_position_record/save_positions_safely/save_account_snapshot`, `daily._place_order` (tâche 4).
- Produces: un `run` complet conforme au schéma de la tâche 1, et un `positions.json` à jour.

**Six décisions d'orchestration, à conserver et à commenter dans le code :**

1. **En `dry_run`, la réconciliation ne ferme rien.** Les positions simulées n'existent évidemment pas chez IBKR : laisser `reconcile` les classer `cloturees_hors_bot` viderait `positions.json` chaque jour et rendrait la validation en simulation (spec 5.3 : « comparer, sur plusieurs semaines et sans risque, les décisions du bot réel à celles du paper-trading ») **totalement vide de sens** — aucune sortie ne serait jamais observée. En `dry_run` on calcule et on journalise quand même la réconciliation (pour voir ce qu'elle dirait), mais les positions actives restent celles du journal local. En mode réel, `reconcile` fait foi, sans exception.
2. **Si les positions IBKR sont illisibles, le batch s'arrête** (`reconciliation_impossible`). Spec 5.4 : le bot ne se fie jamais à son seul état local, et spec 5.5 : aucune décision sur donnée incomplète. Agir sans savoir ce que le compte détient, c'est risquer de racheter une ligne déjà détenue.
3. **Les sorties passent avant les entrées**, et les entrées voient les places libérées par les sorties du jour — même enchaînement que `_close_eligible_positions()` puis `_open_new_signal_positions()` dans le paper-trading, ce qui préserve la comparabilité (spec 1).
4. **`positions.json` est réécrit après CHAQUE ordre exécuté**, pas seulement en fin de batch. Un plantage entre un achat réussi et une sauvegarde de fin de batch laisserait une position réelle absente du journal ; le lendemain, `reconcile` la classerait `ignorees` (« position de l'utilisateur ») et le bot pourrait la racheter. Le coût est dérisoire (au plus 10 écritures atomiques d'un fichier minuscule).
5. **Garde d'idempotence supplémentaire : un signal dont le `conid` est déjà détenu sur le compte n'est jamais acheté**, même s'il est absent de `positions.json` (motif `deja_detenu_hors_journal`). C'est la ceinture de la bretelle précédente : elle ferme la fenêtre de plantage résiduelle et ne touche jamais à la position de l'utilisateur (spec 9.5) — elle se contente de refuser d'en acheter davantage.
6. **Un ticker qui quitte le portefeuille aujourd'hui n'est jamais racheté dans le même batch.** Deux cas, un seul garde : le bot vient de le vendre lui-même (motif `vendu_aujourd_hui`) — racheter dans la minute un titre stop-lossé serait absurde et coûterait deux commissions ; ou la réconciliation l'a trouvé absent du compte (motif `cloturee_hors_bot`) — la spec 5.4 est explicite : « Le bot ne la "rouvre" jamais ». Sans ce garde, la séquence sorties-puis-entrées rouvrirait la position que l'étape précédente vient tout juste de retirer de `positions_ouvertes`, et le filtre `deja_en_portefeuille` de `select_entries` ne verrait plus rien.

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter à la fin de `tests/ibkr_bot/test_daily.py` :

```python
# --- run_batch complet : sorties et entrees ---------------------------

def _positions_locales(env, positions):
    import json as _json
    with open(env["paths"]["positions"], "w", encoding="utf-8") as fh:
        _json.dump({"positions": positions}, fh)


POSITION_MC = {
    "id": "MC.PA-2026-03-02", "ticker": "MC.PA", "name": "LVMH", "index": "CAC40",
    "conid": 17275, "devise": "EUR", "quantite": 5,
    "prix_execution_reference": 200.0, "paper_entry_price": 198.0,
    "date_entree": "2026-03-02", "target_exit_price": 300.0,
    "date_limite": "2026-09-02",
}


def test_dry_run_places_no_order_but_journals_everything(env):
    """LE TEST LE PLUS IMPORTANT DE TOUTE LA SUITE (spec 7) : en dry_run,
    aucun appel de passage d'ordre, et pourtant un journal complet."""
    ecrire_etat(env, dry_run=True)
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert gw.ordres == []
    assert gw.confirmations == []
    assert run["mode"] == "dry_run"
    assert run["statut"] == "termine"
    assert [e["ticker"] for e in run["entrees"]] == ["MC.PA", "ADBE"]
    assert all(e["statut"] == "simule" for e in run["entrees"])
    assert run["entrees"][0]["quantite"] == 5          # floor(500 / 90)
    assert run["entrees"][0]["rang"] == 1              # score 55.2 > 40.0
    assert run["entrees"][0]["conid"] == 17275
    assert run["entrees"][0]["prix_reference_sizing"] == 90.0
    assert run["entrees"][0]["prix_paper"] == 88.0
    journalise = lire_journal(env)[0]
    assert [e["ticker"] for e in journalise["entrees"]] == ["MC.PA", "ADBE"]


def test_real_mode_buys_the_selected_signals(env):
    ecrire_etat(env, dry_run=False)
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert [(o["conid"], o["side"], o["quantity"]) for o in gw.ordres] == [
        (17275, "BUY", 5), (202070, "BUY", 1)]
    assert all(e["statut"] == "execute" for e in run["entrees"])
    import ibkr_bot.portfolio as portfolio
    enregistrees = portfolio.load_positions(env["paths"]["positions"])
    assert {p["ticker"] for p in enregistrees} == {"MC.PA", "ADBE"}
    assert enregistrees[0]["date_limite"] == "2027-03-15"


def test_a_failed_order_does_not_stop_the_following_ones(env):
    """Spec 5.5 / 7 : un ordre en echec n'interrompt pas le batch."""
    ecrire_etat(env, dry_run=False)
    gw = FakeGateway()
    gw.echecs = {17275}   # MC.PA, le mieux classe, echoue

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    par_ticker = {e["ticker"]: e for e in run["entrees"]}
    assert par_ticker["MC.PA"]["statut"] == "erreur"
    assert par_ticker["ADBE"]["statut"] == "execute"
    import ibkr_bot.portfolio as portfolio
    enregistrees = portfolio.load_positions(env["paths"]["positions"])
    assert [p["ticker"] for p in enregistrees] == ["ADBE"]


def test_a_failed_order_is_never_retried(env):
    """Spec 5.5 : pas de reprise automatique d'un ordre echoue."""
    ecrire_etat(env, dry_run=False)
    gw = FakeGateway()
    gw.echecs = {17275}

    daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                    account_id="U1", paths=env["paths"])

    assert [o["conid"] for o in gw.ordres].count(17275) == 1


def test_a_position_already_in_the_journal_is_not_bought_again(env):
    """Spec 7 : relance du batch le meme jour -> pas de double achat."""
    ecrire_etat(env, dry_run=False)
    deja = {**POSITION_MC, "id": "MC.PA-2026-09-15", "date_entree": TODAY,
            "date_limite": "2027-03-15", "prix_execution_reference": 90.5}
    _positions_locales(env, [deja])
    gw = FakeGateway(positions_ibkr=[{"conid": 17275, "position": 5.0}])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert [o["conid"] for o in gw.ordres] == [202070]   # ADBE seulement
    rejets = {r["ticker"]: r["raison"] for r in run["signaux_rejetes"]}
    assert rejets["MC.PA"] == "deja_en_portefeuille"


def test_a_position_held_at_ibkr_but_absent_from_the_journal_is_not_bought(env):
    """Ceinture de la bretelle : si le batch a plante entre l'envoi de
    l'ordre et l'ecriture de positions.json, la ligne existe chez IBKR mais
    pas dans le journal. reconcile la classerait `ignorees` (position de
    l'utilisateur) et rien n'empecherait un rachat."""
    ecrire_etat(env, dry_run=False)
    _positions_locales(env, [])
    gw = FakeGateway(positions_ibkr=[{"conid": 17275, "position": 5.0}])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert [o["conid"] for o in gw.ordres] == [202070]
    rejets = {r["ticker"]: r["raison"] for r in run["signaux_rejetes"]}
    assert rejets["MC.PA"] == "deja_detenu_hors_journal"


def test_a_stop_loss_position_is_sold(env):
    """docs/indices.json donne MC.PA a 90 EUR ; la position a ete ouverte a
    200 EUR -> -55 %, bien au-dela du stop-loss a -20 %."""
    ecrire_etat(env, dry_run=False)
    _positions_locales(env, [POSITION_MC])
    gw = FakeGateway(positions_ibkr=[{"conid": 17275, "position": 5.0}])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    ventes = [o for o in gw.ordres if o["side"] == "SELL"]
    assert ventes == [{"conid": 17275, "side": "SELL", "quantity": 5}]
    assert run["sorties"][0]["close_reason"] == "stop_loss"
    assert run["sorties"][0]["statut"] == "execute"
    import ibkr_bot.portfolio as portfolio
    assert all(p["ticker"] != "MC.PA"
               for p in portfolio.load_positions(env["paths"]["positions"]))


def test_exits_run_before_entries_and_free_a_slot(env):
    """Enchainement identique au paper-trading (clotures puis ouvertures) :
    le plafond de 10 est atteint, mais une position sort aujourd'hui, donc
    exactement un signal doit pouvoir entrer."""
    ecrire_etat(env, dry_run=False)
    occupees = [POSITION_MC] + [
        {**POSITION_MC, "id": f"X{i}-2026-03-02", "ticker": f"X{i}.PA",
         "conid": 900 + i, "prix_execution_reference": 10.0,
         "target_exit_price": 999.0, "date_limite": "2027-01-01"}
        for i in range(9)
    ]
    _positions_locales(env, occupees)
    gw = FakeGateway(positions_ibkr=[{"conid": p["conid"], "position": 5.0}
                                     for p in occupees])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert [o["side"] for o in gw.ordres].count("SELL") == 1
    achats = [o for o in gw.ordres if o["side"] == "BUY"]
    assert [o["conid"] for o in achats] == [202070]   # ADBE, le seul restant


def test_a_ticker_sold_today_is_never_bought_back_in_the_same_batch(env):
    """MC.PA sort en stop_loss aujourd'hui ET porte un nouveau signal
    d'entree du jour. Sans garde, la sequence sorties-puis-entrees le
    rachete dans la minute : deux commissions pour revenir au point de
    depart, sur un titre qu'on vient justement de stop-losser."""
    ecrire_etat(env, dry_run=False)
    _positions_locales(env, [POSITION_MC])
    gw = FakeGateway(positions_ibkr=[{"conid": 17275, "position": 5.0}])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert [(o["conid"], o["side"]) for o in gw.ordres] == [
        (17275, "SELL"), (202070, "BUY")]
    rejets = {r["ticker"]: r["raison"] for r in run["signaux_rejetes"]}
    assert rejets["MC.PA"] == "vendu_aujourd_hui"


def test_a_london_fill_price_is_stored_in_pounds(env):
    """GARDE-FOU PENCE/LIVRE de bout en bout (spec 4.7) : IBKR renvoie
    296 PENCE, docs/indices.json parle en LIVRES. Si 296 atterrissait dans
    positions.json, le stop-loss serait declenche des le lendemain et la
    quantite achetee serait fausse d'un facteur 100."""
    import json as _json
    ecrire_etat(env, dry_run=False)
    tracking = {"positions": [
        {"id": "III.L-2026-09-15", "ticker": "III.L", "name": "3i", "index": "FTSE",
         "status": "open", "entry_date": TODAY, "entry_price": 2.93,
         "target_exit_price": 4.0}]}
    with open(env["paths"]["tracking"], "w", encoding="utf-8") as fh:
        _json.dump(tracking, fh)
    gw = FakeGateway()
    gw.statuts_ordre = {"1234": {"order_status": "Filled", "avgPrice": "296.0"}}

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    # Budget 500 EUR * 0.86 = 430 GBP = 43000 GBp ; 43000 / 295 -> 145.
    assert gw.ordres == [{"conid": 98765, "side": "BUY", "quantity": 145}]
    entree = run["entrees"][0]
    assert entree["prix_execution_cotation"] == pytest.approx(296.0)
    assert entree["prix_execution"] == pytest.approx(2.96)
    assert entree["devise_cotation"] == "GBp"
    assert entree["devise_compte"] == "GBP"
    import ibkr_bot.portfolio as portfolio
    position = portfolio.load_positions(env["paths"]["positions"])[0]
    assert position["prix_execution_reference"] == pytest.approx(2.96)


def test_unreadable_ibkr_positions_abort_the_batch(env):
    """Spec 5.4 : jamais de decision sans savoir ce que le compte detient."""
    ecrire_etat(env, dry_run=False)

    class _Gw(FakeGateway):
        def positions(self, base_url, account_id):
            raise RuntimeError("500 Internal Server Error")

    gw = _Gw()
    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "reconciliation_impossible"
    assert gw.ordres == []
    assert run["erreurs"][0]["etape"] == "positions_ibkr"


def test_dry_run_keeps_simulated_positions_across_days(env):
    """En dry_run, les positions simulees n'existent pas chez IBKR :
    laisser reconcile les fermer viderait positions.json chaque jour et
    rendrait la validation en simulation (spec 5.3) sans objet."""
    ecrire_etat(env, dry_run=True)
    simulee = {**POSITION_MC, "ticker": "ZZZ.PA", "conid": 555,
               "target_exit_price": 999.0, "date_limite": "2027-01-01",
               "prix_execution_reference": 10.0}
    _positions_locales(env, [simulee])
    gw = FakeGateway(positions_ibkr=[])

    daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                    account_id="U1", paths=env["paths"])

    import ibkr_bot.portfolio as portfolio
    restantes = portfolio.load_positions(env["paths"]["positions"])
    assert "ZZZ.PA" in {p["ticker"] for p in restantes}


def test_real_mode_drops_a_position_sold_outside_the_bot(env):
    """Spec 5.4 : vendue a la main par l'utilisateur -> retiree du decompte
    des 10, et « le bot ne la rouvre JAMAIS » — pas meme sur un nouveau
    signal du jour portant le meme ticker."""
    ecrire_etat(env, dry_run=False)
    _positions_locales(env, [POSITION_MC])
    gw = FakeGateway(positions_ibkr=[])   # plus rien chez IBKR

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["reconciliation"]["cloturees_hors_bot"] == ["MC.PA-2026-03-02"]
    assert [o["side"] for o in gw.ordres].count("SELL") == 0
    assert 17275 not in [o["conid"] for o in gw.ordres]
    rejets = {r["ticker"]: r["raison"] for r in run["signaux_rejetes"]}
    assert rejets["MC.PA"] == "cloturee_hors_bot"
    import ibkr_bot.portfolio as portfolio
    assert all(p["ticker"] != "MC.PA"
               for p in portfolio.load_positions(env["paths"]["positions"]))


def test_a_position_without_a_reference_price_raises_an_anomaly(env):
    """portfolio.exit_reason laisse volontairement une telle position
    INCLOSABLE (ecart #4 documente dans portfolio.py) et dit explicitement
    que c'est au Plan B de la signaler a un operateur."""
    ecrire_etat(env, dry_run=False)
    cassee = {**POSITION_MC, "prix_execution_reference": None}
    _positions_locales(env, [cassee])
    gw = FakeGateway(positions_ibkr=[{"conid": 17275, "position": 5.0}])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    types = {a["type"] for a in run["anomalies"]}
    assert "prix_reference_absent" in types
    assert [o["side"] for o in gw.ordres].count("SELL") == 0


def test_an_unavailable_exchange_rate_rejects_the_signal_without_buying(env):
    ecrire_etat(env, dry_run=False)

    class _Gw(FakeGateway):
        def exchange_rate(self, base_url, source, target):
            raise RuntimeError("503 Service Unavailable")

    gw = _Gw()
    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert gw.ordres == []
    raisons = {r["raison"] for r in run["signaux_rejetes"]}
    assert "prix_ou_taux_invalide" in raisons
    assert any(e["etape"] == "taux_de_change" for e in run["erreurs"])


def test_the_account_snapshot_is_written(env):
    ecrire_etat(env, dry_run=False)
    daily.run_batch(TODAY, gw=FakeGateway(), sleep_fn=lambda s: None,
                    account_id="U1", paths=env["paths"])

    with open(env["paths"]["account_snapshot"], encoding="utf-8") as fh:
        instantane = json.load(fh)
    assert instantane["base_cash"] == 10000.0
    assert instantane["fetched_at"].endswith("Z")


def test_a_positions_save_failure_is_reported_in_the_run(env, monkeypatch):
    """positions.json est un ETAT, pas un log : son echec d'ecriture doit
    remonter dans l'email, contrairement a append_run."""
    ecrire_etat(env, dry_run=False)
    monkeypatch.setattr(daily.journal, "save_positions_safely",
                        lambda positions, path: False)
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert any(e["etape"] == "positions.json" for e in run["erreurs"])


def test_the_conid_cache_is_persisted_between_runs(env):
    ecrire_etat(env, dry_run=True)
    daily.run_batch(TODAY, gw=FakeGateway(), sleep_fn=lambda s: None,
                    account_id="U1", paths=env["paths"])

    with open(env["paths"]["conid_cache"], encoding="utf-8") as fh:
        cache = json.load(fh)
    assert cache["MC.PA"]["conid"] == 17275


def test_main_runs_a_batch(monkeypatch):
    appels = []
    monkeypatch.setattr(daily, "run_batch", lambda *a, **k: appels.append(True))
    daily.main()
    assert appels == [True]
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_daily.py -v`
Expected: FAIL — les nouveaux tests échouent (`run["entrees"] == []`, `gw.ordres == []`, `KeyError`), les anciens passent toujours.

- [ ] **Step 3 : Écrire l'implémentation**

Dans `ibkr_bot/daily.py`, ajouter les helpers d'étape avant `run_batch`, puis remplacer le corps de `run_batch` entre le préflight et le `return _terminer(...)` final.

Helpers à ajouter (juste après `_place_order`) :

```python
def _taux_de_change(gw, base_url: str, devise: str, cache: dict, run: dict) -> float:
    """Taux EUR -> devise de l'indice, mis en cache par devise sur la
    duree du batch. Un echec renvoie 0.0, que sizing.compute_quantity
    traduit en motif `prix_ou_taux_invalide` et select_entries en rejet :
    on echoue bruyamment plutot que d'acheter sur un taux devine."""
    if devise in cache:
        return cache[devise]
    try:
        taux = gw.exchange_rate(base_url, "EUR", devise)
    except Exception as e:
        run["erreurs"].append({"etape": "taux_de_change",
                               "detail": f"EUR->{devise} : {e}"})
        taux = 0.0
    cache[devise] = taux
    return taux


def _executer_sortie(gw, base_url, account_id, sortie: dict, chemins: dict) -> dict:
    """Vend une position dont une condition de sortie est remplie."""
    position = sortie["position"]
    execution = _place_order(
        gw, base_url, account_id, ticker=position["ticker"],
        conid=position["conid"], side="SELL", quantity=int(position["quantite"]),
        prix_reference_cotation=sizing.to_quotation_price(
            sortie["current_price"], position["ticker"]),
        state_path=chemins["state"])
    return journal.build_order_record(
        ticker=position["ticker"], conid=position["conid"], sens="SELL",
        quantite=int(position["quantite"]),
        devise_compte=position.get("devise", ""),
        devise_cotation=sizing.quotation_currency(
            position.get("devise", ""), position["ticker"]),
        prix_reference_sizing=sortie["current_price"],
        execution=execution, close_reason=sortie["close_reason"])


def _executer_entree(gw, base_url, account_id, retenu: dict, contrat: dict,
                     chemins: dict) -> dict:
    """Achete un signal retenu par portfolio.select_entries."""
    signal, plan = retenu["signal"], retenu["plan"]
    execution = _place_order(
        gw, base_url, account_id, ticker=signal["ticker"], conid=contrat["conid"],
        side="BUY", quantity=int(plan["quantite"]),
        prix_reference_cotation=plan["prix_unitaire_cotation"],
        state_path=chemins["state"])
    return journal.build_order_record(
        ticker=signal["ticker"], conid=contrat["conid"], sens="BUY",
        quantite=int(plan["quantite"]),
        devise_compte=plan["devise_compte"], devise_cotation=plan["devise_cotation"],
        taux_de_change=plan["taux_de_change"], budget_converti=plan["budget_converti"],
        prix_reference_sizing=signal["current_price"],
        prix_paper=signal.get("paper_entry_price"),
        execution=execution, rang=retenu["rang"])
```

Corps de `run_batch` à insérer à la place du commentaire « Les tâches 4 et 5 insèrent ici » :

```python
    # --- 5. Reconciliation AVANT toute decision (spec 5.4) ------------
    locales = portfolio.load_positions(chemins["positions"])
    try:
        brutes = gw.positions(base_url, account_id)
    except Exception as e:
        # Agir sans savoir ce que le compte detient, c'est risquer de
        # racheter une ligne deja detenue (spec 5.4 / 5.5).
        run["erreurs"].append({"etape": "positions_ibkr", "detail": str(e)})
        run["statut"] = "reconciliation_impossible"
        return _terminer(run, chemins)

    reconciliation = portfolio.reconcile(locales, brutes)
    run["reconciliation"] = {
        "actives": len(reconciliation["actives"]),
        "cloturees_hors_bot": [p.get("id") for p in reconciliation["cloturees_hors_bot"]],
        "anomalies_quantite": reconciliation["anomalies_quantite"],
        "ignorees": len(reconciliation["ignorees"]),
    }

    # En dry_run, les positions simulees n'existent evidemment pas chez
    # IBKR : laisser la reconciliation les fermer viderait positions.json
    # chaque jour et rendrait la validation en simulation (spec 5.3) sans
    # objet — aucune sortie ne serait jamais observee. On journalise donc
    # ce que la reconciliation DIRAIT, mais on garde l'etat local. En mode
    # reel, la reconciliation fait foi, sans exception.
    if etat["dry_run"]:
        positions_ouvertes = list(locales)
    else:
        positions_ouvertes = list(reconciliation["actives"])

    # Alerte operateur : portfolio.exit_reason laisse volontairement
    # inclosable une position sans prix de reference (ecart #4 documente
    # dans portfolio.py), et dit explicitement que c'est au Plan B de la
    # signaler. Sans ca, elle occuperait une place pour toujours, en
    # silence.
    for position in positions_ouvertes:
        if portfolio._is_missing(position.get("prix_execution_reference")):
            run["anomalies"].append({
                "type": "prix_reference_absent",
                "ticker": position.get("ticker"),
                "detail": ("position inclosable par toute regle de sortie tant "
                           "que son prix d'execution de reference n'est pas "
                           "restaure dans positions.json"),
            })

    def _sauver_positions():
        if not journal.save_positions_safely(positions_ouvertes, chemins["positions"]):
            run["erreurs"].append({
                "etape": "positions.json",
                "detail": "ecriture impossible — etat local potentiellement perime",
            })

    _sauver_positions()

    # Un ticker qui quitte le portefeuille aujourd'hui n'est JAMAIS
    # rachete dans le meme batch. Pour les clotures hors bot, la spec 5.4
    # est explicite : "Le bot ne la rouvre jamais". La sortie du bot
    # lui-meme est remplie plus bas, au fil des ventes.
    # En dry_run, cloturees_hors_bot contient TOUTES les positions
    # simulees (IBKR n'en connait aucune) : l'appliquer bloquerait tous
    # les tickers du portefeuille simule pour rien.
    tickers_indisponibles = {
        p.get("ticker"): "cloturee_hors_bot"
        for p in reconciliation["cloturees_hors_bot"]
    } if not etat["dry_run"] else {}

    # --- 6. Sorties (spec 3.6), AVANT les entrees ---------------------
    # Meme enchainement que le paper-trading (_close_eligible_positions
    # puis _open_new_signal_positions) : les places liberees aujourd'hui
    # sont disponibles pour les signaux du jour.
    companies = {c["ticker"]: c for c in indices.get("companies", [])
                 if isinstance(c, dict) and c.get("ticker")}
    for sortie in portfolio.positions_to_close(positions_ouvertes, companies, today):
        record = _executer_sortie(gw, base_url, account_id, sortie, chemins)
        run["sorties"].append(record)
        if record["statut"] in ("execute", "simule"):
            identifiant = sortie["position"].get("id")
            positions_ouvertes = [p for p in positions_ouvertes
                                  if p.get("id") != identifiant]
            # Racheter dans la minute un titre qu'on vient de stop-losser
            # serait absurde, et le filtre `deja_en_portefeuille` de
            # select_entries ne peut plus le voir : il vient d'etre retire
            # de positions_ouvertes juste au-dessus.
            tickers_indisponibles[sortie["position"]["ticker"]] = "vendu_aujourd_hui"
            # Reecrit apres CHAQUE ordre, pas en fin de batch : un plantage
            # entre les deux laisserait positions.json en desaccord avec la
            # realite du compte.
            _sauver_positions()

    # --- 7. Entrees ---------------------------------------------------
    paper_positions = signals.load_signal_tracking(chemins["tracking"])
    signaux, rejets = signals.collect_new_signals(indices, paper_positions, today)
    for rejet in rejets:
        run["signaux_rejetes"].append({"ticker": rejet.get("ticker"),
                                       "raison": rejet.get("raison"),
                                       "rang": None, "score": None})

    try:
        base_cash = gw.base_currency_cash(base_url, account_id)
    except Exception as e:
        # Solde inconnu -> 0.0 : le garde-fou de select_entries rejettera
        # tout, ce qui est le sens sur (spec 9.9).
        run["erreurs"].append({"etape": "base_currency_cash", "detail": str(e)})
        base_cash = 0.0
    journal.save_account_snapshot({"base_cash": base_cash},
                                  chemins["account_snapshot"])

    cache_conid = contracts.load_cache(chemins["conid_cache"])
    taux_par_devise: dict[str, float] = {}
    plans: dict[str, dict] = {}
    contrats: dict[str, dict] = {}
    for signal in signaux:
        ticker = signal["ticker"]
        taux = _taux_de_change(gw, base_url, signal["currency"], taux_par_devise, run)
        plans[ticker] = sizing.compute_quantity(
            ticker, signal["currency"], signal["current_price"], taux)
        contrats[ticker] = contracts.resolve_conid(
            ticker,
            lambda symbole: gw.search_contract(base_url, symbole),
            lambda conid: gw.contract_info(base_url, conid),
            cache_conid, today)
    try:
        contracts.save_cache(cache_conid, chemins["conid_cache"])
    except Exception as e:
        print(f"Erreur ecriture du cache de conid : {e}")

    # Garde d'idempotence supplementaire : un conid deja detenu sur le
    # compte mais absent de positions.json (batch plante entre l'ordre et
    # la sauvegarde) serait classe `ignorees` par la reconciliation —
    # c'est-a-dire "position de l'utilisateur" — et rien n'empecherait un
    # rachat. On refuse d'en acheter davantage ; on n'y touche pas pour
    # autant (spec 9.5).
    conids_detenus = set()
    for brute in reconciliation["ignorees"]:
        try:
            conids_detenus.add(int(brute.get("conid")))
        except (TypeError, ValueError):
            continue
    signaux_financables = []
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
        signaux_financables.append(signal)

    # NOTE : les signaux ecartes ci-dessus ne sont pas passes a
    # select_entries, donc les `rang` renvoyes se comptent sur les seuls
    # signaux finançables. C'est voulu : un signal inachetable ne doit ni
    # consommer une place sous le plafond, ni du budget (meme raisonnement
    # que l'ordre des filtres de select_entries, spec 3.3). Les rejets
    # ecartes ici portent donc `rang: None`.
    retenus, rejets_selection = portfolio.select_entries(
        signaux_financables, positions_ouvertes, plans, contrats, base_cash)
    for rejet in rejets_selection:
        run["signaux_rejetes"].append({"ticker": rejet["ticker"],
                                       "raison": rejet["raison"],
                                       "rang": rejet.get("rang"),
                                       "score": rejet.get("score")})

    for retenu in retenus:
        signal = retenu["signal"]
        contrat = contrats[signal["ticker"]]
        record = _executer_entree(gw, base_url, account_id, retenu, contrat, chemins)
        run["entrees"].append(record)
        if record["statut"] not in ("execute", "simule"):
            continue   # spec 5.5 : pas de reprise, on passe au suivant
        positions_ouvertes.append(journal.build_position_record(
            signal, retenu["plan"], contrat, int(retenu["plan"]["quantite"]),
            record["prix_execution"] if record["prix_execution"] is not None
            else signal["current_price"],
            today))
        _sauver_positions()

    return _terminer(run, chemins)
```

Note d'implémentation : `portfolio._is_missing` est utilisé ci-dessus pour la détection d'anomalie. C'est une fonction privée du module ; si le relecteur préfère éviter l'accès à un privé, dupliquer le garde de 3 lignes dans `daily.py` plutôt que de modifier `portfolio.py` (ce plan ne restructure pas Plan A).

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_daily.py -v`
Expected: PASS (tous)

Run: `python -m pytest`
Expected: PASS

- [ ] **Step 5 : Vérifier à la main les trois chiffres critiques**

Avant de commit, relire les assertions chiffrées et confirmer :
- `floor(500 EUR / 90 EUR) = 5` actions pour MC.PA ;
- `floor(500 × 1.08 / 400 USD) = 1` action pour ADBE ;
- `floor((500 × 0.86 × 100 GBp) / (2.95 × 100 GBp)) = floor(43000 / 295) = 145` actions pour III.L, et `prix_execution` stocké à **2.96 GBP**, pas 296.

Run: `python -m pytest tests/ibkr_bot/test_daily.py -k "london or dry_run_places" -v`
Expected: PASS

- [ ] **Step 6 : Commit**

```bash
git add ibkr_bot/daily.py tests/ibkr_bot/test_daily.py
git commit -m "feat(ibkr_bot): orchestration complete du batch quotidien (Plan B, tache 5)"
```

---

### Task 6 : déploiement VPS — services systemd, timer, documentation

**Files:**
- Create: `deploy/ibkr-gateway.service`
- Create: `deploy/ibkr-bot-daily.service`
- Create: `deploy/ibkr-bot-daily.timer`
- Create: `deploy/deploy-ibkr.sh`
- Create: `deploy/README-ibkr.md`
- Modify: `deploy/README.md` (une ligne de renvoi en tête)
- Modify: `requirements-bot.txt` (ajout de `python-dateutil`)
- Test: `tests/ibkr_bot/test_deploy_files.py`

**Interfaces:**
- Consumes: `python -m ibkr_bot.daily` (tâche 5), `ibkr_bot.state.load_state/save_state` (Plan A).
- Produces: les fichiers de déploiement. Aucun symbole Python.

**Deux vrais défauts à corriger dans cette tâche (trouvés en lisant le déploiement existant, pas inventés) :**

1. **`requirements-bot.txt` est incomplet pour ce bot.** Le venv du VPS s'installe depuis `requirements-bot.txt` (`requests`, `fastapi`, `uvicorn[standard]`, `httpx`), mais `ibkr_bot/portfolio.py` importe `dateutil.relativedelta`, qui n'est déclaré que dans `requirements.txt`. En l'état, `python -m ibkr_bot.daily` s'arrêterait sur un `ModuleNotFoundError` au premier import sur le VPS.
2. **`Type=oneshot` + le préflight = un piège systemd.** Le batch peut dormir jusqu'à 20 minutes (3 tentatives / 10 min). Le défaut systemd `DefaultTimeoutStartSec=90s` tuerait le service en pleine attente, et le journal ne porterait jamais l'abandon propre ni l'email d'alerte. `TimeoutStartSec=2400` est donc **obligatoire**, pas cosmétique.

Deux autres décisions à ne pas « simplifier » :
- **Pas de `Restart=` sur `ibkr-bot-daily.service`.** Relancer automatiquement un batch qui a échoué en plein milieu rejouerait les entrées déjà passées. L'idempotence protège (réconciliation + garde `deja_detenu_hors_journal`), mais elle n'est pas une invitation à relancer en boucle.
- **`Persistent=false` sur le timer.** Un batch manqué (VPS éteint) ne doit **pas** se déclencher en retard : il trouverait les marchés fermés, hors de la fenêtre 14:30–15:30 UTC qui est la seule raison d'être de l'horaire de 14:45 UTC (spec 4.5).

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `tests/ibkr_bot/test_deploy_files.py` :

```python
# Tests des fichiers de deploiement : ils ne lancent rien, ils verifient
# que les unites systemd portent les proprietes dont depend la securite du
# bot (utilisateur dedie, timeout compatible avec le preflight, pas de
# relance automatique, timer non persistant).
import os

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _lire(nom):
    with open(os.path.join(RACINE, "deploy", nom), encoding="utf-8") as fh:
        return fh.read()


def test_the_gateway_service_runs_as_the_dedicated_user():
    contenu = _lire("ibkr-gateway.service")
    assert "User=ibkrbot" in contenu
    assert "User=goldbot" not in contenu
    assert "Restart=on-failure" in contenu


def test_the_daily_service_is_a_oneshot_that_can_outlast_the_preflight():
    """Le preflight peut dormir 20 minutes (3 tentatives / 10 min). Le
    defaut systemd (DefaultTimeoutStartSec=90s) tuerait le batch en pleine
    attente, sans abandon propre ni email d'alerte."""
    contenu = _lire("ibkr-bot-daily.service")
    assert "Type=oneshot" in contenu
    assert "User=ibkrbot" in contenu
    assert "ExecStart=" in contenu and "-m ibkr_bot.daily" in contenu
    assert "EnvironmentFile=/home/ibkrbot/analyse-or/.env" in contenu

    timeout = [l for l in contenu.splitlines() if l.startswith("TimeoutStartSec=")]
    assert timeout, "TimeoutStartSec est obligatoire avec Type=oneshot"
    assert int(timeout[0].split("=")[1]) >= 1800


def test_the_daily_service_never_restarts_itself():
    """Relancer un batch a mi-parcours rejouerait des entrees deja
    passees. L'idempotence protege, elle n'invite pas a la boucle."""
    contenu = _lire("ibkr-bot-daily.service")
    assert not any(l.startswith("Restart=") and l != "Restart=no"
                   for l in contenu.splitlines())


def test_the_timer_fires_at_1445_utc_and_never_catches_up():
    contenu = _lire("ibkr-bot-daily.timer")
    assert "OnCalendar=*-*-* 14:45:00 UTC" in contenu
    assert "Persistent=false" in contenu
    assert "Unit=ibkr-bot-daily.service" in contenu
    assert "WantedBy=timers.target" in contenu


def test_requirements_bot_declares_dateutil():
    """ibkr_bot.portfolio importe dateutil.relativedelta ; le venv du VPS
    s'installe depuis requirements-bot.txt, pas requirements.txt."""
    with open(os.path.join(RACINE, "requirements-bot.txt"), encoding="utf-8") as fh:
        assert "python-dateutil" in fh.read()


def test_the_ibkr_readme_documents_the_operational_essentials():
    contenu = _lire("README-ibkr.md")
    for attendu in ("ibkrbot", "IBKR_GATEWAY_URL", "IBKR_ACCOUNT_ID",
                    "chmod 600", "ibkr-bot-daily.timer", "kill_switch",
                    "dry_run", "ibkr_bot.state", "2FA", "127.0.0.1"):
        assert attendu in contenu, f"{attendu!r} absent de deploy/README-ibkr.md"


def test_the_ibkr_readme_never_asks_for_the_ibkr_password_in_the_env_file():
    """Spec 5.1 : le login et le mot de passe IBKR ne sont JAMAIS stockes
    sur le VPS, sous aucune forme — ils sont saisis a la main dans
    l'interface d'authentification locale du Gateway."""
    contenu = _lire("README-ibkr.md")
    assert "IBKR_PASSWORD" not in contenu
    assert "IBKR_LOGIN" not in contenu


def test_the_gold_readme_points_to_the_ibkr_one():
    assert "README-ibkr.md" in _lire("README.md")
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_deploy_files.py -v`
Expected: FAIL — `FileNotFoundError: deploy/ibkr-gateway.service`

- [ ] **Step 3 : Créer les unités systemd**

`deploy/ibkr-gateway.service` :

```
[Unit]
Description=Gateway IBKR Client Portal (ecoute 127.0.0.1 uniquement)
After=network.target

[Service]
Type=simple
User=ibkrbot
WorkingDirectory=/home/ibkrbot/clientportal.gw
ExecStart=/home/ibkrbot/clientportal.gw/bin/run.sh root/conf.yaml
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`deploy/ibkr-bot-daily.service` :

```
[Unit]
Description=Bot Actions IBKR - batch quotidien
After=network.target ibkr-gateway.service
Wants=ibkr-gateway.service

[Service]
Type=oneshot
User=ibkrbot
WorkingDirectory=/home/ibkrbot/analyse-or
EnvironmentFile=/home/ibkrbot/analyse-or/.env
ExecStart=/home/ibkrbot/analyse-or/venv/bin/python3 -m ibkr_bot.daily
# Le preflight peut attendre jusqu'a 20 minutes (3 tentatives espacees de
# 10 min, spec 5.5). Le defaut systemd (90 s) tuerait le batch en pleine
# attente, sans abandon propre ni email d'alerte.
TimeoutStartSec=2400
# Volontairement PAS de Restart= : relancer un batch a mi-parcours
# rejouerait des entrees deja passees. Le declenchement est le timer, et
# lui seul.

[Install]
WantedBy=multi-user.target
```

`deploy/ibkr-bot-daily.timer` :

```
[Unit]
Description=Bot Actions IBKR - declenchement quotidien a 14:45 UTC

[Timer]
OnCalendar=*-*-* 14:45:00 UTC
# Persistent=false volontairement : un batch manque (VPS eteint) ne doit
# PAS se declencher en retard — il trouverait les marches fermes, hors de
# la fenetre 14:30-15:30 UTC qui est la seule raison d'etre de cet horaire
# (spec 4.5). Mieux vaut sauter un jour que trader a l'aveugle.
Persistent=false
Unit=ibkr-bot-daily.service

[Install]
WantedBy=timers.target
```

`deploy/deploy-ibkr.sh` (miroir de `deploy/deploy.sh`) :

```bash
#!/bin/bash
# Mise a jour du bot Actions IBKR sur le VPS.
# A executer en tant qu'utilisateur ibkrbot, depuis /home/ibkrbot/analyse-or.
set -e

cd /home/ibkrbot/analyse-or
git pull
source venv/bin/activate
pip install -r requirements-bot.txt
# Le batch quotidien n'est pas un service persistant : rien a redemarrer,
# le prochain declenchement du timer prendra le nouveau code. Seul le
# Gateway tourne en continu.
sudo systemctl restart ibkr-gateway
echo "Deploiement Bot Actions IBKR termine. Prochain batch :"
systemctl list-timers ibkr-bot-daily.timer --no-pager
```

Modifier `requirements-bot.txt` pour ajouter la ligne manquante :

```
requests
fastapi
uvicorn[standard]
httpx
python-dateutil
```

- [ ] **Step 4 : Écrire la documentation de déploiement**

Créer `deploy/README-ibkr.md` :

````markdown
# Déploiement du Bot Actions IBKR sur le VPS

Le bot actions (`ibkr_bot/`) tourne sur **le même VPS Hetzner que le Bot
Or**, mais sous un **utilisateur système séparé** (`ibkrbot`) et avec son
propre clone du dépôt. Raison (spec 5.1) : un bug ou une compromission du
bot Or ne doit pas donner accès aux identifiants d'un compte-titres réel,
et réciproquement.

Pour le Bot Or, voir `deploy/README.md`. Les deux guides sont
indépendants ; seule l'étape 1 (sécurisation de base du VPS) est commune
et déjà faite.

## 1. Utilisateur dédié + dépôt

```bash
sudo adduser --disabled-password ibkrbot

# Sudoers restreint a exactement la commande dont deploy-ibkr.sh a besoin :
echo 'ibkrbot ALL=(root) NOPASSWD: /bin/systemctl restart ibkr-gateway' | sudo tee /etc/sudoers.d/ibkrbot-restart
sudo chmod 440 /etc/sudoers.d/ibkrbot-restart

sudo -u ibkrbot -i
git clone https://github.com/Alexandreauq/analyse-or.git
cd analyse-or
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-bot.txt
```

## 2. Installer le Gateway IBKR Client Portal

Le Gateway est un paquet **Java** fourni par IBKR (spec 4.4 / 9.3), pas
une dépendance Python.

```bash
sudo apt install -y openjdk-17-jre-headless unzip

sudo -u ibkrbot -i
mkdir -p ~/clientportal.gw && cd ~/clientportal.gw
# Télécharger l'archive officielle du Client Portal Gateway depuis le
# site IBKR (page "Client Portal Web API"), puis :
unzip clientportal.gw.zip
```

**Éditer `~/clientportal.gw/root/conf.yaml` pour n'écouter que sur la
boucle locale** : le Gateway ne doit **jamais** être joignable depuis
Internet, et **aucun port ne doit être ouvert dans ufw** (spec 4.4).
Vérifier après démarrage :

```bash
sudo ss -lntp | grep 5000    # doit montrer 127.0.0.1:5000, jamais 0.0.0.0:5000
```

## 3. Fichier `.env` (jamais commité)

Créer `/home/ibkrbot/analyse-or/.env` :

```
# Gateway local — jamais expose sur Internet
IBKR_GATEWAY_URL=https://127.0.0.1:5000
# Compte IBKR sur lequel trader (ex. U1234567)
IBKR_ACCOUNT_ID=...
SMTP_USER=...
SMTP_PASSWORD=...
# Optionnel, sinon = SMTP_USER
MAIL_TO=...
```

Permissions restreintes : `chmod 600 .env`.

**Le login et le mot de passe IBKR ne figurent PAS dans ce fichier, et ne
doivent jamais y figurer** (spec 5.1). Ils sont saisis à la main dans
l'interface d'authentification locale du Gateway, une fois par session.
C'est contraignant, mais c'est la meilleure propriété de sécurité du
dispositif : les identifiants maîtres du compte-titres ne sont stockés
sur le VPS sous aucune forme.

**Il n'y a pas non plus de jeton d'API** (`IBKR_BOT_API_TOKEN` n'existe
pas) : l'interrupteur d'urgence est un fichier modifié en SSH, pas une
route HTTPS (spec 5.2 / 9.4, décidé après l'incident `BOT_API_TOKEN` du
2026-09-14 sur le bot Or — un secret de moins à perdre).

## 4. Installer les services systemd

*(En tant qu'administrateur/root, pas dans le shell `ibkrbot`.)*

```bash
sudo cp deploy/ibkr-gateway.service /etc/systemd/system/
sudo cp deploy/ibkr-bot-daily.service /etc/systemd/system/
sudo cp deploy/ibkr-bot-daily.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ibkr-gateway
sudo systemctl enable --now ibkr-bot-daily.timer
```

Le **timer** est activé, pas le service : c'est lui qui déclenche le batch
à 14:45 UTC. Vérifier :

```bash
systemctl list-timers ibkr-bot-daily.timer --no-pager
```

## 5. Authentifier le Gateway (action manuelle récurrente)

**C'est la principale servitude opérationnelle de ce bot, et elle est
structurelle — pas un bug, pas un manque du code** (spec 4.4 / 8). La
session du Gateway expire (historiquement ~24 h) et sa réactivation peut
exiger une validation 2FA sur le téléphone. Il n'existe aucun moyen propre
de l'automatiser.

Depuis un poste de travail, ouvrir un tunnel SSH vers le Gateway (qui
n'écoute que sur la boucle locale du VPS) :

```bash
ssh -L 5000:127.0.0.1:5000 utilisateur@178.105.186.220
```

Puis, dans un navigateur local, ouvrir `https://127.0.0.1:5000` (accepter
le certificat auto-signé), saisir le login et le mot de passe IBKR, et
valider la 2FA. Vérifier :

```bash
curl -sk -X POST https://127.0.0.1:5000/v1/api/iserver/auth/status
# doit renvoyer "authenticated": true et "connected": true
```

**À faire avant 14:45 UTC chaque jour de bourse.** Si la session n'est pas
valide au moment du batch, le bot fait 3 tentatives espacées de 10
minutes, abandonne proprement (aucun ordre, ni entrée ni sortie) et envoie
**un email d'alerte immédiat** dont l'objet commence par « ALERTE ». Les
signaux d'entrée du jour sont perdus ; les sorties sont décalées au
lendemain (spec 5.5 / 9.8).

## 6. Vérifier

```bash
sudo systemctl status ibkr-gateway
systemctl list-timers ibkr-bot-daily.timer --no-pager

# Lancer un batch a la main, sans attendre le timer (reste en dry_run) :
sudo systemctl start ibkr-bot-daily
journalctl -u ibkr-bot-daily -n 100 --no-pager
```

Le bot tourne en **mode simulation** (`dry_run: true` par défaut, spec
5.3) : il évalue tout, journalise exactement ce qu'il aurait fait, et
**n'appelle jamais la route de passage d'ordre**. Observer
`ibkr_bot/real_trading_log.jsonl` et les résumés quotidiens par email.

## 7. Interrupteur d'urgence (`kill_switch`)

`ibkr_bot/state.json` est le **seul** fichier qui contrôle l'interrupteur
d'urgence et le mode simulation. Volontairement pas exposé par une API :
le seul chemin est une commande manuelle en SSH.

```bash
sudo -u ibkrbot -i
cd analyse-or
source venv/bin/activate
python3 -c "import ibkr_bot.state as state; s = state.load_state(state.STATE_PATH); s['kill_switch'] = True; state.save_state(s, state.STATE_PATH)"
```

Aucun redémarrage de service n'est nécessaire : le batch relit ce fichier
à chaque exécution, **et une seconde fois juste avant chaque envoi
d'ordre**. Pour lever l'interrupteur, remettre `False` avec la même
commande.

La cadence quotidienne laisse toujours plusieurs heures pour couper avant
le prochain batch — c'est exactement l'argument qui a fait écarter une API
HTTPS dédiée (spec 9.4).

## 8. Passage en mode réel (à ne faire qu'après validation de la simulation)

Avant tout passage en réel, deux prérequis manuels :

1. **Plusieurs semaines en `dry_run`** avec comparaison quotidienne des
   décisions simulées aux positions du paper-trading. Tout écart doit
   s'expliquer par une cause connue (plafond de 10, 0 action achetable,
   horaire d'exécution) — spec 7.
2. **Vérifier les coûts d'abonnement aux données de marché** sur le
   compte IBKR (ou auprès du support), place par place, ainsi que le seuil
   exact de dispense (spec 6 / 9.2). Ce point n'est délibérément chiffré
   nulle part dans la spec.

```bash
sudo -u ibkrbot -i
cd analyse-or
source venv/bin/activate
python3 -c "import ibkr_bot.state as state; s = state.load_state(state.STATE_PATH); s['dry_run'] = False; state.save_state(s, state.STATE_PATH)"
```

Pour revenir en simulation, remettre `dry_run` à `True` avec la même
commande.

## Mises à jour

```bash
sudo -u ibkrbot -i
cd analyse-or
./deploy/deploy-ibkr.sh
```

Le batch fait lui-même un `git pull` au début de chaque exécution (spec
4.5), donc les données `docs/indices.json` sont toujours fraîches ; mais
un changement de **code** ne prend effet qu'au batch suivant, ce qui est
voulu — un batch ne doit pas changer de version en plein vol.

## Fichiers générés (gitignorés)

| Fichier | Rôle |
|---|---|
| `ibkr_bot/state.json` | `kill_switch`, `dry_run` |
| `ibkr_bot/positions.json` | positions ouvertes **par le bot** |
| `ibkr_bot/real_trading_log.jsonl` | journal append-only, une ligne par batch |
| `ibkr_bot/conid_cache.json` | correspondances ticker → conid IBKR |
| `ibkr_bot/latest_account.json` | dernier instantané des soldes |
````

Ajouter en tête de `deploy/README.md`, juste après le titre `# Déploiement du Bot Or sur un VPS` :

```markdown
> Ce guide concerne le **Bot Or** (`gold_bot/`, MetaApi/MT5, boucle
> continue). Pour le **Bot Actions IBKR** (`ibkr_bot/`, batch quotidien,
> utilisateur `ibkrbot`), voir `deploy/README-ibkr.md`.
```

- [ ] **Step 5 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_deploy_files.py -v`
Expected: PASS (tous)

Run: `python -m pytest`
Expected: PASS — la suite complète (723 tests au départ, plus ceux des tâches 1 à 6).

- [ ] **Step 6 : Commit**

```bash
git add deploy/ibkr-gateway.service deploy/ibkr-bot-daily.service deploy/ibkr-bot-daily.timer deploy/deploy-ibkr.sh deploy/README-ibkr.md deploy/README.md requirements-bot.txt tests/ibkr_bot/test_deploy_files.py
git commit -m "feat(deploy): services systemd, timer 14:45 UTC et guide VPS du bot actions (Plan B, tache 6)"
```

---

## Après la dernière tâche

1. Lancer la suite complète une dernière fois : `python -m pytest`. Aucune régression admise sur les 723 tests existants.
2. Revue de branche complète (`superpowers:requesting-code-review`) **avec une attention particulière sur `ibkr_bot/daily.py`** : c'est le seul module du dépôt d'où un ordre actions réel peut partir. La revue finale de Plan A avait trouvé un défaut de conception que les revues de tâches isolées ne pouvaient pas voir ; la même vigilance s'impose ici, où les 6 modules découplés de Plan A sont recousus pour la première fois.
3. Mettre à jour le `## Statut` de `docs/superpowers/specs/2026-09-14-ibkr-equities-automation-design.md` : Plan B implémenté, sections 5.5 / 7 / 8 validées par l'implémentation, reste la vérification manuelle des coûts de données de marché (9.2) avant tout passage en réel.
4. **Ne pas** basculer `dry_run` à `False`. C'est une étape manuelle, en SSH, après plusieurs semaines de simulation — hors périmètre de ce plan.
