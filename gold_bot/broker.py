# gold_bot/broker.py
# Appels à l'API de trading MetaApi — compte connecté avec le mot de
# passe de trading réel, distinct du compte lecture seule utilisé par
# portfolio_sync_mt5.py. Voir
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import requests

DEFAULT_MT5_REGION = "london"


def _base_url(region: str) -> str:
    return f"https://mt-client-api-v1.{region}.agiliumtrade.ai"


def get_account_information(token: str, account_id: str, region: str = DEFAULT_MT5_REGION) -> dict:
    """`balance` (capital réalisé) ET `equity` (balance + P&L flottant des
    positions ouvertes) en un seul appel MetaApi — le coupe-circuit a
    besoin d'`equity` pour voir une position ouverte en train de perdre
    avant sa clôture (voir gold_bot.bot.decide_and_act)."""
    resp = requests.get(
        f"{_base_url(region)}/users/current/accounts/{account_id}/account-information",
        headers={"auth-token": token},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"balance": data["balance"], "equity": data["equity"]}


def get_account_balance(token: str, account_id: str, region: str = DEFAULT_MT5_REGION) -> float:
    return get_account_information(token, account_id, region)["balance"]


def get_open_positions(token: str, account_id: str, region: str = DEFAULT_MT5_REGION) -> list[dict]:
    resp = requests.get(
        f"{_base_url(region)}/users/current/accounts/{account_id}/positions",
        headers={"auth-token": token},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_symbol_specification(token: str, account_id: str, symbol: str,
                              region: str = DEFAULT_MT5_REGION) -> dict:
    resp = requests.get(
        f"{_base_url(region)}/users/current/accounts/{account_id}/symbols/{symbol}/specification",
        headers={"auth-token": token},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def place_market_order(token: str, account_id: str, symbol: str, direction: str, volume: float,
                        stop_loss: float, take_profit: float, region: str = DEFAULT_MT5_REGION) -> dict:
    """direction : "achat" ou "vente" (convention interne du projet) —
    traduit en ORDER_TYPE_BUY/ORDER_TYPE_SELL attendu par MetaApi."""
    action_type = "ORDER_TYPE_BUY" if direction == "achat" else "ORDER_TYPE_SELL"
    resp = requests.post(
        f"{_base_url(region)}/users/current/accounts/{account_id}/trade",
        headers={"auth-token": token},
        json={
            "actionType": action_type,
            "symbol": symbol,
            "volume": volume,
            "stopLoss": stop_loss,
            "takeProfit": take_profit,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def close_position(token: str, account_id: str, position_id: str,
                    region: str = DEFAULT_MT5_REGION) -> dict:
    resp = requests.post(
        f"{_base_url(region)}/users/current/accounts/{account_id}/trade",
        headers={"auth-token": token},
        json={"actionType": "POSITION_CLOSE_ID", "positionId": position_id},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
