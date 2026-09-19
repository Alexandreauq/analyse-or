import json

import pytest
from fastapi.testclient import TestClient

import ibkr_bot.api as api
import ibkr_bot.journal as journal
import ibkr_bot.portfolio as portfolio


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("IBKR_BOT_API_TOKEN", "secret-token")
    return TestClient(api.app)


def test_dashboard_requires_valid_token(client):
    response = client.get("/dashboard")
    assert response.status_code == 401


def test_dashboard_rejects_wrong_token(client):
    response = client.get("/dashboard", headers={"X-Bot-Token": "wrong-token"})
    assert response.status_code == 401


def test_dashboard_rejected_when_ibkr_bot_api_token_not_configured(monkeypatch):
    monkeypatch.delenv("IBKR_BOT_API_TOKEN", raising=False)
    test_client = TestClient(api.app)
    response = test_client.get("/dashboard", headers={"X-Bot-Token": "anything"})
    assert response.status_code == 401


def test_dashboard_only_exposes_the_get_method_no_mutation_route_exists(client):
    response = client.post("/dashboard", headers={"X-Bot-Token": "secret-token"})
    assert response.status_code in (405, 404)
    methods = [route.methods for route in api.app.routes if hasattr(route, "methods")]
    all_methods = set().union(*methods) if methods else set()
    assert "POST" not in all_methods
    assert "DELETE" not in all_methods


def test_gateway_module_is_never_referenced_in_api_source():
    import inspect
    source = inspect.getsource(api)
    assert "gateway" not in source


def test_api_path_constants_match_source_modules():
    assert api.ACCOUNT_SNAPSHOT_PATH == journal.LATEST_ACCOUNT_PATH
    assert api.POSITIONS_PATH == portfolio.POSITIONS_PATH
    assert api.REAL_TRADING_LOG_PATH == journal.REAL_TRADING_LOG_PATH


def _seed(monkeypatch, tmp_path, *, account=None, positions=None, actions_lines=None):
    monkeypatch.setattr(api, "ACCOUNT_SNAPSHOT_PATH", str(tmp_path / "latest_account.json"))
    monkeypatch.setattr(api, "POSITIONS_PATH", str(tmp_path / "positions.json"))
    monkeypatch.setattr(api, "REAL_TRADING_LOG_PATH", str(tmp_path / "real_trading_log.jsonl"))
    if account is not None:
        with open(api.ACCOUNT_SNAPSHOT_PATH, "w", encoding="utf-8") as fh:
            json.dump(account, fh)
    if positions is not None:
        with open(api.POSITIONS_PATH, "w", encoding="utf-8") as fh:
            json.dump({"positions": positions}, fh)
    if actions_lines is not None:
        with open(api.REAL_TRADING_LOG_PATH, "w", encoding="utf-8") as fh:
            for entry in actions_lines:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def test_dashboard_returns_full_body_when_all_sources_present(client, monkeypatch, tmp_path):
    _seed(
        monkeypatch, tmp_path,
        account={"base_cash": 8123.45, "fetched_at": "2026-09-20T14:45:03Z"},
        positions=[{
            "id": "MC.PA-2026-09-15", "ticker": "MC.PA", "name": "LVMH", "index": "CAC40",
            "conid": 17275, "quantite": 5, "prix_execution_reference": 90.5,
            "date_entree": "2026-09-15", "target_exit_price": 120.0,
        }],
        actions_lines=[{
            "date": "2026-09-18", "entrees": [{
                "ticker": "MC.PA", "sens": "BUY", "quantite": 5, "prix_execution": 90.5,
                "statut": "execute",
            }], "sorties": [],
        }],
    )

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["balance"] == 8123.45
    assert body["balance_fetched_at"] == "2026-09-20T14:45:03Z"
    assert body["positions"] == [{
        "ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "quantite": 5,
        "prix_entree": 90.5, "date_entree": "2026-09-15", "target_exit_price": 120.0,
    }]
    assert body["actions"] == [{
        "ticker": "MC.PA", "sens": "BUY", "quantite": 5, "prix_execution": 90.5,
        "statut": "execute", "date": "2026-09-18",
    }]


def test_dashboard_degrades_gracefully_when_all_source_files_are_absent(client, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["balance"] is None
    assert body["balance_fetched_at"] is None
    assert body["positions"] == []
    assert body["actions"] == []


def test_dashboard_sanitizes_non_finite_balance(client, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, account={"base_cash": float("nan"), "fetched_at": "2026-09-20T14:45:03Z"})

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["balance"] is None
    assert body["balance_fetched_at"] == "2026-09-20T14:45:03Z"


def test_dashboard_sanitizes_non_finite_position_fields(client, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, positions=[{
        "ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "quantite": 5,
        "prix_execution_reference": float("nan"), "date_entree": "2026-09-15",
        "target_exit_price": float("inf"),
    }])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    positions = response.json()["positions"]
    assert positions[0]["prix_entree"] is None
    assert positions[0]["target_exit_price"] is None


def test_dashboard_sanitizes_non_finite_action_fields(client, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, actions_lines=[{
        "date": "2026-09-18", "entrees": [{
            "ticker": "MC.PA", "sens": "BUY", "statut": "execute",
            "prix_execution": float("nan"), "ecart_paper_pct": 2.0,
        }], "sorties": [],
    }])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    action = response.json()["actions"][0]
    assert action["prix_execution"] is None
    assert action["ecart_paper_pct"] == 2.0


def test_dashboard_excludes_annule_interruption_actions(client, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, actions_lines=[{
        "date": "2026-09-18", "entrees": [
            {"ticker": "A", "statut": "execute"},
            {"ticker": "B", "statut": "annule_interruption"},
        ], "sorties": [],
    }])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert [a["ticker"] for a in response.json()["actions"]] == ["A"]


def test_dashboard_respects_the_recent_actions_limit(client, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "ACCOUNT_SNAPSHOT_PATH", str(tmp_path / "latest_account.json"))
    monkeypatch.setattr(api, "POSITIONS_PATH", str(tmp_path / "positions.json"))
    lignes = [
        json.dumps({"date": f"2026-09-{(i % 28) + 1:02d}", "entrees": [
            {"ticker": f"T{i}", "statut": "execute"}], "sorties": []})
        for i in range(60)
    ]
    log_path = tmp_path / "real_trading_log.jsonl"
    log_path.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    monkeypatch.setattr(api, "REAL_TRADING_LOG_PATH", str(log_path))

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    actions = response.json()["actions"]
    assert len(actions) == 50
    assert [a["ticker"] for a in actions] == [f"T{i}" for i in range(10, 60)]


def test_dashboard_skips_non_dict_position_entries(client, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, positions=[
        None, 5, {"ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "quantite": 5,
                  "prix_execution_reference": 90.5, "date_entree": "2026-09-15",
                  "target_exit_price": 120.0},
    ])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    positions = response.json()["positions"]
    assert len(positions) == 1
    assert positions[0]["ticker"] == "MC.PA"
