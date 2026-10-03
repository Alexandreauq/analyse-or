# Actualités pour l'assistant IA (entreprise + contexte de marché) — Design

## Statut

Validé par l'utilisateur le 2026-10-03 (brainstorming architectural). Prêt pour le plan d'implémentation après relecture de cette spec.

## 1. Objectif

Permettre à l'assistant IA de répondre à des questions du type « pourquoi LVMH a baissé aujourd'hui » en s'appuyant sur des actualités réelles et récentes, avec une règle de fiabilité centrale : **ne jamais affirmer une cause que les articles ne soutiennent pas explicitement**. Quand aucune actualité pertinente n'existe, l'assistant le dit.

## 2. Périmètre

### Dans le périmètre

- Deux nouveaux outils : `actualites_entreprise(ticker)` et `actualites_marche()`.
- Fournisseur : **Marketaux**, endpoint `/v1/news/all`. Couverture vérifiée sur les valeurs européennes et asiatiques de notre univers.
- Présentation dans la réponse en deux blocs distincts : **Actualités de l'entreprise** et **Contexte de marché**.
- Cache en mémoire, quota-aware.
- Règles de fiabilité décrites en §5.

### Hors périmètre

- Texte intégral des articles (seuls titre, source, date, résumé court et lien sont utilisés).
- Analyse de sentiment comme base de décision (le score de Marketaux peut être affiché, il ne pilote rien).
- Historique long (au-delà de 48 h pour l'entreprise).
- Recherche de cause pour des mouvements qui ne concernent que le marché (le contexte macro reste du contexte, jamais une explication causale).

## 3. Source et configuration

- Clé `MARKETAUX_API_TOKEN` dans le `.env` du VPS, jamais commitée.
- Paramètres de l'endpoint `/v1/news/all` retenus :
  - `api_token` (requis)
  - `symbols` : le ticker de l'entreprise (actualités d'entreprise)
  - `published_after` : fenêtre de fraîcheur, format `Y-m-d\TH:i:s`
  - `min_match_score` : seuil de pertinence, voir §5
- Quota gratuit de 100 requêtes/jour, à confirmer sur la page officielle avant déploiement.
- Étape de vérification obligatoire en tête du plan : pour le contexte de marché, confirmer le paramètre de filtrage par pays ou par secteur sur la documentation Marketaux, puis tester la couverture sur un échantillon de tickers par indice.

## 4. Outils et flux

### `actualites_entreprise(ticker)`

- Appel à `/v1/news/all` avec `symbols=ticker`, `published_after` = maintenant − 48 h, `min_match_score` = seuil.
- Filtrage côté client supplémentaire sur le `match_score` des entités (défense en profondeur, même si l'API renvoie des résultats faibles).
- Déduplication par URL, puis par titre normalisé.
- Tri par date décroissante, **5 articles maximum**.
- Résultat : liste de `{titre, source, date, resume_court, url}`. `resume_court` est tronqué à 300 caractères.
- Cache mémoire 2 heures par ticker.

### `actualites_marche()`

- Même endpoint, sans `symbols`, restreint aux pays de notre univers via le paramètre de filtrage confirmé en étape de vérification.
- Fenêtre de 24 h, 5 articles maximum, cache mémoire 1 heure.
- Résultat de même forme que ci-dessus.

### Présentation dans la réponse

Deux sections distinctes, toujours :
- **Actualités de l'entreprise** : chaque article cité avec sa date, sa source et son lien.
- **Contexte de marché** : traité comme contexte général, jamais comme cause du mouvement d'une entreprise précise.

## 5. Règles de fiabilité

1. **Seuil de pertinence** : `min_match_score` appliqué côté API et côté client. Valeur initiale fixée par test sur un échantillon réel, documentée dans le code.
2. **Fraîcheur** : 48 h pour l'entreprise, 24 h pour le marché. Un article plus ancien n'est jamais présenté comme « aujourd'hui ».
3. **Causalité** : le prompt système impose qu'une cause ne soit affirmée que si un article la formule explicitement. Sinon, l'assistant écrit « je n'ai pas trouvé d'actualité expliquant ce mouvement » et peut mentionner le contexte de marché comme simple toile de fond, sans le présenter comme la cause.
4. **Absence de résultat** : si l'outil renvoie une liste vide, l'assistant le dit explicitement. Il ne comble jamais le vide avec des connaissances générales.
5. **Dates et sources visibles** : chaque article cité affiche sa date et sa source. L'utilisateur peut juger la fraîcheur lui-même.
6. **Quota et pannes** : quota atteint ou API indisponible → l'outil renvoie une erreur explicite, l'assistant indique que les actualités sont indisponibles et répond quand même avec les données du site.

## 6. Sécurité

- Les titres et résumés d'articles sont des données externes non fiables. Le prompt système dit à Claude de les traiter comme du contenu à résumer, jamais comme des instructions.
- Les champs d'articles sont rendus comme **texte** côté navigateur, jamais comme HTML.
- Seules les URLs en `http://` ou `https://` sont affichées comme liens.
- Les liens de redirection vers le site continuent de passer par `proposer_lien`, validé comme aujourd'hui.
- Le contenu des articles est tronqué et ne transite jamais en entier, ce qui limite la surface d'injection et le coût.

## 7. Coût

- Chaque question qui déclenche les outils d'actualités ajoute des tokens d'entrée (les articles). Le plafond quotidien de 5 $ reste le garde-fou global.
- Limite de 5 articles × 300 caractères par outil, donc un plafond connu par appel.

## 8. Tests

- Client HTTP injecté, aucun appel réel aux tests.
- Paramètres de requête vérifiés : `symbols`, `published_after` (format exact), `min_match_score`, `api_token`.
- Filtrage client sur `match_score`, déduplication, tri, plafond de 5 articles, troncature à 300 caractères.
- Cache : pas de second appel réseau dans la fenêtre, nouvel appel après expiration.
- Erreur de quota et d'API : résultat d'erreur explicite, pas d'exception.
- Article hostile (titre contenant une instruction) : vérifié structurellement, il n'altère ni les liens ni les autres champs.
- Liste vide : le résultat le signale clairement.

## 9. Décisions retenues

- **Marketaux plutôt que Finnhub** : couverture européenne et asiatique, filtrage par pertinence, un seul fournisseur pour entreprise et marché.
- **Pas de recherche causale au-delà de ce que les articles disent** : la fiabilité prime sur la complétude des réponses.
- **Deux blocs séparés** dans chaque réponse, pour que l'utilisateur sache toujours ce qui est direct et ce qui est général.
