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
