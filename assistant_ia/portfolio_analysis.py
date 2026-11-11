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
# Large : jusqu'a WEB_SEARCH_MAX_USES recherches + MAX_TOKENS de sortie sur
# MAX_POSITIONS lignes peuvent depasser la minute. Un timeout cote client
# n'annule pas la facturation cote Anthropic mais saute usage.record_usage
# -> le budget quotidien partage sous-compterait la depense reelle.
REQUEST_TIMEOUT_SECONDS = 180.0
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
    parsed.setdefault("comparaison_precedente", None)
    sante = parsed.get("sante_globale")
    if not isinstance(sante, (int, float)) or isinstance(sante, bool):
        sante = None
    elif isinstance(sante, float):
        sante = int(sante)
    parsed["sante_globale"] = sante
    if not isinstance(parsed["par_ligne"], list):
        return None
    # Les entrees non-dict (ex. chaine nue emise par erreur) sont retirees,
    # pas seulement ignorees : sinon elles atteindraient le front tel quel.
    parsed["par_ligne"] = [ligne for ligne in parsed["par_ligne"] if isinstance(ligne, dict)]
    for ligne in parsed["par_ligne"]:
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


def derniere_analyse_complete(path: str = HISTORY_PATH) -> dict | None:
    """Le resultat complet (pas seulement l'horodatage) de la derniere
    analyse reussie, ou None si aucune n'existe encore. Reutilise
    _lit_derniere_analyse, deja tolerante aux fichiers absents/corrompus."""
    derniere = _lit_derniere_analyse(path)
    return derniere.get("resultat") if derniere else None


def historique_sante(path: str = HISTORY_PATH, limite: int = 200) -> list[dict]:
    """Liste chronologique (plus ancienne en premier) de
    {"horodatage": str, "sante_globale": int | None} pour chaque analyse
    du journal -- lecture seule, jamais les champs lourds (texte, snapshot
    de positions). Une ligne corrompue est ignoree (meme contrat que
    _lit_derniere_analyse), jamais une exception. `limite` borne le volume
    renvoye (les plus recentes), pas de pagination necessaire pour un usage
    personnel a cette frequence."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lignes = fh.readlines()
    except Exception:
        return []
    points = []
    for ligne in lignes:
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            entree = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        if not isinstance(entree, dict):
            continue
        resultat = entree.get("resultat") or {}
        points.append({
            "horodatage": entree.get("horodatage"),
            "sante_globale": resultat.get("sante_globale") if isinstance(resultat, dict) else None,
        })
    return points[-limite:]


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
        # kind "info" = marqueur neutre "aucun signal actif" -- pas une alerte.
        alertes = [a for a in (fiche.get("alerts") or []) if isinstance(a, dict) and a.get("kind") != "info"]
        alertes_txt = ", ".join(a.get("kind", "?") for a in alertes) if alertes else "aucune"
        prix_actuel = fiche.get("current_price")
        prix_actuel_txt = (
            f", prix actuel={prix_actuel}"
            if isinstance(prix_actuel, (int, float)) and not isinstance(prix_actuel, bool)
            else ""
        )
        lignes.append(
            f"- {ticker} ({fiche.get('name')}, {fiche.get('index')}, "
            f"secteur {fiche.get('sector')}) : quantite={position.get('quantity')}, "
            f"prix d'achat={position.get('buy_price')}{prix_actuel_txt}, "
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
    attribution = metriques.get("attribution")
    if isinstance(attribution, list):
        for entree in attribution:
            if not isinstance(entree, dict):
                continue
            pnl_abs, pnl_pct = entree.get("pnlAbs"), entree.get("pnlPct")
            if isinstance(pnl_abs, (int, float)) and isinstance(pnl_pct, (int, float)):
                lignes.append(
                    f"Attribution de performance : {entree.get('ticker')} : "
                    f"P&L {pnl_abs:+.2f} ({pnl_pct:+.1f}%)"
                )
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
        "Pour chaque position, donne :\n"
        "- une recommandation parmi exactement ces quatre valeurs : \"garder\", "
        "\"renforcer\", \"alleger\", \"vendre\" ;\n"
        "- une analyse complete (plusieurs phrases, pas une seule) qui repond "
        "explicitement a la question : au prix d'achat de l'utilisateur compare au "
        "prix actuel, est-ce toujours une bonne idee de garder cette ligne a ce "
        "niveau ? Mentionne les actualites negatives recentes que tu as trouvees "
        "s'il y en a, et dis explicitement \"aucune actualite negative notable "
        "trouvee\" si ta recherche n'en a pas trouve. Mentionne aussi les points "
        "positifs pertinents (resultats, catalyseurs) s'il y en a.\n\n"
        "Donne aussi :\n"
        "- un score global \"sante_globale\", un entier de -100 (portefeuille tres "
        "risque ou mal positionne) a +100 (portefeuille sain et bien positionne), "
        "ta propre appreciation globale ;\n"
        "- un resume global de 2 a 3 phrases sur l'equilibre du portefeuille "
        "(concentration, risque, qualite generale).\n\n"
        "Termine ta reponse par un bloc JSON seul, delimite par des balises de bloc de "
        "code json (```json puis ```), exactement sous cette forme :\n"
        "```json\n"
        "{\n"
        "  \"resume_global\": \"...\",\n"
        "  \"sante_globale\": 0,\n"
        "  \"par_ligne\": [{\"ticker\": \"...\", \"reco\": \"garder\", \"analyse\": \"...\"}],\n"
        "  \"comparaison_precedente\": \"...\"\n"
        "}\n"
        "```"
    )


def run_portfolio_analysis(
    payload: dict, *, client=None, indices_source=data_sources.fetch_indices_data,
    history_path: str = HISTORY_PATH,
) -> dict:
    """Point d'entree appele par la route POST /portfolio-analysis.
    `client` injectable (jamais de vrai appel reseau dans les tests, meme
    principe que assistant.run_assistant_loop). Toute panne reseau/API est
    capturee ici, jamais propagee -- la route n'a jamais a gerer une
    exception venant de ce module."""
    positions = payload.get("positions") or []
    metriques = {
        "totals": payload.get("totals"),
        "concentration": payload.get("concentration"),
        "health": payload.get("health"),
        "attribution": payload.get("attribution"),
        "dividends_received": payload.get("dividends_received"),
        "projected_dividend_income": payload.get("projected_dividend_income"),
    }
    try:
        positions_enrichies = _enrichit_positions(positions, indices_source=indices_source)
    except Exception as e:
        return {"disponible": False, "erreur": f"{type(e).__name__}: {e}", "resultat": None}
    derniere_analyse = _lit_derniere_analyse(history_path)
    prompt = _build_prompt(positions_enrichies, metriques, derniere_analyse)

    if client is None:
        import anthropic
        client = anthropic.Anthropic(timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            output_config={"effort": "medium"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES}],
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        return {"disponible": False, "erreur": f"{type(e).__name__}: {e}", "resultat": None}

    usage.record_usage(response.usage.input_tokens, response.usage.output_tokens, model=MODEL)
    texte = "".join(bloc.text for bloc in response.content if bloc.type == "text")
    resultat = _parse_resultat(texte)
    if resultat is None:
        return {"disponible": False, "erreur": "reponse IA non interpretable, reessaie", "resultat": None}

    resultat["horodatage"] = now_iso()
    _append_history({
        "horodatage": resultat["horodatage"],
        "positions_snapshot": positions,
        "resultat": resultat,
    }, history_path)
    return {"disponible": True, "erreur": None, "resultat": resultat}
