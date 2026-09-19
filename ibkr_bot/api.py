# ibkr_bot/api.py
# Point d'acces HTTPS en LECTURE SEULE du Bot Actions : une unique route,
# GET /dashboard, protegee par jeton. Contrairement a gold_bot/api.py, ce
# module n'a AUCUNE route de mutation (pas de /kill, /resume, /profile) :
# le coupe-circuit d'urgence reste exclusivement SSH-only
# (ibkr_bot/state.json), decision non remise en question par
# docs/superpowers/specs/2026-09-20-ibkr-bot-api-design.md. Ce module
# n'importe jamais le module qui parle au courtier reel (celui qui peut
# passer des ordres reels ou ouvrir une connexion live) : toutes les
# donnees viennent de fichiers ecrits par le dernier batch quotidien
# reussi (voir spec 4.2).
import hmac
import math
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import ibkr_bot.journal as journal
import ibkr_bot.portfolio as portfolio

# Constantes dupliquees ici plutot que lues via les arguments par defaut de
# journal.load_account_snapshot()/read_recent_actions() et
# portfolio.load_positions() : ces fonctions figent leur argument `path`
# par defaut AU MOMENT DE LEUR DEFINITION (comportement standard de
# Python), donc patcher journal.LATEST_ACCOUNT_PATH apres coup ne
# changerait pas ce que load_account_snapshot() lit sans argument. Meme
# motif que gold_bot/api.py, qui duplique ses propres LATEST_BALANCE_PATH
# etc. plutot que de dependre des constantes de gold_bot/loop.py — voir
# test_api_path_constants_match_source_modules, qui garde les deux jeux de
# constantes synchronises.
ACCOUNT_SNAPSHOT_PATH = journal.LATEST_ACCOUNT_PATH
POSITIONS_PATH = portfolio.POSITIONS_PATH
REAL_TRADING_LOG_PATH = journal.REAL_TRADING_LOG_PATH
RECENT_ACTIONS_LIMIT = 50

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://alexandreauq.github.io"],
    allow_methods=["GET"],
    allow_headers=["X-Bot-Token"],
)


def _check_token(x_bot_token: str | None) -> None:
    """Identique a gold_bot.api._check_token : echec ferme si
    IBKR_BOT_API_TOKEN n'est pas configure (pas seulement si le jeton
    fourni est faux), comparaison a temps constant sur les octets UTF-8
    (surrogateescape cote jeton recu, puisque Starlette decode les
    en-tetes en latin-1 et qu'un octet non-ASCII ferait sinon lever un
    TypeError non capture)."""
    expected = os.environ.get("IBKR_BOT_API_TOKEN")
    if not expected or not hmac.compare_digest(
        (x_bot_token or "").encode("utf-8", "surrogateescape"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Jeton invalide ou absent")


def _sanitize_number(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


@app.get("/dashboard")
def dashboard(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)

    account = journal.load_account_snapshot(ACCOUNT_SNAPSHOT_PATH)
    raw_positions = portfolio.load_positions(POSITIONS_PATH)
    raw_actions = journal.read_recent_actions(REAL_TRADING_LOG_PATH, RECENT_ACTIONS_LIMIT)

    positions = [
        {
            "ticker": p.get("ticker"),
            "name": p.get("name"),
            "index": p.get("index"),
            "quantite": p.get("quantite"),
            "prix_entree": _sanitize_number(p.get("prix_execution_reference")),
            "date_entree": p.get("date_entree"),
            "target_exit_price": _sanitize_number(p.get("target_exit_price")),
        }
        for p in raw_positions
        if isinstance(p, dict)
    ]

    # Sanitisation generique (pas seulement les deux champs deja
    # explicitement lus ci-dessus pour les positions) : les actions
    # transportent plusieurs autres champs numeriques issus de calculs
    # reels (ecart_paper_pct, taux_de_change...) qui pourraient un jour
    # produire NaN/Inf. FastAPI ne passe PAS allow_nan=False par defaut
    # (voir la note en tete de fichier et Global Constraints du plan).
    actions = [
        {k: _sanitize_number(v) for k, v in a.items()}
        for a in raw_actions
    ]

    return {
        "balance": _sanitize_number(account.get("base_cash")),
        "balance_fetched_at": account.get("fetched_at"),
        "positions": positions,
        "actions": actions,
    }
