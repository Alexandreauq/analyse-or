# Bot Or vs FTMO — résultats

RECHERCHE / EXPLORATOIRE — PAS DU CODE DE PRODUCTION.

## Question

Le moteur VWAP+EMA200+EMA50 du bot Or (`gold_bot/vwap_reversion.py`), dont
l'edge documenté dépend fortement de quelques trades exceptionnels,
pourrait-il passer une évaluation FTMO ? Si oui à quel réglage de risque,
si non pourquoi structurellement.

## Méthodologie

1. **Règles FTMO** (`ftmo_simulator.py`) : vérifiées via WebFetch sur la
   page officielle `ftmo.com/en/trading-objectives/` le 2026-10-26 (pas de
   source tierce). 2-Step retenu (voie testée ici) : perte quotidienne max
   5% de l'ISC (reset chaque jour à 00:00 CET sur le solde de la veille),
   perte globale max 10% statique, objectif 10% (Challenge) puis 5%
   (Verification), minimum 4 jours de trading (contrainte de temps, non
   simulée — pas un risque), **aucune règle de consistance/best-day**
   (confirmée officielle — contrairement au 1-Step, écarté pour cette
   raison : son edge concentré sur quelques trades y serait disqualifié
   même en cas de succès numérique).

2. **Reconstruction de la liste de trades réelle**
   (`backtest_vwap_ftmo.py`) : 8,5 ans de bougies XAU/USD 5min réelles via
   MetaApi (577 635 bougies, 2018-04 → 2026-10, cache local hors-repo
   `data/xauusd_5min_raw.json`, gitignored). Réutilise `gold_bot.bot.
   decide_and_act_vwap` **sans réimplémentation**, appelé toutes les 15min
   sur une fenêtre glissante de 1000 bougies 5min (= `CANDLES_FETCH_LIMIT`
   de `gold_bot/loop.py`, la fenêtre réelle de production — l'EMA200/
   EMA50 est donc re-seedée à chaque appel, jamais une EMA continue sur
   8,5 ans, exactement comme en prod). Exécution des stops/take-profits
   scannée bougie par bougie (5min), indépendamment du cycle 15min, comme
   le ferait le broker réel.

   **Bug trouvé et corrigé en cours de route** : un vrai gap de prix entre
   deux bougies 5min (trade du 2022-09-28, jour du krach obligataire
   britannique — volatilité réellement élevée ce jour-là, pas un
   artefact) peut sauter par-dessus le niveau de stop sans qu'aucune des
   deux bougies ne le "touche" dans son propre `[low, high]`. Sans la
   détection du gap (comparant aussi l'`open` de la bougie), une position
   restait ouverte jusqu'à la clôture de session à -6,36R au lieu de
   s'arrêter à ~-1R — aurait faussé tout le test vers un risque de perte
   bien plus grave qu'en réalité. Corrigé : remplissage au prix d'`open`
   (pire que le niveau nominal du stop, convention réaliste de glissement
   sur gap) quand le gap a déjà dépassé le niveau.

   Résultat : **683 trades** (jambes partielle+runner comptées séparément,
   voir addendum prise-de-profit-partielle du 2026-09-30 — ce nombre
   diffère du "165 trades" de la validation originale, qui précédait cet
   addendum, pas une erreur de reconstruction). Somme des R-multiples :
   +10,7R sur 683 trades ; sans les 5 plus gros : +0,31R (quasiment nul,
   cohérent avec la réserve déjà documentée dans le spec — le moteur
   *actuel*, avec partial-TP et stop suiveur, est moins dépendant des
   outliers que le moteur brut d'origine, mais reste proche de zéro sans
   eux).

3. **Synthèse rapide multi-scénarios** (`ftmo_pass_rate.py`) : le
   dimensionnement par le risque (`risk.compute_position_size`) rend le %
   de variation de balance par trade **invariant au niveau de balance**
   (risked_dollars = risk_pct × balance, donc %PnL = risk_pct × R quel
   que soit le point de départ ou l'ISC) — modulo la quantification au
   pas de lot (0,01) et le plafond de levier, non modélisés dans cette
   passe rapide (voir Limites). Permet de tester **tous les 683 trades
   comme point de départ candidat**, pour chaque niveau de risque, sans
   refaire tourner le moteur à chaque fois.

## Résultats — Challenge (objectif 10%), ISC 10k$ et 100k$ (identiques,
voir Limites sur la quantification)

| risk_pct | Objectif atteint | Perte **quotidienne** | Perte globale | Incomplet |
|---|---|---|---|---|
| 0.5% | 35.1% | 0.0% | 11.6% | 53.3% |
| **1.0%** | **42.6%** | 40.4% | 8.8% | 8.2% |
| 2.0% | 23.4% | 75.1% | 0.3% | 1.2% |
| 5.0% | 29.9% | 66.0% | 3.7% | 0.4% |
| 10.0% | 36.7% | 50.7% | 12.3% | 0.3% |

**Chaîné Challenge → Verification (2-Step complet)** :
- risk_pct=1% : **38.9%** de réussite complète.
- risk_pct=2% : 13.0%.

## Verdict

**Il existe un réglage avec une chance réaliste de passer : risk_pct≈1%,
~39% de réussite complète (Challenge + Verification) sur les 683 points
de départ testés.** Ni "impossible", ni "quasi garanti" — une pièce
légèrement défavorable, pas un rejet structurel absolu comme pour le
1-Step (bloqué par la règle de consistance quel que soit le réglage, vu
la concentration de l'edge sur 5 trades).

**Mais l'obstacle dominant n'est PAS celui identifié au départ.** La
réserve initiale ("l'edge dépend de rares trades exceptionnels, un
krach va casser le compte avant d'arriver") ne se vérifie pas comme
prévu : la perte **globale** (10%, l'accumulation lente) ne casse que
rarement (3.7% à 12.3% selon le risque). Le vrai obstacle est la perte
**quotidienne** (5%) : à risk_pct≥2%, elle casse 2 fois sur 3 ou plus.
Même à 1% (le réglage le moins mauvais), elle reste la cause de 40,4%
des échecs — la volatilité intra-journalière normale de cette stratégie
(pas spécifiquement un trade exceptionnel) suffit à franchir 5% dans une
mauvaise journée bien plus souvent qu'un effondrement progressif sur
plusieurs jours.

**Ce qu'il faudrait changer pour une meilleure chance** (pas juste le
risque, la nature de la stratégie) : réduire l'exposition intra-journée
plutôt que le risque par trade seul — par exemple un coupe-circuit
*journalier propre à FTMO* (distinct du coupe-circuit interne du bot,
seuil 10% UTC, déjà trop large et sur le mauvais fuseau/jour pour
protéger contre la règle FTMO en CET) calé explicitement sur 5% CET, ou
limiter à un seul trade par jour civil CET quel que soit le signal.
Aucun des deux n'est implémenté ni testé ici — piste pour un chantier
séparé, pas une recommandation d'intégration immédiate.

## Limites explicites

- **Quantification au pas de lot (0,01) et plafond de levier** non
  modélisés dans la synthèse rapide (`ftmo_pass_rate.py`) — les
  résultats à 10k$ et 100k$ sont par construction identiques dans cette
  passe, ce qui n'est qu'une approximation : à 10k$ et faible risk_pct,
  la taille réelle arrondie pourrait dévier un peu plus du risque
  théorique qu'à 100k$. Non vérifié empiriquement par manque de temps
  (aurait demandé une passe complète supplémentaire avec `balance`
  réellement compoundée via `decide_and_act_vwap`, pas la synthèse
  rapide) — à faire avant toute décision réelle d'y risquer de l'argent.
- **r_pire ne suit que le pire point intra-trade, jamais le meilleur** :
  l'objectif de profit n'est vérifié qu'à la clôture de chaque trade, pas
  sur un pic intra-trade qui aurait pu atteindre l'objectif puis
  retomber avant la clôture — convention volontairement conservatrice
  (sous-estime légèrement le taux de réussite, jamais ne le surestime).
- **Remplissage sur gap au prix d'`open`**, pas au tick exact où le
  marché a réellement franchi le niveau — approximation standard de
  backtest sur bougies OHLC (pas de données tick-by-tick), documentée
  comme telle.
- **Spread/slippage réels** potentiellement différents chez le broker
  FTMO réel vs les données de marché MetaApi utilisées ici.
- **4 jours de trading minimum** (contrainte de temps FTMO) non simulée
  — jamais un facteur de risque, mais à vérifier séparément avant de
  s'engager.
- Le coupe-circuit interne du bot (`shared_risk.CircuitBreaker`,
  seuil 10%, jour **UTC**) est actif dans la reconstruction de trades
  mais ne protège quasiment jamais contre la règle FTMO (5% **CET**) --
  les deux mécanismes ne sont pas alignés, voir section Verdict.

## Fichiers

- `ftmo_simulator.py` — règles FTMO 2-Step (daté, vérifié officiellement).
- `backtest_vwap_ftmo.py` — reconstruction des 683 trades réels (réutilise
  `gold_bot.bot.decide_and_act_vwap`), écrit `data/trades_reference.json`.
- `ftmo_pass_rate.py` — synthèse rapide multi-scénarios à partir des trades.
- `data/` (gitignored) — cache de bougies + trades de référence régénérables.
