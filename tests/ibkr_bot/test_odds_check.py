# tests/ibkr_bot/test_odds_check.py
import ibkr_bot.odds_check as odds_check


def _closed(ticker, index, return_pct, index_return_pct):
    return {"ticker": ticker, "status": "closed", "index": index,
            "return_pct": return_pct, "index_return_pct": index_return_pct}


def _open(ticker, index):
    return {"ticker": ticker, "status": "open", "index": index,
            "return_pct": None, "index_return_pct": None}


def test_insufficient_history_when_both_segments_are_below_the_minimum():
    positions = [_closed("A.PA", "CAC40", 5.0, 2.0) for _ in range(5)]
    ticker_to_sector = {"A.PA": "Luxe"}

    verdict = odds_check.historical_odds_for_signal(
        positions, ticker_to_sector, "CAC40", "Luxe", min_sample_size=20)

    assert verdict["verdict"] == "insufficient_history"
    assert verdict["segment_used"] is None


def test_favorable_verdict_when_the_index_segment_has_positive_average_alpha():
    positions = [_closed(f"T{i}.PA", "CAC40", 10.0, 2.0) for i in range(25)]
    ticker_to_sector = {}

    verdict = odds_check.historical_odds_for_signal(
        positions, ticker_to_sector, "CAC40", "", min_sample_size=20)

    assert verdict["verdict"] == "favorable"
    assert verdict["segment_used"] == "index"
    assert verdict["sample_size"] == 25


def test_unfavorable_verdict_when_the_index_segment_has_negative_average_alpha():
    positions = [_closed(f"T{i}.PA", "CAC40", -3.0, 2.0) for i in range(25)]

    verdict = odds_check.historical_odds_for_signal(
        positions, {}, "CAC40", "", min_sample_size=20)

    assert verdict["verdict"] == "unfavorable"


def test_zero_average_alpha_is_unfavorable_not_favorable():
    """La spec est explicite : alpha moyen 'negatif OU NUL' -> defavorable."""
    positions = [_closed(f"T{i}.PA", "CAC40", 2.0, 2.0) for i in range(25)]  # alpha = 0

    verdict = odds_check.historical_odds_for_signal(
        positions, {}, "CAC40", "", min_sample_size=20)

    assert verdict["verdict"] == "unfavorable"


def test_falls_back_to_sector_when_the_index_segment_is_too_small():
    index_positions = [_closed(f"I{i}.PA", "CAC40", 5.0, 2.0) for i in range(3)]
    sector_positions = [_closed(f"S{i}.DE", "DAX", 8.0, 1.0) for i in range(22)]
    ticker_to_sector = {p["ticker"]: "Tech" for p in sector_positions}
    positions = index_positions + sector_positions

    verdict = odds_check.historical_odds_for_signal(
        positions, ticker_to_sector, "CAC40", "Tech", min_sample_size=20)

    assert verdict["verdict"] == "favorable"
    assert verdict["segment_used"] == "sector"
    assert verdict["sample_size"] == 22


def test_open_positions_are_excluded_from_every_segment():
    positions = [_open(f"T{i}.PA", "CAC40") for i in range(25)]

    verdict = odds_check.historical_odds_for_signal(
        positions, {}, "CAC40", "", min_sample_size=20)

    assert verdict["verdict"] == "insufficient_history"
    assert verdict["sample_size"] == 0


def test_blank_sector_never_matches_anything():
    positions = [_closed(f"T{i}.PA", "AUTRE_INDICE", 10.0, 2.0) for i in range(25)]
    ticker_to_sector = {p["ticker"]: "" for p in positions}

    verdict = odds_check.historical_odds_for_signal(
        positions, ticker_to_sector, "CAC40", "", min_sample_size=20)

    assert verdict["verdict"] == "insufficient_history"
