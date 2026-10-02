# ibkr_bot/portfolio.py
# Plafond de 10 positions, classement des signaux par score composite,
# selection des entrees sous contrainte de solde, regles de sortie
# (reproduction fidele du paper-trading) et reconciliation avec IBKR.
#
# Ce module DECIDE, il ne passe aucun ordre et ne touche pas au reseau —
# meme decoupage que gold_bot (un seul module parle au courtier).
import json
import math
import os
from collections import Counter
from datetime import datetime

from dateutil.relativedelta import relativedelta

from ibkr_bot.sizing import BUDGET_EUR

MAX_POSITIONS = 10  # positions ouvertes PAR LE BOT, pas sur le compte (spec 3.4 / 9.5)

# Plafonds de diversification (audit Or/Actions 2026-09-21, point 3 --
# aucune contrainte n'existait : le bot pouvait finir avec 5-6 banques si
# le classement par score les favorisait toutes). Sur MAX_POSITIONS=10 :
# évite qu'un secteur ou un pays domine le portefeuille sans être
# arbitrairement restrictif. Un signal qui échoue ce plafond ne consomme
# ni place ni budget (même philosophie que "plafond_atteint" ci-dessous)
# -- la place reste disponible pour le signal suivant du classement.
MAX_POSITIONS_PER_SECTOR = 3
MAX_POSITIONS_PER_INDEX = 4

# Zones geographiques reelles (audit 2026-10-02) : MAX_POSITIONS_PER_INDEX
# ne plafonne qu'un indice precis, pas une zone -- NASDAQ et DOW sont
# tous deux americains (jusqu'a 8/10 positions US possibles avant ce
# correctif), CAC40/DAX/IBEX35/FTSEMIB sont tous en zone euro (jusqu'a
# 10/10 possibles). Ce plafond s'AJOUTE au plafond par indice, ne le
# remplace pas -- memes 8 indices que signals.INDICES_IN_SCOPE.
INDEX_ZONE = {
    "CAC40": "zone_euro", "DAX": "zone_euro",
    "IBEX35": "zone_euro", "FTSEMIB": "zone_euro",
    "NASDAQ": "amerique_nord", "DOW": "amerique_nord",
    "FTSE": "royaume_uni",
    "SMI": "suisse",
}
MAX_POSITIONS_PER_ZONE = 5

# Plafond quotidien d'entrees (audit 2026-10-02) : un jour de deploiement
# de methodologie de scoring peut faire apparaitre beaucoup de nouveaux
# signaux d'un coup (45 positions papier le 2026-09-24, 13 correctifs
# deployes le meme jour) -- sans ce plafond, le bot les achetterait TOUS
# le meme jour, un achat pilote par un changement de methodologie plutot
# que par un vrai mouvement de marche. Les signaux au-dela de ce plafond
# sont perdus, comme la sursouscription (spec 3.5) : signals.
# collect_new_signals ne lit que les positions ouvertes AUJOURD'HUI, donc
# un signal rejete ici ne reapparaitra PAS automatiquement les jours
# suivants (limite connue, voir le point I-C de l'audit, hors scope de
# ce plan).
MAX_NEW_ENTRIES_PER_DAY = 3

# Regles de sortie : valeurs IDENTIQUES a celles du paper-trading
# (indices_score.SIGNAL_STOP_LOSS_PCT / SIGNAL_SHADOW_DELAY_MONTHS). Un
# test de non-regression verifie l'egalite des deux jeux de constantes ET
# l'egalite des decisions produites.
STOP_LOSS_PCT = -20.0
DELAY_MONTHS = 6

POSITIONS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "positions.json")


def _is_missing(value) -> bool:
    """True si une valeur numerique est absente ou NaN."""
    try:
        return math.isnan(value)
    except TypeError:
        return value is None


def load_positions(path: str = POSITIONS_PATH) -> list[dict]:
    """Positions ouvertes par le bot. [] si le fichier est absent ou
    corrompu — jamais d'exception."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    positions = data.get("positions", [])
    return positions if isinstance(positions, list) else []


def save_positions(positions: list[dict], path: str = POSITIONS_PATH) -> None:
    """Ecriture atomique, meme motif que state.save_state."""
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump({"positions": positions}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def rank_signals(signals: list[dict]) -> list[dict]:
    """Signaux du jour classes par score composite decroissant (spec
    3.5). Egalite departagee par le ticker, pour que deux executions du
    meme batch prennent exactement les memes decisions."""
    return sorted(signals, key=lambda s: (-s["score"], s["ticker"]))


def free_slots(open_positions: list[dict]) -> int:
    """Places libres sous le plafond de 10 positions du bot."""
    return max(0, MAX_POSITIONS - len(open_positions))


def select_entries(
    signals: list[dict], open_positions: list[dict],
    plans: dict[str, dict], contrats: dict[str, dict], base_cash: float,
    entrees_deja_ouvertes_aujourdhui: int = 0,
) -> tuple[list[dict], list[dict]]:
    """Signaux du jour effectivement retenus a l'achat, et rejets motives.

    ORDRE DES FILTRES, qui est lui-meme une regle de la spec :
    deja detenu -> contrat non resolu -> plan absent -> quantite nulle ->
    plafond -> plafond entrees quotidien -> plafond secteur -> plafond indice ->
    plafond zone -> solde. Le cas 0 action passe AVANT le plafond parce que
    "la place ainsi liberee reste disponible pour le signal suivant du classement"
    (spec 3.3) : inverser les deux perdrait un signal financable au profit d'un
    signal inachetable. Le contrat non resolu est verifie tot, avec les autres cas
    "ce signal ne peut fondamentalement pas etre achete", pour la meme
    raison : Plan B ne pourra jamais passer l'ordre sans conid, ce rejet ne
    doit donc jamais consommer une place ni du budget. Les plafonds de
    diversification (MAX_POSITIONS_PER_SECTOR/MAX_POSITIONS_PER_INDEX,
    audit 2026-09-21 point 3) et le plafond quotidien (MAX_NEW_ENTRIES_PER_DAY,
    audit 2026-10-02) suivent la meme logique : un signal qui les depasse ne
    consomme ni place ni budget, la place reste disponible pour le signal suivant.
    Compte les positions deja ouvertes PLUS celles deja retenues plus haut dans
    ce meme classement (pas seulement open_positions), sinon deux signaux du meme
    secteur pourraient passer le meme jour avant que le plafond ne soit jamais vu
    comme atteint. Un signal sans secteur/indice connu (ne devrait pas arriver en
    pratique) n'est jamais bloque par ce plafond plutot que de rejeter une donnee
    de diversification manquante.

    `entrees_deja_ouvertes_aujourdhui` (audit final 2026-10-02, important
    n°1, defaut 0 -- retrocompatible) : nombre de positions DEJA ouvertes
    AUJOURD'HUI (position.get("date_entree") == aujourd'hui), ouvertes par
    un appel precedent de cette meme fonction PLUS TOT dans la journee --
    typiquement apres un crash + redemarrage du batch, positions.json
    etant sauvegarde apres chaque ordre precisement pour survivre a ce
    scenario (voir daily.py). Sans ce parametre, le plafond quotidien
    repart de 0 a chaque appel et autoriserait jusqu'a
    MAX_NEW_ENTRIES_PER_DAY entrees SUPPLEMENTAIRES en plus de celles
    deja ouvertes plus tot le meme jour.

    GARDE-FOU DE SOLDE (spec 9.9, revu) : IBKR convertit automatiquement
    le budget EUR vers la devise locale au moment de l'achat (mecanisme
    IDEAL, spec 9.1) — il n'est donc pas necessaire de detenir du cash
    deja converti dans la devise de chaque signal. Le garde-fou compare
    a la place le cash total dans la devise de BASE du compte
    (`base_cash`, typiquement gateway.base_currency_cash()) au budget
    cumule engage au fil du classement. Chaque position retenue engage
    au plus BUDGET_EUR (le cout reel, arrondi a l'action entiere
    inferieure, ne peut etre que <= BUDGET_EUR) : utiliser ce montant
    fixe plutot que `cout_estime_devise_compte` (dans une devise
    etrangere, non directement comparable a `base_cash` sans un taux de
    conversion que cette fonction n'a pas) est le choix conservateur et
    simple retenu ici.
    """
    tickers_detenus = {p["ticker"] for p in open_positions}
    places = free_slots(open_positions)
    engage = 0.0
    sector_counts = Counter(p["sector"] for p in open_positions if p.get("sector"))
    index_counts = Counter(p["index"] for p in open_positions if p.get("index"))
    zone_counts = Counter(
        INDEX_ZONE[p["index"]] for p in open_positions
        if p.get("index") in INDEX_ZONE
    )

    retenus: list[dict] = []
    rejets: list[dict] = []
    for rang, signal in enumerate(rank_signals(signals), start=1):
        ticker = signal["ticker"]
        base = {"ticker": ticker, "rang": rang, "score": signal["score"]}

        if ticker in tickers_detenus:
            rejets.append({**base, "raison": "deja_en_portefeuille"})
            continue

        contrat = contrats.get(ticker)
        if contrat is None or contrat.get("motif") is not None:
            rejets.append({**base, "raison": "contrat_non_resolu"})
            continue

        plan = plans.get(ticker)
        if plan is None:
            rejets.append({**base, "raison": "plan_indisponible"})
            continue

        if plan["quantite"] < 1:
            rejets.append({**base, "raison": plan["motif"] or "plan_indisponible"})
            continue

        if places < 1:
            rejets.append({**base, "raison": "signal_ignore_plafond_atteint"})
            continue

        if len(retenus) + entrees_deja_ouvertes_aujourdhui >= MAX_NEW_ENTRIES_PER_DAY:
            rejets.append({**base, "raison": "plafond_entrees_quotidien_atteint"})
            continue

        secteur = signal.get("sector") or ""
        if secteur and sector_counts[secteur] >= MAX_POSITIONS_PER_SECTOR:
            rejets.append({**base, "raison": "plafond_secteur_atteint"})
            continue

        indice = signal.get("index") or ""
        if indice and index_counts[indice] >= MAX_POSITIONS_PER_INDEX:
            rejets.append({**base, "raison": "plafond_indice_atteint"})
            continue

        zone = INDEX_ZONE.get(indice, "")
        if zone and zone_counts[zone] >= MAX_POSITIONS_PER_ZONE:
            rejets.append({**base, "raison": "plafond_zone_atteint"})
            continue

        if base_cash - engage < BUDGET_EUR:
            rejets.append({**base, "raison": "solde_insuffisant"})
            continue

        engage += BUDGET_EUR
        places -= 1
        tickers_detenus.add(ticker)
        if secteur:
            sector_counts[secteur] += 1
        if indice:
            index_counts[indice] += 1
            if indice in INDEX_ZONE:
                zone_counts[INDEX_ZONE[indice]] += 1
        retenus.append({"signal": signal, "plan": plan, "rang": rang})

    return retenus, rejets


# --- regles de sortie -------------------------------------------------
# Reproduction fidele de indices_score._close_eligible_positions(). Quatre
# ecarts volontaires :
#   1. le prix de reference du stop-loss est le prix d'execution REEL du
#      bot, pas l'entry_price du paper-trading (spec 3.6) ;
#   2. le benchmark fantome n'est pas reproduit — c'est un instrument de
#      mesure du signal, pas une regle de trading (spec 3.6) ;
#   3. on DECIDE seulement : aucune mutation de la position, aucun calcul
#      de return_pct — le Plan B executera et journalisera ;
#   4. le garde `_is_missing(prix_execution_reference)` ci-dessous n'a
#      PAS d'equivalent dans la fonction reelle. Effet fail-safe voulu
#      (jamais de vente forcee sur un prix de reference corrompu), mais
#      consequence a connaitre : une position dont le prix de reference
#      est absent/NaN devient INCLOSABLE par toute regle, pour toujours,
#      tant que ce prix n'est pas restaure — voir la note pres du garde.
# Tout le reste est identique, inegalites larges comprises.

def deadline_date(entry_date: str) -> str:
    """Date limite de detention : entree + 6 mois, meme calcul que le
    paper-trading (relativedelta, qui ramene le 31 aout au 28/29
    fevrier plutot que de deborder sur mars)."""
    limite = (datetime.strptime(entry_date, "%Y-%m-%d").date()
              + relativedelta(months=DELAY_MONTHS))
    return limite.strftime("%Y-%m-%d")


def exit_reason(position: dict, company: dict | None, today: str,
                roster_tickers: set[str] | None = None) -> str | None:
    """Motif de cloture de `position` aujourd'hui, ou None si aucune
    condition n'est remplie.

    ORDRE DE PRIORITE STRICT, premiere condition remplie gagne (spec
    3.6) : ticker_retire_indice -> stop_loss -> objectif_atteint ->
    delai_max.

    Une position dont le ticker a disparu des donnees du jour, ou dont
    le prix courant manque, est laissee INTACTE et reevaluee demain :
    jamais de vente declenchee par une donnee absente -- SAUF si
    `roster_tickers` est fourni et que le ticker n'y figure plus : dans
    ce cas, la societe n'a pas juste une donnee manquante ce run, elle a
    ete RETIREE DE L'INDICE (audit 2026-10-02) -- meme traitement que
    ticker_retire_indice cote paper-trading (indices_score.py), pour ne
    jamais laisser une position bloquee indefiniment sans stop-loss ni
    sortie possible apres une revision d'indice. `roster_tickers=None`
    (defaut) desactive ce comportement, retrocompatible avec les
    appelants existants qui ne le fournissent pas.
    """
    if company is None:
        if roster_tickers is not None and position["ticker"] not in roster_tickers:
            return "ticker_retire_indice"
        return None
    if _is_missing(company.get("current_price")):
        return None
    prix_reference = position.get("prix_execution_reference")
    if _is_missing(prix_reference):
        # Ecart #4 (voir commentaire de module) : sans alerte externe, une
        # position bloquee ici occupe une place indefiniment sans jamais
        # se clore. C'est au Plan B de detecter et signaler ce cas a un
        # operateur — rien ne le fait aujourd'hui.
        return None

    current_price = company["current_price"]

    if current_price <= prix_reference * (1 + STOP_LOSS_PCT / 100):
        return "stop_loss"
    if current_price >= position["target_exit_price"]:
        return "objectif_atteint"
    today_date = datetime.strptime(today, "%Y-%m-%d").date()
    if today_date >= datetime.strptime(position["date_limite"], "%Y-%m-%d").date():
        return "delai_max"
    return None


def positions_to_close(open_positions: list[dict], companies_by_ticker: dict,
                       today: str, roster_tickers: set[str] | None = None) -> list[dict]:
    """Positions du bot dont une condition de sortie est remplie
    aujourd'hui, avec leur motif et le prix courant ayant declenche la
    decision. Ne mute rien. `roster_tickers` (optionnel, audit 2026-10-02)
    est transmis tel quel a exit_reason -- voir son docstring."""
    a_cloturer = []
    for position in open_positions:
        company = companies_by_ticker.get(position["ticker"])
        raison = exit_reason(position, company, today, roster_tickers=roster_tickers)
        if raison is None:
            continue
        a_cloturer.append({
            "position": position,
            "close_reason": raison,
            "current_price": company["current_price"] if company is not None else None,
        })
    return a_cloturer


# --- reconciliation ---------------------------------------------------

def reconcile(local_positions: list[dict], ibkr_positions: list[dict]) -> dict:
    """Rapproche le journal local de l'etat reel du compte IBKR, AVANT
    toute decision (spec 5.4). Le bot ne se fie jamais a son seul etat
    local — c'est aussi ce qui rend le batch idempotent : une relance le
    meme jour apres un plantage ne peut pas racheter une position deja
    ouverte.

    - position du journal absente chez IBKR (ou quantite nulle, negative
      ou absente/NaN cote IBKR) -> vendue hors bot : retiree du decompte
      des 10, jamais rouverte. Le bot ne doit jamais detenir de position
      negative (pas de short) : une quantite negative signale un probleme
      et est traitee comme une cloture hors bot plutot que comme active ;
    - position chez IBKR inconnue du journal -> IGNOREE : c'est une
      position de l'utilisateur, le bot n'y touche jamais (spec 9.5) ;
    - quantite divergente (mais positive) -> la quantite IBKR fait foi,
      l'ecart est remonte comme anomalie.

    Ne mute aucune position d'entree : les positions actives renvoyees
    sont des copies.

    Les conids sont normalises en int avant tout appariement : l'API
    IBKR est documentee comme incoherente ici (secdef/search renvoie
    parfois le conid en chaine dans ses exemples, /portfolio/.../
    positions en nombre) — sans coercion, un simple mismatch de type
    ferait disparaitre silencieusement une position pourtant bien
    detenue (classee a tort `cloturees_hors_bot`, sa place liberee, plus
    jamais geree en sortie). Un conid non numerique, d'un cote comme de
    l'autre, est traite comme non appariable plutot que de lever.
    """
    def _conid_int(valeur):
        try:
            return int(valeur)
        except (TypeError, ValueError):
            return None

    par_conid = {}
    for brute in ibkr_positions:
        conid = _conid_int(brute.get("conid"))
        if conid is not None:
            par_conid[conid] = brute

    actives, cloturees, anomalies = [], [], []
    conids_du_bot = set()
    for position in local_positions:
        conid = _conid_int(position.get("conid"))
        conids_du_bot.add(conid)
        brute = par_conid.get(conid)
        # .get("position") (sans defaut) renvoie None si la cle est
        # absente ET si IBKR envoie explicitement `"position": null` —
        # _is_missing() couvre les deux, ainsi que NaN. int(None) leverait
        # sinon un TypeError qui interromprait toute la reconciliation du
        # batch (spec 5.4 : jamais une ligne corrompue ne doit en bloquer
        # d'autres).
        quantite_brute = brute.get("position") if brute else None
        if brute is None or _is_missing(quantite_brute):
            cloturees.append(position)
            continue
        quantite_ibkr = int(quantite_brute)
        if quantite_ibkr <= 0:
            # Une quantite negative (short) ne doit jamais arriver pour
            # une position ouverte par ce bot : traitee comme fermee hors
            # bot plutot que comme active, jamais comme candidate a une
            # vente qui augmenterait le short.
            cloturees.append(position)
            continue
        active = dict(position)
        quantite_locale = position.get("quantite")
        if quantite_ibkr != quantite_locale:
            anomalie = {
                "ticker": position["ticker"],
                "conid": conid,
                "quantite_locale": quantite_locale,
                "quantite_ibkr": quantite_ibkr,
            }
            if quantite_ibkr > (quantite_locale or 0):
                # Audit 2026-10-02 : une quantite IBKR SUPERIEURE a la
                # quantite locale peut signifier un conid partage avec
                # une position personnelle de l'utilisateur sur ce meme
                # compte -- ne jamais l'adopter, pour ne jamais risquer
                # de vendre plus que ce que le bot a lui-meme ouvert. La
                # quantite locale est gardee telle quelle (active deja
                # une copie de `position`, donc `active["quantite"]` vaut
                # deja quantite_locale sans rien faire de plus).
                anomalie["quantite_ibkr_superieure"] = True
            else:
                active["quantite"] = quantite_ibkr
            anomalies.append(anomalie)
        actives.append(active)

    ignorees = [b for conid, b in par_conid.items() if conid not in conids_du_bot]

    return {
        "actives": actives,
        "cloturees_hors_bot": cloturees,
        "anomalies_quantite": anomalies,
        "ignorees": ignorees,
    }
