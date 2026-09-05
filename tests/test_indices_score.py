from indices_score import sector_risk_profile


def test_sector_risk_profile_defensif():
    assert sector_risk_profile("Healthcare") == "defensif"
    assert sector_risk_profile("Utilities") == "defensif"
    assert sector_risk_profile("Consumer Defensive") == "defensif"
    assert sector_risk_profile("Real Estate") == "defensif"


def test_sector_risk_profile_standard():
    assert sector_risk_profile("Industrials") == "standard"
    assert sector_risk_profile("Communication Services") == "standard"


def test_sector_risk_profile_cyclique():
    assert sector_risk_profile("Energy") == "cyclique"
    assert sector_risk_profile("Consumer Cyclical") == "cyclique"


def test_sector_risk_profile_defaults_to_standard_when_unknown():
    assert sector_risk_profile("Some Unmapped Sector") == "standard"
    assert sector_risk_profile(None) == "standard"
