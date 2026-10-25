"""ftse_intraday_collector.py
Collecte intrajournaliere (point-in-time, pas retroactive) des cours des
99 entreprises du FTSE, pour alimenter a terme des echelles de graphique
fines (10 min / 1h / 4h) sur le site. Demarre a zero le 2026-10-25 :
aucune donnee historique n'existe avant cette date, ce fichier ne grossit
qu'en avancant dans le temps (voir .github/workflows/ftse_intraday.yml,
qui invoque ce script toutes les ~10 minutes pendant les heures de marche
probables du LSE).

Distinct de docs/price_history.json (indices_score.py, update_price_history) :
celui-ci garde UNE valeur par jour (avec une regle de downsampling vers
UNE valeur par semaine au-dela de 30 jours, retention 6 ans) -- bien trop
grossier pour des echelles sous la journee. Ce module garde au contraire
CHAQUE point collecte (un timestamp complet, pas seulement une date), sans
aucun downsampling, mais avec une retention beaucoup plus courte
(FTSE_INTRADAY_RETENTION_DAYS) pour borner la taille du fichier -- l'usage
vise (zoomer sur les dernieres heures/derniers jours) n'a pas besoin d'un
historique profond.

L'UI (docs/real_price_chart.js, docs/index.html) n'expose PAS encore les
echelles 10 min/1h/4h -- volontaire, le temps que ce fichier accumule
suffisamment de points pour qu'elles aient un sens (meme logique que le
chantier Nikkei/Hang Seng, docs/nikkei_hangseng_price_history.json)."""
import json
import os
from datetime import datetime, timedelta, timezone

FTSE_INTRADAY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "ftse_intraday_history.json")
INDICES_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "indices.json")
FTSE_INTRADAY_RETENTION_DAYS = 30


def is_lse_likely_open(now: datetime) -> bool:
    """Fenetre volontairement large (07h-17h UTC, lun-ven), couvrant les
    horaires reels du LSE (08h00-16h30 heure de Londres) quelle que soit
    la saison -- hiver GMT (08h00-16h30 UTC) ou ete BST (07h00-15h30 UTC).
    Un cycle de collecte de trop en debut/fin de fenetre est sans
    consequence (prix de cloture simplement repete) ; en rater un serait
    une vraie perte de donnee -- d'ou la marge plutot qu'un calcul precis
    a la minute pres du changement d'heure."""
    if now.weekday() >= 5:
        return False
    return 7 <= now.hour < 17


def load_ftse_intraday_history(path: str = FTSE_INTRADAY_PATH) -> list[dict]:
    """Meme contrat que indices_score.load_price_history : [] si le
    fichier est absent ou corrompu, jamais d'exception."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        return []


def _trim_ftse_intraday_entries(entries: list[dict], now: datetime) -> list[dict]:
    """Retention simple par age (pas de downsampling comme
    update_price_history -- voir le docstring du module) : toute entree
    plus ancienne que FTSE_INTRADAY_RETENTION_DAYS est supprimee."""
    cutoff_iso = (now - timedelta(days=FTSE_INTRADAY_RETENTION_DAYS)).replace(microsecond=0).isoformat()
    return [e for e in entries if e["time"] >= cutoff_iso]


def update_ftse_intraday_history(
    new_entries: list[dict], path: str = FTSE_INTRADAY_PATH, now: datetime | None = None,
) -> list[dict]:
    """Ajoute `new_entries` ({time, ticker, price}) a l'historique deja
    accumule, retrimme par age, puis ecrit le resultat. Degrade toujours
    vers [] sur erreur (fichier illisible, NaN detecte par
    allow_nan=False...) -- meme contrat que update_price_history, ne
    doit jamais faire echouer le cycle de collecte appelant."""
    now = now or datetime.now(timezone.utc)
    try:
        history = load_ftse_intraday_history(path)
        history.extend(new_entries)
        trimmed = _trim_ftse_intraday_entries(history, now)
        rounded = [{**e, "price": round(e["price"], 4)} for e in trimmed]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rounded, fh, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return rounded
    except Exception as e:
        print(f"Erreur historique intrajournalier FTSE : {e}")
        return []


def fetch_ftse_latest_prices(tickers: list[str], *, source=None) -> dict[str, float]:
    """Dernier cours connu (bougie 5 minutes la plus recente disponible)
    pour chaque ticker, via UN SEUL appel groupe yfinance -- plus
    efficace et moins susceptible d'etre limite en debit que 99 appels
    individuels. Ne leve jamais : un ticker sans donnee exploitable (ou
    l'appel groupe entier en echec) degrade vers son absence du dict
    renvoye, jamais une exception qui ferait echouer tout le cycle."""
    if source is None:
        import yfinance as yf
        source = yf.download
    try:
        data = source(tickers=tickers, period="1d", interval="5m", progress=False, group_by="ticker", threads=True)
    except Exception as e:
        print(f"Erreur telechargement groupe FTSE intraday : {e}")
        return {}
    prices: dict[str, float] = {}
    for ticker in tickers:
        try:
            closes = data["Close"] if len(tickers) == 1 else data[ticker]["Close"]
            closes = closes.dropna()
            if len(closes):
                prices[ticker] = float(closes.iloc[-1])
        except Exception:
            continue
    return prices


def main(
    *, now: datetime | None = None, indices_path: str = INDICES_JSON_PATH,
    price_source=None, history_path: str = FTSE_INTRADAY_PATH,
) -> None:
    now = now or datetime.now(timezone.utc)
    if not is_lse_likely_open(now):
        print(f"Hors des heures de marche LSE probables ({now.isoformat()}), rien a collecter.")
        return
    try:
        with open(indices_path, encoding="utf-8") as fh:
            indices_data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Erreur lecture {indices_path} : {e}")
        return
    companies = indices_data.get("companies") if isinstance(indices_data, dict) else indices_data
    tickers = [c["ticker"] for c in companies if c.get("index") == "FTSE"]
    if not tickers:
        print("Aucun ticker FTSE trouve dans indices.json.")
        return
    prices = fetch_ftse_latest_prices(tickers, source=price_source)
    if not prices:
        print("Aucun prix recupere pour ce cycle.")
        return
    timestamp = now.replace(microsecond=0).isoformat()
    new_entries = [{"time": timestamp, "ticker": t, "price": p} for t, p in prices.items()]
    result = update_ftse_intraday_history(new_entries, path=history_path, now=now)
    print(f"{len(new_entries)} prix FTSE collectes ({timestamp}), historique intrajournalier : {len(result)} points.")


if __name__ == "__main__":
    main()
