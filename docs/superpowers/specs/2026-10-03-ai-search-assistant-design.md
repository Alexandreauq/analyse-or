# Assistant de recherche intelligent (écran d'accueil) — Design

## Statut

Validé par l'utilisateur le 2026-10-03 (brainstorming architectural), prêt pour le plan d'implémentation.

## 1. Contexte et objectif

L'utilisateur veut, sur l'écran d'accueil du site, une barre de recherche capable de comprendre le langage naturel et de répondre à des questions sur les propres données du site (pas de connaissance générale du monde) : scores/comparaisons d'entreprises, statut des bots de trading, contenu du portefeuille. Deux modes dans la même barre : navigation instantanée pour les requêtes courtes ("LVMH", "bot or"), conversation argumentée pour les questions complètes ("pourquoi X est mieux noté que Y").

Décision explicite de l'utilisateur en cours de brainstorming : viser la version la plus aboutie, pas la plus rapide à livrer — raisonnement/arguments réels appuyés sur les mêmes facteurs que le score affiché, liens de redirection proposés comme des boutons cliquables distincts du texte, désambiguïsation interactive plutôt que des suppositions silencieuses. Voir [[feedback_no_effort_hedging]].

## 2. Périmètre

### Dans le périmètre (V1)

- Questions sur les données déjà publiées par le site : scores/facteurs des 710 entreprises, comparaisons, classements, statut opérationnel des bots Or/Actions, positions des bots, résumé du portefeuille manuel (positions envoyées par le navigateur).
- Navigation instantanée côté client (sans IA) pour les requêtes qui matchent clairement un ticker/nom/section.
- Conversation multi-tour, réponses argumentées facteur par facteur, liens de redirection proposés comme des choix cliquables, désambiguïsation interactive.
- Accès protégé par jeton (même modèle que les tableaux de bord Bot Or/Bot Actions).
- Streaming de la réponse texte.
- Journal d'usage/coût réel, plafond quotidien de dépense.

### Explicitement hors périmètre (V1)

- Toute connaissance nécessitant une source externe (actualités, prévisions de marché, "pourquoi l'or a baissé aujourd'hui") — déjà identifié comme la partie peu faisable du backlog "chatbot général" ([[project_feature_backlog]]).
- Accès public sans jeton.
- Toute action d'écriture (l'assistant ne modifie jamais positions.json, state.json, ni aucun fichier — lecture seule, comme les API Bot Or/Bot Actions existantes).
- Persistance serveur de l'historique de conversation (le navigateur porte l'historique, envoyé à chaque requête — pas de nouvelle base de données, cohérent avec l'absence de backend stateful ailleurs dans le projet).

## 3. Architecture générale

```
Écran d'accueil (docs/index.html)
  │
  ├── Filtre instantané côté client (ticker/nom/section déjà chargés
  │   dans indicesData) → redirection directe, aucun appel réseau
  │
  └── Sinon → POST vers le nouveau service (jeton requis)
                                                        │
                                                        ▼
                                    assistant_ia/api.py (VPS, nouveau,
                                    port 8445, lecture seule)
                                       │
                                       ├── fetch docs/indices.json et les
                                       │   autres fichiers publics du site
                                       │   (cache mémoire ~10 min)
                                       │   — PAS de checkout local, la
                                       │   source de vérité est ce qui est
                                       │   réellement publié
                                       ├── appelle /dashboard de gold_bot/
                                       │   ibkr_bot avec LEURS jetons
                                       │   respectifs (configurés dans le
                                       │   .env de ce nouveau service)
                                       └── appelle l'API Anthropic
                                           (claude-opus-5-5, boucle d'outils)
                                                        │
                                                        ▼
                                    Réponse streamée + liens suggérés
```

**Pourquoi lire les fichiers publics plutôt qu'un checkout VPS local** : `gold_bot` et `ibkr_bot` tournent chacun sous leur propre utilisateur système, avec leur propre checkout du dépôt (`/home/goldbot/analyse-or`, `/home/ibkrbot/analyse-or`), pas nécessairement sur le même commit à un instant donné. Ce nouveau service répond à des questions sur **le site tel qu'il est réellement publié** — il doit donc lire exactement ce que `docs/indices.json` etc. contiennent sur `https://alexandreauq.github.io/analyse-or/` (ou l'URL de publication réelle), jamais un fichier local d'un checkout qui pourrait être en avance/retard. Ça découple aussi ce service de tout déploiement de bot particulier.

**Pourquoi passer par les API existantes pour les statuts/positions des bots plutôt que relire leurs fichiers directement** : `gold_bot/api.py` et `ibkr_bot/api.py` sont déjà la source de vérité protégée par jeton pour ces données (voir `docs/superpowers/specs/2026-09-20-ibkr-bot-api-design.md` pour le précédent). Dupliquer la lecture de leurs fichiers ici recréerait un deuxième chemin de lecture pour la même donnée, avec le risque de diverger. Ce nouveau service est un CLIENT authentifié de ces deux API, pas une troisième source.

## 4. Le service `assistant_ia/api.py`

Structure calquée sur `gold_bot/api.py`/`ibkr_bot/api.py` : FastAPI, `docs_url=None`, CORS restreint à l'origine du site, aucune route de mutation.

```python
# assistant_ia/api.py
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

@app.post("/ask")
async def ask(request: AskRequest, x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)  # identique a gold_bot.api._check_token
    _check_daily_budget()      # voir section 8
    return StreamingResponse(run_assistant_loop(request), media_type="text/event-stream")
```

`AskRequest` : `{ "question": str, "history": list[dict], "manual_positions": list[dict] | None }`. `history` est la conversation complète telle que gardée côté navigateur (voir §7) ; `manual_positions` n'est envoyé que si la question porte potentiellement sur le portefeuille (le frontend décide, ou l'envoie systématiquement — à trancher en implémentation, impact mineur sur la taille de la requête).

**Déploiement** : nouveau service systemd `assistant-ia-api.service`, port **8445** (8443 Bot Or, 8444 Bot Actions déjà pris), même certificat TLS que les deux autres (`goldbot.fr`, un certificat couvre tous les ports du domaine). Jeton dédié `AI_ASSISTANT_API_TOKEN`, clé `ANTHROPIC_API_KEY`, et les jetons des deux autres API (`BOT_API_TOKEN`, `IBKR_BOT_API_TOKEN`) dans le `.env` de ce nouveau service — aucun ne doit être réutilisé entre services (même principe que la génération séparée des jetons Bot Or/Bot Actions).

## 5. Boucle d'outils

Boucle multi-étapes avec `claude-opus-5-5` : envoi de la question + historique + outils → tant que la réponse contient des blocs `tool_use`, exécuter chaque outil et renvoyer les résultats → jusqu'à une réponse texte finale sans nouvel appel d'outil. **Plafond de 6 itérations** : au-delà, l'assistant répond avec ce qu'il a et une note explicite ("je n'ai pas pu rassembler toute l'information nécessaire, essaie de reformuler en plusieurs questions plus précises") plutôt que de boucler indéfiniment.

Notes d'implémentation spécifiques à `claude-opus-5-5` (API réelle, pas supposée) :
- `thinking` ne peut pas être désactivé sur ce modèle (`{"type": "disabled"}` renvoie 400 à tout niveau d'`effort`) — c'est voulu ici (on veut le raisonnement), `effort: "high"` explicite (le défaut du modèle est `medium`, on le relève puisque la qualité de l'argumentaire prime).
- `tool_choice: {"type": "any"}` / `{"type": "tool", ...}` renvoie 400 sur ce modèle — utiliser `"auto"` avec une instruction explicite dans le prompt système plutôt qu'un choix forcé.
- Utiliser `client.messages.stream(...)` (pas l'appel non-streamé) pour le texte final, avec `eager_input_streaming` sur les outils pour un retour plus rapide pendant les étapes intermédiaires.
- Cache de prompt (`cache_control: {"type": "ephemeral"}`) sur le prompt système + la définition des outils (statiques, jamais modifiés en cours de conversation) — réduit le coût du texte répété à chaque requête.

## 6. Outils

Chaque outil est une fonction Python pure (entrée → sortie), testable isolément sans toucher à Claude.

- **`fiche_entreprise(ticker)`** — cherche `ticker` dans `docs/indices.json` (cache ~10 min). Renvoie score, facteurs détaillés, interprétation, stage Weinstein, alertes actives, badge Graham, séquence de dividendes. Introuvable → renvoie les 3 noms les plus proches (correspondance floue sur nom/ticker) pour que Claude puisse proposer un choix via `proposer_lien` plutôt que d'inventer.
- **`comparer_entreprises(ticker1, ticker2)`** — même source, les deux fiches complètes côte à côte (pas un résumé — Claude a besoin du détail factoriel pour argumenter).
- **`classement(indice=None, secteur=None, n=10)`** — top/bottom N par score, filtré optionnellement.
- **`statut_bot(nom)`** — `nom` ∈ `{"or", "actions"}`. Appelle `/dashboard` (ou l'équivalent public si pas de jeton bot configuré) du bot concerné, renvoie les champs opérationnels (mode réel/simulation, dernière exécution, erreurs récentes) — PAS les positions (outil séparé ci-dessous, pour garder chaque outil mono-responsabilité).
- **`positions_bot(nom)`** — même source, renvoie la liste des positions ouvertes (pour `ibkr_bot`, inclut déjà les champs P&L en euros livrés le 2026-10-03, voir [[project_ibkr_fx_pnl]]).
- **`resume_portefeuille(positions_manuelles)`** — combine `positions_manuelles` (reçues du navigateur, seule donnée qui ne vit que côté client) avec `positions_bot("or")` et `positions_bot("actions")` pour une vue d'ensemble (nombre de positions, répartition, pas un recalcul des métriques de risque déjà affichées ailleurs sur le site — un résumé conversationnel, pas un doublon de `portfolio.js`).
- **`proposer_lien(cible, libelle)`** — pas de lecture de donnée, enregistre juste une suggestion. `cible` = `{"type": "ticker"|"section", "valeur": str}`. Utilisé aussi bien pour les suggestions de navigation que pour les choix de désambiguïsation ("vouliez-vous dire A ou B ?") — même mécanique dans les deux cas, pas de système séparé.

Toutes les suggestions `proposer_lien` d'un même échange sont collectées côté serveur et renvoyées ensemble à la fin (elles ne peuvent pas vraiment "streamer" utilement), séparées du texte argumenté.

## 7. Conversation et état

Stateless côté serveur : le navigateur garde l'historique complet de la conversation en mémoire (pas de persistance — fermer l'onglet perd la conversation, comportement accepté, cohérent avec l'absence de compte utilisateur côté site). Chaque requête envoie l'historique complet, comme tout appel à l'API Messages.

## 8. Garde-fou de coût

Fichier `assistant_ia/usage_today.json` (réinitialisé à minuit UTC, même logique que les fichiers journaliers existants du projet) : accumule le coût estimé de chaque appel (`input_tokens × prix_entrée + output_tokens × prix_sortie`, prix en dur dans le code — pas d'appel réseau pour les connaître). Avant chaque nouvelle requête, si le total du jour dépasse un plafond configurable (défaut 5$/jour), renvoyer une erreur claire ("plafond quotidien de l'assistant atteint, réessaie demain") plutôt que d'appeler l'API. Le plafond protège contre un bug côté client qui boucle les appels, pas contre un usage normal — l'utilisateur peut l'augmenter librement puisque c'est son propre budget.

## 9. Frontend

Barre de recherche sur l'écran d'accueil (étend la barre "Rechercher" déjà en place). Filtre instantané côté client en premier (fuzzy-match sur `indicesData.companies` + noms de sections connus) ; si rien ne matche avec confiance, bascule en panneau de conversation qui s'ouvre sous la barre (pas une nouvelle page). Chaque réponse s'affiche en bulle de texte argumenté, suivie d'une rangée de boutons cliquables pour les liens suggérés ou les choix de désambiguïsation — cliquer sur un choix renvoie automatiquement la question précisée sans retaper. Le texte streame au fur et à mesure ; les boutons arrivent une fois la boucle d'outils terminée.

Jeton stocké dans `localStorage` sous une clé dédiée (`aiAssistantToken`), demandé une seule fois via `prompt()`, même pattern que `goldBotToken`/`ibkrBotToken`.

## 10. Gestion des pannes

- Backend inatteignable (réseau, service down) → message d'erreur dans la bulle, la recherche instantanée reste utilisable indépendamment.
- Un outil échoue (ticker introuvable, fichier source temporairement indisponible) → résultat d'erreur renvoyé à Claude comme résultat d'outil (`is_error: true`), jamais une exception qui casse tout l'échange — c'est à Claude de décider quoi en dire à l'utilisateur.
- Plafond de boucle d'outils atteint (6) → réponse best-effort + note explicite.
- Plafond de coût quotidien atteint → erreur claire avant tout appel à l'API.
- Jeton absent/invalide → 401, même garde-fou à échec fermé et comparaison à temps constant que les API existantes.

## 11. Tests

Client Anthropic simulé dans les tests (jamais d'appel réel pendant `pytest`) — injection par paramètre, même pattern que `gw` dans `ibkr_bot`/`gold_bot`. Couverture attendue : dispatch de chaque outil vers la bonne fonction Python avec les bons arguments ; la boucle s'arrête correctement sur une réponse finale sans outil ; plafond de 6 itérations respecté ; collecte correcte des appels `proposer_lien` à travers plusieurs tours ; `resume_portefeuille` fusionne correctement positions manuelles + positions des bots ; dégradation propre quand un fichier source est absent/corrompu ; 401 sans jeton ou avec un mauvais jeton ; plafond de coût quotidien respecté et réinitialisé le jour suivant.

## 12. Points arbitrés

- **Modèle : `claude-opus-5-5`**, décision explicite de l'utilisateur après avoir vu le coût estimé (~30-75$/mois pour un usage personnel de 20-50 questions/jour) — la qualité de l'argumentaire prime sur le coût pour cette fonctionnalité.
- **Lecture des fichiers publics plutôt qu'un checkout VPS local** — découple ce service de tout déploiement de bot particulier, source de vérité = ce qui est réellement publié.
- **Client authentifié des API Bot Or/Bot Actions existantes plutôt que nouvelle lecture directe** — évite un deuxième chemin de lecture pour la même donnée.
- **Pas de persistance serveur de conversation** — cohérent avec l'absence de backend stateful ailleurs dans le projet ; accepté que fermer l'onglet perde l'historique.
- **`proposer_lien` sert à la fois les suggestions de navigation ET la désambiguïsation** — un seul mécanisme, pas deux.

## 13. Hors périmètre, explicitement différé

- Connaissance externe (actualités, prévisions) — nécessiterait une source de données entièrement nouvelle, jamais évaluée.
- Accès public sans jeton, ou avec plafond de débit — reporté, V1 est strictement personnel.
- Export fiscal, screener sauvegardé, multi-utilisateurs — items distincts du backlog, sans lien avec cette fonctionnalité.
