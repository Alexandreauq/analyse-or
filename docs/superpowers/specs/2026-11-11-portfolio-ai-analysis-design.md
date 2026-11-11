# Analyse IA du portefeuille manuel (onglet "Actions") — conception

## Contexte

L'onglet "Actions" (`docs/index.html` → `renderPortfolioActionsTab`, logique
pure dans `docs/portfolio.js`) est un suivi manuel de positions actions,
purement côté navigateur (`localStorage`, clé `analyse-or-portfolio`) : aucune
donnée n'y transite jamais par le pipeline Python/GitHub Actions. Il expose
déjà, par pur calcul JS, un carrousel de 5 sections : ajout de position,
positions détenues (totaux + P&L), opportunités (signaux "entrée" non
détenus), analyse (concentration, santé selon le modèle, attribution de
performance, dividendes reçus/projetés, volatilité/Sharpe/max drawdown vs
benchmark), et un dashboard du Bot Actions (affichage seul).

Le site a par ailleurs un assistant IA déployé (`assistant_ia/`, service
`assistant-ia-api.service`, port 8445 → `https://goldbot.fr:8445`), protégé
par jeton (`X-Bot-Token` / `AI_ASSISTANT_API_TOKEN`), avec deux routes
existantes : `POST /ask` (chatbot conversationnel en streaming, outil
`resume_portefeuille` qui expose les positions manuelles au chatbot de façon
brute, sans reco structurée ni historique) et `GET /financial-analysis/{ticker}`
(analyse IA d'une société, génération déléguée à un sous-processus qui
committe sur le dépôt git — non pertinent ici, voir "Alternatives rejetées").

**Demande utilisateur** (résumée après clarification par questions) :
quand l'utilisateur a saisi ses positions, pouvoir lancer une analyse IA
complète du portefeuille (vue d'ensemble + reco garder/renforcer/alléger/
vendre par ligne, en croisant actualité/risque), avec :
- déclenchement **à la demande** (bouton), pas de tâche automatique en
  arrière-plan — les positions restent strictement locales au navigateur ;
- un **indicateur de fraîcheur** ("dernière analyse il y a X jours") dans
  l'onglet, sans email ni notification ;
- un **historique conservé côté serveur** des analyses passées, pour que
  l'IA puisse dire ce qui a changé depuis la dernière fois.

## Objectifs

1. Un nouveau mécanisme, dédié, qui produit une analyse structurée
   (reco par ligne + points d'attention + résumé global) à partir des
   positions actuelles de l'utilisateur, croisées avec les données déjà
   publiées du site (score, alertes, secteur) et une recherche web pour
   l'actualité récente.
2. Les positions ne sont envoyées au serveur qu'au moment du clic (jamais
   stockées côté serveur en tant que telles) — seul le **résultat textuel**
   de l'analyse est archivé, pour la comparaison dans le temps.
3. Même rigueur que le reste du projet IA (jeton, limites de débit/
   concurrence, plafond de dépense quotidien partagé, échec jamais
   bloquant pour l'utilisateur, JSON du modèle parsé de façon défensive).

## Non-objectifs

- Pas de tâche planifiée / robot qui tourne sans navigateur ouvert (le
  "suivi fréquent" se limite à l'indicateur de fraîcheur, voir §Frontend).
- Pas d'email/notification (explicitement écarté par l'utilisateur).
- Pas de blocage/automatisation d'un ordre réel : c'est un outil
  d'information, jamais un trade exécuté automatiquement (différent du
  veto IA du bot Or, qui bloque réellement des trades).
- Pas de refonte des fonctions de calcul existantes de `portfolio.js`
  (totaux, concentration, santé, attribution, dividendes) : elles sont
  réutilisées telles quelles, jamais dupliquées côté Python.

## Architecture — vue d'ensemble du flux

```
[Navigateur]                              [assistant-ia-api :8445]
positions (localStorage)
   │
   ├─ calcule totals/concentration/health/attribution/dividendes
   │  (fonctions EXISTANTES de portfolio.js, aucune modification)
   │
   ├─ clic "Lancer l'analyse" ──POST /portfolio-analysis──▶  vérifie jeton
   │  {positions, totals, concentration,                     + plafond $/jour
   │   health, attribution,                                  + limites débit/
   │   dividends_received,                                   concurrence
   │   projected_dividend_income}                                │
   │                                                              ├─ pour chaque
   │                                                              │  ticker : relit
   │                                                              │  indices.json
   │                                                              │  publié (score,
   │                                                              │  alertes, secteur)
   │                                                              │
   │                                                              ├─ relit la DERNIÈRE
   │                                                              │  ligne de
   │                                                              │  portfolio_analysis_
   │                                                              │  history.jsonl
   │                                                              │
   │                                                              ├─ 1 appel Claude
   │                                                              │  (web_search inclus)
   │                                                              │
   │◀──────────── {disponible, erreur, resultat} ─────────────────┤
   │                                                              ├─ ajoute une ligne
   ├─ affiche résumé + reco par ligne                             │  au JSONL
   │                                                              ▼
   └─ (au prochain affichage de l'onglet)                 portfolio_analysis_
      GET /portfolio-analysis/last ──▶ {horodatage}        history.jsonl (VPS,
      → "dernière analyse il y a X jours"                  jamais commité)
```

Séparation des responsabilités, respectée strictement : **le calcul
arithmétique reste en JS** (déjà fait, déjà testé) ; **le serveur Python
n'ajoute que le jugement qualitatif IA** (croisement actualité/risque,
reco, comparaison temporelle) — jamais un second calcul des mêmes totaux.

## Contrat API

### `POST /portfolio-analysis`

En-tête requis : `X-Bot-Token: <AI_ASSISTANT_API_TOKEN>` (même jeton que
`/ask` — même modèle d'accès que le reste de l'assistant : un visiteur qui
ne connaît pas le jeton ne peut pas déclencher d'analyse, donc l'historique
reste de facto propre à l'utilisateur, sans notion de compte).

Corps de requête (JSON) — tel qu'assemblé par la nouvelle fonction pure
`buildPortfolioAnalysisPayload` (voir §Frontend) :

```json
{
  "positions": [
    {"id": "pos-...", "ticker": "MC.PA", "quantity": 3, "buy_price": 710.5, "buy_date": "2026-05-12"}
  ],
  "totals": {"EUR": {"value": 0, "pnlAbs": 0}, "USD": {"value": 0, "pnlAbs": 0}},
  "concentration": {"totalPositions": 0, "byIndex": {}, "bySector": {}, "byCurrency": {}},
  "health": {"avgScore": null, "scoredPositions": 0, "riskAlertCount": 0, "majorNewsAlertCount": 0},
  "attribution": [{"ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "pnlAbs": 0, "pnlPct": 0}],
  "dividends_received": {"EUR": 0, "USD": 0},
  "projected_dividend_income": {"EUR": {"annual": 0, "monthly": 0}, "USD": {"annual": 0, "monthly": 0}}
}
```

`positions` : positions **ouvertes** uniquement (une reco n'a de sens que
pour une ligne encore détenue). Validation serveur :
- absent, non liste, ou liste vide → `400` `"Aucune position fournie."`
- plus de `MAX_POSITIONS` (50) éléments → `400`
  `"Trop de positions (50 maximum)."` — garde-fou de coût/taille de prompt,
  jamais une vraie limite attendue en usage normal (même esprit que
  `MAX_QUESTION_CHARS` dans `api.py`).

Les cinq autres champs (`totals`, `concentration`, `health`, `attribution`,
`dividends_received`, `projected_dividend_income`) sont transmis tels que
produits par `portfolio.js` — aucune validation de forme stricte côté
serveur (un champ absent/`null` est simplement omis du prompt, jamais une
erreur 400 : un visiteur avec un ancien front en cache ne doit pas se
retrouver bloqué).

Réponse (toujours `200` si le jeton/débit/concurrence passent — l'échec de
l'appel IA lui-même est signalé dans le corps, jamais par un code HTTP
d'erreur, même convention que `/financial-analysis`) :

```json
{
  "disponible": true,
  "erreur": null,
  "resultat": {
    "resume_global": "texte de 2-3 phrases sur l'équilibre global",
    "points_attention": [
      {"ticker": "MC.PA", "raison": "alerte actu_majeure active sur le site"}
    ],
    "par_ligne": [
      {"ticker": "MC.PA", "reco": "garder", "justification": "une phrase"}
    ],
    "comparaison_precedente": "texte ou null si aucune analyse precedente",
    "horodatage": "2026-11-11T14:32:00Z"
  }
}
```

`reco` ∈ `{garder, renforcer, alleger, vendre}` (validé côté parsing, voir
§Parsing). Sur échec (jeton invalide → `401` ; débit/concurrence dépassés
→ `429`/`503`, mêmes messages que `/financial-analysis`/`/ask` ; plafond
quotidien `$5` déjà en place pour tout l'assistant → `429` ; appel IA
échoué ou JSON non interprétable → `200` avec
`{"disponible": false, "erreur": "...", "resultat": null}`).

### `GET /portfolio-analysis/last`

En-tête requis : `X-Bot-Token`. Pas de corps. Réponse :
`{"horodatage": "2026-11-11T14:32:00Z"}` ou `{"horodatage": null}` si
aucune analyse n'a jamais été faite. Lecture seule, pas de limite de
débit dédiée au-delà du jeton (coût nul : une lecture de fichier local,
pas d'appel IA).

## Côté serveur — nouveau module `assistant_ia/portfolio_analysis.py`

Nouveau fichier, miroir du patron déjà établi par
`gold_bot/ai_shadow.py` (un seul appel structuré, pas une boucle d'outils
en streaming comme `/ask`) :

- `MODEL = usage.MODEL_SONNET_5_5` — même modèle que `/ask` depuis le
  changement du 2026-11-07 (raison de coût déjà actée pour ce service).
- `WEB_SEARCH_MAX_USES = 4`, `MAX_TOKENS = 8000` (plusieurs lignes à
  couvrir, contrairement à `ai_shadow` qui ne juge qu'un seul symbole),
  `REQUEST_TIMEOUT_SECONDS = 60.0`.
- `HISTORY_PATH` : `assistant_ia/portfolio_analysis_history.jsonl`, à
  côté du module — jamais commité (voir §Historique).
- `_enrichit_positions(positions, *, indices_source) -> list[dict]` :
  pour chaque position, appelle **directement**
  `assistant_ia.tools.fiche_entreprise(ticker, indices_source=indices_source)`
  (fonction déjà existante et testée — jamais reréimplémentée) pour
  obtenir score/alertes/secteur/nom à jour. Un ticker introuvable (retiré
  du site) donne la fiche d'erreur déjà renvoyée par `fiche_entreprise`
  (`{"erreur": ..., "suggestions": [...]}`) — la position est quand même
  incluse dans le prompt, marquée comme "donnée indice indisponible",
  jamais exclue silencieusement.
- `_lit_derniere_analyse(path) -> dict | None` et
  `last_analysis_timestamp(path) -> str | None` : lecture du JSONL,
  tolérante aux lignes corrompues/fichier absent (même contrat que
  `ibkr_bot.journal.read_runs` : jamais d'exception).
- `_build_prompt(positions_enrichies, metriques, derniere_analyse) -> str` :
  construit le texte envoyé à Claude — contexte chiffré (totaux,
  concentration, santé, attribution, dividendes, tels que reçus, sans
  recalcul), fiche de chaque ligne (score, secteur, alertes actives),
  consigne de croiser avec `web_search` pour l'actualité récente sur les
  lignes les plus exposées, et **consigne de format de sortie strict** :
  terminer la réponse par un bloc ```` ```json ... ``` ```` sur une seule
  ligne (choix du bloc balisé plutôt que la simple regex à accolades de
  `ai_shadow` : le JSON ici contient des tableaux d'objets imbriqués, une
  regex `\{[^{}]*\}` non récursive ne les capturerait pas). Si
  `derniere_analyse` existe, son `resume_global` et son horodatage sont
  inclus dans le prompt pour que Claude puisse rédiger
  `comparaison_precedente` ; sinon, consigne explicite de renvoyer `null`
  pour ce champ.
- `_parse_resultat(texte) -> dict | None` : extrait le **dernier** bloc
  ```` ```json ... ``` ```` du texte (`re.DOTALL`, même principe défensif
  que `_parse_verdict` — jamais de `IndexError`/exception propagée),
  `json.loads`, vérifie la présence des clés `resume_global` et
  `par_ligne` (sinon `None`), complète les clés optionnelles absentes
  (`points_attention: []`, `comparaison_precedente: None`) plutôt que de
  rejeter une réponse par ailleurs valide. Valide aussi que chaque entrée
  de `par_ligne` a bien `reco` ∈ `{garder, renforcer, alleger, vendre}`
  (normalisé `.strip().lower()`, même precaution que l'incident du
  2026-11-10 documenté dans `ai_shadow.py` sur `verdict`/`confiance`) —
  une entrée avec une valeur `reco` hors de cet ensemble est conservée
  mais sa valeur passe à `"a_verifier"` plutôt que de faire échouer toute
  l'analyse pour une seule ligne mal formée.
- `run_portfolio_analysis(payload, *, client=None, indices_source=data_sources.fetch_indices_data, history_path=HISTORY_PATH) -> dict` :
  fonction d'entrée appelée par la route. `client` injectable (jamais de
  vrai appel réseau dans les tests, même principe que
  `run_assistant_loop`). Orchestre : enrichissement des positions →
  lecture de la dernière analyse → construction du prompt → appel Claude
  (capturé dans un `try/except Exception` large : toute panne réseau/API
  renvoie `{"disponible": False, "erreur": ..., "resultat": None}`,
  jamais une exception qui remonterait à la route) → `usage.record_usage`
  (même compteur de dépense partagé que `/ask`, pas de budget séparé) →
  parsing → si succès, ajoute l'horodatage serveur (jamais celui du
  modèle, pour éviter toute dérive d'horloge côté IA) et archive dans
  `HISTORY_PATH` (`_append_history`, jamais levée sur échec d'écriture,
  même contrat que `journal.append_run`).

## Historique — `assistant_ia/portfolio_analysis_history.jsonl`

Une ligne JSON append-only par analyse réussie, sur le VPS uniquement
(`/home/assistantia/analyse-or/assistant_ia/portfolio_analysis_history.jsonl`),
jamais committée (ajout à `.gitignore`, même famille que
`indices_ai_usage_today.json`) :

```json
{"horodatage": "2026-11-11T14:32:00Z", "positions_snapshot": [...], "resultat": {...}}
```

`positions_snapshot` = les positions brutes envoyées (pas les métriques
calculées, inutiles à relire plus tard — seul `resultat.resume_global` sert
de base à la comparaison suivante). Aucune purge/rotation prévue dans ce
chantier : un usage personnel à faible fréquence (quelques analyses par
mois au plus) ne justifie pas une politique de rétention — à revisiter si
le fichier devient gênant.

## Route — modifications de `assistant_ia/api.py`

Ajouts, sans toucher aux routes existantes :

```python
import assistant_ia.portfolio_analysis as portfolio_analysis

PORTFOLIO_MAX_CONCURRENT = int(os.environ.get("AI_PORTFOLIO_ANALYSIS_MAX_CONCURRENT", "1"))
PORTFOLIO_RATE_MAX_PAR_MINUTE = int(os.environ.get("AI_PORTFOLIO_ANALYSIS_RATE_PAR_MINUTE", "5"))
_concurrence_portefeuille = limits.ConcurrencyLimiter(PORTFOLIO_MAX_CONCURRENT)
_frequence_portefeuille = limits.RateLimiter(PORTFOLIO_RATE_MAX_PAR_MINUTE, 60)


@app.post("/portfolio-analysis")
async def portfolio_analysis_route(request: Request, x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    if usage.budget_exceeded():
        raise HTTPException(status_code=429, detail="Plafond quotidien de l'assistant atteint, reessaie demain")
    adresse = request.client.host if request.client else "inconnue"
    if not _frequence_portefeuille.autorise(adresse):
        raise HTTPException(status_code=429, detail="Trop de demandes en peu de temps : reessaie dans une minute.")
    payload = await request.json()
    positions = payload.get("positions")
    if not isinstance(positions, list) or not positions:
        raise HTTPException(status_code=400, detail="Aucune position fournie.")
    if len(positions) > portfolio_analysis.MAX_POSITIONS:
        raise HTTPException(status_code=400, detail=f"Trop de positions ({portfolio_analysis.MAX_POSITIONS} maximum).")
    if not _concurrence_portefeuille.tente():
        raise HTTPException(status_code=503, detail="Une analyse est deja en cours : reessaie dans un instant.")
    try:
        return portfolio_analysis.run_portfolio_analysis(payload)
    finally:
        _concurrence_portefeuille.libere()


@app.get("/portfolio-analysis/last")
def portfolio_analysis_last(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    return {"horodatage": portfolio_analysis.last_analysis_timestamp()}
```

`MAX_POSITIONS` défini dans `portfolio_analysis.py` (pas dans `api.py`) :
la route ne fait que la validation HTTP, la constante appartient au module
qui en a besoin pour construire le prompt.

## Frontend — `docs/portfolio.js` et `docs/index.html`

**`docs/portfolio.js`** — une seule fonction pure ajoutée (aucune fonction
existante modifiée) :

```js
/**
 * Assemble le payload envoye a POST /portfolio-analysis -- une seule
 * fonction pour que index.html n'ait jamais a construire cet objet a la
 * main (donc jamais a desynchroniser ses cles de celles lues par
 * assistant_ia/portfolio_analysis.py).
 */
function buildPortfolioAnalysisPayload(
  positions, totals, concentration, health, attribution,
  dividendsReceived, projectedDividendIncome,
) {
  return {
    positions, totals, concentration, health, attribution,
    dividends_received: dividendsReceived,
    projected_dividend_income: projectedDividendIncome,
  };
}
```

Exportée dans le bloc `module.exports` existant en fin de fichier.

**`docs/index.html`** — dans `renderPortfolioActionsTab` (L2685), nouvelle
section ajoutée au tableau `portfolioActionsSections`, entre "Analyse" et
"Bot Actions" :

```js
{
  title: 'Analyse IA',
  desc: 'Lance une analyse par l\'assistant IA : vue d\'ensemble, points d\'attention et une recommandation (garder/renforcer/alléger/vendre) pour chaque ligne détenue.',
  html: portfolioAiAnalysisSectionHtml(),
  wire: () => wirePortfolioAiAnalysisSection(positions, closedPositions, companiesByTicker, currencyByIndex, dividendHistoryByTicker),
},
```

Deux nouvelles fonctions (mêmes conventions de nommage/emplacement que les
fonctions `portfolio*`/`wirePortfolio*` déjà présentes dans ce fichier) :

- `portfolioAiAnalysisSectionHtml()` : rend un état initial — un bouton
  "Lancer l'analyse IA", une zone pour l'indicateur de fraîcheur (remplie
  après coup par `wire`, pas de valeur au premier rendu pour éviter un
  flash de contenu obsolète), et une zone de résultat vide.
- `wirePortfolioAiAnalysisSection(positions, closedPositions, companiesByTicker, currencyByIndex, dividendHistoryByTicker)` :
  1. Au branchement : `GET /portfolio-analysis/last` (jeton lu/demandé
     par le même mécanisme `prompt()` déjà en place pour les autres
     panneaux protégés du site — jamais un nouveau mécanisme de jeton) →
     affiche "Dernière analyse : il y a N jour(s)" ou "Aucune analyse
     encore" (`horodatage: null`) ou un message neutre si la requête
     échoue (jamais une erreur bloquante pour le reste de l'onglet).
  2. Au clic sur le bouton : recalcule `concentration`/`health`/
     `attribution`/`dividendsReceived`/`projectedIncome` avec les
     fonctions **déjà existantes** de `portfolio.js` (même appels que
     `portfolioAnalysisHtml`, donc aucune nouvelle fonction de calcul),
     construit le payload via `buildPortfolioAnalysisPayload`, affiche un
     état de chargement, `POST /portfolio-analysis`.
  3. Sur réponse `disponible: true` : rend `resume_global`,
     `points_attention` (liste), `par_ligne` (tableau ticker/reco/
     justification, lien vers la fiche société comme dans
     `attributionHtml`), et `comparaison_precedente` si non `null` ;
     rafraîchit l'indicateur de fraîcheur avec le nouvel horodatage.
  4. Sur réponse `disponible: false` ou échec réseau/HTTP (401/429/503) :
     message d'erreur adapté au code (même ton que les messages déjà
     affichés pour les autres panneaux protégés — "trop de demandes",
     "jeton invalide", etc.), bouton réactivé pour réessayer.

Pas de nouvelle constante d'URL : les appels utilisent l'URL littérale
`https://goldbot.fr:8445/portfolio-analysis` / `/portfolio-analysis/last`,
cohérent avec le style déjà en place pour `/ask` et `/financial-analysis`
(URLs littérales, pas de constante dédiée) — pas de refactor introduit ici.

## Tests

**Python** — `tests/assistant_ia/test_portfolio_analysis.py`, même
patron que `tests/gold_bot/test_ai_shadow.py` (faux client Anthropic,
jamais de vrai appel réseau) :
- `_parse_resultat` : bloc JSON valide/absent/malformé/clés manquantes/
  `reco` hors énumération.
- `_enrichit_positions` : ticker connu, ticker inconnu (fiche d'erreur
  incluse, pas d'exception).
- `_lit_derniere_analyse` / `last_analysis_timestamp` : fichier absent,
  vide, dernière ligne corrompue (ignorée, ligne précédente valide lue).
- `run_portfolio_analysis` : succès (vérifie l'archivage dans le fichier
  d'historique temporaire passé en paramètre), échec réseau/API (capturé,
  jamais levé), réponse non parseable, prise en compte d'une analyse
  précédente dans le prompt construit.

Côté route, extension de `tests/assistant_ia/test_api.py` existant :
jeton absent/invalide (`401`), positions vides/absentes (`400`), plus de
`MAX_POSITIONS` (`400`), plafond quotidien atteint (`429`), concurrence
dépassée (`503`), `GET /portfolio-analysis/last` avec et sans historique.

**JavaScript** — extension de `docs/portfolio.test.js` : un test pour
`buildPortfolioAnalysisPayload` (vérifie que les clés exposées
correspondent exactement au contrat ci-dessus, en particulier le
renommage `dividendsReceived` → `dividends_received`). Les fonctions
`portfolioAiAnalysisSectionHtml`/`wirePortfolioAiAnalysisSection` ne sont
**pas** testées unitairement : convention déjà établie dans ce fichier
(rendu/DOM de `index.html` jamais testé unitairement, voir l'en-tête de
`docs/portfolio.js` — seule la logique pure l'est).

## Sécurité et coût

- Jeton existant (`AI_ASSISTANT_API_TOKEN`), aucun nouveau secret.
- Limites de débit/concurrence dédiées, séparées de `/ask` et
  `/financial-analysis` (un profil d'abus différent, même principe déjà
  appliqué entre ces deux routes).
- Plafond de dépense quotidien ($5) partagé avec tout l'assistant —
  aucune dérogation : un pic de ce nouvel appel contribue au même compteur
  que `/ask`, donc protège déjà contre un abus cumulé.
- `positions_snapshot` dans l'historique contient tickers/quantités/prix
  d'achat — une donnée personnelle modérément sensible, jamais exposée
  publiquement par une route non protégée (contrairement à
  `/financial-analysis`, délibérément public puisqu'il ne traite que des
  données déjà publiques du site).

## Branche de travail

Chantier réalisé sur une branche dédiée (worktree isolé, voir
`superpowers:using-git-worktrees`), jamais directement sur `main` — demande
explicite de l'utilisateur. Fusion dans `main` seulement après validation.

## Alternatives rejetées

- **Étendre `/ask` et l'outil `resume_portefeuille` existant** : `/ask`
  est pensé pour du dialogue libre en streaming SSE, pas une réponse
  structurée reco-par-ligne, et n'a aucune notion de continuité entre deux
  visites — il faudrait de toute façon construire l'historique à côté,
  donc ça n'évite pas le vrai travail de ce chantier, juste moins propre
  à intégrer dans l'UI dédiée du carrousel.
- **Appel direct du navigateur vers l'API Anthropic** : exposerait la clé
  API dans le JS public du site — écarté immédiatement.
- **Déléguer à un sous-processus comme `/financial-analysis`** : ce
  mécanisme existe pour que la génération puisse committer sur le dépôt
  git (fiche société publique, versionnée) — non pertinent ici, puisque
  rien de ce chantier n'est destiné à être public/versionné ; un appel en
  cours de processus (comme `/ask`) est plus simple et suffisant.
