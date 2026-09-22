from datetime import date, datetime, timezone

import pytest
import gold_bot.risk as risk
import gold_bot.state as state


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


def test_compute_position_size_caps_notional_at_max_leverage():
    # Stop très serré (0.20$) + risk_pct élevé -> taille brute par le
    # risque : 1000 / (0.20*100) = 50 lots, notionnel 50*100*4300 =
    # 21 500 000$, soit 2150x le solde de 10000$ -- très au-delà du
    # levier max du compte (500x, voir MAX_LEVERAGE). Doit être
    # plafonné à exactement 500x : volume = (10000*500)/(100*4300).
    size = risk.compute_position_size(
        balance=10000, entry=4300, stop_loss=4299.80, contract_size=100, risk_pct=0.10)
    expected_capped = (10000 * risk.MAX_LEVERAGE) / (100 * 4300)
    assert size == pytest.approx(expected_capped)
    assert size < 50  # bien en dessous de la taille brute par le risque


def test_compute_position_size_not_capped_within_leverage_limit():
    # Cas de l'exemple de l'audit (~95x de levier) : sous le plafond de
    # 500x -> taille inchangée, purement dimensionnée par le risque.
    size = risk.compute_position_size(
        balance=10000, entry=3610, stop_loss=3606.20, contract_size=100, risk_pct=0.10)
    expected_uncapped = (10000 * 0.10) / (3.80 * 100)
    assert size == pytest.approx(expected_uncapped)
    notional = size * 100 * 3610
    assert notional / 10000 < risk.MAX_LEVERAGE


def test_compute_position_size_respects_custom_max_leverage():
    size = risk.compute_position_size(
        balance=10000, entry=4300, stop_loss=4299.80, contract_size=100,
        risk_pct=0.10, max_leverage=50)
    expected_capped = (10000 * 50) / (100 * 4300)
    assert size == pytest.approx(expected_capped)


def test_round_to_volume_step_rounds_down_to_nearest_step():
    # 1.037 lots, pas de 0.01 -> arrondi vers le bas à 1.03, jamais 1.04
    # (arrondir vers le haut dépasserait risk_pct*balance).
    result = risk.round_to_volume_step(1.037, volume_step=0.01, min_volume=0.01, max_volume=500)
    assert result == pytest.approx(1.03)


def test_round_to_volume_step_returns_none_below_minimum():
    # 0.004 lots arrondi à 0.00 au pas de 0.01 -> sous le minimum (0.01)
    # -> None (compte trop petit pour ce stop), pas une taille à zéro.
    result = risk.round_to_volume_step(0.004, volume_step=0.01, min_volume=0.01, max_volume=500)
    assert result is None


def test_round_to_volume_step_returns_none_when_exactly_below_minimum():
    result = risk.round_to_volume_step(0.009, volume_step=0.01, min_volume=0.01, max_volume=500)
    assert result is None


def test_round_to_volume_step_caps_at_broker_maximum():
    result = risk.round_to_volume_step(750.0, volume_step=0.01, min_volume=0.01, max_volume=500)
    assert result == pytest.approx(500)


def test_round_to_volume_step_accepts_exact_minimum():
    result = risk.round_to_volume_step(0.01, volume_step=0.01, min_volume=0.01, max_volume=500)
    assert result == pytest.approx(0.01)


def test_round_to_volume_step_raises_on_non_positive_step():
    with pytest.raises(ValueError, match="pas"):
        risk.round_to_volume_step(1.0, volume_step=0, min_volume=0.01, max_volume=500)


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


def test_circuit_breaker_persists_starting_balance_across_instances(tmp_path):
    path = str(tmp_path / "state.json")
    cb1 = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc), persist_path=path)
    cb1.check(10000)
    # Nouvelle instance (simule un redémarrage) le même jour UTC : doit
    # retrouver le solde de référence persisté, pas repartir du solde courant.
    cb2 = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, 18, tzinfo=timezone.utc), persist_path=path)
    assert cb2.can_open_position(8900) is False  # -11% depuis 10000, pas depuis 8900


def test_circuit_breaker_without_persist_path_stays_in_memory_only():
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    assert cb._persist_path is None


def test_circuit_breaker_resets_on_new_day():
    clock = {"now": datetime(2026, 9, 10, 23, 0, tzinfo=timezone.utc)}
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: clock["now"])
    cb.check(10000)
    cb.can_open_position(8900)  # -11%, coupe-circuit déclenché le 10
    clock["now"] = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)  # jour suivant UTC
    # Le solde de référence doit être refixé au solde courant (8900) au
    # premier appel du nouveau jour, donc plus aucune perte accumulée.
    assert cb.can_open_position(8900) is True


def test_circuit_breaker_ignores_corrupt_persisted_day(tmp_path):
    path = str(tmp_path / "state.json")
    state.save_state({"circuit_breaker_day": "not-a-date", "circuit_breaker_starting_balance": 10000}, path)
    cb = risk.CircuitBreaker(persist_path=path)  # ne doit pas lever
    assert cb._day is None


def test_circuit_breaker_stays_tripped_after_threshold_raised_mid_day():
    """Reproduit le scénario du finding #1 de la revue finale : le
    coupe-circuit se déclenche au profil 3 (threshold_pct=0.10), puis
    quelqu'un relève le profil à 5 (threshold_pct=0.175) le même jour —
    comme le fait gold_bot.loop.run_cycle en mutant threshold_pct en
    place sur l'instance partagée. Le coupe-circuit ne doit jamais se
    "dé-déclencher" silencieusement à cause de ce relèvement."""
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    assert cb.can_open_position(8800) is False  # -12%, dépasse le seuil du profil 3 (10%)
    cb.threshold_pct = 0.175  # relèvement au profil 5, même jour UTC
    assert cb.can_open_position(8800) is False  # -12% est SOUS 17.5% mais doit rester bloqué


def test_circuit_breaker_tripped_flag_resets_on_new_day_even_after_profile_raise():
    clock = {"now": datetime(2026, 9, 10, 23, 0, tzinfo=timezone.utc)}
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: clock["now"])
    cb.check(10000)
    assert cb.can_open_position(8800) is False  # déclenché le 10 au profil 3
    cb.threshold_pct = 0.175  # relevé au profil 5, toujours le 10
    assert cb.can_open_position(8800) is False  # reste bloqué le 10
    clock["now"] = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)  # jour suivant UTC
    # Nouveau jour : solde de référence refixé au solde courant (8800),
    # donc plus aucune perte accumulée — et le flag tripped doit avoir
    # été réinitialisé, pas être resté collé du jour précédent.
    assert cb.can_open_position(8800) is True


def test_circuit_breaker_tripped_flag_persists_across_instances(tmp_path):
    """Un redémarrage du process (nouvelle instance CircuitBreaker sur le
    même persist_path) en pleine journée, APRÈS un déclenchement et un
    relèvement de profil, ne doit pas silencieusement dé-déclencher le
    coupe-circuit — sinon un redémarrage de service ordinaire suffirait à
    contourner la protection."""
    path = str(tmp_path / "state.json")
    cb1 = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc), persist_path=path)
    cb1.check(10000)
    assert cb1.can_open_position(8800) is False  # déclenché au profil 3

    # Nouvelle instance (redémarrage), même jour UTC, DÉJÀ au profil 5
    # (threshold_pct plus large) : doit retrouver le flag déclenché
    # persisté, pas repartir à zéro.
    cb2 = risk.CircuitBreaker(threshold_pct=0.175, now_fn=lambda: datetime(2026, 9, 10, 18, tzinfo=timezone.utc), persist_path=path)
    assert cb2.can_open_position(8800) is False


def test_risk_profile_params_profile_1_is_most_conservative():
    assert risk.risk_profile_params(1) == {"risk_pct": 0.02, "threshold_pct": 0.05}


def test_risk_profile_params_profile_3_matches_current_defaults():
    assert risk.risk_profile_params(3) == {"risk_pct": 0.05, "threshold_pct": 0.10}


def test_risk_profile_params_profile_5_is_most_aggressive():
    assert risk.risk_profile_params(5) == {"risk_pct": 0.10, "threshold_pct": 0.175}


def test_risk_profile_params_all_five_profiles_present():
    assert set(risk.RISK_PROFILE_PARAMS.keys()) == {1, 2, 3, 4, 5}


def test_risk_profile_params_falls_back_to_default_when_out_of_range():
    assert risk.risk_profile_params(6) == risk.RISK_PROFILE_PARAMS[3]
    assert risk.risk_profile_params(0) == risk.RISK_PROFILE_PARAMS[3]
    assert risk.risk_profile_params(-1) == risk.RISK_PROFILE_PARAMS[3]


def test_risk_profile_params_falls_back_to_default_when_none():
    assert risk.risk_profile_params(None) == risk.RISK_PROFILE_PARAMS[3]


def test_risk_profile_params_falls_back_to_default_when_not_int():
    assert risk.risk_profile_params("3") == risk.RISK_PROFILE_PARAMS[3]
    assert risk.risk_profile_params(3.0) == risk.RISK_PROFILE_PARAMS[3]


def test_risk_profile_params_falls_back_to_default_for_bool():
    # bool est une sous-classe d'int en Python (True == 1, False == 0) —
    # doit être explicitement exclu pour ne jamais résoudre un booléen
    # mal formé vers un vrai profil numérique.
    assert risk.risk_profile_params(True) == risk.RISK_PROFILE_PARAMS[3]
    assert risk.risk_profile_params(False) == risk.RISK_PROFILE_PARAMS[3]


def test_resolve_risk_profile_returns_valid_profile_unchanged():
    assert risk.resolve_risk_profile(5) == 5


def test_resolve_risk_profile_falls_back_to_default_for_invalid_values():
    assert risk.resolve_risk_profile(9) == risk.DEFAULT_RISK_PROFILE
    assert risk.resolve_risk_profile("5") == risk.DEFAULT_RISK_PROFILE
    assert risk.resolve_risk_profile(5.0) == risk.DEFAULT_RISK_PROFILE
    assert risk.resolve_risk_profile(None) == risk.DEFAULT_RISK_PROFILE
    assert risk.resolve_risk_profile(True) == risk.DEFAULT_RISK_PROFILE
