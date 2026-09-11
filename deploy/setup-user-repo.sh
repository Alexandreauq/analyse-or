set -e

id -u goldbot >/dev/null 2>&1 || adduser --disabled-password --gecos "" goldbot

cat > /etc/sudoers.d/goldbot-restart <<'SUDOERS'
goldbot ALL=(root) NOPASSWD: /bin/systemctl restart gold-bot-loop, /bin/systemctl restart gold-bot-api
SUDOERS
chmod 440 /etc/sudoers.d/goldbot-restart

apt-get install -y git python3-venv python3-pip

sudo -u goldbot -H bash -c '
set -e
cd ~
if [ ! -d analyse-or ]; then
  git clone https://github.com/Alexandreauq/analyse-or.git
fi
cd analyse-or
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements-bot.txt
'

echo "DONE"
