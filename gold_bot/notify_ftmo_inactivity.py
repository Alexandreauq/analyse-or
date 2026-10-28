# gold_bot/notify_ftmo_inactivity.py
# Email d'alerte si le compte FTMO n'a ouvert aucun trade reel depuis
# INACTIVITY_ALERT_AFTER_DAYS -- marge avant la clause FTMO 13.2.3(a)
# des "FTMO Challenge Terms and Conditions" (lues le 2026-10-28,
# version a jour du 12 octobre 2026) : 30 jours calendaires consecutifs
# sans aucun trade ouvert -> FTMO peut resilier le compte pour
# inactivite prolongee. N'est jamais appele pour l'instance perso --
# voir le site d'appel dans gold_bot/loop.py, conditionne a
# ftmo_guard_instance (meme convention que notify_ftmo_signal.py).
#
# Aucune fonction SMTP partagee n'existe dans ce depot (meme bloc
# duplique dans notify.py/notify_ftmo_signal.py/indices_score.py/
# gold_score.py/ibkr_bot/notify.py) -- ce module suit la meme
# convention plutot que d'introduire une lib partagee hors du
# perimetre demande.
import json
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# Marge de 8 jours avant le seuil FTMO reel de 30 jours consecutifs
# sans trade (clause 13.2.3(a)) -- assez de temps pour reagir (trade
# manuel ou contact support FTMO) avant une resiliation possible du
# compte. Choisi par l'utilisateur dans la plage 20-25 jours qu'il a
# validee.
INACTIVITY_ALERT_AFTER_DAYS = 22

DEFAULT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decisions_log.jsonl")
DEFAULT_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ftmo_inactivity_state.json")


def _load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(data: dict, path: str) -> None:
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _is_real_trade_opened(entry: dict) -> bool:
    # Un trade reellement ouvert est journalise par gold_bot.loop avec
    # action "exécuté" (jamais "simulation"/"simulation_dry_run", qui ne
    # placent aucun ordre reel) et au moins une etape d'ouverture sans
    # erreur dans results -- voir gold_bot/loop.py::run_cycle.
    if entry.get("action") != "exécuté":
        return False
    for result in entry.get("results", []):
        step = result.get("step") or {}
        if step.get("type") == "ouverture_simulee" and result.get("error") is None:
            return True
    return False


def last_activity_reference(log_path: str = DEFAULT_LOG_PATH) -> str | None:
    """Horodatage ISO (UTC) du dernier trade FTMO reellement ouvert, ou,
    si aucun trade n'a jamais ete ouvert, l'horodatage de la toute
    premiere decision journalisee -- sert de point de depart du compte
    en mode reel plutot que de n'avoir aucune reference, ce qui
    empecherait toute alerte tant qu'aucun trade n'a jamais eu lieu.
    None si le journal est vide, absent ou illisible (jamais
    d'exception)."""
    last_trade_ts = None
    first_ts = None
    try:
        with open(log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("timestamp")
                if not ts:
                    continue
                if first_ts is None:
                    first_ts = ts
                if _is_real_trade_opened(entry) and (last_trade_ts is None or ts > last_trade_ts):
                    last_trade_ts = ts
    except Exception:
        return None
    return last_trade_ts or first_ts


def _inactivity_email_body(reference: str, days_since: int) -> str:
    return (
        f"Aucun trade reel ouvert sur le compte FTMO depuis {days_since} jours "
        f"(dernier repere : {reference}).\n\n"
        "Regle FTMO (clause 13.2.3(a) des FTMO Challenge Terms and Conditions, "
        "version du 12 octobre 2026) : 30 jours calendaires consecutifs sans "
        "aucun trade ouvert -> le compte peut etre resilie pour inactivite "
        "prolongee.\n\n"
        f"Marge restante avant ce seuil : {max(30 - days_since, 0)} jour(s)."
    )


def maybe_send_inactivity_email(now: datetime, log_path: str = DEFAULT_LOG_PATH,
                                 state_path: str = DEFAULT_STATE_PATH) -> bool:
    """Envoie un email UNIQUEMENT si aucun trade FTMO reel n'a ete
    ouvert depuis INACTIVITY_ALERT_AFTER_DAYS jours ET que cette serie
    d'inactivite n'a pas deja ete notifiee -- dedoublonne sur la
    reference elle-meme (voir last_activity_reference) : un nouveau
    trade change la reference et reautorise un futur email, exactement
    comme _signal_id dans notify_ftmo_signal.py dedoublonne sur le
    contenu du signal plutot que sur un simple booleen. Ne leve jamais
    d'exception -- renvoie False si SMTP_USER/SMTP_PASSWORD absents, si
    le journal est vide, si le seuil n'est pas atteint, ou si deja
    notifie pour cette reference."""
    reference = last_activity_reference(log_path)
    if reference is None:
        return False

    try:
        reference_dt = datetime.strptime(reference, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False

    days_since = (now - reference_dt).days
    if days_since < INACTIVITY_ALERT_AFTER_DAYS:
        return False

    state = _load_state(state_path)
    if state.get("alerted_for_reference") == reference:
        return False

    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    mail_to = os.environ.get("MAIL_TO") or smtp_user
    if not smtp_user or not smtp_password:
        print("\n(Envoi de l'alerte inactivite FTMO ignore : SMTP_USER / SMTP_PASSWORD non configures.)")
        return False

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            msg = MIMEMultipart("mixed")
            msg["Subject"] = f"FTMO : {days_since} jours sans trade (seuil de résiliation FTMO : 30 jours)"
            msg["From"] = smtp_user
            msg["To"] = mail_to
            msg.attach(MIMEText(_inactivity_email_body(reference, days_since), "plain"))
            server.sendmail(smtp_user, [mail_to], msg.as_string())
        print(f"Alerte inactivite FTMO envoyee ({days_since} jours).")
    except Exception as e:
        print(f"Erreur envoi alerte inactivite FTMO : {e}")
        return False

    _save_state({"alerted_for_reference": reference}, state_path)
    return True
