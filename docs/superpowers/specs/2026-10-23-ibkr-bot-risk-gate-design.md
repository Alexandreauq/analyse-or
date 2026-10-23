# Bot IBKR actions — dimensionnement par le risque, coupe-circuit journalier, vérification des côtes

**Statut** : brainstormé et validé avec l'utilisateur le 2026-10-23, prêt pour relecture puis `writing-plans`.

## Contexte et origine

Deuxième chantier de discipline de risque inspiré de `day-trader-skill` (plugin
Claude Code), après le bot Or (`gold_bot/`, terminé — voir
`docs/superpowers/specs/2026-10-22-gold-bot-risk-gate-design.md`). L'utilisateur
a explicitement choisi ce chantier en second car il doit rendre le bot
**réellement opérationnel**, pas seulement mieux outillé en théorie : le bot
IBKR actions n'a jamais placé un seul ordre réel depuis son lancement.

**Audit du code actuel (vérifié directement, pas supposé)** :

- `ibkr_bot/sizing.py:22` — `BUDGET_EUR = 500.0`, budget **fixe**, aucun lien
  avec un stop ou une distance de risque. `compute_quantity` (ligne 87)
  calcule `quantite = floor(budget_eur * fx_rate / prix_unitaire)`.
- `ibkr_bot/portfolio.py:62` — un stop-loss existe déjà, mais seulement comme
  règle de **sortie** : `STOP_LOSS_PCT = -20.0`, utilisé par `exit_reason()`
  (lignes 268-312) pour clôturer une position déjà ouverte. Jamais utilisé
  pour calculer la taille à l'**entrée**.
- `ibkr_bot/state.py:18` — seulement `kill_switch`/`dry_run`. Aucun
  coupe-circuit de perte journalière.
- `ibkr_bot/portfolio.py:56` — `MAX_NEW_ENTRIES_PER_DAY = 3` existe déjà
  (limite le nombre d'entrées, logique de diversification), avec aussi
  `MAX_POSITIONS=10`, `MAX_POSITIONS_PER_SECTOR=3`,
  `MAX_POSITIONS_PER_INDEX=4`, `MAX_POSITIONS_PER_ZONE=5`. **Décision
  utilisateur : conservé tel quel, indépendant du nouveau coupe-circuit de
  perte** — deux garde-fous différents (nombre d'entrées vs perte en %), pas
  redondants.
- **Cause racine confirmée du "jamais d'ordre réel"** : le compte IBKR réel
  n'a qu'environ 20€, alors que `BUDGET_EUR=500€` par position. Aucun
  garde-fou en amont ne bloque ça — un ordre réel échouerait côté IBKR pour
  fonds insuffisants plutôt que d'être refusé par le bot. **Décision
  utilisateur : résolu par le dimensionnement par le risque lui-même**
  (composant A ci-dessous) — en dérivant la taille du **vrai solde** plutôt
  que d'une constante, les deux problèmes (sous-capitalisation et absence de
  dimensionnement par le risque) se résolvent par le même changement.
- `ibkr_bot/gateway.py:187` — `base_currency_cash(base_url, account_id) ->
  float` existe déjà pour récupérer le vrai solde en cash. Pas de nouvelle
  plomberie réseau nécessaire.
- `docs/signal_tracking.json` — **déjà alimenté en continu** par
  `indices_score.update_signal_tracking` (indices_score.py:4709-4734), pour
  TOUS les signaux "entrée" du système de scoring Indices (pas seulement ceux
  qu'ibkr_bot a réellement tradés). `ibkr_bot/signals.py` le lit déjà, mais
  seulement pour détecter les signaux "nouveaux aujourd'hui" — jamais pour
  une évaluation de qualité/historique. Structure confirmée :
  `positions[]` avec `id, ticker, index, sector, status, entry_date,
  entry_price, close_date, close_price, close_reason, return_pct,
  index_return_pct, ...`.
- **Aucune fonction Python réutilisable n'existe pour calculer l'espérance
  historique** — le seul calcul agrégé existant (taux de réussite, rendement
  moyen, alpha moyen) est côté client, en JavaScript, dans
  `docs/index.html:4976-5014` (`buildSignalStatsHtml`). La formule est
  réutilisable (win = `return_pct > 0`, alpha = `return_pct -
  index_return_pct`, moyenné sur les positions closes) mais doit être
  portée en Python — rien à réutiliser tel quel côté serveur.
- **Constat important, à ne pas cacher** : le diagnostic global déjà connu
  (mémoire du projet) est légèrement négatif (alpha ~-0.4%, ~45% de
  réussite) — c'est le même type de constat "pas d'edge démontré" que pour
  le score fondamental et les stratégies techniques testées ailleurs dans ce
  projet. **Décision utilisateur : segmenter la vérification des côtes par
  indice/secteur** plutôt que sur l'espérance globale, pour laisser une
  vraie chance à un segment qui performerait mieux que la moyenne, plutôt
  que de neutraliser le bot d'entrée de jeu avec un seuil global déjà
  perdant.
- Architecture confirmée identique au bot Or : `signals.py`/`portfolio.py`/
  `sizing.py` sont des fonctions de décision **pures** (commentaire
  `portfolio.py:6-7` : "Ce module DECIDE, il ne passe aucun ordre") ; seul
  `daily.py::_place_order` (via `_executer_entree`) touche le réseau. Le
  nouvel portail de risque doit suivre la même règle : jamais d'appel réseau
  dans les fonctions de décision, les données déjà récupérées leur sont
  passées en paramètre.
- Flux d'appel confirmé (`daily.py::run_batch`, lignes 577-1033) :
  kill_switch → jour de bourse → git pull → fraîcheur des données →
  preflight Gateway → réconciliation positions → sorties (`exit_reason`) →
  entrées : `signals.load_signal_tracking` + `signals.collect_new_signals`
  (lignes 876-877) → par signal : `sizing.compute_quantity` +
  `contracts.resolve_conid` (lignes 896-905) → filtres devise/détention →
  `portfolio.select_entries` (lignes 993-995) → `_executer_entree` →
  `_place_order` → `gw.place_market_order` (lignes 251-391, seul chemin qui
  touche réellement le réseau pour un ordre).

## Composant A — dimensionnement par le risque (résout aussi la sous-capitalisation)

**Extraction partagée** (décision utilisateur) : `gold_bot.risk.CircuitBreaker`
déménage vers un nouveau module `shared_risk.py` à la racine du dépôt
(sibling de `gold_bot/` et `ibkr_bot/`) — la classe elle-même n'a rien de
spécifique à l'or (perte %/nombre de trades par jour, persistance, reset au
changement de jour UTC). `gold_bot/risk.py` devient `from shared_risk import
CircuitBreaker` (ré-export) pour que tout le code et tous les tests existants
de `gold_bot` continuent de fonctionner sans modification
(`risk.CircuitBreaker(...)` reste valide). `compute_position_size`/
`round_to_volume_step` (spécifiques MT5/taille de contrat) restent dans
`gold_bot/risk.py`, rien à y déplacer.

**Nouveau dimensionnement dans `ibkr_bot/sizing.py`** : `STOP_LOSS_PCT`
(déjà dans `portfolio.py`, `-20.0`) est une distance de stop **fixe en %**,
pas un prix absolu comme pour l'or — ça simplifie la formule par rapport au
bot Or (pas besoin de `entry`/`stop_loss` séparés) :

```
risk_amount_eur = balance_eur * risk_pct
notional_eur = risk_amount_eur / (abs(STOP_LOSS_PCT) / 100)
quantite = floor(notional_eur * fx_rate / unit_price_indices)
```

Ex. avec `balance_eur=20`, `risk_pct=0.05`, `STOP_LOSS_PCT=-20.0` :
`notional_eur = 20*0.05/0.20 = 5€` — une position minuscule, proportionnée à
un petit compte, qui grandit automatiquement avec le solde réel sans jamais
avoir besoin d'ajuster une constante à la main. Pas besoin d'un plafond de
levier séparé (contrairement au bot Or) : `notional_eur` est déjà borné par
construction à `balance_eur * risk_pct / 0.20` — pas de levier/marge sur un
compte actions au comptant.

`risk_pct` : une seule constante configurable (pas un système de profils 1-5
comme le bot Or) — le bot IBKR n'a aujourd'hui aucune notion de profil de
risque ni d'API pour en changer, et en ajouter une sortirait du périmètre de
ce chantier (pas demandé). Valeur de départ proposée : `risk_pct = 0.05`
(5%), ajustable si l'utilisateur le souhaite en relecture de spec.

`balance_eur` : récupéré via `gateway.base_currency_cash(base_url,
account_id)` (déjà existant), dans `daily.py`, passé en paramètre à
`compute_quantity` — jamais récupéré par `sizing.py` lui-même, qui reste une
fonction pure.

## Composant B — coupe-circuit journalier de perte

Réutilise `shared_risk.CircuitBreaker` tel quel (perte en % du solde,
persistance, reset automatique au changement de jour UTC) — même classe que
le bot Or, instance séparée (état persistant propre à `ibkr_bot/`, ex.
`ibkr_bot/circuit_breaker_state.json`). Pas de plafond de trades/jour sur
cette instance (`max_trades_per_day=None`, défaut) — **décision utilisateur :
`MAX_NEW_ENTRIES_PER_DAY` existant dans `portfolio.py` reste le seul
plafond de nombre d'entrées**, le `CircuitBreaker` ne gère ici que la perte
en %.

`circuit_breaker.check(equity)` doit être appelé une fois par cycle
`run_batch` (équivalent du `check()` par cycle du bot Or), et
`circuit_breaker.can_open_position(equity)` doit bloquer toute nouvelle
entrée (jamais les sorties — même garantie que le bot Or : un coupe-circuit
ne doit jamais empêcher de couper les pertes sur une position déjà ouverte).
`equity` : solde + valeur des positions ouvertes — à calculer à partir des
données déjà récupérées par `daily.py` (reconciliation), jamais un nouvel
appel réseau dédié.

## Composant C — vérification des côtes avant trade (segmentée)

Nouveau module `ibkr_bot/odds_check.py`, fonction principale
`historical_odds_for_signal(positions: list[dict], index: str, sector: str,
min_sample_size: int = 20) -> dict`, port Python de la formule déjà
implémentée côté client (`docs/index.html::buildSignalStatsHtml`) :
- Filtre `positions` (les éléments de `docs/signal_tracking.json`) sur
  `status == "closed"` ET (`index == index` **ou**, si l'échantillon par
  indice est insuffisant, repli sur `sector == sector`) — segmentation en
  cascade, pas un double filtre strict (sinon l'échantillon serait presque
  toujours vide). Si NI l'indice NI le secteur n'atteignent
  `min_sample_size`, verdict `"insufficient_history"` (ne bloque jamais).
- `alpha_pct` par position = `return_pct - index_return_pct` (même formule
  que `buildSignalStatsHtml`).
- Verdict `"unfavorable"` (bloque) si et seulement si la moyenne de
  `alpha_pct` sur le segment retenu est négative ou nulle — même critère
  simple et objectif que le bot Or (pas de combinaison win-rate/alpha
  arbitraire).
- Renvoie aussi `segment_used` (`"index"` ou `"sector"`) et `sample_size`
  dans le dict, pour que ce soit visible/auditable dans les logs de décision
  plutôt qu'une boîte noire.

Appelé entre `signals.collect_new_signals` et `portfolio.select_entries`
(`daily.py`, autour de la ligne 876-995) — chaque signal candidat qui
survit aux filtres déjà existants passe par `historical_odds_for_signal`
avant d'atteindre `select_entries` ; un verdict `"unfavorable"` retire le
signal de la liste des candidats (avec la raison journalisée), exactement
comme un rejet existant de `collect_new_signals`. `odds_check.py` reste une
fonction pure : `positions` (déjà chargé par `signals.load_signal_tracking`)
lui est passé en paramètre, jamais lu depuis le disque par ce module
lui-même.

## Hors périmètre de ce chantier

- Système de profils de risque (1-5) façon bot Or pour `risk_pct` — pas
  demandé, `risk_pct` reste une constante simple pour l'instant.
- Toute modification de `STOP_LOSS_PCT`, `MAX_NEW_ENTRIES_PER_DAY`,
  `MAX_POSITIONS*` existants — conservés tels quels.
- Recapitalisation réelle du compte IBKR — écarté par l'utilisateur au
  profit du dimensionnement dynamique.
- Mémoïsation/cache du calcul des côtes — contrairement au bot Or (cycle
  continu de 60s), `daily.py` tourne une fois par jour ; le coût de
  `historical_odds_for_signal` (un filtre + une moyenne sur une liste déjà
  en mémoire, pas de rejeu de bougies) est négligeable, aucune mise en cache
  nécessaire.

## Tests

TDD, comme pour le bot Or : extraction de `CircuitBreaker` vers
`shared_risk.py` (tous les tests existants de `gold_bot.risk.CircuitBreaker`
doivent continuer à passer sans modification, plus de nouveaux tests dans
`tests/test_shared_risk.py` si pertinent) ; nouveau dimensionnement dans
`sizing.py` (cas limite : solde quasi nul, `risk_pct` à 0, etc.) ; nouveau
`odds_check.py` (segment indice suffisant, repli secteur, double
insuffisant, verdict favorable/défavorable) ; branchement dans `daily.py`
(signal bloqué par le coupe-circuit, par les côtes, les deux passent).
