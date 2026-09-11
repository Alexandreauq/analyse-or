set -e

apt-get install -y certbot
certbot certonly --standalone --non-interactive --agree-tos -m alexandre.auquiere@gmail.com -d goldbot.fr

mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/restart-gold-bot-api.sh <<'HOOK'
#!/bin/bash
systemctl restart gold-bot-api
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/restart-gold-bot-api.sh

groupadd -f ssl-cert
usermod -aG ssl-cert goldbot
chgrp -R ssl-cert /etc/letsencrypt/archive /etc/letsencrypt/live
chmod -R g+rX /etc/letsencrypt/archive /etc/letsencrypt/live

echo "DONE"
