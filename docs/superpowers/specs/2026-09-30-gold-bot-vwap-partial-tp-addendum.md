# Gold Bot — Addendum : prise de profit partielle (design)

Complète les specs précédentes du moteur VWAP+EMA200+stop suiveur.

## Contexte

Poursuite du "mode machine learning" walk-forward (6 fenêtres roulantes
indépendantes). Idée testée : fermer une fraction de la position à une
cible modérée (en multiples de R) pour sécuriser du gain, laisser le
reste courir sans plafond via le stop suiveur existant — réduit le
"give-back" (perte du gain flottant avant que le stop suiveur ne
rattrape) sans plafonner la queue qui porte l'edge.

**Résultat walk-forward** : `70% de la position fermée à 2R, 30%
runner` choisie par l'entraînement sur 5 fenêtres sur 6 (la 6e a choisi
"aucune prise partielle", résultat identique avec ou sans dans ce cas
précis). **6/6 fenêtres de test nettes positives** — première fois
qu'aucune n'est négative. Comparaison directe (mêmes fenêtres, avec vs
sans) :
- 2021 : -0,8% (DD 22,3%) → **+14,6%** (DD 7,0%)
- 2022 : +153,3% (DD 8,3%) → +85,9% (DD 1,9%) — rendement plus bas mais
  DD bien plus faible
- 2023 : -0,6% (DD 13,4%) → **+6,7%** (DD 3,7%)
- 2024 : +78,7% (DD 5,6%) → +19,8% (DD 1,7%)
- 2025 : +221,0% (DD 25,8%) → +89,3% (DD 8,4%)
- 2026 : +115,7% (DD 36,1%) → identique (cette fenêtre a choisi "aucune
  prise partielle")

## Mécanisme

À l'entrée, si `PARTIAL_TP_PCT > 0` : la taille totale calculée par le
risque est divisée en deux jambes distinctes, ouvertes comme **deux
positions broker séparées** (pas une seule position avec un ordre
partiel — MetaApi n'a pas de notion de clôture partielle programmée à
l'avance, donc on ouvre directement deux positions) :
- **Jambe partielle** (`PARTIAL_TP_PCT` du volume total, 70% par
  défaut) : stop = le stop initial (VWAP ∓ STOP_SIGMA×σ), take-profit
  fixe = entrée ± `PARTIAL_TP_R` × distance (2R par défaut). Fermée
  automatiquement côté broker (SL ou TP), le bot n'a jamais besoin d'y
  toucher après l'ouverture.
- **Jambe "runner"** (le reste, 30% par défaut) : même stop initial,
  **pas de take-profit** — gérée exactement comme avant par le stop
  suiveur (`vwap_reversion.compute_trailing_stop`).

Si `PARTIAL_TP_PCT × volume_total` arrondit à 0 (compte trop petit),
aucune jambe partielle n'est ouverte — tout le volume part en jambe
runner (comportement identique à avant cet addendum).

**Distinction des deux jambes lors de la reconciliation** (`open_positions`
renvoyé par le broker) : la jambe partielle a un `takeProfit` non nul,
la jambe runner n'en a jamais. Le stop suiveur ne s'applique **qu'à la
jambe runner** (`takeProfit` absent/nul) ; si seule la jambe partielle
reste ouverte (la jambe runner a déjà été stoppée), aucune action —
la jambe partielle se gère toute seule côté broker.

## Ce que fait ce plan

1. `gold_bot/vwap_reversion.py` — nouvelles constantes
   `PARTIAL_TP_PCT = 0.7`, `PARTIAL_TP_R = 2.0`.
2. `gold_bot/bot.py::decide_and_act_vwap` :
   - Entrée : calcule `partial_volume`/`runner_volume`, renvoie une ou
     deux étapes `ouverture_simulee` selon que la jambe partielle est
     ouvrable ou non (volume arrondi à 0 → une seule étape, comme
     avant).
   - Position(s) ouverte(s) : sépare `open_positions` (déjà filtrées sur
     le symbole) en jambe(s) runner (`takeProfit` absent/nul) et
     partielle(s) (`takeProfit` non nul). Le stop suiveur ne s'applique
     qu'à la jambe runner si elle existe encore ; si seule la partielle
     reste, aucune action.
3. Pas de nouvelle fonction broker nécessaire — réutilise
   `place_market_order` (déjà appelable avec ou sans `take_profit`) deux
   fois au lieu d'une.

## Non-objectifs

- Pas de prise partielle à plusieurs niveaux (une seule cible, pas de
  cascade 1R/2R/3R) — pas testé, YAGNI tant que ce n'est pas validé.
- Pas de changement au calcul du stop suiveur lui-même — s'applique à la
  jambe runner exactement comme avant.
