# gold_bot/notify.py
# Résumé quotidien par email des décisions du bot — même conventions
# SMTP que indices_score.py (voir SMTP_HOST/SMTP_PORT/variables
# d'environnement ci-dessous). Un email par jour, jamais par trade.
import html
import json
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
# Bien au-dessus de POLL_INTERVAL_SECONDS (60s, gold_bot.loop) -- ne doit
# jamais se déclencher sur un cycle normal ou une retentative réseau,
# seulement sur un arrêt réel du service. Voir build_summary_email_html.
STALE_CYCLE_THRESHOLD_MINUTES = 30

# Calculé indépendamment de gold_bot.loop plutôt qu'importé de là —
# les deux modules résolvent au même chemin car tous deux basés sur
# leur propre __file__ dans le même package gold_bot/, sans avoir à se
# dépendre l'un l'autre pour une simple constante de chemin.
DECISIONS_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decisions_log.jsonl")


def read_todays_decisions(path: str = DECISIONS_LOG_PATH, today: str | None = None) -> list[dict]:
    """Lit les décisions journalisées dont l'horodatage tombe le jour
    UTC demandé (aujourd'hui par défaut). Fichier absent/illisible ou
    lignes corrompues -> ignorées silencieusement, jamais d'exception."""
    if today is None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    decisions = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("timestamp", "").startswith(today):
                    decisions.append(entry)
    except Exception:
        return []
    return decisions


def _stale_cycle_warning_html(last_timestamp: str, now: datetime) -> str:
    """Avertissement visible si le dernier cycle journalisé remonte à
    plus de STALE_CYCLE_THRESHOLD_MINUTES -- distingue "le marché était
    calme" de "le service s'est arrêté en cours de journée" (ex : boucle
    de redémarrage systemd épuisée), deux situations qui produisaient
    jusque-là le même résumé silencieux/rassurant. Trouvé lors de
    l'audit pré-lancement du 2026-09-29. Un horodatage illisible est
    traité comme suspect (avertissement affiché), jamais comme une
    exception."""
    try:
        last_dt = datetime.strptime(last_timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        gap_minutes = (now - last_dt).total_seconds() / 60
        if gap_minutes <= STALE_CYCLE_THRESHOLD_MINUTES:
            return ""
        detail = f"il y a {gap_minutes:.0f} min"
    except ValueError:
        detail = "horodatage illisible"
    return (
        '<p style="color:#a35540;font-size:13px;font-weight:bold;margin:0 0 16px;">'
        f'⚠ Dernier cycle journalisé {detail} — le bot est peut-être arrêté '
        '(vérifier systemctl status gold-bot-loop).</p>'
    )


def build_summary_email_html(decisions: list[dict], day: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if not decisions:
        body_html = (
            '<p style="color:#a35540;font-size:13px;font-weight:bold;margin:0 0 16px;">'
            '⚠ Aucun cycle journalisé aujourd\'hui — le bot est peut-être arrêté '
            '(vérifier systemctl status gold-bot-loop).</p>'
        )
    else:
        last_timestamp = max((d.get("timestamp") for d in decisions if d.get("timestamp")), default=None)
        executed = [d for d in decisions if d.get("action") == "exécuté"]
        errors = [d for d in decisions if d.get("action") == "erreur"]
        body_html = (
            (_stale_cycle_warning_html(last_timestamp, now) if last_timestamp else "")
            + f'<p style="color:#edeef3;font-size:14px;margin:0 0 16px;">'
            f'{len(decisions)} cycle(s) évalué(s), {len(executed)} ordre(s) exécuté(s), '
            f'{len(errors)} erreur(s).</p>'
        )
        for d in executed:
            for step in d.get("steps", []):
                body_html += (
                    f'<p style="margin:0 0 8px;padding:10px 14px;background:#1b1d25;'
                    f'border-left:3px solid #2a2d38;font-family:Arial,sans-serif;">'
                    f'<span style="color:#edeef3;font-size:13px;">{html.escape(str(step.get("type")))} — '
                    f'{html.escape(str(step.get("symbol")))} {step.get("direction", "")}</span></p>'
                )
        for d in errors:
            reason = d.get("reason")
            if reason is None:
                # Forme "exécution partielle en échec" (gold_bot.loop) : pas de
                # clé "reason" de premier niveau, le détail est par étape dans
                # "results".
                failed_steps = [r for r in d.get("results", []) if r.get("error")]
                if failed_steps:
                    detail = "; ".join(
                        f'{html.escape(str(r["step"].get("type", "?")))} '
                        f'{html.escape(str(r["step"].get("symbol", "")))} : {html.escape(str(r["error"]))}'
                        for r in failed_steps
                    )
                else:
                    detail = "échec d'exécution (détail indisponible)"
            else:
                detail = html.escape(str(reason))
            body_html += (
                f'<p style="margin:0 0 8px;padding:10px 14px;background:#1b1d25;'
                f'border-left:3px solid #a35540;font-family:Arial,sans-serif;">'
                f'<span style="color:#a35540;font-size:13px;">Erreur — {detail}</span></p>'
            )
    return f"""
    <html><body style="background:#15161c;margin:0;padding:0;">
      <div style="max-width:480px;margin:0 auto;padding:32px 24px;font-family:Arial,Helvetica,sans-serif;">
        <p style="color:#8a90a3;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;margin:0 0 10px;">
          Bot Or — Résumé du {day}
        </p>
        {body_html}
      </div>
    </body></html>
    """


def send_daily_summary(decisions: list[dict], day: str | None = None) -> bool:
    """Envoie le résumé quotidien. Ignoré silencieusement (avec un
    message) si SMTP_USER/SMTP_PASSWORD ne sont pas configurés — même
    contrat que indices_score.send_daily_digest_email, jamais
    d'exception."""
    if day is None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    mail_to = os.environ.get("MAIL_TO") or smtp_user
    if not smtp_user or not smtp_password:
        print("\n(Envoi du résumé quotidien Bot Or ignoré : SMTP_USER / SMTP_PASSWORD non configurés.)")
        return False
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            msg = MIMEMultipart("mixed")
            msg["Subject"] = f"Bot Or — résumé du {day}"
            msg["From"] = smtp_user
            msg["To"] = mail_to
            msg.attach(MIMEText(build_summary_email_html(decisions, day), "html"))
            server.sendmail(smtp_user, [mail_to], msg.as_string())
        print(f"Résumé quotidien Bot Or envoyé ({len(decisions)} cycle(s)).")
        return True
    except Exception as e:
        print(f"Erreur envoi résumé quotidien Bot Or : {e}")
        return False


def main():
    decisions = read_todays_decisions()
    send_daily_summary(decisions)


if __name__ == "__main__":
    main()
