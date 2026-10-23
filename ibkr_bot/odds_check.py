# ibkr_bot/odds_check.py
# Verification des cotes historiques avant une entree, portee en Python
# depuis la seule logique agregee existante (docs/index.html::
# buildSignalStatsHtml, en JavaScript cote client). Segmente par
# indice/secteur (spec 2026-10-23) plutot que sur l'esperance globale,
# deliberement moins negative/plus discriminante -- voir la spec pour
# le raisonnement complet (le diagnostic global connu est legerement
# negatif, segmenter laisse une chance a un sous-ensemble meilleur que
# la moyenne au lieu de neutraliser le bot d'entree de jeu).
#
# N'utilise QUE les positions CLOTUREES (status == "closed") : une
# position encore ouverte n'a pas d'issue reelle a apprendre d'elle --
# contrairement a buildSignalStatsHtml, qui valorise aussi les positions
# ouvertes au cours du jour pour l'AFFICHAGE (un besoin d'UI, pas une
# donnee d'entrainement). Melanger les deux biaiserait la verification
# historique avec des rendements non definitifs.
#
# docs/signal_tracking.json ne stocke PAS de champ "sector" sur les
# positions (seulement "index") -- `ticker_to_sector` est construit par
# l'appelant (ibkr_bot/daily.py) a partir de docs/indices.json du jour.
def historical_odds_for_signal(
    positions: list[dict], ticker_to_sector: dict[str, str],
    index: str, sector: str, min_sample_size: int = 20,
) -> dict:
    """Cotes historiques (alpha moyen vs indice) pour un signal du meme
    indice, ou a defaut du meme secteur si l'echantillon par indice est
    trop petit. Renvoie {"verdict": "insufficient_history" |
    "favorable" | "unfavorable", "segment_used": "index" | "sector" |
    None, "sample_size": int, "avg_alpha_pct": float | None}.

    REGLE DE VERDICT : alpha moyen negatif OU NUL -> "unfavorable"
    (bloque le trade) ; sinon "favorable". Si NI l'indice NI le secteur
    n'atteignent min_sample_size, "insufficient_history" -- ne bloque
    JAMAIS le trade (un echantillon trop petit ne permet de conclure
    dans aucun sens, voir le meme choix pour gold_bot.odds_check)."""
    closed = [
        p for p in positions
        if p.get("status") == "closed"
        and p.get("return_pct") is not None
        and p.get("index_return_pct") is not None
    ]
    by_index = [p for p in closed if p.get("index") == index]
    if len(by_index) >= min_sample_size:
        segment_used, sample = "index", by_index
    else:
        by_sector = (
            [p for p in closed if sector and ticker_to_sector.get(p["ticker"]) == sector]
        )
        if len(by_sector) >= min_sample_size:
            segment_used, sample = "sector", by_sector
        else:
            return {
                "verdict": "insufficient_history", "segment_used": None,
                "sample_size": max(len(by_index), len(by_sector)),
                "avg_alpha_pct": None,
            }

    alphas = [p["return_pct"] - p["index_return_pct"] for p in sample]
    avg_alpha_pct = sum(alphas) / len(alphas)
    verdict = "unfavorable" if avg_alpha_pct <= 0 else "favorable"
    return {
        "verdict": verdict, "segment_used": segment_used,
        "sample_size": len(sample), "avg_alpha_pct": avg_alpha_pct,
    }
