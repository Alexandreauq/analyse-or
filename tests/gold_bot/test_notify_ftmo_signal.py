import json
import os

import gold_bot.notify_ftmo_signal as notify_ftmo_signal


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


def _signal_decision(entry=2650.0, stop_loss=2645.0, direction="achat", symbol="XAUUSD"):
    return {
        "action": "simulation",
        "symbol": symbol,
        "steps": [{
            "type": "ouverture_simulee", "symbol": symbol, "direction": direction,
            "volume": 0.1, "entry": entry, "stop_loss": stop_loss, "take_profit": None,
        }],
    }


def test_no_email_when_action_is_not_simulation(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    decision = {"action": "aucune", "reason": "pas de signal d'entree"}
    result = notify_ftmo_signal.maybe_send_signal_email(decision, state_path=str(tmp_path / "state.json"))
    assert result is False


def test_no_email_when_simulation_has_no_opening_step(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    decision = {"action": "simulation", "steps": [{"type": "modification_simulee", "symbol": "XAUUSD"}]}
    result = notify_ftmo_signal.maybe_send_signal_email(decision, state_path=str(tmp_path / "state.json"))
    assert result is False


def test_skipped_when_smtp_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    decision = _signal_decision()
    result = notify_ftmo_signal.maybe_send_signal_email(decision, state_path=str(tmp_path / "state.json"))
    assert result is False


def test_new_signal_sends_email_and_persists_state(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    _FakeSMTP.sent = []
    monkeypatch.setattr(notify_ftmo_signal.smtplib, "SMTP", _FakeSMTP)
    state_path = str(tmp_path / "state.json")

    decision = _signal_decision()
    result = notify_ftmo_signal.maybe_send_signal_email(decision, state_path=state_path)

    assert result is True
    assert len(_FakeSMTP.sent) == 1
    from_addr, to_addrs, message = _FakeSMTP.sent[0]
    assert from_addr == "bot@example.com"
    assert to_addrs == ["bot@example.com"]
    assert "XAUUSD" in message
    assert "achat" in message

    with open(state_path, encoding="utf-8") as fh:
        state = json.load(fh)
    assert state["last_notified_signal_id"] == "XAUUSD|achat|2650.0|2645.0"


def test_same_signal_replayed_does_not_send_second_email(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    _FakeSMTP.sent = []
    monkeypatch.setattr(notify_ftmo_signal.smtplib, "SMTP", _FakeSMTP)
    state_path = str(tmp_path / "state.json")

    decision = _signal_decision()
    first = notify_ftmo_signal.maybe_send_signal_email(decision, state_path=state_path)
    second = notify_ftmo_signal.maybe_send_signal_email(decision, state_path=state_path)

    assert first is True
    assert second is False
    assert len(_FakeSMTP.sent) == 1


def test_different_signal_sends_a_new_email(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    _FakeSMTP.sent = []
    monkeypatch.setattr(notify_ftmo_signal.smtplib, "SMTP", _FakeSMTP)
    state_path = str(tmp_path / "state.json")

    first = notify_ftmo_signal.maybe_send_signal_email(
        _signal_decision(entry=2650.0, stop_loss=2645.0), state_path=state_path)
    second = notify_ftmo_signal.maybe_send_signal_email(
        _signal_decision(entry=2660.0, stop_loss=2655.0), state_path=state_path)

    assert first is True
    assert second is True
    assert len(_FakeSMTP.sent) == 2


def test_state_persists_across_separate_calls_reading_fresh_from_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    _FakeSMTP.sent = []
    monkeypatch.setattr(notify_ftmo_signal.smtplib, "SMTP", _FakeSMTP)
    state_path = str(tmp_path / "state.json")

    decision = _signal_decision()
    notify_ftmo_signal.maybe_send_signal_email(decision, state_path=state_path)
    assert os.path.exists(state_path)

    # Nouvel appel independant (simule un redemarrage du process) : lit
    # l'etat ecrit par l'appel precedent, pas une variable en memoire.
    result = notify_ftmo_signal.maybe_send_signal_email(decision, state_path=state_path)
    assert result is False
