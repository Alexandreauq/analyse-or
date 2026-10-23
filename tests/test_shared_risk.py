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
