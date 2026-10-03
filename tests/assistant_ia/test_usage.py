# tests/assistant_ia/test_usage.py
import json

import assistant_ia.usage as usage


def test_record_usage_computes_cost_from_token_counts(tmp_path):
    path = str(tmp_path / "usage_today.json")

    total = usage.record_usage(1_000_000, 0, model="claude-opus-5-5", path=path)

    assert total == 4.0  # 1M tokens d'entree a 4.00 $/MTok


def test_record_usage_accumulates_across_calls(tmp_path):
    path = str(tmp_path / "usage_today.json")

    usage.record_usage(1_000_000, 0, model="claude-opus-5-5", path=path)
    total = usage.record_usage(0, 500_000, model="claude-opus-5-5", path=path)

    assert total == 4.0 + 10.0  # +500k tokens de sortie a 20.00 $/MTok


def test_record_usage_resets_on_a_new_utc_day(tmp_path, monkeypatch):
    path = str(tmp_path / "usage_today.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"date": "2020-01-01", "total_usd": 999.0}, fh)

    total = usage.record_usage(1_000_000, 0, model="claude-opus-5-5", path=path)

    assert total == 4.0  # l'ancien total d'un jour different est ignore


def test_budget_exceeded_is_false_under_the_cap(tmp_path):
    path = str(tmp_path / "usage_today.json")
    usage.record_usage(100_000, 0, model="claude-opus-5-5", path=path)  # 0.40 $

    assert usage.budget_exceeded(path=path, daily_budget_usd=5.0) is False


def test_budget_exceeded_is_true_at_or_above_the_cap(tmp_path):
    path = str(tmp_path / "usage_today.json")
    usage.record_usage(1_500_000, 0, model="claude-opus-5-5", path=path)  # 6.00 $

    assert usage.budget_exceeded(path=path, daily_budget_usd=5.0) is True


def test_budget_exceeded_is_false_when_the_usage_file_is_absent(tmp_path):
    path = str(tmp_path / "usage_today.json")

    assert usage.budget_exceeded(path=path, daily_budget_usd=5.0) is False


def test_load_usage_degrades_to_zero_on_corrupt_file(tmp_path):
    path = str(tmp_path / "usage_today.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")

    data = usage.load_usage(path=path)

    assert data["total_usd"] == 0.0
