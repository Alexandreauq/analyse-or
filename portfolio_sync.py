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


if __name__ == "__main__":
    pass
