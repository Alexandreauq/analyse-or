# Dashboard d'administration (membres) — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Donner à l'administrateur (`role="admin"`) une vue en lecture seule, dans le site Nomi, sur les membres inscrits à `compte_api` : compteurs globaux et liste des membres avec leur statut de connexion.

**Architecture:** Deux routes GET dans un nouveau routeur `compte_api/routers/admin.py`, toutes deux derrière la dépendance `current_admin` déjà existante. Le frontend ajoute un onglet conditionnel dans le panneau « Mon compte » existant (`docs/compte/compte.js`), qui appelle ces deux routes au chargement de l'onglet.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (style `Mapped`/`select`), pytest + `TestClient`, JS vanilla (pas de framework, pas de bundler).

**Spec:** `docs/superpowers/specs/2026-11-16-admin-dashboard-design.md`

## Global Constraints

- Toutes les routes admin sont derrière `Depends(current_admin)` (`compte_api/deps.py:66-69`), qui renvoie 403 si `role != "admin"`, et 401 sans session (via `current_user` en amont).
- « Connecté maintenant » = une ligne `Session` du membre avec `last_seen_at` dans les **15 dernières minutes**.
- Tous les comptages/listes portent uniquement sur les membres non supprimés (`User.deleted_at IS NULL`).
- Pas de migration de base de données (aucune nouvelle table/colonne).
- **Pas de tâche d'enregistrement de routeur** : `compte_api/app.py:29-34` (`_routeurs()`) découvre et inclut automatiquement tout module de `compte_api/routers/` qui expose un objet `router` — créer `compte_api/routers/admin.py` avec un `router = APIRouter(...)` suffit, `app.py` n'a pas besoin d'être modifié.
- Déploiement déjà automatique pour les deux bouts : push sur `main` touchant `compte_api/**` déclenche `.github/workflows/deploy-compte.yml` ; le front (`docs/`) est servi par Vercel, qui se redéploie seul sur push.
- Style des routeurs existants (`compte_api/routers/favoris.py`, `compte_api/routers/auth.py`) : réponses `dict`/`list[dict]` faites à la main (pas de `response_model` Pydantic), pas de docstrings au-dessus des routes, messages d'erreur en français.
- SQLite stocke les dates sans fuseau même écrites en UTC : toute date relue depuis la base et renvoyée en JSON doit repasser par un petit helper `_aware()` avant `.isoformat()` (pattern déjà dans `compte_api/deps.py:37-39` et `compte_api/routers/auth.py:115-117`, dupliqué localement à chaque fichier plutôt que partagé — on suit ce pattern).

---

### Task 1: Route `GET /admin/stats`

**Files:**
- Create: `compte_api/routers/admin.py`
- Create: `tests/compte_api/test_admin.py`

**Interfaces:**
- Consumes: `compte_api.deps.current_admin`, `compte_api.deps.get_db` (dépendances FastAPI existantes) ; `compte_api.models.User`, `compte_api.models.Session`, `compte_api.models.maintenant()`.
- Produces: `FENETRE_CONNECTE: timedelta` (15 min, réutilisée par la Task 2) ; `router: APIRouter` avec le préfixe `/admin` (la Task 2 ajoute une route sur ce même `router`).

- [ ] **Step 1: Écrire les tests (ils échoueront : le module n'existe pas encore)**

Crée `tests/compte_api/test_admin.py` :

```python
# Tests du dashboard d'administration (GET /admin/stats, GET /admin/users).
# Spec : docs/superpowers/specs/2026-11-16-admin-dashboard-design.md
from datetime import timedelta

from compte_api import models
from compte_api.sessions import creer_session


def _marquer_last_seen(db, user, il_y_a):
    """Crée (si besoin) une session pour `user` et fixe last_seen_at à il y a `il_y_a`."""
    creer_session(db, user.id, "pytest")
    for session in db.query(models.Session).filter_by(user_id=user.id):
        session.last_seen_at = models.maintenant() - il_y_a
    db.commit()


# --- Accès --------------------------------------------------------------

def test_stats_sans_session_401(client):
    r = client.get("/admin/stats")
    assert r.status_code == 401


def test_stats_non_admin_403(client_membre):
    r = client_membre.get("/admin/stats")
    assert r.status_code == 403


# --- /admin/stats ---------------------------------------------------------

def test_stats_avec_seulement_l_admin(creer_membre, connecter):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    r = client_admin.get("/admin/stats")
    assert r.status_code == 200
    # L'admin vient de se connecter : sa propre session compte comme "connecté".
    assert r.json() == {"total_membres": 1, "verifies": 1, "connectes_maintenant": 1}


def test_stats_plusieurs_membres(creer_membre, connecter):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    creer_membre(verifie=True)
    creer_membre(verifie=False)
    r = client_admin.get("/admin/stats")
    assert r.json()["total_membres"] == 3
    assert r.json()["verifies"] == 2
    assert r.json()["connectes_maintenant"] == 1  # les deux autres n'ont pas de session


def test_stats_membre_supprime_jamais_compte(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    supprime = creer_membre()
    supprime.deleted_at = models.maintenant()
    db.commit()
    _marquer_last_seen(db, supprime, timedelta(minutes=1))  # même connecté, ne doit pas compter
    r = client_admin.get("/admin/stats")
    assert r.json() == {"total_membres": 1, "verifies": 1, "connectes_maintenant": 1}


def test_stats_connecte_maintenant_a_10_minutes(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    membre = creer_membre()
    _marquer_last_seen(db, membre, timedelta(minutes=10))
    r = client_admin.get("/admin/stats")
    assert r.json()["connectes_maintenant"] == 2


def test_stats_pas_connecte_a_20_minutes(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    membre = creer_membre()
    _marquer_last_seen(db, membre, timedelta(minutes=20))
    r = client_admin.get("/admin/stats")
    assert r.json()["connectes_maintenant"] == 1
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `pytest tests/compte_api/test_admin.py -v`
Expected: FAIL — `ModuleNotFoundError` ou 404 sur `/admin/stats` (le routeur n'existe pas).

- [ ] **Step 3: Créer le routeur avec la route `/admin/stats`**

Crée `compte_api/routers/admin.py` :

```python
# compte_api/routers/admin.py
# Vue d'administration en lecture seule (spec
# docs/superpowers/specs/2026-11-16-admin-dashboard-design.md). Réservée au
# rôle admin (compte_api/deps.py::current_admin) : pas d'édition, juste des
# compteurs et la liste des membres.
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.deps import current_admin, get_db

router = APIRouter(prefix="/admin")

# Marge au-dessus du rafraîchissement de session (10 min,
# compte_api/deps.py::_RAFRAICHISSEMENT_SESSION) pour ne pas rater une
# activité qui vient d'avoir lieu juste avant le dernier rafraîchissement.
FENETRE_CONNECTE = timedelta(minutes=15)


def _aware(dt: datetime) -> datetime:
    # SQLite rend des dates sans fuseau même quand on les écrit en UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.get("/stats")
def stats(_admin: models.User = Depends(current_admin), db: DbSession = Depends(get_db)) -> dict:
    seuil = models.maintenant() - FENETRE_CONNECTE
    total_membres = db.scalar(
        select(func.count()).select_from(models.User).where(models.User.deleted_at.is_(None))
    )
    verifies = db.scalar(
        select(func.count()).select_from(models.User)
        .where(models.User.deleted_at.is_(None), models.User.email_verified_at.is_not(None))
    )
    connectes_maintenant = db.scalar(
        select(func.count(func.distinct(models.Session.user_id)))
        .select_from(models.Session)
        .join(models.User, models.User.id == models.Session.user_id)
        .where(models.User.deleted_at.is_(None), models.Session.last_seen_at >= seuil)
    )
    return {
        "total_membres": total_membres,
        "verifies": verifies,
        "connectes_maintenant": connectes_maintenant,
    }
```

- [ ] **Step 4: Lancer les tests, vérifier qu'ils passent**

Run: `pytest tests/compte_api/test_admin.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add compte_api/routers/admin.py tests/compte_api/test_admin.py
git commit -m "feat(compte): ajoute GET /admin/stats pour le dashboard admin"
```

---

### Task 2: Route `GET /admin/users`

**Files:**
- Modify: `compte_api/routers/admin.py` (ajoute la route et son helper de sérialisation)
- Modify: `tests/compte_api/test_admin.py` (ajoute les tests de cette route)

**Interfaces:**
- Consumes: `router`, `FENETRE_CONNECTE`, `_aware()` produits par la Task 1 (même fichier).
- Produces: `GET /admin/users` — `list[dict]` avec les clés `id`, `email`, `display_name`, `role`, `email_verified`, `created_at` (ISO 8601), `last_login_at` (ISO 8601 ou `null`), `connecte` (bool). Rien d'autre ne dépend de cette route dans ce plan.

- [ ] **Step 1: Ajouter les tests (ils échoueront : la route n'existe pas encore)**

Ajoute à la fin de `tests/compte_api/test_admin.py` :

```python
# --- Accès (route /admin/users) -------------------------------------------

def test_users_sans_session_401(client):
    r = client.get("/admin/users")
    assert r.status_code == 401


def test_users_non_admin_403(client_membre):
    r = client_membre.get("/admin/users")
    assert r.status_code == 403


# --- /admin/users -----------------------------------------------------------

def test_users_champs_et_role(creer_membre, connecter):
    admin = creer_membre(role="admin", email="admin@exemple.test", display_name="Admin")
    client_admin = connecter(admin)
    r = client_admin.get("/admin/users")
    assert r.status_code == 200
    corps = r.json()
    assert len(corps) == 1
    ligne = corps[0]
    assert ligne["email"] == "admin@exemple.test"
    assert ligne["display_name"] == "Admin"
    assert ligne["role"] == "admin"
    assert ligne["email_verified"] is True
    assert ligne["connecte"] is True  # l'admin vient de se connecter
    assert ligne["last_login_at"] is None  # creer_membre ne pose pas last_login_at
    from datetime import datetime
    assert datetime.fromisoformat(ligne["created_at"]).tzinfo is not None


def test_users_tri_par_date_decroissante(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    plus_vieux = creer_membre(email="vieux@exemple.test")
    plus_vieux.created_at = models.maintenant() - timedelta(days=2)
    plus_recent = creer_membre(email="recent@exemple.test")
    plus_recent.created_at = models.maintenant() - timedelta(hours=1)
    db.commit()
    r = client_admin.get("/admin/users")
    emails = [m["email"] for m in r.json()]
    # admin.created_at n'a pas été modifié : il reste "maintenant", donc le
    # plus récent des trois.
    assert emails == [admin.email, "recent@exemple.test", "vieux@exemple.test"]


def test_users_membre_supprime_absent(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    supprime = creer_membre(email="supprime@exemple.test")
    supprime.deleted_at = models.maintenant()
    db.commit()
    r = client_admin.get("/admin/users")
    assert [m["email"] for m in r.json()] == [admin.email]


def test_users_connecte_a_10_minutes(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    membre = creer_membre(email="membre@exemple.test")
    _marquer_last_seen(db, membre, timedelta(minutes=10))
    r = client_admin.get("/admin/users")
    ligne = next(m for m in r.json() if m["email"] == "membre@exemple.test")
    assert ligne["connecte"] is True


def test_users_pas_connecte_a_20_minutes(creer_membre, connecter, db):
    admin = creer_membre(role="admin")
    client_admin = connecter(admin)
    membre = creer_membre(email="membre@exemple.test")
    _marquer_last_seen(db, membre, timedelta(minutes=20))
    r = client_admin.get("/admin/users")
    ligne = next(m for m in r.json() if m["email"] == "membre@exemple.test")
    assert ligne["connecte"] is False
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `pytest tests/compte_api/test_admin.py -v`
Expected: les 6 nouveaux tests FAIL avec 404 (route inexistante) ; les 6 tests de la Task 1 passent toujours.

- [ ] **Step 3: Ajouter la route et son helper dans `compte_api/routers/admin.py`**

Vérifie d'abord que `from sqlalchemy.orm import Session as DbSession` est bien présent dans les imports du fichier (ajouté à la Task 1) — ne pas le dupliquer.

Puis ajoute à la fin de `compte_api/routers/admin.py` :

```python
def _vers_json(user: models.User, connecte: bool) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "email_verified": user.email_verified,
        "created_at": _aware(user.created_at).isoformat(),
        "last_login_at": _aware(user.last_login_at).isoformat() if user.last_login_at else None,
        "connecte": connecte,
    }


@router.get("/users")
def utilisateurs(_admin: models.User = Depends(current_admin), db: DbSession = Depends(get_db)) -> list[dict]:
    seuil = models.maintenant() - FENETRE_CONNECTE
    # Sous-requête des membres ayant une session active, jointe une seule
    # fois : pas de requête par membre.
    connectes = (
        select(models.Session.user_id)
        .where(models.Session.last_seen_at >= seuil)
        .distinct()
        .subquery()
    )
    lignes = db.execute(
        select(models.User, connectes.c.user_id.is_not(None).label("connecte"))
        .outerjoin(connectes, connectes.c.user_id == models.User.id)
        .where(models.User.deleted_at.is_(None))
        .order_by(models.User.created_at.desc())
    ).all()
    return [_vers_json(user, bool(connecte)) for user, connecte in lignes]
```

- [ ] **Step 4: Lancer les tests, vérifier qu'ils passent**

Run: `pytest tests/compte_api/test_admin.py -v`
Expected: PASS (12 tests au total)

- [ ] **Step 5: Commit**

```bash
git add compte_api/routers/admin.py tests/compte_api/test_admin.py
git commit -m "feat(compte): ajoute GET /admin/users pour le dashboard admin"
```

---

### Task 3: Onglet « Administration » dans le panneau Mon compte

**Files:**
- Modify: `docs/compte/compte.js:11-18` (liste des onglets), `docs/compte/compte.js:254-275` (`afficherOnglet`), `docs/compte/compte.js:277-308` (`ouvrirMonCompte`)
- Modify: `docs/compte/compte.css` (styles des tuiles et du tableau)

**Interfaces:**
- Consumes: `window.Compte.fetch('/admin/stats')` → `{total_membres, verifies, connectes_maintenant}` ; `window.Compte.fetch('/admin/users')` → `list` des objets produits par `_vers_json` (Task 2) ; `C.membre()` (déjà fourni ailleurs dans le site, renvoie l'objet membre avec son `role`) ; les helpers déjà présents dans ce fichier : `el()`, `zoneMessage()`, `annoncer()`, `dateCourte()`.
- Produces: rien de nouveau consommé par du code hors de ce fichier.

- [ ] **Step 1: Rendre la liste des onglets conditionnelle au rôle**

Dans `docs/compte/compte.js`, remplace (lignes 12-18) :

```js
  const ONGLETS = [
    ['profil', 'Profil'],
    ['suivis', 'Mes suivis'],
    ['alertes', 'Alertes'],
    ['historique', 'Historique IA'],
    ['donnees', 'Mes données'],
  ];
```

par :

```js
  const ONGLETS_BASE = [
    ['profil', 'Profil'],
    ['suivis', 'Mes suivis'],
    ['alertes', 'Alertes'],
    ['historique', 'Historique IA'],
    ['donnees', 'Mes données'],
  ];

  function ongletsPour(membre) {
    return membre && membre.role === 'admin'
      ? [...ONGLETS_BASE, ['admin', 'Administration']]
      : ONGLETS_BASE;
  }
```

- [ ] **Step 2: Utiliser `ongletsPour` à la création du panneau**

Dans `ouvrirMonCompte` (ligne ~284), remplace :

```js
      const onglets = ONGLETS.map(([cle, libelle]) => el('button', {
        type: 'button', role: 'tab', class: 'moncompte-onglet', 'data-onglet': cle, text: libelle,
        onclick: () => afficherOnglet(cle),
      }));
```

par :

```js
      const membreActuel = C.membre && C.membre();
      const onglets = ongletsPour(membreActuel).map(([cle, libelle]) => el('button', {
        type: 'button', role: 'tab', class: 'moncompte-onglet', 'data-onglet': cle, text: libelle,
        onclick: () => afficherOnglet(cle),
      }));
```

- [ ] **Step 3: Ajouter la branche `admin` dans `afficherOnglet`**

Dans `afficherOnglet` (vers la ligne 269-274), remplace :

```js
    } else if (nom === 'alertes' && typeof C.monterAlertes === 'function') {
      const demonter = await ongletAvecIndices(corps, (racine, donnees) => C.monterAlertes(racine, donnees));
      if (panneau) panneau.demonter = demonter;
    } else {
      corps.append(el('p', { class: 'compte-texte', text: 'Section indisponible.' }));
    }
```

par :

```js
    } else if (nom === 'alertes' && typeof C.monterAlertes === 'function') {
      const demonter = await ongletAvecIndices(corps, (racine, donnees) => C.monterAlertes(racine, donnees));
      if (panneau) panneau.demonter = demonter;
    } else if (nom === 'admin') {
      await ongletAdmin(corps);
    } else {
      corps.append(el('p', { class: 'compte-texte', text: 'Section indisponible.' }));
    }
```

- [ ] **Step 4: Écrire `ongletAdmin`, `tuileAdmin` et `ligneAdmin`**

Ajoute ces trois fonctions juste avant la section `// ------------------------------------------------------------- panneau` (donc juste après `ongletDonnees` et `suppressionHtml`, avant la ligne `function surTouche(e) {`) :

```js
  function tuileAdmin(libelle, valeur) {
    return el('div', { class: 'moncompte-admin-tuile' }, [
      el('p', { class: 'moncompte-admin-tuile-valeur', text: String(valeur) }),
      el('p', { class: 'moncompte-admin-tuile-libelle', text: libelle }),
    ]);
  }

  function ligneAdmin(m) {
    return el('tr', {}, [
      el('td', { text: m.email }),
      el('td', { text: m.display_name || '—' }),
      el('td', { text: m.role }),
      el('td', { text: m.email_verified ? 'Oui' : 'Non' }),
      el('td', { text: dateCourte(m.created_at) }),
      el('td', { text: m.last_login_at ? dateCourte(m.last_login_at) : '—' }),
      el('td', {}, [el('span', {
        class: 'moncompte-admin-point' + (m.connecte ? ' moncompte-admin-point-actif' : ''),
        'aria-label': m.connecte ? 'Connecté' : 'Non connecté',
        text: m.connecte ? '●' : '○',
      })]),
    ]);
  }

  async function ongletAdmin(corps) {
    const message = zoneMessage();
    const tuiles = el('div', { class: 'moncompte-admin-tuiles' });
    const tableau = el('table', { class: 'moncompte-admin-tableau' });
    const actualiser = el('button', {
      type: 'button', class: 'compte-bouton', text: 'Actualiser',
      onclick: () => charger(),
    });
    corps.append(
      el('div', { class: 'moncompte-barre' }, [
        el('p', { class: 'compte-texte', text: 'Vue d’ensemble des membres inscrits.' }),
        actualiser,
      ]),
      message, tuiles, tableau,
    );

    async function charger() {
      actualiser.disabled = true;
      annoncer(message, '', '');
      tuiles.replaceChildren(el('p', { class: 'compte-texte', text: 'Chargement…' }));
      tableau.replaceChildren();
      try {
        const [stats, membres] = await Promise.all([C.fetch('/admin/stats'), C.fetch('/admin/users')]);
        tuiles.replaceChildren(
          tuileAdmin('Membres', stats.total_membres),
          tuileAdmin('Adresses confirmées', stats.verifies),
          tuileAdmin('Connectés maintenant', stats.connectes_maintenant),
        );
        tableau.replaceChildren(
          el('thead', {}, [el('tr', {}, [
            el('th', { text: 'E-mail' }), el('th', { text: 'Nom' }), el('th', { text: 'Rôle' }),
            el('th', { text: 'Confirmé' }), el('th', { text: 'Inscrit le' }),
            el('th', { text: 'Dernière connexion' }), el('th', { text: 'Connecté' }),
          ])]),
          el('tbody', {}, membres.map(ligneAdmin)),
        );
      } catch (err) {
        annoncer(message, err.message, 'erreur');
      } finally {
        actualiser.disabled = false;
      }
    }
    await charger();
  }
```

- [ ] **Step 5: Styles CSS**

Dans `docs/compte/compte.css`, fusionne les nouvelles règles dans le bloc `@media (max-width: 600px)` déjà présent à la toute fin du fichier, et ajoute les règles hors média juste avant ce bloc. Remplace :

```css
@media (max-width: 600px) {
  .moncompte { max-width: none; border-left: 0; }
}
```

par :

```css
.moncompte-admin-tuiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-3); margin-bottom: var(--space-4); }
.moncompte-admin-tuile { padding: var(--space-3); border: 1px solid var(--border); border-radius: 8px; text-align: center; }
.moncompte-admin-tuile-valeur { margin: 0; font-family: 'Newsreader', serif; font-size: var(--text-lg); color: var(--gold); }
.moncompte-admin-tuile-libelle { margin: var(--space-1) 0 0; font-size: var(--text-xs); color: var(--muted); }
.moncompte-admin-tableau { width: 100%; border-collapse: collapse; font-size: var(--text-sm); }
.moncompte-admin-tableau th, .moncompte-admin-tableau td { padding: var(--space-2); border-bottom: 1px solid var(--border); text-align: left; }
.moncompte-admin-tableau th { color: var(--muted); font-weight: 500; }
.moncompte-admin-point { color: var(--muted); }
.moncompte-admin-point-actif { color: var(--gold); }

@media (max-width: 600px) {
  .moncompte { max-width: none; border-left: 0; }
  .moncompte-admin-tuiles { grid-template-columns: 1fr; }
  .moncompte-admin-tableau { display: block; overflow-x: auto; white-space: nowrap; }
}
```

- [ ] **Step 6: Test manuel (aucun framework de test JS dans ce dépôt)**

1. Terminal 1 : `.venv-compte/Scripts/uvicorn compte_api.app:app --port 8447` (ou l'équivalent de l'environnement virtuel du projet).
2. Terminal 2 : `python scripts/dev_compte.py` (sert le site sur `http://127.0.0.1:8000`).
3. Ouvre `http://127.0.0.1:8000`, crée un compte (ou utilise un compte existant).
4. Promeus ce compte en admin directement en base (trouve le fichier SQLite pointé par `database_url` dans la config/`.env` locale, puis) :
   ```bash
   sqlite3 chemin/vers/compte.db "UPDATE users SET role='admin' WHERE email='ton-email@exemple.test';"
   ```
5. Recharge la page, ouvre « Mon compte » (menu du membre) : un onglet « Administration » doit apparaître.
6. Clique-le : les 3 tuiles (Membres / Adresses confirmées / Connectés maintenant) et le tableau des membres doivent s'afficher, avec le point doré sur ta propre ligne (tu es connecté).
7. Clique « Actualiser » : pas d'erreur, les données se rechargent.
8. Déconnecte-toi, reconnecte-toi avec un compte `role="member"` : l'onglet « Administration » ne doit pas apparaître, et un appel direct à `/api/compte/admin/stats` (depuis la console du navigateur, `fetch('/api/compte/admin/stats').then(r => r.status)`) doit renvoyer 403.

- [ ] **Step 7: Commit**

```bash
git add docs/compte/compte.js docs/compte/compte.css
git commit -m "feat(compte): ajoute l'onglet Administration au panneau Mon compte"
```

---

## Self-Review (fait pendant l'écriture de ce plan)

- **Couverture de la spec** : `/admin/stats` (Task 1), `/admin/users` + tri + sous-requête unique (Task 2), onglet conditionnel + tuiles + tableau + bouton Actualiser sans auto-refresh (Task 3), fenêtre de 15 min testée aux bords 10/20 min (Tasks 1 et 2), aucune migration (confirmé, pas de tâche de migration dans ce plan), déploiement déjà automatique (confirmé dans les Global Constraints, pas de tâche de déploiement). Tout est couvert.
- **Placeholders** : aucun "TBD"/"TODO" ; chaque étape contient le code complet à écrire.
- **Cohérence des types/noms** : `FENETRE_CONNECTE` et `_aware()` définis en Task 1, réutilisés tels quels en Task 2 (même fichier, pas de redéfinition) ; `router` défini en Task 1, la Task 2 ajoute une route sur ce même objet ; les clés JSON de `_vers_json` (Task 2) correspondent exactement aux champs lus par `ligneAdmin`/`tuileAdmin` en Task 3 (`email`, `display_name`, `role`, `email_verified`, `created_at`, `last_login_at`, `connecte`, et `total_membres`/`verifies`/`connectes_maintenant`).
- **Tâche "enregistrement du routeur"** demandée initialement dans le brief : supprimée du plan après lecture de `compte_api/app.py`, qui découvre les routeurs automatiquement (`_routeurs()`, lignes 29-34) — ajouter cette tâche aurait été un no-op ; documenté dans les Global Constraints pour que l'implémenteur ne s'étonne pas de son absence.

## Execution Handoff

Plan complet et sauvegardé dans `docs/superpowers/plans/2026-11-16-admin-dashboard-plan.md`. Deux options d'exécution :

1. **Subagent-Driven (recommandé)** — je dispatche un sous-agent par tâche, revue entre les tâches, itération rapide.
2. **Exécution en ligne** — j'exécute les tâches dans cette session via `executing-plans`, par lots avec points de contrôle.

Je pars sur l'option 1 (subagent-driven), conformément à ta préférence par défaut.
