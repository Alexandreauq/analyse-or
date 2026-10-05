# Strat 1 : bot Or (scalping), version en production jusqu'au 2026-10-05

Figée ici pour pouvoir y revenir. Tag git : `strat-1`. Le bot est arrêté
(`gold-bot-loop`) à cette date ; la stratégie suivante sera « strat 2 ».

## Paramètres (valeurs du code à cette date)

- **Profil de risque** : 5 (`gold_bot/risk.py`, `RISK_PROFILE_PARAMS`)
  - risque par trade : 10 % du capital (`risk_pct` = 0,10)
  - coupe-circuit journalier : 17,5 % (`threshold_pct` = 0,175)
- **Levier maximal** : 500 (`MAX_LEVERAGE`)
- **Ratio gain/risque minimum** : 1,5 (`SCALP_TAKEPROFIT_RISK_MULTIPLE`)
- **Bougies** : 5 minutes (`CANDLE_INTERVAL_MINUTES`)
- **Tendance** : pivots d'ordre 3 (`SCALP_PIVOT_K`), séparés des figures chartistes (ordre 5)
- **Blackout news** : 15 minutes autour des événements à fort impact (`SCALP_NEWS_BLACKOUT_MINUTES`)
- **Sortie** : VWAP et prise de profit partielle (validation du 29-30/09)

## État au moment de l'arrêt (2026-10-05)

- Service `gold-bot-loop` : arrêté. Service `gold-bot-api` : actif (tableau de bord).
- Mode réel (`dry_run` = False), kill switch désactivé.
- Positions ouvertes : non vérifiées depuis le serveur (l'API MetaTrader renvoyait des erreurs 500).

## Pour revenir à cette stratégie

`git checkout strat-1` (lecture seule), ou restaurer les valeurs ci-dessus.
