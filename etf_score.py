"""Pipeline de donnees pour les ETF suivis dans l'onglet Indices --
chemin entierement separe de la logique de score des actions
(fetch_company_financials/extract_ratios*) : un ETF n'a ni compte de
resultat ni bilan exploitable de la meme facon, voir
docs/superpowers/specs/2026-11-12-etf-support-indices-design.md."""


def compute_tracking_difference(etf_prices: list[float], benchmark_prices: list[float]) -> float | None:
    """Performance de l'ETF moins performance de son indice de reference
    sur la meme periode (premier point vs dernier point des deux series),
    en points de pourcentage. None si l'une des deux series a moins de 2
    points ou si le prix de depart de l'indice est nul/negatif -- jamais
    une valeur approximee (voir Global Constraints)."""
    if len(etf_prices) < 2 or len(benchmark_prices) < 2:
        return None
    if benchmark_prices[0] <= 0 or etf_prices[0] <= 0:
        return None
    etf_perf_pct = (etf_prices[-1] / etf_prices[0] - 1.0) * 100
    benchmark_perf_pct = (benchmark_prices[-1] / benchmark_prices[0] - 1.0) * 100
    return etf_perf_pct - benchmark_perf_pct
