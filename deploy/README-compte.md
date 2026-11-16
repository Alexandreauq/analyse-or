# Déploiement de l'espace membre (`compte_api`) sur le VPS

> Ce guide concerne le **service des comptes membres** (`compte_api/`) : inscription,
> connexion, favoris, portefeuille, historique, quota IA et alertes e-mail.
> Il tourne sur le même VPS que les bots et l'assistant IA, sous un
> utilisateur dédié `compte`, avec une base **PostgreSQL** locale.
> Les autres services : `deploy/README.md` (Bot Or), `deploy/README-ibkr.md`.

Guide d'installation initiale, à faire une fois. Les mises à jour suivantes
se font avec `deploy/deploy-compte.sh` (voir « Mises à jour »).

## Vue d'ensemble

```
Navigateur ──► Vercel (site docs/) ──/api/compte/*──► goldbot.fr:8447  compte-api (uvicorn, HTTPS)
                    pose X-Compte-Proxy                    │
                                                          ├──► PostgreSQL local (base « compte »)
                                                          ├──► assistant-ia (goldbot.fr:8445)
                                                          └──► SMTP (e-mails)
```

Le site quitte GitHub Pages pour Vercel : Vercel relaie `/api/compte/*` vers
le VPS (`docs/vercel.json`), si bien que le site et l'API partagent la même
adresse et que le cookie de connexion fonctionne. Le service refuse toute
requête qui ne porte pas l'en-tête secret ajouté par Vercel.

Trois unités systemd :

| Unité | Rôle |
|---|---|
| `compte-api.service` | l'API, en continu, port 8447 |
| `compte-alertes.timer` | détection des alertes et résumé par e-mail, chaque jour à 07:30 UTC |
| `compte-sauvegarde.timer` | sauvegarde de la base, chaque jour à 03:00 UTC, 14 jours gardés |

## 0. Prérequis

- Le code de l'espace membre est fusionné dans `main` sur GitHub.
- Le **domaine du site sur Vercel** est connu (ex. `analyse-or.vercel.app`
  ou un domaine personnalisé). Il est noté `DOMAINE_DU_SITE` ci-dessous.
- Les identifiants SMTP (les mêmes que ceux des bots conviennent).
- Le certificat Let's Encrypt `goldbot.fr` existe déjà (`deploy/README.md`, étape 5).

Générer trois secrets différents (à garder pour les étapes 3, 8 et 9) :

```bash
openssl rand -hex 32   # COMPTE_PROXY_SECRET   (aussi dans Vercel)
openssl rand -hex 32   # COMPTE_UNSUBSCRIBE_SECRET
openssl rand -hex 32   # jeton de service assistant IA (deux .env)
```

## 1. PostgreSQL

```bash
sudo apt install -y postgresql
sudo systemctl enable --now postgresql

# Rôle et base « compte ». Le service se connecte par le socket local en
# authentification « peer » (utilisateur Linux compte = rôle compte) :
# aucun mot de passe à stocker.
sudo -u postgres createuser compte
sudo -u postgres createdb -O compte compte
```

PostgreSQL n'écoute que sur la machine elle-même (`listen_addresses =
'localhost'` par défaut) : ne pas ouvrir le port 5432 dans le pare-feu.

## 2. Utilisateur dédié + dépôt

```bash
sudo adduser --disabled-password --gecos "" compte

# Sudoers restreint à la seule commande dont deploy-compte.sh a besoin :
echo 'compte ALL=(root) NOPASSWD: /bin/systemctl restart compte-api' | sudo tee /etc/sudoers.d/compte-restart
sudo chmod 440 /etc/sudoers.d/compte-restart

sudo -u compte -i
git clone https://github.com/Alexandreauq/analyse-or.git
cd analyse-or
python3 -m venv venv
source venv/bin/activate
pip install -r compte_api/requirements.txt
```

## 3. Fichier `.env` (jamais commité, déjà dans `.gitignore`)

Créer `/home/compte/analyse-or/.env` :

```
# Base PostgreSQL locale, par socket (authentification peer, sans mot de passe)
DATABASE_URL=postgresql+psycopg:///compte

# Adresse publique du site (liens des e-mails, retour Google)
COMPTE_SITE_URL=https://DOMAINE_DU_SITE
# Même valeur que la variable COMPTE_PROXY_SECRET du projet Vercel (étape 9)
COMPTE_PROXY_SECRET=...
COMPTE_COOKIE_SECURE=true
COMPTE_UNSUBSCRIBE_SECRET=...

# E-mails
COMPTE_MAILER=smtp
SMTP_HOST=...
SMTP_PORT=465
SMTP_USER=...
SMTP_PASSWORD=...
MAIL_FROM=...

# Assistant IA (étape 8)
ASSISTANT_IA_URL=https://goldbot.fr:8445
ASSISTANT_IA_TOKEN=...
# Questions gratuites par jour et par membre (10 par défaut)
# AI_QUOTA_PAR_JOUR=10

# Connexion Google (facultatif, étape 10 ; sans eux le bouton Google est inactif)
# GOOGLE_CLIENT_ID=...
# GOOGLE_CLIENT_SECRET=...
```

```bash
chmod 600 .env
```

Garde-fou : dès que `COMPTE_SITE_URL` n'est pas une adresse locale, le
service **refuse de démarrer** si `COMPTE_PROXY_SECRET` fait moins de 32
caractères, si `COMPTE_COOKIE_SECURE` n'est pas `true` ou si
`COMPTE_MAILER` n'est pas `smtp`. Le message d'erreur liste ce qui manque
(`journalctl -u compte-api`).

## 4. Créer les tables

Toujours dans le shell `compte` :

```bash
cd ~/analyse-or
set -a; . ./.env; set +a
(cd compte_api && alembic upgrade head)
```

La dernière ligne doit afficher `Running upgrade -> f32f18b9848f, tables initiales`.

## 5. Certificat HTTPS et pare-feu

*(En tant qu'administrateur, hors du shell `compte`.)*

```bash
# Lire la clé privée Let's Encrypt (même groupe que goldbot)
sudo usermod -aG ssl-cert compte

# Redémarrer aussi compte-api quand le certificat est renouvelé
echo '#!/bin/bash
systemctl restart compte-api' | sudo tee /etc/letsencrypt/renewal-hooks/deploy/restart-compte-api.sh
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/restart-compte-api.sh

# Port de l'API (Vercel s'y connecte)
sudo ufw allow 8447
```

## 6. Installer les services systemd

```bash
cd /home/compte/analyse-or
sudo cp deploy/compte-api.service deploy/compte-alertes.service deploy/compte-alertes.timer \
        deploy/compte-sauvegarde.service deploy/compte-sauvegarde.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now compte-api
sudo systemctl enable --now compte-alertes.timer
sudo systemctl enable --now compte-sauvegarde.timer
```

## 7. Vérifier le service

```bash
sudo systemctl status compte-api
# Sans l'en-tête secret : refus attendu (403)
curl -i https://goldbot.fr:8447/me
# Avec l'en-tête : 401 attendu (personne n'est connecté), donc le service répond
curl -i -H "X-Compte-Proxy: <COMPTE_PROXY_SECRET>" https://goldbot.fr:8447/me
# Une sauvegarde tout de suite, pour tester
sudo systemctl start compte-sauvegarde && ls -l /home/compte/sauvegardes
```

## 8. Brancher l'assistant IA

`compte_api` pose les questions des membres à l'assistant avec un jeton de
service. Dans `/home/assistantia/analyse-or/.env`, ajouter la **même
valeur** que `ASSISTANT_IA_TOKEN` ci-dessus :

```
AI_ASSISTANT_SERVICE_TOKEN=...
```

puis `sudo systemctl restart assistant-ia-api`. Ce jeton n'ouvre que
`/ask`, avec la limite par membre de l'assistant.

## 9. Projet Vercel

1. Nouveau projet depuis le dépôt GitHub, **Root Directory : `docs`**,
   framework « Other », sans commande de build.
2. *Settings → Environment Variables* : `COMPTE_PROXY_SECRET` = la même
   valeur que dans le `.env` du VPS (environnement Production).
3. Déployer. Le relais est décrit dans `docs/vercel.json` : `/api/compte/*`
   vers `https://goldbot.fr:8447/*` avec l'en-tête secret.
4. Vérifier : `https://DOMAINE_DU_SITE/api/compte/me` doit répondre `401`
   (et non `403` ni `404`).

## 10. Connexion Google (facultatif)

Dans Google Cloud Console : *APIs & Services → Credentials → Create
credentials → OAuth client ID*, type « Web application ».

- Authorized redirect URI : `https://DOMAINE_DU_SITE/api/compte/google/callback`
- Reporter l'ID et le secret dans `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
  du `.env`, puis `sudo systemctl restart compte-api`.

## 11. Accès à la base pour l'administrateur (DBeaver)

La base n'est pas exposée sur Internet : on y accède par un **tunnel SSH**.
Il faut donc deux choses : un compte SSH sans droits d'administration, et
un rôle PostgreSQL avec mot de passe.

**Compte SSH.** L'administrateur de la base génère une clé sur son PC
(`ssh-keygen -t ed25519`, sous Windows aussi) et transmet le fichier
`id_ed25519.pub` (la clé publique, jamais l'autre). Sur le VPS :

```bash
sudo adduser --disabled-password --gecos "" IDENTIFIANT
sudo mkdir -p /home/IDENTIFIANT/.ssh
echo "CLÉ_PUBLIQUE" | sudo tee /home/IDENTIFIANT/.ssh/authorized_keys
sudo chown -R IDENTIFIANT:IDENTIFIANT /home/IDENTIFIANT/.ssh
sudo chmod 700 /home/IDENTIFIANT/.ssh && sudo chmod 600 /home/IDENTIFIANT/.ssh/authorized_keys
```

Ce compte n'a pas `sudo` et ne peut pas lire les `.env` (`chmod 600`).

**Rôle PostgreSQL `gestion`** : lire, modifier et supprimer des lignes,
sans pouvoir changer la structure des tables.

```bash
sudo -u postgres psql -d compte <<'SQL'
CREATE ROLE gestion LOGIN;
GRANT CONNECT ON DATABASE compte TO gestion;
GRANT USAGE ON SCHEMA public TO gestion;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO gestion;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gestion;
-- Tables créées par les migrations futures (rôle compte) : mêmes droits
ALTER DEFAULT PRIVILEGES FOR ROLE compte IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gestion;
ALTER DEFAULT PRIVILEGES FOR ROLE compte IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO gestion;
SQL
# Mot de passe saisi au clavier (n'apparaît pas dans l'historique du shell)
sudo -u postgres psql -c "\password gestion"
```

**Dans DBeaver** : *Nouvelle connexion → PostgreSQL*.

- Onglet **SSH** : cocher « Use SSH Tunnel », hôte `goldbot.fr`, port 22,
  utilisateur `IDENTIFIANT`, authentification « Public Key » avec le
  fichier `id_ed25519`.
- Onglet **Main** : hôte `localhost`, port `5432`, base `compte`,
  utilisateur `gestion` et son mot de passe.

« localhost » est vu depuis le VPS, au bout du tunnel : c'est bien la base
du serveur.

Supprimer un membre et toutes ses données (favoris, positions, alertes,
historique, sessions partent en cascade) :

```sql
DELETE FROM users WHERE email = 'adresse@exemple.fr';
```

## Mises à jour

```bash
sudo -u compte -i
cd analyse-or
./deploy/deploy-compte.sh
```

Le script récupère le code, installe les dépendances, applique les
migrations de la base puis redémarre `compte-api`.

## Restaurer une sauvegarde

```bash
sudo systemctl stop compte-api
sudo -u compte pg_restore --clean --if-exists -d compte /home/compte/sauvegardes/compte-AAAA-MM-JJ.dump
sudo systemctl start compte-api
```

Les sauvegardes restent sur le VPS : si le disque est perdu, elles le sont
aussi. Pour une copie ailleurs, récupérer régulièrement
`/home/compte/sauvegardes/` (par exemple avec `scp` depuis un autre poste).

## À décider avant l'ouverture

- **CORS des autres API.** `assistant_ia`, `gold_bot` et `ibkr_bot`
  n'acceptent les appels du navigateur que depuis
  `https://alexandreauq.github.io`. Les pages qui les appellent
  directement (scores, statut des bots…) ne marcheront depuis Vercel
  qu'en ajoutant `https://DOMAINE_DU_SITE` à leur `allow_origins`.
  (`compte_api` → assistant n'est pas concerné : c'est un appel de serveur
  à serveur.)
- **Adresse IP des visiteurs.** Les limites d'essais (connexion,
  inscription…) se basent sur le premier élément de `X-Forwarded-For`.
  Vérifier dans `journalctl -u compte-api` qu'une requête passée par
  Vercel montre bien l'IP réelle du visiteur et non une IP de Vercel.

## Dépannage

| Symptôme | Cause probable |
|---|---|
| `compte-api` ne démarre pas, « Réglages de production manquants » | un des trois réglages de l'étape 3 |
| `403` sur `https://DOMAINE_DU_SITE/api/compte/me` | `COMPTE_PROXY_SECRET` différent entre Vercel et le `.env` |
| `502`/`504` sur le même lien | port 8447 fermé (`ufw`) ou service arrêté |
| « Assistant indisponible » pour les membres | `ASSISTANT_IA_URL` vide, ou jeton différent de `AI_ASSISTANT_SERVICE_TOKEN` |
| Pas d'e-mail de confirmation | réglages `SMTP_*` ; voir `journalctl -u compte-api` |
| Aucun résumé d'alertes | `systemctl list-timers compte-alertes.timer`, `journalctl -u compte-alertes` |
