import ibkr_bot.notify as notify

RUN = {
    "timestamp": "2026-09-15T14:45:03Z",
    "date": "2026-09-15",
    "mode": "reel",
    "statut": "termine",
    "git_pull": {"ok": True, "detail": ""},
    "preflight": {"ok": True, "tentatives": 1, "detail": "authentifie"},
    "reconciliation": {"actives": 2, "cloturees_hors_bot": ["OLD.PA-2026-03-01"],
                       "anomalies_quantite": [], "ignorees": 3},
    "sorties": [{"ticker": "SAP.DE", "conid": 111, "sens": "SELL", "quantite": 2,
                 "devise_compte": "EUR", "prix_execution": 210.0,
                 "close_reason": "stop_loss", "statut": "execute", "detail": None,
                 "order_id": "77", "prix_paper": None, "ecart_paper_pct": None}],
    "entrees": [{"ticker": "MC.PA", "conid": 17275, "sens": "BUY", "quantite": 5,
                 "devise_compte": "EUR", "prix_execution": 90.5, "prix_paper": 88.0,
                 "ecart_paper_pct": 2.8409, "statut": "execute", "detail": None,
                 "order_id": "78", "rang": 1, "close_reason": None}],
    "signaux_rejetes": [{"ticker": "ADBE", "raison": "signal_ignore_plafond_atteint",
                         "rang": 4, "score": 40.0}],
    "anomalies": [{"type": "prix_reference_absent", "ticker": "III.L",
                   "detail": "position inclosable tant que le prix n'est pas restaure"}],
    "erreurs": [{"etape": "taux_de_change", "detail": "timeout"}],
}


class _FakeSMTP:
    """Faux serveur SMTP — meme convention de faux objet que
    tests/gold_bot/test_broker.py / tests/ibkr_bot/test_gateway.py.
    Aucun test n'ouvre jamais de vraie connexion."""

    instances = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sent = []
        self.logged_in = None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        self.logged_in = (user, password)

    def sendmail(self, sender, to, message):
        self.sent.append({"from": sender, "to": to, "message": message})


def _configure_smtp(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "motdepasse")
    monkeypatch.setenv("MAIL_TO", "moi@example.com")
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)


def test_summary_lists_buys_sells_rejections_anomalies_and_errors():
    html = notify.build_summary_email_html(RUN, "2026-09-15")
    assert "MC.PA" in html          # achat
    assert "SAP.DE" in html         # vente
    assert "stop_loss" in html      # motif de vente
    assert "ADBE" in html           # signal ignore
    assert "signal_ignore_plafond_atteint" in html
    assert "prix_reference_absent" in html
    assert "timeout" in html        # erreur
    assert "2026-09-15" in html


def test_summary_shows_the_gateway_state_and_the_mode():
    html = notify.build_summary_email_html(RUN, "2026-09-15")
    assert "Gateway" in html
    assert "reel" in html.lower() or "réel" in html.lower()


def test_summary_flags_dry_run_prominently():
    run = {**RUN, "mode": "dry_run"}
    html = notify.build_summary_email_html(run, "2026-09-15")
    assert "dry_run" in html.lower() or "simulation" in html.lower()


def test_summary_reports_a_stale_data_batch_without_crashing():
    run = {"date": "2026-09-15", "mode": "dry_run", "statut": "donnees_perimees",
           "sorties": [], "entrees": [], "signaux_rejetes": [], "erreurs": [],
           "anomalies": []}
    html = notify.build_summary_email_html(run, "2026-09-15")
    assert "donnees_perimees" in html or "périmées" in html


def test_summary_handles_a_minimal_run_without_any_key():
    """Le resume ne doit jamais lever sur une ligne de journal partielle :
    un email manquant, c'est la perte de la seule visibilite quotidienne."""
    html = notify.build_summary_email_html({}, "2026-09-15")
    assert "2026-09-15" in html


def test_summary_escapes_html_in_tickers_and_details():
    run = {**RUN, "erreurs": [{"etape": "x", "detail": "<script>alert(1)</script>"}]}
    html = notify.build_summary_email_html(run, "2026-09-15")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_send_daily_summary_sends_through_smtp(monkeypatch):
    _configure_smtp(monkeypatch)

    assert notify.send_daily_summary(RUN, "2026-09-15") is True

    serveur = _FakeSMTP.instances[0]
    assert (serveur.host, serveur.port) == (notify.SMTP_HOST, notify.SMTP_PORT)
    assert serveur.logged_in == ("bot@example.com", "motdepasse")
    assert serveur.sent[0]["to"] == ["moi@example.com"]
    assert "MC.PA" in serveur.sent[0]["message"]


def test_send_daily_summary_returns_false_without_credentials(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)

    assert notify.send_daily_summary(RUN, "2026-09-15") is False
    assert _FakeSMTP.instances == []


def test_send_daily_summary_returns_false_instead_of_raising(monkeypatch):
    _configure_smtp(monkeypatch)

    def boom(host, port):
        raise OSError("smtp injoignable")

    monkeypatch.setattr(notify.smtplib, "SMTP", boom)
    assert notify.send_daily_summary(RUN, "2026-09-15") is False


def test_gateway_alert_says_how_many_attempts_and_that_nothing_was_traded():
    html = notify.build_gateway_alert_html(3, "2026-09-15")
    assert "3" in html
    assert "aucun ordre" in html.lower()
    assert "2026-09-15" in html


def test_send_gateway_alert_uses_a_distinct_subject(monkeypatch):
    _configure_smtp(monkeypatch)

    assert notify.send_gateway_alert(3, "2026-09-15") is True
    message_alerte = _FakeSMTP.instances[0].sent[0]["message"]

    _FakeSMTP.instances = []
    notify.send_daily_summary(RUN, "2026-09-15")
    message_resume = _FakeSMTP.instances[0].sent[0]["message"]

    def _sujet(message):
        for ligne in message.splitlines():
            if ligne.startswith("Subject:"):
                return ligne
        return ""

    assert _sujet(message_alerte) != _sujet(message_resume)
    assert "ALERTE" in _sujet(message_alerte)


def test_send_gateway_alert_returns_false_instead_of_raising(monkeypatch):
    _configure_smtp(monkeypatch)
    monkeypatch.setattr(notify.smtplib, "SMTP",
                        lambda host, port: (_ for _ in ()).throw(OSError("ko")))
    assert notify.send_gateway_alert(3, "2026-09-15") is False


def test_main_resends_the_last_run_of_the_day(monkeypatch, tmp_path):
    _configure_smtp(monkeypatch)
    log = tmp_path / "real_trading_log.jsonl"
    log.write_text(
        '{"timestamp": "2026-09-15T14:45:00Z", "date": "2026-09-15",'
        ' "statut": "termine", "entrees": [{"ticker": "MC.PA", "quantite": 5,'
        ' "statut": "execute", "devise_compte": "EUR", "prix_execution": 90.5}]}\n',
        encoding="utf-8")
    monkeypatch.setattr(notify.journal, "REAL_TRADING_LOG_PATH", str(log))

    notify.main(day="2026-09-15")

    assert "MC.PA" in _FakeSMTP.instances[0].sent[0]["message"]
