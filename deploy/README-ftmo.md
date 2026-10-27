# Déploiement de l'instance FTMO du Bot Or

> Instance **séparée** de `deploy/README.md` (le bot perso) : même
> code (`gold_bot/`), checkout git et `.env` distincts, pour qu'un
> redémarrage, un kill_switch ou un bug sur l'une n'affecte jamais
> l'autre. Suppose que le VPS et l'utilisateur système `goldbot` sont
> déjà en place (étapes 1-3 de `deploy/README.md` déjà faites).

## 1. Enregistrer le compte MT5 FTMO dans MetaApi

À faire par toi, sur app.metaapi.cloud (compte MetaApi, distinct du
compte FTMO) :

1. Connecte-toi sur https://app.metaapi.cloud
2. "Add account" (ou équivalent) → renseigne :
   - **Login** : l'identifiant du compte MT5 fourni par FTMO après
     l'achat du challenge
   - **Mot de passe** : le mot de passe de **trading** (pas
     investisseur/lecture seule)
   - **Server** : le nom du serveur MT5 FTMO (affiché dans l'email de
     confirmation FTMO ou le portail, ex. `FTMO-Server`)
   - **Platform** : MetaTrader 5
3. Une fois le compte ajouté et déployé côté MetaApi, récupère :
   - le **token API** (créé pour ce compte, ou réutilisé si ton
     organisation MetaApi en a déjà un valable pour plusieurs comptes)
   - l'**account ID** MetaApi généré (différent de celui du bot perso)

Ces deux valeurs vont dans le `.env` de l'étape 3.

## 2. Deuxième checkout du dépôt

```bash
sudo -u goldbot -i
git clone https://github.com/Alexandreauq/analyse-or.git analyse-or-ftmo
cd analyse-or-ftmo
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-bot.txt
```

## 3. Fichier `.env` dédié (jamais commité)

Créer `/home/goldbot/analyse-or-ftmo/.env` :

```
METAAPI_TRADE_TOKEN=...        # token du compte MetaApi FTMO (etape 1)
METAAPI_TRADE_ACCOUNT_ID=...   # account ID MetaApi FTMO (etape 1)

# Garde-fou FTMO (gold_bot/ftmo_guard.py) -- absent = comportement du
# bot perso inchange, present = garde-fou + restriction de symboles actifs.
FTMO_ISC=10000
# FTMO_SYMBOLS=XAUUSD          # deja la valeur par defaut si FTMO_ISC est present,
                                # pas besoin de la fixer sauf cas particulier

TWELVE_DATA_API_KEY=...        # idealement une cle separee de celle du bot perso
SMTP_USER=...
SMTP_PASSWORD=...
MAIL_TO=...                    # optionnel, sinon = SMTP_USER

BOT_API_TOKEN=...              # NOUVEAU jeton, distinct de celui du bot perso --
                                # genere une chaine aleatoire longue
```

`chmod 600 .env`.

## 4. Sudoers (restart sans mot de passe, limité à cette instance)

En tant qu'administrateur/root :

```bash
echo 'goldbot ALL=(root) NOPASSWD: /bin/systemctl restart gold-bot-loop-ftmo, /bin/systemctl restart gold-bot-api-ftmo' | sudo tee /etc/sudoers.d/goldbot-restart-ftmo
sudo chmod 440 /etc/sudoers.d/goldbot-restart-ftmo
```

## 5. Pare-feu : port dédié pour l'API

Le bot perso utilise déjà le port 8443 sur `gold-bot-api.service`.
L'instance FTMO utilise le **8444** (`gold-bot-api-ftmo.service`) sur
le même domaine/certificat :

```bash
sudo ufw allow 8444
```

## 6. Installer les services systemd

```bash
sudo cp deploy/gold-bot-loop-ftmo.service /etc/systemd/system/
sudo cp deploy/gold-bot-api-ftmo.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gold-bot-loop-ftmo
sudo systemctl enable --now gold-bot-api-ftmo
```

Le certificat Let's Encrypt du domaine est déjà en place pour le bot
perso (`deploy/README.md` étape 5) et réutilisable tel quel pour ce
second port -- pas besoin de le régénérer.

## 7. Vérifier

```bash
sudo systemctl status gold-bot-loop-ftmo
sudo systemctl status gold-bot-api-ftmo
curl https://DOMAINE:8444/status
```

Le bot démarre en **mode simulation** (`dry_run: true` par défaut),
exactement comme le bot perso. Laisse-le tourner plusieurs jours et
observe les décisions avant de passer en mode réel -- voir étape 9 de
`deploy/README.md`, à refaire ici depuis
`/home/goldbot/analyse-or-ftmo` (chaque instance a son propre
`gold_bot/state.json`, aucun risque de toucher l'instance perso par
erreur).

## Mises à jour

```bash
sudo -u goldbot -i
cd analyse-or-ftmo
./deploy/deploy-ftmo.sh
```
