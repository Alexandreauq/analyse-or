# gold_news_reasoning_daily.py (en-tete + fonctions de ce Task -- les
# imports reseau/API sont ajoutes au Task 2, ne pas les ajouter ici)
"""Test prospectif IA en paper-trading sur l'or (XAUUSD) -- voir
docs/superpowers/specs/2026-11-06-gold-news-reasoning-daily-paper-trading-design.md.
Execute quotidiennement via .github/workflows/gold_news_reasoning_daily.yml
(jours ouvres). Aucun lien avec gold_bot/ -- purement observationnel,
aucun compte reel, aucune execution de trade."""
import json
import os
import re
from datetime import date, timedelta

import requests
import yfinance as yf

MIN_STOP_PCT = 0.03
MAX_STOP_PCT = 0.15
MAX_HOLD_DAYS = 21


def normalize_direction_call(appel) -> str | None:
    """Normalise un appel directionnel textuel (haussier/leger_haussier/...,
    baissier/leger_baissier/..., neutre/neutre_prudent/...) en "achat",
    "vente", ou None (neutre, non reconnu, ou type inattendu -- aucune
    position n'est alors ouverte, voir spec #4.2)."""
    if not isinstance(appel, str):
        return None
    normalized = appel.strip().lower()
    if "neutre" in normalized:
        return None
    if "haussier" in normalized:
        return "achat"
    if "baissier" in normalized:
        return "vente"
    return None


def clamp_stop_pct(raw_pct: float) -> float:
    """Ramene une distance de stop (valeur absolue, fraction -- ex. 0.08
    pour 8%) a la fourchette [MIN_STOP_PCT, MAX_STOP_PCT]. Prend la
    valeur absolue d'abord (le modele pourrait renvoyer un signe)."""
    magnitude = abs(raw_pct)
    return max(MIN_STOP_PCT, min(MAX_STOP_PCT, magnitude))


def compute_safety_stop_price(entry_price: float, direction: str, raw_stop_pct: float) -> float:
    """raw_stop_pct est la proposition BRUTE du modele -- clampee ici,
    jamais appliquee telle quelle. Pour un achat, le stop est SOUS le
    prix d'entree ; pour une vente, AU-DESSUS."""
    magnitude = clamp_stop_pct(raw_stop_pct)
    if direction == "achat":
        return entry_price * (1 - magnitude)
    return entry_price * (1 + magnitude)


def compute_safety_stop_pct(entry_price: float, stop_price: float, direction: str) -> float:
    """Toujours une valeur NEGATIVE (perte exprimee en %), quel que soit
    le sens de la position."""
    if direction == "achat":
        return stop_price / entry_price - 1
    return entry_price / stop_price - 1


def compute_return_pct(entry_price: float, close_price: float, direction: str) -> float:
    if direction == "achat":
        return close_price / entry_price - 1
    return entry_price / close_price - 1


def compute_buyhold_return_pct(entry_price: float, market_close_price: float) -> float:
    """TOUJOURS brut (jamais ajuste par direction) -- repere passif
    "aurait-on mieux fait de juste detenir de l'or sur cette periode"."""
    return market_close_price / entry_price - 1


def compute_max_close_date(entry_date: date) -> date:
    return entry_date + timedelta(days=MAX_HOLD_DAYS)


def find_stop_breach(direction: str, stop_price: float, ohlc_rows: list[dict]) -> dict | None:
    """ohlc_rows : liste de {"date": "YYYY-MM-DD", "high": float, "low":
    float} dans N'IMPORTE QUEL ordre -- cette fonction les trie
    elle-meme par date pour garantir de renvoyer le PREMIER jour
    chronologique de franchissement, jamais le plus extreme. Renvoie la
    ligne entiere (pour que l'appelant recupere sa date), ou None si
    aucun franchissement."""
    for row in sorted(ohlc_rows, key=lambda r: r["date"]):
        if direction == "achat" and row["low"] <= stop_price:
            return row
        if direction == "vente" and row["high"] >= stop_price:
            return row
    return None


YF_PRIMARY_TICKER = "GC=F"
YF_FALLBACK_TICKER = "XAUUSD=X"


def fetch_daily_ohlc(period: str = "2mo") -> list[dict] | None:
    """Recupere les clotures/plus-hauts/plus-bas journaliers de l'or via
    yfinance (GC=F en premier -- confirme fonctionnel le 2026-11-06,
    XAUUSD=X a echoue seul lors du test manuel -- repli quand meme
    conserve par resilience). Renvoie une liste de {"date", "open",
    "high", "low", "close"} triee par date croissante, ou None si les
    deux tickers echouent/renvoient une serie vide -- jamais
    d'exception (dialogue avec un service tiers dont on ne peut pas
    enumerer precisement tous les modes d'echec, meme philosophie que
    indices_score.py:fetch_article_text)."""
    for ticker in (YF_PRIMARY_TICKER, YF_FALLBACK_TICKER):
        try:
            data = yf.download(ticker, period=period, interval="1d", progress=False)
        except Exception:
            continue
        if data is None or len(data) == 0:
            continue
        columns = data.columns
        if getattr(columns, "nlevels", 1) > 1:
            data.columns = columns.get_level_values(0)
        rows = []
        for ts, row in data.iterrows():
            try:
                rows.append({
                    "date": ts.date().isoformat(),
                    "open": float(row["Open"]), "high": float(row["High"]),
                    "low": float(row["Low"]), "close": float(row["Close"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
        if rows:
            return sorted(rows, key=lambda r: r["date"])
    return None


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
GOLD_REASONING_MODEL = "claude-opus-5-5"
GOLD_REASONING_TIMEOUT_SECONDS = 40
GOLD_REASONING_MAX_TOKENS = 4096
GOLD_REASONING_WEB_SEARCH_MAX_USES = 3


def _extract_json_block(text: str, required_key: str) -> dict | None:
    pattern = re.compile(r'\{[^{}]*"' + re.escape(required_key) + r'"[^{}]*\}')
    matches = pattern.findall(text)
    if not matches:
        return None
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _ohlc_summary_lines(ohlc_rows: list[dict], limit: int = 10) -> str:
    recent = ohlc_rows[-limit:]
    return "\n".join(
        f"{r['date']}: O={r['open']:.2f} H={r['high']:.2f} L={r['low']:.2f} C={r['close']:.2f}"
        for r in recent
    )


def call_claude_for_new_entry(ohlc_rows: list[dict]) -> dict | None:
    """Demande a Claude (avec l'outil web_search) un appel directionnel
    sur l'or pour les 3 prochaines semaines. Renvoie {"appel": str,
    "stop_pct": float, "confiance": str, "raison": str} ou None sur
    tout echec (cle API absente, erreur reseau, reponse non
    parseable) -- jamais d'exception propagee."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    prompt = (
        "Tu es un analyste qui raisonne sur l'or (XAUUSD) a partir de "
        "l'actualite recente et du comportement du prix/volume -- PAS "
        "de formule mecanique, un jugement contextuel comme un trader "
        "humain le ferait. Ceci est un test prospectif methodologique "
        "en paper-trading : aucune position n'est reelle.\n\n"
        f"Dernieres cloture/plus-haut/plus-bas journaliers :\n{_ohlc_summary_lines(ohlc_rows)}\n\n"
        "Cherche l'actualite macro/geopolitique la plus pertinente pour "
        "l'or recemment (banques centrales, inflation, risque "
        "geopolitique, flux ETF). Fais un appel directionnel sur "
        "l'horizon des 3 prochaines semaines : haussier, baissier, ou "
        "neutre (nuances possibles : leger_haussier, neutre_prudent, "
        "etc., comme un vrai jugement nuance), avec une confiance "
        "(faible/moyenne/haute) et une raison en une ou deux phrases. "
        "Si haussier ou baissier, propose aussi une distance de stop de "
        "securite en % (entre 3% et 15% du prix d'entree, exprimee en "
        "fraction, ex. 0.08 pour 8%) adaptee a la volatilite recente.\n\n"
        "Termine ta reponse par un bloc JSON seul sur sa derniere "
        "ligne, exactement sous cette forme : "
        '{"appel": "...", "stop_pct": 0.08, "confiance": "faible ou '
        'moyenne ou haute", "raison": "une ou deux phrases"}'
    )
    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": GOLD_REASONING_MODEL,
                "max_tokens": GOLD_REASONING_MAX_TOKENS,
                "output_config": {"effort": "medium"},
                "tools": [{"type": "web_search_20260209", "name": "web_search", "max_uses": GOLD_REASONING_WEB_SEARCH_MAX_USES}],
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=GOLD_REASONING_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        return _extract_json_block(text, "appel")
    except Exception:
        return None


def call_claude_for_reeval(position: dict, ohlc_rows: list[dict]) -> dict | None:
    """Demande a Claude (avec l'outil web_search) s'il faut GARDER ou
    CLOTURER une position ouverte. Renvoie {"decision": "garder" ou
    "cloturer", "raison": str} ou None sur tout echec. L'appelant
    (voir main()) traite None comme "garder" par defaut -- jamais de
    cloture forcee sur un simple echec technique."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    prompt = (
        "Tu es un analyste qui suit une position simulee sur l'or "
        f"(XAUUSD), ouverte le {position['entry_date']} en "
        f"{position['direction']} a {position['entry_price']}, raison "
        f"initiale : {position.get('raison_ouverture', '')}.\n\n"
        f"Dernieres cloture/plus-haut/plus-bas journaliers :\n{_ohlc_summary_lines(ohlc_rows)}\n\n"
        "Cherche l'actualite macro/geopolitique recente pertinente pour "
        "l'or, et decide : faut-il GARDER cette position, ou la "
        "CLOTURER par anticipation (prise de profit ou retournement de "
        "these) ? Donne une raison en une phrase.\n\n"
        "Termine ta reponse par un bloc JSON seul sur sa derniere "
        "ligne, exactement sous cette forme : "
        '{"decision": "garder ou cloturer", "raison": "une phrase"}'
    )
    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": GOLD_REASONING_MODEL,
                "max_tokens": GOLD_REASONING_MAX_TOKENS,
                "output_config": {"effort": "medium"},
                "tools": [{"type": "web_search_20260209", "name": "web_search", "max_uses": GOLD_REASONING_WEB_SEARCH_MAX_USES}],
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=GOLD_REASONING_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        return _extract_json_block(text, "decision")
    except Exception:
        return None


TRACKING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "gold_news_reasoning_tracking.json")


def _read_tracking(path: str = TRACKING_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {"positions": []}
    if not isinstance(data, dict) or not isinstance(data.get("positions"), list):
        return {"positions": []}
    return data


def _write_tracking(data: dict, path: str = TRACKING_PATH) -> None:
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp_path, path)


def _find_open_position(tracking: dict) -> dict | None:
    for position in tracking["positions"]:
        if position.get("status") == "open":
            return position
    return None


def _rows_since(ohlc_rows: list[dict], since_date: str) -> list[dict]:
    """Lignes dont la date est STRICTEMENT posterieure a since_date --
    since_date est entry_date (pas encore de journal) ou la date de la
    derniere entree de journal (deja evaluee, ne pas la re-evaluer)."""
    return [r for r in ohlc_rows if r["date"] > since_date]


def _close_position(position: dict, close_date: str, close_price: float, close_reason: str, market_close_price: float) -> None:
    position["status"] = "closed"
    position["close_date"] = close_date
    position["close_price"] = close_price
    position["close_reason"] = close_reason
    position["return_pct"] = compute_return_pct(position["entry_price"], close_price, position["direction"])
    position["buyhold_price_at_close"] = market_close_price
    position["buyhold_return_pct"] = compute_buyhold_return_pct(position["entry_price"], market_close_price)


def process_open_position(position: dict, ohlc_rows: list[dict], today: date) -> bool:
    """Reevalue une position ouverte, la mute EN PLACE selon les
    priorites : (1) stop, (2) delai_max -- deterministes, zero appel
    modele -- puis (3) reevaluation qualitative SEULEMENT si ni (1) ni
    (2) ne s'applique. Renvoie True si la position a change (cloturee
    ou journal modifie)."""
    since_date = position["journal"][-1]["date"] if position["journal"] else position["entry_date"]
    new_rows = _rows_since(ohlc_rows, since_date)

    breach = find_stop_breach(position["direction"], position["safety_stop_price"], new_rows)
    if breach is not None:
        _close_position(position, breach["date"], position["safety_stop_price"], "stop_securite", breach["close"])
        return True

    if today.isoformat() >= position["max_close_date"]:
        last_close = ohlc_rows[-1]["close"] if ohlc_rows else position["entry_price"]
        _close_position(position, today.isoformat(), last_close, "delai_max", last_close)
        return True

    verdict = call_claude_for_reeval(position, ohlc_rows)
    last_close = ohlc_rows[-1]["close"] if ohlc_rows else position["entry_price"]
    if verdict is not None and verdict.get("decision") == "cloturer":
        _close_position(position, today.isoformat(), last_close, "cloture_anticipee_ia", last_close)
        return True
    raison = verdict.get("raison") if verdict else "echec de l'appel API, position gardee par defaut"
    position["journal"].append({"date": today.isoformat(), "price": last_close, "decision": "garder", "raison": raison})
    return True


def process_new_entry(ohlc_rows: list[dict], today: date) -> dict | None:
    """Decide d'ouvrir une nouvelle position ou non. Renvoie le nouveau
    dict de position (status="open") ou None si l'appel est
    neutre/non reconnu/l'appel API a echoue."""
    verdict = call_claude_for_new_entry(ohlc_rows)
    if verdict is None:
        return None
    direction = normalize_direction_call(verdict.get("appel", ""))
    if direction is None:
        return None
    entry_price = ohlc_rows[-1]["close"]
    raw_stop_pct = verdict.get("stop_pct", MIN_STOP_PCT)
    if not isinstance(raw_stop_pct, (int, float)) or isinstance(raw_stop_pct, bool):
        raw_stop_pct = MIN_STOP_PCT
    safety_stop_price = compute_safety_stop_price(entry_price, direction, raw_stop_pct)
    safety_stop_pct = compute_safety_stop_pct(entry_price, safety_stop_price, direction)
    entry_date = today.isoformat()
    return {
        "id": f"XAUUSD-{entry_date}", "status": "open",
        "entry_date": entry_date, "entry_price": entry_price, "direction": direction,
        "safety_stop_price": safety_stop_price, "safety_stop_pct": safety_stop_pct,
        "confiance": verdict.get("confiance", ""), "raison_ouverture": verdict.get("raison", ""),
        "max_close_date": compute_max_close_date(today).isoformat(),
        "close_date": None, "close_price": None, "close_reason": None, "return_pct": None,
        "buyhold_price_at_close": None, "buyhold_return_pct": None, "journal": [],
    }


def main() -> None:
    ohlc_rows = fetch_daily_ohlc()
    if not ohlc_rows:
        print("fetch_daily_ohlc a echoue (GC=F et XAUUSD=X indisponibles) -- aucune action aujourd'hui.")
        return  # pas de prix -> aucune action, jamais de repli WebSearch
    print(f"Prix recuperes : {len(ohlc_rows)} jours, dernier = {ohlc_rows[-1]}")
    today = date.fromisoformat(ohlc_rows[-1]["date"])
    tracking = _read_tracking(TRACKING_PATH)
    changed = False

    open_position = _find_open_position(tracking)
    if open_position is not None:
        print(f"Position ouverte trouvee : {open_position['id']} ({open_position['direction']})")
        changed = process_open_position(open_position, ohlc_rows, today) or changed
        print(f"Apres reevaluation : status={open_position['status']} close_reason={open_position.get('close_reason')}")
        open_position = _find_open_position(tracking)

    if open_position is None:
        print("Aucune position ouverte -- interrogation du modele pour une nouvelle entree.")
        new_position = process_new_entry(ohlc_rows, today)
        if new_position is not None:
            print(f"Nouvelle position ouverte : {new_position['id']} ({new_position['direction']})")
            tracking["positions"].append(new_position)
            changed = True
        else:
            print("Appel neutre, non reconnu, ou echec de l'appel API -- aucune position ouverte.")

    if changed:
        _write_tracking(tracking, TRACKING_PATH)
        print("Fichier de suivi mis a jour.")
    else:
        print("Rien n'a change -- aucune ecriture.")


if __name__ == "__main__":
    main()
