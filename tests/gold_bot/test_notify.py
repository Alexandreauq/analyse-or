import json

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
