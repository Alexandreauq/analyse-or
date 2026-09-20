set -e

mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/restart-ibkr-bot-api.sh <<'HOOK'
#!/bin/bash
systemctl restart ibkr-bot-api
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/restart-ibkr-bot-api.sh

groupadd -f ssl-cert
usermod -aG ssl-cert ibkrbot
chgrp -R ssl-cert /etc/letsencrypt/archive /etc/letsencrypt/live
chmod -R g+rX /etc/letsencrypt/archive /etc/letsencrypt/live

echo "DONE"
