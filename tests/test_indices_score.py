from indices_score import sector_risk_profile, score_rentabilite


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


def test_score_rentabilite_above_cost_of_capital_is_positive():
    result = score_rentabilite(roce=13.0, roe=15.0, cost_of_capital=8.0)
    assert result.name == "Rentabilité / création de valeur"
    assert result.weight == 0.30
    assert result.score == 10.0  # spread of +5pp caps the score at +10
    assert "13.0" in result.raw_value
    assert "8.0" in result.raw_value


def test_score_rentabilite_below_cost_of_capital_is_negative():
    result = score_rentabilite(roce=3.0, roe=2.0, cost_of_capital=8.0)
    assert result.score == -10.0  # spread of -5pp floors the score at -10


def test_score_rentabilite_equal_to_cost_of_capital_is_neutral():
    result = score_rentabilite(roce=8.0, roe=8.0, cost_of_capital=8.0)
    assert result.score == 0.0


def test_score_rentabilite_partial_spread_scales_linearly():
    result = score_rentabilite(roce=10.5, roe=11.0, cost_of_capital=8.0)
    assert result.score == 5.0  # +2.5pp spread / 5.0pp scale * 10 = 5.0


from indices_score import score_structure_financiere


def test_score_structure_financiere_comfortable_standard_profile():
    # net_debt_ebitda=0 -> +10 sub-score ; icr très élevé -> +10 sub-score
    result = score_structure_financiere(net_debt_ebitda=0.0, icr=10.0, sector="Industrials")
    assert result.name == "Structure financière / solvabilité"
    assert result.weight == 0.25
    assert result.score == 10.0


def test_score_structure_financiere_at_standard_thresholds_is_neutral():
    # pile au seuil confortable (3.0) et pile au seuil critique ICR (3.0) -> 0 des deux côtés
    result = score_structure_financiere(net_debt_ebitda=3.0, icr=3.0, sector="Industrials")
    assert result.score == 0.0


def test_score_structure_financiere_beyond_risky_threshold_floors_at_minus_ten():
    # net_debt_ebitda largement au-delà du seuil risqué (-10) ET icr nul (-10) -> combiné = -10.0
    result = score_structure_financiere(net_debt_ebitda=8.0, icr=0.0, sector="Industrials")
    assert result.score == -10.0


def test_score_structure_financiere_defensif_profile_more_tolerant():
    # même ratio de 4.0x : risqué en Standard (entre 3 et 5.5) mais mieux toléré en Défensif
    # (seuil confortable ajusté = 3.0 * 1.3 = 3.9, donc 4.0 est tout juste au-delà -> proche de 0)
    standard = score_structure_financiere(net_debt_ebitda=4.0, icr=3.0, sector="Industrials")
    defensif = score_structure_financiere(net_debt_ebitda=4.0, icr=3.0, sector="Healthcare")
    assert defensif.score > standard.score


def test_score_structure_financiere_cyclique_profile_stricter():
    cyclique = score_structure_financiere(net_debt_ebitda=2.5, icr=3.0, sector="Energy")
    standard = score_structure_financiere(net_debt_ebitda=2.5, icr=3.0, sector="Industrials")
    assert cyclique.score < standard.score
    assert "cyclique" in cyclique.raw_value


def test_score_structure_financiere_net_cash_position_leverage_capped():
    # Position de cash net (net_debt_ebitda négatif) : le sous-score de levier
    # ne doit jamais dépasser +10 avant d'être moyenné avec la couverture,
    # sinon le résultat est faussé (5.0 au lieu de ~1.67 dans ce cas précis).
    result = score_structure_financiere(net_debt_ebitda=-2.0, icr=1.0, sector="Industrials")
    assert result.score < 2.0
