# ibkr_bot/contracts.py
# Resolution d'un ticker yfinance (MC.PA, SAP.DE, III.L, ADBE...) vers le
# conid numerique IBKR, avec cache local des correspondances deja
# resolues.
#
# PRINCIPE NON NEGOCIABLE (spec 4.7 point 2) : une recherche par symbole
# renvoie souvent plusieurs contrats (cotations multiples, ADR, derives).
# On n'accepte un contrat que si la BOURSE et la DEVISE attendues
# correspondent, et qu'il ne reste qu'un seul candidat. Sinon on refuse
# et on journalise `contrat_non_resolu` — jamais "le premier de la
# liste", jamais un achat devine.
#
# Ce module ne fait aucun appel reseau lui-meme : les fonctions
# gateway.search_contract / gateway.contract_info lui sont injectees en
# tant que callables a un seul argument (deja lies a une base_url par
# l'appelant), ce qui le rend testable sans mock HTTP.
import json
import os

CONID_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "conid_cache.json")

# Codes de place IBKR verifies le 2026-09-14 sur les listes publiques de
# codes d'echange IBKR. Xetra expose IBIS (actions) et IBIS2 (ETF) : les
# deux sont acceptes, le filtre devise tranche derriere. Le LSE annonce
# sa devise tantot "GBP", tantot "GBp", tout en cotant en pence — les
# deux orthographes sont acceptees ici ; la conversion d'unite, elle, ne
# depend jamais de ce champ mais du suffixe .L (voir sizing.py).
EXPECTED_VENUE = {
    ".PA": {"exchanges": ("SBF",), "currencies": ("EUR",)},
    ".DE": {"exchanges": ("IBIS", "IBIS2"), "currencies": ("EUR",)},
    ".MI": {"exchanges": ("BVME",), "currencies": ("EUR",)},
    ".MC": {"exchanges": ("BM",), "currencies": ("EUR",)},
    ".L": {"exchanges": ("LSE",), "currencies": ("GBP", "GBp")},
    ".SW": {"exchanges": ("EBS",), "currencies": ("CHF",)},
    "": {"exchanges": ("NASDAQ", "NYSE", "ARCA", "AMEX", "BATS"),
         "currencies": ("USD",)},
}

# Suffixes yfinance reconnus comme de VRAIS codes de place mais hors du
# perimetre v1 (Nikkei .T, Hang Seng .HK, et quelques autres places
# majeures). Le role de cette liste est uniquement de produire un refus
# "suffixe hors perimetre" propre et sans appel reseau pour ces cas
# frequents plutot que de laisser tomber sur le comportement par defaut
# ci-dessous (traiter le suffixe comme faisant partie du symbole, cas
# BRK.B). Elle n'a pas besoin d'etre exhaustive pour etre sure : meme un
# suffixe absent d'ici est de toute facon rejete par le filtre 1 de
# resolve_conid (symbole EXACT + bourse US attendue), qui ne peut pas
# accidentellement matcher un symbole etranger different.
OUT_OF_SCOPE_SUFFIXES = {
    ".T", ".HK", ".SS", ".SZ", ".KS", ".KQ", ".TW", ".TWO", ".AX", ".TO",
    ".V", ".NS", ".BO", ".SA",
}


def split_ticker(ticker: str) -> tuple[str, str]:
    """(symbole, suffixe) — le suffixe n'est detache que s'il designe une
    place connue (perimetre v1 ou hors perimetre mais identifiee). BRK.B
    reste ("BRK.B", "") : le point y fait partie du symbole (classe
    d'action), pas d'un code de marche."""
    if "." in ticker:
        symbole, _, fin = ticker.rpartition(".")
        suffixe = f".{fin}"
        if symbole and (suffixe in EXPECTED_VENUE or suffixe in OUT_OF_SCOPE_SUFFIXES):
            return symbole, suffixe
    return ticker, ""


def expected_venue(ticker: str) -> dict | None:
    """Bourse(s) et devise(s) attendues pour ce ticker, ou None si son
    suffixe est hors du perimetre v1 (Nikkei .T, Hang Seng .HK...)."""
    _, suffixe = split_ticker(ticker)
    return EXPECTED_VENUE.get(suffixe)


def load_cache(path: str = CONID_CACHE_PATH) -> dict:
    """Correspondances ticker -> conid deja resolues. {} si le fichier
    est absent ou corrompu — jamais d'exception."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(cache: dict, path: str = CONID_CACHE_PATH) -> None:
    """Ecriture atomique, meme motif que state.save_state."""
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _refus(ticker: str, detail: str) -> dict:
    return {"ticker": ticker, "conid": None, "exchange": "", "currency": "",
            "motif": "contrat_non_resolu", "detail": detail}


def _a_une_section_action(candidat: dict) -> bool:
    sections = candidat.get("sections") or []
    return any(isinstance(s, dict) and s.get("secType") == "STK" for s in sections)


def resolve_conid(ticker: str, search_fn, info_fn,
                  cache: dict | None = None, today: str = "") -> dict:
    """Resout `ticker` en contrat IBKR.

    `search_fn(symbole) -> list[dict]` et `info_fn(conid) -> dict` sont
    typiquement gateway.search_contract / gateway.contract_info lies a
    une base_url. `cache` est le dict renvoye par load_cache() ; il est
    enrichi sur place en cas de succes. Les echecs ne sont jamais mis en
    cache : une resolution peut reussir demain.
    """
    cache = cache if cache is not None else {}
    en_cache = cache.get(ticker)
    if isinstance(en_cache, dict) and en_cache.get("conid"):
        return {"ticker": ticker, "conid": en_cache["conid"],
                "exchange": en_cache.get("exchange", ""),
                "currency": en_cache.get("currency", ""),
                "motif": None, "detail": "cache"}

    venue = expected_venue(ticker)
    if venue is None:
        return _refus(ticker, f"suffixe hors perimetre pour {ticker}")

    symbole, _ = split_ticker(ticker)
    try:
        candidats = search_fn(symbole)
    except Exception as exc:
        return _refus(ticker, f"recherche impossible : {exc}")

    # Filtre 1 : meme symbole, une section action, bourse attendue.
    retenus = [
        c for c in candidats
        if isinstance(c, dict)
        and c.get("symbol") == symbole
        and _a_une_section_action(c)
        and c.get("description") in venue["exchanges"]
    ]

    # Filtre 2 : devise attendue, confirmee contrat par contrat.
    confirmes = []
    for candidat in retenus:
        try:
            details = info_fn(candidat["conid"])
        except Exception as exc:
            return _refus(ticker, f"details indisponibles : {exc}")
        if isinstance(details, dict) and details.get("currency") in venue["currencies"]:
            confirmes.append((candidat, details))

    # Filtre 3 : unicite. Zero ou plusieurs -> on refuse.
    if not confirmes:
        return _refus(
            ticker,
            f"aucun candidat sur {'/'.join(venue['exchanges'])} "
            f"en {'/'.join(venue['currencies'])} pour {symbole} "
            f"({len(candidats)} resultat(s) bruts)",
        )
    if len(confirmes) > 1:
        conids = ", ".join(str(c["conid"]) for c, _ in confirmes)
        return _refus(ticker, f"ambigu : {len(confirmes)} contrats retenus ({conids})")

    candidat, details = confirmes[0]
    conid = int(candidat["conid"])
    exchange = candidat.get("description", "")
    currency = details.get("currency", "")
    cache[ticker] = {"conid": conid, "exchange": exchange,
                     "currency": currency, "resolved_on": today}
    return {"ticker": ticker, "conid": conid, "exchange": exchange,
            "currency": currency, "motif": None, "detail": "resolu"}
