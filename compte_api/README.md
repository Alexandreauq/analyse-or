# compte_api — service des comptes utilisateurs

Spec : `docs/superpowers/specs/2026-11-12-espace-utilisateur-design.md`
Plan : `docs/superpowers/plans/2026-11-15-espace-utilisateur-plan.md`

## Lancer en local

```bash
python -m venv .venv-compte
.venv-compte/Scripts/python -m pip install -r compte_api/requirements.txt   # Windows
cd compte_api && ../.venv-compte/Scripts/alembic upgrade head && cd ..      # crée compte.db
.venv-compte/Scripts/uvicorn compte_api.app:app --port 8447                 # terminal 1
python scripts/dev_compte.py                                                # terminal 2
```

Le site est alors sur http://127.0.0.1:8000, et `/api/compte/*` est relayé
vers le service avec l'en-tête secret, comme le fera Vercel. En local, les
e-mails s'affichent dans le terminal du service.

## Production

Dès que `COMPTE_SITE_URL` n'est pas une adresse locale, le service refuse
de démarrer sans ces trois réglages dans son `.env` :

- `COMPTE_PROXY_SECRET` : 32 caractères minimum (même valeur dans Vercel) ;
- `COMPTE_COOKIE_SECURE=true` ;
- `COMPTE_MAILER=smtp` (sinon les liens de connexion iraient dans les journaux).

Conseillé : `COMPTE_UNSUBSCRIBE_SECRET`, distinct du secret du proxy.

## Tests

```bash
.venv-compte/Scripts/python -m pytest tests/compte_api
```

Fixtures disponibles (`tests/compte_api/conftest.py`) : `client` (anonyme),
`membre` + `client_membre` (connecté, adresse confirmée), `autre_membre` +
`client_autre` (pour les tests d'isolation), `creer_membre(...)`,
`connecter(user)`, `mailer` (e-mails gardés dans `mailer.envoyes`), `db`.

## Ajouter une fonctionnalité

- Un fichier `compte_api/routers/<nom>.py` qui expose `router` : il est
  inclus automatiquement, sans toucher à `app.py`.
- Dépendances : `get_db`, `get_settings`, `get_mailer`, `current_user`,
  `current_user_verified`, `current_admin` (`compte_api/deps.py`).
- Sessions : `compte_api/sessions.py` ; jetons et mots de passe :
  `compte_api/security.py` ; limites : `compte_api/ratelimit.py`.
- Ne pas modifier `models.py` ni la migration sans coordination : toutes
  les tables existent déjà.
- Interface du site : `docs/compte/<nom>.js`, qui s'appuie sur
  `window.Compte.fetch` (`docs/compte/api.js`) et l'état de connexion
  (`docs/compte/session.js`, événement `compte:change`).

## Règles communes

- Toute requête doit porter l'en-tête `X-Compte-Proxy` (posé par le proxy).
- Toute écriture doit porter `X-Requested-With: compte` (posé par
  `window.Compte.fetch`). Seule exception : `POST /alerts/unsubscribe`
  (formulaire HTML, protégé par son jeton signé).
- Les réponses partent avec `Cache-Control: no-store` sauf choix contraire
  de la route.
- Erreurs : toujours `{"detail": "<message en français>"}`.
