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
# Connexion SSH par clé uniquement (pas de mot de passe) — un fichier
# drop-in plutôt qu'un sed sur sshd_config, qui ne matche pas toujours
# la ligne exacte et peut de toute façon être écrasé par un override
# cloud-init (fréquent sur les images Ubuntu/Debian récentes) :
echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/99-hardening.conf
sudo systemctl restart sshd
sudo sshd -T | grep -i passwordauthentication   # doit afficher "no"

# Pare-feu : SSH (22), le défi certbot http-01 (80) et l'API du bot (8443)
sudo apt install -y ufw
sudo ufw allow 22
sudo ufw allow 80
sudo ufw allow 8443
sudo ufw enable

# Mises à jour de sécurité automatiques
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

## 3. Utilisateur dédié + dépôt

```bash
sudo adduser --disabled-password goldbot

# Sudoers restreint à exactement les deux commandes dont deploy.sh a
# besoin (à faire en tant qu'administrateur/root, AVANT de passer dans
# le shell goldbot ci-dessous) :
echo 'goldbot ALL=(root) NOPASSWD: /bin/systemctl restart gold-bot-loop, /bin/systemctl restart gold-bot-api' | sudo tee /etc/sudoers.d/goldbot-restart
sudo chmod 440 /etc/sudoers.d/goldbot-restart

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
# Idéalement une clé SÉPARÉE de celle déjà utilisée par .github/workflows/scalping_tracker.yml
# et docs/scalping.js — ce bot interroge Twelve Data environ une fois par minute (~1440
# requêtes/jour), ce qui peut à lui seul dépasser un quota gratuit partagé et casser aussi
# la fonctionnalité scalping existante du site public si la même clé est réutilisée telle quelle.
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

# Renouvellement automatique (certbot installe déjà un timer systemd) —
# ajoute un hook pour qu'uvicorn recharge le nouveau certificat, sinon
# il continue de servir l'ancien jusqu'au prochain redémarrage manuel :
echo '#!/bin/bash
systemctl restart gold-bot-api' | sudo tee /etc/letsencrypt/renewal-hooks/deploy/restart-gold-bot-api.sh
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/restart-gold-bot-api.sh

# Le service tourne sous l'utilisateur goldbot (non-root), qui doit
# pouvoir lire la clé privée générée par certbot (root:root, 0600 par
# défaut) :
sudo groupadd -f ssl-cert
sudo usermod -aG ssl-cert goldbot
sudo chgrp -R ssl-cert /etc/letsencrypt/archive /etc/letsencrypt/live
sudo chmod -R g+rX /etc/letsencrypt/archive /etc/letsencrypt/live
```

Remplace `DOMAINE_A_REMPLACER` dans `deploy/gold-bot-api.service` par le
vrai domaine avant l'étape suivante.

## 6. Installer les services systemd

*(Exécute cette section en tant qu'administrateur/root, pas dans le shell goldbot ouvert à l'étape 3 — ces commandes installent des fichiers hors de /home/goldbot.)*

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
55 23 * * * cd /home/goldbot/analyse-or && set -a && . ./.env && set +a && venv/bin/python3 -m gold_bot.notify >> /home/goldbot/gold_bot_notify.log 2>&1
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

**`gold_bot/state.json` est le SEUL fichier qui contrôle l'interrupteur d'urgence** (`kill_switch`) et le mode simulation (`dry_run`). `gold_bot/circuit_breaker_state.json` ne sert qu'au suivi interne du coupe-circuit journalier — l'éditer n'a aucun effet sur l'arrêt du bot.

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
