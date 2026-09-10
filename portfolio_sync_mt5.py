# portfolio_sync_mt5.py
# Synchronisation en lecture seule des positions MetaTrader 5 (compte
# Vantage) via l'API REST MetaApi.cloud — voir
# docs/superpowers/specs/2026-09-10-portefeuille-mt5-sync-design.md.
# Le compte MT5 est enregistré chez MetaApi avec le mot de passe
# investisseur : il est structurellement incapable de passer un ordre.
import json
import os
import traceback
from datetime import datetime, timezone

import requests

DEFAULT_MT5_REGION = "london"


def _base_url(region: str) -> str:
    return f"https://mt-client-api-v1.{region}.agiliumtrade.ai"


def fetch_positions(token: str, account_id: str, region: str = DEFAULT_MT5_REGION) -> list[dict]:
    """Appelle l'endpoint MetaApi qui renvoie les positions ouvertes du
    compte MT5. Contrairement à IBKR, une seule requête HTTP synchrone
    suffit — pas de génération asynchrone à attendre. Lève une
    requests.exceptions.RequestException (ex. HTTPError) sur toute
    erreur HTTP (401 jeton invalide, 404 compte introuvable, etc.)."""
    resp = requests.get(
        f"{_base_url(region)}/users/current/accounts/{account_id}/positions",
        headers={"auth-token": token},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def to_public_positions(raw_positions: list[dict]) -> list[dict]:
    """Transforme chaque position brute MetaApi en dict public minimal
    — ne construit jamais profit/volume/openPrice/currentPrice ni
    aucun champ numérique, ce fichier étant publié publiquement (voir
    la section Confidentialité du spec). Seul le signe du profit est
    conservé, jamais sa valeur. profit == 0 ou absent compte comme
    "positif" (convention arbitraire mais sans conséquence : aucun
    montant n'est de toute façon affiché)."""
    positions = []
    for p in raw_positions:
        raw_type = p.get("type")
        if raw_type == "POSITION_TYPE_BUY":
            position_type = "achat"
        elif raw_type == "POSITION_TYPE_SELL":
            position_type = "vente"
        else:
            position_type = "inconnu"
        pnl_sign = "positif" if p.get("profit", 0) >= 0 else "négatif"
        positions.append({
            "symbol": p.get("symbol"),
            "type": position_type,
            "pnl_sign": pnl_sign,
        })
    return positions


REAL_PORTFOLIO_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "real_portfolio_mt5.json"
)


def _load_existing_real_portfolio() -> dict:
    """État de repli si le fichier n'existe pas encore ou est illisible —
    jamais d'exception au démarrage du script."""
    try:
        with open(REAL_PORTFOLIO_JSON_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"updated": None, "sync_status": "ok", "sync_error": None, "positions": []}


def _write_real_portfolio(payload: dict) -> None:
    os.makedirs(os.path.dirname(REAL_PORTFOLIO_JSON_PATH), exist_ok=True)
    with open(REAL_PORTFOLIO_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, allow_nan=False)


def main():
    token = os.environ.get("METAAPI_TOKEN")
    account_id = os.environ.get("METAAPI_ACCOUNT_ID")
    region = os.environ.get("METAAPI_REGION") or DEFAULT_MT5_REGION
    payload = _load_existing_real_portfolio()
    payload["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not token or not account_id:
        payload["sync_status"] = "not_configured"
        payload["sync_error"] = None
        _write_real_portfolio(payload)
        print("Synchronisation MT5 non configurée (secrets absents) — étape attendue avant la configuration du compte.")
        return

    try:
        raw_positions = fetch_positions(token, account_id, region)
        positions = to_public_positions(raw_positions)
    except requests.exceptions.RequestException as e:
        # Même précaution que pour IBKR : ne jamais persister le message
        # brut d'une exception requests dans un fichier publié
        # publiquement. Le détail complet part quand même sur stdout
        # (traceback compris) : GitHub Actions masque automatiquement
        # la valeur du secret dans tous les logs d'un job qui la
        # référence via env:.
        status = getattr(getattr(e, "response", None), "status_code", None)
        payload["sync_status"] = "error"
        payload["sync_error"] = (
            f"Erreur réseau MT5 (HTTP {status})" if status else "Erreur réseau MT5 (pas de réponse)"
        )
        _write_real_portfolio(payload)
        print(f"Erreur synchronisation MT5 (réseau) : HTTP {status}")
        traceback.print_exc()
        return
    except Exception as e:
        payload["sync_status"] = "error"
        payload["sync_error"] = f"Erreur interne de synchronisation MT5 ({type(e).__name__})"
        _write_real_portfolio(payload)
        print(f"Erreur synchronisation MT5 : {e}")
        traceback.print_exc()
        return

    payload["sync_status"] = "ok"
    payload["sync_error"] = None
    payload["positions"] = positions
    _write_real_portfolio(payload)
    print(f"Synchronisation MT5 réussie : {len(positions)} position(s).")


if __name__ == "__main__":
    main()
