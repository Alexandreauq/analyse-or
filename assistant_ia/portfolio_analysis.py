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


def _lit_derniere_analyse(path: str = HISTORY_PATH) -> dict | None:
    """Derniere ligne valide du journal, ou None si absent/vide/toutes les
    lignes corrompues -- jamais d'exception (meme contrat que
    ibkr_bot.journal.read_runs)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lignes = fh.readlines()
    except Exception:
        return None
    for ligne in reversed(lignes):
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            return json.loads(ligne)
        except json.JSONDecodeError:
            continue
    return None


def last_analysis_timestamp(path: str = HISTORY_PATH) -> str | None:
    derniere = _lit_derniere_analyse(path)
    return derniere.get("horodatage") if derniere else None


def _append_history(entry: dict, path: str = HISTORY_PATH) -> bool:
    """Ajoute une ligne JSON au journal append-only. Renvoie False (sans
    jamais lever) si l'ecriture echoue -- meme contrat que
    ibkr_bot.journal.append_run."""
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        print(f"Erreur journalisation analyse portefeuille : {e}")
        return False


def _describe_positions(positions_enrichies: list[dict]) -> str:
    lignes = []
    for item in positions_enrichies:
        position = item["position"]
        fiche = item["fiche"]
        ticker = position.get("ticker", "?")
        if "erreur" in fiche:
            lignes.append(
                f"- {ticker} : quantite={position.get('quantity')}, "
                f"prix d'achat={position.get('buy_price')}, "
                f"date d'achat={position.get('buy_date')} -- donnee indice "
                f"indisponible (ticker retire du site)."
            )
            continue
        alertes = fiche.get("alerts") or []
        alertes_txt = ", ".join(a.get("kind", "?") for a in alertes) if alertes else "aucune"
        lignes.append(
            f"- {ticker} ({fiche.get('name')}, {fiche.get('index')}, "
            f"secteur {fiche.get('sector')}) : quantite={position.get('quantity')}, "
            f"prix d'achat={position.get('buy_price')}, "
            f"date d'achat={position.get('buy_date')}, "
            f"score actuel={fiche.get('score')}, alertes actives={alertes_txt}."
        )
    return "\n".join(lignes) if lignes else "Aucune position."


def _describe_metriques(metriques: dict) -> str:
    lignes = []
    totals = metriques.get("totals")
    if isinstance(totals, dict):
        for devise in ("EUR", "USD"):
            t = totals.get(devise)
            if isinstance(t, dict) and (t.get("value") or t.get("pnlAbs")):
                lignes.append(
                    f"Valeur totale {devise} : {t.get('value')}, "
                    f"P&L latent {devise} : {t.get('pnlAbs')}"
                )
    health = metriques.get("health")
    if isinstance(health, dict):
        lignes.append(
            f"Score moyen du portefeuille (parts egales) : {health.get('avgScore')}, "
            f"positions avec alerte risque : {health.get('riskAlertCount')}, "
            f"positions avec actu majeure : {health.get('majorNewsAlertCount')}"
        )
    concentration = metriques.get("concentration")
    if isinstance(concentration, dict):
        if concentration.get("byIndex"):
            lignes.append(f"Repartition par indice : {concentration.get('byIndex')}")
        if concentration.get("bySector"):
            lignes.append(f"Repartition par secteur : {concentration.get('bySector')}")
    dividendes = metriques.get("dividends_received")
    if isinstance(dividendes, dict) and (dividendes.get("EUR") or dividendes.get("USD")):
        lignes.append(f"Dividendes deja encaisses : {dividendes}")
    projection = metriques.get("projected_dividend_income")
    if isinstance(projection, dict):
        lignes.append(f"Revenu de dividendes projete (TTM) : {projection}")
    return "\n".join(lignes) if lignes else "Aucune metrique transmise."


def _build_prompt(positions_enrichies: list[dict], metriques: dict, derniere_analyse: dict | None) -> str:
    if derniere_analyse:
        resume_precedent = derniere_analyse.get("resultat", {}).get("resume_global", "")
        comparaison_consigne = (
            f"Voici le resume de ta derniere analyse de ce portefeuille, du "
            f"{derniere_analyse.get('horodatage')} : \"{resume_precedent}\". Compare "
            f"la situation actuelle a cette derniere analyse dans le champ "
            f"\"comparaison_precedente\" (ce qui a change : nouvelles lignes, lignes "
            f"sorties, evolution du risque ou de l'actualite)."
        )
    else:
        comparaison_consigne = (
            "Aucune analyse precedente de ce portefeuille n'existe : mets le champ "
            "\"comparaison_precedente\" a null."
        )
    return (
        "Tu analyses le portefeuille actions personnel d'un utilisateur du site "
        "\"AI Investment\" (analyse-or). Voici ses positions actuellement detenues, "
        "avec les donnees du site pour chacune :\n\n"
        f"{_describe_positions(positions_enrichies)}\n\n"
        "Metriques globales deja calculees pour ce portefeuille :\n"
        f"{_describe_metriques(metriques)}\n\n"
        f"{comparaison_consigne}\n\n"
        "Pour les lignes qui te semblent les plus exposees (alerte active, forte "
        "concentration, mouvement de prix marque), utilise web_search pour verifier "
        "s'il y a une actualite recente pertinente -- ne cherche pas systematiquement "
        "sur chaque ligne, concentre les recherches sur ce qui le merite vraiment.\n\n"
        "Pour chaque position, donne une recommandation parmi exactement ces quatre "
        "valeurs : \"garder\", \"renforcer\", \"alleger\", \"vendre\", avec une "
        "justification d'une phrase. Donne aussi un resume global de 2 a 3 phrases sur "
        "l'equilibre du portefeuille (concentration, risque, qualite generale), et une "
        "liste des points d'attention (lignes avec une alerte active ou une actualite "
        "negative trouvee).\n\n"
        "Termine ta reponse par un bloc JSON seul, delimite par des balises de bloc de "
        "code json (```json puis ```), exactement sous cette forme :\n"
        "```json\n"
        "{\n"
        "  \"resume_global\": \"...\",\n"
        "  \"points_attention\": [{\"ticker\": \"...\", \"raison\": \"...\"}],\n"
        "  \"par_ligne\": [{\"ticker\": \"...\", \"reco\": \"garder\", \"justification\": \"...\"}],\n"
        "  \"comparaison_precedente\": \"...\"\n"
        "}\n"
        "```"
    )
