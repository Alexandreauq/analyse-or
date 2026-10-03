# scripts/probe_marketaux.py
# Vérification manuelle avec le VRAI token Marketaux (consomme le quota).
# Usage : MARKETAUX_API_TOKEN=... python scripts/probe_marketaux.py
# Rapporte : articles par ticker, distribution des match_score, validité du
# paramètre `countries`, et écrit un échantillon sans token pour les tests.
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant_ia.data_sources as data_sources
import assistant_ia.news as news

TICKERS_ECHANTILLON = ["MC.PA", "SAP.DE", "7203.T", "HO.PA", "NESN.SW"]
SORTIE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                      "tests", "assistant_ia", "fixtures", "marketaux_sample.json")


def main() -> None:
    if not os.environ.get("MARKETAUX_API_TOKEN"):
        print("MARKETAUX_API_TOKEN absent : rien à faire.")
        return
    maintenant = datetime.now(timezone.utc)
    echantillon = {}

    for ticker in TICKERS_ECHANTILLON:
        brut = data_sources.fetch_marketaux_news(
            news.params_entreprise(ticker, maintenant), 0, f"probe:{ticker}", cache={})
        echantillon[ticker] = brut
        if "erreur" in brut:
            print(f"{ticker} : ERREUR {brut['erreur']}")
            continue
        scores = [e.get("match_score") for a in brut.get("data", [])
                  for e in a.get("entities", []) if e.get("symbol") == ticker]
        retenus = news.normalise(brut, ticker=ticker)
        print(f"{ticker} : {len(brut.get('data', []))} brut, {len(retenus)} retenus, "
              f"scores={sorted(s for s in scores if s is not None)}")

    brut_marche = data_sources.fetch_marketaux_news(
        news.params_marche(maintenant), 0, "probe:marche", cache={})
    echantillon["marche"] = brut_marche
    if "erreur" in brut_marche:
        print(f"MARCHE : ERREUR {brut_marche['erreur']} (vérifier le paramètre 'countries')")
    else:
        print(f"MARCHE : {len(brut_marche.get('data', []))} brut, "
              f"{len(news.normalise(brut_marche))} retenus")

    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as fh:
        json.dump(echantillon, fh, ensure_ascii=False, indent=2)
    print(f"Échantillon écrit dans {os.path.normpath(SORTIE)} (sans token).")


if __name__ == "__main__":
    main()
