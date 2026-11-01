# Réponses structurées de l'assistant IA (titres + actus réelles) — design

## Contexte

L'assistant `/ask` (`assistant_ia/`) répond aujourd'hui en texte brut
continu, sans titres (`SYSTEM_PROMPT`, `assistant_ia/assistant.py:81-83`
interdit explicitement `**gras**`, listes et titres). Une session de
maquettage (Artifact "Maquettes réponse IA", 10 exemples de questions)
a validé un format plus développé : plusieurs paragraphes courts, chacun
précédé d'un petit titre (ex. « Le mécanisme », « Ce que les
fondamentaux disent »), toujours conclu par un paragraphe « Mon avis » —
et, quand c'est pertinent, une bulle « Actualités » listant les vrais
articles qui ont nourri la réponse (titre, résumé, source, date, lien,
logo du média).

Cette V1 couvre uniquement les titres + les actus réelles. Graphiques,
comparatifs à deux entreprises, classements et chiffres-clés (vus dans
le mockup mais pour des questions différentes) sont du scope futur —
voir Non-objectifs.

**Déjà vérifié en conditions réelles avant ce spec** (pas une
hypothèse) :
- `assistant_ia/news.py::actualites_entreprise`/`actualites_marche`
  existent déjà, sont déjà déclarées comme outils Claude
  (`assistant_ia/tools.py:138-156`), et renvoient déjà exactement le
  format nécessaire : `{"articles": [{"titre", "source", "date" (ISO),
  "resume_court" (≤300 car.), "url"}, ...]}` ou `{"erreur": ...}`.
- `FINNHUB_API_TOKEN` manquait sur le VPS — fourni par l'utilisateur et
  configuré dans `/home/assistantia/analyse-or/.env` le 2026-11-01,
  service redémarré, testé en direct avec un vrai ticker US (AAPL) :
  fonctionne.
- GDELT (seule source pour les tickers non-US) renvoie actuellement
  `HTTP 429` en direct depuis l'IP du VPS (testé par `curl` brut,
  indépendamment de toute collision entre processus) — pas une panne
  transitoire de 5 secondes comme le code le suppose
  (`assistant_ia/data_sources.py:90-98`), un blocage qui dure. Les
  tickers non-US dégraderont donc sans bulle actus tant que ce n'est
  pas résolu séparément (voir Non-objectifs).

## Portée exacte

### 1. `assistant_ia/assistant.py` — `SYSTEM_PROMPT`

Remplace les lignes 81-83 actuelles :

```python
Reponds en texte brut, sans formatage markdown (pas de **gras**, pas de \
listes a tirets, pas de titres) — utilise des phrases completes et des \
retours a la ligne simples."""
```

par :

```python
Pour une reponse qui depasse une ou deux phrases, structure-la en \
plusieurs courts paragraphes, chacun precede d'un titre court (3 a 6 \
mots) seul sur sa ligne, au format "## Titre" (ex. "## Le mecanisme") \
— aucun autre formatage markdown (pas de **gras**, pas de listes a \
tirets). Termine TOUJOURS par un paragraphe precede du titre exact \
"## Mon avis", qui donne ta conclusion explicite et, si pertinent, ce \
qui la ferait changer. Pour une reponse courte (un chiffre, une \
confirmation simple, une seule idee), ces titres ne sont pas \
necessaires — n'en mets pas s'il n'y a qu'un seul paragraphe."""
```

Le reste de `SYSTEM_PROMPT` (lignes 26-79) ne change pas — en
particulier la consigne déjà existante de croiser les facteurs avec
`actualites_entreprise`/`actualites_marche` est déjà ce qui alimentera
la bulle actus, aucun changement de logique d'appel d'outil nécessaire.

### 2. `assistant_ia/assistant.py` — `run_assistant_loop` : collecte et émission des actus

Même mécanisme que `liens_collectes` (ligne 145, 176-177, 193-194,
203-204), appliqué à un nouvel `actus_collectes` :

- Déclarer `actus_collectes: list[dict] = []` et `urls_actus_vues:
  set[str] = set()` au même endroit que `liens_collectes` (ligne 145).
- Dans la boucle de dispatch des outils (lignes 185-200), après
  `resultat = tool_dispatch(...)` : si `bloc.name` vaut
  `"actualites_entreprise"` ou `"actualites_marche"` et que
  `"erreur" not in resultat`, parcourir `resultat.get("articles", [])`
  et ajouter à `actus_collectes` chaque article dont l'`url` n'est pas
  déjà dans `urls_actus_vues` (dédoublonnage : la même actu peut
  remonter via plusieurs appels d'outils dans une même conversation).
  Plafonner `actus_collectes` à 5 éléments au total (même constante
  `news.MAX_ARTICLES`) — un appel au-delà du plafond n'ajoute rien de
  plus.
- Émettre `{"type": "actus", "articles": actus_collectes}` aux DEUX
  mêmes points où `liens` est déjà émis : juste avant `yield {"type":
  "fin"}` quand `reponse.stop_reason != "tool_use"` (ligne 176-177) ET
  dans le repli `MAX_TOOL_ITERATIONS` épuisé (ligne 203-204) — dans les
  deux cas uniquement si `actus_collectes` est non vide, même garde que
  `liens`.
- Docstring de `run_assistant_loop` (lignes 120-125) à mettre à jour
  pour mentionner le nouvel événement `actus`, même style que la
  mention actuelle de `liens`.

### 3. Format de l'événement SSE `actus`

```json
{"type": "actus", "articles": [
  {"titre": "...", "source": "...", "date": "2026-11-01T14:06+00:00", "resume_court": "...", "url": "https://..."}
]}
```

Identique au format déjà renvoyé par `news.py` — aucune transformation
de forme nécessaire entre l'outil et l'événement SSE.

### 4. `docs/index.html` — réception SSE

Dans la boucle de parsing (`askAssistant`, autour de la ligne 5061-5076
actuelle) : ajouter une branche `if (evenement.type === 'actus') { ...
}` au même endroit et selon le même schéma que la branche `'liens'`
existante (ligne 5067-5072) — stocke les articles reçus, appelle
`typer.actus(articlesRecus)` si le `typer` existe déjà.

### 5. `docs/index.html` — `createHomeAnswer` (lignes 4827-4881)

- Ajouter `<div class="ai-actus"></div>` au template HTML (ligne
  4834-4842), positionné entre `.ai-msg-text` et `.ai-links` (les actus
  soutiennent la réponse, les liens proposent une navigation à part).
- `const actusEl = msg.querySelector('.ai-actus');` et `let actus =
  [];` à côté de `let liens = [];` (ligne 4847).
- `const afficherActus = () => { actusEl.innerHTML =
  homeSearchActusHtml(actus); actusEl.classList.add('ai-reveal-after');
  };` — même schéma que `afficherLiens` (ligne 4856-4860), appelée dans
  `onDone` (ligne 4864-4869) juste avant `afficherLiens()`.
- Exposer `actus(a) { actus = a || []; if (pret) afficherActus(); }`
  dans l'objet retourné (ligne 4873-4880), même schéma que `liens(l)`.

### 6. `docs/index.html` — rendu des cartes actus

Nouvelle fonction `homeSearchActusHtml(articles)`, à côté de
`homeSearchLinksHtml` (ligne 4772-4777), qui construit pour chaque
article une carte : logo (favicon du domaine extrait de `article.url`
via `new URL(...).hostname`, chargé depuis
`https://www.google.com/s2/favicons?domain=<hostname>&sz=64` — même
service que celui utilisé pour la maquette, aucun travail serveur),
titre, résumé, source + date formatée, lien `target="_blank"
rel="noopener"`. Toutes les valeurs externes (`titre`, `resume_court`,
`source`, `url`) passent par `escHtml` (déjà utilisé par
`homeSearchLinksHtml`) avant insertion.

Styles du logo : même motif que `.hero-logo-wrap`/`.hero-logo` déjà
utilisé pour les logos d'entreprise sur les fiches d'indices (ligne
735-736) — case 32×32, `border-radius`, `overflow:hidden`, **aucun
fond ni bordure**, l'image en `object-fit:contain`. `onerror` cache la
case logo (même motif que `companyLogoUrl`, ligne 6848) si le favicon
ne charge pas.

Nouvelles classes CSS (`.ai-actus`, `.ai-actu-card`, `.ai-actu-logo`,
`.ai-actu-title`, `.ai-actu-summary`, `.ai-actu-meta`) réutilisent les
variables déjà définies (`--text`, `--muted`, `--text-faint`, `--gold`,
section CSS autour de la ligne 447-470) — pas de nouvelle palette,
cohérent avec `.ai-link`/`.ai-msg-text` existants.

### 7. `docs/answer_reveal.js` — affichage des titres `## `

**Contrainte existante à respecter strictement** : le texte de
l'assistant est révélé mot par mot, chaque mot inséré via
`span.textContent` (jamais `innerHTML`, voir commentaire ligne 7-8) —
une protection délibérée contre l'injection de HTML par la réponse du
modèle. Les titres `## ` ne doivent PAS casser cette garantie.

Décision retenue : pendant le flux (lecture mot par mot), les lignes
`## Titre` s'affichent identiques au reste (y compris les deux
caractères `##`) — aucune tentative de les styliser en direct, pour ne
pas complexifier `tokenizeWords`/`appendWord` (lignes 19-21, 53-58) ni
risquer de casser le calcul de `pendingWords` (lignes 27-32) à chaque
chunk reçu. Une fois la réponse terminée (callback `onDone`, même point
où `afficherLiens`/`afficherActus` se déclenchent déjà, ligne
4864-4869), une passe de finalisation reconstruit le contenu de
`.ai-msg-text` ligne par ligne, strictement via `textContent`/
`createElement` (jamais `innerHTML`) : une ligne qui matche
`/^## (.+)$/` devient un `<div class="ai-section-title">` contenant le
titre (sans les `## `), ou `<div class="ai-section-title-avis">` si le
titre, une fois `trim()`é et comparé insensible à la casse, vaut
exactement « Mon avis » ; toute autre ligne reste un noeud texte
`pre-wrap` normal. Cette fonction (nom proposé :
`finalizeStructuredText(target, fullText)`) vit dans
`docs/answer_reveal.js`, appelée depuis `onDone` à la place du contenu
mot-par-mot déjà présent (qui reste affiché tel quel pendant le
streaming, puis est entièrement remplacé par cette passe une fois
`pret`).

Coût si ce choix (pas de stylisation en direct) déçoit à l'usage : les
deux caractères `##` sont visibles un court instant pendant la frappe
avant la passe de finalisation — acceptable, à revoir seulement si
l'utilisateur le juge gênant après un premier essai en prod.

## Non-objectifs (V1)

- **Graphiques inline** (ex. « CAC 40 sur 5 jours ») : aucune donnée
  quotidienne par entreprise n'existe aujourd'hui (seulement
  hebdomadaire, `docs/price_history.json`, depuis nov. 2020) — exigerait
  un nouvel appel `yfinance` en direct et un nouvel événement SSE.
  Reporté à une itération séparée.
- **Comparatifs à deux entreprises, classements, chiffres-clés
  (stats bot)** : vus dans le mockup pour d'autres types de questions,
  demandent une orchestration multi-outils plus complexe (ex. deux
  appels `fiche_entreprise` mis en regard) — reporté.
- **Réparer GDELT (429 persistant)** : hors scope de ce chantier,
  traité comme un gap connu et accepté — la dégradation (pas de bulle
  actus pour un ticker non-US tant que GDELT répond en erreur) est déjà
  le comportement du code existant, rien à coder de plus pour ça.

## Tests

- `assistant_ia/assistant.py` : nouveaux cas dans
  `tests/assistant_ia/test_assistant.py` (fichier déjà existant, à
  vérifier à l'implémentation) couvrant : actus collectées et émises
  une fois à la fin (mock de `tool_dispatch` renvoyant un
  `actualites_entreprise` avec articles) ; dédoublonnage par URL entre
  deux appels d'outils ; plafond à 5 ; rien émis si `actus_collectes`
  reste vide ; chemin `MAX_TOOL_ITERATIONS` épuisé émet aussi `actus`
  si collectées.
- `docs/answer_reveal.test.js` (fichier déjà existant) : nouveaux cas
  pour `finalizeStructuredText` — une ligne `## Titre` devient un
  `.ai-section-title`, `## Mon avis` (toute casse) devient
  `.ai-section-title-avis`, le texte normal reste intact, jamais de
  `innerHTML` utilisé (vérifiable en inspectant qu'aucun texte contenant
  des caractères `<`/`>` n'est jamais interprété comme balise — test
  d'injection explicite recommandé, même esprit que le commentaire de
  sécurité déjà présent dans le fichier).
- Pas de test end-to-end réel contre Finnhub/GDELT dans la suite
  automatisée (clé et réseau réels) — vérifié manuellement sur le VPS
  après déploiement, même motif que le reste du projet.

## Plan de déploiement

Aucune bascule à risque particulière — c'est une évolution du contrat
SSE déjà existant (`texte`/`liens`/`navigation`/`erreur`/`fin`), pas un
nouveau endpoint. Déployer le backend (`assistant_ia/assistant.py`) et
le frontend (`docs/index.html`, `docs/answer_reveal.js`) dans le même
push (ils doivent être cohérents : un ancien frontend ignorerait
simplement un événement `actus` inconnu sans casser, donc l'ordre
backend-avant-frontend ou l'inverse n'a pas d'impact — mais autant les
pousser ensemble). Redémarrer `assistant-ia-api.service` sur le VPS
après le push (le `.env` avec `FINNHUB_API_TOKEN` est déjà en place).
Vérification manuelle post-déploiement : poser une vraie question sur
une entreprise US (le chemin Finnhub, déjà confirmé fonctionnel) et
observer la bulle actus + les titres dans la réponse réelle.
