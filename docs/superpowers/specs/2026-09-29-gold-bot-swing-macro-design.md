# Gold Bot — Mode Swing Macro (design)

## Contexte

Une nuit de validation empirique rigoureuse (figures chartistes, cassure de
session + volume, climax de volume, alignement de tendance SMA,
autocorrélation, effets calendaires, lead-lag EUR/USD, continuation
post-FOMC) n'a fait ressortir **aucun edge de scalping** qui survive à une
validation hors-échantillon sur deux périodes distinctes (2018-2021 vs
2021-2026, XAU/USD 5min via MetaApi).

Le repo dispose déjà, séparément, d'un module `gold_score.py` — un score
macro-fondamental de l'or tournant en production (GitHub Actions, horaire),
combinant taux réels US (FRED, série DFII10), DXY (Yahoo Finance),
anticipations d'inflation (FRED), ton de la Fed/flux ETF/achats banques
centrales (saisies manuelles), et positionnement spéculatif CFTC (API
publique, percentile 3 ans). Il publie sa logique d'alerte "conditions
d'entrée réunies" (`kind: "entree"`) quand : composite > 15, prix proche de
la MM200 (support), positionnement CFTC non extrême, et hors blackout Fed
avant un FOMC. C'est un signal **haussier uniquement** (aucune alerte
symétrique de vente n'existe dans la méthodologie) — cohérent avec la
littérature documentée sur l'or (taux réels, DXY, COT, flux ETF), qui opère
structurellement à l'échelle de la semaine/mois, pas de la minute/heure.

Décision validée avec l'utilisateur : `gold_bot` passe en mode **swing macro
seul pour l'instant** (pas de coexistence avec un moteur scalping — celui-ci
n'a de toute façon montré aucun edge validé). Le bot ouvre une position
**longue uniquement**, tenue plusieurs jours, déclenchée par le signal
`gold_score.py`, avec sa propre gestion de sortie.

## Ce que fait ce plan

1. `gold_score.py` expose `cftc_percentile` en champ de premier niveau dans
   son payload JSON publié (`docs/score.json`), pour que `gold_bot` puisse
   le lire sans reparser la chaîne française `raw_value` du facteur CFTC.
2. Nouveau module `gold_bot/macro_signal.py` : va chercher le payload publié
   de `gold_score.py` et en dérive un signal d'entrée/sortie exploitable par
   `gold_bot`.
3. Nouvelle fonction `gold_bot/bot.py::decide_and_act_swing()` : la
   contrepartie swing de `decide_and_act()` existante (scalping) — même
   contrat de sortie (`{"action": "simulation", "steps": [...]}` avec les
   mêmes types d'étapes `ouverture_simulee`/`clôture_simulee`, pour réutiliser
   `loop.execute_steps` sans modification), mais une logique de décision
   différente (pas de retournement long/court, sortie basée sur la
   dégradation du signal macro plutôt que sur un signal opposé).
4. `gold_bot/loop.py::run_cycle` appelle `decide_and_act_swing` à la place de
   `decide_and_act`. L'ancien moteur scalping (`confluence.py`,
   `chart_patterns.py`, `decide_and_act`) reste intact dans le repo, non
   supprimé, simplement plus appelé par la boucle de production — même
   convention que `session_breakout.py`, ajouté sans retirer
   `chart_patterns.py`.

## Non-objectifs

- Pas de logique de vente à découvert (short) — hors du champ de la
  méthodologie `gold_score.py` actuelle, qui n'a pas d'alerte symétrique.
- Pas de suppression du moteur scalping existant (`confluence.py`,
  `chart_patterns.py`, `decide_and_act`) — conservé, simplement débranché de
  la boucle de production.
- Pas de nouveau profil de risque : les 5 profils existants
  (`risk.RISK_PROFILE_PARAMS`) et le coupe-circuit journalier
  (`risk.CircuitBreaker`) sont réutilisés tels quels — `risk_pct` est déjà
  indépendant de l'échelle de temps de la position (c'est un pourcentage du
  solde risqué jusqu'au stop, pas une durée).
- Pas de changement à `risk.py`, `state.py`, `api.py`, `notify.py`.
- Pas de déploiement/activation réelle sur le VPS — ce plan livre le code,
  testé, mergé ; la bascule en production (arrêt du service actuel,
  redéploiement) est une étape manuelle ultérieure, hors de ce plan.

## Détails techniques

### 1. `gold_score.py` — champ `cftc_percentile` dans le payload

Dans `main()`, le dict `payload` (ligne ~817) gagne un champ
`"cftc_percentile": cftc_percentile` (la variable existe déjà, renvoyée par
`factor_positionnement_cftc()` à la ligne 760 — simple ajout d'une clé, aucun
autre changement).

### 2. Contrat du payload publié (`docs/score.json`, déjà en production)

```json
{
  "date": "2026-09-29",
  "composite_score": -13.1,
  "interpretation": "Neutre — pas d'action",
  "cftc_percentile": 58.0,
  "factors": [...],
  "technical": {"note": "...", "spot": 4185.70, "ma200": 4555.52},
  "calendar": [...],
  "alerts": [
    {"kind": "entree" | "watch" | "risque" | "info", "title": "...", "detail": "...", "date": "29/09/2026"}
  ]
}
```

URL publiée (GitHub Pages, déjà servi) :
`https://alexandreauq.github.io/analyse-or/score.json`

`technical.spot`/`technical.ma200` peuvent être `null` si
`get_gold_spot_and_ma200()` a échoué au moment du run — dans ce cas,
`compute_alerts` ne peut de toute façon jamais produire d'alerte `"entree"`
(la condition `near_support` exige un écart MM200 non nul), donc en pratique
un payload avec une alerte `"entree"` a toujours `technical.ma200` renseigné.
`gold_bot` se protège quand même explicitement de ce cas (voir section 4).

### 3. `gold_bot/macro_signal.py` (nouveau module)

Constantes :

```python
SCORE_JSON_URL = "https://alexandreauq.github.io/analyse-or/score.json"
ENTRY_ALERT_KIND = "entree"
EXIT_COMPOSITE_THRESHOLD = 0.0   # le composite retombe à/sous 0 -> thèse haussière invalidée (l'entrée exigeait > 15)
EXIT_CFTC_PERCENTILE = 90.0      # même seuil que CFTC_EXTREME_PERCENTILE dans gold_score.py -- positionnement trop encombré
SWING_STOP_BUFFER_PCT = 0.03     # stop = MM200 * (1 - 3%)
SWING_TAKE_PROFIT_R_MULTIPLE = 3.0  # take-profit = entrée + 3 * distance(entrée, stop)
SWING_MAX_HOLDING_DAYS = 30       # filet de sécurité : clôture forcée au-delà, indépendamment du signal
```

Fonctions :

- `fetch_macro_payload(url: str = SCORE_JSON_URL, timeout: float = 15.0) -> dict`
  — `requests.get(url, timeout=timeout)`, `resp.raise_for_status()`,
  `return resp.json()`. Lève l'exception telle quelle en cas d'échec réseau
  (même convention que `broker.py` — l'appelant, `loop.py`, gère déjà le
  retry et la capture d'erreur génériques).

- `has_entry_alert(payload: dict) -> bool` — `True` si
  `payload.get("alerts", [])` contient une entrée avec `kind == ENTRY_ALERT_KIND`.

- `compute_entry_levels(payload: dict, current_price: float) -> dict | None`
  — Renvoie `None` si `payload["technical"]["ma200"]` est `None`/absent
  (garde défensive documentée en section 2). Sinon :
  ```python
  ma200 = payload["technical"]["ma200"]
  stop_loss = ma200 * (1 - SWING_STOP_BUFFER_PCT)
  entry = current_price
  distance = entry - stop_loss
  take_profit = entry + SWING_TAKE_PROFIT_R_MULTIPLE * distance
  return {"entry": entry, "stop_loss": stop_loss, "take_profit": take_profit}
  ```
  Si `distance <= 0` (prix déjà sous le stop théorique — ne devrait pas
  arriver si `near_support` a validé l'écart MM200 au moment du calcul du
  score, mais le prix courant a pu bouger depuis le dernier run horaire de
  `gold_score.py`), renvoie `None` — pas d'entrée sur un stop invalide.

- `should_exit(payload: dict, position_open_time_iso: str | None, now: "datetime") -> tuple[bool, str]`
  — Évalue dans l'ordre :
  1. `payload.get("composite_score")` non `None` et `<= EXIT_COMPOSITE_THRESHOLD`
     → `(True, "score composite retombé à {composite_score:+.1f} (seuil {EXIT_COMPOSITE_THRESHOLD})")`
  2. `payload.get("cftc_percentile")` non `None` et
     `>= EXIT_CFTC_PERCENTILE` → `(True, "positionnement CFTC en zone extrême ({cftc_percentile:.0f}e percentile)")`
  3. Si `position_open_time_iso` fourni et parsable (voir ci-dessous) et
     `(now - open_time).days >= SWING_MAX_HOLDING_DAYS` →
     `(True, "durée de détention maximale atteinte ({SWING_MAX_HOLDING_DAYS} jours)")`
  4. Sinon `(False, "")`.

  Parsing de `position_open_time_iso` (champ MetaApi `"time"` d'une
  position — vérifié empiriquement via la documentation officielle
  MetaApi le 2026-09-29 : `MetatraderPosition.time`, format ISO) :
  ```python
  try:
      open_time = datetime.fromisoformat(position_open_time_iso.replace("Z", "+00:00"))
  except (ValueError, AttributeError, TypeError):
      open_time = None
  ```
  Si `open_time` est `None` (champ absent ou format inattendu), l'étape 3
  est simplement ignorée (pas d'exception, pas de sortie forcée par
  précaution injustifiée) — seules les étapes 1 et 2 restent actives.

### 4. `gold_bot/bot.py::decide_and_act_swing`

```python
def decide_and_act_swing(macro_payload: dict, candles: list[dict], *, contract_size: float,
                          balance: float, equity: float, volume_step: float, min_volume: float,
                          max_volume: float, open_positions: list[dict],
                          circuit_breaker: "risk.CircuitBreaker", symbol: str = "XAUUSD",
                          risk_pct: float = 0.05, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    circuit_breaker.check(equity)

    matching = [p for p in open_positions if p.get("symbol") == symbol]

    if len(matching) > 1:
        return {"action": "aucune", "reason": "plusieurs positions ouvertes sur ce symbole, aucune action par prudence"}

    if matching:
        position = matching[0]
        if position.get("type") != "POSITION_TYPE_BUY":
            return {"action": "aucune", "reason": "position de type inattendu (pas un achat), aucune action par prudence"}
        should_exit, reason = macro_signal.should_exit(macro_payload, position.get("time"), now)
        if should_exit:
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

Différences volontaires par rapport à `decide_and_act` (scalping) :

- **Pas de garde `news_blackout`** : `decide_and_act` clôture toute position
  avant une publication macro à fort impact. Une position swing est conçue
  pour *traverser* cette volatilité court terme (la logique d'entrée de
  `gold_score.py` exclut déjà l'ouverture d'une nouvelle position dans les
  heures précédant un FOMC via `fed_ok`, mais rien n'impose de clôturer une
  position swing déjà ouverte juste avant chaque CPI/NFP/FOMC — le ferait
  produirait un va-et-vient coûteux et contraire à l'objectif "swing").
- **Pas de retournement long/court** : signal long uniquement, une seule
  position possible à la fois sur le symbole.
- **Sortie pilotée par la dégradation du signal macro**, pas par un signal
  neutre/opposé.

### 5. `gold_bot/loop.py::run_cycle`

Remplace l'appel à `bot.decide_and_act(...)` par `bot.decide_and_act_swing(...)`.
Réutilise sans modification : le fetch des bougies, la garde
marché-fermé/données-périmées, le fetch solde/positions/spec, le contrôle
anti-slippage avant exécution (`_entry_price_has_drifted`), `execute_steps`.

Ajoute, après le fetch des bougies et avant l'appel de décision, un fetch du
payload macro avec la même politique de retry que les autres appels réseau
du cycle :

```python
macro_payload = _with_retry(lambda: macro_signal.fetch_macro_payload())
```

Si ce fetch échoue après les 3 tentatives, l'exception remonte dans le bloc
`try` existant de `run_cycle`, déjà capturée par le `except Exception as e`
qui journalise `{"action": "erreur", ...}` — aucun traitement spécial
nécessaire, même convention que les échecs solde/positions/spec.

`decide_and_act_swing` est appelée avec `now=now_dt` (déjà disponible dans
`run_cycle`).

Import ajouté en tête de fichier : `import gold_bot.macro_signal as macro_signal`.

## Tests

- `tests/gold_bot/test_macro_signal.py` (nouveau) : `has_entry_alert`,
  `compute_entry_levels` (cas nominal, `ma200` absent, stop invalide),
  `should_exit` (chacune des 3 conditions isolément, aucune condition,
  `position_open_time_iso` absent/invalide).
- `tests/gold_bot/test_bot.py` : cas ajoutés pour `decide_and_act_swing`
  (entrée, maintien, sortie sur composite/CFTC/durée, position de type
  inattendu, plusieurs positions, coupe-circuit déclenché, taille sous le
  minimum, pas de bougies).
- `tests/test_gold_score.py` (existant s'il existe, sinon nouveau minimal) :
  vérifie que `cftc_percentile` apparaît bien dans le payload JSON produit.

## Validation empirique (hors de ce plan)

Comme convenu pour les plans précédents de ce soir (cassure de
session+volume), la validation multi-année de la règle d'entrée/sortie
(composite > 15 + proche MM200 + CFTC ok + hors blackout Fed, sortie sur
dégradation) se fait directement par le contrôleur une fois ce plan terminé
— pas une tâche de ce plan.
