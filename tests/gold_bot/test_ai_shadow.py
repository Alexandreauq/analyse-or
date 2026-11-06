import json
from datetime import datetime, timedelta, timezone

import anthropic
import pytest

import gold_bot.ai_shadow as ai_shadow


@pytest.fixture(autouse=True)
def _reset_shadow_cooldown():
    """`ai_shadow._last_call_at`/`_last_verdict` sont des dicts au niveau
    module (meme objet partage entre tous les tests de ce fichier) --
    sans ce reset, un test qui appelle shadow_judge_entry("XAUUSD", ...)
    ferait sauter le cooldown du test suivant qui utilise le meme
    symbole, ou lui ferait heriter un verdict en cache."""
    ai_shadow._last_call_at.clear()
    ai_shadow._last_verdict.clear()
    yield
    ai_shadow._last_call_at.clear()
    ai_shadow._last_verdict.clear()


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
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        return self


def _candles():
    return [
        {"time": "2026-10-28 12:00:00", "open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0},
        {"time": "2026-10-28 12:05:00", "open": 2651.0, "high": 2655.0, "low": 2650.0, "close": 2654.0},
    ]


def _state():
    return {"level": 2650.5, "close": 2654.0, "high": 2655.0, "low": 2649.0, "atr": 1.2,
            "trend_strength": 0.03, "hour_utc": 12}


def test_build_prompt_uses_engine_label_instead_of_hardcoded_fibonacci():
    prompt = ai_shadow._build_prompt("XAGUSD", "achat", _state(), _candles(), engine_label="test-engine-label", real_decision=False)
    assert "test-engine-label" in prompt
    assert "retracement de Fibonacci" not in prompt


def test_build_prompt_shadow_mode_says_no_real_trade_affected():
    prompt = ai_shadow._build_prompt("XAGUSD", "achat", _state(), _candles(), engine_label="x", real_decision=False)
    assert "n'affecte AUCUN trade reel" in prompt


def test_build_prompt_real_decision_mode_says_trade_will_be_blocked():
    prompt = ai_shadow._build_prompt("XAGUSD", "achat", _state(), _candles(), engine_label="x", real_decision=True)
    assert "n'affecte AUCUN trade reel" not in prompt
    assert "ne sera PAS execute" in prompt


def test_build_prompt_omits_trend_strength_line_when_none():
    state = dict(_state())
    state["trend_strength"] = None
    prompt = ai_shadow._build_prompt("XAGUSD", "achat", state, _candles(), engine_label="x", real_decision=False)
    assert "force de tendance" not in prompt


def test_shadow_judge_entry_returns_none_and_logs_when_api_key_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    log_path = str(tmp_path / "ai_shadow_log.jsonl")

    result = ai_shadow.shadow_judge_entry("XAUUSD", "achat", _state(), _candles(), engine_label="rebond de retracement de Fibonacci", log_path=log_path)

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

    result = ai_shadow.shadow_judge_entry("XAUUSD", "achat", _state(), _candles(), engine_label="rebond de retracement de Fibonacci", log_path=log_path)

    assert result == {"verdict": "confirme", "confiance": "haute", "raison": "pas de news majeure"}
    assert fake_client.messages.last_kwargs["model"] == ai_shadow.MODEL
    # max_retries=0 : un timeout sur cet appel (deja proteger par son
    # propre REQUEST_TIMEOUT_SECONDS) ne doit jamais etre rejoue par le
    # SDK -- incident du 2026-10-29, chaque timeout facturait jusqu'a
    # 3 tentatives reelles (defaut SDK = 2 retries) au lieu d'une.
    assert fake_client.max_retries == 0
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 1
    assert entries[0]["verdict"] == result
    assert "error" not in entries[0]


def test_shadow_judge_entry_returns_none_and_logs_when_verdict_unparseable(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock("reponse sans bloc JSON")]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_shadow.shadow_judge_entry("XAUUSD", "vente", _state(), _candles(), engine_label="rebond de retracement de Fibonacci", log_path=log_path)

    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["error"] == "verdict JSON non trouve ou invalide dans la reponse"
    assert entries[0]["raw_text"] == "reponse sans bloc JSON"


def test_shadow_judge_entry_never_raises_and_logs_on_client_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    fake_client = _FakeAnthropicClient(error=RuntimeError("boom reseau"))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_shadow.shadow_judge_entry("XAUUSD", "achat", _state(), _candles(), engine_label="rebond de retracement de Fibonacci", log_path=log_path)

    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["error"] == "RuntimeError: boom reseau"


def test_shadow_judge_entry_logs_symbol_direction_and_state(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    log_path = str(tmp_path / "ai_shadow_log.jsonl")

    ai_shadow.shadow_judge_entry(
        "XAUUSD", "vente", _state(), _candles(), engine_label="rebond de retracement de Fibonacci",
        real_decision=False, log_path=log_path,
    )

    entry = json.loads(open(log_path, encoding="utf-8").readline())
    assert entry["symbol"] == "XAUUSD"
    assert entry["direction"] == "vente"
    assert entry["state"] == _state()
    assert entry["engine_label"] == "rebond de retracement de Fibonacci"
    assert entry["real_decision"] is False
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
    cout reel sans valeur). Le second appel, saute par le cooldown,
    renvoie le MEME verdict que le premier (pas None) -- voir
    test_shadow_judge_entry_cooldown_reuses_last_verdict_not_fail_open
    pour la raison precise (defaut critique trouve en revue finale de
    branche, 2026-11-06)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "confirme", "confiance": "haute", "raison": "ok"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 10, 29, 10, 0, 0, tzinfo=timezone.utc)

    first = ai_shadow.shadow_judge_entry("XAUUSD", "achat", _state(), _candles(), engine_label="rebond de retracement de Fibonacci", log_path=log_path, now=t0)
    second = ai_shadow.shadow_judge_entry(
        "XAUUSD", "achat", _state(), _candles(), engine_label="rebond de retracement de Fibonacci", log_path=log_path, now=t0 + timedelta(minutes=2)
    )

    assert first is not None
    assert second == first
    assert fake_client.messages.last_kwargs is not None
    # un seul appel reel a l'API malgre les deux invocations
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 1


def test_shadow_judge_entry_cooldown_reuses_last_verdict_not_fail_open(tmp_path, monkeypatch):
    """Defaut CRITIQUE trouve en revue finale de branche (2026-11-06),
    reproduit en execution reelle avant ce correctif : un signal
    Ichimoku reste confirme toute l'heure de la bougie H1 (cycles
    toutes les 60s) -- au cycle 1, Claude repond "opposerait", le vrai
    veto bloque le trade ; au cycle 2 (1 minute plus tard), l'ancien
    code sautait l'appel reel (cooldown 24h) et renvoyait None, et le
    fail-open de decide_and_act_ichimoku laissait alors passer le MEME
    trade que le cycle precedent venait de vetoer -- le veto reel
    n'avait donc aucun effet pratique. Le second appel doit desormais
    renvoyer le verdict "opposerait" mis en cache, pas None."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "opposerait", "confiance": "haute", "raison": "actualite majeure en cours"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc)

    first = ai_shadow.shadow_judge_entry(
        "XAGUSD", "achat", _state(), _candles(), engine_label="croisement Tenkan/Kijun confirme par le nuage (Ichimoku)",
        real_decision=True, log_path=log_path, now=t0,
    )
    second = ai_shadow.shadow_judge_entry(
        "XAGUSD", "achat", _state(), _candles(), engine_label="croisement Tenkan/Kijun confirme par le nuage (Ichimoku)",
        real_decision=True, log_path=log_path, now=t0 + timedelta(minutes=1),
    )

    assert first == {"verdict": "opposerait", "confiance": "haute", "raison": "actualite majeure en cours"}
    assert second == first
    # toujours un seul appel reel facture malgre les deux invocations
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 1


def test_shadow_judge_entry_cooldown_does_not_leak_verdict_across_opposite_directions(tmp_path, monkeypatch):
    """Defaut IMPORTANT trouve en re-confirmation du correctif critique
    (meme jour, 2026-11-06) : la cle de cooldown/cache (symbol,
    engine_label) sans `direction` faisait qu'un "opposerait" sur un
    ACHAT bloquait aussi un signal de VENTE totalement different sur le
    meme symbole, dans la meme fenetre de 24h -- reproduit par le
    re-reviewer. `direction` fait maintenant partie de la cle : le
    second appel (direction opposee) doit declencher un VRAI nouvel
    appel API, pas reutiliser le verdict du premier."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "opposerait", "confiance": "haute", "raison": "actualite majeure en cours"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc)

    achat = ai_shadow.shadow_judge_entry(
        "XAGUSD", "achat", _state(), _candles(), engine_label="croisement Tenkan/Kijun confirme par le nuage (Ichimoku)",
        real_decision=True, log_path=log_path, now=t0,
    )
    vente = ai_shadow.shadow_judge_entry(
        "XAGUSD", "vente", _state(), _candles(), engine_label="croisement Tenkan/Kijun confirme par le nuage (Ichimoku)",
        real_decision=True, log_path=log_path, now=t0 + timedelta(minutes=1),
    )

    assert achat == {"verdict": "opposerait", "confiance": "haute", "raison": "actualite majeure en cours"}
    assert vente is not None  # vrai nouvel appel, pas un echec-ouvert
    # deux appels reels distincts, un par direction
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 2


def test_shadow_judge_entry_cooldown_does_not_reuse_verdict_from_failed_attempt(tmp_path, monkeypatch):
    """Si la tentative qui ouvre une nouvelle fenetre de cooldown
    echoue (verdict non parseable), les appels suivants pendant cette
    fenetre doivent rester en fail-open (None), pas rejouer un verdict
    d'une fenetre PRECEDENTE (ex. un "opposerait" d'hier qui resterait
    fige a tort si le vrai appel du jour timeout)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    t0 = datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc)

    ok_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "opposerait", "confiance": "haute", "raison": "ok"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", ok_client)
    ai_shadow.shadow_judge_entry(
        "XAGUSD", "achat", _state(), _candles(), engine_label="test-expiry", real_decision=True, log_path=log_path, now=t0,
    )

    # fenetre de cooldown expiree -- nouvelle tentative, cette fois sans
    # bloc JSON exploitable (verdict=None cote appelant).
    bad_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock("reponse sans JSON")]))
    monkeypatch.setattr(anthropic, "Anthropic", bad_client)
    failed = ai_shadow.shadow_judge_entry(
        "XAGUSD", "achat", _state(), _candles(), engine_label="test-expiry", real_decision=True, log_path=log_path,
        now=t0 + ai_shadow.SHADOW_COOLDOWN + timedelta(seconds=1),
    )
    skipped = ai_shadow.shadow_judge_entry(
        "XAGUSD", "achat", _state(), _candles(), engine_label="test-expiry", real_decision=True, log_path=log_path,
        now=t0 + ai_shadow.SHADOW_COOLDOWN + timedelta(seconds=2),
    )

    assert failed is None
    assert skipped is None


def test_shadow_judge_entry_calls_again_after_cooldown_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "confirme", "confiance": "haute", "raison": "ok"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 10, 29, 10, 0, 0, tzinfo=timezone.utc)

    first = ai_shadow.shadow_judge_entry("XAUUSD", "achat", _state(), _candles(), engine_label="rebond de retracement de Fibonacci", log_path=log_path, now=t0)
    second = ai_shadow.shadow_judge_entry(
        "XAUUSD", "achat", _state(), _candles(), engine_label="rebond de retracement de Fibonacci", log_path=log_path,
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

    xau = ai_shadow.shadow_judge_entry("XAUUSD", "achat", _state(), _candles(), engine_label="rebond de retracement de Fibonacci", log_path=log_path, now=t0)
    xag = ai_shadow.shadow_judge_entry(
        "XAGUSD", "achat", _state(), _candles(), engine_label="rebond de retracement de Fibonacci", log_path=log_path, now=t0 + timedelta(minutes=1)
    )

    assert xau is not None
    assert xag is not None


def test_shadow_judge_entry_cooldown_is_per_engine_label_too(tmp_path, monkeypatch):
    """Releve en re-review de la Task 3 : le cooldown etait auparavant
    cle uniquement sur `symbol`, partage entre tous les moteurs
    appelants -- un vrai veto (real_decision=True) sur un symbole
    pourrait echouer-ouvert (None) a tort si un appel fantome sur le
    MEME symbole avait deja consomme le cooldown, sans que ce soit
    visible dans les logs applicatifs. Deux moteurs distincts sur le
    meme symbole doivent avoir des cooldowns independants."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_shadow_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(
        '{"verdict": "confirme", "confiance": "haute", "raison": "ok"}'
    )]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 10, 29, 10, 0, 0, tzinfo=timezone.utc)

    fantome = ai_shadow.shadow_judge_entry(
        "XAUUSD", "achat", _state(), _candles(), engine_label="rebond de retracement de Fibonacci",
        real_decision=False, log_path=log_path, now=t0,
    )
    reel = ai_shadow.shadow_judge_entry(
        "XAUUSD", "achat", _state(), _candles(), engine_label="autre moteur (vrai veto)",
        real_decision=True, log_path=log_path, now=t0 + timedelta(minutes=1),
    )

    assert fantome is not None
    assert reel is not None
