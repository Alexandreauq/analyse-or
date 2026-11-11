# assistant_ia/portfolio_analysis.py
# Analyse IA du portefeuille manuel (voir spec 2026-11-11) : un appel
# structure, pas une conversation -- un seul appel Claude avec recherche
# web, JSON extrait en fin de reponse (meme esprit defensif que
# gold_bot/ai_shadow.py::_parse_verdict, mais un bloc balise ```json plutot
# qu'une regex a accolades : le JSON ici contient des tableaux d'objets
# imbriques, qu'une regex non recursive ne capturerait pas). Archive dans
# un historique append-only pour permettre la comparaison "depuis la
# derniere fois". Lecture seule sur indices.json -- seul fichier ecrit :
# HISTORY_PATH, jamais commite (voir .gitignore).
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import assistant_ia.data_sources as data_sources
import assistant_ia.tools as tools
import assistant_ia.usage as usage

MODEL = usage.MODEL_SONNET_5_5
MAX_TOKENS = 8000
REQUEST_TIMEOUT_SECONDS = 60.0
WEB_SEARCH_MAX_USES = 4
MAX_POSITIONS = 50
HISTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_analysis_history.jsonl")

RECO_VALIDES = {"garder", "renforcer", "alleger", "vendre"}
_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_resultat(texte: str) -> dict | None:
    """Extrait le DERNIER bloc ```json...``` du texte (si le modele a
    pense a voix haute avant, ou produit plusieurs blocs par erreur, seul
    le dernier compte). None si absent, JSON invalide, ou si les cles
    obligatoires (resume_global, par_ligne) manquent -- jamais
    d'exception propagee."""
    matches = _JSON_BLOCK_RE.findall(texte)
    if not matches:
        return None
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if "resume_global" not in parsed or "par_ligne" not in parsed:
        return None
    parsed.setdefault("points_attention", [])
    parsed.setdefault("comparaison_precedente", None)
    if not isinstance(parsed["par_ligne"], list):
        return None
    for ligne in parsed["par_ligne"]:
        if not isinstance(ligne, dict):
            continue
        reco = ligne.get("reco")
        if isinstance(reco, str):
            reco = reco.strip().lower()
            ligne["reco"] = reco if reco in RECO_VALIDES else "a_verifier"
        else:
            ligne["reco"] = "a_verifier"
    return parsed


def _enrichit_positions(positions: list[dict], *, indices_source) -> list[dict]:
    """Associe a chaque position sa fiche societe a jour (score, secteur,
    alertes) -- reutilise fiche_entreprise tel quel, jamais reimplemente.
    Un ticker introuvable (retire du site) donne la fiche d'erreur deja
    renvoyee par fiche_entreprise ; la position est quand meme incluse
    (voir _describe_positions), jamais exclue silencieusement."""
    enrichies = []
    for position in positions:
        fiche = tools.fiche_entreprise(position.get("ticker", ""), indices_source=indices_source)
        enrichies.append({"position": position, "fiche": fiche})
    return enrichies
