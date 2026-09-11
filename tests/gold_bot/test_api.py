import json

import pytest
from fastapi.testclient import TestClient

import gold_bot.api as api
import gold_bot.loop as loop
import gold_bot.state as state


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("BOT_API_TOKEN", "secret-token")
    return TestClient(api.app)


def test_status_returns_current_state(client):
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert body["kill_switch"] is False
    assert body["dry_run"] is True


def test_kill_requires_valid_token(client):
    response = client.post("/kill", headers={"X-Bot-Token": "wrong-token"})
    assert response.status_code == 401


def test_kill_without_token_header_is_rejected(client):
    response = client.post("/kill")
    assert response.status_code == 401


def test_kill_sets_kill_switch_true(client):
    response = client.post("/kill", headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 200
    assert response.json()["kill_switch"] is True
    assert client.get("/status").json()["kill_switch"] is True


def test_resume_sets_kill_switch_false(client):
    client.post("/kill", headers={"X-Bot-Token": "secret-token"})
    response = client.post("/resume", headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 200
    assert response.json()["kill_switch"] is False


def test_resume_requires_valid_token(client):
    response = client.post("/resume", headers={"X-Bot-Token": "wrong-token"})
    assert response.status_code == 401


def test_status_reports_circuit_breaker_fields_from_separate_file(client, monkeypatch, tmp_path):
    cb_path = str(tmp_path / "circuit_breaker_state.json")
    monkeypatch.setattr(api, "CIRCUIT_BREAKER_STATE_PATH", cb_path)
    api.state.save_state(
        {"circuit_breaker_day": "2026-09-10", "circuit_breaker_starting_balance": 10000.0},
        cb_path,
    )
    response = client.get("/status")
    body = response.json()
    assert body["circuit_breaker_day"] == "2026-09-10"


def test_kill_rejected_when_bot_api_token_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.delenv("BOT_API_TOKEN", raising=False)
    test_client = TestClient(api.app)
    response = test_client.post("/kill", headers={"X-Bot-Token": "anything"})
    assert response.status_code == 401


def test_rejected_kill_does_not_mutate_state(client):
    client.post("/kill", headers={"X-Bot-Token": "secret-token"})
    client.post("/resume", headers={"X-Bot-Token": "wrong-token"})
    status = client.get("/status").json()
    assert status["kill_switch"] is True  # inchangé malgré la tentative rejetée


def test_broker_module_is_never_referenced_in_api_source():
    import inspect
    source = inspect.getsource(api)
    assert "broker" not in source


def test_kill_rejected_not_crashed_on_non_ascii_token(client):
    # Header envoyé comme octets latin-1 bruts (ce que le serveur reçoit
    # réellement sur le fil) plutôt qu'un `str` Python — httpx (client de
    # test) refuse lui-même d'encoder un `str` non-ASCII en en-tête HTTP
    # avant même que la requête ne parte, ce qui ne reproduirait pas le
    # scénario visé (un octet non-ASCII arrivant côté serveur).
    response = client.post("/kill", headers={"X-Bot-Token": "tokén".encode("latin-1")})
    assert response.status_code == 401


def test_circuit_breaker_state_path_matches_loop_module():
    assert api.CIRCUIT_BREAKER_STATE_PATH == loop.CIRCUIT_BREAKER_STATE_PATH


def _seed_dashboard_caches(monkeypatch, tmp_path, *, balance=None, positions=None, candles=None, decisions_lines=None):
    monkeypatch.setattr(api, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(api, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(api, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(api, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    if balance is not None:
        state.save_state(balance, api.LATEST_BALANCE_PATH)
    if positions is not None:
        state.save_state(positions, api.LATEST_POSITIONS_PATH)
    if candles is not None:
        state.save_state(candles, api.LATEST_CANDLES_PATH)
    if decisions_lines is not None:
        with open(api.DECISIONS_LOG_PATH, "w", encoding="utf-8") as fh:
            for entry in decisions_lines:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def test_dashboard_requires_valid_token(client):
    response = client.get("/dashboard")
    assert response.status_code == 401


def test_dashboard_rejects_wrong_token(client):
    response = client.get("/dashboard", headers={"X-Bot-Token": "wrong-token"})
    assert response.status_code == 401


def test_dashboard_returns_full_body_when_all_caches_present(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(
        monkeypatch, tmp_path,
        balance={"balance": 9140.10, "fetched_at": "2026-09-11T16:40:05Z"},
        positions={"positions": [
            {"symbol": "XAUUSD", "type": "POSITION_TYPE_SELL", "volume": 2.0,
             "openPrice": 4316.28, "currentPrice": 4316.49, "profit": -36.18},
        ], "fetched_at": "2026-09-11T16:40:06Z"},
        candles={"candles": [{"time": "2026-09-11 16:40:00", "close": 3651.5}],
                 "fetched_at": "2026-09-11T16:40:04Z"},
        decisions_lines=[
            {"action": "simulation_dry_run", "timestamp": "2026-09-11T14:22:03Z",
             "steps": [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                        "entry": 3648.2, "stop_loss": 3644.0, "take_profit": 3656.0}]},
            {"action": "aucune", "reason": "signal neutre", "timestamp": "2026-09-11T14:23:03Z"},
        ],
    )

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["balance"] == 9140.10
    assert body["balance_fetched_at"] == "2026-09-11T16:40:05Z"
    assert body["positions"] == [{
        "symbol": "XAUUSD", "direction": "vente", "volume": 2.0,
        "open_price": 4316.28, "current_price": 4316.49, "profit": -36.18,
        "stop_loss": None, "take_profit": None,
    }]
    assert body["candles"] == [{"time": "2026-09-11 16:40:00", "close": 3651.5}]
    assert body["candles_fetched_at"] == "2026-09-11T16:40:04Z"
    assert body["recent_decisions"] == [{
        "timestamp": "2026-09-11T14:22:03Z", "type": "ouverture_simulee", "symbol": "XAUUSD",
        "direction": "achat", "entry": 3648.2, "stop_loss": 3644.0, "take_profit": 3656.0,
    }]


def test_dashboard_translates_buy_position_type(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(monkeypatch, tmp_path, positions={"positions": [
        {"symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "volume": 1.0,
         "openPrice": 3600.0, "currentPrice": 3610.0, "profit": 10.0},
    ], "fetched_at": "2026-09-11T16:40:06Z"})

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.json()["positions"][0]["direction"] == "achat"


def test_dashboard_balance_null_when_cache_missing_but_other_sources_intact(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(
        monkeypatch, tmp_path,
        positions={"positions": [], "fetched_at": "2026-09-11T16:40:06Z"},
        candles={"candles": [{"time": "2026-09-11 16:40:00", "close": 3651.5}], "fetched_at": "2026-09-11T16:40:04Z"},
    )

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["balance"] is None
    assert body["balance_fetched_at"] is None
    assert body["positions"] == []
    assert body["candles"] == [{"time": "2026-09-11 16:40:00", "close": 3651.5}]


def test_dashboard_positions_empty_when_cache_missing_but_other_sources_intact(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(
        monkeypatch, tmp_path,
        balance={"balance": 9140.10, "fetched_at": "2026-09-11T16:40:05Z"},
        candles={"candles": [{"time": "2026-09-11 16:40:00", "close": 3651.5}], "fetched_at": "2026-09-11T16:40:04Z"},
    )

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["positions"] == []
    assert body["balance"] == 9140.10
    assert body["candles"] == [{"time": "2026-09-11 16:40:00", "close": 3651.5}]


def test_dashboard_candles_null_when_cache_missing_but_other_sources_intact(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(
        monkeypatch, tmp_path,
        balance={"balance": 9140.10, "fetched_at": "2026-09-11T16:40:05Z"},
        positions={"positions": [], "fetched_at": "2026-09-11T16:40:06Z"},
    )

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["candles"] is None
    assert body["candles_fetched_at"] is None
    assert body["balance"] == 9140.10


def test_dashboard_recent_decisions_empty_when_log_missing(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(monkeypatch, tmp_path)

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    assert response.json()["recent_decisions"] == []


def test_dashboard_excludes_neutral_and_error_decisions(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(monkeypatch, tmp_path, decisions_lines=[
        {"action": "aucune", "reason": "signal neutre", "timestamp": "2026-09-11T14:20:00Z"},
        {"action": "erreur", "reason": "504 Gateway Timeout", "timestamp": "2026-09-11T14:21:00Z"},
        {"action": "ignore", "reason": "interrupteur d'urgence activé", "timestamp": "2026-09-11T14:22:00Z"},
    ])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.json()["recent_decisions"] == []


def test_dashboard_excludes_closing_steps_from_markers(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(monkeypatch, tmp_path, decisions_lines=[
        {"action": "exécuté", "timestamp": "2026-09-11T14:22:03Z", "steps": [
            {"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"},
            {"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "vente",
             "entry": 3648.2, "stop_loss": 3652.0, "take_profit": 3640.0},
        ]},
    ])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    decisions = response.json()["recent_decisions"]
    assert len(decisions) == 1
    assert decisions[0]["type"] == "ouverture_simulee"


def test_dashboard_cache_path_constants_match_loop_module():
    assert api.LATEST_CANDLES_PATH == loop.LATEST_CANDLES_PATH
    assert api.LATEST_BALANCE_PATH == loop.LATEST_BALANCE_PATH
    assert api.LATEST_POSITIONS_PATH == loop.LATEST_POSITIONS_PATH
    assert api.DECISIONS_LOG_PATH == loop.DECISIONS_LOG_PATH


def test_dashboard_sanitizes_non_finite_candle_fields_without_dropping_the_candle(client, monkeypatch, tmp_path):
    # Twelve Data ne garantit que `close` fini (validé par
    # confluence.fetch_gold_candles) — `open`/`high`/`low` peuvent être
    # NaN. Un seul champ NaN ne doit jamais faire échouer toute la
    # réponse /dashboard (allow_nan=False côté FastAPI), et la bougie
    # elle-même ne doit pas disparaître : seul le champ non fini devient
    # `null`, le reste de la bougie (et les autres bougies) survit.
    _seed_dashboard_caches(monkeypatch, tmp_path, candles={"candles": [
        {"time": "2026-09-11 16:40:00", "open": float("nan"), "close": 3651.5},
        {"time": "2026-09-11 16:41:00", "open": 3651.5, "close": 3652.0},
    ], "fetched_at": "2026-09-11T16:40:04Z"})

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["candles"] == [
        {"time": "2026-09-11 16:40:00", "open": None, "close": 3651.5},
        {"time": "2026-09-11 16:41:00", "open": 3651.5, "close": 3652.0},
    ]
    # Le solde et les positions ne doivent pas être affectés par une
    # bougie invalide ailleurs dans la réponse.
    assert body["balance"] is None
    assert body["positions"] == []


def test_dashboard_recent_decisions_window_counts_only_real_decisions(client, monkeypatch, tmp_path):
    # ~1 ligne par minute et des cycles très majoritairement "aucune"
    # en production : trancher les 50 dernières LIGNES brutes viderait
    # quasiment toujours ce résultat. Il faut filtrer d'abord, puis ne
    # garder que les 50 dernières décisions réelles.
    noise = [
        {"action": "aucune", "reason": "signal neutre", "timestamp": f"2026-09-11T10:{i:02d}:00Z"}
        for i in range(200)
    ]
    real = [
        {
            "action": "exécuté" if i % 2 == 0 else "simulation_dry_run",
            "timestamp": f"2026-09-11T14:{i:02d}:00Z",
            "steps": [{
                "type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                "entry": float(i), "stop_loss": float(i - 1), "take_profit": float(i + 1),
            }],
        }
        for i in range(60)
    ]
    _seed_dashboard_caches(monkeypatch, tmp_path, decisions_lines=noise + real)

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    decisions = response.json()["recent_decisions"]
    assert len(decisions) == 50
    assert [d["entry"] for d in decisions] == [float(i) for i in range(10, 60)]


def test_dashboard_skips_malformed_decision_log_lines_without_crashing(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(monkeypatch, tmp_path, decisions_lines=[
        5,
        [1, 2],
        {"action": "exécuté", "timestamp": "2026-09-11T14:20:00Z", "steps": None},
        {"action": "exécuté", "timestamp": "2026-09-11T14:21:00Z", "steps": ["not-a-dict-step", 42]},
        {"action": "simulation_dry_run", "timestamp": "2026-09-11T14:22:03Z",
         "steps": [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "entry": 3648.2, "stop_loss": 3644.0, "take_profit": 3656.0}]},
    ])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    decisions = response.json()["recent_decisions"]
    assert decisions == [{
        "timestamp": "2026-09-11T14:22:03Z", "type": "ouverture_simulee", "symbol": "XAUUSD",
        "direction": "achat", "entry": 3648.2, "stop_loss": 3644.0, "take_profit": 3656.0,
    }]


def test_dashboard_null_positions_cache_value_degrades_to_empty_list(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(monkeypatch, tmp_path, positions={
        "positions": None, "fetched_at": "2026-09-11T16:40:06Z",
    })

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["positions"] == []
    assert body["positions_fetched_at"] == "2026-09-11T16:40:06Z"


def test_dashboard_skips_non_dict_position_entries(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(monkeypatch, tmp_path, positions={
        "positions": [None, 5, {"symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}],
        "fetched_at": "2026-09-11T16:40:06Z",
    })

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    positions = response.json()["positions"]
    assert len(positions) == 1
    assert positions[0]["symbol"] == "XAUUSD"
    assert positions[0]["direction"] == "achat"
