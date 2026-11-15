# compte_api/mailer.py
# Envoi d'e-mails. ConsoleMailer en développement et dans les tests (garde
# les e-mails en mémoire), SmtpMailer en production avec le SMTP existant.
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage

from compte_api.config import Settings


@dataclass
class EmailEnvoye:
    destinataire: str
    sujet: str
    texte: str


class Mailer:
    def envoyer(self, destinataire: str, sujet: str, texte: str) -> None:
        raise NotImplementedError


@dataclass
class ConsoleMailer(Mailer):
    afficher: bool = True
    envoyes: list[EmailEnvoye] = field(default_factory=list)

    def envoyer(self, destinataire: str, sujet: str, texte: str) -> None:
        self.envoyes.append(EmailEnvoye(destinataire, sujet, texte))
        if self.afficher:
            print(f"\n--- E-mail (console) pour {destinataire} : {sujet}\n{texte}\n---")


class SmtpMailer(Mailer):
    def __init__(self, settings: Settings):
        self.settings = settings

    def envoyer(self, destinataire: str, sujet: str, texte: str) -> None:
        s = self.settings
        message = EmailMessage()
        message["From"] = s.mail_from or s.smtp_user
        message["To"] = destinataire
        message["Subject"] = sujet
        message.set_content(texte)
        contexte = ssl.create_default_context()
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=contexte, timeout=20) as smtp:
                smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
                smtp.starttls(context=contexte)
                smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(message)


def build_mailer(settings: Settings) -> Mailer:
    return SmtpMailer(settings) if settings.mailer == "smtp" else ConsoleMailer()
