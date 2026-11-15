from datetime import date

from compte_api.quota import consommer_quota, quota_restant, rendre_quota


def test_quota_consomme_puis_epuise(db, settings, membre):
    jour = date(2026, 11, 15)
    assert quota_restant(db, membre, settings, jour) == 10
    for _ in range(10):
        assert consommer_quota(db, membre, settings, jour)
    assert quota_restant(db, membre, settings, jour) == 0
    assert not consommer_quota(db, membre, settings, jour)


def test_quota_par_jour_et_par_membre(db, settings, membre, autre_membre):
    consommer_quota(db, membre, settings, date(2026, 11, 15))
    assert quota_restant(db, membre, settings, date(2026, 11, 16)) == 10
    assert quota_restant(db, autre_membre, settings, date(2026, 11, 15)) == 10


def test_rendre_quota(db, settings, membre):
    jour = date(2026, 11, 15)
    consommer_quota(db, membre, settings, jour)
    rendre_quota(db, membre, jour)
    assert quota_restant(db, membre, settings, jour) == 10


def test_admin_sans_quota(db, settings, creer_membre):
    admin = creer_membre(role="admin")
    assert quota_restant(db, admin, settings) is None
    assert all(consommer_quota(db, admin, settings) for _ in range(20))
