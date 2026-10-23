import json
from datetime import datetime, timezone

import shared_risk


def test_can_open_position_blocks_once_loss_exceeds_threshold():
    cb = shared_risk.CircuitBreaker(
        threshold_pct=0.10, now_fn=lambda: datetime(2026, 10, 23, tzinfo=timezone.utc))
    assert cb.can_open_position(10_000.0) is True   # fixe la reference a 10000
    assert cb.can_open_position(8_900.0) is False   # perte de 11% > seuil de 10%


def test_module_has_no_dependency_on_either_bot_package():
    """Garde-fou architectural (spec : ni gold_bot ni ibkr_bot ne doit
    etre importe par ce module partage) -- shared_risk.py definit ses
    propres _load_state/_save_state plutot que d'importer
    gold_bot.state/ibkr_bot.state (voir le commentaire de tete du
    module)."""
    assert not hasattr(shared_risk, "state")
    assert "gold_bot" not in shared_risk.__file__ and "ibkr_bot" not in shared_risk.__file__


def test_can_open_position_daily_passes_on_the_very_first_call():
    """Demarrage a froid : rien a comparer encore, le coupe-circuit ne
    doit jamais bloquer un premier appel sans historique."""
    cb = shared_risk.CircuitBreaker(threshold_pct=0.10)
    assert cb.can_open_position_daily(10_000.0) is True


def test_can_open_position_daily_trips_on_a_drop_since_the_previous_call(tmp_path):
    path = tmp_path / "cb.json"
    cb1 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    assert cb1.can_open_position_daily(10_000.0) is True   # fixe la reference a 10000

    # Nouveau process le lendemain -- nouvelle instance, meme fichier.
    cb2 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    assert cb2.can_open_position_daily(8_900.0) is False   # perte de 11% > seuil de 10%


def test_can_open_position_daily_passes_on_a_drop_under_the_threshold(tmp_path):
    path = tmp_path / "cb.json"
    cb1 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    cb1.can_open_position_daily(10_000.0)

    cb2 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    assert cb2.can_open_position_daily(9_200.0) is True    # perte de 8% < seuil de 10%


def test_can_open_position_daily_advances_the_reference_each_call(tmp_path):
    """Le 3e appel compare au 2e, pas au 1er -- une baisse lente et
    reguliere (moins de 10%/jour) ne doit jamais s'accumuler en un
    declenchement a retardement."""
    path = tmp_path / "cb.json"
    cb1 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    cb1.can_open_position_daily(10_000.0)
    cb2 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    assert cb2.can_open_position_daily(9_500.0) is True    # -5%, ok
    cb3 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    assert cb3.can_open_position_daily(9_100.0) is True    # -4.2% vs 9500, pas vs 10000


def test_can_open_position_daily_never_touches_check_fields(tmp_path):
    """can_open_position_daily ne doit jamais interferer avec
    check()/can_open_position() (champs dedies _last_balance, distincts
    de _day/_starting_balance/_tripped_today)."""
    path = tmp_path / "cb.json"
    cb = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    cb.can_open_position_daily(10_000.0)
    assert cb._day is None
    assert cb._starting_balance is None
    assert cb._tripped_today is False


def test_can_open_position_daily_does_not_crash_without_a_day_ever_set(tmp_path):
    """_persist() est maintenant aussi appelee par can_open_position_daily,
    qui ne fixe jamais _day -- garde-fou ajoute a la Tache 1 (_persist
    doit tolerer _day=None)."""
    path = tmp_path / "cb.json"
    cb = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path))
    cb.can_open_position_daily(10_000.0)  # ne doit pas lever
    saved = json.loads(path.read_text())
    assert saved["circuit_breaker_day"] is None
