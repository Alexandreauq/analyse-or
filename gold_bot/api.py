# gold_bot/api.py
# Point d'accès HTTPS du bot : deux routes de lecture (/status,
# /dashboard) et deux routes de mutation (/kill, /resume — seules
# routes qui changent un état, et seul kill_switch peut être modifié,
# jamais dry_run). Ce module n'importe jamais le module de courtage
# (celui qui peut passer des ordres réels), même pour lire un solde
# ou des positions : /dashboard ne lit que des fichiers locaux mis en
# cache par gold_bot/loop.py (voir
# docs/superpowers/specs/2026-09-11-bot-dashboard-design.md). Aucune
# route ne peut jamais déclencher un ordre — ces actions n'existent
# tout simplement pas ici. Voir aussi
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import hmac
import json
import math
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import gold_bot.state as state

CIRCUIT_BREAKER_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "circuit_breaker_state.json")
DECISIONS_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decisions_log.jsonl")
LATEST_CANDLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_candles.json")
LATEST_BALANCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_balance.json")
LATEST_POSITIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_positions.json")
RECENT_DECISIONS_LIMIT = 50

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://alexandreauq.github.io"],
    allow_methods=["GET", "POST"],
    allow_headers=["X-Bot-Token"],
)


def _check_token(x_bot_token: str | None) -> None:
    """Refuse par défaut si BOT_API_TOKEN n'est pas configuré (échec
    fermé, jamais ouvert) — pas seulement si le jeton fourni est faux.
    Comparaison à temps constant sur les octets UTF-8 (pas des `str`) :
    Starlette décode les en-têtes en latin-1, donc tout octet non-ASCII
    ferait lever un TypeError non capturé à hmac.compare_digest sur des
    `str` — pas seulement pour ne pas fuiter d'information sur la
    position du premier caractère incorrect."""
    expected = os.environ.get("BOT_API_TOKEN")
    if not expected or not hmac.compare_digest(
        (x_bot_token or "").encode("utf-8", "surrogateescape"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Jeton invalide ou absent")


def _position_direction(raw_type: str | None) -> str:
    if raw_type == "POSITION_TYPE_BUY":
        return "achat"
    if raw_type == "POSITION_TYPE_SELL":
        return "vente"
    return "inconnu"


def _read_cache(path: str) -> dict:
    """Lit un fichier de cache JSON écrit par gold_bot.loop — absent,
    illisible, ou JSON invalide compte comme cache vide, jamais
    d'exception (même contrat que gold_bot.state.load_state)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_recent_decisions(path: str, limit: int = RECENT_DECISIONS_LIMIT) -> list[dict]:
    """Dernières décisions marquées par une ouverture de position (simulée
    ou réelle) dans decisions_log.jsonl — voir la spec pour pourquoi seul
    le step "ouverture_simulee" (jamais "clôture_simulee") sert de
    marqueur, et pourquoi ce nom de type reste le même en mode réel.
    Filtre TOUTES les lignes avant de ne garder que les `limit`
    dernières décisions : avec ~1 ligne par minute et des cycles très
    majoritairement "aucune" en production, trancher les lignes brutes
    avant de filtrer viderait quasi toujours ce résultat. Une ligne, une
    entrée ou un step individuellement malformé est ignoré sans jamais
    faire échouer le reste de la réponse."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return []
    decisions = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("action") not in ("simulation_dry_run", "exécuté"):
            continue
        steps = entry.get("steps") or []
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict) or step.get("type") != "ouverture_simulee":
                continue
            decisions.append({
                "timestamp": entry.get("timestamp"),
                "type": step.get("type"),
                "symbol": step.get("symbol"),
                "direction": step.get("direction"),
                "entry": step.get("entry"),
                "stop_loss": step.get("stop_loss"),
                "take_profit": step.get("take_profit"),
            })
    return decisions[-limit:]


def _sanitize_number(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _sanitize_candle(c):
    if not isinstance(c, dict):
        return None
    return {k: _sanitize_number(v) for k, v in c.items()}


@app.get("/status")
def get_status():
    current = state.load_state(state.STATE_PATH)
    circuit_breaker_state = state.load_state(CIRCUIT_BREAKER_STATE_PATH)
    return {
        "kill_switch": current["kill_switch"],
        "dry_run": current["dry_run"],
        "circuit_breaker_day": circuit_breaker_state.get("circuit_breaker_day"),
    }


@app.get("/dashboard")
def dashboard(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)

    balance_cache = _read_cache(LATEST_BALANCE_PATH)
    positions_cache = _read_cache(LATEST_POSITIONS_PATH)
    candles_cache = _read_cache(LATEST_CANDLES_PATH)

    # La forme du cache positions reflète verbatim la réponse HTTP
    # amont (voir gold_bot/loop.py) : si celle-ci dérive un jour vers
    # `null` ou une forme inattendue, on dégrade en liste vide plutôt
    # que de faire échouer toute la réponse /dashboard.
    raw_positions = positions_cache.get("positions", [])
    if not isinstance(raw_positions, list):
        raw_positions = []

    positions = [
        {
            "symbol": p.get("symbol"),
            "direction": _position_direction(p.get("type")),
            "volume": p.get("volume"),
            "open_price": p.get("openPrice"),
            "current_price": p.get("currentPrice"),
            "profit": p.get("profit"),
            "stop_loss": p.get("stopLoss"),
            "take_profit": p.get("takeProfit"),
        }
        for p in raw_positions
        if isinstance(p, dict)
    ]

    raw_candles = candles_cache.get("candles")
    candles = None
    if isinstance(raw_candles, list):
        candles = [c for c in (_sanitize_candle(c) for c in raw_candles) if c is not None]

    return {
        "balance": balance_cache.get("balance"),
        "balance_fetched_at": balance_cache.get("fetched_at"),
        "positions": positions,
        "positions_fetched_at": positions_cache.get("fetched_at"),
        "candles": candles,
        "candles_fetched_at": candles_cache.get("fetched_at"),
        "recent_decisions": _read_recent_decisions(DECISIONS_LOG_PATH),
    }


@app.post("/kill")
def kill(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    current = state.load_state(state.STATE_PATH)
    current["kill_switch"] = True
    state.save_state(current, state.STATE_PATH)
    return {"kill_switch": True}


@app.post("/resume")
def resume(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    current = state.load_state(state.STATE_PATH)
    current["kill_switch"] = False
    state.save_state(current, state.STATE_PATH)
    return {"kill_switch": False}
