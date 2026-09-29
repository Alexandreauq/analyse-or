import gold_bot.chart_patterns as chart_patterns


def test_double_top_detected():
    pivots = [
        {"index": 0, "type": "high", "price": 110},
        {"index": 5, "type": "low", "price": 100},
        {"index": 10, "type": "high", "price": 110.5},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "haussier", 99)
    assert result == {
        "name": "Double Top", "direction": "baissier", "kind": "retournement",
        "breakoutPrice": 100, "extremityPrice": 110.5, "patternHeight": 10.5,
    }


def test_double_top_rejected_when_heights_too_far_apart():
    # Identique au test précédent mais h2=112 au lieu de 110.5 : écart de
    # 2$ > CHARTPATTERN_HEIGHT_TOLERANCE (1$) -> aucune figure détectée.
    pivots = [
        {"index": 0, "type": "high", "price": 110},
        {"index": 5, "type": "low", "price": 100},
        {"index": 10, "type": "high", "price": 112},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "haussier", 99)
    assert result is None


def test_double_bottom_detected():
    pivots = [
        {"index": 0, "type": "low", "price": 90},
        {"index": 5, "type": "high", "price": 100},
        {"index": 10, "type": "low", "price": 90.5},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "baissier", 101)
    assert result == {
        "name": "Double Bottom", "direction": "haussier", "kind": "retournement",
        "breakoutPrice": 100, "extremityPrice": 90, "patternHeight": 10,
    }


def test_tete_epaule_detected():
    pivots = [
        {"index": 0, "type": "high", "price": 100},   # épaule 1
        {"index": 5, "type": "low", "price": 95},
        {"index": 10, "type": "high", "price": 110},  # tête
        {"index": 15, "type": "low", "price": 96},
        {"index": 20, "type": "high", "price": 100.5},  # épaule 2
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "haussier", 95)
    assert result == {
        "name": "Tête-Épaule", "direction": "baissier", "kind": "retournement",
        "breakoutPrice": 96, "extremityPrice": 110, "patternHeight": 14,
    }


def test_tete_epaule_inversee_detected():
    pivots = [
        {"index": 0, "type": "low", "price": 100},
        {"index": 5, "type": "high", "price": 105},
        {"index": 10, "type": "low", "price": 90},
        {"index": 15, "type": "high", "price": 104},
        {"index": 20, "type": "low", "price": 100.5},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "baissier", 105)
    assert result == {
        "name": "Tête-Épaule inversée", "direction": "haussier", "kind": "retournement",
        "breakoutPrice": 104, "extremityPrice": 90, "patternHeight": 14,
    }


def test_triangle_ascendant_detected():
    pivots = [
        {"index": 0, "type": "high", "price": 110},
        {"index": 5, "type": "low", "price": 95},
        {"index": 10, "type": "high", "price": 110.5},
        {"index": 15, "type": "low", "price": 100},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "haussier", 111)
    assert result == {
        "name": "Triangle ascendant", "direction": "haussier", "kind": "continuation",
        "breakoutPrice": 110.5, "extremityPrice": 95, "patternHeight": 15.5,
    }


def test_triangle_descendant_detected():
    pivots = [
        {"index": 0, "type": "low", "price": 90},
        {"index": 5, "type": "high", "price": 105},
        {"index": 10, "type": "low", "price": 90.5},
        {"index": 15, "type": "high", "price": 100},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "baissier", 90)
    assert result == {
        "name": "Triangle descendant", "direction": "baissier", "kind": "continuation",
        "breakoutPrice": 90.5, "extremityPrice": 105, "patternHeight": 14.5,
    }


def test_triangle_symetrique_breakout_haussier():
    pivots = [
        {"index": 0, "type": "high", "price": 110},
        {"index": 5, "type": "low", "price": 90},
        {"index": 10, "type": "high", "price": 105},
        {"index": 15, "type": "low", "price": 95},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "haussier", 106)
    assert result == {
        "name": "Triangle symétrique", "direction": "haussier", "kind": "continuation",
        "breakoutPrice": 105, "extremityPrice": 90, "patternHeight": 20,
    }


def test_triangle_symetrique_breakout_baissier():
    # Mêmes pivots que le test précédent, mais tendance et prix inversés
    # -> cassure dans l'autre sens (le triangle symétrique ne présume pas
    # du sens avant la cassure effective, cf. spec chart_patterns.js).
    pivots = [
        {"index": 0, "type": "high", "price": 110},
        {"index": 5, "type": "low", "price": 90},
        {"index": 10, "type": "high", "price": 105},
        {"index": 15, "type": "low", "price": 95},
    ]
    result = chart_patterns.detect_chart_patterns(pivots, "baissier", 89)
    assert result == {
        "name": "Triangle symétrique", "direction": "baissier", "kind": "continuation",
        "breakoutPrice": 95, "extremityPrice": 110, "patternHeight": 20,
    }
