# L'Analyste — backtest historique du raisonnement IA + actus (6-12 mois)

**Statut** : brainstormé et validé avec l'utilisateur le 2026-10-24, prêt pour `writing-plans`.

## Contexte et origine

Le projet fait déjà tourner un **test prospectif** ("forward test") de raisonnement
IA combiné aux actus sur l'univers actions des 8 indices (CAC40, DAX, NASDAQ,
DOW, FTSE, SMI, IBEX35, FTSEMIB) — 3 lots de paris datés
(`docs/superpowers/experiments/2026-10-{11,12,19}-ia-news-reasoning-test.json`),
chacun un raisonnement qualitatif combiné sur 4 dimensions (actualité, technique,
secteur, volume), fait manuellement en session, **pas une formule codée**. Le
premier lot se vérifie à partir du 2026-11-08. C'est la référence la plus
fiable qui existe : aucune fuite d'information possible, puisque le pari est
pris avant de connaître l'issue.

L'utilisateur veut accélérer la constitution d'un échantillon — attendre
un pari à la fois, chaque mois, serait trop lent pour juger si ce raisonnement
a un edge. D'où ce chantier : **rejouer ce même raisonnement sur des dates
passées**, en acceptant les compromis méthodologiques que ça implique,
explicitement nommé et traité comme un complément au test prospectif, jamais
un remplacement.

Nom produit retenu : **l'Analyste** (ou "l'Analyste actus") — volontairement
distinct des bots existants (Or, Actions), qui exécutent des ordres réels.
L'Analyste raisonne et recommande ; il ne passe et ne passera jamais d'ordre.

## Le problème central : la fuite d'information

Un LLM interrogé sur une date passée a très probablement déjà "vu", dans ses
données d'entraînement, ce qui s'est passé après cette date — contrairement à
un vrai trader à ce moment-là. Un backtest naïf de raisonnement IA serait donc
trompeur, pas seulement coûteux à construire.

**Mitigations retenues (les deux ensemble, aucune n'est suffisante seule)** :

1. **Isolation stricte des données fournies au modèle** à ce qui existait
   réellement à la date testée (prix/volume tronqués à cette date, actus
   bornées par date — voir "Fenêtre et biais fondamental" et "Actus
   point-in-temps" ci-dessous), avec une instruction explicite dans le prompt
   de ne raisonner qu'à partir de ce qui est fourni, jamais de connaissance
   générale des événements postérieurs.
2. **Jugement asymétrique du résultat** : une fuite résiduelle (le modèle
   "sent" malgré tout quelque chose via ses poids, sans le citer
   explicitement) ne peut que faire *mieux* paraître le signal, jamais pire.
   Un résultat nul ou négatif reste donc fiable malgré une fuite possible ;
   un résultat qui bat largement l'indice doit au contraire être regardé avec
   méfiance plutôt que célébré, précisément parce que la fuite irait dans ce
   sens-là.

## Fenêtre et biais fondamental (déjà vérifié techniquement)

`indices_score.py` combine un volet technique (phase Weinstein,
`classify_weinstein_stage`, basé uniquement sur l'historique prix/volume —
reconstituable fidèlement sur plusieurs années via `yf.Ticker(...).history()`)
et un volet fondamental (`score_rentabilite`, `score_structure_financiere`,
`score_croissance`, `score_valorisation`...), qui lit
`yf.Ticker(ticker).financials` / `.quarterly_financials` / `.balance_sheet`.
yfinance ne renvoie que les **derniers états financiers connus aujourd'hui**
(une fenêtre glissante d'environ 4-5 trimestres/années), jamais un instantané
tel que visible à une date passée précise. Au-delà de cette fenêtre
glissante, utiliser les données "actuelles" de yfinance pour juger une date
passée introduirait un biais de *look-ahead* significatif (le score
fondamental refléterait des informations pas encore connues à l'époque).

**Décision retenue** : fenêtre de **6 à 12 mois en arrière** (pas 5 ans, pas
même 1-2 ans) — cette fenêtre reste dans la zone où yfinance est fiable
(peu de restatements en pratique sur une période aussi courte) sans ajouter
de complexité (SEC EDGAR ou toute autre source fondamentale historique n'est
pas nécessaire à cette échelle, et de toute façon ne couvrirait pas les
indices européens du périmètre). Le score composite complet (technique +
fondamental + actus) est conservé tel qu'il existe aujourd'hui, pas appauvri.

Cette fenêtre précède le lancement du projet lui-même (paper-trading démarré
vers septembre 2026) : il n'existe donc **aucun historique réel** de signaux
"entrée" dans `docs/signal_tracking.json` pour cette période. Les signaux
historiques doivent être **recalculés rétroactivement** avec le code de
scoring actuel appliqué aux données de marché/actus de l'époque — pas relus
depuis un journal existant.

## Composant 1 — génération rétroactive des signaux "entrée"

Nouveau script `analyste_backtest.py` (racine du dépôt, même convention que
`indices_score.py`) :

- Pour chaque date cible dans la fenêtre de 6-12 mois et chaque entreprise des
  8 indices : recalcule le score en réutilisant **directement** les fonctions
  existantes de `indices_score.py` (`classify_weinstein_stage`,
  `score_rentabilite`, `score_structure_financiere`, `score_croissance`,
  `score_valorisation`, `score_rentabilite_financiere`,
  `recalibrate_scores_by_profile`, `compute_composite`, etc. — jamais de
  réimplémentation parallèle de cette logique).
- Le volet technique utilise `yf.Ticker(ticker).history(end=date_cible)` —
  l'historique est tronqué strictement à la date cible, jamais au-delà.
- Le volet fondamental utilise les données fondamentales actuelles de
  yfinance telles quelles (justifié par la fenêtre courte ci-dessus).
- Un signal "entrée" rétroactif est retenu avec la **même condition** que
  l'alerte live (`score_favorable and percentile_favorable and near_entry and
  stage_label != "Déclin"`, voir `indices_score.py` ligne ~4996) — pas un
  seuil inventé pour ce chantier.
- Échantillonne **30 à 50** signaux ainsi obtenus, répartis sur les 8
  indices (si plus de 50 qualifient, sous-échantillonner au hasard avec une
  graine fixée, pour que le résultat soit reproductible ; si moins de 30
  qualifient sur la fenêtre choisie, élargir légèrement la fenêtre plutôt que
  de forcer un nombre).

## Composant 2 — actus point-in-temps

`assistant_ia/data_sources.fetch_gdelt_articles` ne prend aujourd'hui qu'une
borne basse (`startdatetime`) — pensée pour la fraîcheur (dernières 24-48h),
jamais pour borner une recherche historique par le haut. Sans extension, un
appel pourrait renvoyer des actus **postérieures** à la date testée, ce qui
serait une fuite directe et grossière.

**Extension requise** : une nouvelle fonction (ou un paramètre optionnel
`fin: datetime | None` ajouté à `fetch_gdelt_articles`) qui ajoute
`enddatetime` (format GDELT `%Y%m%d%H%M%S`, documenté côté API GDELT) aux
paramètres de requête quand il est fourni. Respecte la même contrainte de
rythme déjà documentée dans le code existant (1 requête GDELT toutes les 5
secondes).

Pour chaque signal retenu (Composant 1), récupère les actus de l'entreprise
sur une fenêtre de quelques jours à ~2 semaines avant la date cible (même
ordre de grandeur que la fraîcheur du système live), avec `enddatetime` fixé
strictement à la date cible — jamais après.

## Composant 3 — harnais de replay (Gemini)

Nouveau script qui, pour chaque (ticker, date cible, score technique +
fondamental recalculé, actus point-in-temps) :

- Construit un prompt reprenant le même raisonnement à 4 dimensions que le
  test prospectif (actualité, technique/stage/score, secteur, volume 5j vs 20j
  précédents) — même structure que
  `docs/superpowers/experiments/2026-10-19-ia-news-reasoning-test.json`
  (`methodologie`, `paris[].dimension_dominante`, etc.), pour que les deux
  séries de résultats restent directement comparables.
- Ajoute une instruction explicite et non ambiguë : *"Ne raisonne qu'à partir
  des données fournies ci-dessus. N'utilise jamais ta connaissance générale
  d'événements postérieurs au {date_cible}."*
- Appelle l'API Gemini (clé déjà configurée en variable d'environnement
  utilisateur `GEMINI_API_KEY`, modèle `gemini-2.5-flash` par défaut — pas
  Claude en session, pour que 30-50 appels indépendants se scriptent et se
  rythment sans consommer de tours de conversation).
- Respecte le rythme du niveau gratuit Gemini (5 requêtes/minute, 250 000
  tokens/minute en entrée) avec un backoff simple sur les réponses 429.
- Écrit chaque pari dans un nouveau fichier
  `docs/superpowers/experiments/2026-10-24-analyste-backtest-6-12-mois.json`
  (jamais mélangé avec les fichiers `*-ia-news-reasoning-test.json` du test
  prospectif — deux fiabilités différentes, deux fichiers différents), avec
  un champ `methode: "backtest_retroactif"` qui distingue explicitement ce
  lot du prospectif dans le même format par ailleurs.

## Composant 4 — résolution (rendement réalisé)

Pour chaque pari déjà écrit, calcule le rendement réalisé sur un horizon
d'environ **1 mois** après la date cible (même ordre de grandeur que le
test prospectif : pari du 2026-10-11, vérification prévue le 2026-11-08),
comparé au rendement de l'indice sur la même période — même formule que
l'existant (`return_pct`, `index_return_pct`, alpha = la différence).
Contrairement au test prospectif (qui attend que le temps passe réellement),
cette résolution est immédiate : le prix réel 1 mois après chaque date cible
est déjà connu aujourd'hui.

## Hors périmètre de ce chantier

- Toute automatisation future du test prospectif lui-même (notification du
  lundi, etc.) — sujet séparé, pas traité ici.
- Toute source de données fondamentales historiques "vintage" (SEC EDGAR ou
  équivalent) — écartée par la décision de réduire la fenêtre plutôt que
  d'ajouter cette complexité.
- Toute exécution réelle d'ordre à partir des résultats de ce backtest —
  l'Analyste ne passe jamais d'ordre, ce chantier ne change pas ça.

## Tests

Le point le plus important à tester rigoureusement est la **non-fuite** :

- Un test qui construit le contexte pour une date cible donnée et vérifie
  qu'aucune actu/donnée de prix dans ce contexte n'a une date postérieure à
  la date cible (ni dans l'historique de prix tronqué, ni dans les actus
  GDELT bornées par `enddatetime`).
- Un test que `fetch_gdelt_articles` (étendu) ajoute bien `enddatetime` aux
  paramètres de requête quand il est fourni, et ne l'ajoute pas quand il est
  absent (non-régression du comportement existant, fraîcheur 24-48h toujours
  valide pour les autres appelants).
- Un test que la troncature de l'historique prix (`history(end=date_cible)`)
  ne renvoie aucune bougie postérieure à `date_cible`.
- Un test que la condition de rétention d'un signal "entrée" rétroactif est
  identique à la condition live (même fonction appelée, pas une réécriture
  parallèle).
- Un test de la formule de résolution (rendement réalisé + alpha vs indice)
  contre un cas calculé à la main.

Le raisonnement du LLM lui-même n'est pas testé pour sa "justesse" (ça n'a
pas de sens) — seul le harnais autour (assemblage du contexte, respect du
rythme d'appel, écriture du fichier de résultats) l'est.
