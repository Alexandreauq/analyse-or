import json

import pytest

import ibkr_bot.daily as daily

TODAY = "2026-09-15"


class FakeGateway:
    """Faux gateway : enregistre chaque appel et ne touche jamais au
    reseau. `ordres` reste vide tant qu'aucun ordre n'est envoye — c'est
    l'assertion centrale des tests dry_run/kill_switch."""

    def __init__(self, authentifie=True, positions_ibkr=None, base_cash=10000.0):
        self.authentifie = authentifie
        self.appels = []
        self.ordres = []
        self.confirmations = []
        self.positions_ibkr = positions_ibkr if positions_ibkr is not None else []
        self.base_cash = base_cash
        self.reponse_ordre = [{"order_id": "1234", "order_status": "Submitted"}]
        self.statuts_ordre = {}
        self.echecs = set()

    # --- session ---
    def is_authenticated(self, base_url):
        self.appels.append("is_authenticated")
        return self.authentifie

    def brokerage_accounts(self, base_url):
        self.appels.append("brokerage_accounts")
        return {"accounts": ["U1234567"]}

    def portfolio_accounts(self, base_url):
        self.appels.append("portfolio_accounts")
        return [{"accountId": "U1234567"}]

    # --- donnees ---
    def positions(self, base_url, account_id):
        self.appels.append("positions")
        return list(self.positions_ibkr)

    def base_currency_cash(self, base_url, account_id):
        self.appels.append("base_currency_cash")
        return self.base_cash

    def cash_by_currency(self, base_url, account_id):
        return {"EUR": self.base_cash}

    def exchange_rate(self, base_url, source, target):
        return 1.0 if source == target else {"USD": 1.08, "GBP": 0.86, "CHF": 0.94}[target]

    def search_contract(self, base_url, symbol):
        return [{"conid": SEARCH_CONIDS[symbol], "symbol": symbol,
                 "description": SEARCH_EXCHANGES[symbol],
                 "sections": [{"secType": "STK"}]}]

    def contract_info(self, base_url, conid):
        return INFOS[str(conid)]

    # --- ordres ---
    def place_market_order(self, base_url, account_id, conid, side, quantity):
        self.ordres.append({"conid": conid, "side": side, "quantity": quantity})
        if conid in self.echecs:
            raise RuntimeError("400 Client Error: rejet IBKR")
        return list(self.reponse_ordre)

    def confirm_reply(self, base_url, reply_id, confirmed=True):
        self.confirmations.append(reply_id)
        return [{"order_id": "1234", "order_status": "Submitted"}]

    def order_status(self, base_url, order_id):
        return self.statuts_ordre.get(order_id, {"order_status": "Filled",
                                                 "avgPrice": "90.50"})


SEARCH_CONIDS = {"MC": 17275, "ADBE": 202070, "SAP": 40000, "III": 98765}
SEARCH_EXCHANGES = {"MC": "SBF", "ADBE": "NASDAQ", "SAP": "IBIS", "III": "LSE"}
INFOS = {
    "17275": {"conid": 17275, "currency": "EUR", "listingExchange": "SBF"},
    "202070": {"conid": 202070, "currency": "USD", "listingExchange": "NASDAQ"},
    "40000": {"conid": 40000, "currency": "EUR", "listingExchange": "IBIS"},
    "98765": {"conid": 98765, "currency": "GBP", "listingExchange": "LSE"},
}

INDICES = {
    "updated": TODAY,
    "index_currency": {"CAC40": "EUR", "DAX": "EUR", "NASDAQ": "USD",
                       "DOW": "USD", "FTSE": "GBP", "SMI": "CHF",
                       "IBEX35": "EUR", "FTSEMIB": "EUR"},
    "companies": [
        {"ticker": "MC.PA", "index": "CAC40", "score": 55.2, "current_price": 90.0},
        {"ticker": "ADBE", "index": "NASDAQ", "score": 40.0, "current_price": 400.0},
        {"ticker": "SAP.DE", "index": "DAX", "score": 10.0, "current_price": 100.0},
        {"ticker": "III.L", "index": "FTSE", "score": 30.0, "current_price": 2.95},
    ],
}

TRACKING = {"positions": [
    {"id": "MC.PA-2026-09-15", "ticker": "MC.PA", "name": "LVMH", "index": "CAC40",
     "status": "open", "entry_date": TODAY, "entry_price": 88.0,
     "target_exit_price": 120.0},
    {"id": "ADBE-2026-09-15", "ticker": "ADBE", "name": "Adobe", "index": "NASDAQ",
     "status": "open", "entry_date": TODAY, "entry_price": 395.0,
     "target_exit_price": 600.0},
]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Tout le batch confine dans tmp_path : aucun fichier du depot n'est
    lu ni ecrit, et les deux emails sont captures plutot qu'envoyes."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "indices.json").write_text(
        json.dumps(INDICES), encoding="utf-8")
    (tmp_path / "docs" / "signal_tracking.json").write_text(
        json.dumps(TRACKING), encoding="utf-8")
    (tmp_path / "state.json").write_text(
        json.dumps({"kill_switch": False, "dry_run": True}), encoding="utf-8")

    emails = {"resumes": [], "alertes": []}
    monkeypatch.setattr(daily.notify, "send_daily_summary",
                        lambda run, day=None: emails["resumes"].append((run, day)) or True)
    monkeypatch.setattr(daily.notify, "send_gateway_alert",
                        lambda tentatives, day=None: emails["alertes"].append((tentatives, day)) or True)
    monkeypatch.setattr(daily, "pull_repo", lambda *a, **k: {"ok": True, "detail": "à jour"})

    paths = {
        "state": str(tmp_path / "state.json"),
        "positions": str(tmp_path / "positions.json"),
        "journal": str(tmp_path / "real_trading_log.jsonl"),
        "conid_cache": str(tmp_path / "conid_cache.json"),
        "indices": str(tmp_path / "docs" / "indices.json"),
        "tracking": str(tmp_path / "docs" / "signal_tracking.json"),
        "account_snapshot": str(tmp_path / "latest_account.json"),
    }
    return {"tmp": tmp_path, "paths": paths, "emails": emails}


def ecrire_etat(env, **champs):
    with open(env["paths"]["state"], "w", encoding="utf-8") as fh:
        json.dump({"kill_switch": False, "dry_run": True, **champs}, fh)


def lire_journal(env):
    with open(env["paths"]["journal"], encoding="utf-8") as fh:
        return [json.loads(l) for l in fh.read().splitlines() if l.strip()]


# --- pull_repo -------------------------------------------------------

def test_pull_repo_runs_git_pull_in_the_repo_directory():
    appels = []

    class _Resultat:
        returncode = 0
        stdout = "Already up to date."
        stderr = ""

    def fake_run(cmd, **kwargs):
        appels.append((cmd, kwargs))
        return _Resultat()

    resultat = daily.pull_repo("/home/ibkrbot/analyse-or", run_fn=fake_run)

    assert resultat["ok"] is True
    cmd, kwargs = appels[0]
    assert cmd[:2] == ["git", "pull"]
    assert kwargs["cwd"] == "/home/ibkrbot/analyse-or"
    assert kwargs["timeout"] > 0


def test_pull_repo_reports_a_non_zero_exit_without_raising():
    class _Resultat:
        returncode = 1
        stdout = ""
        stderr = "fatal: could not read from remote"

    resultat = daily.pull_repo("/x", run_fn=lambda *a, **k: _Resultat())
    assert resultat["ok"] is False
    assert "remote" in resultat["detail"]


def test_pull_repo_reports_an_exception_without_raising():
    def boom(*args, **kwargs):
        raise OSError("git introuvable")

    resultat = daily.pull_repo("/x", run_fn=boom)
    assert resultat["ok"] is False
    assert "git introuvable" in resultat["detail"]


# --- preflight -------------------------------------------------------

def test_preflight_succeeds_on_the_first_attempt_without_sleeping():
    gw = FakeGateway(authentifie=True)
    dodos = []

    resultat = daily.preflight("https://127.0.0.1:5000", gw=gw,
                               sleep_fn=dodos.append)

    assert resultat["ok"] is True
    assert resultat["tentatives"] == 1
    assert dodos == []


def test_preflight_primes_the_cpapi_session_before_declaring_success():
    """Le CPAPI exige /iserver/accounts ET /portfolio/accounts au moins une
    fois par session : sans eux, /portfolio/... renvoie silencieusement du
    VIDE, ce qui ferait croire a la reconciliation que le bot ne detient
    rien et justifierait un rachat."""
    gw = FakeGateway(authentifie=True)
    daily.preflight("https://127.0.0.1:5000", gw=gw, sleep_fn=lambda s: None)
    assert "brokerage_accounts" in gw.appels
    assert "portfolio_accounts" in gw.appels


def test_preflight_retries_three_times_ten_minutes_apart_then_gives_up():
    gw = FakeGateway(authentifie=False)
    dodos = []

    resultat = daily.preflight("https://127.0.0.1:5000", gw=gw,
                               sleep_fn=dodos.append)

    assert resultat["ok"] is False
    assert resultat["tentatives"] == 3
    assert dodos == [600, 600]  # 2 attentes entre 3 tentatives, pas 3


def test_preflight_counts_a_session_priming_failure_as_a_failed_attempt():
    class _Gw(FakeGateway):
        def portfolio_accounts(self, base_url):
            raise RuntimeError("503 Service Unavailable")

    gw = _Gw(authentifie=True)
    resultat = daily.preflight("https://127.0.0.1:5000", gw=gw, sleep_fn=lambda s: None)

    assert resultat["ok"] is False
    assert resultat["tentatives"] == 3
    assert "503" in resultat["detail"]


def test_preflight_succeeds_on_a_later_attempt():
    class _Gw(FakeGateway):
        def is_authenticated(self, base_url):
            self.appels.append("is_authenticated")
            return self.appels.count("is_authenticated") >= 2

    gw = _Gw(authentifie=False)
    dodos = []
    resultat = daily.preflight("https://127.0.0.1:5000", gw=gw, sleep_fn=dodos.append)

    assert resultat["ok"] is True
    assert resultat["tentatives"] == 2
    assert dodos == [600]


# --- gardes de run_batch ---------------------------------------------

def test_kill_switch_stops_everything_before_any_gateway_call(env):
    """Spec 7 : kill_switch: true -> aucune action, ni entree ni sortie."""
    ecrire_etat(env, kill_switch=True)
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "kill_switch"
    assert gw.appels == []
    assert gw.ordres == []
    assert run["sorties"] == [] and run["entrees"] == []
    assert lire_journal(env)[0]["statut"] == "kill_switch"
    assert len(env["emails"]["resumes"]) == 1


def test_stale_indices_json_stops_the_batch_entirely(env):
    """Spec 4.5 : donnees d'hier -> aucun ordre, ni entree ni sortie.

    gw est ici delibarement authentifie (FakeGateway() par defaut) : si un
    bug inversait l'ordre des gardes (preflight avant fraicheur), ce
    preflight REUSSIRAIT silencieusement et le statut final ressemblerait
    quand meme a "donnees_perimees" en apparence — seule l'assertion sur
    gw.appels prouve que le Gateway n'a jamais ete touche du tout."""
    perime = {**INDICES, "updated": "2026-09-14"}
    with open(env["paths"]["indices"], "w", encoding="utf-8") as fh:
        json.dump(perime, fh)
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "donnees_perimees"
    assert gw.appels == []
    assert gw.ordres == []
    assert lire_journal(env)[0]["statut"] == "donnees_perimees"
    assert len(env["emails"]["resumes"]) == 1
    assert env["emails"]["alertes"] == []


def test_stale_data_is_checked_before_the_preflight_retries(env):
    """Bruler 20 minutes de reauthentification un jour ou le batch ne fera
    rien de toute facon serait absurde — et l'email d'alerte dirait
    'reauthentifie-toi' alors que le vrai probleme est indices.yml."""
    with open(env["paths"]["indices"], "w", encoding="utf-8") as fh:
        json.dump({**INDICES, "updated": "2026-09-14"}, fh)
    gw = FakeGateway(authentifie=False)
    dodos = []

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=dodos.append,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "donnees_perimees"
    assert dodos == []
    assert "is_authenticated" not in gw.appels


def test_a_missing_indices_file_is_treated_as_stale_data(env):
    import os
    os.remove(env["paths"]["indices"])
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "donnees_perimees"
    assert gw.ordres == []


def test_persistent_gateway_failure_abandons_the_batch_and_alerts(env):
    """Spec 5.5 / 7 : 3 tentatives puis abandon + email d'alerte immediat."""
    gw = FakeGateway(authentifie=False)
    dodos = []

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=dodos.append,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "gateway_indisponible"
    assert run["preflight"]["tentatives"] == 3
    assert dodos == [600, 600]
    assert gw.ordres == []
    assert lire_journal(env)[0]["statut"] == "gateway_indisponible"
    assert env["emails"]["alertes"] == [(3, TODAY)]
    assert env["emails"]["resumes"] == []   # pas de doublon vide


def test_a_failed_git_pull_is_recorded_but_not_fatal(env, monkeypatch):
    monkeypatch.setattr(daily, "pull_repo",
                        lambda *a, **k: {"ok": False, "detail": "réseau indisponible"})
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["git_pull"]["ok"] is False
    # Le garde de fraicheur, pas le git pull, decide s'il y a lieu d'agir :
    # les donnees du depot local sont ici encore du jour, et le Gateway
    # s'authentifie -> le batch va jusqu'au bout de ses gardes (assertion
    # positive, strictement plus forte que deux negations).
    assert run["statut"] == "termine"


def test_the_run_records_the_mode_read_from_the_state_file(env):
    ecrire_etat(env, dry_run=True)
    run = daily.run_batch(TODAY, gw=FakeGateway(), sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])
    assert run["mode"] == "dry_run"

    ecrire_etat(env, dry_run=False)
    run = daily.run_batch(TODAY, gw=FakeGateway(), sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])
    assert run["mode"] == "reel"


def test_resolve_paths_merges_over_the_defaults():
    resolus = daily.resolve_paths({"journal": "/tmp/x.jsonl"})
    assert resolus["journal"] == "/tmp/x.jsonl"
    assert resolus["state"] == daily.DEFAULT_PATHS["state"]


# --- _place_order : le seul chemin d'ordre reel ----------------------

def _etat(tmp_path, **champs):
    import json as _json
    chemin = tmp_path / "state.json"
    chemin.write_text(_json.dumps({"kill_switch": False, "dry_run": True, **champs}),
                      encoding="utf-8")
    return str(chemin)


def test_place_order_sends_nothing_in_dry_run(tmp_path):
    gw = FakeGateway()
    resultat = daily._place_order(
        gw, "https://127.0.0.1:5000", "U1", ticker="MC.PA", conid=17275,
        side="BUY", quantity=5, prix_reference_cotation=90.0,
        state_path=_etat(tmp_path, dry_run=True))

    assert gw.ordres == []
    assert gw.confirmations == []
    assert resultat["statut"] == "simule"
    assert resultat["order_id"] is None


def test_place_order_sends_nothing_when_the_kill_switch_is_on(tmp_path):
    gw = FakeGateway()
    resultat = daily._place_order(
        gw, "https://127.0.0.1:5000", "U1", ticker="MC.PA", conid=17275,
        side="BUY", quantity=5, prix_reference_cotation=90.0,
        state_path=_etat(tmp_path, dry_run=False, kill_switch=True))

    assert gw.ordres == []
    assert resultat["statut"] == "simule"


def test_place_order_rereads_the_state_from_disk_at_each_call(tmp_path):
    """DEFENSE EN PROFONDEUR : l'etat lu au demarrage du batch a pu
    changer pendant les 20 minutes de preflight. La valeur qui compte est
    celle du disque AU MOMENT de l'envoi."""
    gw = FakeGateway()
    chemin = _etat(tmp_path, dry_run=False)

    premier = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=chemin)
    assert premier["statut"] == "execute"
    assert len(gw.ordres) == 1

    _etat(tmp_path, dry_run=False, kill_switch=True)
    second = daily._place_order(
        gw, "u", "U1", ticker="ADBE", conid=202070, side="BUY", quantity=1,
        prix_reference_cotation=400.0, state_path=chemin)
    assert second["statut"] == "simule"
    assert len(gw.ordres) == 1   # aucun ordre supplementaire


def test_place_order_sends_the_right_side_conid_and_quantity(tmp_path):
    gw = FakeGateway()
    daily._place_order(gw, "u", "U1", ticker="SAP.DE", conid=40000, side="SELL",
                       quantity=2, prix_reference_cotation=100.0,
                       state_path=_etat(tmp_path, dry_run=False))

    assert gw.ordres == [{"conid": 40000, "side": "SELL", "quantity": 2}]


def test_place_order_answers_a_confirmation_question(tmp_path):
    gw = FakeGateway()
    gw.reponse_ordre = [{"id": "e1f2-0001", "message": ["Confirmez l'ordre au marché"]}]

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert gw.confirmations == ["e1f2-0001"]
    assert resultat["statut"] == "execute"
    assert resultat["order_id"] == "1234"


def test_place_order_gives_up_on_an_endless_confirmation_chain(tmp_path):
    class _Gw(FakeGateway):
        def confirm_reply(self, base_url, reply_id, confirmed=True):
            self.confirmations.append(reply_id)
            return [{"id": f"question-{len(self.confirmations)}", "message": ["encore"]}]

    gw = _Gw()
    gw.reponse_ordre = [{"id": "q0", "message": ["?"]}]

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert len(gw.confirmations) == daily.MAX_CONFIRMATIONS
    assert resultat["statut"] == "erreur"
    assert "confirmation" in resultat["detail"].lower()


def test_place_order_reads_the_average_fill_price(tmp_path):
    gw = FakeGateway()
    gw.statuts_ordre = {"1234": {"order_status": "Filled", "avgPrice": "91.25",
                                 "commission": "1.10"}}

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["prix_execution_cotation"] == pytest.approx(91.25)
    assert resultat["prix_execution_estime"] is False
    assert resultat["commission"] == pytest.approx(1.10)


def test_place_order_falls_back_to_the_reference_price_when_avgprice_is_missing(tmp_path):
    """Ce prix devient la reference du stop-loss : il ne doit jamais rester
    None en silence. Le repli est signale par prix_execution_estime."""
    gw = FakeGateway()
    gw.statuts_ordre = {"1234": {"order_status": "Submitted"}}

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["prix_execution_cotation"] == pytest.approx(90.0)
    assert resultat["prix_execution_estime"] is True
    assert resultat["statut"] == "execute"


def test_place_order_falls_back_when_order_status_raises(tmp_path):
    class _Gw(FakeGateway):
        def order_status(self, base_url, order_id):
            raise RuntimeError("504 Gateway Timeout")

    gw = _Gw()
    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    # L'ordre est parti : on ne doit SURTOUT pas le declarer en echec.
    assert resultat["statut"] == "execute"
    assert resultat["prix_execution_estime"] is True


def test_place_order_reports_a_rejected_order_without_raising(tmp_path):
    gw = FakeGateway()
    gw.echecs = {17275}

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["statut"] == "erreur"
    assert "rejet IBKR" in resultat["detail"]
    assert resultat["order_id"] is None


def test_place_order_reports_a_response_without_any_order_id(tmp_path):
    gw = FakeGateway()
    gw.reponse_ordre = [{"error": "no trading permission"}]

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["statut"] == "erreur"
    assert "no trading permission" in resultat["detail"]
