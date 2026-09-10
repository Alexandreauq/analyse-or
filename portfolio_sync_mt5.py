# portfolio_sync_mt5.py
# Synchronisation en lecture seule des positions MetaTrader 5 (compte
# Vantage) via l'API REST MetaApi.cloud — voir
# docs/superpowers/specs/2026-09-10-portefeuille-mt5-sync-design.md.
# Le compte MT5 est enregistré chez MetaApi avec le mot de passe
# investisseur : il est structurellement incapable de passer un ordre.
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
        position_type = "achat" if p.get("type") == "POSITION_TYPE_BUY" else "vente"
        pnl_sign = "positif" if p.get("profit", 0) >= 0 else "négatif"
        positions.append({
            "symbol": p.get("symbol"),
            "type": position_type,
            "pnl_sign": pnl_sign,
        })
    return positions
