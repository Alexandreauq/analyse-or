# Gold Bot — Moteur VWAP + Filtre EMA200 + Stop Suiveur EMA50 (design)

## Contexte

Nuit du 2026-09-29 : 10+ approches de scalping testées sur XAU/USD 5min
réel (MetaApi, ~8,5 ans, position sizing réel, spread réel) — voir
mémoire `project_gold_bot_scalping_validation_2026_09_29`. Aucune n'a
d'edge robuste. La moins mauvaise est le **retour-à-la-VWAP filtré par
régime (EMA200) avec sortie par stop suiveur EMA50** : +16,7% à +67,0%
sur 8,5 ans selon le risque, 165 trades, 51% de réussite.

**Réserve explicite, non résolue** : sur les 165 trades, les 3 plus gros
(dont le krach COVID de mars 2020) dépassent à eux seuls le gain total
— sans eux, les 162 autres trades sont nets négatifs (~-10 000$ à 5% de
risque). Ce n'est pas un edge démontré statistiquement, c'est la
configuration la moins mauvaise trouvée cette nuit. Décision de
l'utilisateur (2026-09-30) : la déployer quand même comme moteur de
scalping du bot, à la place du mode swing macro (qui reste dans le repo,
débranché).

## Mécanisme validé (à reproduire à l'identique en production)

- Bougies 5min → rééchantillonnées en bougies 15min (open/high/low/close,
  tick_volume sommé).
- VWAP intrasession (réinitialisée chaque jour calendaire UTC) : prix
  typique (high+low+close)/3, pondéré par tick_volume, avec bandes
  d'écart-type pondérées par le volume depuis le début de la session.
- EMA200 et EMA50 sur les clôtures 15min, **continues** (jamais
  réinitialisées, contrairement à la VWAP).
- **Entrée** (uniquement si aucune position ouverte sur le symbole) :
  - régime haussier (close > EMA200) et close ≤ VWAP − 2σ → achat
  - régime baissier (close < EMA200) et close ≥ VWAP + 2σ → vente
  - garde : au moins `MIN_BARS_INTO_SESSION` (12, soit 3h) bougies 15min
    depuis le début de la session (laisse l'écart-type se stabiliser —
    bug trouvé et corrigé cette nuit : un écart-type quasi nul en tout
    début de session faisait exploser la taille de position calculée
    par le risque)
  - garde : distance entrée→stop ≥ `MIN_DISTANCE_PCT` (0,2%) du prix
  - pas d'entrée après `SESSION_END_HOUR_UTC` (22h UTC)
- **Stop initial** (posé côté broker à l'ouverture, comme SL réel) :
  VWAP ∓ 3σ (achat : VWAP − 3σ ; vente : VWAP + 3σ).
- **Pas de take-profit fixe** — la position n'a aucune cible ; elle
  n'est fermée que par (a) le stop initial touché (géré par le SL posé
  côté broker), (b) la bougie 15min clôture du mauvais côté de l'EMA50
  (le bot envoie alors une clôture), ou (c) l'heure de fin de session
  (22h UTC, le bot clôture aussi).

## Ce que fait ce plan

1. `gold_bot/broker.py::place_market_order` — `take_profit` devient
   optionnel (`None` par défaut) ; le champ `"takeProfit"` n'est envoyé
   à MetaApi que s'il n'est pas `None`. Changement rétrocompatible
   (tous les appelants existants passent déjà une valeur explicite).
2. `gold_bot/confluence.py::fetch_gold_candles` — gagne un paramètre
   `limit: int = 100` (défaut inchangé pour les appelants existants).
   Le nouveau moteur a besoin de bien plus d'historique par cycle que
   l'ancien (EMA200 sur des bougies 15min ⇒ au moins 200×15 = 3000
   minutes ⇒ au moins 600 bougies 5min) — `loop.run_cycle` appellera
   avec `limit=1000` (plafond MetaApi par appel, une seule requête,
   ~3,5 jours de bougies 5min, large marge au-dessus du besoin réel y
   compris pour absorber un week-end de marché fermé).
3. Nouveau module `gold_bot/vwap_reversion.py` : rééchantillonnage,
   calcul des indicateurs, `latest_state()` — expose les valeurs brutes
   (vwap, écart-type, EMA200, EMA50, heure UTC, bougies depuis le début
   de session) pour la dernière bougie 15min disponible.
4. Nouvelle fonction `gold_bot/bot.py::decide_and_act_vwap` — orchestration
   (ouverture/gestion/fermeture), même contrat de retour que
   `decide_and_act`/`decide_and_act_swing` (steps réutilisables tels
   quels par `loop.execute_steps`). Contrairement au mode swing (long
   uniquement), ce moteur est **bidirectionnel** (achat et vente).
5. `gold_bot/loop.py::run_cycle` — appelle `decide_and_act_vwap` à la
   place de `decide_and_act_swing` ; retire le fetch du payload macro
   (`macro_signal.fetch_macro_payload`, plus utilisé) ; augmente la
   taille de la fenêtre de bougies récupérée (`limit=1000`).

## Non-objectifs

- Pas de suppression du mode swing macro (`macro_signal.py`,
  `decide_and_act_swing`) ni de l'ancien moteur chartiste — débranchés,
  conservés.
- Pas de nouveau profil de risque — les 5 profils existants et le
  coupe-circuit journalier sont réutilisés tels quels.
- Pas de mécanisme de modification de stop-loss côté broker en cours de
  position : l'exit EMA50/fin-de-session est géré par le bot (clôture
  au marché), le stop initial posé à l'ouverture ne bouge jamais — fidèle
  au mécanisme backtesté (qui vérifiait juste `low<=stop`/`high>=stop`
  sur le stop FIXE posé à l'entrée, jamais un stop qui se déplace).
- Pas de déploiement réel sur le VPS — code livré, testé, mergé ; la
  bascule en production (arrêt du service actuel, redéploiement, levée
  du `kill_switch`) reste une étape manuelle séparée.

## Tests

- `tests/gold_bot/test_vwap_reversion.py` (nouveau) : `resample_15min`,
  `compute_indicators`, `latest_state` (cas nominal, historique
  insuffisant, écart-type nul/dégénéré).
- `tests/gold_bot/test_bot.py` : cas ajoutés pour `decide_and_act_vwap`
  (entrée achat/vente, maintien, sortie EMA50 achat/vente, sortie fin
  de session, distance sous le minimum, coupe-circuit, plusieurs
  positions, type de position inattendu, historique insuffisant).
- `tests/gold_bot/test_broker.py` : `place_market_order` sans
  `take_profit` n'envoie pas la clé `"takeProfit"`.
- `tests/gold_bot/test_loop.py` : tous les mocks `decide_and_act_swing`
  → `decide_and_act_vwap`, retrait des mocks `macro_signal.fetch_macro_payload`
  (plus appelé).
