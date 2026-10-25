import json

from gold_score import load_previous_payload


def test_load_previous_payload_returns_empty_dict_when_file_missing(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    assert load_previous_payload(str(missing)) == {}


def test_load_previous_payload_returns_empty_dict_on_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not valid json {{{", encoding="utf-8")
    assert load_previous_payload(str(bad)) == {}


def test_load_previous_payload_returns_parsed_content(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"analysis_html": "<p>x</p>"}), encoding="utf-8")
    assert load_previous_payload(str(good)) == {"analysis_html": "<p>x</p>"}
