#!/bin/bash
# Deploiement manuel de l'instance FTMO (checkout separe de l'instance
# perso -- voir deploy/README-ftmo.md). A executer sur le VPS, depuis
# /home/goldbot/analyse-or-ftmo.
set -e

cd /home/goldbot/analyse-or-ftmo
git pull
source venv/bin/activate
pip install -r requirements-bot.txt
sudo systemctl restart gold-bot-loop-ftmo
sudo systemctl restart gold-bot-api-ftmo
echo "Deploiement FTMO termine."
