from datetime import date, datetime, timezone

import pytest
import gold_bot.risk as risk


def test_compute_position_size_basic():
    # Risque 5% de 10000 = 500. Distance entrée->stop = 5. contract_size=100.
    # size = 500 / (5 * 100) = 1.0
    size = risk.compute_position_size(balance=10000, entry=2100, stop_loss=2095, contract_size=100)
    assert size == pytest.approx(1.0)


def test_compute_position_size_scales_with_risk_pct():
    size = risk.compute_position_size(balance=10000, entry=2100, stop_loss=2095, contract_size=100, risk_pct=0.10)
    assert size == pytest.approx(2.0)


def test_compute_position_size_raises_on_zero_distance():
    with pytest.raises(ValueError, match="nulle"):
        risk.compute_position_size(balance=10000, entry=2100, stop_loss=2100, contract_size=100)


def test_circuit_breaker_allows_when_under_threshold():
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)  # fixe le solde de départ du jour
    assert cb.can_open_position(9500) is True  # -5%, sous le seuil de -10%


def test_circuit_breaker_blocks_when_threshold_exceeded():
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    assert cb.can_open_position(8900) is False  # -11%, au-delà du seuil


def test_circuit_breaker_uses_fixed_starting_balance_not_current():
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    cb.can_open_position(9200)  # -8%, toujours autorisé
    # Un deuxième appel le même jour ne doit pas re-fixer le solde de
    # référence à 9200 — le seuil doit rester calculé contre 10000.
    assert cb.can_open_position(8900) is False  # -11% depuis 10000, pas depuis 9200


def test_circuit_breaker_blocks_at_exact_threshold():
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    assert cb.can_open_position(9000) is False  # perte exactement -10%, doit bloquer (comparaison stricte <)


def test_compute_position_size_raises_on_non_positive_balance():
    with pytest.raises(ValueError, match="solde"):
        risk.compute_position_size(balance=0, entry=2100, stop_loss=2095, contract_size=100)


def test_compute_position_size_raises_on_non_positive_contract_size():
    with pytest.raises(ValueError, match="contrat"):
        risk.compute_position_size(balance=10000, entry=2100, stop_loss=2095, contract_size=0)


def test_compute_position_size_raises_on_invalid_risk_pct():
    with pytest.raises(ValueError, match="risk_pct"):
        risk.compute_position_size(balance=10000, entry=2100, stop_loss=2095, contract_size=100, risk_pct=1.5)


def test_circuit_breaker_raises_on_invalid_threshold_pct():
    with pytest.raises(ValueError, match="threshold_pct"):
        risk.CircuitBreaker(threshold_pct=0)


def test_circuit_breaker_resets_on_new_day():
    clock = {"now": datetime(2026, 9, 10, 23, 0, tzinfo=timezone.utc)}
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: clock["now"])
    cb.check(10000)
    cb.can_open_position(8900)  # -11%, coupe-circuit déclenché le 10
    clock["now"] = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)  # jour suivant UTC
    # Le solde de référence doit être refixé au solde courant (8900) au
    # premier appel du nouveau jour, donc plus aucune perte accumulée.
    assert cb.can_open_position(8900) is True
