# Bot Or — moteur de signal "cassure de session confirmée par le volume" — Design

## Contexte

Audit du 2026-09-29 (voir `docs/superpowers/specs/2026-09-29-gold-bot-chart-pattern-strategy-design.md` et son plan) : le moteur à figures chartistes/pivots (et sa variante simplifiée en suivi de tendance pur) ont été validés sur 5 ans d'historique réel (2021-2026, XAU/USD 5min) avec dimensionnement de position réel et composé. Résultat sans ambiguïté : aucune des configurations testées n'a de R-multiple moyen positif sur la période complète, et toutes conduisent à une ruine quasi totale du compte (-64% à -99%) à tout niveau de risque testé. Causes racines identifiées :

- La détection de figures chartistes/pivots n'a pas d'hypothèse de marché causale — c'est de la reconnaissance de forme pure, sans raison structurelle d'avoir un edge sur un marché aussi arbitré que l'or au comptant.
- L'edge apparent trouvé sur une fenêtre de 90 jours était un artefact de surapprentissage (25+ variantes testées sur la même fenêtre) — invalidé par un test hors-échantillon et par les 5 ans complets (4 années sur 6 négatives pour le meilleur candidat).
- Aucun coût de transaction (spread) n'était modélisé.

Ce document décrit un nouveau moteur de signal fondé sur une hypothèse de marché différente : la variation connue et structurelle de la liquidité/participation selon les sessions de trading, confirmée par le volume réel (tick volume), plutôt que sur la reconnaissance de figures géométriques.

## Objectifs

- Remplacer la source de données du bot (Twelve Data) par l'API de données de marché MetaApi, qui fournit tick volume, spread réel, et un historique remontant à 2007 — pour le même compte/courtier que celui utilisé pour trader réellement.
- Implémenter un moteur de signal "cassure de range de session, confirmée par le volume" (stratégie de breakout de session, famille bien connue en FX de détail, appliquée ici à l'or).
- Établir un plan de validation qui empêche de répéter l'erreur du 2026-09-29 (calibrage et validation sur des fenêtres temporelles strictement séparées, jamais un seul chiffre agrégé, dimensionnement réel, coûts de transaction inclus).

## Non-objectifs

- Ne touche à aucune LOGIQUE de `gold_bot/risk.py`, `gold_bot/bot.py`, `gold_bot/loop.py`, `gold_bot/api.py`, `gold_bot/state.py`, `gold_bot/notify.py` — le nouveau moteur produit le même format de signal que l'actuel `confluence.compute_signal`, donc aucune décision/garde en aval ne change. Exception mécanique : `fetch_gold_candles` change de signature (voir section 1), donc `loop.py::run_cycle`/`main()` doivent passer token/account_id/region au lieu de `twelve_data_api_key` à ses 2 points d'appel (le fetch principal et la re-vérification anti-slippage) — un changement d'arguments, pas de logique. `run_cycle` reçoit déjà token/account_id/region pour les appels broker, donc son paramètre `twelve_data_api_key` disparaît plutôt que d'en ajouter un nouveau.
- Ne supprime pas `gold_bot/chart_patterns.py` ni l'ancien contenu de `gold_bot/confluence.py` dans ce document — la décision de les retirer du dépôt (et la bascule réelle du bot en production) se prend après le plan de validation ci-dessous, pas avant.
- N'introduit pas de nouvelle dépendance/API tierce — tout passe par le token MetaApi déjà utilisé pour trader.
- `TWELVE_DATA_API_KEY` n'est plus consommé par `gold_bot` après ce changement, mais reste utilisé ailleurs dans le dépôt (ex: `indices_score.py`) — aucun changement hors `gold_bot/`.

## 1. Source de données — API de données de marché MetaApi

MetaApi expose les données de marché historiques sur un **hôte dédié**, différent de l'API de trading déjà utilisée par `gold_bot/broker.py` :

- Hôte trading (inchangé, `broker.py`) : `https://mt-client-api-v1.{region}.agiliumtrade.ai`
- Hôte données de marché (nouveau) : `https://mt-market-data-client-api-v1.{region}.agiliumtrade.ai`

Endpoint utilisé :

```
GET /users/current/accounts/{accountId}/historical-market-data/symbols/{symbol}/timeframes/{timeframe}/candles
Headers: auth-token: <METAAPI_TRADE_TOKEN>
Query: startTime (optionnel, ISO ou "YYYY-MM-DD HH:MM:SS.mmm", charge en arrière depuis ce point), limit (max 1000)
```

Vérifié empiriquement le 2026-09-29 sur le compte réel (région `london`, symbole `XAUUSD`, timeframe `5m`) : réponse `200`, historique disponible au moins jusqu'en 2007, chaque élément de la forme :

```json
{
  "symbol": "XAUUSD", "time": "2026-09-29T15:50:00.000Z", "timeframe": "5m",
  "open": 4159.24, "high": 4159.68, "low": 4153.65, "close": 4155.62,
  "tickVolume": 2534, "spread": 21, "state": "complete"
}
```

`tickVolume` (entier, nombre de mouvements de prix dans la bougie — proxy standard de volume pour un instrument OTC comme l'or au comptant, aucun volume négocié centralisé n'existe pour cet instrument) et `spread` (entier, en points) sont les deux champs nouveaux exploités par ce design. Le champ `state` vaut `"intermediate"` pour la bougie en cours de formation (pas encore close) — **à exclure systématiquement**, même logique que l'actuel `fetch_gold_candles` qui ne traite jamais une bougie non close.

### Modules impactés

- `gold_bot/confluence.py::fetch_gold_candles(api_key)` → devient `fetch_gold_candles(token, account_id, region)` (signature changée : plus de clé Twelve Data, mais token/compte/région MetaApi — déjà disponibles partout où cette fonction est appelée, voir `loop.py::run_cycle`). Pagine avec `startTime` en reculant depuis maintenant jusqu'à obtenir au moins `SCALP_MIN_CANDLES` bougies **complètes** (`state == "complete"`), même contrat de sortie qu'aujourd'hui (liste chronologique, jamais vide en cas de succès).
- `gold_bot/backtest.py::fetch_gold_candles_range(...)` → même bascule, pagine avec `startTime` sur toute la plage `[start, end]` demandée, par blocs de 1000 (au lieu de 5000 pour Twelve Data). Cascade mécanique : `fetch_gold_candles_range(api_key, start, end)` devient `fetch_gold_candles_range(token, account_id, start, end, region=...)`, donc `run_backtest(...)` et `main()` (CLI, actuellement `os.environ["TWELVE_DATA_API_KEY"]`) changent pareillement pour lire `METAAPI_TRADE_TOKEN`/`METAAPI_TRADE_ACCOUNT_ID` — même principe que pour `loop.py` ci-dessus, un changement d'arguments/variables d'environnement lues, pas de logique de simulation.
- Toute bougie gagne deux clés : `"tick_volume"` (int) et `"spread"` (int, en points — utilisé uniquement dans `backtest.py` pour soustraire un coût de transaction réaliste de chaque trade simulé, voir section Validation).
- `confluence.validate_candles` (ajouté le 2026-09-29) reste utilisée telle quelle sur open/high/low/close ; `tick_volume`/`spread` n'ont pas besoin de validation de cohérence OHLC (ce ne sont pas des prix).

## 2. Sessions et logique du signal

Bornes UTC fixes toute l'année, sans ajustement pour l'heure d'été — même philosophie que `confluence.is_market_closed` ("bornes volontairement prudentes... mieux vaut une petite imprécision aux bords que de la complexité inutile").

- **Session asiatique (référence)** : `00:00`–`08:00` UTC. `range_high` / `range_low` = plus haut des `high` / plus bas des `low` de toutes les bougies complètes de cette fenêtre, pour le jour UTC courant.
- **Fenêtre de trading** : `08:00`–`16:00` UTC (session de Londres + chevauchement New York). En dehors de cette fenêtre : signal neutre — même philosophie de refus plutôt que de deviner que `is_market_closed`/`is_news_blackout`.
- **Range invalide** : si moins de `MIN_ASIAN_SESSION_CANDLES` bougies complètes sont disponibles dans la fenêtre asiatique du jour (valeur de départ : 48, la moitié des 96 bougies de 5min attendues sur 8h — tolère un trou de données partiel sans accepter un range calculé sur une poignée de bougies non représentative) → neutre, même en pleine fenêtre de trading.

### Condition de cassure "fraîche"

Pour éviter de re-déclencher le même signal à chaque bougie tant que le prix reste au-delà du range (le range est recalculé sur une fenêtre glissante, donc rester au-dessus de `range_high` satisferait la condition indéfiniment sans ce garde), le signal n'existe qu'à la bougie de franchissement :

- **Achat** : `close[-1] > range_high` ET `close[-2] <= range_high`.
- **Vente** : `close[-1] < range_low` ET `close[-2] >= range_low`.

Ce garde est purement local (2 dernières bougies), donc `compute_signal` reste une fonction pure de `candles`, sans état externe à faire persister entre les cycles — même contrat que l'actuel `compute_signal`. Une deuxième cassure plus tard dans la journée (après un retour dans le range) reste possible et légitime : ce n'est pas un rejeu du même signal, c'est une nouvelle tentative de cassure.

### Confirmation par le volume

La bougie de cassure doit avoir `tick_volume > VOLUME_CONFIRMATION_MULTIPLE * moyenne(tick_volume des VOLUME_CONFIRMATION_LOOKBACK bougies précédentes)`.

Valeurs de départ (à calibrer empiriquement, voir Validation) : `VOLUME_CONFIRMATION_MULTIPLE = 1.5`, `VOLUME_CONFIRMATION_LOOKBACK = 20`.

### Stop-loss / take-profit

- **Stop-loss** : juste de l'autre côté du niveau cassé, pas du bord opposé du range — `range_high - CHARTPATTERN_STOP_BUFFER` pour un achat, `range_low + CHARTPATTERN_STOP_BUFFER` pour une vente (réutilise le buffer existant de `chart_patterns.py`, pas de nouveau nombre magique). **Correction du 2026-09-29, avant l'écriture du plan** : la version initiale de cette section utilisait le bord opposé du range comme stop, ce qui rend le ratio risque/rendement structurellement toujours < 1 (le risque, de l'ordre de la hauteur du range, dépasse alors systématiquement la récompense, qui est cette même hauteur de range) — `meets_minimum_risk_reward` (seuil 1.5) aurait rejeté quasiment tous les signaux. Avec un stop juste sous le niveau cassé, le risque devient de l'ordre du buffer (petit), et le ratio range_height/buffer dépasse confortablement 1.5 pour un range de taille normale, tout en rejetant naturellement les ranges trop étroits (dégénérés).
- **Take-profit** : objectif mesuré, même principe que `patternHeight` dans `chart_patterns.py` — `close[-1] + (range_high - range_low)` pour un achat, `close[-1] - (range_high - range_low)` pour une vente.
- Filtré par `confluence.meets_minimum_risk_reward` (inchangée), même seuil `SCALP_TAKEPROFIT_RISK_MULTIPLE` que le moteur actuel sauf recalibrage justifié empiriquement.

### Forme du signal renvoyé

Identique au contrat actuel de `compute_signal` : `{"status": "achat"|"vente"|"neutre", "price", "entry", "stop_loss", "take_profit", "trend": "haussier"|"baissier"|"neutre" (déduit du sens de la cassure), "pattern": None}` — `bot.py`/`loop.py`/`backtest.py` n'ont besoin d'aucune modification.

## 3. Argent/risque/exécution (inchangé)

Aucune modification à `risk.py` (dimensionnement, coupe-circuit, plafond de levier), `bot.py` (`decide_and_act`), `loop.py` (boucle réelle, garde marché-fermé/black-out/anti-slippage/re-vérification kill-switch), `api.py`, `state.py`. Le nouveau moteur ne change que la génération du signal.

## 4. Plan de validation (obligatoire avant toute mise en production)

Leçon du 2026-09-29 : optimiser et valider sur la même fenêtre produit des faux positifs qui ne survivent pas hors-échantillon. Ce plan l'empêche structurellement :

1. **Séparation stricte calibrage/validation** : l'historique MetaApi remonte à 2007. Fenêtre de calibrage (ex. 2015-2020) pour caler `VOLUME_CONFIRMATION_MULTIPLE`/`VOLUME_CONFIRMATION_LOOKBACK`/`SCALP_TAKEPROFIT_RISK_MULTIPLE`. Fenêtre de validation (2021-2026, jamais consultée pendant le calibrage) pour le verdict final. Aucun réglage ne doit être modifié après avoir regardé un résultat sur la fenêtre de validation.
2. **Détail annuel obligatoire** : jamais un seul chiffre agrégé sur toute la période — le rapport doit montrer le R-multiple moyen et le P&L par année.
3. **Courbe de capital réelle dès le premier test** : dimensionnement composé via `risk.compute_position_size`/`risk.round_to_volume_step`, pas de P&L à taille fixe.
4. **Coûts de transaction inclus** : `spread` réel (en points, converti en $ via `contract_size`) soustrait de chaque trade simulé dans `backtest.py`.
5. **Critère de rejet explicite** : si le R-multiple moyen sur la fenêtre de validation n'est pas positif et régulier d'une année à l'autre (pas de plus de 1 année négative sur les 5-6 testées, à affiner), la stratégie est rejetée — pas de nouveau cycle de réglage de paramètres pour la "sauver".

## 5. Tests

TDD, conventions existantes du projet (`tests/gold_bot/`) :

- `fetch_gold_candles`/`fetch_gold_candles_range` : pagination MetaApi, exclusion des bougies `state != "complete"`, présence de `tick_volume`/`spread`, mêmes contrats d'erreur que l'actuel (types d'exception, pas de fuite du token dans les messages d'erreur réseau — même correctif que celui appliqué à Twelve Data le 2026-09-29).
- Nouvelle fonction de calcul de range de session : bornes horaires, gestion d'un jour avec trop peu de bougies.
- Nouveau `compute_signal` (ou nouveau module dédié, à trancher en écrivant le plan) : cassure fraîche vs. cassure déjà consommée, confirmation volume passante/rejetée, stop/target corrects pour achat et vente, hors fenêtre de trading → neutre, filtre R:R.
- `backtest.py` : soustraction du spread, intégré aux tests `simulate_trades` existants.

## Risques / questions ouvertes

- Le seuil de volume (1.5×/20 bougies) est une valeur de départ raisonnable mais non validée — le plan de calibrage (section 4.1) doit la confirmer ou l'ajuster avant toute validation finale.
- La pagination MetaApi (max 1000 bougies/appel) est plus petite que Twelve Data (5000) — un backtest sur plusieurs années nécessitera plus d'appels ; à vérifier empiriquement s'il existe une limite de débit sur cet hôte dédié (non testée dans cette session).
- Le rythme d'appel de `fetch_gold_candles` en production (toutes les ~60s, `loop.POLL_INTERVAL_SECONDS`) sur ce nouvel hôte n'a pas été testé en continu — à surveiller au déploiement.
