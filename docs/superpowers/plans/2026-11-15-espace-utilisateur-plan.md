# Espace utilisateur — plan d'implémentation

Spec : `docs/superpowers/specs/2026-11-12-espace-utilisateur-design.md`.
Branche : `bdd`. Tout se développe et se teste en local ; la mise en ligne
(tâche 10) est la seule à toucher Vercel, Google Cloud et le VPS.

## Organisation

| Vague | Tâches | Mode |
|---|---|---|
| 1 | T0 Socle | Séquentiel (fixe les contrats communs) |
| 2 | T1 Comptes + Google, T2 Favoris, T3 Portefeuille, T4 Historique + quota IA, T5 Alertes | En parallèle, un agent par tâche, chacun dans son worktree |
| 3 | T6 RGPD, T7 Pages légales, T8 Intégration `index.html`, T9 Revue sécurité | Séquentiel |
| 4 | T10 Mise en ligne | Avec l'utilisateur |

## Contraintes globales (toutes les tâches)

- **Ne jamais modifier** `gold_bot/`, `ibkr_bot/`, `shared_risk.py`, les
  workflows existants ni les fichiers `docs/*.json`. Seule exception
  prévue : T4 ajoute une option à `assistant_ia/api.py` (voir T4).
- **Une tâche = ses fichiers.** Une tâche de la vague 2 ne modifie ni
  `compte_api/models.py`, ni la migration Alembic, ni `docs/index.html`.
  Un besoin de colonne ou de table en plus se signale dans le rapport de
  fin de tâche, il ne s'ajoute pas en douce.
- **Interface du site :** chaque fonctionnalité vit dans son propre fichier
  `docs/compte/<nom>.js`, qui exporte une fonction `monter<Nom>(racine)` et
  n'écrit dans le DOM que sous `racine`. Le branchement dans `index.html`
  est fait une seule fois, en T8.
- **Style :** commentaires en français, même ton que le reste du dépôt ;
  messages d'erreur destinés à l'utilisateur en français clair.
- **Tests :** `pytest` dans `tests/compte_api/`, base SQLite temporaire,
  jamais de réseau réel (SMTP, Google, assistant IA simulés). Chaque
  route : un test du cas nominal, un test sans session (401), un test de
  données invalides (422/400), un test d'isolation (un membre ne voit
  jamais les données d'un autre).
- **Environnement local :** `python -m venv .venv-compte`, puis
  `pip install -r compte_api/requirements.txt`. `.venv-compte/` est ignoré
  par Git.
- **Sécurité :** jamais de mot de passe, de jeton ni d'e-mail complet dans
  les journaux ; jetons stockés hachés (SHA-256) ; comparaisons à temps
  constant.

## T0 — Socle (séquentiel)

**Fichiers**

```
compte_api/
  __init__.py
  requirements.txt     fastapi, uvicorn, sqlalchemy>=2, alembic, argon2-cffi,
                       httpx, pytest (versions figées)
  config.py            Settings lus dans l'environnement (voir ci-dessous)
  db.py                engine, SessionLocal, Base, get_db()
  models.py            TOUTES les tables de la spec §4
  security.py          hash_password/verify_password (Argon2id),
                       nouveau_jeton() -> (clair, hache), hacher_jeton()
  deps.py              current_user, current_user_verified, verifier_csrf
  ratelimit.py         limiteur en mémoire par clé (IP, e-mail), fenêtre glissante
  mailer.py            interface Mailer.envoyer(dest, sujet, texte) ;
                       ConsoleMailer (dev/tests), SmtpMailer (prod)
  app.py               create_app(settings) : middleware d'en-tête secret,
                       routeurs, gestion d'erreurs JSON
  routers/__init__.py
  routers/sante.py     GET /sante -> {"ok": true}
  alembic.ini, alembic/ migration initiale = toutes les tables
docs/compte/
  api.js               compteFetch(chemin, options) : credentials 'same-origin',
                       en-tête X-Requested-With, JSON, erreurs -> {statut, message}
  session.js           état connecté/déconnecté, chargerSession() via /me,
                       événement window 'compte:change' (detail = user ou null)
scripts/dev_compte.py  sert docs/ en local et relaie /api/compte/* vers
                       127.0.0.1:8447 en ajoutant l'en-tête secret (imite Vercel)
tests/compte_api/conftest.py
```

**Configuration (`.env` du service)**

| Variable | Rôle | Défaut dev |
|---|---|---|
| `DATABASE_URL` | Base | `sqlite:///compte.db` |
| `COMPTE_PROXY_SECRET` | En-tête `X-Compte-Proxy` exigé sur toute requête | `dev-secret` |
| `COMPTE_SITE_URL` | URL publique du site (liens des e-mails) | `http://127.0.0.1:8000` |
| `COMPTE_COOKIE_SECURE` | Cookie `Secure` | `false` en dev |
| `COMPTE_MAILER` | `console` ou `smtp` | `console` |
| `SMTP_HOST/PORT/USER/PASSWORD`, `MAIL_FROM` | SMTP existant | — |
| `AI_QUOTA_PAR_JOUR` | Quota IA | `10` |
| `ASSISTANT_IA_URL`, `ASSISTANT_IA_TOKEN` | Relais IA (T4) | — |
| `GOOGLE_CLIENT_ID/SECRET` | OAuth (T1) | — |

**Contrats communs**

- Préfixe des routes côté service : aucun (`/me`, `/favorites`…). Le site
  appelle `/api/compte/me`, réécrit par Vercel ou par `scripts/dev_compte.py`.
- Session : cookie `compte_session` (`HttpOnly`, `SameSite=Lax`, `Secure`
  selon config, 30 jours), valeur = jeton clair ; table `sessions` =
  jeton haché. `current_user` renvoie 401 `{"detail": "Connexion requise."}`.
- Mutations (POST/PUT/PATCH/DELETE) : en-tête `X-Requested-With: compte`
  exigé, sinon 403.
- Erreurs : toujours `{"detail": "<message en français>"}`.
- Fixtures de test fournies : `client` (TestClient avec en-tête secret),
  `membre` (utilisateur confirmé) et `client_membre` (client déjà connecté),
  `autre_membre` pour les tests d'isolation, `mailer` (ConsoleMailer qui
  garde les e-mails envoyés en mémoire).

**Terminé quand :** `pytest tests/compte_api` passe, `uvicorn
compte_api.app:app --port 8447` démarre, `alembic upgrade head` crée la
base, `scripts/dev_compte.py` sert le site et `GET /api/compte/sante`
répond via le relais.

## T1 — Comptes + connexion Google (agent 1)

**Fichiers :** `compte_api/routers/auth.py`, `compte_api/routers/google.py`,
`docs/compte/auth.js`, `tests/compte_api/test_auth.py`, `test_google.py`.

**Routes**

| Route | Entrée | Sortie / effet |
|---|---|---|
| POST `/register` | `{email, password, display_name?, site_web?}` (`site_web` = piège anti-robot, doit être vide) | 201 `{user}` + e-mail de confirmation ; 409 si e-mail pris |
| POST `/verify` | `{token}` | 200, `email_verified_at` rempli ; jeton à usage unique, 24 h |
| POST `/login` | `{email, password}` | 200 `{user}` + cookie ; 401 message unique quel que soit l'échec |
| POST `/logout` | — | 204, session supprimée, cookie effacé |
| POST `/password/forgot` | `{email}` | 202 toujours identique ; e-mail seulement si le compte existe |
| POST `/password/reset` | `{token, password}` | 204 ; toutes les sessions du membre fermées |
| GET `/me` | — | `{id, email, display_name, role, email_verified, quota_ia_restant}` |
| GET `/google/start` | — | 302 vers Google (code + PKCE + `state` en cookie court) |
| GET `/google/callback` | `code, state` | crée ou lie le compte (même e-mail vérifié), pose la session, 302 vers le site |

**Règles :** mot de passe 10 caractères minimum, refus d'une liste de mots
de passe courants (fichier fourni dans le dépôt) ; 5 échecs de connexion
par e-mail ou par IP en 15 min -> 429 ; inscription limitée par IP.
Google testé avec un faux fournisseur injecté (jamais d'appel réel).

**Interface (`auth.js`) :** bouton « Se connecter » / menu du compte,
fenêtre connexion / inscription / mot de passe oublié, page de
confirmation (lien `?verify=`), page de réinitialisation (`?reset=`),
bouton « Continuer avec Google ».

## T2 — Favoris (agent 2)

**Fichiers :** `compte_api/routers/favoris.py`, `docs/compte/favoris.js`,
`tests/compte_api/test_favoris.py`.

**Routes :** GET `/favorites` -> `[{kind, key, created_at}]` ; PUT
`/favorites/{kind}/{key}` (idempotent) ; DELETE `/favorites/{kind}/{key}`.
`kind` ∈ `index`, `company` ; `key` = clé d'indice (`CAC40`) ou ticker,
validé par une expression (lettres, chiffres, `.`, `-`, `^`, 1 à 20
caractères). 200 favoris maximum par membre.

**Interface :** `monterEtoile(element, kind, key)` (bouton étoile, état
optimiste, retour arrière en cas d'erreur, invite à se connecter si
déconnecté) ; `monterMesSuivis(racine, indicesData)` : liste des favoris
avec score et variation, tirés de `indices.json` déjà chargé.

## T3 — Portefeuille sur le compte (agent 3)

**Fichiers :** `compte_api/routers/portefeuille.py`,
`docs/compte/portefeuille.js`, `tests/compte_api/test_portefeuille.py`.

**Routes :** GET `/positions?statut=ouvertes|cloturees|toutes` ; POST
`/positions` `{ticker, quantity, buy_price, buy_date}` ; PATCH
`/positions/{id}` (mêmes champs) ; PATCH `/positions/{id}/close`
`{sell_price, sell_date}` ; DELETE `/positions/{id}` ; POST `/import`
`{positions: [...], positions_cloturees: [...], historique_ia: [...],
reglages: {...}}` -> `{importees, ignorees}` (doublon = même ticker, date
et quantité).

Mêmes validations que `docs/portfolio.js` (`validatePositionInput`).

**Interface :** adaptateur qui fait lire/écrire le portefeuille par l'API
quand le membre est connecté et par `localStorage` sinon, **sans changer
la signature** des fonctions appelées par `index.html`
(`loadPortfolio`, `savePortfolio`, `loadClosedPortfolio`, `closePosition`…) ;
fenêtre « Importer les données de cet appareil ? » à la première connexion.

## T4 — Historique et quota IA (agent 4)

**Fichiers :** `compte_api/routers/historique.py`, `compte_api/relais_ia.py`,
`docs/compte/historique.js`, `tests/compte_api/test_historique.py` ; option
ajoutée à `assistant_ia/api.py` (avec ses tests dans
`tests/assistant_ia/test_api.py`).

**Routes :** GET/POST/DELETE `/searches` (100 dernières gardées) ; GET/DELETE
`/ai/history` (100 derniers échanges) ; POST `/ai/ask` : vérifie
`current_user_verified` et le quota du jour (`ai_usage`, remise à zéro à
minuit UTC, admin sans quota), relaie la requête en flux SSE vers
`assistant_ia`, enregistre l'échange à la fin du flux, n'incrémente le
quota que si la réponse a réellement commencé. 429 `{"detail": ...,
"quota_restant": 0}` quand le quota est atteint.

**Point d'attention :** `assistant_ia` limite la fréquence par adresse IP
(`request.client.host`). Derrière le relais, tous les membres
partageraient l'IP du service compte. Ajouter à `assistant_ia/api.py` un
en-tête `X-Compte-User` pris en compte **uniquement** quand la requête
porte un jeton de service dédié (`AI_ASSISTANT_SERVICE_TOKEN`), qui sert
alors de clé de limitation à la place de l'IP. Sans ce jeton, comportement
actuel inchangé.

## T5 — Alertes e-mail (agent 5)

**Fichiers :** `compte_api/routers/alertes.py`, `compte_api/jobs/alertes.py`,
`docs/compte/alertes.js`, `tests/compte_api/test_alertes.py`.

**Routes :** GET/POST/PATCH/DELETE `/alerts` ; règles `{kind:
score_above|score_below|entry_signal, target, threshold?, active}`, 50
maximum par membre ; GET `/alerts/unsubscribe?token=...` (lien signé
HMAC, sans session, désactive les e-mails d'alerte du membre).

**Tâche planifiée** (`python -m compte_api.jobs.alertes detecter|resume`) :
- `detecter` : télécharge `indices.json` publié, compare aux valeurs de la
  détection précédente (instantané stocké en base), enregistre chaque
  franchissement dans `alert_events` (une seule fois par franchissement,
  pas à chaque passage tant que la valeur reste au-delà du seuil) ;
- `resume` : un seul e-mail par membre et par jour regroupant ses
  événements non envoyés, avec lien de désinscription ; seulement pour les
  comptes confirmés.

Fichiers systemd fournis dans `deploy/` (`compte-alertes.service` +
`.timer`), non installés.

## T6 — RGPD (séquentiel, après la vague 2)

GET `/export` (JSON de toutes les données du membre, toutes tables) ;
DELETE `/account` (`{password}` ou reconnexion Google récente exigée),
suppression en cascade, e-mail de confirmation. Interface dans
`docs/compte/compte.js` (page « Mon compte »).

## T7 — Pages légales (séquentiel)

`docs/legal/confidentialite.html`, `docs/legal/mentions-legales.html` :
données collectées, finalités, durée de conservation, droits (export,
suppression), hébergeurs (Vercel, VPS), contact. Les informations
d'identité de l'éditeur restent en champs à compléter par l'utilisateur.

## T8 — Intégration dans `index.html` (séquentiel)

Charger `docs/compte/*.js`, placer le bouton de connexion dans la barre du
haut et la barre du bas, les étoiles sur les listes d'indices et les
fiches, « Mes suivis » sur l'accueil, l'adaptateur de portefeuille,
l'historique et le quota dans l'assistant, les alertes dans « Mon compte ».
Ajouter `vercel.json` (rewrite `/api/compte/:path*` vers le VPS avec
l'en-tête secret). Monter `CACHE_NAME` du service worker.

## T9 — Revue sécurité (séquentiel)

Relecture de l'ensemble : authentification, sessions, CSRF, limites,
isolation entre membres, fuites dans les journaux, en-tête secret,
cookies. Tests de bout en bout via `scripts/dev_compte.py`.

**Fait (2026-11-15).** Aucune fuite entre membres trouvée (chaque route
filtre sur le membre connecté, un objet d'un autre répond 404). Corrigé :

- Liaison Google sur un compte jamais confirmé : le mot de passe est
  effacé et les sessions fermées (sinon la personne qui avait inscrit
  l'adresse avant son titulaire gardait l'accès).
- Désinscription des alertes : le lien affiche une page de confirmation,
  la désinscription se fait au clic (POST). Les antivirus de messagerie
  qui ouvrent les liens ne désinscrivent plus personne.
- Démarrage refusé en production avec un réglage de développement
  (secret du proxy par défaut ou trop court, cookie sans Secure, e-mails
  en console).
- `Cache-Control: no-store`, `nosniff` et `Referrer-Policy` sur toutes les
  réponses ; une connexion ferme la session précédente du navigateur.

Écarts assumés par rapport à la spec : `/password/reset` répond 200 avec
le membre (connexion directe) ; « Mes suivis » n'affiche pas de variation
de score (`indices.json` n'en fournit pas) ; pas de délai minimal de
saisie anti-robot (contournable, le champ piège et les limites par IP
suffisent) ; `/register` dit si l'adresse est déjà prise (choix de
confort, limité à 5 essais par heure et par IP).

À vérifier en T10 : Vercel doit remplacer `X-Forwarded-For` par l'IP
réelle (sinon les limites par IP se contournent) ; les API du VPS
n'autorisent en CORS que `alexandreauq.github.io`, à compléter avec le
domaine Vercel.

## T10 — Mise en ligne (avec l'utilisateur)

Projet Vercel, application Google OAuth, service systemd `compte-api` et
minuteur des alertes sur le VPS, sauvegarde quotidienne de la base,
vérification que les cookies traversent bien le rewrite Vercel (repli
`app.goldbot.fr` / `api.goldbot.fr` sinon), essai complet en ligne.

Préparé (2026-11-15) : la production passe sur **PostgreSQL**, installé
sur le VPS (accès à la base par DBeaver à travers un tunnel SSH). Le VPS
est administré par un collaborateur : le guide pas à pas est
`deploy/README-compte.md`, avec les unités `compte-api.service`,
`compte-sauvegarde.{service,timer}` et le script `deploy-compte.sh`.
Les 325 tests passent sur SQLite et sur PostgreSQL 17
(`COMPTE_TEST_DATABASE_URL`) ; seule correction nécessaire : connexions
PostgreSQL forcées en UTC (`compte_api/db.py`). Migration Alembic et
parcours complet dans le navigateur vérifiés sur PostgreSQL.
