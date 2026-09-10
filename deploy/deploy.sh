#!/bin/bash
# Déploiement manuel (pas de CI/CD pour cette phase — voir le spec).
# À exécuter sur le VPS, depuis /home/goldbot/analyse-or.
set -e

cd /home/goldbot/analyse-or
git pull
source venv/bin/activate
pip install -r requirements-bot.txt
sudo systemctl restart gold-bot-loop
sudo systemctl restart gold-bot-api
echo "Déploiement terminé."
