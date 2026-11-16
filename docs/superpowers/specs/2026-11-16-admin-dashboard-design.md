# Dashboard d'administration (membres) — Design

## Contexte et objectif

L'espace membre (`compte_api`) vient d'être déployé. L'administrateur
(l'unique compte `role="admin"` aujourd'hui) n'a aucun moyen de voir qui
s'inscrit, combien de membres existent, ou qui est actuellement connecté —
sans ça, il faudrait installer DBeaver et un tunnel SSH (`deploy/README-compte.md`
§12), jamais fait à ce jour. Ce chantier ajoute une vue en lecture seule,
réservée à l'administrateur, directement dans le site.

**Hors périmètre** : pas d'édition (changer un rôle, désactiver un
compte...), pas de fiche détaillée par membre (favoris, portefeuille,
alertes individuels) — juste la liste des membres et des compteurs
globaux, pour commencer.

## Ce qui existe déjà et sera réutilisé

- `compte_api/deps.py::current_admin` : dépendance FastAPI qui renvoie
  403 si `user.role != "admin"`. Déjà utilisée nulle part encore, mais
  prête.
- `compte_api/models.py::User` : `email`, `display_name`, `role`,
  `email_verified_at`, `created_at`, `last_login_at`, `deleted_at`.
- `compte_api/models.py::Session` : `user_id`, `last_seen_at` (rafraîchi
  au plus toutes les 10 minutes par requête, voir `deps.py`,
  `_RAFRAICHISSEMENT_SESSION`), `expires_at`.
- Le panneau « Mon compte » (`docs/compte/compte.js`, fonction
  `Compte.ouvrirMonCompte(onglet?)`) : panneau latéral à onglets
  (`profil`, `suivis`, `alertes`, `historique`, `donnees`), plein écran
  sur mobile. Déjà gardé par condition sur `C.ouvrirMonCompte` existant
  (`docs/compte/auth.js`).
- `window.Compte.fetch(chemin, options)` (`docs/compte/api.js`) : client
  HTTP vers `/api/compte/...`, gère déjà le cookie de session, l'en-tête
  anti-CSRF, et l'erreur normalisée (`CompteErreur`).
- La réponse de `/me` expose déjà `role` au front (vu dans
  `tests/compte_api/test_auth.py`), donc le front sait déjà si le membre
  connecté est administrateur, sans appel supplémentaire.

## Définition de « connecté maintenant »

Une session (cookie de 30 jours) n'implique pas une activité récente.
« Connecté maintenant » = au moins une ligne `Session` du membre dont
`last_seen_at` est dans les **15 dernières minutes** (marge au-dessus
du rafraîchissement de 10 min, pour ne pas rater une activité qui vient
d'avoir lieu juste avant le dernier rafraîchissement).

## Backend

Nouveau fichier `compte_api/routers/admin.py`, deux routes, toutes deux
derrière `Depends(current_admin)` :

### `GET /admin/stats`

Compteurs globaux, sur les membres non supprimés (`deleted_at IS NULL`) :

```json
{
  "total_membres": 1,
  "verifies": 1,
  "connectes_maintenant": 1
}
```

### `GET /admin/users`

Liste des membres non supprimés, triés par `created_at` décroissant
(le plus récent en premier) :

```json
[
  {
    "id": 1,
    "email": "alexandre.auquiere@gmail.com",
    "display_name": null,
    "role": "admin",
    "email_verified": true,
    "created_at": "2026-11-16T14:27:18Z",
    "last_login_at": "2026-11-16T15:23:01Z",
    "connecte": true
  }
]
```

`connecte` : vrai si ce membre a une session avec `last_seen_at` dans
les 15 dernières minutes (même règle que `/admin/stats`). Une seule
requête SQL avec une sous-jointure sur `sessions`, pas une requête par
membre.

Adresses e-mail en clair dans la réponse : la route n'est accessible
qu'à l'administrateur (403 sinon), donc pas de masquage nécessaire ici
(contrairement aux journaux, qui ne logguent jamais une adresse en
clair).

### Tests

`tests/compte_api/test_admin.py`, sur le modèle des autres routeurs :

- 403 pour un membre non-admin, 401 sans session.
- `/admin/stats` : compteurs corrects avec 0, 1, puis plusieurs membres ;
  un membre supprimé (`deleted_at` renseigné) n'est compté nulle part.
- `/admin/users` : champs renvoyés corrects, tri par date décroissante,
  un membre supprimé absent de la liste.
- `connecte` / `connectes_maintenant` : vrai avec une session dont
  `last_seen_at` a 10 minutes, faux à 20 minutes — test au niveau de la
  limite (15 min).

## Frontend

Nouvel onglet `'admin'` dans le panneau existant (`docs/compte/compte.js`) :

- Ajouté à la liste `ONGLETS` **seulement si** `membre.role === 'admin'`
  (comme le bouton « Renvoyer l'e-mail » n'apparaît que si
  `!membre.email_verified` dans `auth.js` — même logique conditionnelle).
- Contenu de l'onglet : les 3 compteurs de `/admin/stats` en haut
  (style reprenant les tuiles déjà utilisées ailleurs sur le site,
  ex. `.home-tile`), puis un tableau simple (e-mail, nom, rôle, vérifié,
  inscrit le, dernière connexion, un point vert si `connecte`).
- Appels : `window.Compte.fetch('/admin/stats')` et
  `window.Compte.fetch('/admin/users')` au chargement de l'onglet (pas
  au chargement du panneau entier, pour ne pas faire ces deux appels à
  chaque membre qui ouvre juste son profil).
- Pas de rafraîchissement automatique (pas de `setInterval`) pour cette
  première version — un bouton « Actualiser » suffit, cohérent avec le
  reste du panneau (ex. `ibkrBotDashboardRefreshBtn` sur le tableau de
  bord IBKR).

## Déploiement

Même chemin que le reste de `compte_api` : push sur `main` touchant
`compte_api/**` déclenche automatiquement `deploy-compte.yml`
(mis en place aujourd'hui). Le front (`docs/compte/compte.js`) est
servi par Vercel, qui se redéploie aussi automatiquement sur push.
Aucune migration de base nécessaire (aucune nouvelle table/colonne).
