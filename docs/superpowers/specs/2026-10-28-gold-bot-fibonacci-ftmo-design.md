# Gold Bot — Moteur Fibonacci pour l'instance FTMO (design)

Remplace le moteur VWAP par un moteur de rebond sur retracement de
Fibonacci, **uniquement pour l'instance FTMO** — l'instance personnelle
garde le moteur VWAP actuel sans aucun changement.

## Contexte

Chantier de recherche complet (10+ passes, voir
`docs/superpowers/research/gold_bot_fibonacci_seasonality/`,
`docs/superpowers/research/gold_bot_deepen_winners/`,
`docs/superpowers/research/gold_bot_deepen_fibonacci/`,
`docs/superpowers/research/gold_bot_validate_fib_trend_filter/`,
`docs/superpowers/research/gold_bot_validate_fib_trend_filter_nolookahead/`) :
le moteur VWAP actuel passe le 2-Step FTMO en 869 jours médian à
risk_pct=1% (38.5% de réussite) ou 149 jours à 2% (14.6%) — trop lent
pour l'usage prévu par l'utilisateur (offrir ce service à d'autres
personnes). Après une exploration large (S/R, ORB, Donchian/Turtle,
RSI, Bollinger, Ichimoku, MACD, Fibonacci, pivots, patterns de
chandeliers, filtres de volatilité/tendance/saisonnalité), le signal
de rebond sur retracement de Fibonacci, avec une gestion de sortie à
deux jambes (prise partielle + stop suiveur ATR) et un filtre de force
de tendance, est le candidat final retenu :

- **risk_pct=2%, chiffre final walk-forward-validé indépendamment,
  CORRIGÉ le 2026-10-28** (y compris le seuil du filtre de tendance,
  validé séparément des autres paramètres) : **22.8% de réussite
  2-Step / 35 jours médian** — contre 14.6%/149j pour le VWAP actuel.
  Un premier chiffre (25.4%/29j) avait été rapporté, mais sa
  validation taguait chaque signal avec la clôture FINALE de son
  propre jour (lookahead, inconnue au moment réel du signal) ; une
  revue finale de branche a trouvé ce biais, et une re-validation
  indépendante sans lookahead (clôture du dernier jour COMPLET avant
  le signal, comme le code de production) donne le chiffre corrigé
  ci-dessus — une dégradation réelle, pas cosmétique (~10% de réussite
  relative, ~21% de durée en plus), voir le répertoire de recherche
  cité pour le détail par année. Le seuil de 5% lui-même survit à la
  correction (toujours le plus fréquent, 4/6 fenêtres d'entraînement).
- Généralisation : 3/6 années de test (2021-2026) positives (corrigé
  depuis 4/6) — moins que le 6/6 du VWAP en production, un risque
  assumé et documenté, pas une garantie de même niveau.
- À risk_pct=1%, le chiffre corrigé s'améliore au contraire (70.2% de
  réussite / 328 jours médian, contre 60.9%/272j avant correction) —
  bien plus fiable, mais ~11 mois au lieu d'~1 mois pour valider un
  compte. Retenu malgré tout : risk_pct=2% avec le chiffre corrigé,
  décision utilisateur du 2026-10-28 après avoir vu les deux options.
- Paramètres walk-forward validés, à utiliser tels quels (pas une
  ré-optimisation, voir recherche citée) : fenêtre de retracement
  `window=40` bougies 5min, niveau `fib_pct=0.618`, cible de la jambe
  partielle `target_r=1.5`, filtre de force de tendance sur rendement
  glissant 20 jours ouvrés, seuil `trend_filter_threshold=0.05` (5%).

## Portée

- **Uniquement l'instance FTMO** (`gold_bot-loop-ftmo`/`gold-bot-api-ftmo`
  sur le VPS, checkout `/home/goldbot/analyse-or-ftmo`). L'instance
  personnelle (`gold-bot-loop`/`gold-bot-api`, checkout
  `/home/goldbot/analyse-or`) continue d'utiliser `decide_and_act_vwap`
  sans aucun changement — même code partagé (`gold_bot/`), juste un
  choix de moteur différent par instance.
- **XAUUSD uniquement** — pas de calibration Fibonacci pour XAGUSD
  (jamais testée dans la recherche), et la restriction XAUUSD-only
  pour l'instance FTMO existe déjà (clause CGU sur l'exposition
  corrélée, voir `gold_bot/loop.py::SYMBOLS` filtré par
  `ftmo_guard_instance`).
- **Ne touche pas** `gold_bot/risk.py` (sizing/profils de risque
  inchangés — le moteur Fibonacci utilise le même `risk_pct` résolu
  par le profil actif), `gold_bot/ftmo_guard.py` (garde-fou FTMO
  inchangé, continue de protéger n'importe quel moteur), ni
  `gold_bot/vwap_reversion.py` (moteur VWAP non modifié).

## Architecture

### 1. `gold_bot/fibonacci_reversion.py` (nouveau fichier)

Même structure que `gold_bot/vwap_reversion.py` (constantes au niveau
module, fonctions pures, indicateurs recalculés à chaque appel) :

- `ATR_PERIOD = 14`, `TOUCH_ATR_MULT = 0.3`, `STOP_BUFFER_ATR_MULT = 0.5`,
  `TRAIL_BUFFER_ATR_MULT = 0.5`, `PARTIAL_TP_PCT = 0.7`,
  `TARGET_R = 1.5`, `FIB_WINDOW = 40`, `FIB_PCT = 0.618`,
  `TREND_WINDOW_DAYS = 20`, `TREND_FILTER_THRESHOLD = 0.05`,
  `SESSION_END_HOUR_UTC = 22`, `MIN_DISTANCE_PCT = 0.002` — mêmes
  valeurs que la recherche validée, copiées telles quelles.
- `compute_atr(candles, period=ATR_PERIOD)` — ATR causal (Wilder
  simplifié), portée depuis
  `docs/superpowers/research/gold_bot_sr_breakout/generate_breakout_trades.py::compute_atr`
  (recherche, jamais importée directement depuis `docs/` en
  production — code dupliqué dans ce nouveau fichier, même convention
  que `vwap_reversion.py` qui ne dépend d'aucun module de recherche).
- `levels_fibonacci(candles, window=FIB_WINDOW, fib_pct=FIB_PCT)` —
  portée depuis
  `docs/superpowers/research/gold_bot_fibonacci_seasonality/fibonacci_trades.py::levels_fibonacci`
  à l'identique (deque monotone causale, renvoie niveau+biais par
  bougie).
- `daily_trend_strength(candles, window_days=TREND_WINDOW_DAYS)` —
  portée depuis
  `docs/superpowers/research/gold_bot_deepen_fibonacci/diagnose_and_improve.py::daily_trend_strength`
  à l'identique (rendement glissant sur clôtures journalières).
- `latest_state(candles_5min)` — même contrat que
  `vwap_reversion.latest_state` : rééchantillonne si besoin (ici,
  granularité 5min brute, pas de rééchantillonnage 15min — la
  recherche a validé sur 5min brut), renvoie l'état de la dernière
  bougie (niveau, biais, ATR, force de tendance du jour).
- `compute_trailing_stop(candles_5min, direction, entry_time_iso,
  entry_price, current_stop_loss) -> float | None` — même contrat que
  `vwap_reversion.compute_trailing_stop`, mais trail basé sur l'ATR
  capturé à l'entrée (pas l'EMA50, voir
  `docs/superpowers/research/gold_bot_deepen_winners/fibonacci_two_leg.py::trail_fn`) :
  actif seulement après une excursion favorable ≥ 1R, ne resserre
  jamais au-delà du prix d'entrée, ne recule jamais.

### 2. `gold_bot/bot.py::decide_and_act_fibonacci` (nouvelle fonction)

Même signature et même contrat de retour que `decide_and_act_vwap`
(`candles`, `contract_size`, `balance`, `equity`, `volume_step`,
`min_volume`, `max_volume`, `open_positions`, `circuit_breaker`,
`symbol="XAUUSD"`, `risk_pct`, `historical_candles=None`) — renvoie un
dict `{"action": "aucune"|"simulation"|"erreur"|"ignore", "steps": [...]}`,
mêmes conventions de step (`ouverture_simulee`, `modification_simulee`,
`clôture_simulee`) que `decide_and_act_vwap`, pour que
`gold_bot/loop.py::run_cycle` n'ait besoin d'aucune adaptation au-delà
du choix de fonction (voir point 3) :

- Position ouverte sur le symbole : gère les DEUX jambes (partielle
  TP fixe déjà posé côté broker, runner géré par
  `compute_trailing_stop`) — même distinction `takeProfit` présent/
  absent que `decide_and_act_vwap` (voir
  `docs/superpowers/specs/2026-09-30-gold-bot-vwap-partial-tp-addendum.md`,
  logique identique, portée telle quelle).
- Pas de position : calcule le niveau Fibonacci, le biais, l'ATR et la
  force de tendance du jour courant. Signal si (a) le niveau est
  touché avec rejet (clôture au-delà, tolérance `TOUCH_ATR_MULT×ATR`)
  ET (b) `abs(force_de_tendance_du_jour) <= TREND_FILTER_THRESHOLD`.
  Si oui : calcule stop (niveau ∓ `STOP_BUFFER_ATR_MULT×ATR`), cible
  partielle (`entry ± TARGET_R×distance`), répartit le volume total
  70/30 (partielle/runner) comme `decide_and_act_vwap`, avec le même
  repli (jambe partielle nulle → tout en runner) si le compte est
  trop petit pour les deux jambes séparément.

### 3. `gold_bot/loop.py` — sélection du moteur par instance

Dans `run_cycle`, remplace l'appel direct à
`bot.decide_and_act_vwap(...)` par un choix conditionné sur
`ftmo_guard_instance is not None` (même garde déjà utilisée pour la
restriction XAUUSD-only et les alertes FTMO) :

```python
engine = bot.decide_and_act_fibonacci if ftmo_guard_instance is not None else bot.decide_and_act_vwap
decision = engine(candles, contract_size=contract_size, balance=balance, equity=equity, ...)
```

Aucun autre changement dans `loop.py` — `historical_candles` reste
passé tel quel (ignoré par `decide_and_act_fibonacci` si non
pertinent, même convention de compatibilité que les autres
paramètres).

## Tests

TDD complet, même rigueur que `tests/gold_bot/test_vwap_reversion.py`
et `tests/gold_bot/test_bot.py` existants :

- `tests/gold_bot/test_fibonacci_reversion.py` — `compute_atr`,
  `levels_fibonacci`, `daily_trend_strength`, `latest_state`,
  `compute_trailing_stop` (cas : pas de position, excursion
  insuffisante, resserrement normal, jamais de recul, jamais au-delà
  de l'entrée).
- `tests/gold_bot/test_bot.py` — nouveaux cas pour
  `decide_and_act_fibonacci` : aucun signal, signal filtré par la
  tendance, signal valide avec sizing des deux jambes, repli volume
  nul, gestion de position déjà ouverte (jambe partielle seule,
  runner seul, les deux), mêmes cas que les tests `decide_and_act_vwap`
  existants adaptés au nouveau moteur.
- `tests/gold_bot/test_loop.py` — vérifie que `run_cycle` appelle
  `decide_and_act_fibonacci` quand `ftmo_guard_instance` est fourni et
  `decide_and_act_vwap` sinon (régression : l'instance personnelle ne
  doit JAMAIS être affectée).

## Déploiement (sécurité — compte réel)

1. Déployer le code sur `/home/goldbot/analyse-or-ftmo` avec
   `dry_run=true` (déjà le comportement par défaut de
   `state.json` — pas besoin de le repasser explicitement si déjà
   remis à `true`, sinon le repasser avant déploiement).
   Redémarrer les services, observer plusieurs cycles réels
   (`decisions_log.jsonl`, `/status`, `/dashboard`) pour confirmer un
   comportement sain avant tout passage en live.
2. Une fois confirmé : repasser `dry_run=false` (édition directe de
   `state.json` sur le VPS, même procédure que pour le passage en
   live du moteur VWAP).
3. `risk_profile` reste inchangé (profil 1, risk_pct=2%, déjà actif).

## Non-objectifs

- Pas de support XAGUSD pour ce moteur (jamais calibré).
- Pas de changement à l'instance personnelle.
- Pas de mécanisme de recalibrage automatique du seuil de tendance
  (dérive potentielle dans le temps, documentée dans la recherche
  mais hors scope — recalibrage manuel futur si nécessaire, comme
  pour tout paramètre du moteur VWAP).
- Pas de mode "cumul" avec le VWAP (le candidat retenu est le mode
  "remplacement" — le cumul a une généralisation plus faible d'après
  la recherche).
