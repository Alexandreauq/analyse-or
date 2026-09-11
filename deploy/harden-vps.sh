set -e
echo 'PasswordAuthentication no' | tee /etc/ssh/sshd_config.d/99-hardening.conf
systemctl restart sshd
sshd -T | grep -i passwordauthentication

apt-get update -y
apt-get install -y ufw unattended-upgrades
ufw allow 22
ufw allow 80
ufw allow 8443
ufw --force enable
ufw status

echo "DONE"
