# Nikkei 225 — recherche pour ajout d'indice (analyse-or)

Recherche uniquement — aucun fichier du repo n'a été modifié. Ce document
fournit tout ce qu'il faut pour que quelqu'un d'autre ajoute
`NIKKEI225_COMPANIES` dans `indices_score.py` en connaissance de cause,
sur le même modèle que le CAC40/DAX/Nasdaq-100/Dow/FTSE 100 déjà en place.

Contrairement aux recherches précédentes (Nasdaq-100, FTSE 100), cette
session avait un accès réseau direct (curl/WebFetch non bloqués), ce qui a
permis de récupérer la page officielle Nikkei Indexes en direct plutôt que
de dépendre uniquement de Wikipédia/WebSearch — voir §1.

## 1. Sources et fraîcheur

1. **Source primaire (nouvelle par rapport aux recherches précédentes)** :
   la page officielle **Nikkei Indexes**
   (`https://indexes.nikkei.co.jp/en/nkave/index/component?idx=nk225`),
   récupérée en direct le **2026-09-12** (curl, HTTP 200 — pas une
   synthèse IA, le HTML brut a été parsé programmatiquement). Cette page
   liste les 225 constituants **actuels**, classés par secteur Nikkei,
   avec code à 4 caractères et nom légal anglais en majuscules. C'est la
   source la plus fraîche et la plus autoritaire possible : l'éditeur de
   l'indice lui-même, pas un tiers.
2. **Source de recoupement n°1** : Wikipédia, article "Nikkei 225"
   (anglais), récupéré le 2026-09-12 via l'API `action=raw` (wikitext
   brut). Le tableau des constituants indique explicitement *"As of April
   2026, the Nikkei 225 consists of the following companies"* → version
   au 01/04/2026, 225 lignes. Utilisée pour ses noms d'entreprise plus
   lisibles (ex. "Toyota Motor" plutôt que "TOYOTA MOTOR CORP.").
3. **Source de recoupement n°2** : topforeignstocks.com "The Complete List
   of Constituents of the Japan Nikkei 225 Index", as-of **01/01/2024**
   (~2,5 ans plus ancien que les deux sources ci-dessus). Confirme le
   format de ticker Yahoo (`XXXX.T`) et sert uniquement à documenter le
   turnover connu de l'indice depuis 2024 (voir §6) — pas utilisée comme
   source de composition actuelle, trop datée.
4. **Source de recoupement n°3** : marketscreener.com, page composants
   Nikkei 225, as-of affiché **11/09/2026** (la veille de cette
   recherche) — liste partielle (top 80 par poids), utilisée uniquement
   pour confirmer un point précis (Ibiden, voir §1bis).
5. **WebSearch** (Nikkei Asia, BigGo Finance) pour les annonces de
   réajustement récentes/à venir — voir §1bis et §6.

**Accord sur le nombre de constituants** : 225 sur les trois sources
principales (source officielle, Wikipédia, et — pour la période qu'elle
couvre — topforeignstocks).

### 1bis. La seule divergence trouvée entre sources, et sa résolution

En comparant les 225 codes de la source officielle (2026-09-12) à ceux du
tableau Wikipédia ("as of April 2026"), une seule différence : Wikipédia
liste encore `6594` (**Nidec**), absent de la source officielle, qui liste
à la place `4062` (**Ibiden**), absent de Wikipédia. Un remplacement 1
pour 1, pas une erreur de lecture — vérifié en diffant les deux ensembles
de codes programmatiquement (225 codes communs + 1 écart de chaque côté).

Résolution : la source officielle (2026-09-12) fait foi car plus fraîche
que le tableau Wikipédia (01/04/2026). Confirmation indépendante :
- marketscreener (11/09/2026) liste bien Ibiden parmi les composants.
- Un article Simply Wall St daté de novembre 2025 confirme l'entrée
  d'Ibiden dans le Nikkei 225 ("Why Ibiden (TSE:4062) Is Up After Nikkei
  225 Inclusion...").
- Le ticker Yahoo Finance `4062.T` répond avec `longName: "Ibiden
  Co.,Ltd."`, `currency: JPY`, `exchangeName: JPX` (vérifié en direct, voir
  §2).

**Ibiden (4062.T) est donc bien incluse dans la liste finale ci-dessous,
Nidec ne l'est pas** — c'est le tableau Wikipédia qui a un train de retard
sur ce point précis, malgré sa mention "as of April 2026".

## 2. Format de ticker et vérifications individuelles (Yahoo Finance)

Format attendu et confirmé : **code numérique à 4 chiffres + suffixe
`.T`** (Tokyo Stock Exchange, bourse `JPX` côté Yahoo). Vérifié en direct
via `https://query1.finance.yahoo.com/v8/finance/chart/<TICKER>` (appel
réseau réel depuis ce sandbox, pas une simulation) :

| Ticker | `longName`/`shortName` | `currency` | `exchangeName` |
|---|---|---|---|
| `7203.T` | Toyota Motor Corporation | JPY | JPX |
| `6758.T` | Sony Group Corporation | JPY | JPX |
| `9984.T` | SoftBank Group Corp. | JPY | JPX |
| `9983.T` | Fast Retailing Co., Ltd. | JPY | JPX |
| `4062.T` | Ibiden Co.,Ltd. | JPY | JPX |

Confirmé également pour les deux tickers alphanumériques atypiques de
l'indice (voir §5) :

| Ticker | `longName` | `currency` | `exchangeName` |
|---|---|---|---|
| `543A.T` | ARCHION Corporation | JPY | JPX |
| `285A.T` | Kioxia Holdings Corporation | JPY | JPX |

### Indice lui-même

`^N225` vérifié en direct : `longName: "Nikkei 225"`, `currency: JPY`,
`exchangeName: OSA` (Osaka — cohérent, le Nikkei 225 future/l'indice est
référencé côté Osaka Exchange chez Yahoo, ce qui n'affecte pas
l'utilisation en lecture du niveau de l'indice). Confirme l'hypothèse de
consigne : **`^N225`** est le bon ticker yfinance pour le niveau de
l'indice.

## 3. Liste Python `NIKKEI225_COMPANIES` (225 entrées, triée alphabétiquement)

Aucun chevauchement avec les indices déjà suivis (voir §4) : les 225
constituants réels du Nikkei 225 sont donc tous présents ci-dessous, sans
retrait volontaire de doublon (contrairement au DAX 39/40, Dow 21/30, FTSE
99/100).

Note de nommage : les noms combinent la source officielle (pour le
ticker et l'exactitude de la raison sociale actuelle) et Wikipédia (pour
la lisibilité — suffixes légaux "Co., Ltd." / "Corp." / "Inc." retirés,
comme pour les autres indices). Quelques renommages récents ont été
appliqués manuellement là où Wikipédia était en retard sur la source
officielle (voir notes en bas de tableau) : Sumitomo Pharma (ex-Sumitomo
Dainippon Pharma), Mitsubishi Chemical Group (ex-Holdings), Toppan
Holdings (ex-Toppan Printing), Tokyu Fudosan Holdings (ex-Tokyu Land),
Konami Group (ex-Konami), NGK (ex-NGK Insulators), Sumitomo Mitsui Trust
Group (ex-Holdings), Daiichi Life Group (ex-Dai-ichi Life Insurance
Company), NTT (ex-Nippon Telegraph & Telephone), JGC Holdings (ex-JGC
Corporation).

```python
NIKKEI225_COMPANIES = [
    {"ticker": "6857.T", "name": "Advantest"},
    {"ticker": "8267.T", "name": "Aeon"},
    {"ticker": "5201.T", "name": "AGC"},
    {"ticker": "2802.T", "name": "Ajinomoto"},
    {"ticker": "6770.T", "name": "Alps Alpine"},
    {"ticker": "6113.T", "name": "Amada"},
    {"ticker": "9202.T", "name": "ANA Holdings"},
    {"ticker": "8304.T", "name": "Aozora Bank"},
    {"ticker": "543A.T", "name": "Archion"},
    {"ticker": "2502.T", "name": "Asahi Group Holdings"},
    {"ticker": "3407.T", "name": "Asahi Kasei"},
    {"ticker": "4503.T", "name": "Astellas Pharma"},
    {"ticker": "7832.T", "name": "Bandai Namco Holdings"},
    {"ticker": "6532.T", "name": "Baycurrent"},
    {"ticker": "5108.T", "name": "Bridgestone"},
    {"ticker": "7751.T", "name": "Canon"},
    {"ticker": "9022.T", "name": "Central Japan Railway Company"},
    {"ticker": "8331.T", "name": "Chiba Bank"},
    {"ticker": "9502.T", "name": "Chubu Electric Power"},
    {"ticker": "4519.T", "name": "Chugai Pharmaceutical"},
    {"ticker": "1721.T", "name": "Comsys Holdings"},
    {"ticker": "8253.T", "name": "Credit Saison"},
    {"ticker": "4751.T", "name": "CyberAgent"},
    {"ticker": "7912.T", "name": "Dai Nippon Printing"},
    {"ticker": "8750.T", "name": "Daiichi Life Group"},
    {"ticker": "4568.T", "name": "Daiichi Sankyo"},
    {"ticker": "6367.T", "name": "Daikin Industries"},
    {"ticker": "1925.T", "name": "Daiwa House Industry"},
    {"ticker": "8601.T", "name": "Daiwa Securities Group"},
    {"ticker": "2432.T", "name": "Dena"},
    {"ticker": "4061.T", "name": "Denka"},
    {"ticker": "6902.T", "name": "Denso"},
    {"ticker": "4324.T", "name": "Dentsu"},
    {"ticker": "6146.T", "name": "Disco"},
    {"ticker": "5714.T", "name": "Dowa Holdings"},
    {"ticker": "9020.T", "name": "East Japan Railway Company"},
    {"ticker": "6361.T", "name": "Ebara"},
    {"ticker": "4523.T", "name": "Eisai"},
    {"ticker": "5020.T", "name": "Eneos Holdings"},
    {"ticker": "6954.T", "name": "FANUC"},
    {"ticker": "9983.T", "name": "Fast Retailing"},
    {"ticker": "6504.T", "name": "Fuji Electric"},
    {"ticker": "4901.T", "name": "Fujifilm Holdings"},
    {"ticker": "5803.T", "name": "Fujikura"},
    {"ticker": "6702.T", "name": "Fujitsu"},
    {"ticker": "8354.T", "name": "Fukuoka Financial Group"},
    {"ticker": "5801.T", "name": "Furukawa Electric"},
    {"ticker": "1808.T", "name": "Haseko"},
    {"ticker": "6501.T", "name": "Hitachi"},
    {"ticker": "6305.T", "name": "Hitachi Construction Machinery"},
    {"ticker": "7267.T", "name": "Honda Motor"},
    {"ticker": "7741.T", "name": "Hoya"},
    {"ticker": "4062.T", "name": "Ibiden"},
    {"ticker": "5019.T", "name": "Idemitsu Kosan"},
    {"ticker": "7013.T", "name": "IHI"},
    {"ticker": "1605.T", "name": "Inpex"},
    {"ticker": "3099.T", "name": "Isetan Mitsukoshi Holdings"},
    {"ticker": "7202.T", "name": "Isuzu Motors"},
    {"ticker": "8001.T", "name": "Itochu"},
    {"ticker": "3086.T", "name": "J. Front Retailing"},
    {"ticker": "9201.T", "name": "Japan Airlines"},
    {"ticker": "8697.T", "name": "Japan Exchange Group"},
    {"ticker": "6178.T", "name": "Japan Post Holdings"},
    {"ticker": "5631.T", "name": "Japan Steel Works"},
    {"ticker": "2914.T", "name": "Japan Tobacco"},
    {"ticker": "5411.T", "name": "JFE Holdings"},
    {"ticker": "1963.T", "name": "JGC Holdings"},
    {"ticker": "6473.T", "name": "JTEKT"},
    {"ticker": "1812.T", "name": "Kajima"},
    {"ticker": "7004.T", "name": "Kanadevia"},
    {"ticker": "9503.T", "name": "Kansai Electric Power"},
    {"ticker": "4452.T", "name": "Kao"},
    {"ticker": "7012.T", "name": "Kawasaki Heavy Industries"},
    {"ticker": "9107.T", "name": "Kawasaki Kisen Kaisha"},
    {"ticker": "9433.T", "name": "KDDI"},
    {"ticker": "9008.T", "name": "Keio"},
    {"ticker": "9009.T", "name": "Keisei Electric Railway"},
    {"ticker": "6861.T", "name": "Keyence"},
    {"ticker": "2801.T", "name": "Kikkoman"},
    {"ticker": "285A.T", "name": "Kioxia Holdings"},
    {"ticker": "2503.T", "name": "Kirin Holdings"},
    {"ticker": "5406.T", "name": "Kobe Steel"},
    {"ticker": "6301.T", "name": "Komatsu"},
    {"ticker": "9766.T", "name": "Konami Group"},
    {"ticker": "4902.T", "name": "Konica Minolta Holdings"},
    {"ticker": "6326.T", "name": "Kubota"},
    {"ticker": "3405.T", "name": "Kuraray"},
    {"ticker": "6971.T", "name": "Kyocera"},
    {"ticker": "4151.T", "name": "Kyowa Hakko Kirin"},
    {"ticker": "6920.T", "name": "Lasertec"},
    {"ticker": "4689.T", "name": "LY"},
    {"ticker": "2413.T", "name": "M3"},
    {"ticker": "8002.T", "name": "Marubeni"},
    {"ticker": "8252.T", "name": "Marui Group"},
    {"ticker": "7261.T", "name": "Mazda Motor"},
    {"ticker": "2269.T", "name": "Meiji Holdings"},
    {"ticker": "4385.T", "name": "Mercari"},
    {"ticker": "6479.T", "name": "MinebeaMitsumi"},
    {"ticker": "4188.T", "name": "Mitsubishi Chemical Group"},
    {"ticker": "8058.T", "name": "Mitsubishi Corporation"},
    {"ticker": "6503.T", "name": "Mitsubishi Electric"},
    {"ticker": "8802.T", "name": "Mitsubishi Estate"},
    {"ticker": "7011.T", "name": "Mitsubishi Heavy Industries"},
    {"ticker": "5711.T", "name": "Mitsubishi Materials"},
    {"ticker": "7211.T", "name": "Mitsubishi Motors"},
    {"ticker": "8306.T", "name": "Mitsubishi UFJ Financial Group"},
    {"ticker": "8031.T", "name": "Mitsui & Co."},
    {"ticker": "4183.T", "name": "Mitsui Chemicals"},
    {"ticker": "8801.T", "name": "Mitsui Fudosan"},
    {"ticker": "5706.T", "name": "Mitsui Kinzoku (Mitsui Mining & Smelting)"},
    {"ticker": "9104.T", "name": "Mitsui O.S.K. Lines"},
    {"ticker": "8411.T", "name": "Mizuho Financial Group"},
    {"ticker": "8725.T", "name": "MS&AD Insurance Group"},
    {"ticker": "6981.T", "name": "Murata Manufacturing"},
    {"ticker": "6701.T", "name": "NEC"},
    {"ticker": "3659.T", "name": "Nexon"},
    {"ticker": "5333.T", "name": "NGK"},
    {"ticker": "2282.T", "name": "NH Foods"},
    {"ticker": "2871.T", "name": "Nichirei"},
    {"ticker": "7731.T", "name": "Nikon"},
    {"ticker": "7974.T", "name": "Nintendo"},
    {"ticker": "5214.T", "name": "Nippon Electric Glass"},
    {"ticker": "9147.T", "name": "Nippon Express Holdings"},
    {"ticker": "5401.T", "name": "Nippon Steel"},
    {"ticker": "9101.T", "name": "Nippon Yusen"},
    {"ticker": "4021.T", "name": "Nissan Chemical"},
    {"ticker": "7201.T", "name": "Nissan Motor"},
    {"ticker": "2002.T", "name": "Nisshin Seifun Group"},
    {"ticker": "1332.T", "name": "Nissui"},
    {"ticker": "9843.T", "name": "Nitori Holdings"},
    {"ticker": "6988.T", "name": "Nitto Denko"},
    {"ticker": "8604.T", "name": "Nomura Holdings"},
    {"ticker": "4307.T", "name": "Nomura Research Institute"},
    {"ticker": "6471.T", "name": "NSK"},
    {"ticker": "6472.T", "name": "NTN"},
    {"ticker": "9432.T", "name": "NTT"},
    {"ticker": "1802.T", "name": "Obayashi"},
    {"ticker": "9007.T", "name": "Odakyu Electric Railway"},
    {"ticker": "3861.T", "name": "Oji Holdings"},
    {"ticker": "6103.T", "name": "Okuma"},
    {"ticker": "7733.T", "name": "Olympus"},
    {"ticker": "6645.T", "name": "Omron"},
    {"ticker": "4661.T", "name": "Oriental Land"},
    {"ticker": "8591.T", "name": "Orix"},
    {"ticker": "9532.T", "name": "Osaka Gas"},
    {"ticker": "4578.T", "name": "Otsuka Holdings"},
    {"ticker": "7532.T", "name": "Pan Pacific International Holdings"},
    {"ticker": "6752.T", "name": "Panasonic Holdings"},
    {"ticker": "4755.T", "name": "Rakuten"},
    {"ticker": "6098.T", "name": "Recruit Holdings"},
    {"ticker": "6723.T", "name": "Renesas Electronics"},
    {"ticker": "8308.T", "name": "Resona Holdings"},
    {"ticker": "4004.T", "name": "Resonac"},
    {"ticker": "7752.T", "name": "Ricoh"},
    {"ticker": "6963.T", "name": "Rohm"},
    {"ticker": "7453.T", "name": "Ryohin Keikaku (Muji)"},
    {"ticker": "2501.T", "name": "Sapporo Holdings"},
    {"ticker": "7735.T", "name": "SCREEN Holdings"},
    {"ticker": "9735.T", "name": "Secom"},
    {"ticker": "6724.T", "name": "Seiko Epson"},
    {"ticker": "1928.T", "name": "Sekisui House"},
    {"ticker": "3382.T", "name": "Seven & I Holdings"},
    {"ticker": "6753.T", "name": "Sharp"},
    {"ticker": "3697.T", "name": "SHIFT"},
    {"ticker": "1803.T", "name": "Shimizu"},
    {"ticker": "4063.T", "name": "Shin-Etsu Chemical"},
    {"ticker": "4507.T", "name": "Shionogi & Co."},
    {"ticker": "4911.T", "name": "Shiseido"},
    {"ticker": "5831.T", "name": "Shizuoka Financial Group"},
    {"ticker": "6273.T", "name": "SMC"},
    {"ticker": "6526.T", "name": "Socionext"},
    {"ticker": "9434.T", "name": "SoftBank"},
    {"ticker": "9984.T", "name": "SoftBank Group"},
    {"ticker": "2768.T", "name": "Sojitz"},
    {"ticker": "8630.T", "name": "Sompo Holdings"},
    {"ticker": "6758.T", "name": "Sony Group"},
    {"ticker": "7270.T", "name": "Subaru"},
    {"ticker": "3436.T", "name": "SUMCO"},
    {"ticker": "4005.T", "name": "Sumitomo Chemical"},
    {"ticker": "8053.T", "name": "Sumitomo Corporation"},
    {"ticker": "5802.T", "name": "Sumitomo Electric Industries"},
    {"ticker": "6302.T", "name": "Sumitomo Heavy Industries"},
    {"ticker": "5713.T", "name": "Sumitomo Metal Mining"},
    {"ticker": "8316.T", "name": "Sumitomo Mitsui Financial Group"},
    {"ticker": "8309.T", "name": "Sumitomo Mitsui Trust Group"},
    {"ticker": "4506.T", "name": "Sumitomo Pharma"},
    {"ticker": "8830.T", "name": "Sumitomo Realty & Development"},
    {"ticker": "7269.T", "name": "Suzuki Motor"},
    {"ticker": "8795.T", "name": "T&D Holdings"},
    {"ticker": "5233.T", "name": "Taiheiyo Cement"},
    {"ticker": "1801.T", "name": "Taisei"},
    {"ticker": "6976.T", "name": "Taiyo Yuden"},
    {"ticker": "8233.T", "name": "Takashimaya"},
    {"ticker": "4502.T", "name": "Takeda Pharmaceutical"},
    {"ticker": "6762.T", "name": "TDK"},
    {"ticker": "3401.T", "name": "Teijin"},
    {"ticker": "4543.T", "name": "Terumo"},
    {"ticker": "9001.T", "name": "Tobu Railway"},
    {"ticker": "9602.T", "name": "Toho"},
    {"ticker": "5301.T", "name": "Tokai Carbon"},
    {"ticker": "8766.T", "name": "Tokio Marine Holdings"},
    {"ticker": "4043.T", "name": "Tokuyama"},
    {"ticker": "9501.T", "name": "Tokyo Electric Power Company Holdings"},
    {"ticker": "8035.T", "name": "Tokyo Electron"},
    {"ticker": "9531.T", "name": "Tokyo Gas"},
    {"ticker": "8804.T", "name": "Tokyo Tatemono"},
    {"ticker": "9005.T", "name": "Tokyu"},
    {"ticker": "3289.T", "name": "Tokyu Fudosan Holdings"},
    {"ticker": "7911.T", "name": "Toppan Holdings"},
    {"ticker": "3402.T", "name": "Toray Industries"},
    {"ticker": "4042.T", "name": "Tosoh"},
    {"ticker": "5332.T", "name": "Toto"},
    {"ticker": "7203.T", "name": "Toyota Motor"},
    {"ticker": "8015.T", "name": "Toyota Tsusho"},
    {"ticker": "4704.T", "name": "Trend Micro"},
    {"ticker": "4208.T", "name": "Ube Industries"},
    {"ticker": "9021.T", "name": "West Japan Railway Company"},
    {"ticker": "7951.T", "name": "Yamaha"},
    {"ticker": "7272.T", "name": "Yamaha Motor"},
    {"ticker": "9064.T", "name": "Yamato Holdings"},
    {"ticker": "6506.T", "name": "Yaskawa Electric"},
    {"ticker": "6841.T", "name": "Yokogawa Electric"},
    {"ticker": "7186.T", "name": "Yokohama Financial Group"},
    {"ticker": "5101.T", "name": "Yokohama Rubber"},
    {"ticker": "3092.T", "name": "ZOZO"},
]
```

Compte vérifié programmatiquement : **225 entrées, 225 tickers uniques,
225 noms uniques** (pas de doublon interne, pas de collision de nom entre
les multiples entités "Mitsubishi"/"Sumitomo"/"Mitsui").

## 4. Chevauchements avec les indices déjà suivis

**Aucun chevauchement trouvé.** Les 301 noms d'entreprise actuellement
dans `CAC40_COMPANIES` + `DAX_COMPANIES` + `NASDAQ_COMPANIES` +
`DOW_COMPANIES` + `FTSE_COMPANIES` (lus directement dans
`indices_score.py`) ont été comparés un par un aux 225 noms du Nikkei
225 : zéro correspondance. Cohérent avec l'attente de la consigne — les
multinationales japonaises du Nikkei 225 ne sont pas des constituants des
indices occidentaux déjà suivis (ni l'inverse : aucune des entreprises
CAC40/DAX/Nasdaq/Dow/FTSE n'est cotée à la Bourse de Tokyo sous un
double listing qui la ferait apparaître ici).

**Le Nikkei 225 est donc à 225/225** — pas de retrait par choix de
déduplication, contrairement aux autres indices déjà en place.

## 5. Secteur financier (banques / assureurs / réassureurs)

Définition du projet rappelée : uniquement banques de dépôt/crédit et
assureurs/réassureurs qui souscrivent du risque — PAS les gestionnaires
d'actifs, sociétés d'investissement (trusts), opérateurs de marché ou
courtiers/plateformes.

La source officielle Nikkei classe elle-même les constituants par
secteur, ce qui rend la classification particulièrement fiable ici (pas
une reconstruction a posteriori) :

**Banques (catégorie officielle "Banking", 10 tickers)** :

| Ticker | Société | Type |
|---|---|---|
| `8306.T` | Mitsubishi UFJ Financial Group | Mégabanque (réseau + banque d'investissement) |
| `8316.T` | Sumitomo Mitsui Financial Group | Mégabanque |
| `8411.T` | Mizuho Financial Group | Mégabanque |
| `8308.T` | Resona Holdings | Banque de réseau |
| `8309.T` | Sumitomo Mitsui Trust Group | Banque de fiducie/gestion d'actifs pour compte de tiers adossée à un bilan bancaire |
| `8331.T` | Chiba Bank | Banque régionale |
| `8354.T` | Fukuoka Financial Group | Banque régionale |
| `5831.T` | Shizuoka Financial Group | Banque régionale (holding de Shizuoka Bank) |
| `7186.T` | Yokohama Financial Group | Banque régionale (ex-Concordia Financial Group) |
| `8304.T` | Aozora Bank | Banque spécialisée (ex-Nippon Credit Bank) |

**Assureurs/réassureurs (catégorie officielle "Insurance", 5 tickers)** :

| Ticker | Société | Type |
|---|---|---|
| `8766.T` | Tokio Marine Holdings | Assureur/réassureur composite |
| `8725.T` | MS&AD Insurance Group | Assureur composite (fusion Mitsui Sumitomo + Aioi + Nissay Dowa) |
| `8630.T` | Sompo Holdings | Assureur composite |
| `8750.T` | Daiichi Life Group | Assureur vie (ex-Dai-ichi Life Insurance Company/Dai-ichi Life Holdings) |
| `8795.T` | T&D Holdings | Assureur vie (Taiyo Life + Daido Life) |

**Total : 15 tickers financiers**, ce qui correspond exactement à la
liste d'exemples donnée dans la consigne (Mitsubishi UFJ, Sumitomo
Mitsui, Mizuho, Tokio Marine, MS&AD, Sompo, Dai-ichi Life, T&D) plus les
banques régionales/spécialisées confirmées constituantes (Chiba, Fukuoka,
Shizuoka, Yokohama, Aozora, Sumitomo Mitsui Trust).

### Explicitement exclues du traitement financier (méthodologie standard)

Toutes constituantes du Nikkei 225 mais volontairement laissées en
méthodologie standard, même logique que les exclusions FTSE
(gestionnaires d'actifs/courtiers/opérateurs de marché) :

- `8604.T` Nomura Holdings, `8601.T` Daiwa Securities Group — courtiers/
  banques d'investissement (catégorie officielle "Securities", pas
  "Banking") : pas des banques de dépôt, même traitement que Nomura ne
  prenant pas de dépôts de détail comme HSBC/BNP Paribas.
- `8697.T` Japan Exchange Group — **opérateur de marché** (catégorie
  officielle "Other Financial Services"), confirmée constituante du
  Nikkei 225. Exactement le cas anticipé par la consigne : même
  traitement que LSEG (FTSE)/Deutsche Börse (DAX)/Euronext (CAC40),
  standard malgré le profil de bilan atypique.
- `8591.T` Orix — conglomérat financier diversifié (leasing, financement
  corporate, immobilier, gestion d'actifs, capital-investissement — un
  segment assurance-vie existait mais a été cédé en 2021) : pas une
  banque de dépôt ni un assureur au sens strict du projet, catégorie
  officielle "Other Financial Services" comme JPX.

**Cas limite signalé, à trancher par un humain plutôt que tranché ici :**

- `8253.T` Credit Saison — société de crédit à la consommation/cartes de
  crédit (catégorie officielle "Other Financial Services"). Elle émet du
  crédit mais ne prend pas de dépôts (pas une banque au sens
  "dépôt/crédit" strict), et n'est pas non plus un assureur. Je ne l'ai
  **pas** mise dans la liste financière ci-dessus — comparable au
  traitement de St. James's Place dans le rapport FTSE 100 (cas limite
  laissé en méthodologie standard, à confirmer/infirmer manuellement si
  les états financiers yfinance s'avèrent inexploitables en grille
  standard).

## 6. Taux sans risque — série FRED pour le Japon

Suivant le même schéma que `RISK_FREE_SERIES_BY_CURRENCY` (OAT France
`IRLTLT01FRM156N`, Treasury US `DGS10`, Gilt UK `IRLTLT01GBM156N`) :
**`IRLTLT01JPM156N`** existe bien sur FRED et suit exactement le même
format que la série France/UK (famille OCDE "IRLTLT01").

Vérifié en direct (WebFetch sur `https://fred.stlouisfed.org/series/
IRLTLT01JPM156N`) :

- **Titre exact** : "Interest Rates: Long-Term Government Bond Yields:
  10-Year: Main (Including Benchmark) for Japan"
- **Unités** : pourcentage, non désaisonnalisé
- **Fréquence** : **mensuelle** — identique à France/UK, PAS quotidienne
  comme le Treasury US (`DGS10`)
- **Source** : OCDE (Organisation for Economic Co-operation and
  Development), même organisme source que les séries France/UK
- **Dernière valeur observée** : 2,670 % (juin 2026)

Confirmé : c'est bien un taux d'obligation d'État japonaise à 10 ans
(JGB 10 ans). À ajouter à `RISK_FREE_SERIES_BY_CURRENCY["JPY"]` le jour de
l'intégration.

## 7. Tickers — niveaux de confiance

Contrairement aux recherches Nasdaq-100/FTSE 100 précédentes (sandbox
sans accès réseau), cette session avait un accès réseau direct — la
confiance globale est donc plus élevée qu'à l'accoutumée, mais tous les
tickers n'ont pas été vérifiés individuellement un par un (225 appels
n'ont pas été faits).

**Vérifiés individuellement via un appel Yahoo Finance réel (haute
confiance)** : `7203.T` (Toyota Motor), `6758.T` (Sony Group), `9984.T`
(SoftBank Group), `9983.T` (Fast Retailing), `4062.T` (Ibiden), `543A.T`
(Archion), `285A.T` (Kioxia Holdings), ainsi que `^N225` (l'indice
lui-même). Voir tableaux §2.

**Haute confiance par construction (source officielle + motif confirmé)**
— les 218 tickers restants suivent le motif standard "code officiel
Nikkei + suffixe `.T`", motif confirmé exact sur les 7 vérifications
ci-dessus (y compris les deux cas non standards) : à considérer comme
fiables mais pas vérifiés un par un cette session. Comme pour les
recherches précédentes, le bandeau "santé" du frontend (`docs/index.html`)
signalera après le premier run réel tout ticker qui s'avérerait invalide.

**Tickers au format atypique, signalés explicitement** : `543A.T`
(Archion) et `285A.T` (Kioxia Holdings) utilisent le **nouveau format
alphanumérique** introduit par le Japan Exchange Group une fois les codes
purement numériques à 4 chiffres épuisés pour les nouvelles
attributions/réorganisations (Kioxia a été introduite en bourse en
décembre 2024, Archion résulte d'une réorganisation capitalistique de Hino
Motors en 2026). **Vérifié : ces deux tickers fonctionnent normalement
avec le simple suffixe `.T`**, sans transformation particulière (pas de
tiret, pas de format spécial) — voir §2. Aucun autre ticker de la liste ne
présente un format inhabituel (pas de 5 chiffres, pas d'autre suffixe que
`.T`).

## 8. Contexte : turnover récent et changement à venir (hors périmètre de la liste actuelle)

Pour information, à ne **pas** intégrer dans `NIKKEI225_COMPANIES`
aujourd'hui — ce sont des changements soit déjà remplacés (voir §1bis),
soit dont la date d'effet est **postérieure** à la date de cette
recherche (2026-09-12) :

- **Retraits déjà effectifs, absents de la liste ci-dessus** (comparaison
  avec la source topforeignstocks du 01/01/2024, turnover normal en 2,5
  ans) : Nidec (remplacée par Ibiden, voir §1bis), Casio Computer, GS
  Yuasa, Hino Motors (fusionnée dans Archion), DIC Corp, NTT Data Group
  (retirée de cote, absorbée par NTT en 2025), Pacific Metals, Takara
  Holdings, Sumitomo Osaka Cement (fusionnée dans Taiheiyo Cement),
  Mitsubishi Logistics.
- **Changement annoncé mais PAS encore effectif à la date de cette
  recherche** : selon Nikkei Asia/BigGo Finance, à partir du
  **1er octobre 2026** (review périodique), **JX Advanced Metals**,
  **Kokusai Electric** et **Capcom** rejoindront l'indice, en remplacement
  d'**Archion**, **Konica Minolta** et **Kanadevia**. Comme la présente
  recherche documente la composition **au 2026-09-12** (confirmé par la
  page officielle Nikkei Indexes elle-même, qui liste encore Archion/
  Konica Minolta/Kanadevia et pas les trois entrantes), la liste
  ci-dessus conserve à raison les 3 sortantes et n'inclut pas les 3
  entrantes. **À surveiller après le 01/10/2026** si l'intégration réelle
  dans `indices_score.py` a lieu après cette date — un rafraîchissement
  de la liste serait alors nécessaire (retirer Archion/Konica
  Minolta/Kanadevia, ajouter JX Advanced Metals/Kokusai Electric/Capcom).
