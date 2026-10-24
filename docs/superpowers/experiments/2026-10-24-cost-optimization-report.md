# 2026-10-24 — Optimisation de cout du backtest (score_point_in_temps)

## Contexte

`score_point_in_temps` (`analyste_backtest.py`) appelait `actus_source` (GDELT +
une classification Haiku par article) pour **chaque** tentative du sampler
(`genere_signaux_historiques`), y compris les ~98% rejetees au final (taux
"entree" reel mesure dans `docs/indices.json` : ~1.8%). Sur un echantillonnage
de jusqu'a 2000 tentatives, l'essentiel du cout Haiku et du temps d'horloge
(throttle GDELT a 5s/appel) etait depense sur des tentatives jetees.

## Analyse

`genere_signaux_historiques` ne garde un resultat que si
`indices_score.compute_company_alerts` renvoie une alerte "entree", dont la
condition reelle (`indices_score.py:4996`, avec `score_recalibrated=False`) est :

```
score_favorable AND near_entry AND stage_label != "Déclin"
```

C'est un ET logique. `near_entry` (`indices_score.py:4980-4984`) et
`stage_label` ne dependent **jamais** des actus — uniquement du prix/volume et
de `entry_price` (via `estimate_valuation_targets`, qui ne lit jamais `actus`).
Donc si l'un des deux est faux, le signal est deja rejete quel que soit le
facteur actualite (10% du score composite) — sauter le fetch GDELT+Haiku dans
ce cas ne peut jamais changer l'acceptation/rejet d'un signal.

## Changement applique

Dans `score_point_in_temps` (`analyste_backtest.py`) :

1. `valuation_targets` (et tout ce qu'il requiert : `market_cap`, `wacc`,
   `cost_of_capital`, `cost_of_equity`, `cost_of_equity_for_roe`) est
   desormais calcule **avant** l'appel a `actus_source`, au lieu d'apres
   `compute_composite` — pure permutation, ces valeurs ne dependent jamais des
   actus.
2. Avant l'appel a `actus_source`, un pre-check bon marche (`pres_de_lentree`,
   reutilisant directement `indices_score.NEAR_ENTRY_PCT`, pas de valeur
   codee en dur) reproduit exactement la condition `near_entry` de
   `compute_company_alerts`. Si `pres_de_lentree` est faux OU
   `stage_label == "Déclin"`, `actus_source` n'est pas appele et
   `actus = []` est utilise directement (chemin deja gere partout ailleurs
   dans la fonction : `_score_actualite_recente_point_in_temps([])` renvoie
   deja un `FactorResult` neutre, identique au cas "aucune actu trouvee").
3. L'ancienne ligne dupliquee `valuation_targets = indices_score.
   estimate_valuation_targets(...)` (apres `compute_composite`) a ete
   supprimee — calculee une seule fois maintenant, reutilisee par le dict de
   retour (`fair_value`/`entry_price`/`exit_price`).

Aucun changement a `indices_score.py`, `assistant_ia/data_sources.py`,
`assistant_ia/news.py`, au throttle GDELT, ni a la logique de
`genere_signaux_historiques`.

## Tests (TDD)

Trois nouveaux tests dans `tests/test_analyste_backtest.py`, tous construits
sur une fabrique commune `_fixture_score_point_in_temps_base` qui fixe
`entry_price` via un `estimate_valuation_targets` factice (plutot que de
reconstruire un DCF/actif net complet) et `stage_label` via un
`classify_weinstein_stage` factice :

- `test_score_point_in_temps_skips_news_fetch_when_price_far_from_entry` —
  prix courant nettement au-dessus du repere d'entree (`near_entry` faux) :
  `actus_source` jamais appele, `actus == []` renvoye.
- `test_score_point_in_temps_fetches_news_when_near_entry_and_not_declin` —
  prix a 1% sous le repere (`near_entry` vrai), stage "Achat" :
  `actus_source` appele exactement une fois.
- `test_score_point_in_temps_skips_news_fetch_when_stage_is_declin` —
  `near_entry` serait vrai au seul critere prix, mais `stage_label ==
  "Déclin"` : `actus_source` jamais appele.

Verification red/green faite manuellement avant l'implementation (2 des 3
tests echouaient avant le changement, comme attendu).

Tous les tests `score_point_in_temps` preexistants ont ete relus : aucun
n'assertait un nombre d'appels a `actus_source` ni ne dependait du facteur
actualite reel (ils passent tous un `actus_source` factice renvoyant `[]`,
compatible avec les deux chemins). Aucune fixture n'a du etre modifiee.

Les tests de `genere_signaux_historiques` (Tache 5) mockent entierement
`score_point_in_temps` via `score_source` — non affectes par construction.

## Resultats

- `pytest tests/test_analyste_backtest.py tests/test_analyste_replay.py -v` :
  **39 passed**.
- `pytest tests/ -v` (suite complete) : **1640 passed**, 1 warning (sans
  rapport avec ce changement — deprecation `anyio`/`starlette`).

## Limite reconnue

Cette optimisation agit uniquement sur le fetch d'actus ; les appels
fondamentaux/yfinance (necessaires pour connaitre `entry_price` et donc
`near_entry` lui-meme) restent faits a chaque tentative, par construction
(demande explicite — hors perimetre de cette tache).
