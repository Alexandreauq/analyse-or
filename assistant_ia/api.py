# assistant_ia/api.py
# Point d'acces HTTPS de l'assistant IA : une seule route, POST /ask,
# protegee par jeton, en streaming SSE. Lecture seule — ce module
# n'ecrit jamais dans positions.json/state.json/aucun fichier d'un
# autre module (voir Global Constraints du plan). Structure calquee sur
# gold_bot/api.py/ibkr_bot/api.py.
import hmac
import json
import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import assistant_ia.assistant as assistant
import assistant_ia.usage as usage

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://alexandreauq.github.io"],
    allow_methods=["POST"],
    allow_headers=["X-Bot-Token", "Content-Type"],
)


def _check_token(x_bot_token: str | None) -> None:
    """Identique a gold_bot.api._check_token : echec ferme, comparaison
    a temps constant sur les octets UTF-8."""
    expected = os.environ.get("AI_ASSISTANT_API_TOKEN")
    if not expected or not hmac.compare_digest(
        (x_bot_token or "").encode("utf-8", "surrogateescape"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Jeton invalide ou absent")


def _formate_evenements_sse(evenements):
    for evenement in evenements:
        yield f"data: {json.dumps(evenement, ensure_ascii=False)}\n\n"


@app.post("/ask")
async def ask(request: Request, x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    if usage.budget_exceeded():
        raise HTTPException(status_code=429, detail="Plafond quotidien de l'assistant atteint, reessaie demain")

    payload = await request.json()
    question = payload.get("question", "")
    history = payload.get("history", [])
    manual_positions = payload.get("manual_positions")

    evenements = assistant.run_assistant_loop(question, history, manual_positions)
    return StreamingResponse(_formate_evenements_sse(evenements), media_type="text/event-stream")
