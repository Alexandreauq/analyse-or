# Déploiement de l'espace membre (`compte_api`) sur un VPS dédié

> Ce guide concerne le **service des comptes membres** (`compte_api/`) : inscription,
> connexion, favoris, portefeuille, historique, quota IA et alertes e-mail.
> Il tourne sur un **VPS séparé** de celui des bots/assistant IA (`goldbot.fr`),
> sous le nom `nomi.goldbot.fr`, pour isoler les données des membres (base
> PostgreSQL) du compte FTMO réel. Utilisateur dédié `compte` sur ce VPS,
> avec une base **PostgreSQL** locale.
> Les autres services, sur l'autre VPS : `deploy/README.md` (Bot Or), `deploy/README-ibkr.md`.

Guide d'installation initiale, à faire une fois, **sur le nouveau VPS**
(IP notée dans l'enregistrement DNS `nomi.goldbot.fr`). Les mises à jour
suivantes se font avec `deploy/deploy-compte.sh` (voir « Mises à jour »).

## Vue d'ensemble

```
Navigateur ──► Vercel (site docs/) ──/api/compte/*──► nomi.goldbot.fr:8447  compte-api (uvicorn, HTTPS)
                    pose X-Compte-Proxy                    │                [VPS dédié, séparé de goldbot.fr]
                                                          ├──► PostgreSQL local (base « compte »)
                                                          ├──► assistant-ia (goldbot.fr:8445, autre VPS)
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

## 0. Provisionnement de base du VPS (celui-ci est vierge)

Ce service tourne sur un **second VPS**, distinct de celui des bots —
contrairement aux autres guides de ce dossier, rien n'est encore en place
dessus. Mêmes étapes que `deploy/README.md` §1-2, adaptées :

```bash
# Connexion SSH par clé uniquement
echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/99-hardening.conf
sudo systemctl restart sshd
sudo sshd -T | grep -i passwordauthentication   # doit afficher "no"

# Pare-feu : SSH (22), le défi certbot http-01 (80), l'API compte (8447)
sudo apt install -y ufw
sudo ufw allow 22
sudo ufw allow 80
sudo ufw allow 8447
sudo ufw enable

# Mises à jour de sécurité automatiques
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

Le DNS `nomi.goldbot.fr` (enregistrement A chez Gandi, zone `goldbot.fr`)
doit déjà pointer vers l'IP de ce VPS avant l'étape 6 (certificat) —
sinon Let's Encrypt ne pourra pas vérifier le domaine.

## 1. Prérequis

- Le code de l'espace membre est fusionné dans `main` sur GitHub.
- Le **domaine du site sur Vercel** est connu (ex. `analyse-or.vercel.app`
  ou un domaine personnalisé). Il est noté `DOMAINE_DU_SITE` ci-dessous.
- Les identifiants SMTP (les mêmes que ceux des bots conviennent).
- Le DNS `nomi.goldbot.fr` pointe déjà vers ce VPS (voir ci-dessus) — le
  certificat Let's Encrypt est obtenu à l'étape 6, il n'existe pas encore.

Générer trois secrets différents (à garder pour les étapes 4, 9 et 10) :

```bash
openssl rand -hex 32   # COMPTE_PROXY_SECRET   (aussi dans Vercel)
openssl rand -hex 32   # COMPTE_UNSUBSCRIBE_SECRET
openssl rand -hex 32   # jeton de service assistant IA (deux .env)
```

## 2. PostgreSQL

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

## 3. Utilisateur dédié + dépôt

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

## 4. Fichier `.env` (jamais commité, déjà dans `.gitignore`)

Créer `/home/compte/analyse-or/.env` :

```
# Base PostgreSQL locale, par socket (authentification peer, sans mot de passe)
DATABASE_URL=postgresql+psycopg:///compte

# Adresse publique du site (liens des e-mails, retour Google)
COMPTE_SITE_URL=https://DOMAINE_DU_SITE
# Même valeur que la variable COMPTE_PROXY_SECRET du projet Vercel (étape 10)
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

# Assistant IA (étape 9 ; appel inter-serveurs vers goldbot.fr, autre VPS)
ASSISTANT_IA_URL=https://goldbot.fr:8445
ASSISTANT_IA_TOKEN=...
# Questions gratuites par jour et par membre (10 par défaut)
# AI_QUOTA_PAR_JOUR=10

# Connexion Google (facultatif, étape 11 ; sans eux le bouton Google est inactif)
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

## 5. Créer les tables

Toujours dans le shell `compte` :

```bash
cd ~/analyse-or
set -a; . ./.env; set +a
(cd compte_api && alembic upgrade head)
```

La dernière ligne doit afficher `Running upgrade -> f32f18b9848f, tables initiales`.

## 6. Certificat HTTPS et pare-feu

*(En tant qu'administrateur, hors du shell `compte`.)*

Contrairement aux autres services de ce dépôt, il n'y a **pas de certificat
existant à réutiliser** ici : ce VPS est nouveau, donc on en obtient un pour
`nomi.goldbot.fr` avec `deploy/setup-tls-compte.sh` (même procédure que
`deploy/setup-tls.sh`, adaptée à ce domaine et service) :

```bash
cd /home/compte/analyse-or   # ou le chemin du dépôt cloné en tant qu'administrateur
sudo bash deploy/setup-tls-compte.sh
```

Le script installe `certbot`, obtient le certificat en mode `standalone`
(le port 80 doit être libre et joignable — DNS `nomi.goldbot.fr` déjà
propagé, voir étape 0), configure le hook de renouvellement qui redémarre
`compte-api`, ajoute `compte` au groupe `ssl-cert` et lui donne la
permission de lire la clé privée.

```bash
# Port de l'API (Vercel s'y connecte)
sudo ufw allow 8447
```

## 7. Installer les services systemd

```bash
cd /home/compte/analyse-or
sudo cp deploy/compte-api.service deploy/compte-alertes.service deploy/compte-alertes.timer \
        deploy/compte-sauvegarde.service deploy/compte-sauvegarde.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now compte-api
sudo systemctl enable --now compte-alertes.timer
sudo systemctl enable --now compte-sauvegarde.timer
```

## 8. Vérifier le service

```bash
sudo systemctl status compte-api
# Sans l'en-tête secret : refus attendu (403)
curl -i https://nomi.goldbot.fr:8447/me
# Avec l'en-tête : 401 attendu (personne n'est connecté), donc le service répond
curl -i -H "X-Compte-Proxy: <COMPTE_PROXY_SECRET>" https://nomi.goldbot.fr:8447/me
# Une sauvegarde tout de suite, pour tester
sudo systemctl start compte-sauvegarde && ls -l /home/compte/sauvegardes
```

## 9. Brancher l'assistant IA

`compte_api` pose les questions des membres à l'assistant avec un jeton de
service. **Sur l'autre VPS** (`goldbot.fr`), dans
`/home/assistantia/analyse-or/.env`, ajouter la **même valeur** que
`ASSISTANT_IA_TOKEN` ci-dessus :

```
AI_ASSISTANT_SERVICE_TOKEN=...
```

puis `sudo systemctl restart assistant-ia-api`. Ce jeton n'ouvre que
`/ask`, avec la limite par membre de l'assistant.

## 10. Projet Vercel

1. Nouveau projet depuis le dépôt GitHub, **Root Directory : `docs`**,
   framework « Other », sans commande de build.
2. *Settings → Environment Variables* : `COMPTE_PROXY_SECRET` = la même
   valeur que dans le `.env` du VPS (environnement Production).
3. Déployer. Le relais est décrit dans `docs/vercel.json` : `/api/compte/*`
   vers `https://nomi.goldbot.fr:8447/*` avec l'en-tête secret.
4. Vérifier : `https://DOMAINE_DU_SITE/api/compte/me` doit répondre `401`
   (et non `403` ni `404`).

## 11. Connexion Google (facultatif)

Dans Google Cloud Console : *APIs & Services → Credentials → Create
credentials → OAuth client ID*, type « Web application ».

- Authorized redirect URI : `https://DOMAINE_DU_SITE/api/compte/google/callback`
- Reporter l'ID et le secret dans `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
  du `.env`, puis `sudo systemctl restart compte-api`.

## 12. Accès à la base pour l'administrateur (DBeaver)

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

- Onglet **SSH** : cocher « Use SSH Tunnel », hôte `nomi.goldbot.fr`, port 22,
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
| `compte-api` ne démarre pas, « Réglages de production manquants » | un des trois réglages de l'étape 4 |
| `403` sur `https://DOMAINE_DU_SITE/api/compte/me` | `COMPTE_PROXY_SECRET` différent entre Vercel et le `.env` |
| `502`/`504` sur le même lien | port 8447 fermé (`ufw`) ou service arrêté |
| « Assistant indisponible » pour les membres | `ASSISTANT_IA_URL` vide, ou jeton différent de `AI_ASSISTANT_SERVICE_TOKEN` |
| Pas d'e-mail de confirmation | réglages `SMTP_*` ; voir `journalctl -u compte-api` |
| Aucun résumé d'alertes | `systemctl list-timers compte-alertes.timer`, `journalctl -u compte-alertes` |
