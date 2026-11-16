set -e

apt-get install -y certbot
certbot certonly --standalone --non-interactive --agree-tos -m alexandre.auquiere@gmail.com -d nomi.goldbot.fr

mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/restart-compte-api.sh <<'HOOK'
#!/bin/bash
systemctl restart compte-api
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/restart-compte-api.sh

groupadd -f ssl-cert
usermod -aG ssl-cert compte
chgrp -R ssl-cert /etc/letsencrypt/archive /etc/letsencrypt/live
chmod -R g+rX /etc/letsencrypt/archive /etc/letsencrypt/live

echo "DONE"
