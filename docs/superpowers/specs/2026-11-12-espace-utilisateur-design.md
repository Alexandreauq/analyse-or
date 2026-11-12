# Espace utilisateur (comptes, favoris, portefeuille, historique, alertes) — design

Branche : `bdd`. Statut : proposition, à valider avant le plan d'implémentation.

## 1. Objectif

Ouvrir le site au public avec un vrai compte utilisateur, pour que les
données personnelles suivent le membre d'un appareil à l'autre au lieu de
vivre dans le `localStorage` du navigateur.

Version 1 :

- inscription et connexion (e-mail + mot de passe, ou Google) ;
- favoris : indices et entreprises ;
- portefeuille Actions (positions ouvertes et clôturées) ;
- historique des recherches et des conversations avec l'assistant IA ;
- alertes e-mail sur les favoris ;
- quota gratuit d'assistant IA par compte.

Un visiteur non connecté voit tout le contenu public (scores, commodités,
indices, fiches entreprise). Favoris, portefeuille, historique, alertes et
assistant IA demandent un compte.

## 2. Décisions déjà prises

| Sujet | Décision |
|---|---|
| Public | Inscription ouverte à tous |
| Hébergement du site | Vercel, offre Hobby (gratuite) |
| API et base | Nouveau service FastAPI sur le VPS `goldbot.fr` |
| Base de données | SQLite (un fichier) sur le VPS ; code compatible PostgreSQL (SQLAlchemy + Alembic), bascule par une variable `DATABASE_URL` |
| Connexion | E-mail + mot de passe, plus « Continuer avec Google » |
| E-mails | SMTP existant des bots, alertes en résumé quotidien |
| Assistant IA | Quota gratuit par compte et par jour |

## 3. Architecture

```
Navigateur ──► Vercel (site statique, docs/)
                 │  rewrite /api/compte/* ──► https://goldbot.fr:8447/*
                 │  (en-tête secret X-Vercel-Proxy ajouté par Vercel)
                 ▼
            VPS : service compte (FastAPI + SQLite)
                 ├── lit indices.json / score*.json publiés (alertes)
                 ├── SMTP (confirmation, mot de passe oublié, alertes)
                 └── appelle assistant_ia (quota vérifié avant)
```

- **Même origine.** Le site appelle `/api/compte/...` sur son propre
  domaine Vercel ; Vercel relaie vers le VPS. Le navigateur ne voit qu'un
  site : la session tient dans un cookie `HttpOnly; Secure; SameSite=Lax`,
  illisible par le JavaScript de la page (les scripts TradingView ne
  peuvent pas le voler). Aucun problème de cookies tiers sur Safari.
- **Origine verrouillée.** Le service refuse toute requête sans l'en-tête
  secret ajouté par la règle de rewrite Vercel : on ne peut pas
  contourner le site en appelant le VPS directement.
- **Données publiques inchangées.** Les fichiers `docs/*.json` restent
  servis statiquement ; seuls les appels de compte passent par le VPS.
- **Service séparé.** Nouvel utilisateur système et nouveau service
  systemd `compte-api`, port 8447, comme `assistant-ia-api`. Une panne du
  service compte ne touche jamais les bots de trading.

À vérifier pendant l'implémentation : que les en-têtes `Set-Cookie` et
`Cookie` traversent bien un rewrite externe Vercel (documentation non
explicite sur ce point). Repli si ce n'est pas le cas : sous-domaine
personnalisé `app.goldbot.fr` sur Vercel et API sur `api.goldbot.fr`,
même site au sens des cookies.

## 4. Modèle de données (SQLite)

```
users            id, email (unique, minuscules), email_verified_at,
                 password_hash (null si compte Google seul), google_sub (unique, null),
                 display_name, role ('member' | 'admin'), created_at,
                 last_login_at, deleted_at
sessions         id_hash (jamais le jeton en clair), user_id, created_at,
                 expires_at, last_seen_at, user_agent
email_tokens     token_hash, user_id, purpose ('verify' | 'reset'),
                 expires_at, used_at
favorites        user_id, kind ('index' | 'company'), key, created_at
                 clé primaire (user_id, kind, key)
positions        id, user_id, ticker, quantity, buy_price, buy_date,
                 sell_price (null), sell_date (null), created_at
searches         id, user_id, query, created_at          (100 dernières gardées)
ai_exchanges     id, user_id, question, answer, created_at (100 derniers gardés)
ai_usage         user_id, day, count                       clé (user_id, day)
alert_rules      id, user_id, kind ('score_above' | 'score_below' | 'entry_signal'),
                 target (ticker ou indice), threshold (null pour entry_signal),
                 active, created_at
alert_events     id, rule_id, triggered_at, value, emailed_at (null tant que non envoyé)
settings         user_id, json (thème, niveau de réponse IA, filtres sauvegardés)
```

`positions` reprend exactement les champs de `docs/portfolio.js`
(`ticker`, `quantity`, `buy_price`, `buy_date`, puis `sell_price` et
`sell_date` à la clôture) : une position clôturée est la même ligne avec
ses deux champs de vente remplis.

## 5. API (préfixe `/api/compte`)

| Méthode | Route | Rôle |
|---|---|---|
| POST | `/register` | Création (e-mail, mot de passe), envoie l'e-mail de confirmation |
| POST | `/verify` | Valide le lien de confirmation |
| POST | `/login` | Connexion e-mail + mot de passe, pose le cookie de session |
| GET | `/google/start` | Redirige vers Google (code + PKCE + `state`) |
| GET | `/google/callback` | Crée ou lie le compte, pose le cookie |
| POST | `/logout` | Supprime la session |
| POST | `/password/forgot` | Envoie le lien de réinitialisation (réponse identique que l'e-mail existe ou non) |
| POST | `/password/reset` | Change le mot de passe, ferme toutes les sessions |
| GET | `/me` | Profil, rôle, quota IA restant |
| GET/PUT/DELETE | `/favorites` | Liste, ajout, retrait |
| GET/POST/PATCH/DELETE | `/positions` | Portefeuille ; PATCH `/positions/{id}/close` pour clôturer |
| GET/POST/DELETE | `/searches` | Historique des recherches |
| GET/DELETE | `/ai/history` | Historique des échanges IA |
| POST | `/ai/ask` | Vérifie le quota, relaie vers `assistant_ia`, enregistre l'échange |
| GET/POST/PATCH/DELETE | `/alerts` | Règles d'alerte |
| GET/PUT | `/settings` | Préférences |
| POST | `/import` | Import unique du `localStorage` (voir §7) |
| GET | `/export` | Export RGPD de toutes les données du compte (JSON) |
| DELETE | `/account` | Suppression du compte et de toutes ses données |

Toute route de mutation exige la session et un en-tête anti-CSRF
(`X-Requested-With`) ; le cookie `SameSite=Lax` complète la protection.

## 6. Sécurité (site public)

- Mots de passe hachés avec **Argon2id** ; 10 caractères minimum ; refus
  des mots de passe les plus courants.
- **Limitation de débit** par IP et par e-mail sur `/login`, `/register`
  et `/password/forgot` ; blocage temporaire après 5 échecs.
- **Anti-robots** sur l'inscription : champ piège invisible et délai
  minimal de remplissage (pas de service tiers en V1).
- Jetons de session et d'e-mail : aléatoires 256 bits, stockés **hachés**,
  durée de vie 30 jours (session) et 1 heure (réinitialisation).
- Compte non confirmé : connexion possible mais ni alertes ni IA.
- **Séparation stricte admin / membre.** Les tableaux de bord Bot Or, FTMO,
  Bot Actions et leurs boutons Arrêter/Relancer restent protégés par leurs
  jetons actuels en V1, jamais exposés à un membre. Le rôle `admin`
  pourra les remplacer en V2.
- Journal des connexions et des erreurs, sans mot de passe ni jeton.

## 7. Migration des données du navigateur

À la première connexion sur un appareil qui a des données locales, le site
propose « Importer mes données de cet appareil ? » :

| Clé `localStorage` | Destination |
|---|---|
| `analyse-or-portfolio` | `positions` (ouvertes) |
| `analyse-or-portfolio-closed` | `positions` (clôturées) |
| `analyse-or-assistant-history` | `ai_exchanges` (`question`, `reponse`, `date`) |
| `analyse-or-saved-screeners` | `settings.json` |
| `analyse-or-answer-level`, `theme` | `settings.json` |

Les doublons (même ticker, même date et même quantité) sont ignorés. Les
clés locales restent intactes après import, pour pouvoir revenir en
arrière ; un visiteur non connecté continue d'utiliser le `localStorage`
comme aujourd'hui.

## 8. Assistant IA et quota

- 10 questions par jour et par compte confirmé (valeur réglable dans le
  `.env` du service) ; compteur remis à zéro à minuit UTC.
- Le quota est vérifié **avant** l'appel à Claude ; le plafond global de
  dépense déjà en place reste actif au-dessus.
- L'admin n'a pas de quota.

## 9. Alertes e-mail

- Types V1 : score d'une entreprise favorite au-dessus ou en dessous d'un
  seuil, activation d'un signal d'entrée.
- Un minuteur systemd sur le VPS lit les fichiers publiés après chaque
  mise à jour (`indices.json`, `score*.json`), compare à la valeur
  précédente et enregistre les franchissements dans `alert_events`.
- **Un seul e-mail par jour et par membre** (résumé), pour rester sous la
  limite d'envoi du SMTP actuel ; lien de désinscription en un clic.
- Seuil d'alerte : à partir de quelques centaines de membres actifs, passer
  à un service d'envoi dédié (Brevo, Resend).

## 10. RGPD et pages légales

- Politique de confidentialité et mentions légales (pages statiques).
- Export et suppression du compte en libre-service (§5).
- Mention « Pas un conseil d'investissement » conservée sur les pages de
  scores.
- Base et sauvegardes uniquement sur le VPS ; aucune donnée personnelle
  dans le dépôt Git public.

## 11. Exploitation

- Sauvegarde quotidienne du fichier SQLite (copie à chaud avec l'API de
  sauvegarde SQLite), 14 jours conservés, copie hors du VPS.
- Hébergement Vercel Hobby : 100 Go de trafic par mois, usage non
  commercial uniquement. `indices.json` (17,7 Mo brut, ~3 Mo compressé)
  est le poste le plus lourd : à découper si le trafic dépasse ~30 000
  chargements par mois.

## 12. Étapes manuelles (hors code)

1. Créer le projet Vercel relié au dépôt (dossier `docs/`), et y définir
   le secret d'en-tête du rewrite.
2. Créer une application OAuth dans Google Cloud (écran de consentement,
   identifiant client, URL de retour sur le domaine Vercel).
3. Sur le VPS : utilisateur système, `.env` du service (secret Vercel,
   identifiants Google, SMTP, quota IA), service systemd et minuteur des
   alertes, ouverture du port 8447.

## 13. Découpage proposé

Tout se développe et se teste en local avant les étapes manuelles du §12 :
service lancé avec `uvicorn` sur le poste, site servi en local avec un
petit proxy `/api/compte` qui imite le rewrite Vercel, e-mails affichés
dans la console, Google simulé dans les tests.

0. Socle : dossier `compte_api/`, configuration par `.env`, SQLAlchemy +
   Alembic, en-tête secret, journalisation, proxy de développement.
1. Comptes : inscription, confirmation, connexion, session, déconnexion,
   mot de passe oublié, `/me`, limitation de débit ; interface de
   connexion/inscription dans le site.
2. Favoris : API + étoile sur les indices et fiches + page « Mes suivis ».
3. Portefeuille sur le compte + import du `localStorage`.
4. Historique recherches/IA + quota IA (relais vers `assistant_ia`).
5. Alertes e-mail : règles, détection des franchissements, résumé
   quotidien, désinscription.
6. Connexion Google (code complet, testé avec un faux fournisseur).
7. Pages légales, export et suppression du compte.
8. Mise en ligne : étapes manuelles du §12, vérification des cookies à
   travers le rewrite Vercel, sauvegardes, essai de bout en bout.

## 14. Hors périmètre V1

Paiement et offre payante, notifications push, partage de portefeuille
entre membres, application mobile, accès admin aux bots par rôle (V2),
historique des trades Bot Or / FTMO enregistré par le bot.
