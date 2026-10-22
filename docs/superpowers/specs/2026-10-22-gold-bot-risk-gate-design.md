# Bot Or — plafond de trades/jour + vérification des cotes avant trade

**Statut** : validé en brainstorming avec l'utilisateur le 2026-10-22, prêt pour `writing-plans`.

## Contexte et origine

En auditant plusieurs repos de trading externes (ai-gold-scalper, GOLD_ORB,
XAUUSD_TRADING_ASISTENT_AI, TradingView_GoldenIndicator, Golden-Strategist-EA)
et le plugin Claude Code `day-trader-skill`, aucune stratégie d'entrée
n'est apparue solide — mais la discipline de risque de `day-trader-skill`
(`trader-budget`, `trader-risk`, `trader-timemachine`) a été jugée digne
d'intérêt, indépendamment de sa propre stratégie (cassure d'ouverture de
range, jugée faible). Ce document couvre le premier des deux chantiers
(bot Or avant bot IBKR actions, décidé explicitement par l'utilisateur).

**Audit initial corrigé en cours de brainstorming** : une première analyse
du code existant avait conclu, à tort, qu'aucun coupe-circuit automatique
n'existait. En creusant plus loin, `gold_bot/risk.py::CircuitBreaker` est
en réalité déjà complet et déjà branché en production
(`loop.py::run_cycle` → `bot.py::decide_and_act_vwap`) : coupe-circuit sur
la perte journalière en % du solde (lié au profil de risque 1-5), reset
automatique au changement de jour UTC, état persisté (survit à un
redémarrage), verrou `_tripped_today` qui empêche un dé-déclenchement
silencieux si le profil change en cours de journée. `decide_and_act_vwap`
fonctionne déjà comme un portail de décision unique de facto : chaque
rejet renvoie une raison explicite (`{"action": "aucune", "reason": "..."}`).

Il ne manque donc, une fois l'audit corrigé, que **deux choses réelles** :

1. Un plafond de **nombre de trades/jour** — le coupe-circuit actuel ne
   regarde que la perte en %, jamais le nombre d'ouvertures.
2. Une **vérification des cotes historiques en direct**, avant chaque
   ouverture — rien d'équivalent n'existe dans le chemin live
   (`gold_bot/backtest.py` est confirmé être un outil de recherche
   hors-ligne, et surtout il rejoue par défaut `confluence.compute_signal`,
   l'**ancien** moteur à base de figures chartistes — pas
   `vwap_reversion`/`decide_and_act_vwap`, la stratégie réellement en
   production aujourd'hui. Aucun script de validation historique propre à
   VWAP-reversion n'est committé dans le dépôt principal ; la validation
   train/test mentionnée dans
   `docs/superpowers/specs/2026-09-30-gold-bot-vwap-reversion-design.md`
   a été faite via un script jetable, non réutilisable tel quel).

**Explicitement hors périmètre** (décidé en brainstorming) : un ratio R:R
minimum par trade. La stratégie VWAP-reversion n'a volontairement pas de
cible fixe sur sa jambe principale — elle laisse courir le profit via le
stop suiveur plutôt que de viser un multiple de R fixe. Importer l'idée de
`day-trader-skill` (pensée pour une cible fixe façon `trader-exit`, 2R)
dénaturerait la philosophie de sortie du bot plutôt que de l'améliorer.
Seule la jambe de prise partielle a une cible (`partial_tp_r`, déjà figée
en config par symbole) — l'utilisateur a choisi de ne pas non plus en
faire un garde-fou de configuration pour ce chantier.

## Composant A — plafond de trades/jour

Extension de `gold_bot/risk.py::CircuitBreaker`, pas un nouveau mécanisme :
la classe possède déjà la notion de "jour UTC courant" et la persistance,
c'est le point d'extension naturel plutôt qu'un système parallèle.

**`RISK_PROFILE_PARAMS`** gagne une troisième clé par profil,
`max_trades_per_day`, proposée (à ajuster librement, pas une valeur
scientifique) en cohérence avec la progression déjà existante de
`risk_pct`/`threshold_pct` :

```python
RISK_PROFILE_PARAMS: dict[int, dict[str, float]] = {
    1: {"risk_pct": 0.02,  "threshold_pct": 0.05,  "max_trades_per_day": 2},
    2: {"risk_pct": 0.035, "threshold_pct": 0.075, "max_trades_per_day": 3},
    3: {"risk_pct": 0.05,  "threshold_pct": 0.10,  "max_trades_per_day": 4},
    4: {"risk_pct": 0.075, "threshold_pct": 0.135, "max_trades_per_day": 6},
    5: {"risk_pct": 0.10,  "threshold_pct": 0.175, "max_trades_per_day": 8},
}
```

**`CircuitBreaker`** :
- `__init__` gagne `max_trades_per_day: int | None = None` (comportement
  inchangé si omis — important pour ne pas casser les tests/usages
  existants qui construisent un `CircuitBreaker` sans ce paramètre).
- Nouvel état interne `_trades_opened_today: int = 0`, chargé/persisté
  exactement comme `_day`/`_starting_balance`/`_tripped_today`
  aujourd'hui (même bloc `_persist()`, nouvelle clé
  `circuit_breaker_trades_opened_today` dans l'état sauvegardé).
- `check()` réinitialise `_trades_opened_today = 0` dans les deux cas qui
  réinitialisent déjà `_tripped_today` (nouveau jour, ou rebaselining sur
  compte fraîchement financé) — même logique, pas de nouveau cas.
- Nouvelle méthode publique `record_trade_opened() -> None` : incrémente
  `_trades_opened_today`, persiste. Ne touche à rien d'autre (ne
  déclenche pas `_tripped_today` elle-même — c'est `can_open_position`
  qui lit le compteur).
- `can_open_position()` : après la vérification existante sur la perte en
  %, ajoute une vérification symétrique — si
  `max_trades_per_day is not None and self._trades_opened_today >= self.max_trades_per_day`,
  retourne `False` et pose `_tripped_today = True` (même sémantique que
  le déclenchement par perte : bloqué pour le reste du jour UTC,
  reset automatique le lendemain — cohérent avec la décision déjà prise
  pour le coupe-circuit perte).

**Où appeler `record_trade_opened()`** : dans `loop.py::run_cycle`, après
l'appel à `execute_steps` (ligne ~312), uniquement si l'exécution a
réellement réussi (`not had_error`) ET que les `steps` exécutés
contenaient au moins un step de type `"ouverture_simulee"`. Compte
**un seul trade** même quand une entrée se scinde en deux ordres broker
(jambe partielle + jambe runner, voir `decide_and_act_vwap`) — c'est un
seul signal/une seule décision d'entrée, pas deux. Ne compte jamais les
modifications de stop suiveur (`"modification_simulee"`) ni les clôtures
(`"clôture_simulee"`).

## Composant B — vérification des cotes historiques avant trade

Nouveau module `gold_bot/odds_check.py`. Contrairement à
`day-trader-skill`'s `trader-timemachine` (qui fait un bootstrap par
ré-échantillonnage aléatoire de bougies du même créneau horaire, perdant
l'autocorrélation intra-session réelle), le bot Or a assez d'historique
5 minutes disponible pour faire mieux : **rejouer directement les
instances historiques réelles** du même type de signal, avec les
**vraies mécaniques de sortie** de la stratégie (stop suiveur, jambe
partielle, clôture forcée en fin de session), plutôt qu'un bootstrap
statistique. C'est aussi, en bonus, le premier outil de validation
historique propre à VWAP-reversion committé dans le dépôt (comble le trou
identifié ci-dessus).

**Fonction principale** :

```python
def historical_odds_for_setup(
    candles_history: list[dict], direction: str, symbol: str = "XAUUSD",
    min_sample_size: int = 20,
) -> dict:
```

Logique :
1. Parcourt `candles_history` (récupéré via
   `backtest.fetch_gold_candles_range`, même fonction déjà utilisée par
   `backtest.py` — pas de nouveau code de récupération de données), en
   réappliquant `vwap_reversion.latest_state` + `get_symbol_params` à
   chaque fenêtre glissante, exactement comme le fait `decide_and_act_vwap`
   en production — pour retrouver tous les instants passés où **le même
   type de signal** (même `direction`, mêmes seuils `entry_sigma`) se
   serait déclenché.
2. Pour chaque instance trouvée, simule la sortie réelle en rejouant
   `vwap_reversion.compute_trailing_stop` bougie par bougie à partir de
   l'entrée (même mécanique que la boucle live), plus la jambe de prise
   partielle (`partial_tp_pct`/`partial_tp_r`) et la clôture forcée à
   `SESSION_END_HOUR_UTC` — pas une simulation simplifiée à stop fixe.
3. Agrège : nombre d'instances trouvées, % gagnantes, rendement moyen en
   $ et en % du prix d'entrée.
4. Renvoie `{"sample_size": n, "win_rate_pct": ..., "avg_return_pct": ...,
   "verdict": "insufficient_history" | "favorable" | "unfavorable"}`.

**Règle de verdict, importante** :
- `sample_size < min_sample_size` → `"insufficient_history"` — **ne
  bloque jamais le trade**. Un échantillon trop petit ne permet de
  conclure dans aucun sens ; bloquer par défaut reviendrait à empêcher le
  bot de jamais trader sur un signal rare. Journalisé, mais laisse passer.
- `sample_size >= min_sample_size` et `avg_return_pct <= 0` (espérance
  nette négative sur l'échantillon historique, frais de spread déjà
  déduits par `_compute_return`) → `"unfavorable"`, **bloque le trade**.
  Critère volontairement simple et objectif (un seul seuil, pas de
  combinaison win-rate/R-multiple arbitraire) — cohérent avec la
  méthodologie déjà utilisée dans ce projet pour juger une stratégie
  (comparaison d'espérance, jamais de seuil de taux de réussite isolé,
  qui ignore l'asymétrie gain/perte).
- Sinon → `"favorable"`, laisse passer.

**Intégration dans `decide_and_act_vwap`** : appelée juste avant
`circuit_breaker.can_open_position(equity)` (échouer vite sur l'historique
avant de consommer un appel réseau pour le solde du compte), avec sa
propre raison explicite en cas de blocage :
`{"action": "aucune", "reason": "cotes historiques defavorables pour ce type de signal"}`.

**Coût/performance** : ne s'exécute que lorsqu'un signal d'entrée est
effectivement détecté (pas à chaque cycle — la grande majorité des cycles
n'atteignent jamais cette branche), donc pas de surcoût réseau/calcul
significatif sur le fonctionnement normal du bot. Mise en cache du
résultat par `(direction, jour)` laissée en option d'implémentation, pas
une exigence de ce chantier.

## Tests

TDD, comme le reste du projet :
- `CircuitBreaker` : nouveau test pour le reset de `_trades_opened_today`
  au changement de jour/rebaselining ; test pour `record_trade_opened()`
  + persistance ; test pour le déclenchement par nombre de trades
  (indépendant du déclenchement par perte) ; test que les deux motifs de
  déclenchement figent `_tripped_today` de la même façon.
- `odds_check.py` : tests sur un historique de bougies fixé (fixture
  déterministe), couvrant les 3 verdicts (`insufficient_history`,
  `favorable`, `unfavorable`) et la bonne reproduction de la mécanique de
  sortie réelle (stop suiveur, jambe partielle, clôture de session).
- `decide_and_act_vwap` : test que le nouveau plafond de trades/jour et
  la vérification des cotes bloquent bien l'ouverture avec la raison
  attendue, sans affecter la gestion des positions déjà ouvertes
  (modification du stop suiveur, clôture de fin de session).

## Hors périmètre de ce chantier

- Ratio R:R minimum (abandonné, voir Contexte).
- Bot IBKR actions — chantier séparé, à brainstormer à part (écart plus
  important : dimensionnement à budget fixe, aucun stop-loss en
  production, problème de sous-capitalisation du compte réel à traiter
  en même temps).
- Tout changement à la stratégie d'entrée elle-même (VWAP-reversion) —
  ce chantier ajoute des garde-fous autour d'une stratégie dont l'edge
  n'a jamais été validé positivement ; il ne prétend pas en créer un.
