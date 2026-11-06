# tests/test_gold_news_reasoning_daily.py
import json
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

import gold_news_reasoning_daily as g


def test_normalize_direction_call_haussier_variants():
    assert g.normalize_direction_call("haussier") == "achat"
    assert g.normalize_direction_call("leger_haussier") == "achat"
    assert g.normalize_direction_call("HAUSSIER") == "achat"


def test_normalize_direction_call_baissier_variants():
    assert g.normalize_direction_call("baissier") == "vente"
    assert g.normalize_direction_call("leger_baissier") == "vente"
    assert g.normalize_direction_call("Baissier") == "vente"


def test_normalize_direction_call_neutre_variants():
    assert g.normalize_direction_call("neutre") is None
    assert g.normalize_direction_call("neutre_prudent") is None


def test_normalize_direction_call_unrecognized_or_wrong_type_returns_none():
    assert g.normalize_direction_call("incertain") is None
    assert g.normalize_direction_call("") is None
    assert g.normalize_direction_call(None) is None
    assert g.normalize_direction_call(123) is None


def test_clamp_stop_pct_within_range_unchanged():
    assert g.clamp_stop_pct(0.08) == 0.08


def test_clamp_stop_pct_below_min_clamped_up():
    assert g.clamp_stop_pct(0.01) == g.MIN_STOP_PCT


def test_clamp_stop_pct_above_max_clamped_down():
    assert g.clamp_stop_pct(0.25) == g.MAX_STOP_PCT


def test_clamp_stop_pct_negative_input_takes_absolute_value():
    assert g.clamp_stop_pct(-0.10) == 0.10


def test_clamp_stop_pct_exact_boundaries_unchanged():
    assert g.clamp_stop_pct(0.03) == 0.03
    assert g.clamp_stop_pct(0.15) == 0.15


def test_compute_safety_stop_price_achat_is_below_entry():
    stop = g.compute_safety_stop_price(4000.0, "achat", 0.05)
    assert stop == 3800.0


def test_compute_safety_stop_price_vente_is_above_entry():
    stop = g.compute_safety_stop_price(4000.0, "vente", 0.05)
    assert stop == 4200.0


def test_compute_safety_stop_price_clamps_out_of_range_input():
    stop = g.compute_safety_stop_price(4000.0, "achat", 0.50)
    assert stop == 4000.0 * (1 - g.MAX_STOP_PCT)


def test_compute_safety_stop_pct_achat_is_negative():
    pct = g.compute_safety_stop_pct(4000.0, 3800.0, "achat")
    assert round(pct, 4) == -0.05


def test_compute_safety_stop_pct_vente_is_negative():
    pct = g.compute_safety_stop_pct(4000.0, 4200.0, "vente")
    assert round(pct, 4) == -0.0476


def test_compute_return_pct_achat_positive_when_price_rises():
    assert round(g.compute_return_pct(4000.0, 4200.0, "achat"), 4) == 0.05


def test_compute_return_pct_vente_positive_when_price_falls():
    assert round(g.compute_return_pct(4000.0, 3800.0, "vente"), 4) == 0.0526


def test_compute_buyhold_return_pct_is_raw_never_direction_adjusted():
    # Une vente dont le prix baisse gagne (return_pct positif), mais le
    # repere buy&hold reste neutre/BRUT sur le meme mouvement de prix --
    # ici negatif puisque le prix a baisse, peu importe la direction du pari.
    buyhold = g.compute_buyhold_return_pct(4000.0, 3800.0)
    assert round(buyhold, 4) == -0.05


def test_compute_max_close_date_adds_21_days():
    assert g.compute_max_close_date(date(2026, 11, 10)) == date(2026, 12, 1)


def test_find_stop_breach_achat_detects_low_crossing():
    rows = [
        {"date": "2026-11-10", "high": 4100.0, "low": 4050.0},
        {"date": "2026-11-11", "high": 4060.0, "low": 3940.0},
    ]
    breach = g.find_stop_breach("achat", 3950.0, rows)
    assert breach["date"] == "2026-11-11"


def test_find_stop_breach_vente_detects_high_crossing():
    rows = [
        {"date": "2026-11-10", "high": 4150.0, "low": 4050.0},
        {"date": "2026-11-11", "high": 4260.0, "low": 4040.0},
    ]
    breach = g.find_stop_breach("vente", 4250.0, rows)
    assert breach["date"] == "2026-11-11"


def test_find_stop_breach_returns_first_chronological_day_not_most_extreme():
    # Le 11 franchit deja le stop (low 3940 < 3950) ; le 12, non trie en
    # premier dans la liste, descend bien plus bas (3800) mais est APRES
    # le 11 chronologiquement -- doit renvoyer le 11, pas le 12.
    rows = [
        {"date": "2026-11-12", "high": 4000.0, "low": 3800.0},
        {"date": "2026-11-11", "high": 4060.0, "low": 3940.0},
        {"date": "2026-11-10", "high": 4100.0, "low": 4050.0},
    ]
    breach = g.find_stop_breach("achat", 3950.0, rows)
    assert breach["date"] == "2026-11-11"


def test_find_stop_breach_returns_none_when_never_crossed():
    rows = [
        {"date": "2026-11-10", "high": 4100.0, "low": 4050.0},
        {"date": "2026-11-11", "high": 4060.0, "low": 4030.0},
    ]
    assert g.find_stop_breach("achat", 3950.0, rows) is None


def _fake_yf_dataframe(rows):
    df = pd.DataFrame(
        {"Open": [r["open"] for r in rows], "High": [r["high"] for r in rows],
         "Low": [r["low"] for r in rows], "Close": [r["close"] for r in rows]},
        index=pd.to_datetime([r["date"] for r in rows]),
    )
    return df


def test_fetch_daily_ohlc_success_with_primary_ticker(monkeypatch):
    rows = [
        {"date": "2026-11-05", "open": 4070.0, "high": 4080.0, "low": 4060.0, "close": 4075.0},
        {"date": "2026-11-06", "open": 4075.0, "high": 4090.0, "low": 4070.0, "close": 4080.0},
    ]

    def fake_download(ticker, period=None, interval=None, progress=None):
        assert ticker == g.YF_PRIMARY_TICKER
        return _fake_yf_dataframe(rows)

    monkeypatch.setattr(g.yf, "download", fake_download)
    result = g.fetch_daily_ohlc()
    assert result == rows


def test_fetch_daily_ohlc_falls_back_to_second_ticker_when_primary_empty(monkeypatch):
    rows = [{"date": "2026-11-06", "open": 4075.0, "high": 4090.0, "low": 4070.0, "close": 4080.0}]

    def fake_download(ticker, period=None, interval=None, progress=None):
        if ticker == g.YF_PRIMARY_TICKER:
            return pd.DataFrame()
        return _fake_yf_dataframe(rows)

    monkeypatch.setattr(g.yf, "download", fake_download)
    assert g.fetch_daily_ohlc() == rows


def test_fetch_daily_ohlc_returns_none_when_both_tickers_fail(monkeypatch):
    monkeypatch.setattr(g.yf, "download", lambda *a, **k: pd.DataFrame())
    assert g.fetch_daily_ohlc() is None


def test_fetch_daily_ohlc_returns_none_on_exception(monkeypatch):
    def raising(*a, **k):
        raise ConnectionError("boom")

    monkeypatch.setattr(g.yf, "download", raising)
    assert g.fetch_daily_ohlc() is None


def test_fetch_daily_ohlc_sorted_by_date(monkeypatch):
    rows = [
        {"date": "2026-11-06", "open": 4075.0, "high": 4090.0, "low": 4070.0, "close": 4080.0},
        {"date": "2026-11-05", "open": 4070.0, "high": 4080.0, "low": 4060.0, "close": 4075.0},
    ]
    monkeypatch.setattr(g.yf, "download", lambda *a, **k: _fake_yf_dataframe(rows))
    result = g.fetch_daily_ohlc()
    assert [r["date"] for r in result] == ["2026-11-05", "2026-11-06"]


def _fake_response(status_ok=True, json_body=None, raise_exc=None):
    resp = MagicMock()
    if raise_exc is not None:
        resp.raise_for_status.side_effect = raise_exc
    else:
        resp.raise_for_status.return_value = None
    resp.json.return_value = json_body or {}
    return resp


def test_call_claude_for_new_entry_returns_none_when_api_key_absent(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert g.call_claude_for_new_entry([{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}]) is None


def test_call_claude_for_new_entry_parses_successful_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    body = {"content": [{"type": "text", "text": 'blah {"appel": "haussier", "stop_pct": 0.08, "confiance": "moyenne", "raison": "test"}'}]}
    monkeypatch.setattr(g.requests, "post", lambda *a, **k: _fake_response(json_body=body))
    result = g.call_claude_for_new_entry([{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}])
    assert result == {"appel": "haussier", "stop_pct": 0.08, "confiance": "moyenne", "raison": "test"}


def test_call_claude_for_new_entry_returns_none_on_http_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setattr(g.requests, "post", lambda *a, **k: _fake_response(raise_exc=Exception("500")))
    assert g.call_claude_for_new_entry([{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}]) is None


def test_call_claude_for_new_entry_returns_none_on_unparseable_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    body = {"content": [{"type": "text", "text": "reponse sans JSON exploitable"}]}
    monkeypatch.setattr(g.requests, "post", lambda *a, **k: _fake_response(json_body=body))
    assert g.call_claude_for_new_entry([{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}]) is None


def test_call_claude_for_new_entry_returns_none_on_timeout(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    def raising(*a, **k):
        raise TimeoutError("boom")

    monkeypatch.setattr(g.requests, "post", raising)
    assert g.call_claude_for_new_entry([{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}]) is None


def test_call_claude_for_reeval_returns_none_when_api_key_absent(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    position = {"entry_date": "2026-11-01", "direction": "achat", "entry_price": 4000.0, "raison_ouverture": "x"}
    assert g.call_claude_for_reeval(position, [{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}]) is None


def test_call_claude_for_reeval_parses_successful_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    body = {"content": [{"type": "text", "text": '{"decision": "garder", "raison": "test"}'}]}
    monkeypatch.setattr(g.requests, "post", lambda *a, **k: _fake_response(json_body=body))
    position = {"entry_date": "2026-11-01", "direction": "achat", "entry_price": 4000.0, "raison_ouverture": "x"}
    result = g.call_claude_for_reeval(position, [{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}])
    assert result == {"decision": "garder", "raison": "test"}


def test_call_claude_for_reeval_returns_none_on_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setattr(g.requests, "post", lambda *a, **k: _fake_response(raise_exc=Exception("boom")))
    position = {"entry_date": "2026-11-01", "direction": "achat", "entry_price": 4000.0, "raison_ouverture": "x"}
    assert g.call_claude_for_reeval(position, [{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}]) is None
