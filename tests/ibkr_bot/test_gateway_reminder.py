import ibkr_bot.gateway_reminder as gateway_reminder


def test_main_calls_send_gateway_reauth_reminder(monkeypatch):
    calls = []
    monkeypatch.setattr(gateway_reminder.notify, "send_gateway_reauth_reminder",
                        lambda: calls.append(True) or True)

    gateway_reminder.main()

    assert calls == [True]
