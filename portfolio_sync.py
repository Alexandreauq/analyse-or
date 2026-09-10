# portfolio_sync.py
# Synchronisation en lecture seule des positions Interactive Brokers via
# le Flex Web Service — voir
# docs/superpowers/specs/2026-09-10-portefeuille-ibkr-sync-design.md.
# Le jeton utilisé ne donne accès qu'à des rapports Flex configurés côté
# IBKR : il est structurellement incapable de passer un ordre.
import json
import os
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

FLEX_BASE_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"


def fetch_flex_reference_code(token: str, query_id: str) -> str:
    """Étape 1 du Flex Web Service : déclenche la génération du rapport.
    Renvoie le ReferenceCode à passer à fetch_flex_statement. Lève une
    RuntimeError si IBKR renvoie une erreur explicite (jeton expiré/
    révoqué, query_id invalide) ou une réponse sans ReferenceCode ni
    ErrorCode (format inattendu)."""
    resp = requests.get(
        f"{FLEX_BASE_URL}/SendRequest",
        params={"t": token, "q": query_id, "v": "3"},
        timeout=15,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    error_code = root.findtext("ErrorCode")
    if error_code:
        error_message = root.findtext("ErrorMessage") or "erreur IBKR inconnue"
        raise RuntimeError(f"Flex Query SendRequest a échoué ({error_code}) : {error_message}")
    reference_code = root.findtext("ReferenceCode")
    if not reference_code:
        raise RuntimeError("Flex Query SendRequest : réponse sans ReferenceCode ni ErrorCode")
    return reference_code


def fetch_flex_statement(
    token: str, reference_code: str, max_attempts: int = 5, retry_delay_s: float = 3.0
) -> str:
    """Étape 2 : récupère le rapport généré. IBKR peut mettre plusieurs
    secondes à le préparer — dans ce cas la réponse est un texte brut
    contenant "Statement generation in progress" (pas du XML bien
    formé), et on retente après retry_delay_s. Lève une RuntimeError si
    le rapport n'est toujours pas prêt après max_attempts tentatives, ou
    si IBKR renvoie une erreur explicite."""
    for attempt in range(max_attempts):
        resp = requests.get(
            f"{FLEX_BASE_URL}/GetStatement",
            params={"t": token, "q": reference_code, "v": "3"},
            timeout=15,
        )
        resp.raise_for_status()
        text = resp.text
        if "Statement generation in progress" in text:
            if attempt < max_attempts - 1:
                time.sleep(retry_delay_s)
            continue
        root = ET.fromstring(text)
        error_code = root.findtext("ErrorCode")
        if error_code:
            error_message = root.findtext("ErrorMessage") or "erreur IBKR inconnue"
            raise RuntimeError(f"Flex Query GetStatement a échoué ({error_code}) : {error_message}")
        return text
    raise RuntimeError(
        f"Flex Query GetStatement : rapport toujours en préparation après {max_attempts} tentatives"
    )


def _to_float(raw):
    """Parse un attribut XML IBKR potentiellement absent ou vide (ex. un
    ETF/obligation transféré sans coût de revient connu) sans lever —
    une position incomplète dégrade son pnl_pct à None plutôt que de
    faire échouer toute la synchronisation."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_open_positions(xml_text: str) -> list[dict]:
    """Extrait chaque <OpenPosition> "résumé" (levelOfDetail=SUMMARY) du
    rapport Flex en dict. Ne construit jamais les montants absolus
    (quantité, valeur, coût de revient, P&L en devise) — seul un
    pourcentage de performance est calculé et conservé, car ce fichier
    est publié publiquement (voir la section Confidentialité du spec).
    Une section OpenPositions absente ou vide renvoie une liste vide
    (compte sans position ouverte — cas valide, pas une erreur). Une
    ligne "LOT" (détail par lot fiscal, si la Flex Query est configurée
    en détail lot plutôt que résumé) est ignorée pour ne pas compter
    une même position plusieurs fois."""
    root = ET.fromstring(xml_text)
    positions = []
    for el in root.iter("OpenPosition"):
        level = (el.get("levelOfDetail") or "SUMMARY").upper()
        if level != "SUMMARY":
            continue
        cost_basis_value = _to_float(el.get("costBasisMoney"))
        unrealized_pnl = _to_float(el.get("fifoPnlUnrealized"))
        pnl_pct = None
        if cost_basis_value and unrealized_pnl is not None:
            pnl_pct = unrealized_pnl / abs(cost_basis_value) * 100
        positions.append({
            "ibkr_symbol": el.get("symbol"),
            "description": el.get("description"),
            "currency": el.get("currency"),
            "pnl_pct": pnl_pct,
        })
    return positions


def match_tickers(positions: list[dict], companies: list[dict]) -> list[dict]:
    """Ajoute matched_ticker à chaque position, par recherche du symbole
    IBKR (insensible à la casse) contre les tickers suivis avec leur
    suffixe Yahoo (.PA/.DE) retiré. Si plusieurs entreprises suivies
    partagent le même symbole nu (ex. MRK.DE et MRK), aucune des deux
    n'est retenue — mieux vaut ne pas enrichir que de rattacher un vrai
    montant détenu à la mauvaise entreprise. None si aucune
    correspondance (ou correspondance ambiguë) : pas une erreur, attendu
    pour tout ce qui n'est pas dans les indices suivis."""
    bare_counts = {}
    for c in companies:
        bare = c["ticker"].split(".")[0].upper()
        bare_counts[bare] = bare_counts.get(bare, 0) + 1
    bare_to_ticker = {}
    for c in companies:
        ticker = c["ticker"]
        bare = ticker.split(".")[0].upper()
        if bare_counts[bare] == 1:
            bare_to_ticker[bare] = ticker
    result = []
    for p in positions:
        symbol = (p.get("ibkr_symbol") or "").upper()
        result.append({**p, "matched_ticker": bare_to_ticker.get(symbol)})
    return result


REAL_PORTFOLIO_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "real_portfolio.json"
)

INDICES_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "indices.json"
)


def _load_existing_real_portfolio() -> dict:
    """État de repli si le fichier n'existe pas encore ou est illisible —
    jamais d'exception au démarrage du script."""
    try:
        with open(REAL_PORTFOLIO_JSON_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"updated": None, "sync_status": "ok", "sync_error": None, "positions": []}


def _load_tracked_companies() -> list[dict]:
    """Companies suivies pour le rapprochement des tickers — liste vide
    si indices.json est absent/illisible (le rapprochement échoue alors
    pour toutes les positions, sans bloquer la synchronisation)."""
    try:
        with open(INDICES_JSON_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)["companies"]
    except Exception:
        return []


def _write_real_portfolio(payload: dict) -> None:
    os.makedirs(os.path.dirname(REAL_PORTFOLIO_JSON_PATH), exist_ok=True)
    with open(REAL_PORTFOLIO_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, allow_nan=False)


def main():
    token = os.environ.get("IBKR_FLEX_TOKEN")
    query_id = os.environ.get("IBKR_FLEX_QUERY_ID")
    payload = _load_existing_real_portfolio()
    payload["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not token or not query_id:
        payload["sync_status"] = "not_configured"
        payload["sync_error"] = None
        _write_real_portfolio(payload)
        print("Synchronisation IBKR non configurée (secrets absents) — étape attendue avant la configuration du compte.")
        return

    try:
        reference_code = fetch_flex_reference_code(token, query_id)
        xml_text = fetch_flex_statement(token, reference_code)
        positions = parse_open_positions(xml_text)
        companies = _load_tracked_companies()
        positions = match_tickers(positions, companies)
    except requests.exceptions.RequestException as e:
        # Le message par défaut d'une exception requests (ex. HTTPError)
        # inclut l'URL complète de la requête, donc le jeton IBKR passé en
        # paramètre — jamais stocker ce message brut dans real_portfolio.json,
        # publié publiquement par ce dépôt. Le détail complet part quand même
        # sur stdout (traceback compris) : GitHub Actions masque automatiquement
        # la valeur du secret dans tous les logs d'un job qui la référence via
        # env:, donc seul le fichier persisté a besoin d'être assaini ici.
        status = getattr(getattr(e, "response", None), "status_code", None)
        payload["sync_status"] = "error"
        payload["sync_error"] = (
            f"Erreur réseau IBKR (HTTP {status})" if status else "Erreur réseau IBKR (pas de réponse)"
        )
        _write_real_portfolio(payload)
        print(f"Erreur synchronisation IBKR (réseau) : HTTP {status}")
        traceback.print_exc()
        return
    except Exception as e:
        payload["sync_status"] = "error"
        payload["sync_error"] = str(e)
        _write_real_portfolio(payload)
        print(f"Erreur synchronisation IBKR : {e}")
        traceback.print_exc()
        return

    payload["sync_status"] = "ok"
    payload["sync_error"] = None
    payload["positions"] = positions
    _write_real_portfolio(payload)
    print(f"Synchronisation IBKR réussie : {len(positions)} position(s).")


if __name__ == "__main__":
    main()
