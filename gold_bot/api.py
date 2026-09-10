# gold_bot/api.py
# Point d'accès HTTPS minimal pour l'interrupteur d'urgence — la SEULE
# action possible via ce point d'accès est d'arrêter le bot (jamais de
# route qui l'active, augmente une position, ou déclenche un ordre :
# ces actions n'existent tout simplement pas ici. Voir
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import gold_bot.state as state

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://alexandreauq.github.io"],
    allow_methods=["GET", "POST"],
    allow_headers=["X-Bot-Token"],
)


def _check_token(x_bot_token: str | None) -> None:
    """Refuse par défaut si BOT_API_TOKEN n'est pas configuré (échec
    fermé, jamais ouvert) — pas seulement si le jeton fourni est faux."""
    expected = os.environ.get("BOT_API_TOKEN")
    if not expected or x_bot_token != expected:
        raise HTTPException(status_code=401, detail="Jeton invalide ou absent")


@app.get("/status")
def get_status():
    current = state.load_state(state.STATE_PATH)
    return {
        "kill_switch": current["kill_switch"],
        "dry_run": current["dry_run"],
        "circuit_breaker_day": current.get("circuit_breaker_day"),
        "circuit_breaker_starting_balance": current.get("circuit_breaker_starting_balance"),
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
