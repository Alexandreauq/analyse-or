set -e
sudo -u goldbot -H bash -c '
set -e
git config --global --add safe.directory /home/goldbot/analyse-or
cd ~/analyse-or
git checkout main
git pull origin main
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements-bot.txt
'
echo "DONE"
