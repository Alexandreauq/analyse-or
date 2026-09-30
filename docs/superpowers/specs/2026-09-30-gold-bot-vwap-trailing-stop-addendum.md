# Gold Bot — Addendum : stop suiveur EMA50 + recalibrage entrée/stop (design)

Complète `docs/superpowers/specs/2026-09-30-gold-bot-vwap-reversion-design.md`.

## Contexte

Après déploiement du moteur VWAP+EMA200+EMA50 initial, un balayage large
(4 largeurs entrée/stop × 10 mécanismes de sortie, 8,5 ans) a d'abord
produit des résultats extrêmes (+607 458%) — **diagnostiqué comme un
artefact de calcul** : un mode "suiveur immédiat" (sans attendre de
profit avant de faire suivre le stop) verrouillait le stop près du seuil
de rentabilité dès la 1ère bougie (l'EMA50, plus lente, est presque
toujours du mauvais côté du prix d'entrée juste après une entrée en
retour-à-la-moyenne), produisant un taux de réussite ~98% artificiel et
une explosion de la taille des positions par composition du solde.

**Correctif méthodologique** : découpage train/test strict — optimisation
des paramètres sur 2018-2023 uniquement, jamais sur 2024-2026, puis
vérification sur cette période jamais vue. Résultat retenu :
`entry_sigma=1.5, stop_sigma=2.5`, sortie par **stop suiveur EMA50 avec
attente d'un seuil de rentabilité** (pas de suivi immédiat) :
- Entraînement (2018-2023) : +312,0% (160 trades, 55,6% de réussite),
  reste à +184,8% sans son meilleur trade.
- Test (2024-2026, jamais vu pendant l'optimisation) : +255,0% (212
  trades, 54,2% de réussite), **positif sur les 3 années individuellement**
  (2024/2025/2026), reste à +162,0% sans son meilleur trade.

Réserve qui persiste : les 10 plus gros trades sur 212 pèsent 224% du
gain net (le trade "moyen" reste légèrement perdant, l'edge vient de
bien capter une poignée de vrais mouvements). Drawdown test : 45,5% —
toujours élevé, pas une stratégie "sûre".

## Changements

1. `gold_bot/vwap_reversion.py` — `ENTRY_SIGMA` 2.0→1.5, `STOP_SIGMA`
   3.0→2.5, nouvelle constante `TRAIL_BUFFER_SIGMA = 0.5` (marge de
   sécurité entre l'EMA50 et le stop suiveur, en multiples de l'écart-type
   VWAP au moment de l'entrée — identique au paramètre validé dans le
   backtest).
2. Nouvelle fonction `vwap_reversion.compute_trailing_stop(candles_5min,
   direction, entry_time_iso, entry_price, current_stop_loss) -> float |
   None` : recalcule R (distance entrée→stop initial) en retrouvant la
   bougie 15min d'ouverture dans l'historique récupéré (les positions de
   ce moteur se ferment toujours dans la même session UTC, donc l'entrée
   est toujours dans la fenêtre de `CANDLES_FETCH_LIMIT` bougies
   récupérées) ; vérifie si le plus haut (achat)/plus bas (vente) atteint
   depuis l'entrée dépasse R ; si oui, calcule le stop candidat (EMA50 ∓
   marge) et ne l'accepte que s'il resserre le stop en faveur du trade
   (`max`/`min` avec le stop actuel — ne recule jamais). Renvoie `None`
   si le seuil n'est pas atteint ou si rien ne change. **Sans état côté
   bot** — tout est recalculé à chaque cycle depuis les positions et
   bougies réelles, même philosophie que le reste de `gold_bot` (jamais
   de confiance en un état local qui pourrait être périmé).
3. `gold_bot/broker.py::modify_position_stop_loss(token, account_id,
   position_id, stop_loss, region=...)` — nouvelle fonction, POST
   `actionType: "POSITION_MODIFY"` (vérifié via la documentation
   officielle MetaApi le 2026-09-30 : `positionId` + `stopLoss`/`takeProfit`
   optionnels).
4. `gold_bot/bot.py::decide_and_act_vwap` — la branche "position déjà
   ouverte" n'utilise plus la clôture sur croisement EMA50 (remplacée par
   le stop suiveur réel, fidèle au mécanisme backtesté où la sortie sur
   clôture EMA50 ne s'applique qu'au mode "stop fixe", jamais au mode
   "suiveur") : soit clôture en fin de session (22h UTC), soit demande de
   modification du stop via `vwap_reversion.compute_trailing_stop`, soit
   aucune action si le stop ne bouge pas.
5. Nouveau type d'étape `"modification_simulee"` (aux côtés de
   `"ouverture_simulee"`/`"clôture_simulee"`) : `{"type":
   "modification_simulee", "position_id": ..., "symbol": ..., "new_stop_loss": ...}`.
   `gold_bot/loop.py::execute_steps` gagne une branche qui appelle
   `broker.modify_position_stop_loss`.

## Non-objectifs

- Pas de nouveau balayage de paramètres avant déploiement — celui-ci a
  déjà la discipline train/test, le prochain tour de validation (si
  besoin) se fera après un temps de fonctionnement réel.
- Le contrôle anti-slippage existant (`loop._entry_price_has_drifted`)
  ne s'applique qu'aux étapes d'ouverture, pas aux modifications de
  stop — une modification de stop suiveur n'a pas de notion de
  "glissement de prix d'entrée" à protéger.
