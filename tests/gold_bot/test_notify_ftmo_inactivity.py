import json
from datetime import datetime, timezone

import gold_bot.notify_ftmo_inactivity as notify_ftmo_inactivity


class _FakeSMTP:
    sent = []

    def __init__(self, host, port):
        self.host_port = (host, port)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        self.login_args = (user, password)

    def sendmail(self, from_addr, to_addrs, message):
        _FakeSMTP.sent.append((from_addr, to_addrs, message))


def _write_log(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def _executed_trade_entry(timestamp):
    return {
        "action": "exécuté",
        "symbol": "XAUUSD",
        "timestamp": timestamp,
        "results": [{"step": {"type": "ouverture_simulee"}, "error": None}],
    }


def _no_signal_entry(timestamp):
    return {"action": "aucune", "reason": "pas de signal d'entree", "symbol": "XAUUSD", "timestamp": timestamp}


def test_last_activity_reference_is_none_for_missing_log(tmp_path):
    reference = notify_ftmo_inactivity.last_activity_reference(str(tmp_path / "missing.jsonl"))
    assert reference is None


def test_last_activity_reference_uses_most_recent_real_trade(tmp_path):
    log_path = str(tmp_path / "decisions_log.jsonl")
    _write_log(log_path, [
        _no_signal_entry("2026-09-01T10:00:00Z"),
        _executed_trade_entry("2026-09-05T10:00:00Z"),
        _no_signal_entry("2026-09-10T10:00:00Z"),
        _executed_trade_entry("2026-09-20T10:00:00Z"),
    ])
    reference = notify_ftmo_inactivity.last_activity_reference(log_path)
    assert reference == "2026-09-20T10:00:00Z"


def test_last_activity_reference_falls_back_to_first_entry_when_no_trade_ever(tmp_path):
    log_path = str(tmp_path / "decisions_log.jsonl")
    _write_log(log_path, [
        _no_signal_entry("2026-09-01T10:00:00Z"),
        _no_signal_entry("2026-09-10T10:00:00Z"),
    ])
    reference = notify_ftmo_inactivity.last_activity_reference(log_path)
    assert reference == "2026-09-01T10:00:00Z"


def test_no_email_when_log_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    log_path = str(tmp_path / "missing.jsonl")
    state_path = str(tmp_path / "state.json")
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    result = notify_ftmo_inactivity.maybe_send_inactivity_email(now, log_path=log_path, state_path=state_path)
    assert result is False


def test_no_email_when_below_threshold(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    log_path = str(tmp_path / "decisions_log.jsonl")
    state_path = str(tmp_path / "state.json")
    _write_log(log_path, [_executed_trade_entry("2026-10-10T10:00:00Z")])
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)  # 10 jours, sous le seuil de 22
    result = notify_ftmo_inactivity.maybe_send_inactivity_email(now, log_path=log_path, state_path=state_path)
    assert result is False


def test_skipped_when_smtp_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    log_path = str(tmp_path / "decisions_log.jsonl")
    state_path = str(tmp_path / "state.json")
    _write_log(log_path, [_executed_trade_entry("2026-09-01T10:00:00Z")])
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    result = notify_ftmo_inactivity.maybe_send_inactivity_email(now, log_path=log_path, state_path=state_path)
    assert result is False


def test_sends_email_and_persists_state_once_threshold_reached(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    _FakeSMTP.sent = []
    monkeypatch.setattr(notify_ftmo_inactivity.smtplib, "SMTP", _FakeSMTP)
    log_path = str(tmp_path / "decisions_log.jsonl")
    state_path = str(tmp_path / "state.json")
    _write_log(log_path, [_executed_trade_entry("2026-10-01T10:00:00Z")])
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)  # 26 jours pleins, au-dessus du seuil de 22

    result = notify_ftmo_inactivity.maybe_send_inactivity_email(now, log_path=log_path, state_path=state_path)

    assert result is True
    assert len(_FakeSMTP.sent) == 1
    from_addr, to_addrs, message = _FakeSMTP.sent[0]
    assert from_addr == "bot@example.com"
    assert to_addrs == ["bot@example.com"]
    assert "26 jours" in message
    assert "30 jours" in message

    with open(state_path, encoding="utf-8") as fh:
        state = json.load(fh)
    assert state["alerted_for_reference"] == "2026-10-01T10:00:00Z"


def test_same_inactivity_streak_does_not_send_a_second_email(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    _FakeSMTP.sent = []
    monkeypatch.setattr(notify_ftmo_inactivity.smtplib, "SMTP", _FakeSMTP)
    log_path = str(tmp_path / "decisions_log.jsonl")
    state_path = str(tmp_path / "state.json")
    _write_log(log_path, [_executed_trade_entry("2026-10-01T10:00:00Z")])
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)

    first = notify_ftmo_inactivity.maybe_send_inactivity_email(now, log_path=log_path, state_path=state_path)
    second = notify_ftmo_inactivity.maybe_send_inactivity_email(now, log_path=log_path, state_path=state_path)

    assert first is True
    assert second is False
    assert len(_FakeSMTP.sent) == 1


def test_new_trade_after_alert_resets_and_allows_a_future_email(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    _FakeSMTP.sent = []
    monkeypatch.setattr(notify_ftmo_inactivity.smtplib, "SMTP", _FakeSMTP)
    log_path = str(tmp_path / "decisions_log.jsonl")
    state_path = str(tmp_path / "state.json")
    _write_log(log_path, [_executed_trade_entry("2026-10-01T10:00:00Z")])
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    first = notify_ftmo_inactivity.maybe_send_inactivity_email(now, log_path=log_path, state_path=state_path)
    assert first is True

    # Un nouveau trade reel change la reference -- une future inactivite
    # (apres ce nouveau trade) doit pouvoir redeclencher un email.
    _write_log(log_path, [
        _executed_trade_entry("2026-10-01T10:00:00Z"),
        _executed_trade_entry("2026-10-28T10:00:00Z"),
    ])
    later = datetime(2026, 11, 25, tzinfo=timezone.utc)  # 28 jours apres le nouveau trade
    second = notify_ftmo_inactivity.maybe_send_inactivity_email(later, log_path=log_path, state_path=state_path)
    assert second is True
    assert len(_FakeSMTP.sent) == 2
