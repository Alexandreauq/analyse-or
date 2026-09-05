# Méthodologie : scoring fondamental CAC 40 (onglet "Indices")

## Objectif

Produire un premier rating fondamental par entreprise, basé sur l'analyse
des comptes publiés des 5 dernières années. Version pilote sur 5
entreprises représentatives de profils sectoriels différents, avant
extension aux 40 valeurs du CAC 40.

Les concepts et seuils ci-dessous s'appuient sur la méthodologie de
référence française en finance d'entreprise (Vernimmen — diagnostic
financier, analyse de la rentabilité comptable, analyse du financement,
coût du capital, pratique de l'évaluation), synthétisée et adaptée ici
pour un calcul automatisé.

## Entreprises pilotes

| Ticker | Entreprise | Secteur (yfinance) | Profil de risque |
|---|---|---|---|
| `MC.PA` | LVMH | Consumer Cyclical | Cyclique |
| `TTE.PA` | TotalEnergies | Energy | Cyclique |
| `SU.PA` | Schneider Electric | Industrials | Standard |
| `SAN.PA` | Sanofi | Healthcare | Défensif |
| `BN.PA` | Danone | Consumer Defensive | Défensif |

Note : les banques et sociétés financières (ex. BNP Paribas) sont
volontairement exclues du pilote — leur bilan (pas d'EBITDA, ratios
réglementaires type CET1) ne correspond pas à cette grille de ratios,
conçue pour des entreprises non financières. Elles feront l'objet d'une
grille dédiée dans une phase ultérieure.

## Profils de risque sectoriel

Un même niveau d'endettement n'a pas la même signification selon que
l'entreprise opère dans un secteur à flux de trésorerie stables et
prévisibles ou dans un secteur cyclique. Trois profils, dérivés du champ
`sector` de yfinance :

| Profil | Secteurs yfinance | Logique |
|---|---|---|
| **Défensif** | Utilities, Consumer Defensive, Healthcare, Real Estate | Flux prévisibles (infrastructures, santé, biens de consommation courante) → tolérance d'endettement plus élevée |
| **Standard** | Industrials, Communication Services | Profil intermédiaire → seuils Vernimmen de base |
| **Cyclique** | Energy, Basic Materials, Consumer Cyclical, Technology | Flux sensibles à la conjoncture → tolérance d'endettement plus faible |

Les seuils de la section "Structure financière / solvabilité"
ci-dessous sont exprimés pour le profil **Standard** et ajustés
(multiplicateur) pour les deux autres profils.

## Grille de scoring

Score composite pondéré sur l'échelle **-100/+100** (même échelle que
l'or), chaque facteur noté **-10/+10**.

### 1. Rentabilité / création de valeur — poids 30%

- **ROCE** (rentabilité économique, *Re*) = Résultat d'exploitation ×
  (1 − taux d'IS apparent) / Actif économique, décomposé en **marge
  d'exploitation** (Résultat d'exploitation / CA) **× rotation de
  l'actif économique** (CA / Actif économique).
- **ROE** (rentabilité des capitaux propres, *RCP*) = Résultat net /
  Capitaux propres.
- **Effet de levier** : RCP = Re + (Re − i) × D/CP (i = coût de la
  dette nette après impôt, D = dette nette, CP = capitaux propres).
  Sert à vérifier que la rentabilité des capitaux propres provient
  d'une vraie performance opérationnelle (ROCE) et non uniquement de
  l'endettement.
- Score favorable si le ROCE dépasse durablement une estimation
  simplifiée du coût du capital (proxy : taux sans risque + prime de
  risque actions, sans calcul de bêta complet au v1), et si la
  tendance sur 5 ans est stable ou croissante.

### 2. Structure financière / solvabilité — poids 25%

Seuils de base (profil Standard), ajustés par profil sectoriel :

| Ratio | Confortable | Lourd | Risqué |
|---|---|---|---|
| Dette nette / EBITDA | < 3 | 3 à 5-6 | > 5-6 |
| Couverture des intérêts (EBIT / frais financiers nets) | > 3 | proche de 3 | < 3 |

Ajustement par profil : profil Défensif = seuils × 1,3 (plus tolérant),
profil Cyclique = seuils × 0,7 (plus strict). Ex. pour un profil
Défensif, le seuil "confortable" de dette nette/EBITDA passe de 3 à
~4 ; pour un profil Cyclique, il descend à ~2.

- **Levier financier (gearing)** = Dette nette / Capitaux propres —
  facteur secondaire de lecture, non seuillé au v1.

### 3. Croissance — poids 20%

- CAGR chiffre d'affaires sur 5 ans
- CAGR EBITDA sur 5 ans
- Score favorable si croissance positive et cohérente entre CA et
  EBITDA (une croissance du CA sans croissance de l'EBITDA signale une
  dégradation de la rentabilité).

### 4. Génération de cash — poids 15%

- **Flux de trésorerie disponible (FCF)** = EBITDA − IS théorique sur
  le résultat d'exploitation − variation du BFR − investissements
  nets des désinvestissements.
- **Conversion FCF/EBITDA** : plus ce ratio est élevé, plus la
  rentabilité comptable se traduit réellement en cash (une rentabilité
  élevée mais un FCF durablement négatif ou faible est un signal
  d'alerte : BFR ou capex qui consomment tout le cash généré).

### 5. Valorisation relative — poids 10%

- Multiples **EV/EBITDA** et **P/E (PER)** actuels comparés à la
  moyenne des 5 dernières années de l'entreprise elle-même (pas de
  comparaison à des pairs sectoriels au v1).
- Lecture prudente : un multiple élevé par rapport à l'historique de
  l'entreprise reflète le plus souvent des perspectives de croissance
  jugées meilleures par le marché ou un risque perçu plus faible — ce
  n'est pas systématiquement un signal négatif de "cherté". Le score
  ne pénalise donc un multiple élevé que modérément, et seulement en
  combinaison avec un ralentissement de la croissance (facteur 3) qui
  rendrait ce multiple difficile à justifier.

## Interprétation du score composite

Mêmes bornes que pour l'or, pour la cohérence de lecture dans l'app :

- **> +50** : profil fondamental très solide
- **+15 à +50** : solide
- **-15 à +15** : neutre
- **< -15** : fragile

## Hors périmètre (v1)

- Extension aux 40 valeurs du CAC 40 et aux valeurs financières
  (grille dédiée à construire séparément)
- Calcul complet du coût du capital (bêta désendetté, prime de risque
  de marché) — proxy simplifié au v1
- Comparaison à un échantillon de pairs sectoriels pour la valorisation
- Historique de score / alertes de franchissement de seuil par
  entreprise
