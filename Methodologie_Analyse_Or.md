# Méthodologie d'Analyse Fondamentale et de Détection de Points d'Entrée — Or (Métal / ETF)

**Version 1 — Phase 1 du projet : Or physique / GLD**

---

## 1. Objectif et périmètre

Construire un cadre reproductible qui :
1. Évalue en continu l'état fondamental (macro) du marché de l'or.
2. Croise ce diagnostic avec un déclencheur technique pour identifier des **points d'entrée**.
3. Intègre l'actualité et les annonces de la Fed comme inputs structurants (pas de simple veille passive : chaque événement doit modifier un score).
4. Reste extensible : la même architecture (schéma de scoring + overlay technique + calendrier macro) sera réutilisée pour les minières aurifères, puis pour d'autres indices.

L'or n'a pas de bilan ni de résultat net : "l'analyse fondamentale" ici = **analyse macro-fondamentale**, c'est-à-dire l'étude des variables qui déterminent l'offre/demande réelle et l'opportunité de détention de l'or (coût d'opportunité, monnaie, risque).

---

## 2. Les moteurs fondamentaux du cours de l'or

| Facteur | Logique économique | Sens de la corrélation |
|---|---|---|
| **Taux réels US (10 ans, TIPS)** | L'or ne verse pas de coupon ; son coût d'opportunité augmente avec les taux réels | Taux réels ↓ → Or ↑ (moteur historiquement le plus puissant) |
| **Dollar (DXY)** | L'or est coté en USD ; un dollar faible le rend moins cher pour les autres devises | DXY ↓ → Or ↑ |
| **Anticipations d'inflation (breakevens 5-10 ans)** | L'or est perçu comme réserve de valeur | Anticipations ↑ → Or ↑ (mais seulement si taux réels ne montent pas plus vite) |
| **Politique monétaire Fed (taux directeurs, forward guidance)** | Détermine la trajectoire des taux réels | Cycle d'assouplissement anticipé → Or ↑ |
| **Achats des banques centrales** | Demande structurelle, indépendante du prix | Achats nets ↑ (rapports trimestriels World Gold Council) → soutien de fond |
| **Flux dans les ETF or (GLD, IAU)** | Proxy de la demande d'investissement occidentale | Entrées nettes → confirmation de tendance |
| **Positionnement spéculatif (CFTC COT, futures Comex)** | Indicateur de surachat/survente du marché | Positionnement net extrême → risque de retournement technique |
| **Risque géopolitique / aversion au risque** | Valeur refuge | Tensions ↑ → Or ↑ (effet souvent transitoire, à distinguer du fond) |
| **Demande physique (Inde, Chine, bijouterie)** | Saisonnalité (Diwali, Nouvel An chinois) | Effet secondaire, utile pour affiner le timing |

---

## 3. Grille de scoring fondamental

Un score composite sur 100, recalculé à chaque publication de donnée ou événement Fed.

| Composante | Poids | Méthode de notation |
|---|---|---|
| Taux réels US 10 ans (niveau + variation 4 semaines) | 30% | Score -10 à +10 selon niveau absolu et momentum |
| Dollar (DXY, variation 4 semaines) | 15% | Score -10 à +10 selon momentum |
| Anticipations d'inflation vs taux nominaux | 15% | Écart réel implicite |
| Ton de la Fed (dernier FOMC + discours) | 15% | Classification qualitative : hawkish / neutre / dovish → -10/0/+10 |
| Flux ETF (GLD+IAU, variation hebdo des encours) | 10% | Entrées/sorties nettes normalisées |
| Positionnement spéculatif CFTC (percentile 3 ans) | 10% | Extrême haut = signal de prudence, pas d'achat |
| Achats banques centrales (dernier trimestre WGC) | 5% | Tendance vs historique |

**Lecture du score composite :**
- **> +50** : contexte fondamental très favorable
- **+15 à +50** : favorable, en attente d'un déclencheur technique
- **-15 à +15** : neutre, pas d'action
- **< -15** : défavorable, pas d'entrée même sur signal technique

---

## 4. Calendrier macro à surveiller (déclencheurs d'actualisation du score)

- **Réunions FOMC** (8/an) : décision de taux, *dot plot* (mars/juin/sept/déc), *Summary of Economic Projections*
- **Discours des membres du FOMC**, en particulier le Président (Jackson Hole, auditions Congrès)
- **Minutes du FOMC** (3 semaines après chaque réunion)
- **CPI et Core CPI** (mensuel)
- **PCE et Core PCE** (mensuel — indicateur préféré de la Fed)
- **Nonfarm Payrolls + taux de chômage** (1er vendredi du mois)
- **Rapport CFTC Commitment of Traders** (hebdomadaire, vendredi)
- **Rapports trimestriels World Gold Council** (Gold Demand Trends)
- **Flux ETF** (données quotidiennes disponibles via les émetteurs)

Chaque publication doit déclencher une règle : *quel sous-score du tableau 3 est mis à jour, et de combien*.

---

## 5. Overlay technique — le déclencheur du point d'entrée

Le score fondamental dit **si** on veut être acheteur ; l'overlay technique dit **quand**.

Conditions combinées pour un signal d'entrée (à affiner ensemble) :
1. Score fondamental composite **> +15**
2. Prix proche d'un support majeur (moyenne mobile 200j, ou niveau de retracement Fibonacci d'un mouvement précédent) **ou** rupture haussière confirmée d'une résistance
3. Absence de positionnement spéculatif extrême (percentile CFTC < 85%) — évite d'acheter un marché déjà "plein"
4. Pas d'événement Fed majeur dans les 48h suivantes (évite d'entrer juste avant un catalyseur binaire)

Un signal de **sortie/prudence** est symétrique : score composite qui bascule sous +15, ou positionnement spéculatif en zone extrême avec divergence baissière.

---

## 6. Logique d'alerte

- **Alerte "watch"** : score composite franchit +15 à la hausse → surveillance active du déclencheur technique
- **Alerte "entrée"** : les 4 conditions de la section 5 sont réunies simultanément
- **Alerte "risque"** : score composite chute de plus de 20 points en moins de 5 jours (repricing rapide de la Fed) ou positionnement CFTC atteint un extrême historique
- Chaque alerte doit citer **les facteurs qui l'ont déclenchée**, pas seulement un signal binaire — pour que tu gardes la main sur la décision finale

---

## 7. Sources de données envisageables

| Donnée | Source gratuite | Source payante (si accès BNP) |
|---|---|---|
| Taux réels US, breakevens | FRED (Réserve fédérale de St. Louis) | Bloomberg (série TIPS) |
| DXY | Yahoo Finance, Investing.com | Bloomberg/Refinitiv |
| Calendrier Fed / discours | federalreserve.gov, Investing.com Economic Calendar | Bloomberg Economic Calendar |
| CPI/PCE/NFP | bls.gov, bea.gov | Refinitiv Eikon |
| CFTC COT | cftc.gov (gratuit, hebdo) | — |
| Flux ETF | spdrgoldshares.com (GLD), ishares.com (IAU) | Bloomberg (GLD US Equity) |
| Achats banques centrales | gold.org (World Gold Council) | — |
| Actualité générale / news flow | APIs news gratuites (NewsAPI, GDELT) ou RSS Reuters/Bloomberg | Bloomberg Terminal News, Refinitiv News |
| Prix spot or | Yahoo Finance, TradingEconomics (gratuit avec limites) | Bloomberg/Refinitiv temps réel |

Comme tu n'as pas de préférence, je recommande de démarrer avec les sources gratuites (FRED, CFTC, WGC, Yahoo Finance) pour prototyper vite, puis d'évaluer si l'accès pro (Bloomberg/Refinitiv via BNP) apporte un vrai gain de fiabilité/latence avant de le brancher.

---

## 8. Prochaine étape (phase code)

Une fois cette méthodologie validée avec toi, l'architecture technique pourra suivre ce schéma :
1. **Module de collecte** : scripts de récupération périodique des données ci-dessus
2. **Moteur de scoring** : implémentation de la grille de la section 3
3. **Module technique** : calcul des niveaux (moyennes mobiles, supports/résistances)
4. **Moteur de règles** : logique d'alerte de la section 6
5. **Interface** : tableau de bord (score composite, historique, alertes) — à définir ensemble (app web, notification, chatbot)

---

## 9. Roadmap d'extension

- **Phase 1 (actuelle)** : or métal / GLD
- **Phase 2** : minières aurifères (GDX et composantes) — nécessitera d'ajouter un vrai volet fondamental d'entreprise (coûts de production AISC, réserves, levier opérationnel au cours de l'or)
- **Phase 3** : généralisation à d'autres indices boursiers avec un schéma de scoring fondamental adapté par secteur

---

*Document de travail — à faire évoluer avec toi avant la phase de développement.*
