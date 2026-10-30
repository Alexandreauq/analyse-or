# Analyse financière IA à la demande (design)

Remplace la génération proactive de `financial_analysis_html` pour les
710 entreprises (déclenchée en masse à chaque changement de trimestre
détecté par le batch quotidien) par une génération **paresseuse,
déclenchée par le premier visiteur** qui consulte réellement la fiche
d'une entreprise dont le trimestre a changé.

## Contexte

Diagnostic du 2026-10-29/30 (voir mémoire `project_indices_cac40.md`,
`project_feature_backlog.md`) : `generate_financial_analysis` (Opus
5.5, jusqu'à 710 régénérations/jour, aucun plafond à l'époque) a été le
plus gros poste de dépense réel du compte Anthropic en un jour. Un
plafond `$15/jour` partagé a déjà été ajouté (`indices_score.py`,
commit `737cccb`/`ce293e2`) et l'effort a été réaligné sur le défaut du
modèle (`ce293e2`), mais le problème de fond reste : **la quasi-totalité
des analyses générées ne sont probablement jamais lues**. Vérifié dans
le code (2026-10-30) :

- `financial_analysis_html` n'alimente ni le score (`factors` est
  calculé indépendamment dans `build_company_entry`, *avant* l'appel à
  `generate_financial_analysis` — le prompt dit explicitement
  « ne pas les recalculer, les interpréter ») ni les réponses de
  l'assistant IA (`assistant_ia/tools.py::_fiche_allegee` supprime déjà
  ce champ avant de le transmettre au modèle, pour la même raison de
  coût — « finding 2 de la revue finale »).
- Son seul consommateur réel est `docs/index.html:6511`
  (`renderCompanyAnalysis`) — un texte narratif affiché aux visiteurs
  humains de la fiche détaillée d'une entreprise.

Décision utilisateur du 2026-10-30 : générer ce texte **à la demande du
visiteur** plutôt que pour toutes les entreprises par avance, avec un
plafond $ à trois fenêtres (jour/semaine/mois) en garde-fou dur **et**
une fréquence max de régénération par entreprise en garde-fou contre le
gaspillage même sous le plafond. Ce document est une nouvelle spec, pas
une révision d'un design existant.

## Non-objectifs

- Ne touche pas au calcul du score (`factors`, `score`,
  `interpretation`) ni à sa fréquence (reste quotidien, pour toutes les
  entreprises, inchangé).
- Ne touche pas à `summarize_news_item`/`summarize_commodity_news_item`
  (Haiku, classification des actus) ni à `generate_commodity_analysis`
  (or/argent/pétrole/gaz — volume fixe à 4/jour, déjà peu coûteux,
  hors sujet de ce chantier).
- N'ajoute pas d'authentification visiteur : contrairement à
  `/ask` (jeton manuel, conversation ouverte et potentiellement
  coûteuse), chaque paire (entreprise, trimestre) n'est générée **au
  plus une fois** quel que soit le nombre de visiteurs — le plafond $
  borne déjà le pire cas d'abus.

## Architecture

```
Visiteur ouvre la fiche détaillée d'une entreprise (docs/index.html)
        │
        ▼
company.financial_analysis_html correspond-il au trimestre courant
(company.financial_analysis_quarter == dernier trimestre connu) ?
        │
   oui ─┴─ affiche directement, aucun appel réseau (cas le plus fréquent)
        │
   non
        ▼
GET https://<vps>:8445/financial-analysis/<ticker>   (assistant_ia, pas de jeton)
        │
        ▼
Plafond $ (jour/semaine/mois) OU fréquence max par ticker dépassée ?
        │
   oui ─┴─ renvoie l'ancienne analyse en cache si elle existe,
        │   sinon {"disponible": false} — jamais d'erreur bloquante
        │
   non
        ▼
Sous-processus : python3 -m indices_score --generate-one <ticker>
        │   (voir § Sous-processus — isole l'écriture de assistant_ia/api.py,
        │    qui reste "lecture seule" pour ses propres besoins)
        ▼
indices_score.py :
  1. fetch_company_financials(ticker)  — identique au batch, un seul ticker
  2. build_ratios_summary(data, cost_of_capital)  — extrait de build_company_entry
  3. generate_financial_analysis(name, financial_context, ratios_summary)
  4. patch docs/indices.json : remplace l'entrée de ce ticker
     (financial_analysis_html, financial_analysis_quarter), le reste
     de l'entrée (score, factors, etc.) inchangé
  5. git add docs/indices.json && git commit && git pull --rebase --autostash && git push
        │
        ▼
Le sous-processus renvoie le HTML (stdout JSON) à la route, qui le
renvoie au visiteur. Le lendemain, le batch (indices.yml, 06h00 UTC)
lit ce même docs/indices.json : financial_analysis_quarter déjà à jour
pour ce ticker → ne régénère pas.
```

### Pourquoi un sous-processus plutôt qu'un appel direct depuis `assistant_ia/api.py`

`assistant_ia/api.py` porte une contrainte explicite héritée d'un plan
précédent : *« Lecture seule — ce module n'écrit jamais dans
positions.json/state.json/aucun fichier d'un autre module »*. Écrire
dans `docs/indices.json` (qui appartient à `indices_score.py`) et faire
un `git commit`/`push` depuis le même processus violerait cette règle.
Un sous-processus séparé garde la frontière de module intacte : la
route FastAPI ne fait que lire le cache et déclencher/attendre un
script externe, elle n'écrit jamais elle-même.

### Nouveau mode CLI — `indices_score.py --generate-one <ticker>`

- Charge `docs/indices.json`, retrouve l'entrée du ticker. Si absente
  → erreur (ticker inconnu).
- Si `financial_analysis_quarter` de l'entrée == trimestre courant
  (recalculé via un appel `fetch_company_financials(ticker)` léger,
  un seul ticker, pas tout le batch) ET `financial_analysis_html` déjà
  présent → ne rappelle pas Opus, renvoie l'existant (idempotent — un
  deuxième visiteur qui arrive pendant que le premier attend encore,
  ou juste après, ne paie pas deux fois).
- Sinon, vérifie le plafond $ (jour/semaine/mois, voir § Plafonds) et
  la fréquence par ticker. Si dépassés → renvoie l'ancien
  `financial_analysis_html` (ou `null` s'il n'y en a jamais eu), code
  de sortie dédié pour que la route sache qu'elle n'a rien généré.
- Sinon, appelle `generate_financial_analysis`, patch **uniquement
  l'entrée de ce ticker** dans `docs/indices.json` (lecture →
  modification du dict → écriture, jamais une réécriture complète du
  payload comme `main()` le fait), commit + push.
- Imprime le résultat en JSON sur stdout (`{"html": "...", "genere": true|false}`)
  pour que la route n'ait qu'à parser stdout, pas à relire le fichier.

### `build_company_entry` — retrait de l'appel proactif

Le bloc actuel (lignes ~6085-6152, voir `build_company_entry`) qui
appelle `generate_financial_analysis` dès qu'un changement de trimestre
est détecté est supprimé du batch quotidien. À la place, quand le
trimestre change, `build_company_entry` garde simplement l'ancien
`financial_analysis_html`/`financial_analysis_quarter` tels quels dans
l'entrée (sans les regénérer) — le score et les facteurs, eux, sont
toujours recalculés avec les données du trimestre courant, inchangé.
Le champ ne devient "périmé" que du point de vue de l'analyse texte,
jamais du score.

`build_ratios_summary(data, cost_of_capital, is_financial, is_trust)`
est extrait tel quel du corps de `build_company_entry` (les trois
branches `is_financial`/`is_trust`/générique déjà existantes) pour être
appelé indépendamment par le mode `--generate-one`, sans dupliquer la
logique.

## Plafonds

Extension du mécanisme déjà en place (`_load_indices_ai_usage`/
`_record_indices_ai_usage`/`_indices_ai_budget_exceeded`,
`indices_score.py`) :

- **`$/jour`** : `INDICES_AI_DAILY_BUDGET_USD = 15.0` (déjà existant,
  inchangé — partagé avec les analyses de commodités et la
  classification d'actus).
- **`$/semaine`** et **`$/mois`** : mêmes fichiers/mécanisme (fenêtre
  glissante basée sur la date, remise à zéro au changement de
  semaine/mois ISO). Valeurs de départ : `INDICES_AI_WEEKLY_BUDGET_USD
  = 30.0`, `INDICES_AI_MONTHLY_BUDGET_USD = 75.0` — volontairement
  inférieures à `7×`/`30×` le plafond jour, puisque la demande réelle
  attendue (quelques tickers distincts consultés par jour, chacun
  générée une seule fois par trimestre) est bien plus basse que le
  plafond jour lui-même ; à ajuster après quelques semaines d'usage
  réel si trop bas.
- **Fréquence par ticker** : pas plus d'une régénération réelle par
  ticker par 24h, quel que soit le nombre de visiteurs ou de
  changements de trimestre détectés dans l'intervalle (fichier cache
  séparé, clé = ticker, même format que `indices_ai_usage_today.json`).
- Dépassement d'un plafond : jamais d'erreur bloquante côté visiteur,
  toujours un repli propre (ancien contenu ou message d'indisponibilité).

## Frontend (`docs/index.html`)

`renderCompanyAnalysis(company)` :
- Si `company.financial_analysis_html` existe et correspond au
  trimestre courant (`company.financial_analysis_quarter` déjà exposé
  par le payload) → injecte directement, comportement inchangé.
- Sinon → affiche un indicateur de chargement, appelle
  `GET /financial-analysis/<ticker>`, injecte le résultat à l'arrivée
  (ou le message d'indisponibilité en cas de plafond dépassé).

## Tests

- `tests/test_indices_score.py` : `build_ratios_summary` isolée (une
  fonction testable indépendamment des trois profils), le nouveau mode
  CLI (`--generate-one`) avec mocks sur `generate_financial_analysis`
  et l'écriture fichier/git, les plafonds semaine/mois (même schéma que
  les tests jour déjà écrits pour `_indices_ai_budget_exceeded`), la
  fréquence par ticker, et la régression : `build_company_entry` ne
  doit plus jamais appeler `generate_financial_analysis` lui-même.
- `tests/assistant_ia/` : la nouvelle route (mock du sous-processus,
  jamais un vrai appel réseau/git dans les tests), le repli plafond
  dépassé, l'absence de jeton requis.
- Pas de test end-to-end réel git push dans la suite automatisée —
  vérifié manuellement sur le VPS après déploiement.

## Plan de déploiement

1. Déployer le nouveau mode CLI + la route, en gardant
   `build_company_entry` inchangé (génération encore proactive) un
   cycle de batch, pour vérifier que la route répond correctement sur
   des tickers déjà analysés (cas "déjà en cache, pas d'appel Opus").
2. Retirer l'appel proactif de `build_company_entry` seulement après
   cette vérification.
3. Confirmer que le VPS (`/home/assistantia/analyse-or`, voir mémoire
   `project_ai_search_assistant.md`) a des identifiants git avec droit
   de push sur `main` — **point ouvert, à vérifier avant
   l'implémentation**, sinon le sous-processus échouera au commit.
