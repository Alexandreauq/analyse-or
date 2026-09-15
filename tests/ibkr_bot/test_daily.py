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


def test_a_weekend_batch_is_abandoned_before_the_gateway_is_touched(env):
    """Critical #1 (revue finale de branche) : le timer systemd tourne
    7j/7 et le garde de fraicheur ne detecte PAS un week-end (le workflow
    indices.json tourne lui aussi 7j/7 et tamponne la date du jour sans
    condition). 2026-09-19 est un samedi reel."""
    SAMEDI = "2026-09-19"
    gw = FakeGateway()

    run = daily.run_batch(SAMEDI, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "hors_jour_de_bourse"
    assert gw.appels == []
    assert gw.ordres == []
    assert lire_journal(env)[0]["statut"] == "hors_jour_de_bourse"
    assert len(env["emails"]["resumes"]) == 1
    assert env["emails"]["alertes"] == []


def test_a_sunday_batch_is_also_abandoned(env):
    """2026-09-20 est un dimanche reel."""
    DIMANCHE = "2026-09-20"
    gw = FakeGateway()

    run = daily.run_batch(DIMANCHE, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "hors_jour_de_bourse"
    assert gw.appels == []


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


def test_place_order_keeps_the_confirmation_audit_trail_on_an_exhausted_chain(tmp_path):
    """Re-revue du round de correctifs de la tache 4 : les messages des
    confirmations deja auto-validees (confirmed=True) avant l'abandon ne
    doivent pas disparaitre du detail — ce sont les seuls avertissements
    IBKR reellement acceptes sur cet ordre."""
    class _Gw(FakeGateway):
        def confirm_reply(self, base_url, reply_id, confirmed=True):
            self.confirmations.append(reply_id)
            return [{"id": f"question-{len(self.confirmations)}",
                     "message": [f"avertissement {len(self.confirmations)}"]}]

    gw = _Gw()
    gw.reponse_ordre = [{"id": "q0", "message": ["avertissement 0"]}]

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["statut"] == "erreur"
    assert "avertissement 0" in resultat["detail"]
    assert "avertissement 1" in resultat["detail"]


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


def test_place_order_strips_a_genuine_thousands_separator(tmp_path):
    gw = FakeGateway()
    gw.statuts_ordre = {"1234": {"order_status": "Filled", "avgPrice": "1,234.50"}}

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["prix_execution_cotation"] == pytest.approx(1234.50)
    assert resultat["prix_execution_estime"] is False


def test_place_order_never_mistakes_a_decimal_comma_for_a_thousands_separator(tmp_path):
    """Regression du round de correctifs de la tache 4 : un retrait
    inconditionnel de la virgule confondrait "1234,50" (virgule
    decimale, format europeen plausible sur un futur fournisseur) avec
    un separateur de milliers et produirait un prix 100x trop grand,
    pris a tort pour un prix d'execution REEL (estime=False). La chaine
    ne correspond pas au format milliers anglo-saxon (pas de groupes de
    3 chiffres) : elle doit echouer au parsing et retomber, signalee,
    sur le prix de reference."""
    gw = FakeGateway()
    gw.statuts_ordre = {"1234": {"order_status": "Filled", "avgPrice": "1234,50"}}

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["prix_execution_cotation"] == pytest.approx(90.0)
    assert resultat["prix_execution_estime"] is True
    assert resultat["statut"] == "execute"


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


# --- corrections de revue (findings Important #1/2/3, Minor #1/2/3) --

class _ExplodingGateway:
    """Gateway qui leve sur TOUT appel — preuve, plus stricte qu'un
    FakeGateway ordinaire, que _place_order ne touche JAMAIS le reseau
    quand l'etat est manquant/corrompu (repli fail-safe de
    state.load_state vers dry_run=True)."""

    def __getattr__(self, name):
        def _boom(*args, **kwargs):
            raise AssertionError(f"gw.{name} n'aurait jamais du etre appele")
        return _boom


def test_place_order_stays_dry_run_when_the_state_file_is_missing(tmp_path):
    gw = _ExplodingGateway()
    chemin_absent = str(tmp_path / "nexiste-pas.json")

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=chemin_absent)

    assert resultat["statut"] == "simule"
    assert resultat["order_id"] is None


def test_place_order_stays_dry_run_when_the_state_file_is_corrupt(tmp_path):
    gw = _ExplodingGateway()
    chemin = tmp_path / "state.json"
    chemin.write_text("{ceci n'est pas du json valide", encoding="utf-8")

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=str(chemin))

    assert resultat["statut"] == "simule"
    assert resultat["order_id"] is None


def test_place_order_succeeds_on_a_confirmation_chain_resolved_on_the_boundary_attempt(tmp_path):
    """La borne MAX_CONFIRMATIONS est un maximum, pas un echec garanti :
    une chaine qui se resout PILE au dernier essai autorise doit encore
    reussir normalement."""
    class _Gw(FakeGateway):
        def confirm_reply(self, base_url, reply_id, confirmed=True):
            self.confirmations.append(reply_id)
            if len(self.confirmations) < daily.MAX_CONFIRMATIONS:
                return [{"id": f"question-{len(self.confirmations)}", "message": ["encore"]}]
            return [{"order_id": "9999", "order_status": "Submitted"}]

    gw = _Gw()
    gw.reponse_ordre = [{"id": "q0", "message": ["premiere question"]}]

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert len(gw.confirmations) == daily.MAX_CONFIRMATIONS
    assert resultat["statut"] == "execute"
    assert resultat["order_id"] == "9999"


def test_place_order_reports_an_empty_gateway_response_without_raising(tmp_path):
    gw = FakeGateway()
    gw.reponse_ordre = []

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["statut"] == "erreur"
    assert resultat["order_id"] is None


def test_place_order_reports_no_commission_when_the_field_is_absent(tmp_path):
    gw = FakeGateway()
    gw.statuts_ordre = {"1234": {"order_status": "Filled", "avgPrice": "91.25"}}

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["statut"] == "execute"
    assert resultat["commission"] is None


def test_place_order_parses_avgprice_with_a_thousands_separator(tmp_path):
    gw = FakeGateway()
    gw.statuts_ordre = {"1234": {"order_status": "Filled", "avgPrice": "1,234.50"}}

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["prix_execution_cotation"] == pytest.approx(1234.50)
    assert resultat["prix_execution_estime"] is False


def test_place_order_flags_an_unreliable_execution_price_in_detail(tmp_path):
    """Finding Important #2 : si avgPrice ET le prix de reference du
    sizing sont tous les deux inutilisables, l'ordre reste 'execute' (il
    est bien parti) mais `detail` doit porter la trace du probleme —
    sinon c'est exactement l'Ecart #4 de la Tache 7 (position dont le
    prix de reference manque, donc jamais fermable)."""
    gw = FakeGateway()
    gw.statuts_ordre = {"1234": {"order_status": "Submitted"}}   # avgPrice absent

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=None, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["statut"] == "execute"
    assert resultat["prix_execution_estime"] is True
    assert resultat["prix_execution_cotation"] is None
    assert resultat["detail"] is not None
    assert "prix" in resultat["detail"].lower()


def test_place_order_records_confirmation_messages_in_detail_on_success(tmp_path):
    """Finding Important #3 : un confirmed=True automatique sur un
    avertissement IBKR doit laisser une trace auditable dans le journal,
    pas disparaitre silencieusement."""
    gw = FakeGateway()
    gw.reponse_ordre = [{"id": "e1f2-0001", "message": ["Ordre hors seance : confirmer ?"]}]

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert resultat["statut"] == "execute"
    assert resultat["detail"] is not None
    assert "1" in resultat["detail"]
    assert "hors seance" in resultat["detail"].lower()


def test_place_order_treats_a_mixed_response_as_already_resolved(tmp_path):
    """Finding Minor #1 : une reponse melangeant une question et une
    confirmation d'ordre doit etre traitee comme resolue (order_id
    verifie en premier), pas relancee en confirmation superflue."""
    gw = FakeGateway()
    gw.reponse_ordre = [{"id": "ignored-question", "message": ["ignorer"]},
                        {"order_id": "5555", "order_status": "Submitted"}]

    resultat = daily._place_order(
        gw, "u", "U1", ticker="MC.PA", conid=17275, side="BUY", quantity=5,
        prix_reference_cotation=90.0, state_path=_etat(tmp_path, dry_run=False))

    assert gw.confirmations == []
    assert resultat["statut"] == "execute"
    assert resultat["order_id"] == "5555"


# --- run_batch complet : sorties et entrees ---------------------------

def _positions_locales(env, positions):
    import json as _json
    with open(env["paths"]["positions"], "w", encoding="utf-8") as fh:
        _json.dump({"positions": positions}, fh)


POSITION_MC = {
    "id": "MC.PA-2026-03-02", "ticker": "MC.PA", "name": "LVMH", "index": "CAC40",
    "conid": 17275, "devise": "EUR", "quantite": 5,
    "prix_execution_reference": 200.0, "paper_entry_price": 198.0,
    "date_entree": "2026-03-02", "target_exit_price": 300.0,
    "date_limite": "2026-09-02",
}


def test_dry_run_places_no_order_but_journals_everything(env):
    """LE TEST LE PLUS IMPORTANT DE TOUTE LA SUITE (spec 7) : en dry_run,
    aucun appel de passage d'ordre, et pourtant un journal complet."""
    ecrire_etat(env, dry_run=True)
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert gw.ordres == []
    assert gw.confirmations == []
    assert run["mode"] == "dry_run"
    assert run["statut"] == "termine"
    assert [e["ticker"] for e in run["entrees"]] == ["MC.PA", "ADBE"]
    assert all(e["statut"] == "simule" for e in run["entrees"])
    assert run["entrees"][0]["quantite"] == 5          # floor(500 / 90)
    assert run["entrees"][0]["rang"] == 1              # score 55.2 > 40.0
    assert run["entrees"][0]["conid"] == 17275
    assert run["entrees"][0]["prix_reference_sizing"] == 90.0
    assert run["entrees"][0]["prix_paper"] == 88.0
    journalise = lire_journal(env)[0]
    assert [e["ticker"] for e in journalise["entrees"]] == ["MC.PA", "ADBE"]


def test_real_mode_buys_the_selected_signals(env):
    ecrire_etat(env, dry_run=False)
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert [(o["conid"], o["side"], o["quantity"]) for o in gw.ordres] == [
        (17275, "BUY", 5), (202070, "BUY", 1)]
    assert all(e["statut"] == "execute" for e in run["entrees"])
    import ibkr_bot.portfolio as portfolio
    enregistrees = portfolio.load_positions(env["paths"]["positions"])
    assert {p["ticker"] for p in enregistrees} == {"MC.PA", "ADBE"}
    assert enregistrees[0]["date_limite"] == "2027-03-15"


def test_a_failed_order_does_not_stop_the_following_ones(env):
    """Spec 5.5 / 7 : un ordre en echec n'interrompt pas le batch."""
    ecrire_etat(env, dry_run=False)
    gw = FakeGateway()
    gw.echecs = {17275}   # MC.PA, le mieux classe, echoue

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    par_ticker = {e["ticker"]: e for e in run["entrees"]}
    assert par_ticker["MC.PA"]["statut"] == "erreur"
    assert par_ticker["ADBE"]["statut"] == "execute"
    import ibkr_bot.portfolio as portfolio
    enregistrees = portfolio.load_positions(env["paths"]["positions"])
    assert [p["ticker"] for p in enregistrees] == ["ADBE"]


def test_a_failed_order_is_never_retried(env):
    """Spec 5.5 : pas de reprise automatique d'un ordre echoue."""
    ecrire_etat(env, dry_run=False)
    gw = FakeGateway()
    gw.echecs = {17275}

    daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                    account_id="U1", paths=env["paths"])

    assert [o["conid"] for o in gw.ordres].count(17275) == 1


def test_a_position_already_in_the_journal_is_not_bought_again(env):
    """Spec 7 : relance du batch le meme jour -> pas de double achat."""
    ecrire_etat(env, dry_run=False)
    deja = {**POSITION_MC, "id": "MC.PA-2026-09-15", "date_entree": TODAY,
            "date_limite": "2027-03-15", "prix_execution_reference": 90.5}
    _positions_locales(env, [deja])
    gw = FakeGateway(positions_ibkr=[{"conid": 17275, "position": 5.0}])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert [o["conid"] for o in gw.ordres] == [202070]   # ADBE seulement
    rejets = {r["ticker"]: r["raison"] for r in run["signaux_rejetes"]}
    assert rejets["MC.PA"] == "deja_en_portefeuille"


def test_a_position_held_at_ibkr_but_absent_from_the_journal_is_not_bought(env):
    """Ceinture de la bretelle : si le batch a plante entre l'envoi de
    l'ordre et l'ecriture de positions.json, la ligne existe chez IBKR mais
    pas dans le journal. reconcile la classerait `ignorees` (position de
    l'utilisateur) et rien n'empecherait un rachat."""
    ecrire_etat(env, dry_run=False)
    _positions_locales(env, [])
    gw = FakeGateway(positions_ibkr=[{"conid": 17275, "position": 5.0}])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert [o["conid"] for o in gw.ordres] == [202070]
    rejets = {r["ticker"]: r["raison"] for r in run["signaux_rejetes"]}
    assert rejets["MC.PA"] == "deja_detenu_hors_journal"


def test_a_stop_loss_position_is_sold(env):
    """docs/indices.json donne MC.PA a 90 EUR ; la position a ete ouverte a
    200 EUR -> -55 %, bien au-dela du stop-loss a -20 %."""
    ecrire_etat(env, dry_run=False)
    _positions_locales(env, [POSITION_MC])
    gw = FakeGateway(positions_ibkr=[{"conid": 17275, "position": 5.0}])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    ventes = [o for o in gw.ordres if o["side"] == "SELL"]
    assert ventes == [{"conid": 17275, "side": "SELL", "quantity": 5}]
    assert run["sorties"][0]["close_reason"] == "stop_loss"
    assert run["sorties"][0]["statut"] == "execute"
    import ibkr_bot.portfolio as portfolio
    assert all(p["ticker"] != "MC.PA"
               for p in portfolio.load_positions(env["paths"]["positions"]))


def test_exits_run_before_entries_and_free_a_slot(env):
    """Enchainement identique au paper-trading (clotures puis ouvertures) :
    le plafond de 10 est atteint, mais une position sort aujourd'hui, donc
    exactement un signal doit pouvoir entrer."""
    ecrire_etat(env, dry_run=False)
    occupees = [POSITION_MC] + [
        {**POSITION_MC, "id": f"X{i}-2026-03-02", "ticker": f"X{i}.PA",
         "conid": 900 + i, "prix_execution_reference": 10.0,
         "target_exit_price": 999.0, "date_limite": "2027-01-01"}
        for i in range(9)
    ]
    _positions_locales(env, occupees)
    gw = FakeGateway(positions_ibkr=[{"conid": p["conid"], "position": 5.0}
                                     for p in occupees])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert [o["side"] for o in gw.ordres].count("SELL") == 1
    achats = [o for o in gw.ordres if o["side"] == "BUY"]
    assert [o["conid"] for o in achats] == [202070]   # ADBE, le seul restant


def test_a_ticker_sold_today_is_never_bought_back_in_the_same_batch(env):
    """MC.PA sort en stop_loss aujourd'hui ET porte un nouveau signal
    d'entree du jour. Sans garde, la sequence sorties-puis-entrees le
    rachete dans la minute : deux commissions pour revenir au point de
    depart, sur un titre qu'on vient justement de stop-losser."""
    ecrire_etat(env, dry_run=False)
    _positions_locales(env, [POSITION_MC])
    gw = FakeGateway(positions_ibkr=[{"conid": 17275, "position": 5.0}])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert [(o["conid"], o["side"]) for o in gw.ordres] == [
        (17275, "SELL"), (202070, "BUY")]
    rejets = {r["ticker"]: r["raison"] for r in run["signaux_rejetes"]}
    assert rejets["MC.PA"] == "vendu_aujourd_hui"


def test_a_london_fill_price_is_stored_in_pounds(env):
    """GARDE-FOU PENCE/LIVRE de bout en bout (spec 4.7) : IBKR renvoie
    296 PENCE, docs/indices.json parle en LIVRES. Si 296 atterrissait dans
    positions.json, le stop-loss serait declenche des le lendemain et la
    quantite achetee serait fausse d'un facteur 100."""
    import json as _json
    ecrire_etat(env, dry_run=False)
    tracking = {"positions": [
        {"id": "III.L-2026-09-15", "ticker": "III.L", "name": "3i", "index": "FTSE",
         "status": "open", "entry_date": TODAY, "entry_price": 2.93,
         "target_exit_price": 4.0}]}
    with open(env["paths"]["tracking"], "w", encoding="utf-8") as fh:
        _json.dump(tracking, fh)
    gw = FakeGateway()
    gw.statuts_ordre = {"1234": {"order_status": "Filled", "avgPrice": "296.0"}}

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    # Budget 500 EUR * 0.86 = 430 GBP = 43000 GBp ; 43000 / 295 -> 145.
    assert gw.ordres == [{"conid": 98765, "side": "BUY", "quantity": 145}]
    entree = run["entrees"][0]
    assert entree["prix_execution_cotation"] == pytest.approx(296.0)
    assert entree["prix_execution"] == pytest.approx(2.96)
    assert entree["devise_cotation"] == "GBp"
    assert entree["devise_compte"] == "GBP"
    import ibkr_bot.portfolio as portfolio
    position = portfolio.load_positions(env["paths"]["positions"])[0]
    assert position["prix_execution_reference"] == pytest.approx(2.96)


def test_unreadable_ibkr_positions_abort_the_batch(env):
    """Spec 5.4 : jamais de decision sans savoir ce que le compte detient."""
    ecrire_etat(env, dry_run=False)

    class _Gw(FakeGateway):
        def positions(self, base_url, account_id):
            raise RuntimeError("500 Internal Server Error")

    gw = _Gw()
    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["statut"] == "reconciliation_impossible"
    assert gw.ordres == []
    assert run["erreurs"][0]["etape"] == "positions_ibkr"


def test_dry_run_keeps_simulated_positions_across_days(env):
    """En dry_run, les positions simulees n'existent pas chez IBKR :
    laisser reconcile les fermer viderait positions.json chaque jour et
    rendrait la validation en simulation (spec 5.3) sans objet."""
    ecrire_etat(env, dry_run=True)
    simulee = {**POSITION_MC, "ticker": "ZZZ.PA", "conid": 555,
               "target_exit_price": 999.0, "date_limite": "2027-01-01",
               "prix_execution_reference": 10.0}
    _positions_locales(env, [simulee])
    gw = FakeGateway(positions_ibkr=[])

    daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                    account_id="U1", paths=env["paths"])

    import ibkr_bot.portfolio as portfolio
    restantes = portfolio.load_positions(env["paths"]["positions"])
    assert "ZZZ.PA" in {p["ticker"] for p in restantes}


def test_real_mode_drops_a_position_sold_outside_the_bot(env):
    """Spec 5.4 : vendue a la main par l'utilisateur -> retiree du decompte
    des 10, et « le bot ne la rouvre JAMAIS » — pas meme sur un nouveau
    signal du jour portant le meme ticker."""
    ecrire_etat(env, dry_run=False)
    _positions_locales(env, [POSITION_MC])
    gw = FakeGateway(positions_ibkr=[])   # plus rien chez IBKR

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["reconciliation"]["cloturees_hors_bot"] == ["MC.PA-2026-03-02"]
    assert [o["side"] for o in gw.ordres].count("SELL") == 0
    assert 17275 not in [o["conid"] for o in gw.ordres]
    rejets = {r["ticker"]: r["raison"] for r in run["signaux_rejetes"]}
    assert rejets["MC.PA"] == "cloturee_hors_bot"
    import ibkr_bot.portfolio as portfolio
    assert all(p["ticker"] != "MC.PA"
               for p in portfolio.load_positions(env["paths"]["positions"]))


def test_a_position_without_a_reference_price_raises_an_anomaly(env):
    """portfolio.exit_reason laisse volontairement une telle position
    INCLOSABLE (ecart #4 documente dans portfolio.py) et dit explicitement
    que c'est au Plan B de la signaler a un operateur."""
    ecrire_etat(env, dry_run=False)
    cassee = {**POSITION_MC, "prix_execution_reference": None}
    _positions_locales(env, [cassee])
    gw = FakeGateway(positions_ibkr=[{"conid": 17275, "position": 5.0}])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    types = {a["type"] for a in run["anomalies"]}
    assert "prix_reference_absent" in types
    assert [o["side"] for o in gw.ordres].count("SELL") == 0


def test_an_unavailable_exchange_rate_rejects_the_signal_without_buying(env):
    ecrire_etat(env, dry_run=False)

    class _Gw(FakeGateway):
        def exchange_rate(self, base_url, source, target):
            raise RuntimeError("503 Service Unavailable")

    gw = _Gw()
    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert gw.ordres == []
    raisons = {r["raison"] for r in run["signaux_rejetes"]}
    assert "prix_ou_taux_invalide" in raisons
    assert any(e["etape"] == "taux_de_change" for e in run["erreurs"])


def test_the_account_snapshot_is_written(env):
    ecrire_etat(env, dry_run=False)
    daily.run_batch(TODAY, gw=FakeGateway(), sleep_fn=lambda s: None,
                    account_id="U1", paths=env["paths"])

    with open(env["paths"]["account_snapshot"], encoding="utf-8") as fh:
        instantane = json.load(fh)
    assert instantane["base_cash"] == 10000.0
    assert instantane["fetched_at"].endswith("Z")


def test_a_positions_save_failure_is_reported_in_the_run(env, monkeypatch):
    """positions.json est un ETAT, pas un log : son echec d'ecriture doit
    remonter dans l'email, contrairement a append_run."""
    ecrire_etat(env, dry_run=False)
    monkeypatch.setattr(daily.journal, "save_positions_safely",
                        lambda positions, path: False)
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert any(e["etape"] == "positions.json" for e in run["erreurs"])


def test_the_conid_cache_is_persisted_between_runs(env):
    ecrire_etat(env, dry_run=True)
    daily.run_batch(TODAY, gw=FakeGateway(), sleep_fn=lambda s: None,
                    account_id="U1", paths=env["paths"])

    with open(env["paths"]["conid_cache"], encoding="utf-8") as fh:
        cache = json.load(fh)
    assert cache["MC.PA"]["conid"] == 17275


def test_main_runs_a_batch(monkeypatch):
    appels = []
    monkeypatch.setattr(daily, "run_batch", lambda *a, **k: appels.append(True))
    daily.main()
    assert appels == [True]


# --- revue stricte tache 5 : 3 findings Important ---------------------

def test_positions_json_is_rewritten_after_each_order_not_just_at_the_end(env, monkeypatch):
    """Decision 4 : la CADENCE d'ecriture, pas seulement le contenu final.
    Sans ce test, rien ne distingue "sauvegarde apres chaque ordre" de
    "sauvegarde une fois en fin de batch" — les deux produisent le meme
    fichier final si rien ne plante entre-temps. On espionne l'appel pour
    verifier a la fois le NOMBRE d'ecritures et qu'un instantane
    INTERMEDIAIRE (apres la vente, avant l'achat suivant) reflete deja
    l'etat partiel."""
    ecrire_etat(env, dry_run=False)
    _positions_locales(env, [POSITION_MC])
    gw = FakeGateway(positions_ibkr=[{"conid": 17275, "position": 5.0}])

    appels = []
    original = daily.journal.save_positions_safely

    def _espion(positions, path):
        appels.append([dict(p) for p in positions])
        return original(positions, path)

    monkeypatch.setattr(daily.journal, "save_positions_safely", _espion)

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    # MC.PA stop-losse (1 sortie executee) puis ADBE achete (1 entree
    # executee ; MC.PA porte bien un nouveau signal aujourd'hui mais est
    # bloque par le garde `vendu_aujourd_hui`, donc n'ajoute pas d'ecriture).
    nb_sorties_executees = sum(1 for s in run["sorties"]
                               if s["statut"] in ("execute", "simule"))
    nb_entrees_executees = sum(1 for e in run["entrees"]
                               if e["statut"] in ("execute", "simule"))
    assert nb_sorties_executees == 1 and nb_entrees_executees == 1
    # 1 sauvegarde juste apres reconciliation + 1 par ordre execute : la
    # cadence, pas seulement le resultat final.
    assert len(appels) == 1 + nb_sorties_executees + nb_entrees_executees

    # Instantane intermediaire (juste apres la vente de MC.PA, avant
    # l'achat d'ADBE) : deja sans MC.PA, pas encore avec ADBE.
    tickers_apres_vente = {p["ticker"] for p in appels[1]}
    assert "MC.PA" not in tickers_apres_vente
    assert "ADBE" not in tickers_apres_vente


def test_a_dry_run_to_real_switch_during_preflight_is_honored_for_reconciliation(env, monkeypatch):
    """DEFENSE EN PROFONDEUR (spec 4.3 point 2) pour la POLITIQUE DE
    RECONCILIATION elle-meme, symetrique a celle de _place_order : si
    dry_run passe a false PENDANT le preflight (bascule operateur
    plausible sur les ~20 minutes de la fenetre), reconcile() doit faire
    foi -- pas la lecture d'avant preflight. Sans cette relecture, une
    position purement simulee (jamais detenue chez IBKR) resterait
    "active" localement, son stop-loss serait evalue, et _place_order
    (qui, lui, relit deja l'etat a chaque ordre) enverrait un VRAI ordre
    de vente a nu pour un conid dont le compte ne detient rien."""
    ecrire_etat(env, dry_run=True)
    _positions_locales(env, [POSITION_MC])
    gw = FakeGateway(positions_ibkr=[])   # IBKR ne detient rien de MC.PA

    preflight_reel = daily.preflight

    def _preflight_qui_bascule_en_reel(*args, **kwargs):
        ecrire_etat(env, dry_run=False)   # l'operateur bascule en cours de preflight
        return preflight_reel(*args, **kwargs)

    monkeypatch.setattr(daily, "preflight", _preflight_qui_bascule_en_reel)

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["mode"] == "reel"
    ventes = [o for o in gw.ordres if o["side"] == "SELL"]
    assert ventes == []   # pas de vente a nu sur une position jamais detenue
    import ibkr_bot.portfolio as portfolio
    assert all(p["ticker"] != "MC.PA"
              for p in portfolio.load_positions(env["paths"]["positions"]))


def test_a_malformed_position_does_not_crash_the_whole_batch(env):
    """Une ligne corrompue dans positions.json (ex. `date_limite` absent —
    precisement le champ que l'anomalie `prix_reference_absent` invite un
    operateur a corriger a la main) ne doit JAMAIS faire perdre tout le
    batch : aucune ligne de journal, aucun email, silence total serait
    pire que l'anomalie elle-meme (spec 5.5)."""
    ecrire_etat(env, dry_run=False)
    cassee = {**POSITION_MC, "id": "CASSE-2026-03-02", "ticker": "CASSE.PA",
             "conid": 700}
    del cassee["date_limite"]
    _positions_locales(env, [POSITION_MC, cassee])

    indices_augmentes = {**INDICES, "companies": INDICES["companies"] + [
        {"ticker": "CASSE.PA", "index": "CAC40", "score": 20.0, "current_price": 250.0}]}
    with open(env["paths"]["indices"], "w", encoding="utf-8") as fh:
        json.dump(indices_augmentes, fh)

    gw = FakeGateway(positions_ibkr=[
        {"conid": 17275, "position": 5.0}, {"conid": 700, "position": 5.0}])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    # La position bien formee (MC.PA, stop-loss) est traitee normalement.
    assert [s["ticker"] for s in run["sorties"]] == ["MC.PA"]
    assert run["sorties"][0]["close_reason"] == "stop_loss"
    assert run["sorties"][0]["statut"] == "execute"
    # La position cassee est signalee, pas silencieusement perdue.
    assert any(e["etape"] == "positions_to_close" and "CASSE.PA" in e["detail"]
              for e in run["erreurs"])
    # Le batch va jusqu'au bout : ligne de journal + email, pas un plantage.
    assert run["statut"] == "termine"
    assert lire_journal(env)[0]["statut"] == "termine"
    assert len(env["emails"]["resumes"]) == 1


def test_a_position_missing_conid_at_execution_does_not_crash_the_batch(env):
    """Un cran plus loin que le test precedent : `conid` et `quantite` ne
    sont lus qu'a l'EXECUTION de la sortie (_executer_sortie), jamais par
    sa DECISION (portfolio.positions_to_close) -- une ligne corrompue sur
    CES champs precis passe donc le premier garde intacte. Teste en
    dry_run, le chemin le PLUS expose : la reconciliation reelle
    filtrerait une ligne pareille avant qu'elle n'atteigne ce stade, et
    dry_run est le mode que les operateurs feront tourner pendant les
    semaines de validation (spec 5.3)."""
    ecrire_etat(env, dry_run=True)
    cassee = {**POSITION_MC, "id": "CASSE-2026-03-02", "ticker": "CASSE.PA",
             "prix_execution_reference": 50.0, "target_exit_price": 999.0}
    del cassee["conid"]
    _positions_locales(env, [POSITION_MC, cassee])

    indices_augmentes = {**INDICES, "companies": INDICES["companies"] + [
        {"ticker": "CASSE.PA", "index": "CAC40", "score": 20.0, "current_price": 30.0}]}
    with open(env["paths"]["indices"], "w", encoding="utf-8") as fh:
        json.dump(indices_augmentes, fh)

    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    # La position bien formee (MC.PA, stop-loss) est traitee normalement.
    assert [s["ticker"] for s in run["sorties"]] == ["MC.PA"]
    assert run["sorties"][0]["close_reason"] == "stop_loss"
    assert run["sorties"][0]["statut"] == "simule"
    # La position cassee (decision de sortie declenchee : 30 <= 50*0.8) est
    # signalee a l'EXECUTION, pas silencieusement perdue.
    assert any(e["etape"] == "executer_sortie" and "CASSE.PA" in e["detail"]
              for e in run["erreurs"])
    # Le batch va jusqu'au bout : ligne de journal + email, pas un plantage.
    assert run["statut"] == "termine"
    assert lire_journal(env)[0]["statut"] == "termine"
    assert len(env["emails"]["resumes"]) == 1


# --- revue finale de branche : Critical #2 --------------------------

def test_kill_switch_pulled_mid_batch_blocks_the_order_and_does_not_orphan_the_position(env, monkeypatch):
    """Critical #2 (revue finale de branche) : symetrique du fix deja fait
    pour la bascule dry_run -> reel PENDANT le preflight
    (test_a_dry_run_to_real_switch_during_preflight_is_honored_for_reconciliation
    ci-dessus). Ici c'est l'AUTRE sens : un batch deja engage en mode REEL
    voit l'operateur tirer l'interrupteur d'urgence EN COURS D'EXECUTION,
    entre le debut du batch et l'envoi effectif d'un ordre de vente.
    _place_order bloque deja l'envoi (defense en profondeur, relecture
    disque) ; SANS le fix, run_batch traiterait le statut "simule" renvoye
    comme une simulation authentique et retirerait la position de
    positions.json comme si elle avait ete vendue, alors qu'elle est
    toujours bien reelle chez IBKR — orpheline pour toujours
    (portfolio.reconcile n'adopte jamais une position inconnue du
    journal, spec 9.5)."""
    ecrire_etat(env, dry_run=False)
    _positions_locales(env, [POSITION_MC])   # stop-loss : 90.0 <= 200*0.8
    gw = FakeGateway(positions_ibkr=[{"conid": 17275, "position": 5.0}])

    appels = {"n": 0}

    def fake_load_state(path):
        appels["n"] += 1
        if appels["n"] <= 2:
            # 1er appel : lecture au tout debut de run_batch (kill_switch).
            # 2e appel : relecture post-preflight (politique de reconciliation).
            return {"kill_switch": False, "dry_run": False}
        # 3e appel et suivants : DANS _place_order, au moment precis de
        # l'envoi -> l'operateur vient de tirer l'interrupteur d'urgence.
        return {"kill_switch": True, "dry_run": False}

    monkeypatch.setattr(daily.state, "load_state", fake_load_state)

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    # (a) aucun ordre reel envoye : place_market_order n'est jamais appele.
    assert gw.ordres == []
    assert appels["n"] >= 3   # preuve que la relecture DANS _place_order a eu lieu

    # (b) la position n'est PAS retiree de positions.json, et pas corrompue.
    import ibkr_bot.portfolio as portfolio
    positions_finales = portfolio.load_positions(env["paths"]["positions"])
    assert [p["ticker"] for p in positions_finales] == ["MC.PA"]
    assert positions_finales[0]["quantite"] == 5
    assert positions_finales[0]["prix_execution_reference"] == 200.0

    # (c) l'evenement est visible dans run["erreurs"], pas seulement noye
    # au milieu des cartes de vente ordinaires du journal/email.
    assert run["sorties"][0]["statut"] == "annule_interruption"
    assert any(e["etape"] == "execution_sortie_interrompue" and "MC.PA" in e["detail"]
              for e in run["erreurs"])


def test_a_genuine_dry_run_batch_is_completely_unaffected_by_the_mode_attendu_plumbing(env):
    """Garde-fou de non-regression pour Critical #2 : mode_attendu ne doit
    RIEN changer au comportement dry_run d'origine (statut "simule",
    positions bien retirees/ajoutees normalement)."""
    ecrire_etat(env, dry_run=True)
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert gw.ordres == []
    assert all(e["statut"] == "simule" for e in run["entrees"])
    assert not any(e["etape"] in ("execution_sortie_interrompue", "execution_entree_interrompue")
                  for e in run["erreurs"])


# --- revue finale de branche : Important #1 ---------------------------

def test_dry_run_reconciliation_reported_reflects_local_positions_not_ibkr(env):
    """Important #1 : en dry_run, ce que l'email affiche doit refleter ce
    que le bot gere REELLEMENT (positions locales), pas le verdict brut de
    reconcile() qui, lui, ne voit evidemment aucune position simulee chez
    IBKR et dirait "0 active, tout cloture hors bot" chaque jour pendant
    toute la periode de validation (spec 7)."""
    ecrire_etat(env, dry_run=True)
    simulee = {**POSITION_MC, "ticker": "ZZZ.PA", "conid": 555,
               "target_exit_price": 999.0, "date_limite": "2027-01-01",
               "prix_execution_reference": 10.0}
    _positions_locales(env, [simulee])
    gw = FakeGateway(positions_ibkr=[])   # IBKR ne connait aucune position simulee

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert run["reconciliation"]["actives"] == 1
    assert run["reconciliation"]["cloturees_hors_bot"] == []


# --- revue finale de branche : Important #3 ---------------------------

def test_a_currency_mismatch_between_contract_and_sizing_rejects_the_signal(env, monkeypatch):
    """Important #3 : contracts.py valide la devise du contrat resolu
    contre EXPECTED_VENUE, sizing.py prend independamment `devise_compte`
    dans index_currency de docs/indices.json. Les deux ne sont jamais
    confrontees l'une a l'autre aujourd'hui. On force ici un desaccord
    (MC.PA resolu en USD alors que sizing.py dit EUR pour le CAC40) pour
    prouver que le signal est rejete plutot que finance/execute dans la
    mauvaise devise."""
    ecrire_etat(env, dry_run=False)
    resolve_original = daily.contracts.resolve_conid

    def resolve_avec_devise_incoherente(ticker, search_fn, info_fn, cache, today):
        contrat = resolve_original(ticker, search_fn, info_fn, cache, today)
        if ticker == "MC.PA" and contrat.get("conid") is not None:
            return {**contrat, "currency": "USD"}
        return contrat

    monkeypatch.setattr(daily.contracts, "resolve_conid", resolve_avec_devise_incoherente)
    gw = FakeGateway()

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    assert 17275 not in [o["conid"] for o in gw.ordres]
    rejets = {r["ticker"]: r["raison"] for r in run["signaux_rejetes"]}
    assert rejets["MC.PA"] == "devise_incoherente"
    assert any(e["etape"] == "coherence_devise" and "MC.PA" in e["detail"]
              for e in run["erreurs"])
    # ADBE, non affecte par la falsification ci-dessus, est achete normalement.
    assert 202070 in [o["conid"] for o in gw.ordres]


# --- revue finale de branche : Important #4 ----------------------------

def test_a_real_run_batch_result_renders_correctly_through_notify(env, monkeypatch):
    """Important #4 : jusqu'ici, aucun test ne verifiait que le dict `run`
    produit par run_batch et ce que notify.py en affiche sont reellement
    d'accord — chaque module etait teste isolement (fixture a la main
    cote notify, send_daily_summary monkeypatchee cote daily). On fait
    tourner un vrai run_batch (dry_run, avec une position locale simulee,
    et un git pull en echec) puis on rend son `run` reel via
    build_summary_email_html, pour prouver que Important #1 (compte de
    positions actives en dry_run) ET Important #2 (detail d'un git pull en
    echec) apparaissent effectivement dans le HTML produit — pas
    seulement dans des fixtures isolees de chaque cote."""
    monkeypatch.setattr(daily, "pull_repo",
                        lambda *a, **k: {"ok": False, "detail": "fatal: divergent branches"})
    ecrire_etat(env, dry_run=True)
    simulee = {**POSITION_MC, "ticker": "ZZZ.PA", "conid": 555,
               "target_exit_price": 999.0, "date_limite": "2027-01-01",
               "prix_execution_reference": 10.0}
    _positions_locales(env, [simulee])
    gw = FakeGateway(positions_ibkr=[])

    run = daily.run_batch(TODAY, gw=gw, sleep_fn=lambda s: None,
                          account_id="U1", paths=env["paths"])

    html = daily.notify.build_summary_email_html(run, TODAY)

    assert run["reconciliation"]["actives"] == 1        # Important #1
    assert "1 position(s) active(s)" in html             # ... et rendu tel quel
    assert "fatal: divergent branches" in html            # Important #2
