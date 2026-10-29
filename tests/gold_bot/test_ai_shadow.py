import json
from datetime import datetime, timedelta, timezone

import anthropic
import pytest

import gold_bot.ai_shadow as ai_shadow


@pytest.fixture(autouse=True)
def _reset_shadow_cooldown():
    """`ai_shadow._last_call_at` est un dict au niveau module (meme
    objet partage entre tous les tests de ce fichier) -- sans ce reset,
    un test qui appelle shadow_judge_entry("XAUUSD", ...) ferait sauter
    le cooldown du test suivant qui utilise le meme symbole."""
    ai_shadow._last_call_at.clear()
    yield
    ai_shadow._last_call_at.clear()


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

    def __call__(self, api_key, timeout):
        self.api_key = api_key
        self.timeout = timeout
        return self


def _candles():
    return [
        {"time": "2026-10-28 12:00:00", "open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0},
        {"time": "2026-10-28 12:05:00", "open": 2651.0, "high": 2655.0, "low": 2650.0, "close": 2654.0},
    ]


def _state():
    return {"level": 2650.5, "close": 2654.0, "high": 2655.0, "low": 2649.0, "atr": 1.2,
            "trend_strength": 0.03, "hour_utc": 12}


def test_shadow_judge_entry_returns_none_and_logs_when_api_key_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    log_path = str(tmp_path / "ai_shadow_log.jsonl")

    result = ai_shadow.shadow_judge_entry("XAUUSD", "achat", _state(), _candles(), log_path=log_path)

    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 1
    assert entries[0]["error"] == "ANTHROPIC_API_KEY absent"


def test_shadow_judge_entry_parses_verdict_and_logs_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    response_text = (
        "Rien de particulier trouve.\n"
        '{"verdict": "confirme", "confiance": "haute", "raison": "pas de news majeure"}'
    )
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_shadow.shadow_judge_entry("XAUUSD", "achat", _state(), _candles(), log_path=log_path)

    assert result == {"verdict": "confirme", "confiance": "haute", "raison": "pas de news majeure"}
    assert fake_client.messages.last_kwargs["model"] == ai_shadow.MODEL
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 1
    assert entries[0]["verdict"] == result
    assert "error" not in entries[0]


def test_shadow_judge_entry_returns_none_and_logs_when_verdict_unparseable(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock("reponse sans bloc JSON")]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_shadow.shadow_judge_entry("XAUUSD", "vente", _state(), _candles(), log_path=log_path)

    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["error"] == "verdict JSON non trouve ou invalide dans la reponse"
    assert entries[0]["raw_text"] == "reponse sans bloc JSON"


def test_shadow_judge_entry_never_raises_and_logs_on_client_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    fake_client = _FakeAnthropicClient(error=RuntimeError("boom reseau"))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_shadow.shadow_judge_entry("XAUUSD", "achat", _state(), _candles(), log_path=log_path)

    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["error"] == "RuntimeError: boom reseau"


def test_shadow_judge_entry_logs_symbol_direction_and_state(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    log_path = str(tmp_path / "ai_shadow_log.jsonl")

    ai_shadow.shadow_judge_entry("XAUUSD", "vente", _state(), _candles(), log_path=log_path)

    entry = json.loads(open(log_path, encoding="utf-8").readline())
    assert entry["symbol"] == "XAUUSD"
    assert entry["direction"] == "vente"
    assert entry["state"] == _state()
    assert "timestamp" in entry


def test_parse_verdict_returns_none_without_json_block():
    assert ai_shadow._parse_verdict("pas de JSON ici") is None


def test_parse_verdict_takes_last_json_block_when_several_present():
    text = (
        '{"verdict": "confirme", "confiance": "faible", "raison": "premier bloc, ignore"}\n'
        'Correction :\n'
        '{"verdict": "opposerait", "confiance": "haute", "raison": "dernier bloc"}'
    )
    assert ai_shadow._parse_verdict(text) == {
        "verdict": "opposerait", "confiance": "haute", "raison": "dernier bloc"
    }


def test_describe_candles_skips_malformed_entries():
    candles = [
        {"time": "2026-10-28 12:00:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        {"time": "2026-10-28 12:05:00"},  # champs manquants -> ignore
    ]
    description = ai_shadow._describe_candles(candles)
    assert "12:00:00" in description
    assert "12:05:00" not in description


def test_shadow_judge_entry_skips_second_call_within_cooldown(tmp_path, monkeypatch):
    """Incident reel du 2026-10-29 : sans ce garde-fou, un signal qui
    reste confirme cycle apres cycle declenchait un vrai appel facture
    toutes les 1-2 minutes pendant des heures (28 appels, 0 succes,
    cout reel sans valeur)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "confirme", "confiance": "haute", "raison": "ok"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 10, 29, 10, 0, 0, tzinfo=timezone.utc)

    first = ai_shadow.shadow_judge_entry("XAUUSD", "achat", _state(), _candles(), log_path=log_path, now=t0)
    second = ai_shadow.shadow_judge_entry(
        "XAUUSD", "achat", _state(), _candles(), log_path=log_path, now=t0 + timedelta(minutes=2)
    )

    assert first is not None
    assert second is None
    assert fake_client.messages.last_kwargs is not None
    # un seul appel reel a l'API malgre les deux invocations
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 1


def test_shadow_judge_entry_calls_again_after_cooldown_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "confirme", "confiance": "haute", "raison": "ok"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 10, 29, 10, 0, 0, tzinfo=timezone.utc)

    first = ai_shadow.shadow_judge_entry("XAUUSD", "achat", _state(), _candles(), log_path=log_path, now=t0)
    second = ai_shadow.shadow_judge_entry(
        "XAUUSD", "achat", _state(), _candles(), log_path=log_path,
        now=t0 + ai_shadow.SHADOW_COOLDOWN + timedelta(seconds=1),
    )

    assert first is not None
    assert second is not None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 2


def test_shadow_judge_entry_cooldown_is_per_symbol(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "confirme", "confiance": "haute", "raison": "ok"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 10, 29, 10, 0, 0, tzinfo=timezone.utc)

    xau = ai_shadow.shadow_judge_entry("XAUUSD", "achat", _state(), _candles(), log_path=log_path, now=t0)
    xag = ai_shadow.shadow_judge_entry(
        "XAGUSD", "achat", _state(), _candles(), log_path=log_path, now=t0 + timedelta(minutes=1)
    )

    assert xau is not None
    assert xag is not None
