from datetime import datetime, timezone

import pytest

import gold_bot.macro_signal as macro_signal


def _payload(composite_score=20.0, cftc_percentile=50.0, ma200=4000.0, alerts=None):
    return {
        "composite_score": composite_score,
        "cftc_percentile": cftc_percentile,
        "technical": {"ma200": ma200, "spot": ma200},
        "alerts": alerts if alerts is not None else [],
    }


def test_has_entry_alert_true_when_entree_alert_present():
    payload = _payload(alerts=[{"kind": "entree", "title": "Conditions d'entrée réunies"}])
    assert macro_signal.has_entry_alert(payload) is True


def test_has_entry_alert_false_when_no_entree_alert():
    payload = _payload(alerts=[{"kind": "risque", "title": "Positionnement CFTC en zone extrême"}])
    assert macro_signal.has_entry_alert(payload) is False


def test_has_entry_alert_false_when_alerts_missing():
    payload = {"composite_score": 20.0}
    assert macro_signal.has_entry_alert(payload) is False


def test_compute_entry_levels_nominal():
    payload = _payload(ma200=4000.0)
    levels = macro_signal.compute_entry_levels(payload, current_price=4010.0)
    assert levels["entry"] == 4010.0
    assert levels["stop_loss"] == pytest.approx(4000.0 * 0.97)
    distance = 4010.0 - 4000.0 * 0.97
    assert levels["take_profit"] == pytest.approx(4010.0 + 3.0 * distance)


def test_compute_entry_levels_none_when_ma200_missing():
    payload = _payload(ma200=None)
    assert macro_signal.compute_entry_levels(payload, current_price=4010.0) is None


def test_compute_entry_levels_none_when_stop_distance_not_positive():
    # MM200 très supérieure au prix courant -> stop calculé au-dessus du prix.
    payload = _payload(ma200=5000.0)
    assert macro_signal.compute_entry_levels(payload, current_price=4010.0) is None


def test_should_exit_true_on_composite_at_or_below_threshold():
    payload = _payload(composite_score=0.0, cftc_percentile=50.0)
    should_exit, reason = macro_signal.should_exit(payload, None, datetime(2026, 9, 29, tzinfo=timezone.utc))
    assert should_exit is True
    assert "composite" in reason


def test_should_exit_true_on_cftc_extreme():
    payload = _payload(composite_score=20.0, cftc_percentile=90.0)
    should_exit, reason = macro_signal.should_exit(payload, None, datetime(2026, 9, 29, tzinfo=timezone.utc))
    assert should_exit is True
    assert "CFTC" in reason


def test_should_exit_true_on_max_holding_days_exceeded():
    payload = _payload(composite_score=20.0, cftc_percentile=50.0)
    open_time_iso = "2026-08-01T00:00:00.000Z"
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)  # 59 jours plus tard
    should_exit, reason = macro_signal.should_exit(payload, open_time_iso, now)
    assert should_exit is True
    assert "durée" in reason


def test_should_exit_false_when_no_condition_met():
    payload = _payload(composite_score=20.0, cftc_percentile=50.0)
    open_time_iso = "2026-09-20T00:00:00.000Z"
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)  # 9 jours plus tard
    should_exit, reason = macro_signal.should_exit(payload, open_time_iso, now)
    assert should_exit is False


def test_should_exit_ignores_unparseable_open_time():
    payload = _payload(composite_score=20.0, cftc_percentile=50.0)
    should_exit, reason = macro_signal.should_exit(payload, "pas une date", datetime(2026, 9, 29, tzinfo=timezone.utc))
    assert should_exit is False


def test_fetch_macro_payload_returns_parsed_json(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"composite_score": 20.0}

    captured = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(macro_signal.requests, "get", fake_get)
    payload = macro_signal.fetch_macro_payload()
    assert payload == {"composite_score": 20.0}
    assert captured["url"] == macro_signal.SCORE_JSON_URL
