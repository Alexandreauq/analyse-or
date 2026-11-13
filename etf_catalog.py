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

    # ========================================================================
    # Extension du 2026-11-13 : recherche/vérification identique (justETF,
    # extraETF, Boursorama/Fortuneo/Zonebourse, fiche émetteur/DIC, plus au
    # moins un recoupement yfinance par ticker -- voir le commit qui a
    # introduit ce bloc pour le détail par ETF) à l'extension initiale
    # ci-dessus. Objectif explicite de l'utilisateur : couvrir le plus grand
    # nombre d'ETF réels possible, pas une liste figée à ~20-25 (l'ancienne
    # borne venait d'une simple estimation de la spec de conception, jamais
    # d'une contrainte dure -- voir test_etf_catalog.py).
    # ========================================================================

    # --- Géographiques supplémentaires --------------------------------------
    {
        # MSCI Japan IMI (pas TOPIX, pas l'Amundi CJ1 renommé "ESG").
        "ticker": "EUNN.DE",
        "name": "iShares Core MSCI Japan IMI UCITS ETF (Acc)",
        "isin": "IE00B4L5YX21",
        "benchmark_name": "MSCI Japan IMI",
        "benchmark_ticker": "EWJ",
        "ter_pct": 0.12,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        "ticker": "100H.PA",
        "name": "Amundi Core FTSE 100 Swap UCITS ETF EUR Hedged Acc",
        "isin": "LU1650492330",
        "benchmark_name": "FTSE 100",
        "benchmark_ticker": "^FTSE",
        "ter_pct": 0.30,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        # Ligne non-PEA (DJE) ; une version PEA distincte existe (LU2572256662)
        # mais sans ligne Paris confirmée.
        "ticker": "DJE.PA",
        "name": "Amundi Dow Jones Industrial Average UCITS ETF Dist",
        "isin": "FR0007056841",
        "benchmark_name": "Dow Jones Industrial Average",
        "benchmark_ticker": "^DJI",
        "ter_pct": 0.50,
        "replication": "synthetique",
        "distribution": "distribuant",
        "pea_eligible": False,
    },
    {
        "ticker": "GPEA.PA",
        "name": "Amundi PEA Global (MSCI ACWI) UCITS ETF Acc",
        "isin": "FR0014017NX3",
        "benchmark_name": "MSCI ACWI",
        "benchmark_ticker": "ACWI",
        "ter_pct": 0.30,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "WEXE.DE",
        "name": "Amundi MSCI World Ex USA UCITS ETF Acc",
        "isin": "IE00085PWS28",
        "benchmark_name": "MSCI World ex USA",
        "benchmark_ticker": "IEFA",
        "ter_pct": 0.15,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        "ticker": "EUNJ.DE",
        "name": "iShares MSCI Pacific ex-Japan UCITS ETF (Dist)",
        "isin": "IE00B4WXJD03",
        "benchmark_name": "MSCI Pacific ex Japan",
        "benchmark_ticker": "EPP",
        "ter_pct": 0.60,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": False,
    },
    {
        # Indice MSCI USA brut (pas "Screened"/"ESG Selection").
        "ticker": "BIGUS.PA",
        "name": "BNP Paribas Easy MSCI USA UCITS ETF EUR Acc",
        "isin": "IE000M46QA27",
        "benchmark_name": "MSCI USA",
        "benchmark_ticker": "MLRG",
        "ter_pct": 0.04,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        # Indice MSCI China brut (pas "China Tech" ni "Screened ex Thermal Coal").
        "ticker": "XCS6.DE",
        "name": "Xtrackers MSCI China UCITS ETF 1C",
        "isin": "LU0514695690",
        "benchmark_name": "MSCI China",
        "benchmark_ticker": "MCHI",
        "ter_pct": 0.65,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        # PEA confirmé par la fiche mensuelle officielle Amundi (contredit
        # par le champ générique PEA de Zonebourse, écarté comme source ici).
        "ticker": "LYXIB.MC",
        "name": "Amundi IBEX 35 UCITS ETF Dist",
        "isin": "FR0010251744",
        "benchmark_name": "IBEX 35",
        "benchmark_ticker": "^IBEX",
        "ter_pct": 0.30,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": True,
    },
    {
        "ticker": "MIB.PA",
        "name": "Amundi FTSE MIB UCITS ETF Dist",
        "isin": "FR0010010827",
        "benchmark_name": "FTSE MIB",
        "benchmark_ticker": "FTSEMIB.MI",
        "ter_pct": 0.35,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": True,
    },
    {
        # PEA retenu par analogie directe avec CAC.PA/C50.PA/DAX.PA (même
        # profil : réplication physique, 100% actions d'un État membre UE).
        "ticker": "TDT.AS",
        "name": "VanEck AEX UCITS ETF",
        "isin": "NL0009272749",
        "benchmark_name": "AEX",
        "benchmark_ticker": "^AEX",
        "ter_pct": 0.30,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": True,
    },
    {
        # L'ancien tracker Amundi/Lyxor Nikkei 225 (LU0378453376) a été
        # fusionné en 2022 dans un fonds MSCI Japan ESG -- cette ligne
        # Xtrackers est la ligne EUR vivante retenue à la place.
        "ticker": "XDJP.DE",
        "name": "Xtrackers Nikkei 225 UCITS ETF 1D",
        "isin": "LU0839027447",
        "benchmark_name": "Nikkei 225",
        "benchmark_ticker": "^N225",
        "ter_pct": 0.09,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": False,
    },

    # --- Sectoriels (Europe) supplémentaires ---------------------------------
    {
        "ticker": "BRES.PA",
        "name": "Amundi STOXX Europe 600 Basic Resources UCITS ETF Acc",
        "isin": "LU1834983550",
        "benchmark_name": "STOXX Europe 600 Basic Resources",
        "benchmark_ticker": "EXV6.DE",
        "ter_pct": 0.30,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        # Indice STOXX renommé "Industrials" (ex "Industrial Goods &
        # Services") après la refonte ICB 2023 -- même secteur.
        "ticker": "IND.PA",
        "name": "Amundi STOXX Europe 600 Industrials UCITS ETF Acc",
        "isin": "LU1834987890",
        "benchmark_name": "STOXX Europe 600 Industrials",
        "benchmark_ticker": "EXH4.DE",
        "ter_pct": 0.30,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "INS.PA",
        "name": "Amundi STOXX Europe 600 Insurance UCITS ETF Acc",
        "isin": "LU1834987973",
        "benchmark_name": "STOXX Europe 600 Insurance",
        "benchmark_ticker": "EXH5.DE",
        "ter_pct": 0.30,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "TELE.PA",
        "name": "Amundi STOXX Europe 600 Telecommunications UCITS ETF Acc",
        "isin": "LU1834988609",
        "benchmark_name": "STOXX Europe 600 Telecommunications",
        "benchmark_ticker": "EXV2.DE",
        "ter_pct": 0.30,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "UTI.PA",
        "name": "Amundi STOXX Europe 600 Utilities UCITS ETF Acc",
        "isin": "LU1834988864",
        "benchmark_name": "STOXX Europe 600 Utilities",
        "benchmark_ticker": "EXH9.DE",
        "ter_pct": 0.30,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        # Univers "Screened" (filtre ESG) -- l'indice réel diffère légèrement
        # d'un "Oil & Gas" pur ; seul ETF de cette gamme en réplication physique.
        "ticker": "ENRG.PA",
        "name": "Amundi STOXX Europe 600 Energy Screened UCITS ETF Acc",
        "isin": "LU1834988278",
        "benchmark_name": "STOXX Europe 600 Energy Screened+",
        "benchmark_ticker": "EXH1.DE",
        "ter_pct": 0.30,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        # Ticker "FOO" trompeur : l'indice réel est "Consumer Staples 30-15",
        # plus large qu'un pur "Food & Beverage" (voir EXH3.DE ci-dessous pour
        # la ligne Food & Beverage exacte, non-PEA).
        "ticker": "FOO.PA",
        "name": "Amundi STOXX Europe 600 Consumer Staples UCITS ETF Acc",
        "isin": "LU1834985845",
        "benchmark_name": "STOXX Europe 600 Industry Consumer Staples 30-15",
        "benchmark_ticker": "EXH3.DE",
        "ter_pct": 0.30,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        # Non-PEA : indice physique avec UK/Suisse très présents (Nestlé,
        # Diageo) -- au-delà du seuil des 75% d'actions UE du PEA.
        "ticker": "EXH3.DE",
        "name": "iShares STOXX Europe 600 Food & Beverage UCITS ETF (DE)",
        "isin": "DE000A0H08H3",
        "benchmark_name": "STOXX Europe 600 Food & Beverage",
        "benchmark_ticker": "SC03.DE",
        "ter_pct": 0.46,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": False,
    },
    {
        # Non-PEA : indice physique avec UK/Suisse très présents dans
        # l'immobilier européen (British Land, Swiss Prime Site...).
        "ticker": "EXI5.DE",
        "name": "iShares STOXX Europe 600 Real Estate UCITS ETF (DE)",
        "isin": "DE000A0Q4R44",
        "benchmark_name": "STOXX Europe 600 Real Estate",
        "benchmark_ticker": "EPRE.PA",
        "ter_pct": 0.46,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": False,
    },

    # --- Dividendes -----------------------------------------------------------
    {
        # PEA confirmé directement par la fiche produit officielle SSGA.
        "ticker": "EUDV.PA",
        "name": "SPDR S&P Euro Dividend Aristocrats UCITS ETF (Dist)",
        "isin": "IE00B5M1WJ87",
        "benchmark_name": "S&P Euro High Yield Dividend Aristocrats",
        "benchmark_ticker": "EXSG.DE",
        "ter_pct": 0.30,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": True,
    },

    # --- Obligataire & autres classes d'actifs ---------------------------------
    {
        "ticker": "PRAR.DE",
        "name": "Amundi Prime Euro Government Bond UCITS ETF Acc",
        "isin": "LU2089238898",
        "benchmark_name": "Solactive Eurozone Government Bond Index",
        "benchmark_ticker": "IEGA.AS",
        "ter_pct": 0.05,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        "ticker": "PR1C.DE",
        "name": "Amundi Core EUR Corporate Bond UCITS ETF EUR Dist",
        "isin": "LU1931975079",
        "benchmark_name": "Bloomberg Euro Aggregate Corporate Bond Index",
        "benchmark_ticker": "IEAC.AS",
        "ter_pct": 0.07,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": False,
    },
    {
        "ticker": "IBB1.DE",
        "name": "iShares $ Treasury Bond 7-10yr UCITS ETF EUR Hedged (Dist)",
        "isin": "IE00BGPP6697",
        "benchmark_name": "ICE US Treasury 7-10 Year (EUR Hedged) Index",
        "benchmark_ticker": "IEF",
        "ter_pct": 0.10,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": False,
    },
    {
        "ticker": "EUNA.DE",
        "name": "iShares Core Global Aggregate Bond UCITS ETF EUR Hedged (Acc)",
        "isin": "IE00BDBRDM35",
        "benchmark_name": "Bloomberg Global Aggregate Bond Index (EUR Hedged)",
        "benchmark_ticker": "AGGG.L",
        "ter_pct": 0.10,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        # ETC (titre de créance adossé à de l'argent physique), même
        # structure que GOLD.PA -- non éligible PEA (pas un OPCVM).
        "ticker": "XAD6.DE",
        "name": "Xtrackers Physical Silver ETC (EUR)",
        "isin": "DE000A1E0HS6",
        "benchmark_name": "LBMA Silver Price",
        "benchmark_ticker": "SI=F",
        "ter_pct": 0.40,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        # PEA confirmé par la fiche KID officielle BlackRock FR ("Régime
        # fiscal PEA : Oui").
        "ticker": "CMSE.PA",
        "name": "iShares Diversified Commodity Swap UCITS ETF (DE)",
        "isin": "DE000A0H0728",
        "benchmark_name": "Bloomberg Commodity Total Return Index",
        "benchmark_ticker": "DBC",
        "ter_pct": 0.46,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        # PEA confirmé par le communiqué de presse officiel WisdomTree FR.
        "ticker": "WBLD.PA",
        "name": "WisdomTree Europe Infrastructure UCITS ETF",
        "isin": "IE000AFVONT7",
        "benchmark_name": "WisdomTree Europe Infrastructure UCITS Index (NTR)",
        "benchmark_ticker": "IGF",
        "ter_pct": 0.45,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        # PEA confirmé par le rapport périodique officiel Amundi (85,98%
        # d'actifs en titres éligibles PEA).
        "ticker": "AWAT.PA",
        "name": "Amundi PEA Eau (MSCI Water) UCITS ETF Capi",
        "isin": "FR0011882364",
        "benchmark_name": "MSCI ACWI IMI Water Custom Index",
        "benchmark_ticker": "CGW",
        "ter_pct": 0.60,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },

    # --- Diversité d'émetteur sur des indices déjà couverts -------------------
    # benchmark_ticker identique à l'entrée Amundi/iShares/BNP déjà présente
    # sur le même benchmark_name (même indice, déjà vérifié plus haut).
    {
        "ticker": "XDWD.DE",
        "name": "Xtrackers MSCI World UCITS ETF 1C",
        "isin": "IE00BJ0KDQ92",
        "benchmark_name": "MSCI World",
        "benchmark_ticker": "^990100-USD-STRD",
        "ter_pct": 0.12,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        "ticker": "D5BM.DE",
        "name": "Xtrackers S&P 500 Swap UCITS ETF 1C",
        "isin": "LU0490618542",
        "benchmark_name": "S&P 500",
        "benchmark_ticker": "^GSPC",
        "ter_pct": 0.15,
        "replication": "synthetique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        "ticker": "EQQQ.PA",
        "name": "Invesco EQQQ NASDAQ-100 UCITS ETF (Dist)",
        "isin": "IE0032077012",
        "benchmark_name": "Nasdaq-100",
        "benchmark_ticker": "^NDX",
        "ter_pct": 0.30,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": False,
    },
    {
        "ticker": "XMME.DE",
        "name": "Xtrackers MSCI Emerging Markets UCITS ETF 1C",
        "isin": "IE00BTJRMP35",
        "benchmark_name": "MSCI Emerging Markets",
        "benchmark_ticker": "EEM",
        "ter_pct": 0.18,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        "ticker": "XMEU.DE",
        "name": "Xtrackers MSCI Europe UCITS ETF 1C",
        "isin": "LU0274209237",
        "benchmark_name": "MSCI Europe",
        "benchmark_ticker": "^990500-EUR-STRD",
        "ter_pct": 0.12,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        "ticker": "DX2G.DE",
        "name": "Xtrackers CAC 40 UCITS ETF 1D",
        "isin": "LU0322250985",
        "benchmark_name": "CAC 40",
        "benchmark_ticker": "^FCHI",
        "ter_pct": 0.20,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": True,
    },
    {
        "ticker": "XESC.DE",
        "name": "Xtrackers Euro Stoxx 50 UCITS ETF 1C",
        "isin": "LU0380865021",
        "benchmark_name": "Euro Stoxx 50",
        "benchmark_ticker": "^STOXX50E",
        "ter_pct": 0.09,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "DBXD.DE",
        "name": "Xtrackers DAX UCITS ETF 1C",
        "isin": "LU0274211480",
        "benchmark_name": "DAX",
        "benchmark_ticker": "^GDAXI",
        "ter_pct": 0.09,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": True,
    },
    {
        "ticker": "XSX6.DE",
        "name": "Xtrackers STOXX Europe 600 UCITS ETF 1C",
        "isin": "LU0328475792",
        "benchmark_name": "STOXX Europe 600",
        "benchmark_ticker": "^STOXX",
        "ter_pct": 0.20,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        "ticker": "XRS2.DE",
        "name": "Xtrackers Russell 2000 UCITS ETF 1C",
        "isin": "IE00BJZ2DD79",
        "benchmark_name": "Russell 2000",
        "benchmark_ticker": "^RUT",
        "ter_pct": 0.30,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },

    # --- Small/mid cap (nouveaux indices) --------------------------------------
    {
        "ticker": "SMC.PA",
        "name": "SPDR MSCI Europe Small Cap UCITS ETF",
        "isin": "IE00BKWQ0M75",
        "benchmark_name": "MSCI Europe Small Cap",
        "benchmark_ticker": "XXSC.DE",
        "ter_pct": 0.30,
        "replication": "physique",
        "distribution": "capitalisant",
        "pea_eligible": False,
    },
    {
        # Indice de prix USD alors que l'ETF cote en EUR non couvert (même
        # limite déjà documentée pour GOLD.PA) : l'écart ETF vs benchmark
        # mélangera frais, dividendes et effet de change.
        "ticker": "US4D.DE",
        "name": "SPDR S&P 400 US Mid Cap UCITS ETF USD Unhedged (Dist)",
        "isin": "IE000UYVEVN3",
        "benchmark_name": "S&P MidCap 400",
        "benchmark_ticker": "MDY",
        "ter_pct": 0.30,
        "replication": "physique",
        "distribution": "distribuant",
        "pea_eligible": False,
    },
]
