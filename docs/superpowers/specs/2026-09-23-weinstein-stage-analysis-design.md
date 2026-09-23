# Méthode Stage Analysis (Stan Weinstein) pour affiner entrée/sortie — design

## Statut

Validé par l'utilisateur le 2026-09-23, prêt pour le plan d'implémentation.

## 1. Contexte et objectif

L'utilisateur a consulté des professionnels de la finance en dehors du
projet, qui lui ont conseillé la méthode "Stage Analysis" de Stan
Weinstein (*Secrets for Profiting in Bull and Bear Markets*) pour
affiner les points d'entrée/sortie du module Actions/Indices.

Aujourd'hui, `estimate_entry_exit_prices` (`indices_score.py:3465`)
combine un repère de valorisation (juste valeur ± marge) et un repère
technique dérivé uniquement de l'écart du prix à sa moyenne mobile 200
jours (`ecart_pct_ma200`) — sans jamais regarder la **pente** de cette
moyenne mobile, ni le **volume**. Or la pente et le volume sont le
cœur de la méthode Weinstein : un prix au-dessus de sa moyenne mobile
ne veut pas dire la même chose selon que cette moyenne monte ou
descend.

Objectif : classer chaque société suivie dans l'une des 4 phases de
Weinstein (base, avancée/achat, distribution, déclin) et s'en servir
pour produire des repères d'entrée/sortie plus sûrs — en particulier
ne jamais proposer de point d'entrée sur un titre en déclin confirmé,
même si son prix paraît bon marché par la valorisation seule.

Deuxième référence citée par l'utilisateur, Benjamin Graham
(*L'investisseur intelligent*) : jugée déjà globalement alignée avec
le calcul de juste valeur existant (marge de sécurité) — gardée en
réserve, aucun changement demandé dessus dans cette spec.

## 2. Périmètre

### Dans le périmètre

- Récupération du **volume hebdomadaire** (absent du pipeline
  aujourd'hui — seul le `Close` est extrait de l'historique yfinance).
- Nouvelle classification de phase (1 à 4) par société, calculée à
  partir de la moyenne mobile 30 semaines et de sa pente.
- Utilisation de cette phase pour **recalculer** le repère technique
  dans `estimate_entry_exit_prices` — pas juste l'ajouter comme une
  moyenne de plus.
- Un nouveau champ `stage` exposé par société dans `docs/indices.json`,
  et mentionné dans le corps de l'email d'alerte "entrée" comme
  contexte informatif.

### Explicitement hors périmètre

- Toute modification de `score_dynamique_recente` ou du score composite
  — ce facteur reste sur l'écart à la MM200 tel quel. Le recalibrage du
  score (rangs percentiles) est un chantier séparé, en pause.
- Tout filtrage du déclenchement des alertes "entrée" sur la base de la
  phase — décision explicite de l'utilisateur : la phase est affichée,
  pas bloquante, tant qu'elle n'a pas été mesurée dans la durée (même
  discipline que le paper-trading déjà en place pour les autres
  signaux du projet).
- Toute relecture de Benjamin Graham / changement du calcul de juste
  valeur.
- Distinction fine et fiable entre Phase 1 (base) et Phase 3
  (distribution) — voir §5.3, repli sur une étiquette "Neutre" commune
  si l'ambiguïté ne peut pas être levée simplement.

## 3. Architecture

### 3.1 Vue d'ensemble

```
fetch_company_financials() (indices_score.py, déjà existant)
  |
  |-- récupère déjà history(period="6y")["Close"] par entreprise
  |   (jusqu'ici seul le Close est gardé, Volume jeté)
  |
  |-- NOUVEAU : garde aussi la colonne Volume de la même réponse
  |   yfinance (aucun appel réseau supplémentaire)
  v
NOUVEAU : classify_weinstein_stage(daily_closes, daily_volumes)
  |-- rééchantillonnage hebdomadaire (pandas resample, pas de nouvel
  |   appel réseau — dérivé de l'historique quotidien déjà en mémoire)
  |-- MM30 semaines + pente -> phase 1/2/3/4
  v
estimate_entry_exit_prices() (existant, modifié)
  |-- repère technique recalculé selon la phase (voir §6)
  v
docs/indices.json (nouveau champ "stage" par société)
  |
  v
Email d'alerte "entrée" (gold_bot n'est pas concerné — ceci est le
  volet Indices, notify.py équivalent côté indices_score.py) — phase
  mentionnée en contexte informatif
```

### 3.2 Pourquoi pas d'autre approche

- **Fetch séparé en `interval="1wk"`** pour obtenir directement des
  bougies hebdomadaires : rejeté — un appel réseau de plus par
  entreprise (710 appels supplémentaires par run) pour un résultat que
  le rééchantillonnage du quotidien déjà récupéré donne à coût nul.
- **Détection automatique de cassure de résistance façon
  `docs/chart_patterns.js`** (le moteur de figures chartistes existant
  côté Or/Scalping) pour distinguer précisément Phase 1 de Phase 3 :
  rejeté pour cette v1 — complexité disproportionnée par rapport au
  besoin exprimé ("affiner entrée/sortie"), qui porte avant tout sur
  Phase 2 (achat) et Phase 4 (déclin), les deux phases où la pente de
  la MM30s seule donne déjà un signal net et fiable.

## 4. Modèle de données

### 4.1 Champs internes ajoutés à `ratios` (`fetch_company_financials`)

- `weekly_volumes` : série pandas des volumes hebdomadaires (moyenne des
  volumes quotidiens de chaque semaine ISO — pas la somme, voir §5.1
  pour pourquoi), même fenêtre que `history` (6 ans). Interne, non
  exposée telle quelle dans `docs/indices.json` (trop volumineuse) —
  sert uniquement au calcul de la phase.
- `stage` : `1`, `2`, `3`, `4`, ou `None` (historique insuffisant — 30
  semaines minimum requises).
- `stage_label` : `"Achat"` (stage 2), `"Déclin"` (stage 4), ou
  `"Neutre"` (tout le reste, y compris quand `stage` vaut 1 ou 3 —
  cette distinction interne n'est jamais exposée comme un label
  "Base"/"Distribution" tant qu'elle n'est pas fiable, voir §5.3).
- `volume_confirme` : `bool`, informatif — `True` si le volume de la
  semaine en cours dépasse 1,5x sa moyenne 30 semaines. Calculé
  systématiquement, quel que soit `stage`/`stage_label` — pas
  seulement lors d'un franchissement de la MM30s (voir §5.4).

### 4.2 `docs/indices.json`

Nouveau champ par société, à côté des champs existants
(`current_price`, `entry_price`, `exit_price`, ...) :

```json
{
  "stage": 2,
  "stage_label": "Achat",
  "volume_confirme": true
}
```

`stage`/`stage_label`/`volume_confirme` sont `null`/`"Neutre"`/`false`
si le calcul n'a pas pu être fait (historique < 30 semaines, ou panne
de récupération des données) — jamais d'exception remontée, même
contrat que le reste du pipeline (`data_available=False` pattern déjà
utilisé pour `score_rentabilite` etc.).

## 5. Méthode de calcul

### 5.1 Rééchantillonnage hebdomadaire

À partir de `history` (Series quotidienne déjà récupérée) et de son
Volume associé (nouvellement gardé). **Point de correction identifié à
la relecture** : `daily_closes` subit déjà un `dropna()` (purge des
clôtures NaN, voir `indices_score.py:2415`) et, pour les valeurs FTSE,
une division par 100 (conversion pence→livres, `indices_score.py:2398`)
— aucune des deux ne doit s'appliquer au Volume (pas de notion de
"pence" pour un volume, et un jour purgé côté Close doit l'être aussi
côté Volume, sous peine de désaligner les deux séries). Le Volume est
donc filtré sur le **même index de dates valides** que le Close déjà
nettoyé, jamais sur son propre `dropna()` indépendant :

```python
daily_volumes = daily_volumes.reindex(daily_closes.index)  # même dates que le Close nettoyé
weekly_closes = daily_closes.resample("W").last().dropna()
weekly_volumes = daily_volumes.resample("W").mean()
```

**Pourquoi `.mean()` et pas `.sum()`** : le pipeline tourne
quotidiennement, donc la dernière semaine du resample est presque
toujours EN COURS (incomplète) au moment du run. Avec `.sum()`, cette
semaine partielle serait comparée à une moyenne de 30 semaines
COMPLÈTES — un run du mardi n'aurait que ~1/5 du volume d'une semaine
pleine, rendant `volume_confirme` dépendant du jour de la semaine plutôt
que du volume réel. `.mean()` (volume quotidien moyen de la semaine)
reste comparable qu'une semaine soit pleine ou partielle, pour la
semaine courante comme pour les 30 semaines de référence.

### 5.2 MM30 semaines et sa pente

```python
ma30w = weekly_closes.rolling(30).mean()
```

Pente mesurée sur les 4 dernières semaines (fenêtre courte délibérée —
Weinstein juge la pente "à l'œil" sur un graphique hebdomadaire ; 4
semaines est le plus petit intervalle qui filtre le bruit d'une
semaine isolée sans lisser la pente au point de la rendre insensible à
un vrai changement de régime) :

```python
WEINSTEIN_SLOPE_LOOKBACK_WEEKS = 4
WEINSTEIN_SLOPE_NOISE_FLOOR_PCT = 0.5  # % sur 4 semaines, sous ce seuil -> "plate"

slope_pct = (ma30w.iloc[-1] - ma30w.iloc[-1 - WEINSTEIN_SLOPE_LOOKBACK_WEEKS])
            / ma30w.iloc[-1 - WEINSTEIN_SLOPE_LOOKBACK_WEEKS] * 100
```

`slope_pct > +0.5%` -> montante ; `< -0.5%` -> descendante ; sinon
plate.

### 5.3 Classification en 4 phases

Nécessite au moins 34 semaines de données (30 pour la MM30s + 4 pour
mesurer sa pente) — sinon `stage = None`.

- **Phase 2 (Achat)** : prix hebdomadaire courant > MM30s ET pente
  montante.
- **Phase 4 (Déclin)** : prix hebdomadaire courant < MM30s ET pente
  descendante.
- **Tout le reste** (MM30s plate, ou prix et pente en désaccord —
  ex. prix au-dessus d'une MM30s qui descend encore, une transition
  typique) : `stage_label = "Neutre"`, `stage` reçoit une valeur
  informative best-effort (`1` si le prix est dans la moitié
  inférieure de son range 52 semaines — probable base après déclin —
  sinon `3` — probable distribution après avancée), mais **cette
  distinction 1 vs 3 n'est jamais utilisée pour la décision d'entrée/
  sortie** (§6) : seuls Phase 2 et Phase 4 changent le comportement,
  le reste (1, 3, et None) partage le même traitement "neutre".

### 5.4 Confirmation volume (informative)

**Deuxième point de correction identifié à la relecture** : la moyenne
de référence doit porter sur les 30 semaines qui *précèdent* la semaine
courante, pas l'inclure — sinon un volume exceptionnel gonfle sa propre
moyenne de comparaison et s'auto-masque partiellement :

```python
WEINSTEIN_VOLUME_CONFIRMATION_MULTIPLE = 1.5

avg_volume_30w = weekly_volumes.iloc[-31:-1].mean()  # 30 semaines avant la courante, courante exclue
volume_confirme = weekly_volumes.iloc[-1] > avg_volume_30w * WEINSTEIN_VOLUME_CONFIRMATION_MULTIPLE
```

Nécessite donc 31 semaines pour ce calcul spécifique (au lieu de 30) —
sans effet pratique sur le minimum global de la §5.3 (34 semaines,
déjà supérieur).

Calculé quel que soit le stage — reste `False` si `weekly_volumes` est
indisponible (yfinance ne renvoie pas toujours un volume fiable, en
particulier hors actions US/Europe majeures) plutôt que de faire
échouer le calcul de phase.

## 6. Intégration dans `estimate_entry_exit_prices`

Le repère de **valorisation** (juste valeur ± marge) est strictement
inchangé. Seul le repère **technique** change de comportement selon la
phase :

- **Phase 2** : repère technique conservé tel quel (MM200 décalée par
  la dynamique récente, calcul actuel inchangé) — la tendance haussière
  confirmée par Weinstein soutient déjà ce repère.
- **Phase 4** : repère technique **exclu** des candidats
  entrée/sortie — `entry_candidates`/`exit_candidates` ne reçoivent
  que la valorisation (si disponible). Si la valorisation est elle
  aussi indisponible, `entry`/`exit` restent `None` (même contrat
  qu'aujourd'hui quand aucun candidat n'est disponible) plutôt que de
  proposer un repère bâti sur un titre en déclin confirmé.
- **Neutre (Phase 1/3/None)** : comportement actuel inchangé (moyenne
  valorisation + technique) — pas de changement pour la majorité des
  cas ambigus, cohérent avec le choix de ne pas trancher 1 vs 3 dans
  cette v1.

## 7. Email d'alerte "entrée"

`_entry_alert_context` (`indices_score.py:4083`) construit déjà le
contexte affiché dans l'email (dynamique récente + actus récentes) —
elle gagne un paragraphe supplémentaire, au même format que celui de
la dynamique récente, avec `stage_label`/`volume_confirme` :
`"Phase Weinstein : Achat (volume confirmé)"` / `"Phase Weinstein :
Neutre"` / `"Phase Weinstein : Déclin"`. Jamais utilisée pour décider
si l'email part ou non (voir §2, hors périmètre) — `_attach_alerts_and_update_history`
et `compute_company_alerts` restent inchangées.

## 8. Tests

- `classify_weinstein_stage` : Phase 2 (MM30s montante, prix dessus),
  Phase 4 (MM30s descendante, prix dessous), Neutre (MM30s plate),
  Neutre (désaccord prix/pente), `None` (historique < 34 semaines).
- Pente : cas pile au seuil de bruit (0,5% exactement — comparaison
  stricte), montante/descendante/plate sans ambiguïté.
- Confirmation volume : au-dessus/en dessous du multiple, volume
  manquant -> `False` sans exception ; un volume courant extrême ne
  doit PAS faire remonter sa propre moyenne de référence (vérifie
  explicitement l'exclusion `iloc[-31:-1]`, voir §5.4).
- Alignement Close/Volume : une date purgée par le `dropna()` du Close
  (ou une entreprise FTSE avec conversion pence→livres) ne doit
  désaligner ni fausser le Volume correspondant (voir §5.1).
- `estimate_entry_exit_prices` : Phase 2 inchangé vs comportement
  actuel ; Phase 4 exclut le candidat technique (avec et sans
  valorisation disponible en repli) ; Neutre inchangé.
- Bout en bout : un `build_company_entry` avec historique fabriqué
  correspondant à chaque phase, vérifie que `stage`/`stage_label`/
  `volume_confirme` apparaissent correctement dans l'entrée finale.
