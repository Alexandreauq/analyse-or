# Figures chartistes (Double Top/Bottom, Tête-Épaule, Triangles) — design

## Contexte et contrainte fondatrice

Le moteur de signal Scalping Or (`docs/scalping.js`) combine aujourd'hui
tendance + support/résistance + indicateurs + chandeliers japonais. Les
figures chartistes (formées sur plusieurs dizaines de bougies : Double
Top/Bottom, Tête-Épaule, Triangles...) faisaient partie du catalogue du
cours mais avaient été explicitement exclues du v1, au même titre que le
Volume et Elliott.

**Contrainte de calendrier, non négociable :** `docs/scalping.js` est
actuellement utilisé en production par la fois l'UI navigateur et un
essai de paper-trading de 7 jours déjà en cours
(`scalping_tracker.js`, se termine le 2026-09-16T17:18Z). Ce chantier
développe sur une branche séparée et **ne merge rien sur `main` avant la
fin de cet essai** — modifier le moteur de signal en cours de semaine
invaliderait les résultats du paper-trading (mélange de comportements
selon le moment où le changement atterrirait).

## Portée

**Dans le périmètre (v1 de ce chantier) — 7 figures :**
- **Retournement :** Double Top (M), Double Bottom (W), Tête-Épaule,
  Tête-Épaule inversée.
- **Continuation :** Triangle ascendant, Triangle descendant, Triangle
  symétrique.

**Hors périmètre, explicitement :**
- Triple Top/Bottom, Saucier, Diamant, Wedge, Tasse avec anse,
  Flags/Pennants, Rectangle — repoussés à une itération future.
- **Volume** : toujours absent (Twelve Data ne fournit pas de volume pour
  XAU/USD — confirmé lors du chantier chandeliers). Le cours insiste
  pourtant sur le volume comme critère de validation le plus important
  pour les figures chartistes ; ce moteur s'en passe entièrement, comme
  pour le reste du module Or. Limite assumée et documentée, pas cachée.
- Lignes de tendance/cou obliques précises : simplifiées en niveaux
  horizontaux approchés (voir "Simplifications géométriques" plus bas).
- Aucun changement au moteur de candlestick existant (les 8 figures de
  1-3 bougies restent inchangées).

## Architecture

### Détection : un `K` de pivot différent pour les figures chartistes

Les figures chartistes s'étalent sur bien plus de bougies qu'un niveau de
S/R ponctuel. Réutiliser `detectPivots` (déjà dans `docs/scalping.js`)
mais avec un **K plus grand** dédié aux figures chartistes :

```
CHARTPATTERN_PIVOT_K = 5   (vs. SCALP_PIVOT_K = 3 pour la tendance/S-R)
```

Un `K` plus grand filtre le bruit des pivots trop rapprochés, nécessaire
pour dessiner une épaule ou un triangle propre plutôt qu'une suite de
micro-zigzags.

### Nouveau fichier — `docs/chart_patterns.js`

Fichier séparé (pas ajouté directement à `docs/scalping.js`, qui gère déjà
6 responsabilités distinctes) suivant le même patron UMD-lite que
`docs/scalping.js` (`<script>` classique navigateur + `module.exports`
pour Node/tests). Exporte une seule fonction publique :

```
detectChartPatterns(candles, k) ->
  { name, direction: 'haussier'|'baissier', kind: 'retournement'|'continuation',
    breakoutPrice, extremityPrice, patternHeight } | null
```

- `breakoutPrice` : le niveau (neckline ou côté du triangle) dont le
  franchissement confirme la figure.
- `extremityPrice` : le point le plus extrême de la figure (le sommet
  d'un Double Top, la tête d'une Tête-Épaule, le pivot le plus ancien
  d'un triangle) — sert de base au calcul du stop-loss.
- `patternHeight` : hauteur verticale de la figure — sert au calcul de
  l'objectif de prix (voir plus bas).

Comme `matchCandlestickPattern`, ne renvoie que la **première** figure
trouvée sur les pivots disponibles (ordre de test à définir en
implémentation ; pas de recherche exhaustive de toutes les figures
présentes simultanément).

### Règles de détection (résumé — précision complète dans le plan)

Toutes les comparaisons "hauteur comparable" utilisent une tolérance en
pourcentage (nouvelle constante, ex. `CHARTPATTERN_HEIGHT_TOLERANCE`),
même esprit que `SCALP_LEVEL_PROXIMITY` pour les niveaux de S/R.

| Figure | Pivots requis | Neckline / cassure | Contexte de tendance requis | Sens |
|---|---|---|---|---|
| Double Top | 2 derniers pivots hauts (hauteur comparable) + le pivot bas entre eux | clôture sous le pivot bas intermédiaire | `haussier` (retournement) | baissier |
| Double Bottom | symétrique (pivots bas) | clôture au-dessus du pivot haut intermédiaire | `baissier` | haussier |
| Tête-Épaule | 3 derniers pivots hauts : épaule₁ ≈ épaule₂, tête clairement plus haute | clôture sous le plus récent des 2 pivots bas intermédiaires (approximation de la ligne de cou, voir ci-dessous) | `haussier` | baissier |
| Tête-Épaule inversée | symétrique (pivots bas) | clôture au-dessus du plus récent des 2 pivots hauts intermédiaires | `baissier` | haussier |
| Triangle ascendant | 2 derniers pivots hauts quasi égaux (résistance plate) ET 2 derniers pivots bas strictement croissants | clôture au-dessus de la résistance plate | `haussier` (continuation) | haussier |
| Triangle descendant | symétrique (support plat, pivots hauts décroissants) | clôture sous le support plat | `baissier` | baissier |
| Triangle symétrique | 2 derniers pivots hauts décroissants ET 2 derniers pivots bas croissants | clôture au-dessus du pivot haut le plus récent (→ haussier) ou sous le pivot bas le plus récent (→ baissier) | le sens de la tendance doit correspondre au sens de la cassure effective | déterminé par la cassure |

### Simplifications géométriques, assumées

- **Ligne de cou/tendance = niveau horizontal**, pas une droite oblique
  réelle. Pour la Tête-Épaule, on utilise le plus récent des deux creux
  intermédiaires (approximation standard côté trading pratique : c'est le
  niveau réellement surveillé pour une cassure, pas la droite théorique
  complète).
- **Triangle symétrique** : la cassure est jugée par rapport au pivot le
  plus récent du côté concerné, pas par extrapolation de la pente des
  deux droites convergentes (géométrie d'intersection non implémentée en
  v1 — cohérent avec le choix déjà fait pour la ligne de cou de la
  Tête-Épaule).

### Intégration dans `computeSignal` (`docs/scalping.js`, modifié)

Deux déclencheurs structurels distincts coexistent (le second est
nouveau) :

```
// Retournement (logique existante, élargie)
structurel_achat_retournement := tendance=="baissière" ET (rebond/cassure S-R OU figure chartiste retournement haussière)
structurel_vente_retournement := tendance=="haussière" ET (rebond/cassure S-R OU figure chartiste retournement baissière)

// Continuation (nouveau, pour les triangles)
structurel_achat_continuation := tendance=="haussière" ET figure chartiste continuation haussière (cassure confirmée)
structurel_vente_continuation := tendance=="baissière" ET figure chartiste continuation baissière (cassure confirmée)

structurel_achat := structurel_achat_retournement OU structurel_achat_continuation
structurel_vente := structurel_vente_retournement OU structurel_vente_continuation
```

La confirmation (indicateur + pattern) reste inchangée dans son principe
— RSI/MACD dans le bon sens, plus une figure (chandelier **ou** figure
chartiste) dans le même sens. Une figure chartiste détectée peut donc
servir à la fois de déclencheur structurel (ci-dessus) et de
confirmation, exactement comme un chandelier sert aujourd'hui uniquement
de confirmation — cohérent avec le principe "jamais un déclencheur seul",
puisque la confirmation reste une vérification indépendante du même
type de figure.

### Objectif de prix et stop-loss spécifiques aux figures chartistes

Le cours donne une règle différente de celle déjà utilisée pour les
trades déclenchés par S/R+chandelier :

- **Objectif (TP)** = prix de cassure ± `patternHeight` (dans le sens de
  la cassure) — remplace le calcul actuel basé sur le prochain niveau de
  S/R, uniquement quand une figure chartiste est le déclencheur
  structurel.
- **Stop-loss** = juste au-delà de `extremityPrice` (le point le plus
  extrême de la figure), avec la même marge `SCALP_STOP_BUFFER` déjà en
  place — un seul niveau (le cours en propose deux, agressif/prudent ;
  on ne garde que le prudent, cohérent avec l'unique stop-loss déjà
  utilisé ailleurs dans le moteur).

Quand le déclencheur structurel vient uniquement de S/R (pas d'une
figure chartiste), le calcul TP/SL actuel (niveau opposé ou repli ×1,5)
reste inchangé.

## Interfaces

- Nouveau : `docs/chart_patterns.js` → `detectChartPatterns(candles, k)`.
- Modifié : `docs/scalping.js` → `computeSignal` (nouveaux
  `structurel_*_continuation`, nouvelle branche TP/SL), export mis à
  jour pour inclure la dépendance vers `chart_patterns.js`.
- Modifié : `docs/index.html` → charge `chart_patterns.js` dans le même
  chargement paresseux que `scalping.js` (`loadScalpingModule`).
- Inchangé : `scalping_tracker.js` (réutilise `computeSignal` sans
  modification directe — bénéficie automatiquement des nouvelles règles
  une fois la branche mergée après l'essai).

## Tests

Même philosophie que `docs/scalping.test.js`/`docs/scalping_tracker.test.js` :
fichier Node-runnable sans framework (`docs/chart_patterns.test.js`),
données de bougies synthétiques, valeurs attendues calculées à la main.
Couverture minimale attendue : une détection positive par figure (7),
un cas de tolérance de hauteur qui échoue de justesse (prouve que la
tolérance est bien appliquée, pas juste une égalité stricte), et le cas
triangle symétrique dans les deux sens de cassure.

## Risques connus, assumés

- **Absence de volume** malgré son importance soulignée par le cours —
  déjà assumé pour tout le module Or, documenté ici explicitement pour
  les figures chartistes en particulier.
- **Lignes de cou/tendance horizontales, pas obliques** — simplification
  déjà en place pour les chandeliers (pas de vérification de gap),
  cohérente avec l'esprit du reste du moteur : privilégier une règle
  simple et testable à une géométrie parfaitement fidèle.
- **K=5 dédié aux figures chartistes** crée une deuxième notion de
  "pivot" dans le même moteur (K=3 pour tendance/S-R, K=5 pour les
  figures chartistes) — pas une incohérence, mais à documenter clairement
  dans le code pour qu'un futur lecteur ne les confonde pas.
- **Interaction avec l'essai en cours** : ce chantier ne doit merger sur
  `main` qu'après le 2026-09-16 — risque principal si cette règle n'est
  pas respectée : invalidation des résultats du paper-trading en cours.
