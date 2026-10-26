from datetime import datetime, timezone

import pytest

import gold_bot.ftmo_guard as ftmo_guard


def _dt(y, m, d, h=12, minute=0):
    return datetime(y, m, d, h, minute, tzinfo=timezone.utc)


def test_allows_when_equity_above_both_floors():
    guard = ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _dt(2026, 11, 1))
    assert guard.check(balance=10000, equity=9900) is None


def test_daily_floor_is_four_percent_below_balance_at_rebaseline_by_default():
    guard = ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _dt(2026, 11, 1))
    guard.check(balance=10000, equity=10000)  # fixe le plancher quotidien à 9600
    assert guard.check(balance=10000, equity=9601) is None
    assert guard.check(balance=10000, equity=9600) == "daily"


def test_overall_floor_is_nine_percent_below_isc_by_default():
    guard = ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _dt(2026, 11, 1))
    assert guard.overall_floor == pytest.approx(9100)
    # daily_loss_pct=0.50 pour isoler le plancher global : avec les 4%
    # quotidiens par défaut, le plancher quotidien (9600) est plus haut
    # que le plancher global (9100) et déclencherait "daily" en premier
    # -- voir test_overall_breach_reported_in_priority_over_daily_breach
    # pour le cas où les deux planchers sont en jeu simultanément.
    guard = ftmo_guard.FtmoGuard(
        isc=10000, daily_loss_pct=0.50, now_fn=lambda: _dt(2026, 11, 1))
    guard.check(balance=10000, equity=10000)
    assert guard.check(balance=10000, equity=9101) is None
    assert guard.check(balance=10000, equity=9100) == "overall"


def test_overall_floor_never_rebaselines_across_days():
    guard = ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _dt(2026, 11, 1))
    guard.check(balance=10000, equity=10000)
    guard._now_fn = lambda: _dt(2026, 11, 3)
    # Le solde a grimpé à 20000 entre-temps -- un plancher global qui se
    # rebaserait sur ce nouveau solde laisserait passer une perte totale
    # bien supérieure à overall_loss_pct de l'ISC réel (10000).
    guard.check(balance=20000, equity=20000)
    assert guard.overall_floor == pytest.approx(9100)


def test_daily_floor_rebaselines_on_cet_day_change():
    guard = ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _dt(2026, 11, 1))
    guard.check(balance=10000, equity=9700)  # plancher jour 1 : 9600, pas déclenché
    guard._now_fn = lambda: _dt(2026, 11, 2)
    # Nouveau jour CET : plancher rebasé sur le solde constaté maintenant (9700) -> 9312.
    assert guard.check(balance=9700, equity=9400) is None
    assert guard.check(balance=9700, equity=9312) == "daily"


def test_cet_day_boundary_uses_cet_not_utc():
    # 23:30 UTC le 1er novembre = 00:30 CET le 2 novembre -- doit déjà
    # être traité comme le jour suivant côté plancher quotidien.
    guard = ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _dt(2026, 11, 1, 23, 30))
    guard.check(balance=10000, equity=10000)  # plancher jour "2 nov CET" : 9600
    guard._now_fn = lambda: _dt(2026, 11, 1, 23, 45)  # toujours 1er nov UTC, même jour CET (02-nov)
    # Pas de rebaselining : toujours le même jour CET -> solde different (9999) ignoré pour le plancher.
    assert guard.check(balance=9999, equity=9600) == "daily"


def test_stays_tripped_for_rest_of_cet_day_even_if_equity_recovers():
    guard = ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _dt(2026, 11, 1))
    guard.check(balance=10000, equity=10000)  # plancher 9600
    assert guard.check(balance=9500, equity=9500) == "daily"  # déclenche
    # L'équité remonte nettement au-dessus du plancher le même jour CET --
    # doit rester déclenché, même raison.
    assert guard.check(balance=9500, equity=9900) == "daily"


def test_tripped_flag_resets_on_next_cet_day():
    guard = ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _dt(2026, 11, 1))
    guard.check(balance=10000, equity=10000)
    guard.check(balance=9500, equity=9500)  # déclenche "daily"
    guard._now_fn = lambda: _dt(2026, 11, 2)
    assert guard.check(balance=9500, equity=9500) is None  # nouveau jour, nouveau plancher (9120), pas déclenché


def test_overall_breach_reported_in_priority_over_daily_breach():
    # ISC bas + daily_loss_pct large pour que le plancher quotidien soit
    # SOUS le plancher global -- une équité qui franchit les deux
    # simultanément doit rapporter "overall", pas "daily".
    guard = ftmo_guard.FtmoGuard(
        isc=10000, daily_loss_pct=0.50, overall_loss_pct=0.09, now_fn=lambda: _dt(2026, 11, 1))
    guard.check(balance=10000, equity=10000)  # plancher quotidien : 5000, plancher global : 9100
    assert guard.check(balance=10000, equity=5000) == "overall"


def test_state_persists_and_reloads_across_restart(tmp_path):
    path = str(tmp_path / "ftmo_guard_state.json")
    guard1 = ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _dt(2026, 11, 1), persist_path=path)
    guard1.check(balance=10000, equity=9500)  # déclenche "daily" (plancher 9600), persiste

    # Nouvelle instance ("redémarrage du process") avec le même persist_path,
    # même jour CET -- doit retrouver le plancher ET le verdict figé sans
    # repartir du solde courant.
    guard2 = ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _dt(2026, 11, 1), persist_path=path)
    assert guard2.check(balance=10000, equity=9999) == "daily"


def test_fresh_persist_path_with_no_prior_state_starts_clean(tmp_path):
    path = str(tmp_path / "does_not_exist_yet.json")
    guard = ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _dt(2026, 11, 1), persist_path=path)
    assert guard.check(balance=10000, equity=9900) is None


def test_custom_thresholds_are_respected():
    guard = ftmo_guard.FtmoGuard(
        isc=10000, daily_loss_pct=0.05, overall_loss_pct=0.10, now_fn=lambda: _dt(2026, 11, 1))
    assert guard.overall_floor == pytest.approx(9000)
    guard.check(balance=10000, equity=10000)  # plancher quotidien (5%) : 9500
    assert guard.check(balance=10000, equity=9501) is None
    assert guard.check(balance=10000, equity=9000) == "overall"


def test_rejects_non_positive_isc():
    with pytest.raises(ValueError, match="isc"):
        ftmo_guard.FtmoGuard(isc=0)


def test_rejects_invalid_daily_loss_pct():
    with pytest.raises(ValueError, match="daily_loss_pct"):
        ftmo_guard.FtmoGuard(isc=10000, daily_loss_pct=0)


def test_rejects_invalid_overall_loss_pct():
    with pytest.raises(ValueError, match="overall_loss_pct"):
        ftmo_guard.FtmoGuard(isc=10000, overall_loss_pct=1.5)
