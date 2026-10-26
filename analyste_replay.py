# analyste_replay.py
# Harnais de replay Gemini pour "l'Analyste" -- voir docs/superpowers/
# specs/2026-10-24-analyste-backtest-design.md. Consomme les signaux
# produits par analyste_backtest.py (deja isoles point-in-temps), construit
# le meme raisonnement a 4 dimensions que le test prospectif, avec une
# instruction anti-fuite explicite, et ecrit les resultats resolus
# immediatement (Tache 7).
import json
import os
import time

import analyste_backtest
import ibkr_bot.signals
import indices_score

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modele}:generateContent"


class ErreurLimiteDebit(Exception):
    """Leve par `client` sur un 429 Gemini -- distincte d'une erreur
    reseau quelconque pour que appelle_gemini sache quand reessayer."""


def _appel_http_gemini(prompt: str, cle_api: str, modele: str, *, http_post=None) -> dict:
    """Implementation reelle par defaut de `client` (appelle_gemini) :
    POST direct a l'API Gemini (meme endpoint verifie manuellement en
    session lors de la validation de la cle -- generativelanguage.
    googleapis.com/v1beta/models/{modele}:generateContent). Demande une
    reponse JSON structuree via responseMimeType (pas de parsing fragile
    d'un texte libre). Leve ErreurLimiteDebit sur un 429, toute autre
    erreur HTTP remonte telle quelle (pas de degrade silencieux -- une
    reponse de pari manquante doit etre visible, jamais remplacee par une
    valeur par defaut qui fausserait le backtest)."""
    import requests as _requests
    http_post = http_post or _requests.post
    url = GEMINI_API_URL.format(modele=modele)
    corps = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    reponse = http_post(url, params={"key": cle_api}, json=corps, timeout=30)
    if reponse.status_code == 429:
        raise ErreurLimiteDebit(reponse.text[:200])
    reponse.raise_for_status()
    texte = reponse.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(texte)


def _fmt_pct(valeur: float | None) -> str:
    return f"{valeur:+.1f}%" if valeur is not None else "n/d"


def construit_prompt(signal: dict) -> str:
    """Meme raisonnement a 4 dimensions (actualite, technique, secteur,
    volume) que docs/superpowers/experiments/2026-10-19-ia-news-reasoning-
    test.json, avec une instruction anti-fuite explicite -- jamais de
    connaissance generale d'evenements posterieurs a date_cible. N'inclut
    QUE les actus de `signal["actus"]` (deja bornees point-in-temps par
    analyste_backtest.py, Taches 2-3) -- ne jamais ajouter de contexte non
    fourni explicitement ici.

    Filtre defensivement `signal["actus"]` sur `date_cible` ici meme si
    Taches 2-3 garantissent deja l'isolation en amont -- meme ceinture et
    bretelles que `actualites_entreprise_point_in_temps` (Tache 2) et
    `historique_prix_volume_point_in_temps` (Tache 3), qui re-filtrent
    chacune leur propre source deja censee respecter la borne. Comparaison
    de chaines : les deux valeurs sont des ISO "YYYY-MM-DD" dans ce
    pipeline (confirme par les Taches 2-3).

    FIX 8 (revue finale) : surface aussi `rendement_5j_pct`/
    `rendement_20j_pct`/`volume_ratio_5j_vs_20j` (deja calcules par
    score_point_in_temps, analyste_backtest.py, sur l'historique deja
    tronque point-in-temps -- aucune fuite supplementaire) pour donner au
    modele une vraie donnee sur les dimensions "technique"/"volume" du
    raisonnement a 4 dimensions, plutot que de lui demander de raisonner
    sur du vide qu'il serait tente de combler par sa connaissance
    generale -- exactement ce que l'instruction anti-fuite essaie
    d'empecher. Accede via `.get()` (jamais par crochet direct) : les
    signaux plus anciens / les fixtures de test existantes n'ont pas ces
    champs, et doivent continuer a produire un prompt valide (repli
    "n/d"). Ne tente PAS de combler la dimension "secteur" par un contexte
    de pairs du meme secteur/indice a la meme date -- demanderait de
    construire un pool comparatif inter-entreprises a date fixee, le meme
    type de simplification deja acceptee pour le pool de recalibration de
    percentile (Tache 5, score_recalibrated=False) ; hors perimetre de ce
    chantier pour la meme raison."""
    date_cible = signal["date_cible"]
    actus_bornees = [a for a in signal["actus"] if a["date"] <= date_cible]
    actus_texte = "\n".join(
        f"- {a['date']} ({a['importance']}) : {a['titre']}" for a in actus_bornees
    ) or "(aucune actualite recente exploitable)"
    rendement_5j = _fmt_pct(signal.get("rendement_5j_pct"))
    rendement_20j = _fmt_pct(signal.get("rendement_20j_pct"))
    ratio_volume = signal.get("volume_ratio_5j_vs_20j")
    ratio_volume_txt = f"{ratio_volume:.2f}" if ratio_volume is not None else "n/d"
    return (
        f"Tu es un analyste financier. Nous sommes le {date_cible}. "
        f"Ne raisonne qu'a partir des donnees fournies ci-dessous. "
        f"N'utilise JAMAIS ta connaissance generale d'evenements posterieurs "
        f"au {date_cible} -- si tu en as une, ignore-la completement.\n\n"
        f"Entreprise : {signal['name']} ({signal['ticker']}, secteur {signal.get('sector', '?')})\n"
        f"Phase technique (Weinstein) : {signal['stage_label']}\n"
        f"Score fondamental composite : {signal['score']:.1f}\n"
        f"Prix actuel : {signal['current_price']:.2f}, repere d'entree : {signal['entry_price']:.2f}\n"
        f"Rendement 5j / 20j : {rendement_5j} / {rendement_20j} -- ratio volume 5j vs 20j : {ratio_volume_txt}\n"
        f"Actualites recentes (bornees au {date_cible}) :\n{actus_texte}\n\n"
        f"En raisonnant sur ces 4 dimensions (actualite, technique, secteur, volume), "
        f"donne un appel (haussier / neutre / baissier), un niveau de confiance "
        f"(tres_faible / faible / moyenne / elevee), et la dimension dominante de "
        f"ta decision, en JSON : "
        f'{{"appel": "...", "confiance": "...", "dimension_dominante": "...", "raison": "..."}}'
    )


def appelle_gemini(
    prompt: str, *, cle_api: str | None = None, modele: str = "gemini-3.8-flash",
    client=_appel_http_gemini, max_tentatives: int = 5, sleep_fn=time.sleep,
) -> dict:
    """Appelle l'API Gemini avec backoff simple sur ErreurLimiteDebit.
    `modele` : gemini-2.5-flash (choix initial du plan) a ete retire pour
    les nouveaux utilisateurs cote Google le 2026-10-25 (404 "no longer
    available to new users"), remplace par gemini-3.8-flash -- verifie
    directement contre l'API ce jour-la (requete reelle, cle_api=cle
    utilisateur, responseMimeType=application/json : reponse 200, JSON
    valide dans parts[0]["text"], pas de part "reflexion" separee qui
    casserait l'indexation). `client` injectable pour les tests (defaut :
    _appel_http_gemini, l'appel HTTP reel). Lit la cle depuis
    GEMINI_API_KEY si `cle_api` est None."""
    cle_api = cle_api or os.environ.get("GEMINI_API_KEY")
    derniere_erreur = None
    for tentative in range(max_tentatives):
        try:
            return client(prompt, cle_api, modele)
        except ErreurLimiteDebit as e:
            derniere_erreur = e
            if tentative < max_tentatives - 1:
                sleep_fn(60)  # niveau gratuit : 5 req/min, on attend une minute pleine
    raise derniere_erreur


HORIZON_RESOLUTION_JOURS = 30  # ~1 mois, meme ordre de grandeur que le test prospectif

INDEX_YFINANCE_TICKER = {
    "CAC40": "^FCHI", "DAX": "^GDAXI", "NASDAQ": "^IXIC", "DOW": "^DJI",
    "FTSE": "^FTSE", "SMI": "^SSMI", "IBEX35": "^IBEX", "FTSEMIB": "FTSEMIB.MI",
}


def _prix_a_date(ticker: str, date_str: str) -> float:
    """BUG REEL (backtest du 2026-10-26) : yfinance cote les actions LSE
    (suffixe .L) en PENCE (GBp), jamais en livres -- indices_score.py
    divise deja par 100 a la source (signal["current_price"], utilise
    comme prix_entree dans resout_pari, est donc en livres), mais un
    appel yfinance brut ici renvoyait la cloture en pence sans la meme
    conversion : return_pct/alpha_pct explosaient a +11000% sur les
    tickers .L (ex. STAN.L : prix_entree ~17 livres compare a prix_sortie
    ~1780 pence, facteur ~100 parasite). ibkr_bot.sizing.from_quotation_price
    applique deja exactement cette conversion (uniquement pour les
    tickers .L, jamais pour un indice ^FTSE qui cote en points) -- reutilise
    ici plutot que reimplementee."""
    import yfinance as yf
    import ibkr_bot.sizing as sizing
    historique = yf.Ticker(ticker).history(start=date_str, end=None)
    return sizing.from_quotation_price(float(historique["Close"].iloc[0]), ticker)


def _prix_avant_date(ticker: str, date_str: str) -> float:
    """Dernier cours de cloture STRICTEMENT avant date_str -- aligne
    l'entree indice sur l'entree action (signal["current_price"], deja
    "dernier close avant date_cible", voir le commentaire de resout_pari).

    FIX 4 (revue finale) : `_prix_a_date` renvoie le PREMIER close ON OU
    APRES sa date -- utilisee jusqu'ici aussi pour `index_entree`, alors
    que `prix_entree` (action) est le DERNIER close AVANT date_cible.
    Quand date_cible tombe un week-end (2/7 du temps), ces deux
    conventions divergent (vendredi pour l'action, lundi pour l'indice) :
    alpha_pct mesurait alors en partie un mouvement d'indice que l'action
    n'a jamais partage, puisque le marche actions etait ferme ce jour-la
    pour elle aussi. Utilisee UNIQUEMENT pour index_entree ci-dessous --
    la sortie (action et indice) reste sur `prix_source`/`_prix_a_date`,
    les deux dates de sortie etant deja mutuellement coherentes (meme
    convention "a ou apres" des deux cotes).

    Meme correctif pence/livres que _prix_a_date -- voir sa docstring."""
    import yfinance as yf
    import ibkr_bot.sizing as sizing
    historique = yf.Ticker(ticker).history(end=date_str)
    return sizing.from_quotation_price(float(historique["Close"].dropna().iloc[-1]), ticker)


def resout_pari(
    signal: dict, pari: dict, *, index_yfinance_ticker: str | None = None,
    prix_source=_prix_a_date, prix_source_avant=_prix_avant_date,
) -> dict:
    """Resolution IMMEDIATE (le prix ~1 mois apres date_cible est deja
    connu aujourd'hui, contrairement au test prospectif qui doit attendre
    que le temps passe reellement). `index_yfinance_ticker` : resolu via
    INDEX_YFINANCE_TICKER[signal["index"]] si non fourni.

    `prix_entree` est pris directement sur `signal["current_price"]`
    (deja calcule par `score_point_in_temps` dans analyste_backtest.py,
    cf. `historique_prix_volume_point_in_temps` qui tronque avec
    `historique.index < date_cible`) plutot que via un nouvel appel a
    `prix_source` : c'est litteralement le prix sur lequel le signal
    d'entree (near_entry / score) a ete decide. Un appel `prix_source(...,
    start=date_cible)` renverrait le premier jour de bourse ON OU APRES
    date_cible -- potentiellement un jour et un prix differents de celui
    utilise par le signal lui-meme. On evite donc cette incoherence (et
    un appel reseau redondant) en reutilisant `current_price` tel quel ;
    il est garanti non-None pour tout signal qui arrive jusqu'ici (sinon
    score_point_in_temps renvoie None et la Tache 5 filtre le signal en
    amont). `prix_source` reste utilise pour la sortie de l'action et
    pour la sortie de l'indice (les deux "a ou apres" date_resolution,
    deja mutuellement coherentes) ; `prix_source_avant` (FIX 4) est
    utilise UNIQUEMENT pour l'entree de l'indice, pour l'aligner sur la
    meme convention "dernier close avant" que `prix_entree`."""
    import datetime as _dt
    index_ticker = index_yfinance_ticker or INDEX_YFINANCE_TICKER[signal["index"]]
    date_cible = signal["date_cible"]
    date_resolution = (
        _dt.datetime.strptime(date_cible, "%Y-%m-%d") + _dt.timedelta(days=HORIZON_RESOLUTION_JOURS)
    ).strftime("%Y-%m-%d")

    prix_entree = signal["current_price"]
    prix_sortie = prix_source(signal["ticker"], date_resolution)
    index_entree = prix_source_avant(index_ticker, date_cible)
    index_sortie = prix_source(index_ticker, date_resolution)

    return_pct = (prix_sortie - prix_entree) / prix_entree * 100
    index_return_pct = (index_sortie - index_entree) / index_entree * 100

    return {
        **signal, **pari, "methode": "backtest_retroactif",
        "date_resolution": date_resolution,
        "return_pct": return_pct, "index_return_pct": index_return_pct,
        "alpha_pct": return_pct - index_return_pct,
    }


def ecrit_resultats(paris_resolus: list[dict], chemin: str) -> None:
    dirname = os.path.dirname(chemin)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    # Correctif anti-corruption (revue 2) : ecrire directement dans
    # `chemin` avec "w" laissait un fichier TRONQUE et invalide si
    # json.dump levait en cours de route (NaN, allow_nan=False) --
    # confirme empiriquement par la revue precedente. On ecrit plutot
    # dans un fichier temporaire puis on bascule avec os.replace() :
    # si json.dump leve, le fichier final (celui de la derniere
    # ecriture reussie) reste intact.
    chemin_temp = chemin + ".tmp"
    with open(chemin_temp, "w", encoding="utf-8") as fh:
        # FIX 6 (revue finale) : allow_nan=False -- meme pattern que
        # indices_score.py::save_risk_free_rate_cache. json.dump ecrit NaN
        # tel quel par defaut (JSON invalide au sens strict, que
        # JSON.parse()/Response.json() refusent) ; un seul NaN avait deja
        # suffi a casser le chargement d'un fichier entier par le passe
        # (incident documente) -- echouer tot et bruyamment plutot que
        # d'ecrire un fichier silencieusement corrompu.
        json.dump({"methode": "backtest_retroactif", "paris": paris_resolus}, fh,
                   ensure_ascii=False, indent=2, allow_nan=False)
    os.replace(chemin_temp, chemin)


def main(chemin_sortie: str = "docs/superpowers/experiments/2026-10-24-analyste-backtest-6-12-mois.json") -> None:
    """Point d'entree bout-en-bout : echantillonnage (Tache 5) -> pour
    chaque signal, prompt + appel Gemini (Tache 6) -> resolution
    immediate (Tache 7) -> ecriture. Lance une fois, manuellement --
    pas de timer/cron (chantier ponctuel, pas un service).

    Filtre `indices_score.COMPANIES` sur `ibkr_bot.signals.INDICES_IN_SCOPE`
    avant de le passer a `genere_signaux_historiques` : COMPANIES couvre
    les 10 indices suivis par l'app (dont NIKKEI225 et HANGSENG), mais le
    perimetre de ce backtest est explicitement les 8 indices passes au
    reel (meme liste que le plan, exactement ibkr_bot.signals.
    INDICES_IN_SCOPE -- une seule source de verite, pas de risque de
    derive). Sans ce filtre, le tirage pourrait produire un signal
    NIKKEI225/HANGSENG, qui ferait lever un KeyError dans resout_pari
    (INDEX_YFINANCE_TICKER ci-dessus ne couvre que les 8 indices du
    perimetre).

    FIX 5 (revue finale) : la cle GEMINI_API_KEY est verifiee TOUT DE
    SUITE, avant tout echantillonnage -- sinon son absence n'etait
    decouverte qu'au premier appel Gemini, APRES avoir deja depense le
    budget d'appels yfinance/FRED de l'echantillonnage complet. Chaque
    signal est aussi isole dans son propre try/except (une erreur
    Gemini/reseau sur un seul pari ne doit jamais faire perdre les
    paris deja resolus dans la meme execution), et ecrit_resultats est
    appele apres CHAQUE signal reussi (pas seulement a la toute fin) --
    elle ecrase tout le fichier a chaque fois, ce qui convient a cette
    taille de donnees, et garantit qu'un crash en cours de route laisse
    quand meme un fichier avec tout ce qui a deja reussi."""
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError(
            "GEMINI_API_KEY n'est pas definie -- requise avant de lancer "
            "l'echantillonnage (qui consomme du budget yfinance/FRED pour "
            "rien si l'appel Gemini est de toute facon impossible ensuite).")

    companies_perimetre = [
        c for c in indices_score.COMPANIES if c["index"] in ibkr_bot.signals.INDICES_IN_SCOPE
    ]
    risk_free_rate_by_currency = {
        currency: indices_score.fetch_risk_free_rate(series_id)
        for currency, series_id in indices_score.RISK_FREE_SERIES_BY_CURRENCY.items()
    }
    fx_rate_by_currency = {
        currency: indices_score.fetch_fx_rate_to_usd(currency)
        for currency in set(indices_score.INDEX_CURRENCY.values())
    }
    signaux = analyste_backtest.genere_signaux_historiques(
        companies_perimetre, risk_free_rate_by_currency, fx_rate_by_currency)

    paris_resolus = []
    for signal in signaux:
        try:
            prompt = construit_prompt(signal)
            pari = appelle_gemini(prompt)
            paris_resolus.append(resout_pari(signal, pari))
        except Exception as e:
            print(f"Pari ignore ({signal.get('ticker')}, {signal.get('date_cible')}) : {e!r}")
            continue
        try:
            ecrit_resultats(paris_resolus, chemin_sortie)
        except Exception as e:
            # Correctif (revue 2) : un echec isole de L'ECRITURE elle-meme
            # (ex. NaN dans CE pari precis, allow_nan=False) ne doit pas
            # non plus faire perdre tout le run -- meme garde-fou que le
            # try/except ci-dessus pour Gemini/resolution, mais a l'etage
            # de l'ecriture (c'est elle qui, avant ce correctif, n'etait
            # PAS couverte et faisait remonter l'exception jusqu'a faire
            # sortir main() entierement). On retire aussi le pari qui
            # vient de faire echouer l'ecriture : le laisser dans
            # paris_resolus ferait echouer TOUTES les ecritures suivantes
            # a cause de lui, meme pour des paris sains.
            print(f"Ecriture ignoree ({signal.get('ticker')}, {signal.get('date_cible')}) : {e!r}")
            paris_resolus.pop()

    if not paris_resolus:
        # Aucun pari resolu avec succes (cas limite) -- que ce soit parce
        # qu'aucun signal n'a ete echantillonne, OU parce que tous les
        # signaux echantillonnes ont echoue en resolution/ecriture (FIX,
        # revue 2 -- l'ancienne condition `if not signaux` ne couvrait
        # pas ce second cas) : ecrit tout de meme un fichier (vide) plutot
        # que rien. Jamais de double ecriture dans le cas normal : quand
        # paris_resolus n'est pas vide, la boucle ci-dessus l'a deja
        # ecrit a sa derniere iteration reussie.
        ecrit_resultats(paris_resolus, chemin_sortie)


if __name__ == "__main__":
    main()
