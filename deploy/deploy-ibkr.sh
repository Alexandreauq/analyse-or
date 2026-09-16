#!/bin/bash
# Mise a jour du bot Actions IBKR sur le VPS.
# A executer en tant qu'utilisateur ibkrbot, depuis /home/ibkrbot/analyse-or.
set -e

cd /home/ibkrbot/analyse-or
git pull
source venv/bin/activate
pip install -r requirements-bot.txt
# Le batch quotidien n'est pas un service persistant : rien a redemarrer,
# le prochain declenchement du timer prendra le nouveau code. Seul le
# Gateway tourne en continu.
sudo systemctl restart ibkr-gateway
echo "Deploiement Bot Actions IBKR termine. Prochain batch :"
systemctl list-timers ibkr-bot-daily.timer --no-pager
