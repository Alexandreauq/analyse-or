import json
import os

import pytest

import ibkr_bot.journal as journal
import ibkr_bot.portfolio as portfolio


def test_now_iso_is_a_utc_timestamp():
    valeur = journal.now_iso()
    assert valeur.endswith("Z")
    assert len(valeur) == 20  # 2026-09-15T14:45:03Z


def test_append_run_writes_one_json_line_and_adds_a_timestamp(tmp_path):
    path = str(tmp_path / "real_trading_log.jsonl")

    assert journal.append_run({"date": "2026-09-15", "statut": "termine"}, path) is True

    with open(path, encoding="utf-8") as fh:
        lignes = fh.read().splitlines()
    assert len(lignes) == 1
    entree = json.loads(lignes[0])
    assert entree["date"] == "2026-09-15"
    assert entree["timestamp"].endswith("Z")


def test_append_run_keeps_an_explicit_timestamp(tmp_path):
    path = str(tmp_path / "log.jsonl")
    journal.append_run({"timestamp": "2026-09-15T14:45:03Z", "statut": "termine"}, path)
    with open(path, encoding="utf-8") as fh:
        assert json.loads(fh.read())["timestamp"] == "2026-09-15T14:45:03Z"


def test_append_run_appends_without_truncating(tmp_path):
    path = str(tmp_path / "log.jsonl")
    journal.append_run({"date": "2026-09-14"}, path)
    journal.append_run({"date": "2026-09-15"}, path)
    with open(path, encoding="utf-8") as fh:
        lignes = fh.read().splitlines()
    assert [json.loads(l)["date"] for l in lignes] == ["2026-09-14", "2026-09-15"]


def test_append_run_returns_false_instead_of_raising(tmp_path):
    """Une panne d'ecriture du journal ne doit JAMAIS interrompre le batch
    (meme contrat que gold_bot.loop._log_decision) : le repertoire parent
    est ici un fichier, donc l'ouverture echoue forcement."""
    fichier = tmp_path / "pas_un_repertoire"
    fichier.write_text("x", encoding="utf-8")
    path = str(fichier / "log.jsonl")

    assert journal.append_run({"date": "2026-09-15"}, path) is False


def test_append_run_never_raises_on_non_serializable_content(tmp_path):
    path = str(tmp_path / "log.jsonl")
    assert journal.append_run({"date": "2026-09-15", "objet": object()}, path) is False


def test_read_runs_filters_on_the_day_and_skips_corrupt_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text(
        '{"timestamp": "2026-09-14T14:45:00Z", "statut": "termine"}\n'
        "pas du json\n"
        "\n"
        '{"timestamp": "2026-09-15T14:45:00Z", "statut": "kill_switch"}\n',
        encoding="utf-8",
    )
    runs = journal.read_runs(str(path), day="2026-09-15")
    assert [r["statut"] for r in runs] == ["kill_switch"]


def test_read_runs_returns_empty_list_when_the_file_is_absent(tmp_path):
    assert journal.read_runs(str(tmp_path / "absent.jsonl"), day="2026-09-15") == []


def test_build_order_record_carries_every_field_the_spec_requires():
    record = journal.build_order_record(
        ticker="MC.PA", conid=17275, sens="BUY", quantite=5,
        devise_compte="EUR", devise_cotation="EUR", taux_de_change=1.0,
        budget_converti=500.0, prix_reference_sizing=90.0, prix_paper=88.0,
        execution={"statut": "execute", "order_id": "1234",
                   "prix_execution_cotation": 90.5, "prix_execution_estime": False,
                   "commission": 1.25, "detail": None},
        rang=1,
    )
    assert record["ticker"] == "MC.PA"
    assert record["conid"] == 17275
    assert record["sens"] == "BUY"
    assert record["quantite"] == 5
    assert record["devise_compte"] == "EUR"
    assert record["taux_de_change"] == 1.0
    assert record["budget_converti"] == 500.0
    assert record["prix_reference_sizing"] == 90.0
    assert record["prix_execution"] == 90.5
    assert record["commission"] == 1.25
    assert record["prix_paper"] == 88.0
    assert record["order_id"] == "1234"
    assert record["statut"] == "execute"
    assert record["rang"] == 1


def test_build_order_record_converts_a_london_fill_price_from_pence_to_pounds():
    """GARDE-FOU PENCE/LIVRE (spec 4.7) : IBKR renvoie avgPrice en PENCE
    pour un ticker .L. Le champ `prix_execution` est celui qu'on compare a
    docs/indices.json (en LIVRES) et qu'on rapporte a l'utilisateur : il
    doit valoir 100x moins que le brut IBKR, jamais autant."""
    record = journal.build_order_record(
        ticker="III.L", conid=98765, sens="BUY", quantite=145,
        devise_compte="GBP", devise_cotation="GBp", taux_de_change=0.86,
        budget_converti=43000.0, prix_reference_sizing=2.95, prix_paper=2.93,
        execution={"statut": "execute", "order_id": "9", "prix_execution_cotation": 296.0,
                   "prix_execution_estime": False, "commission": None, "detail": None},
    )
    assert record["prix_execution_cotation"] == 296.0
    assert record["prix_execution"] == pytest.approx(2.96)
    assert record["devise_cotation"] == "GBp"
    assert record["devise_compte"] == "GBP"


def test_build_order_record_computes_the_gap_against_the_paper_price():
    record = journal.build_order_record(
        ticker="ADBE", conid=202070, sens="BUY", quantite=1,
        devise_compte="USD", devise_cotation="USD", prix_paper=500.0,
        execution={"statut": "execute", "order_id": "2", "prix_execution_cotation": 510.0,
                   "prix_execution_estime": False, "commission": None, "detail": None},
    )
    assert record["ecart_paper_pct"] == pytest.approx(2.0)


def test_build_order_record_leaves_the_gap_none_without_a_paper_price():
    record = journal.build_order_record(
        ticker="MC.PA", conid=1, sens="SELL", quantite=5, devise_compte="EUR",
        devise_cotation="EUR", close_reason="stop_loss",
        execution={"statut": "execute", "order_id": "3", "prix_execution_cotation": 70.0,
                   "prix_execution_estime": False, "commission": None, "detail": None},
    )
    assert record["prix_paper"] is None
    assert record["ecart_paper_pct"] is None
    assert record["close_reason"] == "stop_loss"


def test_build_order_record_on_a_failed_order_keeps_the_cause_and_no_price():
    record = journal.build_order_record(
        ticker="SAP.DE", conid=1234, sens="BUY", quantite=3, devise_compte="EUR",
        devise_cotation="EUR",
        execution={"statut": "erreur", "order_id": None, "prix_execution_cotation": None,
                   "prix_execution_estime": False, "commission": None,
                   "detail": "400 Client Error"},
    )
    assert record["statut"] == "erreur"
    assert record["prix_execution"] is None
    assert record["prix_execution_cotation"] is None
    assert record["ecart_paper_pct"] is None
    assert record["detail"] == "400 Client Error"
    assert record["quantite"] == 3


def test_build_order_record_without_an_execution_is_a_simulation():
    record = journal.build_order_record(
        ticker="MC.PA", conid=17275, sens="BUY", quantite=5,
        devise_compte="EUR", devise_cotation="EUR",
    )
    assert record["statut"] == "simule"
    assert record["order_id"] is None
    assert record["prix_execution"] is None


def test_build_position_record_produces_exactly_what_portfolio_needs():
    """Contrat inter-modules : la position ecrite ici est relue telle quelle
    par portfolio.exit_reason et portfolio.reconcile — on les appelle donc
    POUR DE VRAI sur le resultat plutot que d'assert des noms de cles."""
    signal = {"id": "MC.PA-2026-09-15", "ticker": "MC.PA", "name": "LVMH",
              "index": "CAC40", "currency": "EUR", "entry_date": "2026-09-15",
              "paper_entry_price": 88.0, "target_exit_price": 120.0,
              "score": 55.2, "current_price": 90.0}
    plan = {"ticker": "MC.PA", "quantite": 5, "devise_cotation": "EUR",
            "devise_compte": "EUR", "taux_de_change": 1.0, "budget_converti": 500.0,
            "prix_unitaire_cotation": 90.0, "cout_estime_devise_compte": 450.0,
            "motif": None}
    contrat = {"ticker": "MC.PA", "conid": 17275, "exchange": "SBF",
               "currency": "EUR", "motif": None, "detail": "resolu"}

    position = journal.build_position_record(
        signal, plan, contrat, quantite=5, prix_execution_reference=90.5,
        today="2026-09-15")

    assert position["id"] == "MC.PA-2026-09-15"
    assert position["conid"] == 17275
    assert position["quantite"] == 5
    assert position["prix_execution_reference"] == 90.5
    assert position["date_entree"] == "2026-09-15"
    assert position["date_limite"] == "2027-03-15"
    assert position["target_exit_price"] == 120.0
    assert position["devise"] == "EUR"

    # Relue par les VRAIES fonctions de Plan A, sans adaptation :
    company = {"ticker": "MC.PA", "current_price": 70.0}
    assert portfolio.exit_reason(position, company, "2026-09-16") == "stop_loss"
    reconciliation = portfolio.reconcile(
        [position], [{"conid": 17275, "position": 5.0}])
    assert reconciliation["actives"][0]["id"] == "MC.PA-2026-09-15"
    assert reconciliation["anomalies_quantite"] == []


def test_save_positions_safely_writes_a_file_portfolio_can_reload(tmp_path):
    path = str(tmp_path / "positions.json")
    positions = [{"id": "MC.PA-2026-09-15", "ticker": "MC.PA", "conid": 17275,
                  "quantite": 5}]

    assert journal.save_positions_safely(positions, path) is True
    assert portfolio.load_positions(path) == positions


def test_save_positions_safely_returns_false_instead_of_raising(tmp_path):
    fichier = tmp_path / "pas_un_repertoire"
    fichier.write_text("x", encoding="utf-8")
    assert journal.save_positions_safely([], str(fichier / "positions.json")) is False


def test_save_account_snapshot_writes_json_and_never_raises(tmp_path):
    path = str(tmp_path / "latest_account.json")
    assert journal.save_account_snapshot({"base_cash": 8000.0}, path) is True
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["base_cash"] == 8000.0

    fichier = tmp_path / "fichier"
    fichier.write_text("x", encoding="utf-8")
    assert journal.save_account_snapshot({}, str(fichier / "x.json")) is False


def test_load_account_snapshot_reads_back_what_save_account_snapshot_wrote(tmp_path):
    path = str(tmp_path / "latest_account.json")
    journal.save_account_snapshot({"base_cash": 8000.0}, path)
    data = journal.load_account_snapshot(path)
    assert data["base_cash"] == 8000.0
    assert data["fetched_at"].endswith("Z")


def test_load_account_snapshot_degrades_to_empty_dict_when_file_absent_or_corrupt(tmp_path):
    assert journal.load_account_snapshot(str(tmp_path / "absent.json")) == {}
    corrompu = tmp_path / "corrompu.json"
    corrompu.write_text("pas du json", encoding="utf-8")
    assert journal.load_account_snapshot(str(corrompu)) == {}


def test_load_account_snapshot_degrades_to_empty_dict_when_top_level_is_not_a_dict(tmp_path):
    path = tmp_path / "liste.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert journal.load_account_snapshot(str(path)) == {}
