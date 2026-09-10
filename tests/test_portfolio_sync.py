import json

import pytest
import portfolio_sync


class _FakeFlexResponse:
    def __init__(self, text):
        self._text = text

    def raise_for_status(self):
        pass

    @property
    def text(self):
        return self._text


def test_fetch_flex_reference_code_returns_reference_code_on_success(monkeypatch):
    monkeypatch.setattr(
        portfolio_sync.requests, "get",
        lambda *a, **k: _FakeFlexResponse(
            '<FlexStatementResponse><ReferenceCode>1234567890</ReferenceCode>'
            '<Url>https://example.com</Url></FlexStatementResponse>'
        ),
    )
    assert portfolio_sync.fetch_flex_reference_code("tok", "qid") == "1234567890"


def test_fetch_flex_reference_code_raises_on_ibkr_error(monkeypatch):
    monkeypatch.setattr(
        portfolio_sync.requests, "get",
        lambda *a, **k: _FakeFlexResponse(
            '<FlexStatementResponse><ErrorCode>1003</ErrorCode>'
            '<ErrorMessage>Statement is not available.</ErrorMessage></FlexStatementResponse>'
        ),
    )
    with pytest.raises(RuntimeError, match="Statement is not available"):
        portfolio_sync.fetch_flex_reference_code("tok", "qid")


def test_fetch_flex_reference_code_raises_when_no_reference_and_no_error(monkeypatch):
    monkeypatch.setattr(
        portfolio_sync.requests, "get",
        lambda *a, **k: _FakeFlexResponse("<FlexStatementResponse></FlexStatementResponse>"),
    )
    with pytest.raises(RuntimeError, match="ReferenceCode"):
        portfolio_sync.fetch_flex_reference_code("tok", "qid")


def test_fetch_flex_statement_returns_xml_on_immediate_success(monkeypatch):
    xml = "<FlexQueryResponse>ok</FlexQueryResponse>"
    monkeypatch.setattr(portfolio_sync.requests, "get", lambda *a, **k: _FakeFlexResponse(xml))
    result = portfolio_sync.fetch_flex_statement("tok", "ref", max_attempts=3, retry_delay_s=0)
    assert result == xml


def test_fetch_flex_statement_retries_while_in_progress_then_succeeds(monkeypatch):
    xml = "<FlexQueryResponse>done</FlexQueryResponse>"
    responses = [
        _FakeFlexResponse("Statement generation in progress. Please try again shortly."),
        _FakeFlexResponse("Statement generation in progress. Please try again shortly."),
        _FakeFlexResponse(xml),
    ]
    calls = {"n": 0}

    def fake_get(*a, **k):
        resp = responses[calls["n"]]
        calls["n"] += 1
        return resp

    monkeypatch.setattr(portfolio_sync.requests, "get", fake_get)
    result = portfolio_sync.fetch_flex_statement("tok", "ref", max_attempts=5, retry_delay_s=0)
    assert result == xml
    assert calls["n"] == 3


def test_fetch_flex_statement_raises_after_max_attempts_still_in_progress(monkeypatch):
    monkeypatch.setattr(
        portfolio_sync.requests, "get",
        lambda *a, **k: _FakeFlexResponse("Statement generation in progress."),
    )
    with pytest.raises(RuntimeError, match="toujours en préparation"):
        portfolio_sync.fetch_flex_statement("tok", "ref", max_attempts=3, retry_delay_s=0)


def test_fetch_flex_statement_raises_on_ibkr_error(monkeypatch):
    monkeypatch.setattr(
        portfolio_sync.requests, "get",
        lambda *a, **k: _FakeFlexResponse(
            '<FlexStatementResponse><ErrorCode>1019</ErrorCode>'
            '<ErrorMessage>Token has expired.</ErrorMessage></FlexStatementResponse>'
        ),
    )
    with pytest.raises(RuntimeError, match="Token has expired"):
        portfolio_sync.fetch_flex_statement("tok", "ref", max_attempts=3, retry_delay_s=0)


_SAMPLE_FLEX_XML = """<FlexQueryResponse queryName="Open Positions" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U1234567" fromDate="20260910" toDate="20260910">
      <OpenPositions>
        <OpenPosition symbol="MC" description="LVMH MOET HENNESSY LOUIS VUI" currency="EUR" position="10" markPrice="652.3" positionValue="6523.0" costBasisPrice="600.0" costBasisMoney="6000.0" fifoPnlUnrealized="523.0" side="Long"/>
        <OpenPosition symbol="AAPL" description="APPLE INC" currency="USD" position="5" markPrice="200.0" positionValue="1000.0" costBasisPrice="180.0" costBasisMoney="900.0" fifoPnlUnrealized="100.0" side="Long"/>
      </OpenPositions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""


def test_parse_open_positions_extracts_all_fields():
    result = portfolio_sync.parse_open_positions(_SAMPLE_FLEX_XML)
    assert len(result) == 2
    assert result[0] == {
        "ibkr_symbol": "MC",
        "description": "LVMH MOET HENNESSY LOUIS VUI",
        "currency": "EUR",
        "quantity": 10.0,
        "current_price": 652.3,
        "position_value": 6523.0,
        "cost_basis_price": 600.0,
        "cost_basis_value": 6000.0,
        "unrealized_pnl": 523.0,
    }
    assert result[1]["ibkr_symbol"] == "AAPL"
    assert result[1]["currency"] == "USD"


def test_parse_open_positions_empty_section_returns_empty_list():
    xml = (
        '<FlexQueryResponse><FlexStatements count="1">'
        '<FlexStatement><OpenPositions/></FlexStatement>'
        "</FlexStatements></FlexQueryResponse>"
    )
    assert portfolio_sync.parse_open_positions(xml) == []


def test_match_tickers_matches_cac40_by_stripping_yahoo_suffix():
    positions = [{
        "ibkr_symbol": "MC", "description": "LVMH", "currency": "EUR",
        "quantity": 10.0, "current_price": 652.3, "position_value": 6523.0,
        "cost_basis_price": 600.0, "cost_basis_value": 6000.0, "unrealized_pnl": 523.0,
    }]
    companies = [{"ticker": "MC.PA", "index": "CAC40"}]
    result = portfolio_sync.match_tickers(positions, companies)
    assert result[0]["matched_ticker"] == "MC.PA"


def test_match_tickers_matches_nasdaq_without_suffix():
    positions = [{
        "ibkr_symbol": "AAPL", "description": "APPLE", "currency": "USD",
        "quantity": 5.0, "current_price": 200.0, "position_value": 1000.0,
        "cost_basis_price": 180.0, "cost_basis_value": 900.0, "unrealized_pnl": 100.0,
    }]
    companies = [{"ticker": "AAPL", "index": "NASDAQ"}]
    result = portfolio_sync.match_tickers(positions, companies)
    assert result[0]["matched_ticker"] == "AAPL"


def test_match_tickers_is_case_insensitive():
    positions = [{
        "ibkr_symbol": "aapl", "description": "APPLE", "currency": "USD",
        "quantity": 5.0, "current_price": 200.0, "position_value": 1000.0,
        "cost_basis_price": 180.0, "cost_basis_value": 900.0, "unrealized_pnl": 100.0,
    }]
    companies = [{"ticker": "AAPL", "index": "NASDAQ"}]
    result = portfolio_sync.match_tickers(positions, companies)
    assert result[0]["matched_ticker"] == "AAPL"


def test_match_tickers_none_when_symbol_not_tracked():
    positions = [{
        "ibkr_symbol": "SPY", "description": "SPDR S&P 500 ETF", "currency": "USD",
        "quantity": 1.0, "current_price": 500.0, "position_value": 500.0,
        "cost_basis_price": 480.0, "cost_basis_value": 480.0, "unrealized_pnl": 20.0,
    }]
    companies = [{"ticker": "AAPL", "index": "NASDAQ"}]
    result = portfolio_sync.match_tickers(positions, companies)
    assert result[0]["matched_ticker"] is None


def test_match_tickers_preserves_all_original_fields():
    positions = [{
        "ibkr_symbol": "AAPL", "description": "APPLE", "currency": "USD",
        "quantity": 5.0, "current_price": 200.0, "position_value": 1000.0,
        "cost_basis_price": 180.0, "cost_basis_value": 900.0, "unrealized_pnl": 100.0,
    }]
    result = portfolio_sync.match_tickers(positions, [])
    assert result[0]["ibkr_symbol"] == "AAPL"
    assert result[0]["description"] == "APPLE"
    assert result[0]["matched_ticker"] is None


def test_match_tickers_returns_none_when_bare_symbol_is_ambiguous():
    positions = [{
        "ibkr_symbol": "MRK", "description": "MERCK", "currency": "USD",
        "quantity": 1.0, "current_price": 147.0, "position_value": 147.0,
        "cost_basis_price": 140.0, "cost_basis_value": 140.0, "unrealized_pnl": 7.0,
    }]
    companies = [
        {"ticker": "MRK.DE", "index": "DAX"},
        {"ticker": "MRK", "index": "DOW"},
    ]
    result = portfolio_sync.match_tickers(positions, companies)
    assert result[0]["matched_ticker"] is None


def test_main_writes_error_status_when_credentials_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("IBKR_FLEX_TOKEN", raising=False)
    monkeypatch.delenv("IBKR_FLEX_QUERY_ID", raising=False)
    output_path = tmp_path / "real_portfolio.json"
    monkeypatch.setattr(portfolio_sync, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    portfolio_sync.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["sync_status"] == "error"
    assert "IBKR_FLEX_TOKEN" in written["sync_error"]
    assert written["positions"] == []


def test_main_writes_ok_status_and_matched_positions_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "qid")
    output_path = tmp_path / "real_portfolio.json"
    indices_path = tmp_path / "indices.json"
    indices_path.write_text(
        json.dumps({"companies": [{"ticker": "MC.PA", "index": "CAC40"}]}), encoding="utf-8"
    )
    monkeypatch.setattr(portfolio_sync, "REAL_PORTFOLIO_JSON_PATH", str(output_path))
    monkeypatch.setattr(portfolio_sync, "INDICES_JSON_PATH", str(indices_path))
    monkeypatch.setattr(portfolio_sync, "fetch_flex_reference_code", lambda token, query_id: "ref123")
    monkeypatch.setattr(
        portfolio_sync, "fetch_flex_statement", lambda token, reference_code: _SAMPLE_FLEX_XML
    )

    portfolio_sync.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["sync_status"] == "ok"
    assert written["sync_error"] is None
    assert len(written["positions"]) == 2
    by_symbol = {p["ibkr_symbol"]: p for p in written["positions"]}
    assert by_symbol["MC"]["matched_ticker"] == "MC.PA"
    assert by_symbol["AAPL"]["matched_ticker"] is None  # pas dans indices.json de ce test


def test_main_keeps_previous_positions_and_sets_error_on_fetch_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "qid")
    output_path = tmp_path / "real_portfolio.json"
    output_path.write_text(
        json.dumps({
            "updated": "2026-09-09T07:00:00Z", "sync_status": "ok", "sync_error": None,
            "positions": [{"ibkr_symbol": "MC", "matched_ticker": "MC.PA"}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(portfolio_sync, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    def _raise(token, query_id):
        raise RuntimeError("Token has expired.")

    monkeypatch.setattr(portfolio_sync, "fetch_flex_reference_code", _raise)

    portfolio_sync.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["sync_status"] == "error"
    assert "Token has expired" in written["sync_error"]
    assert written["positions"] == [{"ibkr_symbol": "MC", "matched_ticker": "MC.PA"}]


def test_main_starts_from_empty_state_when_output_file_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("IBKR_FLEX_TOKEN", raising=False)
    output_path = tmp_path / "does_not_exist" / "real_portfolio.json"
    monkeypatch.setattr(portfolio_sync, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    portfolio_sync.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["positions"] == []


def test_main_sanitizes_request_exception_message_to_avoid_leaking_token(monkeypatch, tmp_path):
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "SECRET123")
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "qid")
    output_path = tmp_path / "real_portfolio.json"
    monkeypatch.setattr(portfolio_sync, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    def _raise(token, query_id):
        response = portfolio_sync.requests.Response()
        response.status_code = 429
        raise portfolio_sync.requests.exceptions.HTTPError(
            "429 Client Error: Too Many Requests for url: "
            "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest?t=SECRET123&q=qid",
            response=response,
        )

    monkeypatch.setattr(portfolio_sync, "fetch_flex_reference_code", _raise)

    portfolio_sync.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["sync_status"] == "error"
    assert "SECRET123" not in written["sync_error"]
    assert "429" in written["sync_error"]
