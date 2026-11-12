"""Catalogue statique des ETF suivis dans l'onglet Indices.

Chaque valeur factuelle (ISIN, indice, frais courants, mode de réplication,
politique de distribution, éligibilité PEA) a été vérifiée le 2026-11-12 en
recoupant au moins deux sources indépendantes (fiche justETF + Boursorama,
extraETF, Morningstar, fiche émetteur ou DIC selon l'ETF). Les sources
retenues par ETF sont listées dans le message du commit qui a introduit ce
fichier (git log -- etf_catalog.py).

Ce sont des données publiques qui changent rarement mais qui CHANGENT
(baisses de frais, renommages "ESG" d'Amundi qui modifient l'indice suivi) :
à re-vérifier à la main avant toute mise à jour, jamais de mémoire.

`ter_pct` = frais courants (TER) du DIC, pas les "frais de gestion maximum"
affichés par Boursorama/Fortuneo, qui sont une autre grandeur (ex. CW8 :
0,28 % max affiché, 0,38 % de frais courants réels).

`benchmark_ticker` : ticker yfinance de l'indice quand il existe avec un
historique exploitable, sinon un ETF/future de proxy (commentaire au-dessus
de l'entrée). Attention pour le calcul d'écart de suivi : la plupart de ces
séries sont des indices de PRIX (dividendes non réinvestis) et plusieurs
sont en USD/JPY alors que l'ETF cote en EUR -- l'écart brut ETF vs
benchmark mélange donc frais, dividendes et effet de change.
"""

ETF_CATALOG = [
    # --- Larges / géographiques -------------------------------------------
    {
        "ticker": "CW8.PA",
        "name": "Amundi MSCI World Swap UCITS ETF EUR Acc",
        "isin": "LU1681043599",
        "benchmark_name": "MSCI World",
        # Indice MSCI World (prix, USD) tel que publié par Yahoo.
        "benchmark_ticker": "^990100-USD-STRD",
        "ter_pct": 0.38,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "DCAM.PA",
        "name": "Amundi PEA Monde (MSCI World) UCITS ETF Acc",
        "isin": "FR001400U5Q4",
        "benchmark_name": "MSCI World",
        "benchmark_ticker": "^990100-USD-STRD",
        "ter_pct": 0.20,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        # Émetteur non-Amundi (BlackRock iShares), même indice que CW8/DCAM.
        "ticker": "WPEA.PA",
        "name": "iShares MSCI World Swap PEA UCITS ETF EUR (Acc)",
        "isin": "IE0002XZSHO1",
        "benchmark_name": "MSCI World",
        "benchmark_ticker": "^990100-USD-STRD",
        "ter_pct": 0.20,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "PSP5.PA",
        "name": "Amundi PEA S&P 500 UCITS ETF Acc",
        "isin": "FR0011871128",
        "benchmark_name": "S&P 500",
        "benchmark_ticker": "^GSPC",
        "ter_pct": 0.12,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        # Émetteur non-Amundi (BNP Paribas AM), même indice que PSP5.
        "ticker": "ESE.PA",
        "name": "BNP Paribas Easy S&P 500 UCITS ETF EUR C",
        "isin": "FR0011550185",
        "benchmark_name": "S&P 500",
        "benchmark_ticker": "^GSPC",
        "ter_pct": 0.14,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "PUST.PA",
        "name": "Amundi PEA Nasdaq-100 UCITS ETF Acc",
        "isin": "FR0011871110",
        "benchmark_name": "Nasdaq-100",
        "benchmark_ticker": "^NDX",
        "ter_pct": 0.30,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        # Version non-PEA retenue : la version PEA d'Amundi (PAEEM) suit
        # désormais un indice "ESG Transition", pas le MSCI EM standard.
        "ticker": "AEEM.PA",
        "name": "Amundi MSCI Emerging Markets Swap UCITS ETF EUR Acc",
        "isin": "LU1681045370",
        "benchmark_name": "MSCI Emerging Markets",
        # Proxy : iShares MSCI Emerging Markets ETF (USD). L'indice lui-même
        # (^891800-USD-STRD) existe sur Yahoo mais sans historique exploitable.
        "benchmark_ticker": "EEM",
        "ter_pct": 0.20,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        "ticker": "PCEU.PA",
        "name": "Amundi PEA MSCI Europe UCITS ETF Acc",
        "isin": "FR0013412038",
        "benchmark_name": "MSCI Europe",
        # Indice MSCI Europe (prix, EUR) tel que publié par Yahoo.
        "benchmark_ticker": "^990500-EUR-STRD",
        "ter_pct": 0.15,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        # Exposition Japon : TOPIX et non MSCI Japan -- l'ETF MSCI Japan
        # d'Amundi coté à Paris (CJ1) suit désormais un indice "ESG Broad
        # Transition" ; le PEA Japon (TOPIX) est l'exposition large standard.
        "ticker": "PTPXE.PA",
        "name": "Amundi PEA Japon (TOPIX) UCITS ETF EUR Acc",
        "isin": "FR0013411980",
        "benchmark_name": "TOPIX",
        # Proxy : NEXT FUNDS TOPIX ETF (Tokyo, JPY) -- pas de ticker Yahoo
        # pour l'indice TOPIX lui-même.
        "benchmark_ticker": "1306.T",
        "ter_pct": 0.20,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "RS2K.PA",
        "name": "Amundi Russell 2000 UCITS ETF EUR Acc",
        "isin": "LU1681038672",
        "benchmark_name": "Russell 2000",
        "benchmark_ticker": "^RUT",
        "ter_pct": 0.35,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    # --- Zone euro / France ------------------------------------------------
    {
        "ticker": "CAC.PA",
        "name": "Amundi CAC 40 UCITS ETF Dist",
        "isin": "FR0007052782",
        "benchmark_name": "CAC 40",
        "benchmark_ticker": "^FCHI",
        "ter_pct": 0.25,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": True,
    },
    {
        "ticker": "C50.PA",
        "name": "Amundi Core EURO STOXX 50 UCITS ETF EUR Acc",
        "isin": "LU1681047236",
        "benchmark_name": "Euro Stoxx 50",
        "benchmark_ticker": "^STOXX50E",
        "ter_pct": 0.09,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "MFEC.PA",
        "name": "Amundi Core MSCI EMU UCITS ETF Acc",
        "isin": "LU1646361276",
        "benchmark_name": "MSCI EMU",
        # Proxy : iShares Core MSCI EMU UCITS ETF EUR (Acc), Xetra -- pas de
        # ticker Yahoo pour l'indice MSCI EMU lui-même.
        "benchmark_ticker": "SXR7.DE",
        "ter_pct": 0.12,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        # Émetteur non-Amundi (BNP Paribas AM).
        "ticker": "ETZ.PA",
        "name": "BNP Paribas Easy STOXX Europe 600 UCITS ETF EUR C",
        "isin": "FR0011550193",
        "benchmark_name": "STOXX Europe 600",
        "benchmark_ticker": "^STOXX",
        "ter_pct": 0.19,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "DAX.PA",
        "name": "Amundi DAX II UCITS ETF Acc",
        "isin": "LU0252633754",
        "benchmark_name": "DAX",
        "benchmark_ticker": "^GDAXI",
        "ter_pct": 0.15,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    # --- Matières premières ------------------------------------------------
    {
        # ETC (titre de créance adossé à de l'or physique), pas un OPCVM :
        # pas de distribution, non éligible PEA. La ligne Euronext Paris cote
        # en EUR (justETF ; prix identiques aux lignes EUR Xetra/Milan), même
        # si yfinance renvoie currency="USD" pour GOLD.PA (devise de
        # référence du produit) -- ne pas se fier à ce champ yfinance ici.
        "ticker": "GOLD.PA",
        "name": "Amundi Physical Gold ETC (C)",
        "isin": "FR0013416716",
        "benchmark_name": "Or (LBMA Gold Price PM, USD)",
        # Proxy : future or COMEX (USD) -- pas de ticker Yahoo pour le fixing
        # LBMA lui-même.
        "benchmark_ticker": "GC=F",
        "ter_pct": 0.12,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    # --- Sectoriels (Europe) -----------------------------------------------
    # Pas de ticker Yahoo pour les indices sectoriels STOXX Europe 600 : proxy
    # = l'ETF iShares (DE) en EUR sur Xetra qui suit le même indice.
    {
        "ticker": "HLT.PA",
        "name": "Amundi STOXX Europe 600 Healthcare UCITS ETF Acc",
        "isin": "LU1834986900",
        "benchmark_name": "STOXX Europe 600 Health Care",
        "benchmark_ticker": "EXV4.DE",
        "ter_pct": 0.30,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "TNO.PA",
        "name": "Amundi STOXX Europe 600 Technology UCITS ETF Acc",
        "isin": "LU1834988518",
        "benchmark_name": "STOXX Europe 600 Technology",
        "benchmark_ticker": "EXV3.DE",
        "ter_pct": 0.30,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "BNK.PA",
        "name": "Amundi STOXX Europe 600 Banks UCITS ETF Acc",
        "isin": "LU1834983477",
        "benchmark_name": "STOXX Europe 600 Banks",
        "benchmark_ticker": "EXV1.DE",
        "ter_pct": 0.30,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
]
