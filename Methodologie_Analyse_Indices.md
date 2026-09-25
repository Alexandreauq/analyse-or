# Méthodologie : scoring fondamental (onglet "Indices")

## Objectif

Produire un rating fondamental par entreprise, basé sur l'analyse des
comptes publiés des dernières années. Couvre aujourd'hui **710 sociétés
réparties sur 10 indices** (CAC 40, DAX, Nasdaq 100, Dow Jones, FTSE
100, SMI, IBEX 35, FTSE MIB, Nikkei 225, Hang Seng) — la phase pilote
initiale (5 sociétés du CAC 40) a été étendue à l'ensemble de ces
indices.

Les concepts et seuils ci-dessous s'appuient sur la méthodologie de
référence française en finance d'entreprise (Vernimmen — diagnostic
financier, analyse de la rentabilité comptable, analyse du financement,
coût du capital, pratique de l'évaluation), synthétisée et adaptée ici
pour un calcul automatisé.

## Profils de notation

Trois grilles distinctes, selon le type de société :

| Profil | Sociétés concernées | Facteurs "Rentabilité"/"Valorisation" |
|---|---|---|
| **Standard** | La grande majorité (non financière, non trust) | ROCE vs coût du capital ; EV/EBITDA et P/E |
| **Financier** | Banques, assurances (`FINANCIAL_SECTOR_TICKERS`) — pas de notion d'EBITDA/EBIT exploitable chez yfinance pour ces sociétés | ROE vs coût des **capitaux propres** (pas le WACC — le ROE est un rendement pour les seuls actionnaires) ; P/E et P/B |
| **Trust** | 9 trusts d'investissement cotés à Londres (`TRUST_TICKERS`) — même trou de données que les financières | Même grille que le profil financier ; valorisation par décote/prime sur P/B (proxy NAV) |

Les trois profils partagent la même échelle -100/+100 et la même
pondération par facteur, mais avec des indicateurs adaptés à ce que
chaque type de société publie réellement.

## Profils de risque sectoriel

Un même niveau d'endettement n'a pas la même signification selon que
l'entreprise opère dans un secteur à flux de trésorerie stables et
prévisibles ou dans un secteur cyclique. Trois profils, dérivés du champ
`sector` de yfinance :

| Profil | Secteurs yfinance | Logique |
|---|---|---|
| **Défensif** | Utilities, Consumer Defensive, Healthcare, Real Estate | Flux prévisibles → tolérance d'endettement plus élevée |
| **Standard** | Industrials, Communication Services | Profil intermédiaire → seuils Vernimmen de base |
| **Cyclique** | Energy, Basic Materials, Consumer Cyclical, Technology | Flux sensibles à la conjoncture → tolérance d'endettement plus faible |

Les seuils de la section "Structure financière / solvabilité"
ci-dessous sont exprimés pour le profil **Standard** et ajustés
(multiplicateur ×1,3 Défensif, ×0,7 Cyclique) pour les deux autres.

## Grille de scoring

Score composite pondéré sur l'échelle **-100/+100** (même échelle que
l'or), chaque facteur noté **-10/+10**.

### 1. Rentabilité / création de valeur — poids 24%

- **Profil standard : ROCE** (rentabilité économique) = Résultat
  d'exploitation × (1 − taux d'IS apparent) / Actif économique, comparé
  au **WACC** de l'entreprise.
- **Profils financier/trust : ROE** (résultat net / capitaux propres),
  comparé au **coût des capitaux propres** (Ke), pas au WACC — le ROE
  mesure un rendement pour les seuls actionnaires, le comparer au WACC
  (qui mélange dette et capitaux propres, structurellement plus bas)
  surestimerait la création de valeur.
- Score = écart (spread) à l'échelle du coût du capital, plafonné à
  ±10 pour un écart de ±15 points ou plus (`ROCE_SPREAD_SCALE`),
  calibré sur la distribution réelle observée en production.

> **Coût du capital réel par entreprise (WACC).** `Re = Rf_devise + β ×
> prime_marché + prime_taille(capitalisation)` (CAPM), `WACC =
> capitalisation/(capitalisation+dette) × Re + dette/(capitalisation+dette)
> × Rd_après_IS`. `Rf_devise` = dernier taux long terme publié (FRED)
> **dans la devise de cotation de l'entreprise** — OAT 10 ans pour
> l'EUR, Treasury 10 ans pour l'USD (Nasdaq/Dow, et par extension le
> HKD via le peg HKD/USD, Hong Kong n'ayant pas de série FRED propre),
> Gilt 10 ans pour le GBP, emprunt confédéral pour le CHF, JGB pour le
> JPY. `β` = bêta yfinance brut (endetté). Prime de risque marché fixe
> à 5,0%. Prime de taille par bandes de capitalisation (convertie en
> USD avant comparaison aux seuils). `Rd` = coût de la dette **propre à
> chaque entreprise** quand disponible (`|Interest Expense| / Total
> Debt`, borné entre 1,5% et 12% pour éviter les distorsions de
> financement captif type constructeur auto ou les données ponctuelles
> aberrantes), sinon repli sur un proxy fixe de 3,0%. Repli sur un coût
> du capital fixe de 8% pour l'entreprise entière si une donnée
> essentielle manque (bêta, taux sans risque...).

> **Fenêtre de calcul.** Le ROCE/ROE ne porte que sur le dernier
> exercice publié, sans vérification de tendance sur plusieurs années.

### 2. Structure financière / solvabilité — poids 20%

Seuils de base (profil Standard), ajustés par profil sectoriel :

| Ratio | Confortable | Risqué |
|---|---|---|
| Dette nette / EBITDA | < 3 | > 5,5 |
| Couverture des intérêts (EBIT / frais financiers) | > 3 | < 3 |

La couverture des intérêts utilise le même coût de la dette implicite
que le WACC (repli sur le proxy 3% si non calculable).

### 3. Croissance — poids 16%

- CAGR chiffre d'affaires et CAGR EBITDA (profil standard) ou résultat
  net (profils financier/trust), **lissés** : moyenne des 2 exercices
  les plus récents vs moyenne des 2 plus anciens (repli sur un calcul
  point à point si moins de 4 exercices disponibles), pour réduire la
  sensibilité à une année isolée atypique. La fenêtre effective varie
  selon le nombre d'exercices réellement publiés par yfinance — pas un
  nombre d'années fixe.
- Score favorable si croissance positive et cohérente entre les deux
  indicateurs (une croissance du CA sans croissance de l'EBITDA signale
  une dégradation de la rentabilité).

### 4. Génération de cash — poids 12%

- **FCF** = Flux de trésorerie opérationnel (yfinance) − |Capex|.
- **Conversion FCF/EBITDA** (ou OCF/résultat net pour les profils
  financier/trust) : plus ce ratio est élevé, plus la rentabilité
  comptable se traduit réellement en cash.

### 5. Valorisation relative — poids 8%

- Multiples **EV/EBITDA** et **P/E** (P/B pour les trusts) comparés à
  la moyenne des exercices disponibles de l'entreprise elle-même (pas
  de comparaison à des pairs sectoriels). Le multiple "actuel" utilise
  le **cours du jour**, pas le cours de clôture de l'exercice fiscal le
  plus récent (qui peut dater de plusieurs mois) — seule la moyenne
  historique reste sur les cours de chaque exercice passé.
- Lecture prudente : un multiple élevé par rapport à l'historique de
  l'entreprise n'est pénalisé que modérément, et seulement en
  combinaison avec un ralentissement de la croissance.

### 6. Dynamique récente — poids 10%

- **Tendance du cours** : écart entre le cours actuel et sa moyenne
  mobile 200 jours (%).
- **Accélération des résultats** : croissance du chiffre d'affaires du
  dernier trimestre publié vs le même trimestre l'an dernier, comparée
  au CAGR déjà calculé (facteur Croissance).
- Les deux sous-signaux sont mis à l'échelle indépendamment puis
  moyennés ; si l'un est indisponible, le score ne porte que sur
  l'autre ; si aucun n'est disponible, le facteur est neutre.

### 7. Actualité récente — poids 10%

- Moyenne pondérée du sentiment des actualités des 14 derniers jours
  (poids par importance : une actu classée "majeure" pèse jusqu'à 4x
  plus qu'une actu "mineure"), tel que classé par Claude au moment de
  la génération du résumé de chaque actu.
- Neutre (0,0) si aucune actualité récente exploitable — délibéré,
  jamais de biais optimiste/pessimiste par défaut sur une donnée
  absente.

## Juste valeur et repères d'entrée/sortie

Combine trois méthodes (DCF, valeur patrimoniale, multiples), pondérées
par profil sectoriel, puis bornées entre **0,15x et 6x le cours
actuel** — filet de sécurité empirique contre une valeur terminale de
DCF dégénérée (WACC très bas) ou une moyenne de multiples faussée par
un exercice aberrant. La croissance terminale du DCF (2% par défaut)
est elle-même plafonnée au taux sans risque de la devise de
l'entreprise quand celui-ci est plus bas (JPY, CHF notamment). La
valeur patrimoniale (capitaux propres / actions, pondérée par
ROE/coût du capital) peut dépasser la valeur comptable brute (jusqu'à
3x) pour les profils financier/trust, où c'est la seule méthode
disponible (DCF et multiples EV/EBITDA n'ont pas de sens pour une
banque). Repères d'entrée/sortie = juste valeur ± marge ajustée par le
bêta, moyennée avec un repère technique (MM200) sauf en phase
Weinstein "Déclin" (voir ci-dessous).

## Score affiché : rang percentile, pas la somme brute des facteurs

Le score affiché (-100/+100) est le **rang percentile** de la société
au sein de son propre pool (standard / financier / trust), recalculé
chaque jour — pas directement la somme pondérée des facteurs. Un score
de +40 signifie "mieux noté que ~70% des sociétés du même profil", pas
une note absolue. Le score brut (somme pondérée des facteurs) est
conservé séparément (`score_raw`) mais pas affiché — voir
`docs/superpowers/specs/2026-09-24-score-recalibration-design.md` pour
le détail complet du mécanisme. Un profil dont le pool est trop petit
pour qu'un classement percentile ait un sens (aujourd'hui : le profil
trust, 9 sociétés) garde son score brut tel quel, faute d'alternative.

- **> +60** : profil fondamental très solide (~top 20%)
- **+20 à +60** : solide
- **-20 à +20** : neutre, autour de la médiane
- **-60 à -20** : fragile
- **< -60** : très fragile

## Phases de marché (Weinstein) et badge qualité (Graham)

Deux enrichissements indépendants du score composite, ajoutés depuis la
version pilote :

- **Phase de marché** (méthode Stan Weinstein) : base / avancée / distribution
  / déclin, dérivée de la pente de la moyenne mobile 30 semaines et du
  volume. En phase "Déclin", le repère technique est exclu du calcul
  d'entrée/sortie, et l'alerte "entree" (voir ci-dessous) ne se
  déclenche jamais. Voir
  `docs/superpowers/specs/2026-09-23-weinstein-stage-analysis-design.md`.
- **Badge défensif Graham** : 6 critères (5 pour les profils
  financier/trust) inspirés de *L'investisseur intelligent* — structure
  financière, stabilité des bénéfices, dividendes, croissance,
  valorisation. Affiché sur la fiche société quand tous les critères
  applicables sont remplis. Voir
  `docs/superpowers/specs/2026-09-23-graham-defensive-investor-design.md`.

## Historique de score & alertes

Chaque entreprise conserve un historique quotidien de son score
composite **brut** (`indices_history.json`, ~2 ans de profondeur par
date calendaire, tickers retirés d'un indice purgés automatiquement),
utilisé pour dériver 3 types d'alertes affichées sur sa page détail —
toutes basées sur le score **brut**, pas le rang percentile (qui
bouge même quand les fondamentaux propres d'une société n'ont pas
changé, puisqu'il dépend aussi des autres sociétés du pool) :

- **Veille** — le score brut vient de franchir un seuil neutre autour
  de 0, avec hystérésis (bande morte de ±5 points) pour éviter qu'un
  score qui oscille près de zéro ne redéclenche l'alerte à chaque
  petit mouvement.
- **Risque, chute rapide** — chute de 20 points ou plus (score brut) en
  moins de 5 jours (mêmes seuils que le volet Or).
- **Entrée, conditions réunies** — score brut favorable et cours actuel
  **au niveau ou en dessous** du repère d'entrée, à moins de 5% d'écart
  (pas au-dessus, même de peu) — sauf en phase Weinstein "Déclin", où
  cette alerte ne se déclenche jamais.

Sans déclencheur, une alerte neutre ("Pas de signal actif") est
affichée. **Un email quotidien récapitulatif est envoyé** (signaux
"entree" nouvellement déclenchés + actualités classées "majeure") —
voir `[[project_indices_alerts]]`. Chaque signal "entree" nouvellement
déclenché ouvre aussi une position de suivi de performance en
paper-trading (`docs/signal_tracking.json`), clôturée automatiquement
sur stop-loss, objectif atteint, délai maximal, ou retrait de la
société de son indice.

## Analyse financière complète (Vernimmen)

Une page dédiée par entreprise (accessible par un bouton sur sa fiche)
propose une analyse financière écrite, structurée selon la synthèse du
diagnostic financier Vernimmen : diagnostic global, structure
financière et solvabilité, rentabilité économique et financière,
analyse de la trésorerie et du free cash-flow, dynamique récente
(dernier trimestre vs tendance), synthèse.

Générée par Claude à partir des comptes annuels et des derniers
trimestres publiés, ainsi que des ratios déjà calculés par ailleurs —
le modèle interprète, il ne recalcule pas ces chiffres.

**Régénérée uniquement quand un nouveau trimestre est publié** (pas
quotidiennement) : le run compare la date du dernier trimestre connu à
celle stockée la veille et ne régénère que si elle a changé, sinon
recopie l'analyse existante.

## Réconciliation de devise

Les comptes d'une société peuvent être publiés dans une devise
différente de sa devise de cotation (ex. AIA Group, 1299.HK, comptes en
USD, cotée en HKD). `financialCurrency` (yfinance) est comparé à la
devise de cotation ; en cas d'écart, les postes monétaires des états
financiers sont convertis au taux du jour avant tout calcul de ratio.
Voir `docs/superpowers/specs/2026-09-24-financial-currency-reconciliation-design.md`.

## Hors périmètre

- Bêta désendetté puis réendetté à la structure financière de chaque
  entreprise (nécessiterait un échantillon de comparables) — le WACC
  utilise le bêta yfinance brut.
- Comparaison à un échantillon de pairs sectoriels pour la valorisation
  (comparaison uniquement à l'historique propre de chaque société).
- Tendance du ROCE/ROE sur plusieurs années (calcul sur le seul dernier
  exercice publié).
