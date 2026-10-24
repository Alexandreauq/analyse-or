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
    pipeline (confirme par les Taches 2-3)."""
    date_cible = signal["date_cible"]
    actus_bornees = [a for a in signal["actus"] if a["date"] <= date_cible]
    actus_texte = "\n".join(
        f"- {a['date']} ({a['importance']}) : {a['titre']}" for a in actus_bornees
    ) or "(aucune actualite recente exploitable)"
    return (
        f"Tu es un analyste financier. Nous sommes le {date_cible}. "
        f"Ne raisonne qu'a partir des donnees fournies ci-dessous. "
        f"N'utilise JAMAIS ta connaissance generale d'evenements posterieurs "
        f"au {date_cible} -- si tu en as une, ignore-la completement.\n\n"
        f"Entreprise : {signal['name']} ({signal['ticker']}, secteur {signal.get('sector', '?')})\n"
        f"Phase technique (Weinstein) : {signal['stage_label']}\n"
        f"Score fondamental composite : {signal['score']:.1f}\n"
        f"Prix actuel : {signal['current_price']:.2f}, repere d'entree : {signal['entry_price']:.2f}\n"
        f"Actualites recentes (bornees au {date_cible}) :\n{actus_texte}\n\n"
        f"En raisonnant sur ces 4 dimensions (actualite, technique, secteur, volume), "
        f"donne un appel (haussier / neutre / baissier), un niveau de confiance "
        f"(tres_faible / faible / moyenne / elevee), et la dimension dominante de "
        f"ta decision, en JSON : "
        f'{{"appel": "...", "confiance": "...", "dimension_dominante": "...", "raison": "..."}}'
    )


def appelle_gemini(
    prompt: str, *, cle_api: str | None = None, modele: str = "gemini-2.5-flash",
    client=_appel_http_gemini, max_tentatives: int = 5, sleep_fn=time.sleep,
) -> dict:
    """Appelle l'API Gemini avec backoff simple sur ErreurLimiteDebit
    (niveau gratuit : 5 requetes/minute). `client` injectable pour les
    tests (defaut : _appel_http_gemini, l'appel HTTP reel). Lit la cle
    depuis GEMINI_API_KEY si `cle_api` est None."""
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
    import yfinance as yf
    historique = yf.Ticker(ticker).history(start=date_str, end=None)
    return float(historique["Close"].iloc[0])


def resout_pari(
    signal: dict, pari: dict, *, index_yfinance_ticker: str | None = None,
    prix_source=_prix_a_date,
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
    pour les deux niveaux de l'indice, qui n'ont pas d'equivalent
    deja calcule sur le signal."""
    import datetime as _dt
    index_ticker = index_yfinance_ticker or INDEX_YFINANCE_TICKER[signal["index"]]
    date_cible = signal["date_cible"]
    date_resolution = (
        _dt.datetime.strptime(date_cible, "%Y-%m-%d") + _dt.timedelta(days=HORIZON_RESOLUTION_JOURS)
    ).strftime("%Y-%m-%d")

    prix_entree = signal["current_price"]
    prix_sortie = prix_source(signal["ticker"], date_resolution)
    index_entree = prix_source(index_ticker, date_cible)
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
    import os
    dirname = os.path.dirname(chemin)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as fh:
        json.dump({"methode": "backtest_retroactif", "paris": paris_resolus}, fh, ensure_ascii=False, indent=2)
