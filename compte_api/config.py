# compte_api/config.py
# Réglages du service, lus une seule fois dans l'environnement (le .env du
# service systemd en production). Les valeurs par défaut sont celles du
# développement local : jamais de secret réel ici.
import os
from dataclasses import dataclass
from urllib.parse import urlparse


def _bool(valeur: str | None, defaut: bool) -> bool:
    if valeur is None:
        return defaut
    return valeur.strip().lower() in ("1", "true", "oui", "yes")


def _int(valeur: str | None, defaut: int) -> int:
    try:
        return int(valeur) if valeur is not None else defaut
    except ValueError:
        return defaut


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///compte.db"
    # En-tête X-Compte-Proxy exigé sur toute requête : posé par le rewrite
    # Vercel (ou scripts/dev_compte.py en local), jamais connu du navigateur.
    proxy_secret: str = "dev-secret"
    # URL publique du site, pour les liens des e-mails.
    site_url: str = "http://127.0.0.1:8000"
    cookie_secure: bool = False
    mailer: str = "console"
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""
    ai_quota_par_jour: int = 10
    assistant_ia_url: str = ""
    assistant_ia_token: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""

    def en_local(self) -> bool:
        hote = urlparse(self.site_url).hostname or ""
        return hote in ("127.0.0.1", "localhost", "::1") or hote.endswith(".test")

    def verifier_production(self) -> None:
        """Refuse de démarrer un service public avec un réglage de
        développement oublié dans le .env : secret du proxy connu, cookie
        sans Secure, e-mails (et donc liens de connexion) écrits dans les
        journaux au lieu d'être envoyés."""
        if self.en_local():
            return
        problemes = []
        if self.proxy_secret == Settings.proxy_secret or len(self.proxy_secret) < 32:
            problemes.append("COMPTE_PROXY_SECRET (32 caractères minimum, différent de la valeur de dev)")
        if not self.cookie_secure:
            problemes.append("COMPTE_COOKIE_SECURE=true")
        if self.mailer != "smtp":
            problemes.append("COMPTE_MAILER=smtp")
        if problemes:
            raise RuntimeError("Réglages de production manquants : " + ", ".join(problemes))

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        env = os.environ if env is None else env
        d = cls()
        return cls(
            database_url=env.get("DATABASE_URL", d.database_url),
            proxy_secret=env.get("COMPTE_PROXY_SECRET", d.proxy_secret),
            site_url=env.get("COMPTE_SITE_URL", d.site_url).rstrip("/"),
            cookie_secure=_bool(env.get("COMPTE_COOKIE_SECURE"), d.cookie_secure),
            mailer=env.get("COMPTE_MAILER", d.mailer),
            smtp_host=env.get("SMTP_HOST", d.smtp_host),
            smtp_port=_int(env.get("SMTP_PORT"), d.smtp_port),
            smtp_user=env.get("SMTP_USER", d.smtp_user),
            smtp_password=env.get("SMTP_PASSWORD", d.smtp_password),
            mail_from=env.get("MAIL_FROM", d.mail_from),
            ai_quota_par_jour=_int(env.get("AI_QUOTA_PAR_JOUR"), d.ai_quota_par_jour),
            assistant_ia_url=env.get("ASSISTANT_IA_URL", d.assistant_ia_url),
            assistant_ia_token=env.get("ASSISTANT_IA_TOKEN", d.assistant_ia_token),
            google_client_id=env.get("GOOGLE_CLIENT_ID", d.google_client_id),
            google_client_secret=env.get("GOOGLE_CLIENT_SECRET", d.google_client_secret),
        )
