# Déploiement du Bot Or sur un VPS

Guide d'installation initiale (à faire une fois). Les mises à jour
suivantes se font avec `deploy.sh` (voir en bas).

## 1. Provisionner le VPS

N'importe quel petit VPS Linux (ex. Hetzner CX22, DigitalOcean droplet
1 Go) avec Ubuntu/Debian récent. Un nom de domaine ou sous-domaine
pointant vers son IP est nécessaire pour le certificat HTTPS (ex.
`bot.tondomaine.fr`).

## 2. Sécurisation de base

```bash
# Connexion SSH par clé uniquement (pas de mot de passe)
sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart sshd

# Pare-feu : uniquement SSH (22) et l'API du bot (8443)
sudo apt install -y ufw
sudo ufw allow 22
sudo ufw allow 8443
sudo ufw enable

# Mises à jour de sécurité automatiques
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

## 3. Utilisateur dédié + dépôt

```bash
sudo adduser --disabled-password goldbot
sudo -u goldbot -i
git clone https://github.com/Alexandreauq/analyse-or.git
cd analyse-or
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-bot.txt
```

## 4. Fichier `.env` (jamais commité)

Créer `/home/goldbot/analyse-or/.env` :

```
# Token du DEUXIÈME compte MetaApi, connecté avec le mot de passe de trading réel
METAAPI_TRADE_TOKEN=...
# Account ID MetaApi de ce compte (différent du compte lecture seule)
METAAPI_TRADE_ACCOUNT_ID=...
# Déjà utilisé ailleurs dans ce projet
TWELVE_DATA_API_KEY=...
SMTP_USER=...
SMTP_PASSWORD=...
# Optionnel, sinon = SMTP_USER
MAIL_TO=...
# Jeton secret pour /kill et /resume — génère une chaîne aléatoire longue
BOT_API_TOKEN=...
```

Permissions restreintes : `chmod 600 .env`.

## 5. Certificat HTTPS (Let's Encrypt)

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone -d DOMAINE_A_REMPLACER
```

Remplace `DOMAINE_A_REMPLACER` dans `deploy/gold-bot-api.service` par le
vrai domaine avant l'étape suivante.

## 6. Installer les services systemd

```bash
sudo cp deploy/gold-bot-loop.service /etc/systemd/system/
sudo cp deploy/gold-bot-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gold-bot-loop
sudo systemctl enable --now gold-bot-api
```

## 7. Résumé quotidien par email (cron)

```bash
crontab -e -u goldbot
```

Ajouter :

```
0 23 * * * cd /home/goldbot/analyse-or && venv/bin/python3 -m gold_bot.notify >> /home/goldbot/gold_bot_notify.log 2>&1
```

## 8. Vérifier

```bash
sudo systemctl status gold-bot-loop
sudo systemctl status gold-bot-api
curl https://DOMAINE_A_REMPLACER:8443/status
```

Le bot tourne maintenant en **mode simulation** (`dry_run: true` par
défaut) — il évalue les signaux et journalise ce qu'il ferait, sans
jamais passer de vrai ordre. Laisse-le tourner plusieurs jours et
observe `gold_bot/decisions_log.jsonl` / les résumés quotidiens par
email avant de passer à l'étape suivante.

## 9. Passage en mode réel (à ne faire qu'après validation du mode simulation)

**Volontairement pas exposé via l'API HTTPS** — le seul chemin est une
commande manuelle sur le VPS, en SSH, pour que ce soit la bascule la
plus difficile à déclencher par erreur de tout le système :

```bash
sudo -u goldbot -i
cd analyse-or
source venv/bin/activate
python3 -c "import gold_bot.state as state; s = state.load_state(state.STATE_PATH); s['dry_run'] = False; state.save_state(s, state.STATE_PATH)"
sudo systemctl restart gold-bot-loop
```

Pour revenir en simulation, remets `dry_run` à `True` avec la même
commande et redémarre à nouveau.

## Mises à jour

```bash
sudo -u goldbot -i
cd analyse-or
./deploy/deploy.sh
```
