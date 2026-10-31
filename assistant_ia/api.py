# assistant_ia/api.py
# Point d'acces HTTPS de l'assistant IA : une seule route, POST /ask,
# protegee par jeton, en streaming SSE. Lecture seule — ce module
# n'ecrit jamais dans positions.json/state.json/aucun fichier d'un
# autre module (voir Global Constraints du plan). Structure calquee sur
# gold_bot/api.py/ibkr_bot/api.py.
import hmac
import json
import os
import subprocess

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import assistant_ia.assistant as assistant
import assistant_ia.limits as limits
import assistant_ia.usage as usage

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://alexandreauq.github.io"],
    allow_methods=["POST", "GET"],
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


# Garde-fous de charge (voir assistant_ia/limits.py). Réglables par variables
# d'environnement sans modifier le code.
MAX_CONCURRENT = int(os.environ.get("AI_ASSISTANT_MAX_CONCURRENT", "4"))
RATE_MAX_PAR_MINUTE = int(os.environ.get("AI_ASSISTANT_RATE_PAR_MINUTE", "10"))
MAX_QUESTION_CHARS = 500
MAX_HISTORY_MESSAGES = 12

_concurrence = limits.ConcurrencyLimiter(MAX_CONCURRENT)
_frequence = limits.RateLimiter(RATE_MAX_PAR_MINUTE, 60)

# Garde-fous dédiés à /financial-analysis -- séparés de ceux de /ask
# (une conversation ouverte et un ticker ponctuel n'ont pas le même
# profil d'abus). Concurrence à 1 : évite toute course entre deux
# sous-processus qui committeraient en même temps (voir
# indices_score.py::_git_commit_and_push, qui fait déjà un pull --rebase
# avant de pousser, mais une seule génération à la fois reste plus sûr
# et plus simple qu'un retry).
ANALYSE_MAX_CONCURRENT = int(os.environ.get("AI_FINANCIAL_ANALYSIS_MAX_CONCURRENT", "1"))
ANALYSE_RATE_MAX_PAR_MINUTE = int(os.environ.get("AI_FINANCIAL_ANALYSIS_RATE_PAR_MINUTE", "10"))
ANALYSE_SUBPROCESS_TIMEOUT_S = 150  # generate_financial_analysis a son propre timeout HTTP a 120s

_concurrence_analyse = limits.ConcurrencyLimiter(ANALYSE_MAX_CONCURRENT)
_frequence_analyse = limits.RateLimiter(ANALYSE_RATE_MAX_PAR_MINUTE, 60)

_REPO_ROOT = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))


def _generate_one_subprocess(ticker: str) -> dict:
    """Délègue toute la génération + écriture + commit git à
    indices_score.py --generate-one, en sous-processus -- ce module
    reste ainsi "lecture seule" (voir Global Constraints du plan). Lève
    (jamais n'avale) sur sortie invalide/timeout : l'appelant (la route)
    décide du repli visiteur."""
    resultat = subprocess.run(
        ["python3", "indices_score.py", "--generate-one", ticker],
        cwd=_REPO_ROOT, capture_output=True, text=True,
        timeout=ANALYSE_SUBPROCESS_TIMEOUT_S,
    )
    return json.loads(resultat.stdout)


def _formate_evenements_sse(evenements):
    for evenement in evenements:
        yield f"data: {json.dumps(evenement, ensure_ascii=False)}\n\n"


def _flux_protege(evenements):
    """Libère la place de concurrence à la fin du flux, en erreur comme en cas
    de déconnexion du visiteur (le générateur est alors fermé)."""
    try:
        yield from _formate_evenements_sse(evenements)
    finally:
        _concurrence.libere()


@app.post("/ask")
async def ask(request: Request, x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    if usage.budget_exceeded():
        raise HTTPException(status_code=429, detail="Plafond quotidien de l'assistant atteint, reessaie demain")

    adresse = request.client.host if request.client else "inconnue"
    if not _frequence.autorise(adresse):
        raise HTTPException(status_code=429, detail="Trop de questions en peu de temps : réessaie dans une minute.")

    payload = await request.json()
    question = payload.get("question", "")
    if not isinstance(question, str) or not question.strip():
        raise HTTPException(status_code=400, detail="Question vide.")
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(status_code=400, detail=f"Question trop longue ({MAX_QUESTION_CHARS} caractères maximum).")
    history = payload.get("history", [])
    if not isinstance(history, list):
        history = []
    history = history[-MAX_HISTORY_MESSAGES:]
    manual_positions = payload.get("manual_positions")
    niveau = payload.get("niveau", assistant.NIVEAU_DEFAUT)
    contexte = payload.get("contexte")
    if not isinstance(contexte, str):
        contexte = None

    if not _concurrence.tente():
        raise HTTPException(status_code=503, detail="L'assistant est très sollicité en ce moment : réessaie dans un instant.")
    try:
        evenements = assistant.run_assistant_loop(
            question, history, manual_positions, niveau=niveau, contexte=contexte)
    except Exception:
        _concurrence.libere()
        raise
    return StreamingResponse(_flux_protege(evenements), media_type="text/event-stream")


@app.get("/financial-analysis/{ticker}")
def financial_analysis(ticker: str):
    if not _frequence_analyse.autorise(ticker):
        raise HTTPException(status_code=429, detail="Trop de demandes pour ce ticker : réessaie dans une minute.")
    if not _concurrence_analyse.tente():
        raise HTTPException(status_code=503, detail="Une génération est déjà en cours : réessaie dans un instant.")
    try:
        try:
            resultat = _generate_one_subprocess(ticker)
            html = resultat.get("html")
            disponible = bool(resultat.get("html"))
        except Exception:
            html, disponible = None, False
    finally:
        _concurrence_analyse.libere()
    return {"html": html, "disponible": disponible}
