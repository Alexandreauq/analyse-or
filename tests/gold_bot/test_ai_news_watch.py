import json
from datetime import datetime, timedelta, timezone

import anthropic
import pytest

import gold_bot.ai_news_watch as ai_news_watch


@pytest.fixture(autouse=True)
def _reset_watch_cooldown():
    """Meme raison que le fixture equivalent de test_ai_shadow.py :
    _last_call_at/_last_verdict sont des dicts au niveau module,
    partages entre tous les tests de ce fichier."""
    ai_news_watch._last_call_at.clear()
    ai_news_watch._last_verdict.clear()
    yield
    ai_news_watch._last_call_at.clear()
    ai_news_watch._last_verdict.clear()


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response=None, error=None):
        self.messages = _FakeMessages(response=response, error=error)

    def __call__(self, api_key, timeout, max_retries=None):
        return self


def _candles():
    return [
        {"time": "2026-11-06 13:45:00", "open": 4056.68, "high": 4058.40, "low": 4025.09, "close": 4049.66},
        {"time": "2026-11-06 13:50:00", "open": 4049.57, "high": 4054.94, "low": 4044.59, "close": 4052.85},
    ]


def test_judge_post_news_calm_returns_none_and_logs_when_api_key_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    result = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path)
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 1
    assert entries[0]["error"] == "ANTHROPIC_API_KEY absent"


def test_judge_post_news_calm_parses_verdict_and_logs_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "encore_agite", "confiance": "haute", "raison": "le marche digere encore le NFP"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    result = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path)
    assert result == {"verdict": "encore_agite", "confiance": "haute", "raison": "le marche digere encore le NFP"}
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["symbol"] == "XAUUSD"
    assert entries[0]["minutes_since_blackout"] == 10.0
    assert entries[0]["verdict"] == result


def test_judge_post_news_calm_returns_none_and_logs_when_verdict_unparseable(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock("reponse sans JSON exploitable")]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    result = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path)
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["error"] == "verdict JSON non trouve ou invalide dans la reponse"


def test_judge_post_news_calm_never_raises_and_logs_on_client_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    fake_client = _FakeAnthropicClient(error=TimeoutError("boom"))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    result = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path)
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert "TimeoutError" in entries[0]["error"]


def test_judge_post_news_calm_skips_second_call_within_cooldown_reusing_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "encore_agite", "confiance": "haute", "raison": "test"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 11, 6, 13, 55, 0, tzinfo=timezone.utc)
    first = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path, now=t0)
    second = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 11.0, log_path=log_path, now=t0 + timedelta(minutes=1))
    assert first is not None
    assert second == first
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 1  # un seul appel reel malgre les deux invocations


def test_judge_post_news_calm_calls_again_after_cooldown_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "calme", "confiance": "haute", "raison": "test"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 11, 6, 13, 55, 0, tzinfo=timezone.utc)
    first = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path, now=t0)
    second = ai_news_watch.judge_post_news_calm(
        "XAUUSD", _candles(), 10.0, log_path=log_path, now=t0 + ai_news_watch.WATCH_COOLDOWN + timedelta(seconds=1),
    )
    assert first is not None and second is not None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 2


def test_judge_post_news_calm_cooldown_is_per_symbol(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_news_watch_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "calme", "confiance": "haute", "raison": "test"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 11, 6, 13, 55, 0, tzinfo=timezone.utc)
    xau = ai_news_watch.judge_post_news_calm("XAUUSD", _candles(), 10.0, log_path=log_path, now=t0)
    xag = ai_news_watch.judge_post_news_calm("XAGUSD", _candles(), 10.0, log_path=log_path, now=t0 + timedelta(minutes=1))
    assert xau is not None and xag is not None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 2
