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
    declenchement a retardement. now_fn distinct par instance pour
    simuler trois JOURS CALENDAIRES reellement differents -- depuis le
    Fix 3 (verrou intra-jour, revue finale de branche), un rappel sur le
    MEME jour calendaire comparerait au contraire contre la reference
    GELEE du premier appel de ce jour (voir les tests dedies au verrou
    plus bas), donc ce test doit avancer explicitement le jour simule
    pour continuer a exercer l'avancement JOUR A JOUR qu'il decrit."""
    path = tmp_path / "cb.json"
    jour1 = lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
    jour2 = lambda: datetime(2026, 1, 2, tzinfo=timezone.utc)
    jour3 = lambda: datetime(2026, 1, 3, tzinfo=timezone.utc)
    cb1 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path), now_fn=jour1)
    cb1.can_open_position_daily(10_000.0)
    cb2 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path), now_fn=jour2)
    assert cb2.can_open_position_daily(9_500.0) is True    # -5%, ok
    cb3 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path), now_fn=jour3)
    assert cb3.can_open_position_daily(9_100.0) is True    # -4.2% vs 9500, pas vs 10000


# --- verrou intra-jour (Fix 3, revue finale de branche) --------------

def test_can_open_position_daily_stays_tripped_on_a_same_day_rerun(tmp_path):
    """Un batch declenche le coupe-circuit sur une vraie perte, puis est
    relance manuellement PLUS TARD LE MEME JOUR CALENDAIRE (procedure de
    redemarrage documentee dans deploy/README-ibkr.md) : le second appel
    doit rester bloque, pas se comparer a la reference deja abaissee par
    le premier appel de ce meme jour."""
    path = tmp_path / "cb.json"
    jour0 = lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
    jour1 = lambda: datetime(2026, 1, 2, tzinfo=timezone.utc)
    cb0 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path), now_fn=jour0)
    cb0.can_open_position_daily(10_000.0)   # fixe la reference a 10000

    cb1 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path), now_fn=jour1)
    assert cb1.can_open_position_daily(8_500.0) is False   # -15% -> declenche

    # Relance manuelle, MEME jour calendaire (meme now_fn jour1), equite
    # inchangee -- sans verrou, se comparerait a 8500 (le resultat du
    # premier appel de ce jour) et repasserait a tort.
    cb1_rerun = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path), now_fn=jour1)
    assert cb1_rerun.can_open_position_daily(8_500.0) is False


def test_can_open_position_daily_stays_open_on_a_same_day_rerun_without_a_trip(tmp_path):
    """Symetrique du test ci-dessus : si le premier appel du jour n'a PAS
    declenche le coupe-circuit, un rappel le meme jour calendaire (meme
    equite, ou une equite encore en baisse mais sous le seuil compare a
    la reference du matin) ne doit pas non plus se bloquer a tort."""
    path = tmp_path / "cb.json"
    jour0 = lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
    jour1 = lambda: datetime(2026, 1, 2, tzinfo=timezone.utc)
    cb0 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path), now_fn=jour0)
    cb0.can_open_position_daily(10_000.0)   # fixe la reference a 10000

    cb1 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path), now_fn=jour1)
    assert cb1.can_open_position_daily(9_700.0) is True   # -3%, ok

    # Relance manuelle, MEME jour calendaire : doit comparer a la
    # reference GELEE du matin (10000), pas a 9700 (le resultat du
    # premier appel de ce jour, qui a deja avance _last_balance).
    cb1_rerun = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path), now_fn=jour1)
    assert cb1_rerun.can_open_position_daily(9_200.0) is True   # -8% vs 10000, toujours < seuil


def test_can_open_position_daily_rebaselines_correctly_on_a_genuine_new_day_after_a_trip(tmp_path):
    """Apres un declenchement le jour J (equite tombee de 10000 a 8500),
    le jour J+1 doit repartir d'une reference NORMALE -- la derniere
    equite observee le jour J (8500), pas la reference gelee du matin de
    J (10000) ni un verrou qui survivrait au changement de jour."""
    path = tmp_path / "cb.json"
    jourJ = lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
    jourJ1 = lambda: datetime(2026, 1, 2, tzinfo=timezone.utc)
    cb0 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path),
                                     now_fn=lambda: datetime(2025, 12, 31, tzinfo=timezone.utc))
    cb0.can_open_position_daily(10_000.0)

    cbJ = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path), now_fn=jourJ)
    assert cbJ.can_open_position_daily(8_500.0) is False   # -15% -> declenche le jour J

    # Jour J+1 : repart de 8500 (dernier solde observe le jour J), pas de
    # 10000 (reference gelee du matin de J). Une equite de 9000 le jour
    # J+1 represente un GAIN (+500) vs 8500 -> jamais tripped.
    cbJ1 = shared_risk.CircuitBreaker(threshold_pct=0.10, persist_path=str(path), now_fn=jourJ1)
    assert cbJ1.can_open_position_daily(9_000.0) is True


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
