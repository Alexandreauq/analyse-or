# Tests des fichiers de deploiement : ils ne lancent rien, ils verifient
# que les unites systemd portent les proprietes dont depend la securite du
# bot (utilisateur dedie, timeout compatible avec le preflight, pas de
# relance automatique, timer non persistant).
import os

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _lire(nom):
    with open(os.path.join(RACINE, "deploy", nom), encoding="utf-8") as fh:
        return fh.read()


def test_the_gateway_service_runs_as_the_dedicated_user():
    contenu = _lire("ibkr-gateway.service")
    assert "User=ibkrbot" in contenu
    assert "User=goldbot" not in contenu
    assert "Restart=on-failure" in contenu


def test_the_daily_service_is_a_oneshot_that_can_outlast_the_preflight():
    """Le preflight peut dormir 20 minutes (3 tentatives / 10 min). Le
    defaut systemd (DefaultTimeoutStartSec=90s) tuerait le batch en pleine
    attente, sans abandon propre ni email d'alerte."""
    contenu = _lire("ibkr-bot-daily.service")
    assert "Type=oneshot" in contenu
    assert "User=ibkrbot" in contenu
    assert "ExecStart=" in contenu and "-m ibkr_bot.daily" in contenu
    assert "EnvironmentFile=/home/ibkrbot/analyse-or/.env" in contenu

    timeout = [l for l in contenu.splitlines() if l.startswith("TimeoutStartSec=")]
    assert timeout, "TimeoutStartSec est obligatoire avec Type=oneshot"
    assert int(timeout[0].split("=")[1]) >= 1800


def test_the_daily_service_never_restarts_itself():
    """Relancer un batch a mi-parcours rejouerait des entrees deja
    passees. L'idempotence protege, elle n'invite pas a la boucle."""
    contenu = _lire("ibkr-bot-daily.service")
    assert not any(l.startswith("Restart=") and l != "Restart=no"
                   for l in contenu.splitlines())


def test_the_timer_fires_at_1445_utc_and_never_catches_up():
    contenu = _lire("ibkr-bot-daily.timer")
    assert "OnCalendar=*-*-* 14:45:00 UTC" in contenu
    assert "Persistent=false" in contenu
    assert "Unit=ibkr-bot-daily.service" in contenu
    assert "WantedBy=timers.target" in contenu


def test_requirements_bot_declares_dateutil():
    """ibkr_bot.portfolio importe dateutil.relativedelta ; le venv du VPS
    s'installe depuis requirements-bot.txt, pas requirements.txt."""
    with open(os.path.join(RACINE, "requirements-bot.txt"), encoding="utf-8") as fh:
        assert "python-dateutil" in fh.read()


def test_requirements_bot_declares_ib_async():
    """ibkr_bot.gateway importe ib_async (client TWS API) ; le venv du VPS
    s'installe depuis requirements-bot.txt, pas requirements.txt. Sans
    cette ligne, le premier batch reel plante a l'import, avant toute
    gestion d'erreur — silence total, aucune entree de journal, aucun
    email (revue finale de branche, Fix Critical #1)."""
    with open(os.path.join(RACINE, "requirements-bot.txt"), encoding="utf-8") as fh:
        assert "ib_async" in fh.read()


def test_the_ibkr_readme_documents_the_operational_essentials():
    contenu = _lire("README-ibkr.md")
    for attendu in ("ibkrbot", "IBKR_GATEWAY_URL", "IBKR_ACCOUNT_ID",
                    "chmod 600", "ibkr-bot-daily.timer", "kill_switch",
                    "dry_run", "ibkr_bot.state", "2FA", "127.0.0.1",
                    "IBKR_BOT_API_TOKEN", "ibkr-bot-api"):
        assert attendu in contenu, f"{attendu!r} absent de deploy/README-ibkr.md"


def test_the_ibkr_readme_never_asks_for_the_ibkr_password_in_the_env_file():
    """Spec 5.1 : le login et le mot de passe IBKR ne sont JAMAIS stockes
    sur le VPS, sous aucune forme — ils sont saisis a la main dans
    l'interface d'authentification locale du Gateway."""
    contenu = _lire("README-ibkr.md")
    assert "IBKR_PASSWORD" not in contenu
    assert "IBKR_LOGIN" not in contenu


def test_the_api_service_runs_as_the_dedicated_user_on_its_own_port():
    contenu = _lire("ibkr-bot-api.service")
    assert "User=ibkrbot" in contenu
    assert "User=goldbot" not in contenu
    assert "--port 8444" in contenu
    assert "ibkr_bot.api:app" in contenu
    assert "EnvironmentFile=/home/ibkrbot/analyse-or/.env" in contenu
    assert "Restart=on-failure" in contenu


def test_the_api_service_reuses_the_goldbot_fr_certificate():
    contenu = _lire("ibkr-bot-api.service")
    assert "/etc/letsencrypt/live/goldbot.fr/privkey.pem" in contenu
    assert "/etc/letsencrypt/live/goldbot.fr/fullchain.pem" in contenu


def test_the_firewall_script_opens_the_api_port():
    contenu = _lire("harden-vps-firewall.sh")
    assert "ufw allow 8444" in contenu


def test_the_tls_setup_script_adds_ibkrbot_to_the_ssl_cert_group_and_a_renewal_hook():
    contenu = _lire("setup-tls-ibkr.sh")
    assert "usermod -aG ssl-cert ibkrbot" in contenu
    assert "systemctl restart ibkr-bot-api" in contenu


def test_the_gold_readme_points_to_the_ibkr_one():
    assert "README-ibkr.md" in _lire("README.md")
