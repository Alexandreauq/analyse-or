# tests/ibkr_bot/test_order_routes_are_isolated.py
# Garde-fou structurel : ibkr_bot/gateway.py est le SEUL module du paquet
# qui a le droit de parler a IBKR (voir ibkr_bot/gateway.py:5 et :13-16).
# place_market_order() y est le seul chemin par lequel de l'argent reel
# peut bouger (voir ibkr_bot/gateway.py:272-276) ; ce test verifie
# qu'aucun AUTRE module de ibkr_bot/ ne peut emprunter ce chemin en
# contournant gateway.py.
#
# Ce fichier est referme par deux commentaires d'en-tete de gateway.py
# (Taches 1 et 5) qui anticipaient sa creation sous ce nom exact — a
# l'epoque du client REST (Client Portal Web API) l'equivalent visait
# `requests`/les chemins `/iserver/.../orders` ; depuis la migration vers
# la TWS API (ib_async), les points sensibles sont le paquet `ib_async`
# lui-meme et les noms de methode `placeOrder`/`reqContractDetails`/
# `reqMktData` qui envoient reellement des requetes a TWS.
#
# CORRECTIF (revue finale de branche) : le paragraphe ci-dessus affirmait
# auparavant qu'"aucune version anterieure de ce test n'existe dans
# l'historique du depot" — c'est FAUX. Un test structurel d'isolation
# existait deja AVANT cette migration (ajoute entre les commits beb7dda
# et e2cee7a de l'ancienne suite CPAPI, dans
# tests/ibkr_bot/test_gateway.py :
# test_only_gateway_and_daily_may_reference_the_order_functions et
# test_ibkr_bot_package_does_not_reexport_the_order_routes). La Tache 1
# de cette migration (commit 5b381f2) l'a SUPPRIME sans le remplacer, en
# recreant gateway.py from scratch pour la TWS API. La Tache 8 (ce
# fichier) l'a reconstruit avec une couverture plus large, specifique a
# ib_async (motifs `ib_async`/`placeOrder`/`MarketOrder`/
# `reqContractDetails`/`reqMktData`, voir MOTIFS_INTERDITS ci-dessous) —
# mais sans reprendre les deux garanties de l'ancien test portant sur les
# NOMS DE FONCTION du gateway plutot que sur les symboles ib_async. Ces
# deux garanties sont desormais ajoutees plus bas (voir
# test_no_module_other_than_daily_calls_the_gateway_order_functions et
# test_ibkr_bot_package_does_not_reexport_the_order_functions), fermant
# l'ecart restant entre l'ancien test et celui-ci.
#
# Mecanique : parcours de tous les fichiers .py de ibkr_bot/ (paquet de
# production, pas tests/), a l'exclusion de gateway.py lui-meme, et
# recherche de motifs suspects par simple inclusion de sous-chaine —
# aucun module de ibkr_bot/ ne mentionne aujourd'hui ces motifs, y
# compris dans ses commentaires (verifie avant l'ecriture de ce test),
# donc l'approche ne produit aucun faux positif au moment ou elle est
# ecrite. Une recherche par sous-chaine plutot qu'une analyse d'AST
# reste volontairement simple : elle attrape aussi bien
# `import ib_async`, `from ib_async import ...`,
# `importlib.import_module("ib_async")` qu'une mention en commentaire —
# un test structurel de ce type doit pecher par exces de prudence.
import os

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAQUET = os.path.join(RACINE, "ibkr_bot")

# Fichier autorise a parler a ib_async — le seul chemin d'ordre reel.
FICHIER_AUTORISE = "gateway.py"

# Motifs qui, trouves hors de gateway.py, signaleraient qu'un autre
# module contourne le garde-fou et parle directement a IBKR.
MOTIFS_INTERDITS = [
    "ib_async",          # tout import (direct, "from", ou dynamique) du
                          # paquet, et toute mention meme en commentaire.
    "placeOrder",        # IB.placeOrder() : envoie reellement un ordre.
    "MarketOrder",        # ib_async.MarketOrder : construit l'ordre envoye.
    "reqContractDetails",  # IB.reqContractDetails() : appel reseau IBKR.
    "reqMktData",         # IB.reqMktData() : appel reseau IBKR (flux prix).
]


def _fichiers_python_du_paquet_hors_gateway():
    """Tous les .py de ibkr_bot/ (recursif, pour survivre a un futur
    sous-paquet) sauf gateway.py lui-meme. Le paquet est petit et plat
    aujourd'hui ; le parcours recursif est une securite pour plus tard,
    pas un besoin actuel."""
    resultat = []
    for dossier, _sous_dossiers, fichiers in os.walk(PAQUET):
        if "__pycache__" in dossier:
            continue
        for nom in fichiers:
            if not nom.endswith(".py"):
                continue
            if nom == FICHIER_AUTORISE:
                continue
            resultat.append(os.path.join(dossier, nom))
    return resultat


_FICHIERS = _fichiers_python_du_paquet_hors_gateway()


def test_the_isolation_scan_actually_finds_the_package_files():
    """Garde-fou du garde-fou : si ibkr_bot/ etait introuvable ou vide,
    les tests ci-dessous passeraient tous vacuously sans rien verifier.
    On exige un nombre minimal de fichiers connus pour detecter ce
    piege silencieux."""
    noms = {os.path.basename(f) for f in _FICHIERS}
    assert {"daily.py", "contracts.py", "portfolio.py", "signals.py",
            "sizing.py", "state.py", "journal.py", "notify.py"} <= noms
    assert FICHIER_AUTORISE not in noms


@pytest.mark.parametrize("chemin", _FICHIERS, ids=lambda p: os.path.basename(p))
@pytest.mark.parametrize("motif", MOTIFS_INTERDITS)
def test_no_other_ibkr_bot_module_references_the_order_routes(chemin, motif):
    with open(chemin, encoding="utf-8") as fh:
        contenu = fh.read()
    assert motif not in contenu, (
        f"{os.path.relpath(chemin, RACINE)} contient {motif!r} — seul "
        f"{FICHIER_AUTORISE} a le droit de parler a ib_async/IBKR "
        f"(voir ibkr_bot/gateway.py:5)."
    )


def test_gateway_itself_is_the_one_module_allowed_to_import_ib_async():
    """Controle positif : si ce test echouait, ce serait la preuve que le
    scan ci-dessus ne detecte plus rien du tout (par ex. si gateway.py
    perdait son import ib_async sans que personne d'autre ne le
    reprenne) plutot que la preuve d'une isolation reelle."""
    chemin_gateway = os.path.join(PAQUET, FICHIER_AUTORISE)
    with open(chemin_gateway, encoding="utf-8") as fh:
        contenu = fh.read()
    assert "from ib_async import" in contenu
    assert "placeOrder" in contenu


# --- garanties reprises de l'ancien test CPAPI (voir en-tete du fichier) --
#
# Le scan ci-dessus (MOTIFS_INTERDITS) ne detecte que les symboles
# ib_async eux-memes. Il ne detecterait PAS un module qui contournerait
# gateway.py en appelant directement `gateway.place_market_order(...)` ou
# `gateway.confirm_reply(...)` sans jamais mentionner `ib_async`,
# `placeOrder`, etc. Ces deux fonctions sont les NOMS DE FONCTION du
# gateway (pas des symboles ib_async) : seuls gateway.py (definition) et
# daily.py (seul appelant legitime, spec 4.3 point 2) ont le droit de les
# mentionner.
NOMS_DE_FONCTION_ORDRE = ["place_market_order", "confirm_reply"]

# daily.py s'ajoute a gateway.py comme fichier autorise pour CE scan
# precis (il appelle legitimement gateway.place_market_order /
# gateway.confirm_reply) — contrairement au scan MOTIFS_INTERDITS
# ci-dessus, qui interdit les symboles ib_async partout, daily.py compris.
APPELANTS_AUTORISES_DES_FONCTIONS_ORDRE = {FICHIER_AUTORISE, "daily.py"}


def _fichiers_python_du_paquet_hors_appelants_autorises():
    """Meme parcours que _fichiers_python_du_paquet_hors_gateway() (voir
    plus haut), mais excluant aussi daily.py — le seul appelant legitime
    des fonctions de passage d'ordre."""
    resultat = []
    for dossier, _sous_dossiers, fichiers in os.walk(PAQUET):
        if "__pycache__" in dossier:
            continue
        for nom in fichiers:
            if not nom.endswith(".py"):
                continue
            if nom in APPELANTS_AUTORISES_DES_FONCTIONS_ORDRE:
                continue
            resultat.append(os.path.join(dossier, nom))
    return resultat


_FICHIERS_HORS_APPELANTS_AUTORISES = _fichiers_python_du_paquet_hors_appelants_autorises()


@pytest.mark.parametrize("chemin", _FICHIERS_HORS_APPELANTS_AUTORISES,
                         ids=lambda p: os.path.basename(p))
@pytest.mark.parametrize("nom", NOMS_DE_FONCTION_ORDRE)
def test_no_module_other_than_daily_calls_the_gateway_order_functions(chemin, nom):
    with open(chemin, encoding="utf-8") as fh:
        contenu = fh.read()
    assert nom not in contenu, (
        f"{os.path.relpath(chemin, RACINE)} mentionne {nom!r} — seuls "
        f"{FICHIER_AUTORISE} (definition) et daily.py (seul appelant "
        f"legitime, spec 4.3 point 2) ont le droit de reference cette "
        f"fonction."
    )


def test_ibkr_bot_package_does_not_reexport_the_order_functions():
    """Ceinture et bretelles par rapport aux scans textuels ci-dessus :
    meme si un futur ibkr_bot/__init__.py se mettait a faire
    `from .gateway import *`, les fonctions de passage d'ordre ne
    doivent pas devenir accessibles comme `ibkr_bot.place_market_order`.
    Aujourd'hui __init__.py est vide, donc ce test passe trivialement —
    il sert de garde-fou si ca change."""
    import ibkr_bot

    assert not hasattr(ibkr_bot, "place_market_order")
    assert not hasattr(ibkr_bot, "confirm_reply")
    assert not hasattr(ibkr_bot, "order_status")
