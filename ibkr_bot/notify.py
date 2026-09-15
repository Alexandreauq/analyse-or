# ibkr_bot/notify.py
# Deux emails, une seule plomberie SMTP (spec 4.9 / 5.5) :
#   1. le RESUME QUOTIDIEN, envoye a la fin de chaque batch qui s'est
#      deroule (achats, ventes, signaux ignores et pourquoi, anomalies,
#      erreurs, etat du Gateway) — une fois par jour, jamais par trade ;
#   2. l'ALERTE IMMEDIATE "Gateway non authentifie", envoyee UNIQUEMENT
#      quand le preflight a echoue ses 3 tentatives. C'est le seul cas ou
#      l'utilisateur doit agir le jour meme (reauthentifier le Gateway,
#      2FA comprise), donc le seul qui merite un email distinct plutot
#      qu'une ligne dans le resume.
#
# Quand l'alerte part, le resume ne part pas : le corps de l'alerte dit
# deja qu'aucun ordre n'a ete passe, un second email vide noierait
# l'alerte.
#
# Memes conventions SMTP que gold_bot/notify.py (qui reutilise deja les
# identifiants SMTP des alertes Indices) : SMTP_USER / SMTP_PASSWORD /
# MAIL_TO dans l'environnement, jamais d'exception vers l'appelant.
import html
import os
import smtplib
from datetime import datetime, timezone
from email.charset import QP, Charset
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import ibkr_bot.journal as journal

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# Encodage quoted-printable plutot que le base64 par defaut de MIMEText
# pour un corps utf-8 : le corps garde des caracteres accentues lisibles
# (ecart_paper_pct, resume...), donc un encodage base64 les aurait rendus
# illisibles tels quels dans le fichier .eml brut — quoted-printable
# laisse l'ASCII (tickers, montants) en clair et n'encode que les
# caracteres non-ASCII. Charset() est instancie localement (jamais via
# email.charset.add_charset, qui modifierait le registre global du
# process) pour ne pas affecter gold_bot.notify ou tout autre module qui
# construit aussi des MIMEText utf-8.
_CHARSET_UTF8_QP = Charset("utf-8")
_CHARSET_UTF8_QP.body_encoding = QP

_FOND = "background:#15161c"
_CARTE = ("margin:0 0 8px;padding:10px 14px;background:#1b1d25;"
          "font-family:Arial,sans-serif;border-left:3px solid ")


def _e(valeur) -> str:
    return html.escape(str(valeur))


def _carte(couleur: str, texte: str) -> str:
    return (f'<p style="{_CARTE}{couleur};">'
            f'<span style="color:#edeef3;font-size:13px;">{texte}</span></p>')


def _page(titre: str, corps: str) -> str:
    return f"""
    <html><body style="{_FOND};margin:0;padding:0;">
      <div style="max-width:560px;margin:0 auto;padding:32px 24px;font-family:Arial,Helvetica,sans-serif;">
        <p style="color:#8a90a3;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;margin:0 0 10px;">
          {titre}
        </p>
        {corps}
      </div>
    </body></html>
    """


def _prix(record: dict) -> str:
    prix = record.get("prix_execution")
    if not isinstance(prix, (int, float)) or isinstance(prix, bool):
        return "prix indisponible"
    estime = " (estimé)" if record.get("prix_execution_estime") else ""
    return f'{prix:.4g} {_e(record.get("devise_compte", ""))}{estime}'


def build_summary_email_html(run: dict, day: str) -> str:
    """Resume quotidien. Ne doit JAMAIS lever, meme sur une ligne de
    journal partielle : un email manquant, c'est la perte de la seule
    visibilite quotidienne sur un bot qui manie de l'argent reel — d'ou
    les .get() partout plutot que des acces directs."""
    mode = run.get("mode", "?")
    statut = run.get("statut", "?")
    preflight = run.get("preflight") or {}
    etat_gateway = ("authentifié" if preflight.get("ok")
                    else f'NON authentifié ({preflight.get("tentatives", 0)} tentative(s))')

    corps = (
        f'<p style="color:#edeef3;font-size:14px;margin:0 0 16px;">'
        f'Mode <b>{_e(mode)}</b> — statut <b>{_e(statut)}</b> — '
        f'Gateway : {_e(etat_gateway)}.</p>'
    )
    if mode == "dry_run":
        corps += _carte("#7a6a2a",
                        "Mode simulation (dry_run) : aucun ordre réel n'a été envoyé.")

    entrees = run.get("entrees") or []
    sorties = run.get("sorties") or []
    rejets = run.get("signaux_rejetes") or []
    anomalies = run.get("anomalies") or []
    erreurs = run.get("erreurs") or []

    corps += (f'<p style="color:#8a90a3;font-size:12px;margin:16px 0 8px;">'
              f'{len(entrees)} achat(s), {len(sorties)} vente(s), '
              f'{len(rejets)} signal(aux) ignoré(s), {len(erreurs)} erreur(s).</p>')

    for record in sorties:
        couleur = "#3f6f4a" if record.get("statut") == "execute" else "#a35540"
        corps += _carte(couleur, (
            f'VENTE {_e(record.get("ticker"))} × {_e(record.get("quantite"))} — '
            f'{_e(record.get("close_reason"))} — {_prix(record)} — '
            f'{_e(record.get("statut"))}'
            + (f' — {_e(record.get("detail"))}' if record.get("detail") else "")))

    for record in entrees:
        couleur = "#3f6f4a" if record.get("statut") == "execute" else "#a35540"
        ecart = record.get("ecart_paper_pct")
        ecart_txt = (f' — écart paper {ecart:+.2f} %'
                     if isinstance(ecart, (int, float)) and not isinstance(ecart, bool)
                     else "")
        corps += _carte(couleur, (
            f'ACHAT {_e(record.get("ticker"))} × {_e(record.get("quantite"))} '
            f'(rang {_e(record.get("rang"))}) — {_prix(record)}{ecart_txt} — '
            f'{_e(record.get("statut"))}'
            + (f' — {_e(record.get("detail"))}' if record.get("detail") else "")))

    for rejet in rejets:
        corps += _carte("#4a4d5a", (
            f'Signal ignoré — {_e(rejet.get("ticker"))} : '
            f'{_e(rejet.get("raison"))}'
            + (f' (rang {_e(rejet.get("rang"))})' if rejet.get("rang") else "")))

    reconciliation = run.get("reconciliation") or {}
    if reconciliation:
        hors_bot = reconciliation.get("cloturees_hors_bot") or []
        corps += _carte("#4a4d5a", (
            f'Réconciliation — {_e(reconciliation.get("actives", 0))} position(s) '
            f'active(s), {len(hors_bot)} clôturée(s) hors bot, '
            f'{_e(reconciliation.get("ignorees", 0))} position(s) du compte ignorée(s).'))
        for anomalie in reconciliation.get("anomalies_quantite") or []:
            corps += _carte("#a35540", (
                f'Anomalie de quantité — {_e(anomalie.get("ticker"))} : '
                f'journal {_e(anomalie.get("quantite_locale"))} vs IBKR '
                f'{_e(anomalie.get("quantite_ibkr"))} (IBKR fait foi).'))

    for anomalie in anomalies:
        corps += _carte("#a35540", (
            f'Anomalie — {_e(anomalie.get("type"))} '
            f'{_e(anomalie.get("ticker"))} : {_e(anomalie.get("detail"))}'))

    for erreur in erreurs:
        corps += _carte("#a35540", (
            f'Erreur — {_e(erreur.get("etape"))} : {_e(erreur.get("detail"))}'))

    return _page(f"Bot Actions IBKR — Résumé du {_e(day)}", corps)


def build_gateway_alert_html(tentatives: int, day: str) -> str:
    corps = (
        _carte("#a35540",
               f'Le Gateway IBKR n\'est pas authentifié après {_e(tentatives)} '
               f'tentative(s) espacées de 10 minutes. Le batch du '
               f'{_e(day)} a été abandonné : <b>aucun ordre n\'a été passé</b>, '
               f'ni entrée ni sortie.')
        + _carte("#4a4d5a",
                 "Les signaux d\'entrée du jour sont perdus (pas de file "
                 "d\'attente, règle 3.5). Les sorties éligibles sont simplement "
                 "décalées au batch de demain.")
        + _carte("#4a4d5a",
                 "Action attendue aujourd\'hui : rouvrir l\'interface "
                 "d\'authentification locale du Gateway et se reconnecter "
                 "(login + 2FA). Voir deploy/README-ibkr.md, section "
                 "« Authentifier le Gateway ».")
    )
    return _page(f"Bot Actions IBKR — ALERTE Gateway du {_e(day)}", corps)


def _send(subject: str, body_html: str) -> bool:
    """Plomberie SMTP commune aux deux emails. Ignore silencieusement (avec
    un message) si SMTP_USER/SMTP_PASSWORD ne sont pas configures — meme
    contrat que gold_bot.notify.send_daily_summary, jamais d'exception."""
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    mail_to = os.environ.get("MAIL_TO") or smtp_user
    if not smtp_user or not smtp_password:
        print("\n(Envoi email Bot Actions IBKR ignoré : SMTP_USER / SMTP_PASSWORD non configurés.)")
        return False
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            msg = MIMEMultipart("mixed")
            msg["Subject"] = subject
            msg["From"] = smtp_user
            msg["To"] = mail_to
            msg.attach(MIMEText(body_html, "html", _CHARSET_UTF8_QP))
            server.sendmail(smtp_user, [mail_to], msg.as_string())
        print(f"Email Bot Actions IBKR envoyé : {subject}")
        return True
    except Exception as e:
        print(f"Erreur envoi email Bot Actions IBKR : {e}")
        return False


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def send_daily_summary(run: dict, day: str | None = None) -> bool:
    day = day or _today()
    return _send(f"Bot Actions IBKR — résumé du {day}",
                 build_summary_email_html(run, day))


def send_gateway_alert(tentatives: int, day: str | None = None) -> bool:
    day = day or _today()
    return _send(f"Bot Actions IBKR — ALERTE : Gateway non authentifié ({day})",
                 build_gateway_alert_html(tentatives, day))


def main(day: str | None = None) -> None:
    """Renvoi manuel du resume du jour depuis le journal (le batch
    l'envoie deja lui-meme a la fin de chaque execution — cette entree
    sert a le renvoyer apres coup, par exemple si le SMTP etait tombe)."""
    day = day or _today()
    runs = journal.read_runs(journal.REAL_TRADING_LOG_PATH, day=day)
    if not runs:
        print(f"Aucun batch journalisé le {day}.")
        return
    send_daily_summary(runs[-1], day)


if __name__ == "__main__":
    main()
