# assistant_ia/tools.py
# Les 7 outils que Claude peut appeler (voir spec §6). Chaque outil est
# une fonction pure (entree -> dict JSON-serialisable), testable sans
# toucher a l'API Anthropic. dispatch_tool() ne leve JAMAIS — un outil
# inconnu ou une erreur interne renvoie un dict {"erreur": ...}, que la
# boucle d'orchestration (Task 4) renvoie a Claude comme resultat
# d'outil en erreur plutot que de casser tout l'echange.
from difflib import get_close_matches

import assistant_ia.data_sources as data_sources

TOOL_DEFINITIONS = [
    {
        "name": "fiche_entreprise",
        "description": (
            "Renvoie la fiche complete d'une entreprise du site (score, "
            "facteurs detailles, interpretation, stage, alertes) a partir "
            "de son ticker exact. Si le ticker n'existe pas, renvoie des "
            "suggestions de tickers proches a presenter a l'utilisateur "
            "via proposer_lien plutot que de deviner."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Ticker exact, ex. MC.PA"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "comparer_entreprises",
        "description": "Renvoie les fiches completes de deux entreprises, pour un argumentaire comparatif facteur par facteur.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker1": {"type": "string"},
                "ticker2": {"type": "string"},
            },
            "required": ["ticker1", "ticker2"],
        },
    },
    {
        "name": "classement",
        "description": "Renvoie les N entreprises les mieux notees, filtrees optionnellement par indice et/ou secteur.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indice": {
                    "type": "string",
                    "description": "Valeur exacte de l'indice, ex. CAC40, NASDAQ",
                    "enum": [
                        "CAC40", "DAX", "DOW", "FTSE", "FTSEMIB",
                        "HANGSENG", "IBEX35", "NASDAQ", "NIKKEI225", "SMI",
                    ],
                },
                "secteur": {
                    "type": "string",
                    "description": (
                        "Secteur exact en anglais tel qu'utilise par le site, ex. "
                        "Consumer Cyclical, Consumer Defensive, Healthcare, "
                        "Financial Services, Technology, Industrials, Energy, "
                        "Basic Materials, Communication Services, Utilities, "
                        "Real Estate."
                    ),
                },
                "n": {"type": "integer", "description": "Nombre de resultats, defaut 10, maximum 25"},
            },
        },
    },
    {
        "name": "statut_bot",
        "description": "Renvoie le statut operationnel (solde, derniere execution) du bot Or ou du Bot Actions.",
        "input_schema": {
            "type": "object",
            "properties": {"nom": {"type": "string", "enum": ["or", "actions"]}},
            "required": ["nom"],
        },
    },
    {
        "name": "positions_bot",
        "description": "Renvoie les positions actuellement ouvertes par le bot Or ou le Bot Actions.",
        "input_schema": {
            "type": "object",
            "properties": {"nom": {"type": "string", "enum": ["or", "actions"]}},
            "required": ["nom"],
        },
    },
    {
        "name": "resume_portefeuille",
        "description": (
            "Combine les positions manuelles de l'utilisateur avec les "
            "positions des deux bots pour une vue d'ensemble du "
            "portefeuille. Les positions manuelles sont injectees "
            "automatiquement par le serveur — n'envoie aucun argument."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "proposer_lien",
        "description": (
            "Suggere a l'utilisateur de naviguer vers une fiche entreprise "
            "ou une section du site, OU propose un choix de "
            "desambiguisation (appeler plusieurs fois pour plusieurs "
            "choix). N'affecte jamais le texte de la reponse : ces "
            "suggestions sont affichees a part, comme des boutons."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cible_type": {"type": "string", "enum": ["ticker", "section"]},
                "cible_valeur": {"type": "string", "description": "Ex. 'MC.PA' ou 'or'/'portefeuille'/'indices'"},
                "libelle": {"type": "string", "description": "Texte du bouton, ex. 'Voir LVMH'"},
            },
            "required": ["cible_type", "cible_valeur", "libelle"],
        },
    },
]


def _find_company(ticker: str, companies: list[dict]) -> dict | None:
    for c in companies:
        if c.get("ticker") == ticker:
            return c
    return None


def _suggestions_proches(ticker: str, companies: list[dict]) -> list[str]:
    """Cherche des correspondances approchees sur le ticker ET le nom de
    l'entreprise (ex. 'LVMHH' est proche du *nom* 'LVMH', pas du ticker
    'MC.PA'), puis renvoie les tickers correspondants, dedupliques et
    dans l'ordre de pertinence."""
    candidats: dict[str, str] = {}
    for c in companies:
        t = c.get("ticker", "")
        n = c.get("name", "")
        if t:
            candidats.setdefault(t, t)
        if n:
            candidats.setdefault(n, t)
    matches = get_close_matches(ticker, list(candidats.keys()), n=3)
    suggestions: list[str] = []
    for m in matches:
        tk = candidats[m]
        if tk not in suggestions:
            suggestions.append(tk)
    return suggestions


def _fiche_allegee(company: dict) -> dict:
    """Renvoie la fiche d'une entreprise SANS les champs volumineux
    (financial_analysis_html ~8.5Ko, news ~3.9Ko) — voir finding 2 de la
    revue finale : sans cette coupe, un classement avec un n eleve ou
    plusieurs fiche_entreprise/comparer_entreprises dans une meme
    conversation peut couter plusieurs dollars en une seule question."""
    return {k: v for k, v in company.items() if k not in ("financial_analysis_html", "news")}


def fiche_entreprise(ticker: str, *, indices_source=data_sources.fetch_indices_data) -> dict:
    companies = indices_source().get("companies", [])
    company = _find_company(ticker, companies)
    if company is None:
        return {
            "erreur": f"ticker introuvable : {ticker!r}",
            "suggestions": _suggestions_proches(ticker, companies),
        }
    return _fiche_allegee(company)


def comparer_entreprises(
    ticker1: str, ticker2: str, *, indices_source=data_sources.fetch_indices_data,
) -> dict:
    return {
        "entreprise_1": fiche_entreprise(ticker1, indices_source=indices_source),
        "entreprise_2": fiche_entreprise(ticker2, indices_source=indices_source),
    }


def classement(
    indice: str | None = None, secteur: str | None = None, n: int = 10,
    *, indices_source=data_sources.fetch_indices_data,
) -> dict:
    n = min(n, 25)
    companies = indices_source().get("companies", [])
    if indice:
        companies = [c for c in companies if c.get("index") == indice]
    if secteur:
        companies = [c for c in companies if c.get("sector") == secteur]
    trie = sorted(companies, key=lambda c: c.get("score") or 0, reverse=True)
    return {"classement": [_fiche_allegee(c) for c in trie[:n]]}


def statut_bot(nom: str, *, dashboard_source=data_sources.fetch_bot_dashboard) -> dict:
    data = dashboard_source(nom)
    if "erreur" in data:
        return data
    return {k: v for k, v in data.items() if k != "positions"}


def positions_bot(nom: str, *, dashboard_source=data_sources.fetch_bot_dashboard) -> dict:
    data = dashboard_source(nom)
    if "erreur" in data:
        return data
    return {"positions": data.get("positions", [])}


def resume_portefeuille(
    positions_manuelles: list[dict] | None = None,
    *, dashboard_source=data_sources.fetch_bot_dashboard,
) -> dict:
    toutes = list(positions_manuelles or [])
    for nom in ("or", "actions"):
        data = dashboard_source(nom)
        if "erreur" not in data:
            toutes.extend(data.get("positions", []))
    return {"positions": toutes, "nombre_total": len(toutes)}


def proposer_lien(cible_type: str, cible_valeur: str, libelle: str) -> dict:
    if cible_type not in ("ticker", "section"):
        return {"erreur": f"cible_type invalide : {cible_type!r}"}
    return {"cible_type": cible_type, "cible_valeur": cible_valeur, "libelle": libelle}


_HANDLERS = {
    "fiche_entreprise": lambda i, **deps: fiche_entreprise(i["ticker"], indices_source=deps["indices_source"]),
    "comparer_entreprises": lambda i, **deps: comparer_entreprises(
        i["ticker1"], i["ticker2"], indices_source=deps["indices_source"]),
    "classement": lambda i, **deps: classement(
        i.get("indice"), i.get("secteur"), i.get("n", 10), indices_source=deps["indices_source"]),
    "statut_bot": lambda i, **deps: statut_bot(i["nom"], dashboard_source=deps["dashboard_source"]),
    "positions_bot": lambda i, **deps: positions_bot(i["nom"], dashboard_source=deps["dashboard_source"]),
    "resume_portefeuille": lambda i, **deps: resume_portefeuille(
        i.get("positions_manuelles"), dashboard_source=deps["dashboard_source"]),
    "proposer_lien": lambda i, **deps: proposer_lien(i["cible_type"], i["cible_valeur"], i["libelle"]),
}


def dispatch_tool(
    name: str, tool_input: dict, *,
    indices_source=data_sources.fetch_indices_data,
    dashboard_source=data_sources.fetch_bot_dashboard,
) -> dict:
    """Execute l'outil nomme `name` avec `tool_input`. Ne leve JAMAIS :
    un nom inconnu ou une exception interne (cle manquante dans
    tool_input, etc.) renvoie un dict {"erreur": ...} plutot que de
    laisser une exception remonter jusqu'a la boucle d'orchestration."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"erreur": f"outil inconnu : {name!r}"}
    try:
        return handler(tool_input, indices_source=indices_source, dashboard_source=dashboard_source)
    except Exception as e:
        return {"erreur": f"echec de l'outil {name} : {e}"}
