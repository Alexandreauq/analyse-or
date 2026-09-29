import json
from datetime import datetime, timezone

import gold_bot.loop as loop
import gold_bot.notify as notify


def test_read_todays_decisions_filters_by_date(tmp_path):
    path = tmp_path / "decisions_log.jsonl"
    path.write_text(
        json.dumps({"timestamp": "2026-09-10T08:00:00Z", "action": "aucune"}) + "\n" +
        json.dumps({"timestamp": "2026-09-09T08:00:00Z", "action": "aucune"}) + "\n" +
        json.dumps({"timestamp": "2026-09-10T09:00:00Z", "action": "exécuté"}) + "\n",
        encoding="utf-8",
    )
    result = notify.read_todays_decisions(str(path), today="2026-09-10")
    assert len(result) == 2
    assert all(d["timestamp"].startswith("2026-09-10") for d in result)


def test_read_todays_decisions_returns_empty_list_when_file_absent(tmp_path):
    path = tmp_path / "does_not_exist.jsonl"
    assert notify.read_todays_decisions(str(path), today="2026-09-10") == []


def test_read_todays_decisions_skips_corrupted_lines(tmp_path):
    path = tmp_path / "decisions_log.jsonl"
    path.write_text(
        "{not valid json\n" + json.dumps({"timestamp": "2026-09-10T08:00:00Z", "action": "aucune"}) + "\n",
        encoding="utf-8",
    )
    result = notify.read_todays_decisions(str(path), today="2026-09-10")
    assert len(result) == 1


def test_build_summary_email_html_surfaces_reason_errors():
    decisions = [{"action": "erreur", "reason": "Twelve Data indisponible"}]
    html = notify.build_summary_email_html(decisions, "2026-09-10")
    assert "Twelve Data indisponible" in html


def test_build_summary_email_html_surfaces_partial_execution_errors():
    decisions = [{
        "action": "erreur",
        "steps": [{"type": "ouverture_simulee", "symbol": "XAUUSD"}],
        "results": [{
            "step": {"type": "ouverture_simulee", "symbol": "XAUUSD"},
            "result": None,
            "error": "échec MetaApi",
        }],
    }]
    html = notify.build_summary_email_html(decisions, "2026-09-10")
    assert "échec MetaApi" in html
    assert "XAUUSD" in html


def test_build_summary_email_html_warns_when_no_decisions_at_all():
    """Trouvé lors de l'audit pré-lancement du 2026-09-29 : un service
    arrêté toute la journée (ex : boucle de redémarrage systemd épuisée)
    produisait le même résumé neutre qu'un jour de marché calme."""
    html = notify.build_summary_email_html([], "2026-09-10")
    assert "peut-être arrêté" in html


def test_build_summary_email_html_no_stale_warning_when_last_cycle_is_recent():
    now = datetime(2026, 9, 10, 23, 55, tzinfo=timezone.utc)
    decisions = [{"action": "aucune", "reason": "signal neutre", "timestamp": "2026-09-10T23:54:00Z"}]
    html = notify.build_summary_email_html(decisions, "2026-09-10", now=now)
    assert "peut-être arrêté" not in html


def test_build_summary_email_html_stale_warning_when_last_cycle_is_old():
    now = datetime(2026, 9, 10, 23, 55, tzinfo=timezone.utc)
    # Dernier cycle à 14h00, plus de 30 min avant "now" (23h55) -> le
    # service s'est probablement arrêté en cours de journée.
    decisions = [{"action": "aucune", "reason": "signal neutre", "timestamp": "2026-09-10T14:00:00Z"}]
    html = notify.build_summary_email_html(decisions, "2026-09-10", now=now)
    assert "peut-être arrêté" in html
    assert "595 min" in html  # 23h55 - 14h00 = 9h55 = 595 min


def test_build_summary_email_html_uses_the_most_recent_timestamp_among_decisions():
    now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    decisions = [
        {"action": "aucune", "reason": "signal neutre", "timestamp": "2026-09-10T08:00:00Z"},
        {"action": "aucune", "reason": "signal neutre", "timestamp": "2026-09-10T11:50:00Z"},
    ]
    html = notify.build_summary_email_html(decisions, "2026-09-10", now=now)
    assert "peut-être arrêté" not in html  # le plus récent (11h50) est à 10 min de "now", sous le seuil


def test_send_daily_summary_skipped_when_smtp_not_configured(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    assert notify.send_daily_summary([], day="2026-09-10") is False


def test_send_daily_summary_sends_email_when_configured(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    sent = {}

    class _FakeSMTP:
        def __init__(self, host, port):
            sent["host_port"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            sent["login"] = (user, password)

        def sendmail(self, from_addr, to_addrs, message):
            sent["sendmail"] = (from_addr, to_addrs)

    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)
    result = notify.send_daily_summary([{"action": "exécuté", "steps": []}], day="2026-09-10")

    assert result is True
    assert sent["host_port"] == (notify.SMTP_HOST, notify.SMTP_PORT)
    assert sent["login"] == ("bot@example.com", "secret")
    assert sent["sendmail"][0] == "bot@example.com"


def test_decisions_log_path_matches_loop_module():
    assert notify.DECISIONS_LOG_PATH == loop.DECISIONS_LOG_PATH
