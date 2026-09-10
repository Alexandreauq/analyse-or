# gold_bot/api.py
# Point d'accès HTTPS minimal pour l'interrupteur d'urgence — la SEULE
# action possible via ce point d'accès est d'arrêter le bot (jamais de
# route qui l'active, augmente une position, ou déclenche un ordre :
# ces actions n'existent tout simplement pas ici. Voir
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import hmac
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import gold_bot.state as state

CIRCUIT_BREAKER_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "circuit_breaker_state.json")

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


@app.get("/status")
def get_status():
    current = state.load_state(state.STATE_PATH)
    circuit_breaker_state = state.load_state(CIRCUIT_BREAKER_STATE_PATH)
    return {
        "kill_switch": current["kill_switch"],
        "dry_run": current["dry_run"],
        "circuit_breaker_day": circuit_breaker_state.get("circuit_breaker_day"),
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
