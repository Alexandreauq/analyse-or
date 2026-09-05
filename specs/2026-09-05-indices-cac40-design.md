# Scoring fondamental CAC 40 — activation de l'onglet "Indices"

Statut : validé en brainstorming, en attente d'implémentation.
Date : 2026-09-05

## Contexte et objectif

L'app "IA Investment" analyse aujourd'hui uniquement l'or (onglet "Or").
L'onglet "Indices" existe déjà dans la nav mais est désactivé (placeholder
pour la phase 2/3 de la roadmap). Ce projet l'active avec un premier
rating fondamental par entreprise du CAC 40, basé sur l'analyse des
comptes publiés des 5 dernières années.

L'utilisateur fournira une version numérique du "Vernimmen" (référence
française de finance d'entreprise) comme base méthodologique pour affiner
la grille de scoring exacte. **Le fichier ne doit jamais être commité ni
publié dans ce dépôt public** — il sert uniquement de référence de travail
locale, lue mais jamais redistribuée.

## Périmètre du v1 ("premier rating")

- **5 entreprises pilotes**, pas les 40 : LVMH (`MC.PA`), TotalEnergies
  (`TTE.PA`), BNP Paribas (`BNP.PA`), Schneider Electric (`SU.PA`),
  Sanofi (`SAN.PA`) — panel volontairement diversifié par secteur pour
  valider la méthodologie et le pipeline de données avant d'étendre aux
  40 valeurs du CAC 40.
- Objectif explicite : valider que yfinance fournit des données
  exploitables et que la grille de scoring produit des résultats
  cohérents, avant de généraliser.
- Pas de notation automatique du sentiment des news au v1 — juste
  l'affichage des titres récents.
- Pas d'historique de score / logique d'alerte de franchissement de
  seuil au v1 (YAGNI) — à ajouter plus tard si besoin.

## Décisions actées en brainstorming

1. **Source de données financières** : yfinance (API automatique,
   gratuite, sans clé), cohérent avec le reste du projet (déjà utilisé
   pour le cours de l'or et le DXY). Limite connue : historique
   généralement limité à ~4 ans selon les entreprises, pas toujours 5
   pile, et les libellés de lignes comptables varient d'une entreprise à
   l'autre (ex: "Total Debt" absent chez certaines) — à valider au cas
   par cas sur les 5 pilotes avant de généraliser aux 40.
2. **Fréquence de calcul** : **quotidien** (pas hebdomadaire, pas
   temps réel). Décision finale après discussion : l'utilisateur veut
   une réactivité aux news ("en veille"), donc on préfère rafraîchir
   tous les jours (coût réseau minime pour 5 entreprises) plutôt
   qu'une fois par semaine. Le score fondamental lui-même ne bougera
   de toute façon qu'au rythme des publications réelles ; le garde-fou
   `git diff --quiet` déjà utilisé dans `daily.yml` évite les commits
   vides les jours sans changement.
3. **Gestion des news** : séparée du score fondamental, à l'image de la
   séparation "score composite" / "alertes" déjà en place pour l'or.
   Section "Actu" indépendante par entreprise, alimentée par un flux
   RSS Google News filtré par nom d'entreprise (gratuit, sans clé),
   sans classification de sentiment automatique au v1 — juste titre,
   date, lien pour les ~5 actus les plus récentes.
4. **Intégration front-end** : réutilisation maximale des patterns déjà
   construits pour "Or" (accordéon "Actu" / "Détail du calcul", classes
   CSS existantes) plutôt que de nouveaux composants.

## Architecture

### Nouveaux fichiers

| Fichier | Rôle |
|---|---|
| `Methodologie_Analyse_Indices.md` | Méthodologie de scoring fondamental (facteurs, poids, seuils), affinée avec le Vernimmen une fois lu |
| `indices_score.py` | Script de calcul : récupère les données yfinance + news, calcule le score par entreprise, génère `docs/indices.json` |
| `.github/workflows/indices.yml` | Workflow GitHub Actions : cron quotidien + déclenchement manuel |
| `specs/2026-09-05-indices-cac40-design.md` | Ce document |

### Grille de scoring (squelette provisoire — à affiner avec le Vernimmen)

Score composite pondéré sur l'échelle **-100/+100** (même échelle que
l'or, pour la cohérence mentale de l'utilisateur), chaque facteur noté
**-10/+10** :

| Facteur | Poids (provisoire) | Ce qu'il mesure |
|---|---|---|
| Rentabilité / création de valeur | 30% | ROE, ROCE vs coût du capital (WACC), tendance sur 5 ans |
| Structure financière / solvabilité | 25% | Dette nette/EBITDA, couverture des intérêts (EBIT/frais financiers) |
| Croissance | 20% | CAGR chiffre d'affaires et EBITDA sur 5 ans |
| Génération de cash | 15% | Conversion FCF (FCF/EBITDA), intensité des capex |
| Valorisation relative | 10% | P/E et EV/EBITDA actuels vs moyenne 5 ans de l'entreprise elle-même |

Ces poids et ratios sont un point de départ standard de l'analyse
fondamentale à la Vernimmen (décomposition de la rentabilité, structure
financière, génération de cash, valorisation) — à ajuster avec le
fichier fourni par l'utilisateur avant l'implémentation du calcul, comme
`Methodologie_Analyse_Or.md` l'a été avant `gold_score.py`. Cette étape
ne bloque pas le reste de l'architecture.

### Format de `docs/indices.json`

```json
{
  "updated": "2026-09-05",
  "companies": [
    {
      "ticker": "MC.PA",
      "name": "LVMH",
      "score": 42.0,
      "interpretation": "Solide",
      "factors": [
        {"name": "Rentabilité (ROE/ROCE)", "score": 6.0, "weight": 0.30, "raw_value": "ROE 22% (moy. 5 ans 19%)"}
      ],
      "news": [
        {"title": "...", "date": "2026-09-04", "link": "https://..."}
      ]
    }
  ]
}
```

### Pipeline de données (`indices_score.py`)

- Liste des entreprises pilotes en constante Python en haut du fichier
  (ticker + nom), sur le modèle des facteurs manuels de `gold_score.py`.
- Pour chaque entreprise : `yfinance.Ticker(ticker).financials`,
  `.balance_sheet`, `.cashflow`, `.info` (P/E, capitalisation).
- Calcul des ratios par catégorie, combinés en score composite pondéré.
- News : requête au flux RSS `news.google.com/rss/search?q=<nom
  entreprise>&hl=fr&gl=FR&ceid=FR:fr`, parsée avec le module XML standard
  de Python (`xml.etree.ElementTree`) — pas de nouvelle dépendance dans
  `requirements.txt`.
- Écriture de `docs/indices.json`.

### Automatisation (`.github/workflows/indices.yml`)

- Même structure que `daily.yml` : checkout, setup Python, install
  requirements, exécution du script, commit + push de `docs/indices.json`
  si changement (garde-fou `git diff --staged --quiet`).
- Cron quotidien (heure à définir, ex. `0 6 * * *`) + `workflow_dispatch`
  pour les tests manuels.
- Workflow séparé de `daily.yml` : domaines et historiques d'exécution
  différents, pas de bénéfice à les fusionner.

### Intégration front-end (`docs/index.html`)

- Le bouton "Indices" de la nav passe de `disabled` à actif, branché sur
  le routeur par hash existant.
- Nouvel écran plein écran `#indices`, même mécanique de transition
  (glissement) que l'écran `#or`.
- Sous-routage pour la liste vs le détail d'une entreprise (ex:
  `#indices` = liste, `#indices/MC.PA` = détail).
- Vue liste : une carte par entreprise (nom, ticker, score coloré doré/
  rouge comme pour l'or).
- Vue détail : réutilisation à l'identique du composant accordéon
  "Actu" / "Détail du calcul" déjà construit pour l'or (mêmes classes
  CSS `toggle-btn`, `notice`, `panel`, `alert-row`) — quasiment aucun
  nouveau CSS nécessaire, juste le composant "carte entreprise" pour la
  liste.

## Hors périmètre du v1 (explicitement reporté)

- Extension aux 40 valeurs du CAC 40 (après validation du pilote)
- Classification automatique de sentiment sur les news
- Historique de score / alertes de franchissement de seuil par entreprise
- Comparaison sectorielle pour la valorisation (on compare l'entreprise
  à son propre historique 5 ans, pas à ses pairs, au v1)
- Analyse des minières aurifères (reste une roadmap distincte)

## Risques connus à valider pendant l'implémentation

- Complétude et cohérence des données yfinance pour les 5 pilotes
  (lignes comptables manquantes ou étiquetées différemment selon
  l'entreprise) — à traiter au cas par cas, pas de solution générique
  attendue avant d'avoir vu les données réelles.
- Le Vernimmen n'a pas encore été lu au moment de la rédaction de ce
  spec — la grille de scoring ci-dessus est un point de départ standard,
  pas une version finale.
