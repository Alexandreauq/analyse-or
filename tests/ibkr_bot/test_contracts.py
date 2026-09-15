import json

import ibkr_bot.contracts as contracts


def _search_fn(reponses):
    """Faux gateway.search_contract : dict symbole -> liste de candidats.
    Compte ses appels pour verifier l'efficacite du cache."""
    appels = []

    def search(symbol):
        appels.append(symbol)
        return reponses.get(symbol, [])

    search.appels = appels
    return search


def _info_fn(reponses):
    """Faux gateway.contract_info : dict conid (str) -> details."""
    appels = []

    def info(conid):
        appels.append(str(conid))
        return reponses.get(str(conid), {})

    info.appels = appels
    return info


def _candidat(conid, symbol, description, sec_types=("STK",)):
    return {
        "conid": str(conid),
        "companyName": f"{symbol} SA",
        "symbol": symbol,
        "description": description,
        "sections": [{"secType": t} for t in sec_types],
    }


# --- decoupage du ticker ---------------------------------------------

def test_split_ticker_separates_known_suffixes():
    assert contracts.split_ticker("MC.PA") == ("MC", ".PA")
    assert contracts.split_ticker("SAP.DE") == ("SAP", ".DE")
    assert contracts.split_ticker("ABBN.SW") == ("ABBN", ".SW")
    assert contracts.split_ticker("III.L") == ("III", ".L")
    assert contracts.split_ticker("A2A.MI") == ("A2A", ".MI")
    assert contracts.split_ticker("ANA.MC") == ("ANA", ".MC")


def test_split_ticker_treats_us_tickers_as_suffixless():
    assert contracts.split_ticker("ADBE") == ("ADBE", "")


def test_split_ticker_does_not_strip_an_unknown_dotted_suffix():
    """BRK.B est un ticker americain a point, pas un suffixe de place."""
    assert contracts.split_ticker("BRK.B") == ("BRK.B", "")


def test_expected_venue_covers_every_suffix_of_the_v1_perimeter():
    for suffixe in (".PA", ".DE", ".MI", ".MC", ".L", ".SW", ""):
        venue = contracts.expected_venue("X" + suffixe)
        assert venue is not None, suffixe
        assert venue["exchanges"], suffixe
        assert venue["currencies"], suffixe


def test_expected_venue_maps_london_to_lse_and_accepts_both_gbp_spellings():
    venue = contracts.expected_venue("III.L")
    assert venue["exchanges"] == ("LSE",)
    assert set(venue["currencies"]) == {"GBP", "GBp"}


def test_expected_venue_is_none_for_an_out_of_scope_suffix():
    assert contracts.expected_venue("7203.T") is None
    assert contracts.expected_venue("0005.HK") is None


# --- resolution ------------------------------------------------------

def test_resolve_conid_accepts_a_single_matching_contract():
    search = _search_fn({"MC": [_candidat(4901, "MC", "SBF")]})
    info = _info_fn({"4901": {"conid": 4901, "symbol": "MC", "currency": "EUR",
                              "listingExchange": "SBF"}})
    cache = {}

    result = contracts.resolve_conid("MC.PA", search, info, cache, today="2026-09-14")

    assert result["conid"] == 4901
    assert result["exchange"] == "SBF"
    assert result["currency"] == "EUR"
    assert result["motif"] is None
    assert result["ticker"] == "MC.PA"


def test_resolve_conid_accepts_a_london_contract_quoted_in_gbp():
    search = _search_fn({"III": [_candidat(8675, "III", "LSE")]})
    info = _info_fn({"8675": {"conid": 8675, "symbol": "III", "currency": "GBP",
                              "listingExchange": "LSE"}})

    result = contracts.resolve_conid("III.L", search, info, {}, today="2026-09-14")

    assert result["conid"] == 8675
    assert result["currency"] == "GBP"
    assert result["motif"] is None


def test_resolve_conid_accepts_a_london_contract_reported_as_gbp_pence():
    search = _search_fn({"ABDN": [_candidat(9001, "ABDN", "LSE")]})
    info = _info_fn({"9001": {"conid": 9001, "symbol": "ABDN", "currency": "GBp",
                              "listingExchange": "LSE"}})

    result = contracts.resolve_conid("ABDN.L", search, info, {}, today="2026-09-14")

    assert result["conid"] == 9001
    assert result["motif"] is None


def test_resolve_conid_accepts_a_us_contract_on_any_us_venue():
    search = _search_fn({"ADBE": [_candidat(265768, "ADBE", "NASDAQ")]})
    info = _info_fn({"265768": {"conid": 265768, "symbol": "ADBE",
                                "currency": "USD", "listingExchange": "NASDAQ"}})

    result = contracts.resolve_conid("ADBE", search, info, {}, today="2026-09-14")

    assert result["conid"] == 265768
    assert result["exchange"] == "NASDAQ"


def test_resolve_conid_refuses_when_no_candidate_is_on_the_expected_exchange():
    """Cotation secondaire / ADR : le symbole existe, mais pas sur la
    bourse attendue -> on refuse plutot que d'acheter un autre
    instrument (spec 4.7 point 2)."""
    search = _search_fn({"MC": [_candidat(999, "MC", "SWB"),
                                _candidat(998, "MC", "FWB")]})
    info = _info_fn({})

    result = contracts.resolve_conid("MC.PA", search, info, {}, today="2026-09-14")

    assert result["conid"] is None
    assert result["motif"] == "contrat_non_resolu"
    assert "aucun candidat" in result["detail"]


def test_resolve_conid_refuses_when_the_currency_does_not_match():
    search = _search_fn({"MC": [_candidat(4901, "MC", "SBF")]})
    info = _info_fn({"4901": {"conid": 4901, "symbol": "MC", "currency": "USD",
                              "listingExchange": "SBF"}})

    result = contracts.resolve_conid("MC.PA", search, info, {}, today="2026-09-14")

    assert result["conid"] is None
    assert result["motif"] == "contrat_non_resolu"
    assert "aucun candidat" in result["detail"]


def test_resolve_conid_refuses_an_ambiguous_result_instead_of_guessing():
    """Deux contrats survivent aux deux filtres : on refuse. Prendre le
    premier de la liste serait un achat devine."""
    search = _search_fn({"MC": [_candidat(4901, "MC", "SBF"),
                                _candidat(4902, "MC", "SBF")]})
    info = _info_fn({
        "4901": {"conid": 4901, "symbol": "MC", "currency": "EUR", "listingExchange": "SBF"},
        "4902": {"conid": 4902, "symbol": "MC", "currency": "EUR", "listingExchange": "SBF"},
    })

    result = contracts.resolve_conid("MC.PA", search, info, {}, today="2026-09-14")

    assert result["conid"] is None
    assert result["motif"] == "contrat_non_resolu"
    assert "ambigu" in result["detail"]
    assert "4901" in result["detail"] and "4902" in result["detail"]


def test_resolve_conid_ignores_candidates_whose_symbol_differs():
    """La recherche IBKR renvoie aussi des symboles voisins."""
    search = _search_fn({"MC": [_candidat(1, "MCD", "SBF"),
                                _candidat(4901, "MC", "SBF")]})
    info = _info_fn({"4901": {"conid": 4901, "symbol": "MC", "currency": "EUR",
                              "listingExchange": "SBF"}})

    result = contracts.resolve_conid("MC.PA", search, info, {}, today="2026-09-14")

    assert result["conid"] == 4901
    assert "1" not in info.appels


def test_resolve_conid_ignores_candidates_without_a_stock_section():
    search = _search_fn({"MC": [_candidat(4901, "MC", "SBF", sec_types=("OPT", "FOP"))]})
    info = _info_fn({})

    result = contracts.resolve_conid("MC.PA", search, info, {}, today="2026-09-14")

    assert result["conid"] is None
    assert result["motif"] == "contrat_non_resolu"


def test_resolve_conid_refuses_an_out_of_scope_suffix_without_calling_ibkr():
    search = _search_fn({})
    info = _info_fn({})

    result = contracts.resolve_conid("7203.T", search, info, {}, today="2026-09-14")

    assert result["conid"] is None
    assert result["motif"] == "contrat_non_resolu"
    assert "suffixe hors perimetre" in result["detail"]
    assert search.appels == []


def test_resolve_conid_survives_a_search_that_raises():
    def search(symbol):
        raise RuntimeError("gateway injoignable")

    result = contracts.resolve_conid("MC.PA", search, _info_fn({}), {}, today="2026-09-14")

    assert result["conid"] is None
    assert result["motif"] == "contrat_non_resolu"
    assert "gateway injoignable" in result["detail"]


# --- cache -----------------------------------------------------------

def test_resolve_conid_stores_a_success_in_the_cache():
    search = _search_fn({"MC": [_candidat(4901, "MC", "SBF")]})
    info = _info_fn({"4901": {"conid": 4901, "symbol": "MC", "currency": "EUR",
                              "listingExchange": "SBF"}})
    cache = {}

    contracts.resolve_conid("MC.PA", search, info, cache, today="2026-09-14")

    assert cache["MC.PA"] == {
        "conid": 4901, "exchange": "SBF", "currency": "EUR", "resolved_on": "2026-09-14",
    }


def test_resolve_conid_reuses_the_cache_without_calling_ibkr():
    search = _search_fn({"MC": [_candidat(4901, "MC", "SBF")]})
    info = _info_fn({"4901": {"conid": 4901, "symbol": "MC", "currency": "EUR"}})
    cache = {"MC.PA": {"conid": 4901, "exchange": "SBF", "currency": "EUR",
                       "resolved_on": "2026-09-01"}}

    result = contracts.resolve_conid("MC.PA", search, info, cache, today="2026-09-14")

    assert result["conid"] == 4901
    assert result["motif"] is None
    assert search.appels == []
    assert info.appels == []


def test_resolve_conid_never_caches_a_failure():
    search = _search_fn({"MC": []})
    info = _info_fn({})
    cache = {}

    contracts.resolve_conid("MC.PA", search, info, cache, today="2026-09-14")

    assert cache == {}


def test_load_cache_degrades_to_empty_dict(tmp_path):
    assert contracts.load_cache(str(tmp_path / "absent.json")) == {}
    corrupted = tmp_path / "conid_cache.json"
    corrupted.write_text("{nope", encoding="utf-8")
    assert contracts.load_cache(str(corrupted)) == {}


def test_save_cache_then_load_cache_round_trips(tmp_path):
    path = str(tmp_path / "nested" / "conid_cache.json")
    cache = {"MC.PA": {"conid": 4901, "exchange": "SBF", "currency": "EUR",
                       "resolved_on": "2026-09-14"}}
    contracts.save_cache(cache, path)

    assert contracts.load_cache(path) == cache
    assert json.loads(open(path, encoding="utf-8").read()) == cache


def test_default_cache_path_points_inside_ibkr_bot_package():
    assert contracts.CONID_CACHE_PATH.replace("\\", "/").endswith(
        "ibkr_bot/conid_cache.json")
