# ibkr_bot/sizing.py
# Conversion du budget fixe de 500 EUR vers la devise locale, arrondi a
# l'action entiere inferieure, et GARDE-FOU PENCE/LIVRE pour le LSE.
#
# LE PIEGE (voir spec 4.7 point 1, deja rencontre et corrige dans ce
# depot le 2026-09-13) : yfinance renvoie les cours londoniens en PENCE
# (GBp) ; indices_score.py les divise par 100 a la source (chercher
# `history = history / 100.0`), donc docs/indices.json contient des
# LIVRES (GBP). IBKR, lui, cote et execute le LSE en PENCE. Melanger les
# deux unites donne une quantite 100x trop grande (budget en pence /
# prix en livres) ou 100x trop petite (l'inverse).
#
# LA REGLE : budget et prix sont TOUJOURS ramenes a la meme unite, et
# cette unite est celle d'IBKR (la "devise de cotation"). On distingue
# donc explicitement :
#   - devise_cotation : GBp pour .L, la devise de l'indice ailleurs.
#     Sert au calcul de quantite et aux comparaisons avec mktPrice IBKR.
#   - devise_compte   : toujours la devise de l'indice (GBP pour .L).
#     Sert au garde-fou de solde par devise (voir spec 9.9).
import math

BUDGET_EUR = 500.0          # budget nominal par position (spec 3.1)
PENCE_SUFFIX = ".L"         # suffixe yfinance du London Stock Exchange
PENCE_PER_POUND = 100.0


def _is_positive_number(value) -> bool:
    """True seulement pour un nombre fini et strictement positif. Un NaN
    passe tous les tests de comparaison sans lever : il doit etre rejete
    explicitement, sinon math.floor(nan) leve plus loin, ou pire, une
    comparaison silencieusement fausse laisse passer un ordre."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if math.isnan(value) or math.isinf(value):
        return False
    return value > 0


def _is_valid_currency(value) -> bool:
    """True seulement pour une devise non vide, sous forme de chaine.
    `index_currency` peut arriver vide ou None si docs/indices.json perd
    une entree de `index_currency` pour un indice (voir
    signals.py:101, `currencies.get(..., "")`) : laisser passer ce cas
    produirait un plan "achetable" avec `devise_compte` a None/"", qui
    degraderait silencieusement le garde-fou de solde par devise en aval
    (spec 9.9) au lieu d'echouer bruyamment ici."""
    return isinstance(value, str) and value.strip() != ""


def is_pence_quoted(ticker: str) -> bool:
    """True pour les tickers du LSE, cotes en pence (GBp) chez IBKR."""
    return isinstance(ticker, str) and ticker.endswith(PENCE_SUFFIX)


def quotation_currency(index_currency: str, ticker: str) -> str:
    """Devise dans laquelle IBKR cote et execute ce ticker."""
    return "GBp" if is_pence_quoted(ticker) else index_currency


def to_quotation_price(price_indices: float, ticker: str) -> float:
    """Prix de docs/indices.json (livres pour le LSE) converti dans
    l'unite de cotation IBKR (pence pour le LSE)."""
    if is_pence_quoted(ticker):
        return price_indices * PENCE_PER_POUND
    return price_indices


def from_quotation_price(price_quotation: float, ticker: str) -> float:
    """Inverse exact de to_quotation_price : ramene un prix IBKR dans
    l'unite de docs/indices.json, la seule unite dans laquelle les
    regles de sortie comparent quoi que ce soit."""
    if is_pence_quoted(ticker):
        return price_quotation / PENCE_PER_POUND
    return price_quotation


def compute_quantity(
    ticker: str, index_currency: str, unit_price_indices: float,
    fx_rate: float, budget_eur: float = BUDGET_EUR,
) -> dict:
    """Nombre entier d'actions achetables avec `budget_eur` euros.

    `fx_rate` est le taux EUR -> devise de l'indice (ex. 0.86 pour
    EUR/GBP), tel que renvoye par gateway.exchange_rate(). Le reliquat
    reste en cash (spec 3.3). Si le prix unitaire depasse le budget
    converti, la quantite vaut 0 et le motif est renseigne : AUCUN ordre
    ne doit etre passe dans ce cas, et surtout pas "1 action au moins".
    """
    devise_cotation = quotation_currency(index_currency, ticker)
    plan = {
        "ticker": ticker,
        "quantite": 0,
        "devise_cotation": devise_cotation,
        "devise_compte": index_currency,
        "taux_de_change": fx_rate if _is_positive_number(fx_rate) else 0.0,
        "budget_converti": 0.0,
        "prix_unitaire_cotation": 0.0,
        "cout_estime_devise_compte": 0.0,
        "motif": None,
    }

    if not (_is_positive_number(unit_price_indices)
            and _is_positive_number(fx_rate)
            and _is_positive_number(budget_eur)
            and _is_valid_currency(index_currency)):
        plan["motif"] = "prix_ou_taux_invalide"
        return plan

    # Les deux grandeurs sont ramenees a la MEME unite (devise de
    # cotation) avant la moindre division — c'est tout le garde-fou.
    budget_compte = budget_eur * fx_rate
    budget_cotation = to_quotation_price(budget_compte, ticker)
    prix_cotation = to_quotation_price(unit_price_indices, ticker)

    plan["budget_converti"] = budget_cotation
    plan["prix_unitaire_cotation"] = prix_cotation

    # Tolerance epsilon : en arithmetique flottante, un ratio
    # mathematiquement entier (ex. 420.6 / 0.8412 -> exactement 1) peut
    # atterrir a 0.9999999999999999. Sans cette tolerance, floor()
    # sous-achete de 1 action ET ecrit un motif "prix superieur au
    # budget" FACTUELLEMENT FAUX dans le journal d'audit (le prix ne
    # depasse pas reellement le budget). La tolerance est volontairement
    # minuscule (1e-9, mise a l'echelle du ratio) : elle ne peut arrondir
    # a la hausse qu'un ratio deja a une distance negligeable d'un
    # entier — jamais un ratio reellement inferieur (ex. 0.86), qui reste
    # rejete comme il se doit (spec 3.3 : jamais "1 action au moins").
    ratio = budget_cotation / prix_cotation
    quantite = math.floor(ratio + 1e-9 * max(1.0, ratio))
    if quantite < 1:
        plan["motif"] = "signal_ignore_prix_unitaire_superieur_au_budget"
        return plan

    plan["quantite"] = int(quantite)
    plan["cout_estime_devise_compte"] = quantite * unit_price_indices
    return plan
