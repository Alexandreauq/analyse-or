#!/bin/bash
# Mise a jour du service compte. A executer sur le VPS en tant que
# l'utilisateur compte, depuis /home/compte/analyse-or (voir
# deploy/README-compte.md, section « Mises a jour »).
set -e

cd /home/compte/analyse-or
git pull
source venv/bin/activate
pip install -r compte_api/requirements.txt
# Applique les nouvelles migrations de la base (sans effet s'il n'y en a pas).
set -a; . ./.env; set +a
(cd compte_api && alembic upgrade head)
sudo systemctl restart compte-api
echo "Déploiement terminé."
