# Moteur Ichimoku+XAGUSD avec veto IA branché — Design

**Contexte** : design approuvé en conversation le 2026-11-05, après une journée de recherche exhaustive (20+ agents, plusieurs instruments/moteurs testés). Candidat retenu : Ichimoku deux-jambes recalibré sur XAGUSD (argent), le plus robuste de toute la recherche — voir `docs/superpowers/research/gold_bot_metaux_silver_engines/README.md` pour la validation complète (walk-forward 2021-2026, 2 paramètres stables 6/6 fenêtres, 70% de l'edge conservé sans les 5 plus gros trades).

Objectif : porter ce moteur en code de production (nouvelle instance, aucun impact sur le bot personnel ni sur le compte FTMO actuel), et brancher pour de vrai le veto IA déjà existant (`gold_bot/ai_shadow.py`, jusqu'ici uniquement en mode fantôme).

## Non-objectifs explicites

- Ne modifie ni `gold_bot/fibonacci_reversion.py`/`decide_and_act_fibonacci` (compte FTMO actuel) ni `gold_bot/vwap_reversion.py`/`decide_and_act_vwap` (bot personnel) — code et comportement inchangés pour ces deux instances.
- Pas d'ouverture réelle de compte FTMO dans ce chantier — déployé d'abord en `dry_run=true` (simulation), décision de passer en réel reportée à plus tard.
- Pas de calibration pour un autre symbole que XAGUSD avec ce moteur.

## 1. Nouveau moteur : `gold_bot/ichimoku_reversion.py`

Port du moteur de recherche validé (`docs/superpowers/research/gold_bot_metaux_silver_engines/ichimoku_two_leg_xag.py` + `two_leg_execution_xag.py` + `docs/superpowers/research/gold_bot_ichimoku_macd/generate_trend_trades.py` pour les indicateurs génériques) en code de production causal (décision cycle par cycle, pas un backtest).

### Constantes (valeurs walk-forward-validées, à copier telles quelles — PAS à ré-optimiser)

```python
SYMBOL = "XAGUSD"
CONTRACT_SIZE = 5000.0  # verifie empiriquement via broker.get_symbol_specification
MAX_VOLUME = 20.0
VOLUME_STEP = 0.01
MIN_VOLUME = 0.01
ATR_PERIOD = 14
TENKAN_PERIOD = 9
KIJUN_PERIOD = 30
SENKOU_B_PERIOD = 52  # standard Ichimoku, non grid-searche
CLOUD_SHIFT = 26  # standard Ichimoku, non grid-searche
STOP_BUFFER_ATR_MULT = 0.5
TRAIL_BUFFER_ATR_MULT = 0.5  # meme valeur, non grid-searchee separement (limite documentee dans la recherche)
TARGET_R = 2.0
PARTIAL_TP_PCT = 0.7
MIN_DISTANCE_PCT = 0.002
SESSION_END_HOUR_UTC = 22  # meme convention que vwap_reversion/fibonacci_reversion
```

### Fonctions requises

- `resample_h1(candles_5min: list[dict]) -> list[dict]` — réutilise la logique générique de `generate_trend_trades.resample` (bucket horaire UTC). Causale (n'utilise jamais une bougie 5min future pour fermer un bucket passé — un bucket H1 n'est "complet" dans le flux de production qu'une fois toutes ses bougies 5min connues, exactement comme le fait déjà `vwap_reversion` pour son rééchantillonnage M15 ; vérifier ce point précisément lors de l'implémentation, c'est le piège de lookahead le plus probable ici).
- `compute_atr_h1(candles_h1: list[dict], period: int = ATR_PERIOD) -> list[float | None]` — identique à `generate_trend_trades.compute_atr`.
- `compute_ichimoku(candles_h1, tenkan_period=TENKAN_PERIOD, kijun_period=KIJUN_PERIOD, senkou_b_period=SENKOU_B_PERIOD, shift=CLOUD_SHIFT) -> dict` — identique à `generate_trend_trades.compute_ichimoku` (tenkan/kijun = milieu plus-haut/plus-bas glissant INCLUANT la bougie courante ; senkou_a/b décalés de `shift` périodes en avant, lus tels quels à l'indice courant — déjà causal par construction).
- `latest_state(candles_5min: list[dict]) -> dict | None` — nouvelle fonction (pas de contrepartie directe dans le moteur VWAP/Fibonacci, dont les signaux travaillent directement sur des bougies 5min). Rééchantillonne en H1, calcule ATR/Ichimoku sur cette fenêtre, renvoie l'état de la DERNIÈRE bougie H1 complète : `{"close", "high", "low", "atr", "tenkan", "tenkan_prev", "kijun", "kijun_prev", "senkou_a", "senkou_b", "hour_utc"}`. `None` si la fenêtre H1 résultante est trop courte pour que `senkou_b`/`kijun` soient définis (il faut au moins `SENKOU_B_PERIOD + CLOUD_SHIFT` = 78 bougies H1 ≈ 3,25 jours ; la fenêtre de 1000 bougies 5min déjà récupérée par `gold_bot.loop` — CANDLES_FETCH_LIMIT — donne ~83h ≈ 3,5 jours, suffisant mais limite : documenter cette marge serrée explicitement dans le code, contrairement à Fibonacci qui a besoin d'un paramètre `historical_candles` séparé pour sa fenêtre de 90 jours, Ichimoku n'en a PAS besoin avec la fenêtre existante).
- `signal(state: dict) -> str | None` — croisement Tenkan/Kijun confirmé par la position du prix vs nuage (identique à `generate_trend_trades.make_ichimoku_signals`, mais évalué uniquement sur l'état courant, pas en boucle sur tout l'historique) : `"achat"` si tenkan croise kijun à la hausse ET close > max(senkou_a, senkou_b) ; `"vente"` si croisement à la baisse ET close < min(senkou_a, senkou_b) ; `None` sinon.
- `compute_trailing_stop(candles_5min, direction, entry_time_iso, entry_price, current_stop_loss) -> float | None` — reprend la logique de `trail_fn` de la recherche (stop suiveur ancré sur le kijun courant ± `TRAIL_BUFFER_ATR_MULT*atr`, ne se resserre jamais que dans le sens favorable, ne redevient actif qu'après `best_excursion_r >= 1.0` comme dans le moteur de recherche). Même contrat de retour que `fibonacci_reversion.compute_trailing_stop` (`None` = pas de changement).

## 2. Nouvelle fonction `decide_and_act_ichimoku` dans `gold_bot/bot.py`

Même contrat que `decide_and_act_fibonacci` (signature identique, `symbol: str = "XAGUSD"` par défaut, seul symbole calibré) :

```python
def decide_and_act_ichimoku(candles: list[dict], *, contract_size: float, balance: float, equity: float,
                             volume_step: float, min_volume: float, max_volume: float,
                             open_positions: list[dict], circuit_breaker: "risk.CircuitBreaker",
                             symbol: str = "XAGUSD", risk_pct: float = 0.02) -> dict:
```

Flux (reproduit la structure exacte de `decide_and_act_fibonacci`, lignes 392-530 de `bot.py` — s'en inspirer directement, ne pas réinventer l'enchaînement des garde-fous) :

1. `circuit_breaker.check(equity)`.
2. `state = ichimoku_reversion.latest_state(candles)` ; si `None` → `{"action": "aucune", "reason": "historique insuffisant pour l'ichimoku"}`.
3. Position(s) déjà ouverte(s) sur `symbol` : même logique que Fibonacci — clôture de session (`hour_utc >= SESSION_END_HOUR_UTC`), gestion de la jambe runner seule (trailing stop via `compute_trailing_stop`), jambe partielle seule = rien à faire. **Pas de blackout macro/choc de volatilité pour ce moteur** (non testé dans la recherche — à ajouter dans un chantier séparé si jugé utile plus tard, ne pas l'inventer ici sans validation).
4. Pas de position ouverte : si `hour_utc >= SESSION_END_HOUR_UTC` → aucune action. Sinon `direction = ichimoku_reversion.signal(state)` ; si `None` → `{"action": "aucune", "reason": "pas de signal d'entree"}`.
5. `stop_loss = state["kijun"] - STOP_BUFFER_ATR_MULT*state["atr"]` (achat) ou `+` (vente). Vérifier `distance/entry_price >= MIN_DISTANCE_PCT` sinon rejet (même message que Fibonacci : `"distance entree-stop trop faible"`).
6. `circuit_breaker.can_open_position(equity)` sinon rejet (`"coupe-circuit journalier déclenché"`).
7. Sizing : `risk.compute_position_size` + `risk.round_to_volume_step`, split partial/runner via `PARTIAL_TP_PCT` (même mécanique que `two_leg_execution_xag.run_two_leg_backtest`, à adapter au contrat live deux-étapes `ouverture_simulee` déjà utilisé par VWAP/Fibonacci — réutiliser ce format de `steps`, ne pas en inventer un nouveau).
8. **Veto IA — branché pour de vrai, pas fantôme** (voir section 3).
9. Retour `{"action": "simulation", "steps": [...]}` avec les deux jambes (partial avec `take_profit`, runner sans), ou `{"action": "aucune", "reason": "veto IA (opposition)"}` si vétoté à l'étape 8.

## 3. Veto IA branché — modifications de `gold_bot/ai_shadow.py`

### Généraliser le prompt (bug de fond sinon : le prompt actuel mentionne explicitement "rebond de retracement de Fibonacci", factuellement faux pour un signal Ichimoku)

`shadow_judge_entry` et `_build_prompt` prennent un nouveau paramètre `engine_label: str` (ex. `"rebond de retracement de Fibonacci"` pour l'appel existant dans `decide_and_act_fibonacci`, `"croisement Tenkan/Kijun confirme par le nuage (Ichimoku)"` pour le nouvel appel) — remplace la mention en dur dans `_build_prompt`. **Mettre à jour l'appel existant dans `decide_and_act_fibonacci` pour passer explicitement `engine_label="rebond de retracement de Fibonacci"`** (comportement inchangé, juste explicite au lieu d'implicite).

### Fix déjà appliqué (2026-11-05, avant ce chantier)

`max_tokens` passé de 1024 à 4096 dans `shadow_judge_entry` — bug de troncature reproduit en direct (recherche web + raisonnement consomment une partie du budget avant le texte final contenant le JSON du verdict). Déjà fait, déjà testé (96/96 tests passent), ne pas refaire.

### Règle de décision — FAIL-OPEN explicite (point de sécurité validé avec l'utilisateur)

Dans `decide_and_act_ichimoku`, étape 8 :

```python
verdict = ai_shadow.shadow_judge_entry(
    symbol, direction, state_for_ai, candles,
    engine_label="croisement Tenkan/Kijun confirme par le nuage (Ichimoku)",
)
if verdict is not None and verdict.get("verdict") == "opposerait":
    return {"action": "aucune", "reason": "veto IA (opposition)"}
# verdict is None (erreur, timeout, cooldown) OU verdict confirme : le trade se fait normalement
```

**Justification, à documenter en commentaire dans le code** : le veto IA n'a, à la date de ce chantier, jamais produit un seul verdict exploitable en conditions réelles (0/36 tentatives historiques avant le fix du 2026-11-05 — 28 timeouts, 7 échecs de crédits API, 1 réponse mal formée). Bloquer par défaut en cas d'échec reviendrait quasiment à arrêter le moteur. Le signal technique validé par la recherche prime donc en cas de doute sur la disponibilité/fiabilité de l'IA — l'IA ne peut qu'ajouter une restriction (veto explicite), jamais remplacer le signal en cas d'absence de réponse.

`state_for_ai` : construire un dict compatible avec `_build_prompt` (`level` n'a pas de sens pour Ichimoku — utiliser par exemple `state["kijun"]` comme `level` ou ajuster `_build_prompt` pour qu'il accepte des champs optionnels selon `engine_label`, à trancher au moment de l'implémentation en gardant le prompt lisible pour un humain qui relirait le log).

## 4. Déploiement — nouvel environnement, aucun risque pour les deux instances existantes

### Sélection du moteur dans `gold_bot/loop.py`

Nouvelle variable d'environnement `GOLD_BOT_ENGINE` (valeurs : `"vwap"` (défaut implicite, comportement actuel inchangé), `"fibonacci"` (comportement actuel inchangé, piloté par `ftmo_guard_instance is not None` comme aujourd'hui), `"ichimoku_xag"` (nouveau)). Si `GOLD_BOT_ENGINE` est absent de l'environnement, le comportement doit être **identique bit pour bit** à aujourd'hui pour les deux instances existantes (bot personnel, compte FTMO) — tester explicitement cette non-régression.

Quand `GOLD_BOT_ENGINE=ichimoku_xag` : `SYMBOLS = ["XAGUSD"]` (un seul symbole, pas de VWAP en parallèle sur XAUUSD pour cette instance), moteur = `decide_and_act_ichimoku`.

### Déploiement initial

Troisième checkout dédié sur le VPS (même convention que `analyse-or-ftmo` vis-à-vis de `analyse-or`, voir `deploy/README-ftmo.md`), utilisateur système dédié ou partagé à définir au moment du déploiement (pas dans ce spec — décision opérationnelle, pas une décision de conception). **`dry_run=true` obligatoire au premier déploiement** — observer plusieurs cycles réels (decisions_log.jsonl, y compris de vrais appels au veto IA maintenant branché) avant toute bascule manuelle en `dry_run=false`, exactement la même procédure que celle suivie pour le déploiement du moteur Fibonacci sur FTMO.

## Tests requis (TDD complet, comme tout le code de production de ce repo)

- `tests/gold_bot/test_ichimoku_reversion.py` (nouveau) : `resample_h1` (causalité, agrégation correcte), `compute_atr_h1`, `compute_ichimoku` (tenkan/kijun/cloud, y compris le décalage du nuage), `latest_state` (cas insuffisant → None, cas nominal), `signal` (croisement haussier/baissier confirmé par le nuage, cas sans signal), `compute_trailing_stop` (resserrement uniquement favorable, inactif avant best_excursion_r>=1.0).
- Nouveaux cas dans `tests/gold_bot/test_bot.py` pour `decide_and_act_ichimoku` : pas de position + signal achat/vente → ouverture deux jambes ; distance trop faible → rejet ; coupe-circuit → rejet ; position runner ouverte → trailing stop ; fin de session → clôture ; **veto IA actif (opposerait) → aucune action** ; **veto IA en erreur/timeout (None) → le trade se fait quand même (fail-open)** ; **veto IA confirme → le trade se fait**.
- Nouveaux cas dans `tests/gold_bot/test_ai_shadow.py` pour le paramètre `engine_label` (prompt contient le bon libellé selon l'appelant).
- Nouveaux cas de régression dans `tests/gold_bot/test_loop.py` : `GOLD_BOT_ENGINE` absent → comportement strictement identique à aujourd'hui pour les deux instances existantes ; `GOLD_BOT_ENGINE=ichimoku_xag` → `decide_and_act_ichimoku` appelé sur XAGUSD uniquement.
