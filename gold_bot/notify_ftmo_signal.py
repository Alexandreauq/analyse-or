# gold_bot/notify_ftmo_signal.py
# Email d'alerte sur NOUVEAU signal d'entree FTMO -- distinct du resume
# quotidien (gold_bot/notify.py, un email/jour, jamais par trade). Ici
# c'est l'inverse : un email par signal d'entree reellement nouveau,
# jamais de resume periodique (voir conception validee par
# l'utilisateur). N'est jamais appele pour l'instance perso -- voir le
# site d'appel dans gold_bot/loop.py, conditionne a ftmo_guard_instance.
#
# Aucune fonction SMTP partagee n'existe dans ce depot (meme bloc
# duplique dans notify.py/indices_score.py/gold_score.py/
# ibkr_bot/notify.py) -- ce module suit la meme convention plutot que
# d'introduire une lib partagee hors du perimetre demande.
import json
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

DEFAULT_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ftmo_signal_state.json")


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


def _first_opening_step(decision: dict) -> dict | None:
    if decision.get("action") != "simulation":
        return None
    for step in decision.get("steps", []):
        if step.get("type") == "ouverture_simulee":
            return step
    return None


def _signal_id(symbol: str, step: dict) -> str:
    # Identifiant base sur le CONTENU du signal (symbole/direction/
    # entree/stop), pas un horodatage -- `decision` n'en porte pas a ce
    # stade (ajoute seulement par _log_decision sur sa propre copie, voir
    # gold_bot/loop.py). Consequence voulue : tant que l'entree/le stop
    # calcules restent les memes d'un cycle a l'autre (meme signal
    # persistant, rien ne l'a encore "ouvert" puisque dry_run ne place
    # jamais de vrai ordre), un seul email part. Un vrai nouveau signal
    # (entree/stop recalcules suite a un mouvement de prix) obtient un
    # nouvel identifiant et redeclenche un email.
    entry = step.get("entry")
    stop_loss = step.get("stop_loss")
    entry_r = round(entry, 2) if isinstance(entry, (int, float)) else entry
    stop_r = round(stop_loss, 2) if isinstance(stop_loss, (int, float)) else stop_loss
    return f"{symbol}|{step.get('direction')}|{entry_r}|{stop_r}"


def _signal_email_body(symbol: str, step: dict) -> str:
    direction = step.get("direction", "?")
    entry = step.get("entry")
    stop_loss = step.get("stop_loss")
    take_profit = step.get("take_profit")
    volume = step.get("volume")
    lines = [
        f"Symbole : {symbol}",
        f"Direction : {direction}",
        f"Volume : {volume}",
        f"Entree : {entry}",
        f"Stop : {stop_loss}",
        f"Take profit : {take_profit if take_profit is not None else '-'}",
    ]
    return "\n".join(lines)


def maybe_send_signal_email(decision: dict, state_path: str = DEFAULT_STATE_PATH) -> bool:
    """Envoie un email UNIQUEMENT si `decision` contient un nouveau
    signal d'entree (action "simulation" avec une etape
    "ouverture_simulee") qui n'a pas deja ete notifie (voir _signal_id).
    Ne leve jamais d'exception -- renvoie False si SMTP_USER/
    SMTP_PASSWORD absents (meme contrat que notify.send_daily_summary),
    si aucun signal, ou si le signal a deja ete notifie."""
    step = _first_opening_step(decision)
    if step is None:
        return False

    symbol = decision.get("symbol", step.get("symbol", "?"))
    signal_id = _signal_id(symbol, step)

    state = _load_state(state_path)
    if state.get("last_notified_signal_id") == signal_id:
        return False

    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    mail_to = os.environ.get("MAIL_TO") or smtp_user
    if not smtp_user or not smtp_password:
        print("\n(Envoi de l'alerte signal FTMO ignore : SMTP_USER / SMTP_PASSWORD non configures.)")
        return False

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            msg = MIMEMultipart("mixed")
            msg["Subject"] = f"Signal FTMO : {step.get('direction', '?')} {symbol}"
            msg["From"] = smtp_user
            msg["To"] = mail_to
            msg.attach(MIMEText(_signal_email_body(symbol, step), "plain"))
            server.sendmail(smtp_user, [mail_to], msg.as_string())
        print(f"Alerte signal FTMO envoyee ({symbol} {step.get('direction')}).")
    except Exception as e:
        print(f"Erreur envoi alerte signal FTMO : {e}")
        return False

    _save_state({"last_notified_signal_id": signal_id}, state_path)
    return True
