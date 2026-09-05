# Actus enrichies (source, résumé IA) — design

Branche : `test3`
Sous-projet 1/3 d'une refonte plus large des Indices (2: méthode de
notation, 3: cours + point d'entrée/sortie — chacun aura son propre
design et plan, brainstormés séparément).

## Contexte

Les actus par entreprise (`docs/indices.json` → `companies[].news`)
n'affichent aujourd'hui que titre + date + lien, en couleur de lien bleue
par défaut. Le titre seul ne donne pas assez de contexte pour juger
l'impact d'une actu sans cliquer, et la couleur ne se distingue pas du
reste de l'app.

## Objectif

Pour chaque actu : afficher la source, un résumé/contexte en 1-2 phrases
généré par IA à partir du contenu réel de l'article (pas juste du titre),
et un titre dans la couleur d'accent de l'app plutôt qu'en bleu.

## Hors périmètre

- L'impact de l'actu sur le score composite (sous-projet 2).
- Toute source d'actu autre que le flux Google News déjà utilisé.
- Le contenu complet de l'article n'est jamais stocké ni affiché — seul
  le résumé généré par Claude est conservé dans `docs/indices.json`.

## Pipeline de données

`indices_score.py`, dans l'ordre pour chaque item RSS retourné par
`parse_news_rss` :

1. **`parse_news_rss`** extrait en plus le tag `<source url="...">Nom</source>`
   de chaque `<item>` (confirmé présent dans le flux réel). Nouveau champ
   `source` (texte du tag, `""` si absent).
2. **`fetch_article_text(url: str) -> str | None`** : requête GET sur le
   lien de l'actu (redirection Google News suivie automatiquement par
   `requests`, timeout 10s), extraction du texte principal via
   `trafilatura.extract()`, tronqué à 4000 caractères. Renvoie `None` sur
   tout échec (statut non-200, exception réseau, extraction vide) — ne
   lève jamais.
3. **`summarize_news_item(title, company_name, article_text) -> str`** :
   appel direct à l'API Anthropic (`POST https://api.anthropic.com/v1/messages`,
   pas de SDK ajouté — `requests` suffit, cohérent avec le reste du
   fichier), modèle `claude-haiku-4-5-20251001`, `max_tokens` ~150.
   - Si `article_text` fourni : demande une synthèse neutre en français
     (1-2 phrases) du contenu réel, contextualisée pour l'entreprise.
   - Si `article_text` est `None` : demande une phrase de contexte
     prudente basée sur le titre seul, formulée en hypothèse ("Cet
     article suggère que...") plutôt qu'en fait affirmé.
   - Sur tout échec (clé API absente, erreur réseau, réponse HTTP non-200,
     réponse malformée) : renvoie `""`. Ne lève jamais, n'interrompt
     jamais le traitement des autres actus.
4. `fetch_news(company_name: str) -> list[dict]` (existant, `indices_score.py:431`)
   garde son rôle d'assembler la liste finale : après `parse_news_rss`,
   elle appelle `fetch_article_text` puis `summarize_news_item` pour
   chaque item et leur attache `source` (déjà extrait à l'étape 1) et
   `summary` avant de renvoyer la liste.

Chaque étape peut échouer indépendamment sans faire disparaître l'actu :
au pire, une actu s'affiche avec titre + date + lien seulement (état
actuel), jamais avec une exception qui ferait perdre toute la liste
d'actus ou le score de l'entreprise (principe déjà établi pour
`fetch_news` dans son ensemble).

## Schéma JSON

Chaque objet de `companies[].news[]` gagne deux champs :

```json
{
  "title": "...",
  "date": "2026-09-05",
  "link": "...",
  "source": "Le Monde.fr",
  "summary": "Cet article évoque ..."
}
```

`source` et `summary` sont toujours présents (chaîne vide si
indisponible), pas de champ optionnel/absent — cohérent avec le reste du
schéma existant.

## Secrets / configuration

- Nouveau secret GitHub `ANTHROPIC_API_KEY`, déjà ajouté par
  l'utilisateur. À ajouter dans `.github/workflows/indices.yml` (variable
  d'environnement du step qui exécute `indices_score.py`).
- Si la variable d'environnement est absente au runtime (ex : exécution
  locale sans le secret), `summarize_news_item` renvoie `""` sans lever —
  le pipeline reste utilisable en local/dev sans la clé.

## Dépendances

Nouvelle dépendance Python : `trafilatura` (extraction de texte
principal), ajoutée à `requirements.txt`. Aucune dépendance ajoutée pour
l'appel Anthropic (HTTP direct via `requests`, déjà présent).

## Front-end (`docs/index.html`)

- Nouvelle classe CSS pour le titre d'actu, couleur `var(--gold)` (accent
  déjà utilisé partout ailleurs dans l'app — dates du calendrier, scores
  positifs, onglets actifs) au lieu du bleu de lien par défaut.
- Sous le titre : source + date (texte secondaire, `var(--muted)`).
- Sous la source/date : résumé (texte secondaire, taille réduite).
- Portée limitée à `renderCompanyDetail` (l'écran détail d'une entreprise
  dans l'onglet Indices) — c'est le seul endroit qui affiche des actus
  RSS par entreprise.

## Tests

Dans `tests/test_indices_score.py`, en suivant les conventions déjà en
place (fixtures XML/dict, pas d'appel réseau réel, imports en milieu de
fichier acceptés) :

- `parse_news_rss` : nouveau test vérifiant l'extraction du champ
  `source` depuis une fixture XML incluant le tag `<source>`.
- `fetch_article_text` : mock de `requests.get` — succès (retourne le
  texte extrait tronqué), échec HTTP (retourne `None`), exception réseau
  (retourne `None`), extraction vide (retourne `None`).
- `summarize_news_item` : mock de `requests.post` — vérifie le contenu du
  prompt selon que `article_text` est fourni ou `None`, vérifie le
  parsing de la réponse, vérifie le retour `""` sur échec HTTP/exception/
  réponse malformée, vérifie le retour `""` quand `ANTHROPIC_API_KEY`
  n'est pas définie (sans appel réseau dans ce cas).
- Test d'intégration légère : un item dont `fetch_article_text` échoue
  obtient quand même `source`/`summary` (via le repli titre seul), et
  n'empêche pas les autres items de la liste d'être traités normalement.

## Risques connus

- Beaucoup de sites de presse bloquent le scraping (paywall, anti-bot) —
  taux d'échec de l'extraction attendu significatif. Accepté : dans ce
  cas le résumé retombe sur la variante "titre seul", qui reste
  informative même si moins précise.
- Coût API : ~25 résumés/jour max (5 entreprises × 5 actus), modèle
  économique (Haiku), coût négligeable (largement sous 1$/mois).
- `trafilatura` peut échouer silencieusement à isoler le bon contenu sur
  des sites à structure inhabituelle — dégrade vers le repli titre seul,
  pas un bug bloquant.
