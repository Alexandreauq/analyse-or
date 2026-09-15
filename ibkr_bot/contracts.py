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

    Une entree en cache n'est servie que si elle est encore coherente
    avec EXPECTED_VENUE au moment de l'appel (bourse ET devise valides
    pour le suffixe du ticker). Le fichier de cache est persiste sur
    disque sans date d'expiration — `resolved_on` est ecrit pour l'audit
    mais jamais relu pour perimer une entree — donc si EXPECTED_VENUE
    est corrige un jour (ses codes sont verifies contre des listes
    publiques, pas contre un Gateway en direct), une entree deja
    resolue sous l'ancienne table ne doit pas rester approuvee pour
    toujours pour les tickers meme que la correction visait. Une entree
    qui ne passe plus ce controle est simplement ignoree : on retombe
    sur une resolution normale plutot que sur un refus immediat, car le
    ticker peut tres bien rester resolvable — seule l'entree en cache
    est perimee.
    """
    cache = cache if cache is not None else {}
    venue = expected_venue(ticker)

    en_cache = cache.get(ticker)
    if (venue is not None and isinstance(en_cache, dict) and en_cache.get("conid")
            and en_cache.get("exchange") in venue["exchanges"]
            and en_cache.get("currency") in venue["currencies"]):
        try:
            conid_cache = int(en_cache["conid"])
        except (TypeError, ValueError):
            conid_cache = None
        if conid_cache is not None:
            return {"ticker": ticker, "conid": conid_cache,
                    "exchange": en_cache.get("exchange", ""),
                    "currency": en_cache.get("currency", ""),
                    "motif": None, "detail": "cache"}

    if venue is None:
        return _refus(ticker, f"suffixe hors perimetre pour {ticker}")

    symbole, _ = split_ticker(ticker)
    try:
        candidats = search_fn(symbole)
    except Exception as exc:
        return _refus(ticker, f"recherche impossible : {exc}")
    # Un search_fn injecte peut renvoyer None (au lieu de la liste vide
    # documentee) sans que ce module plante pour autant.
    candidats = candidats if isinstance(candidats, list) else []

    # Filtre 1 : meme symbole, une section action, bourse attendue.
    retenus = [
        c for c in candidats
        if isinstance(c, dict)
        and c.get("symbol") == symbole
        and _a_une_section_action(c)
        and c.get("description") in venue["exchanges"]
    ]

    # Filtre 2 : devise attendue ET bourse de cotation confirmee,
    # contrat par contrat. listingExchange (renvoye par contract_info)
    # existe precisement pour ce controle croise : la bourse annoncee
    # par la recherche (description) et celle confirmee par la fiche
    # detaillee (listingExchange) doivent s'accorder, sinon rien ne
    # garantit que la fiche detaillee decrit bien le contrat filtre plus
    # haut. Repli sur `description` quand listingExchange est absent,
    # pour rester compatible avec des fixtures qui ne le renseignent pas.
    confirmes = []
    for candidat in retenus:
        candidat_conid = candidat.get("conid")
        if candidat_conid is None:
            continue
        try:
            details = info_fn(candidat_conid)
        except Exception as exc:
            return _refus(ticker, f"details indisponibles : {exc}")
        if not isinstance(details, dict):
            continue
        exchange_confirme = details.get("listingExchange", candidat.get("description"))
        if (details.get("currency") in venue["currencies"]
                and exchange_confirme in venue["exchanges"]):
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
        conids = ", ".join(str(c.get("conid")) for c, _ in confirmes)
        return _refus(ticker, f"ambigu : {len(confirmes)} contrats retenus ({conids})")

    candidat, details = confirmes[0]
    try:
        conid = int(candidat["conid"])
    except (TypeError, ValueError):
        return _refus(
            ticker,
            f"conid non numerique renvoye par IBKR pour {ticker} : "
            f"{candidat.get('conid')!r}",
        )
    exchange = candidat.get("description", "")
    currency = details.get("currency", "")
    cache[ticker] = {"conid": conid, "exchange": exchange,
                     "currency": currency, "resolved_on": today}
    return {"ticker": ticker, "conid": conid, "exchange": exchange,
            "currency": currency, "motif": None, "detail": "resolu"}
