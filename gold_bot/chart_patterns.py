# gold_bot/chart_patterns.py
# Détection de figures chartistes (Double Top/Bottom, Tête-Épaule,
# Triangles) — portage fidèle de docs/chart_patterns.js::detectChartPatterns
# (même comportement, mêmes 9 cas de test). Module autonome, sans
# dépendance vers gold_bot.confluence, comme son équivalent JS n'a pas
# de dépendance vers docs/scalping.js.

CHARTPATTERN_PIVOT_K = 5  # plus grand que l'ancien K=3 (tendance/S-R
                          # ponctuel) : une figure chartiste s'étale sur
                          # bien plus de bougies qu'un niveau ponctuel.
CHARTPATTERN_HEIGHT_TOLERANCE = 1.0  # $ de tolérance pour juger deux
                                      # sommets/creux "de hauteur comparable"


def _height_close(a: float, b: float) -> bool:
    return abs(a - b) <= CHARTPATTERN_HEIGHT_TOLERANCE


def detect_chart_patterns(pivots: list[dict], trend: str, price: float) -> dict | None:
    """Détecte une des 7 figures chartistes reconnues à partir de `pivots`
    (déjà calculés par l'appelant avec K=CHARTPATTERN_PIVOT_K), `trend`
    (classify_trend, gold_bot.confluence) et `price` (dernière clôture,
    pour juger si une cassure est confirmée). Renvoie la première figure
    trouvée (ordre : retournement d'abord, puis continuation) ou None.
    Ne lève jamais d'exception — pivots insuffisants renvoie simplement None."""
    highs = [p for p in pivots if p["type"] == "high"]
    lows = [p for p in pivots if p["type"] == "low"]

    # --- Double Top (retournement baissier) ---
    if trend == "haussier" and len(highs) >= 2:
        h1, h2 = highs[-2:]
        between = [l for l in lows if h1["index"] < l["index"] < h2["index"]]
        if between and _height_close(h1["price"], h2["price"]):
            neckline = between[-1]["price"]
            if price < neckline:
                return {
                    "name": "Double Top", "direction": "baissier", "kind": "retournement",
                    "breakoutPrice": neckline, "extremityPrice": max(h1["price"], h2["price"]),
                    "patternHeight": max(h1["price"], h2["price"]) - neckline,
                }

    # --- Double Bottom (retournement haussier) ---
    if trend == "baissier" and len(lows) >= 2:
        l1, l2 = lows[-2:]
        between = [h for h in highs if l1["index"] < h["index"] < l2["index"]]
        if between and _height_close(l1["price"], l2["price"]):
            neckline = between[-1]["price"]
            if price > neckline:
                return {
                    "name": "Double Bottom", "direction": "haussier", "kind": "retournement",
                    "breakoutPrice": neckline, "extremityPrice": min(l1["price"], l2["price"]),
                    "patternHeight": neckline - min(l1["price"], l2["price"]),
                }

    # --- Tête-Épaule (retournement baissier) ---
    if trend == "haussier" and len(highs) >= 3:
        s1, head, s2 = highs[-3:]
        troughs = [l for l in lows if s1["index"] < l["index"] < s2["index"]]
        if len(troughs) >= 2 and _height_close(s1["price"], s2["price"]) \
                and head["price"] > s1["price"] and head["price"] > s2["price"]:
            neckline = troughs[-1]["price"]
            if price < neckline:
                return {
                    "name": "Tête-Épaule", "direction": "baissier", "kind": "retournement",
                    "breakoutPrice": neckline, "extremityPrice": head["price"],
                    "patternHeight": head["price"] - neckline,
                }

    # --- Tête-Épaule inversée (retournement haussier) ---
    if trend == "baissier" and len(lows) >= 3:
        s1, head, s2 = lows[-3:]
        peaks = [h for h in highs if s1["index"] < h["index"] < s2["index"]]
        if len(peaks) >= 2 and _height_close(s1["price"], s2["price"]) \
                and head["price"] < s1["price"] and head["price"] < s2["price"]:
            neckline = peaks[-1]["price"]
            if price > neckline:
                return {
                    "name": "Tête-Épaule inversée", "direction": "haussier", "kind": "retournement",
                    "breakoutPrice": neckline, "extremityPrice": head["price"],
                    "patternHeight": neckline - head["price"],
                }

    # --- Triangle ascendant (continuation haussière) ---
    if trend == "haussier" and len(highs) >= 2 and len(lows) >= 2:
        h1, h2 = highs[-2:]
        l1, l2 = lows[-2:]
        if _height_close(h1["price"], h2["price"]) and l2["price"] > l1["price"]:
            resistance = h2["price"]
            if price > resistance:
                return {
                    "name": "Triangle ascendant", "direction": "haussier", "kind": "continuation",
                    "breakoutPrice": resistance, "extremityPrice": l1["price"],
                    "patternHeight": resistance - l1["price"],
                }

    # --- Triangle descendant (continuation baissière) ---
    if trend == "baissier" and len(highs) >= 2 and len(lows) >= 2:
        h1, h2 = highs[-2:]
        l1, l2 = lows[-2:]
        if _height_close(l1["price"], l2["price"]) and h2["price"] < h1["price"]:
            support = l2["price"]
            if price < support:
                return {
                    "name": "Triangle descendant", "direction": "baissier", "kind": "continuation",
                    "breakoutPrice": support, "extremityPrice": h1["price"],
                    "patternHeight": h1["price"] - support,
                }

    # --- Triangle symétrique (continuation, sens déterminé par la cassure) ---
    if len(highs) >= 2 and len(lows) >= 2:
        h1, h2 = highs[-2:]
        l1, l2 = lows[-2:]
        converging = h2["price"] < h1["price"] and l2["price"] > l1["price"]
        if converging:
            if trend == "haussier" and price > h2["price"]:
                return {
                    "name": "Triangle symétrique", "direction": "haussier", "kind": "continuation",
                    "breakoutPrice": h2["price"], "extremityPrice": l1["price"],
                    "patternHeight": h1["price"] - l1["price"],
                }
            if trend == "baissier" and price < l2["price"]:
                return {
                    "name": "Triangle symétrique", "direction": "baissier", "kind": "continuation",
                    "breakoutPrice": l2["price"], "extremityPrice": h1["price"],
                    "patternHeight": h1["price"] - l1["price"],
                }

    return None
