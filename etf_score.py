"""Pipeline de donnees pour les ETF suivis dans l'onglet Indices --
chemin entierement separe de la logique de score des actions
(fetch_company_financials/extract_ratios*) : un ETF n'a ni compte de
resultat ni bilan exploitable de la meme facon, voir
docs/superpowers/specs/2026-11-12-etf-support-indices-design.md."""

import yfinance as yf


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


def fetch_etf_data(ticker: str) -> dict:
    """Donnees dynamiques d'un ETF (prix, historique, rendement du
    dividende si distribuant) -- chemin independant de
    fetch_company_financials, qui suppose un compte de resultat
    inexistant pour un ETF. Leve l'exception telle quelle en cas
    d'echec (meme convention que fetch_company_financials) ; c'est a
    l'appelant (boucle main()) de capturer par ticker."""
    t = yf.Ticker(ticker)
    info = t.info
    history_df = t.history(period="2y")
    if history_df.empty:
        raise ValueError(f"Historique de prix vide pour {ticker}")
    price_history = [
        {"date": ts.strftime("%Y-%m-%d"), "close": float(close)}
        for ts, close in history_df["Close"].items()
    ]
    dividend_yield = info.get("dividendYield")
    return {
        "current_price": price_history[-1]["close"],
        "price_history": price_history,
        "dividend_yield": float(dividend_yield) if dividend_yield is not None else None,
    }


def build_etf_entry(catalog_entry: dict, benchmark_price_history: list[dict] | None) -> dict:
    """Assemble l'entree finale d'un ETF pour docs/indices.json. Jamais de
    score/factors/sector/is_financial/is_trust/alerts (voir Global
    Constraints du plan) -- etf_info porte les metriques reelles a la
    place. `_price_history_daily` suit la forme {date, ticker, price}
    produite par extract_ratios/attendue par update_price_history dans
    indices_score.py -- pas {ticker, date, close}, qui n'est que la forme
    interne de fetch_etf_data()["price_history"]."""
    data = fetch_etf_data(catalog_entry["ticker"])
    etf_prices = [p["close"] for p in data["price_history"]]
    benchmark_prices = (
        [p["close"] for p in benchmark_price_history] if benchmark_price_history else None
    )
    tracking_difference = (
        compute_tracking_difference(etf_prices, benchmark_prices)
        if benchmark_prices else None
    )
    return {
        "ticker": catalog_entry["ticker"],
        "name": catalog_entry["name"],
        "index": "ETF",
        "current_price": data["current_price"],
        "news": [],
        "is_etf": True,
        "etf_info": {
            "benchmark_name": catalog_entry["benchmark_name"],
            "ter_pct": catalog_entry["ter_pct"],
            "replication": catalog_entry["replication"],
            "distribution": catalog_entry["distribution"],
            "pea_eligible": catalog_entry["pea_eligible"],
            "dividend_yield": data["dividend_yield"],
            "tracking_difference_pct": tracking_difference,
        },
        "_price_history_daily": [
            {"date": p["date"], "ticker": catalog_entry["ticker"], "price": p["close"]}
            for p in data["price_history"]
        ],
    }
