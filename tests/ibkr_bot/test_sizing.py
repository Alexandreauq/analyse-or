import math

import pytest

import ibkr_bot.sizing as sizing


# --- helpers d'unite -------------------------------------------------

def test_is_pence_quoted_only_for_london_suffix():
    assert sizing.is_pence_quoted("III.L") is True
    assert sizing.is_pence_quoted("ABDN.L") is True
    assert sizing.is_pence_quoted("MC.PA") is False
    assert sizing.is_pence_quoted("ADBE") is False
    assert sizing.is_pence_quoted("ABBN.SW") is False


def test_quotation_currency_maps_gbp_to_pence_only_for_london():
    assert sizing.quotation_currency("GBP", "III.L") == "GBp"
    assert sizing.quotation_currency("EUR", "MC.PA") == "EUR"
    assert sizing.quotation_currency("USD", "ADBE") == "USD"
    assert sizing.quotation_currency("CHF", "ABBN.SW") == "CHF"


def test_to_quotation_price_multiplies_by_100_only_for_london():
    assert sizing.to_quotation_price(2.45, "III.L") == pytest.approx(245.0)
    assert sizing.to_quotation_price(415.0, "MC.PA") == 415.0
    assert sizing.to_quotation_price(50.0, "ADBE") == 50.0


def test_from_quotation_price_is_the_exact_inverse():
    assert sizing.from_quotation_price(245.0, "III.L") == pytest.approx(2.45)
    assert sizing.from_quotation_price(415.0, "MC.PA") == 415.0
    for price in (2.45, 26.6, 0.9412):
        round_tripped = sizing.from_quotation_price(
            sizing.to_quotation_price(price, "III.L"), "III.L")
        assert round_tripped == pytest.approx(price)


# --- arrondi entier et reliquat --------------------------------------

def test_compute_quantity_floors_and_leaves_the_remainder_in_cash():
    """500 EUR / 415 EUR = 1.204... -> 1 action, le reliquat (85 EUR)
    reste en cash, jamais reinvesti (voir spec 3.3)."""
    plan = sizing.compute_quantity("MC.PA", "EUR", 415.0, 1.0)

    assert plan["quantite"] == 1
    assert plan["devise_cotation"] == "EUR"
    assert plan["devise_compte"] == "EUR"
    assert plan["budget_converti"] == pytest.approx(500.0)
    assert plan["prix_unitaire_cotation"] == pytest.approx(415.0)
    assert plan["cout_estime_devise_compte"] == pytest.approx(415.0)
    assert plan["motif"] is None
    assert plan["ticker"] == "MC.PA"


def test_compute_quantity_floors_a_non_integer_ratio():
    plan = sizing.compute_quantity("BN.PA", "EUR", 120.0, 1.0)
    assert plan["quantite"] == 4  # 500 / 120 = 4.166...
    assert plan["cout_estime_devise_compte"] == pytest.approx(480.0)


def test_compute_quantity_on_an_exact_boundary_does_not_round_up():
    plan = sizing.compute_quantity("BN.PA", "EUR", 250.0, 1.0)
    assert plan["quantite"] == 2  # 500 / 250 = exactement 2


# --- conversion de devise --------------------------------------------

def test_compute_quantity_converts_to_usd():
    plan = sizing.compute_quantity("ADBE", "USD", 50.0, 1.08)
    assert plan["budget_converti"] == pytest.approx(540.0)
    assert plan["quantite"] == 10  # 540 / 50 = 10.8
    assert plan["devise_cotation"] == "USD"
    assert plan["taux_de_change"] == pytest.approx(1.08)
    assert plan["cout_estime_devise_compte"] == pytest.approx(500.0)


def test_compute_quantity_converts_to_chf():
    plan = sizing.compute_quantity("ABBN.SW", "CHF", 100.0, 0.94)
    assert plan["budget_converti"] == pytest.approx(470.0)
    assert plan["quantite"] == 4  # 470 / 100 = 4.7
    assert plan["devise_cotation"] == "CHF"
    assert plan["cout_estime_devise_compte"] == pytest.approx(400.0)


def test_compute_quantity_with_rate_one_for_eur_is_a_no_op_conversion():
    plan = sizing.compute_quantity("SAP.DE", "EUR", 100.0, 1.0)
    assert plan["budget_converti"] == pytest.approx(500.0)
    assert plan["quantite"] == 5


# --- GARDE-FOU PENCE / LIVRE (spec 4.7 point 1, spec 7) --------------

def test_compute_quantity_on_london_ticker_is_not_100x_too_large():
    """indices.json donne 2.45 GBP ; IBKR cote 245 GBp. Budget 500 EUR au
    taux 0.86 = 430 GBP = 43 000 GBp. 43 000 / 245 = 175.5 -> 175 actions.
    Le bug a 100x (budget en pence / prix en livres) donnerait 17 551 ;
    le bug inverse (budget en livres / prix en pence) donnerait 1."""
    plan = sizing.compute_quantity("III.L", "GBP", 2.45, 0.86)

    assert plan["quantite"] == 175
    assert plan["quantite"] != 17551
    assert plan["quantite"] != 1
    assert plan["devise_cotation"] == "GBp"
    assert plan["devise_compte"] == "GBP"
    assert plan["budget_converti"] == pytest.approx(43000.0)
    assert plan["prix_unitaire_cotation"] == pytest.approx(245.0)
    assert plan["cout_estime_devise_compte"] == pytest.approx(175 * 2.45)
    assert plan["motif"] is None


def test_compute_quantity_on_expensive_london_ticker_is_not_100x_too_large():
    """30 GBP = 3 000 GBp. 43 000 / 3 000 = 14.33 -> 14 actions.
    Le bug a 100x donnerait 1 433."""
    plan = sizing.compute_quantity("AZN.L", "GBP", 30.0, 0.86)

    assert plan["quantite"] == 14
    assert plan["quantite"] != 1433
    assert plan["cout_estime_devise_compte"] == pytest.approx(420.0)


def test_compute_quantity_on_london_ticker_above_budget_buys_nothing():
    """500 GBP = 50 000 GBp > 43 000 GBp de budget -> 0 action. Le bug a
    100x donnerait 86 actions, soit ~43 000 GBP engages au lieu de 430."""
    plan = sizing.compute_quantity("XPENSIVE.L", "GBP", 500.0, 0.86)

    assert plan["quantite"] == 0
    assert plan["quantite"] != 86
    assert plan["motif"] == "signal_ignore_prix_unitaire_superieur_au_budget"
    assert plan["cout_estime_devise_compte"] == 0.0


def test_compute_quantity_on_penny_london_ticker_stays_coherent():
    """Cours tres bas (0.9412 GBP = 94.12 GBp) : 43 000 / 94.12 = 456.86
    -> 456 actions."""
    plan = sizing.compute_quantity("LLOY.L", "GBP", 0.9412, 0.86)
    assert plan["quantite"] == 456


# --- cas 0 action et entrees invalides -------------------------------

def test_compute_quantity_returns_zero_and_a_reason_when_price_exceeds_budget():
    """Cas limite explicite de la spec 3.3 : aucun ordre n'est passe, la
    position ne s'ouvre pas, et le motif doit etre journalisable. Ne
    JAMAIS acheter 1 action 'au moins'."""
    plan = sizing.compute_quantity("MC.PA", "EUR", 600.0, 1.0)

    assert plan["quantite"] == 0
    assert plan["motif"] == "signal_ignore_prix_unitaire_superieur_au_budget"
    assert plan["prix_unitaire_cotation"] == pytest.approx(600.0)
    assert plan["budget_converti"] == pytest.approx(500.0)
    assert plan["cout_estime_devise_compte"] == 0.0


@pytest.mark.parametrize("price", [0.0, -3.0, None, float("nan")])
def test_compute_quantity_rejects_invalid_price(price):
    plan = sizing.compute_quantity("MC.PA", "EUR", price, 1.0)
    assert plan["quantite"] == 0
    assert plan["motif"] == "prix_ou_taux_invalide"


@pytest.mark.parametrize("rate", [0.0, -1.0, None, float("nan")])
def test_compute_quantity_rejects_invalid_fx_rate(rate):
    plan = sizing.compute_quantity("ADBE", "USD", 50.0, rate)
    assert plan["quantite"] == 0
    assert plan["motif"] == "prix_ou_taux_invalide"


def test_compute_quantity_rejects_non_positive_budget():
    plan = sizing.compute_quantity("MC.PA", "EUR", 50.0, 1.0, budget_eur=0.0)
    assert plan["quantite"] == 0
    assert plan["motif"] == "prix_ou_taux_invalide"


def test_compute_quantity_never_raises_on_garbage_input():
    plan = sizing.compute_quantity("MC.PA", "EUR", "pas un nombre", 1.0)
    assert plan["quantite"] == 0
    assert plan["motif"] == "prix_ou_taux_invalide"


def test_budget_constant_is_500_eur():
    assert sizing.BUDGET_EUR == 500.0
    assert math.isclose(sizing.PENCE_PER_POUND, 100.0)
