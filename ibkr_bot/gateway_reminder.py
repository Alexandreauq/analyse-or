# ibkr_bot/gateway_reminder.py
# Point d'entree du timer systemd hebdomadaire (dimanche) : envoie le
# rappel "reauthentifier le Gateway aujourd'hui" — voir
# notify.build_gateway_reauth_reminder_html pour le pourquoi (AUTO_RESTART_TIME
# rend les redemarrages des autres jours silencieux, celui du dimanche
# reste le seul qui exige un geste manuel).
import ibkr_bot.notify as notify


def main() -> None:
    notify.send_gateway_reauth_reminder()


if __name__ == "__main__":
    main()
