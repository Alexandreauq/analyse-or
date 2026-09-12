# Hang Seng Index — recherche pour ajout d'indice (analyse-or)

Recherche uniquement — aucun fichier du repo n'a été modifié (`indices_score.py`,
`docs/index.html` etc. sont intacts). Ce document fournit tout ce qu'il faut
pour que quelqu'un d'autre colle `HANGSENG_COMPANIES` dans `indices_score.py`
en connaissance de cause.

Avertissement général : le Hang Seng est structurellement le plus différent
des 4 indices déjà suivis (CAC40/DAX/NASDAQ/DOW) et du FTSE 100 en cours
d'ajout — bourse de Hong Kong (HKEX), tickers numériques, devise HKD, mélange
d'entreprises hongkongaises pures et de sociétés chinoises continentales
cotées à Hong Kong (H-shares/red-chips) ou dual-listées Chine
continentale + Hong Kong (ex. CATL, Midea). Chaque incertitude réelle est
signalée explicitement plutôt que masquée par une valeur par défaut plausible.

## 1. Sources et fraîcheur

1. **Source primaire (la plus autoritaire possible)** : communiqué officiel
   *"Hang Seng Indexes Company Announces Index Review Results"*, Hang Seng
   Indexes Company (filiale à 100% de Hang Seng Bank), daté du **21 août
   2026**, récupéré via
   `https://www.hsi.com.hk/static/uploads/contents/en/news/pressRelease/20260821T174500.pdf`.
   Ce communiqué annonce la révision trimestrielle (trimestre clos au 30 juin
   2026) : 2 entrées (Hua Hong Grace Semiconductor 1347, Weichai Power 2338),
   **aucune sortie**, effective le **7 septembre 2026** — donc en vigueur
   depuis 5 jours à la date de cette recherche (12/09/2026). Le total passe de
   93 à 95 constituants. **L'Appendix 1** du document donne la liste complète
   des 95 constituants avec code boursier, nom et sous-indice
   (Financials/Consumer Discretionary & Staples/Information Technology/
   Energy Materials Industrials & Conglomerates/Telecommunications &
   Utilities/Healthcare/Properties & Construction) — c'est la liste utilisée
   ci-dessous, transcrite intégralement.
2. **Source de recoupement n°1** : aastocks.com (plateforme de données
   boursières hongkongaise), page constituants HSI en direct
   (`aastocks.com/en/stocks/market/index/hk-index-con.aspx?index=HSI`),
   consultée le 12/09/2026. Les ~29 lignes affichées dans la fenêtre
   récupérée (CKH Holdings, CLP Holdings, HK & China Gas, HSBC, Power Assets,
   Henderson Land, SHK PPT, Galaxy Ent, MTR, Hang Lung PPT, Geely Auto, Ali
   Health, CITIC, BYD Electronic, WH Group, China Res Beer, Midea, OOIL,
   Tingyi, Sinopec Corp, HKEX, Techtronic, China Overseas, Tencent, China
   Telecom, China Unicom, Link REIT, China Res Power, PetroChina, Xinyi
   Glass) correspondent toutes à l'Appendix 1 officiel — aucune divergence.
   Cette source utilise elle-même un code à 5 chiffres zero-paddé + `.HK`
   (ex. `00001.HK`), confirmation indépendante que HKEX/Yahoo travaillent en
   codes zero-paddés (voir §2 pour la vérification yfinance précise).
3. **Source de recoupement n°2 (partielle, datée)** : Wikipédia, article
   "Hang Seng Index" (anglais), récupéré via `action=raw` le 12/09/2026 — la
   légende de son tableau indique explicitement une photographie **de
   janvier 2026** (88 constituants, avant la révision du 21/08/2026 qui porte
   le total à 95). Wikipédia n'est donc PAS à jour sur le nombre exact de
   constituants au 12/09/2026 — écart de 2 trimestres de révision, entièrement
   expliqué par les révisions trimestrielles normales de l'indice (Hua Hong
   Grace et Weichai Power ajoutés en septembre 2026, postérieurs à la version
   Wikipédia figée en janvier). Elle reste utile pour confirmer le format
   général ticker/nom sur les valeurs communes aux deux versions, mais
   **la source primaire (§1) prime** pour le compte exact et la liste
   exacte au 12/09/2026.

**Compte confirmé au 12/09/2026** : **95 constituants réels** du Hang Seng
Index (source officielle Hang Seng Indexes Company, en vigueur depuis le
07/09/2026) → **94 dans la liste `HANGSENG_COMPANIES` ci-dessous**, HSBC
Holdings étant un chevauchement réel avec `FTSE_COMPANIES` (voir §3), traité
par `also_indices` plutôt que dupliqué — même convention que Airbus
(CAC40/DAX), les 9 chevauchements NASDAQ/DOW et CCEP (FTSE/NASDAQ).

Environnement de recherche : WebFetch/WebSearch disponibles pendant cette
session (contrairement au blocage réseau documenté pour Nasdaq-100/FTSE 100)
— une vingtaine de tickers ont donc pu être vérifiés individuellement via un
appel réel à l'API Yahoo Finance (`query1.finance.yahoo.com/v8/finance/chart/…`),
voir §6 pour le détail par ticker.

## 2. Format des tickers Yahoo Finance (HKEX)

**Vérifié directement, point critique de cette recherche** : Yahoo
Finance/yfinance utilise un code numérique **zero-paddé sur 4 chiffres** +
suffixe `.HK` — PAS le code brut HKEX (qui va de 1 à 5 chiffres selon
l'entreprise).

Preuves obtenues par appel direct à
`https://query1.finance.yahoo.com/v8/finance/chart/<TICKER>` :

| Requête | Résultat |
|---|---|
| `0700.HK` | 200 OK — `longName: "Tencent Holdings Limited"`, `currency: "HKD"`, `symbol: "0700.HK"` |
| `700.HK` (non paddé) | **HTTP 404 Not Found** |
| `0005.HK` | 200 OK — `longName: "HSBC Holdings plc"`, `currency: "HKD"` |
| `0001.HK` | 200 OK — `longName: "CK Hutchison Holdings Limited"` |
| `0002.HK` | 200 OK — `longName: "CLP Holdings Limited"` |

Le test négatif (`700.HK` sans zéros → 404) est la preuve la plus solide :
le zero-padding sur 4 chiffres n'est pas une option parmi d'autres, c'est
la seule forme acceptée. Règle appliquée à toute la liste `HANGSENG_COMPANIES` :
`str(code).zfill(4) + ".HK"` (ex. code HKEX `1` → `"0001.HK"`, code `700` →
`"0700.HK"`, code `9988` → `"9988.HK"` inchangé).

## 3. Ticker de l'indice lui-même

Vérifié de la même façon :
`https://query1.finance.yahoo.com/v8/finance/chart/%5EHSI` → 200 OK,
`longName: "HANG SENG INDEX"`, `symbol: "^HSI"`, `currency: "HKD"`,
`exchangeName: "HKG"`. Confirme l'hypothèse `^HSI` — à ajouter dans
`INDEX_YFINANCE_TICKERS["HANGSENG"] = "^HSI"` le jour de l'intégration.

## 4. Liste Python `HANGSENG_COMPANIES` (94 entrées, triée alphabétiquement)

HSBC Holdings est un constituant Hang Seng réel (code 5, sous-indice
Financials) mais n'est **pas dupliqué** ici — voir §5, double cotation
primaire Londres/Hong Kong, même choix déjà fait pour Airbus (CAC40/DAX),
CCEP (NASDAQ/FTSE) et les 9 chevauchements NASDAQ/DOW.

```python
HANGSENG_COMPANIES = [
    {"ticker": "1299.HK", "name": "AIA Group"},
    {"ticker": "9988.HK", "name": "Alibaba Group Holding"},
    {"ticker": "0241.HK", "name": "Alibaba Health Information Technology"},
    {"ticker": "2600.HK", "name": "Aluminum Corporation of China (Chalco)"},
    {"ticker": "2020.HK", "name": "Anta Sports Products"},
    {"ticker": "9888.HK", "name": "Baidu"},
    {"ticker": "3988.HK", "name": "Bank of China"},
    {"ticker": "6160.HK", "name": "BeOne Medicines"},
    {"ticker": "2388.HK", "name": "BOC Hong Kong (Holdings)"},
    {"ticker": "1876.HK", "name": "Budweiser Brewing Company APAC"},
    {"ticker": "1211.HK", "name": "BYD Company"},
    {"ticker": "0285.HK", "name": "BYD Electronic (International)"},
    {"ticker": "0939.HK", "name": "China Construction Bank"},
    {"ticker": "1378.HK", "name": "China Hongqiao Group"},
    {"ticker": "2628.HK", "name": "China Life Insurance Company"},
    {"ticker": "2319.HK", "name": "China Mengniu Dairy"},
    {"ticker": "3968.HK", "name": "China Merchants Bank"},
    {"ticker": "0941.HK", "name": "China Mobile Limited"},
    {"ticker": "3993.HK", "name": "China Molybdenum (CMOC)"},
    {"ticker": "0688.HK", "name": "China Overseas Land & Investment"},
    {"ticker": "0291.HK", "name": "China Resources Beer (Holdings)"},
    {"ticker": "1109.HK", "name": "China Resources Land"},
    {"ticker": "1209.HK", "name": "China Resources Mixc Lifestyle Services"},
    {"ticker": "0836.HK", "name": "China Resources Power Holdings"},
    {"ticker": "1088.HK", "name": "China Shenhua Energy"},
    {"ticker": "0728.HK", "name": "China Telecom Corporation"},
    {"ticker": "0762.HK", "name": "China Unicom (Hong Kong)"},
    {"ticker": "1929.HK", "name": "Chow Tai Fook Jewellery Group"},
    {"ticker": "0267.HK", "name": "CITIC Limited"},
    {"ticker": "1113.HK", "name": "CK Asset Holdings"},
    {"ticker": "0001.HK", "name": "CK Hutchison Holdings"},
    {"ticker": "1038.HK", "name": "CK Infrastructure Holdings"},
    {"ticker": "0002.HK", "name": "CLP Holdings"},
    {"ticker": "0883.HK", "name": "CNOOC Limited"},
    {"ticker": "3750.HK", "name": "Contemporary Amperex Technology (CATL)"},
    {"ticker": "1093.HK", "name": "CSPC Pharmaceutical Group"},
    {"ticker": "2688.HK", "name": "ENN Energy Holdings"},
    {"ticker": "0027.HK", "name": "Galaxy Entertainment Group"},
    {"ticker": "0175.HK", "name": "Geely Automobile Holdings"},
    {"ticker": "6862.HK", "name": "Haidilao International Holding"},
    {"ticker": "6690.HK", "name": "Haier Smart Home"},
    {"ticker": "0101.HK", "name": "Hang Lung Properties"},
    {"ticker": "3692.HK", "name": "Hansoh Pharmaceutical Group"},
    {"ticker": "0012.HK", "name": "Henderson Land Development"},
    {"ticker": "1044.HK", "name": "Hengan International Group"},
    {"ticker": "0003.HK", "name": "Hong Kong and China Gas Company (Towngas)"},
    {"ticker": "0388.HK", "name": "Hong Kong Exchanges and Clearing"},
    {"ticker": "1347.HK", "name": "Hua Hong Semiconductor"},
    {"ticker": "1398.HK", "name": "Industrial and Commercial Bank of China (ICBC)"},
    {"ticker": "1801.HK", "name": "Innovent Biologics"},
    {"ticker": "1519.HK", "name": "J&T Global Express"},
    {"ticker": "6618.HK", "name": "JD Health International"},
    {"ticker": "2618.HK", "name": "JD Logistics"},
    {"ticker": "9618.HK", "name": "JD.com"},
    {"ticker": "1024.HK", "name": "Kuaishou Technology"},
    {"ticker": "6181.HK", "name": "Laopu Gold"},
    {"ticker": "0992.HK", "name": "Lenovo Group"},
    {"ticker": "2015.HK", "name": "Li Auto"},
    {"ticker": "2331.HK", "name": "Li Ning Company"},
    {"ticker": "0823.HK", "name": "Link Real Estate Investment Trust"},
    {"ticker": "0960.HK", "name": "Longfor Group Holdings"},
    {"ticker": "3690.HK", "name": "Meituan"},
    {"ticker": "0300.HK", "name": "Midea Group"},
    {"ticker": "0066.HK", "name": "MTR Corporation"},
    {"ticker": "9999.HK", "name": "NetEase"},
    {"ticker": "9901.HK", "name": "New Oriental Education & Technology Group"},
    {"ticker": "9633.HK", "name": "Nongfu Spring"},
    {"ticker": "0316.HK", "name": "Orient Overseas (International)"},
    {"ticker": "0857.HK", "name": "PetroChina Company"},
    {"ticker": "2318.HK", "name": "Ping An Insurance (Group) Company of China"},
    {"ticker": "9992.HK", "name": "Pop Mart International Group"},
    {"ticker": "0006.HK", "name": "Power Assets Holdings"},
    {"ticker": "1928.HK", "name": "Sands China"},
    {"ticker": "0981.HK", "name": "Semiconductor Manufacturing International Corporation (SMIC)"},
    {"ticker": "2313.HK", "name": "Shenzhou International Group Holdings"},
    {"ticker": "1177.HK", "name": "Sino Biopharmaceutical"},
    {"ticker": "0386.HK", "name": "Sinopec Corp (China Petroleum & Chemical)"},
    {"ticker": "1099.HK", "name": "Sinopharm Group"},
    {"ticker": "0016.HK", "name": "Sun Hung Kai Properties"},
    {"ticker": "2382.HK", "name": "Sunny Optical Technology (Group)"},
    {"ticker": "0669.HK", "name": "Techtronic Industries"},
    {"ticker": "0700.HK", "name": "Tencent Holdings"},
    {"ticker": "0322.HK", "name": "Tingyi (Cayman Islands) Holding"},
    {"ticker": "9961.HK", "name": "Trip.com Group"},
    {"ticker": "2338.HK", "name": "Weichai Power"},
    {"ticker": "0288.HK", "name": "WH Group"},
    {"ticker": "1997.HK", "name": "Wharf Real Estate Investment Company"},
    {"ticker": "2359.HK", "name": "WuXi AppTec"},
    {"ticker": "2269.HK", "name": "WuXi Biologics (Cayman)"},
    {"ticker": "1810.HK", "name": "Xiaomi Corporation"},
    {"ticker": "0868.HK", "name": "Xinyi Glass Holdings"},
    {"ticker": "0968.HK", "name": "Xinyi Solar Holdings"},
    {"ticker": "2899.HK", "name": "Zijin Mining Group"},
    {"ticker": "2057.HK", "name": "ZTO Express (Cayman)"},
]
```

Compte : **94 entreprises** (95 constituants réels du Hang Seng Index moins
HSBC Holdings, déjà suivie côté `FTSE_COMPANIES` — le Hang Seng serait donc à
94/95 par ce choix, pas par erreur, même logique que DAX 39/40, Dow 21/30 et
FTSE 99/100).

Notes de nommage : les suffixes `-W` (weighted voting rights / actions à
droits de vote pondérés), `-S` (secondary listing) et `-SW`/`-S` utilisés par
Hang Seng Indexes Company dans ses propres publications (ex. "BABA-W",
"TRIP.COM-S") sont une notation interne à la classification de l'indice, PAS
un suffixe de ticker Yahoo Finance — ils ont été retirés des noms ci-dessus
et n'apparaissent jamais dans le ticker (`9988.HK`, pas `9988-W.HK`). Le
suffixe "(H)" utilisé par Hang Seng Indexes Company pour distinguer les
"H-shares" (actions de sociétés chinoises continentales cotées à Hong Kong,
par opposition à leurs actions A-shares cotées à Shanghai/Shenzhen) a été
retiré du nom par souci de lisibilité mais reste implicite : toutes les
entreprises listées ci-dessus sont bien la classe d'actions **cotée à Hong
Kong** (code HKEX), jamais l'A-share ou l'ADR américain — cohérent avec la
consigne (Tencent/Alibaba/Meituan/Xiaomi en identité Hong Kong, pas ADR US).

## 5. Chevauchements avec les indices déjà suivis

**Un seul chevauchement trouvé : HSBC Holdings.**

- Nom : HSBC Holdings plc
- Déjà suivie dans `FTSE_COMPANIES` (`indices_score.py`, ligne ~395), ticker
  `"HSBA.L"`, sans `also_indices` actuellement.
- Constituant Hang Seng Index confirmé : code HKEX 5, sous-indice
  "Financials", poids affiché ~8,00% (le plus gros poids de l'indice après
  recalibrage) dans l'Appendix 1 officiel du 21/08/2026 — HSBC est
  effectivement double-cotée à titre **primaire** à Londres ET à Hong Kong
  (plus une cotation aux Bermudes et à New York sous forme d'ADR) : ce n'est
  pas une cotation secondaire mineure, contrairement à beaucoup de sociétés
  chinoises qui ne sont "primaires" qu'à Hong Kong.
- Ticker yfinance HK vérifié individuellement : `0005.HK` → `longName: "HSBC
  Holdings plc"`, `currency: "HKD"` (voir §2).
- Action recommandée (à faire par quelqu'un d'autre, pas par cette
  recherche) : ajouter `"also_indices": ["HANGSENG"]` à l'entrée HSBC dans
  `FTSE_COMPANIES`, ne pas dupliquer dans `HANGSENG_COMPANIES`.

**Prudential plc et Standard Chartered : PAS de chevauchement, contrairement
à l'hypothèse de départ.** Point explicitement demandé par la consigne — à
signaler comme un résultat de recherche réel, pas une supposition :

- **Standard Chartered** (`STAN.L` dans `FTSE_COMPANIES`) **n'apparaît nulle
  part** dans les 95 constituants du Hang Seng Index au 21/08/2026 (source
  primaire, Appendix 1 — sous-indice Financials, 10 lignes exhaustives :
  HSBC, CCB, AIA, ICBC, HKEX, Bank of China, Ping An, China Life, CM Bank,
  BOC Hong Kong ; aucune trace de Standard Chartered). Standard Chartered
  est bien cotée à Hong Kong (secondary listing) mais n'est **plus** un
  constituant de l'indice actuellement — recherche web complémentaire non
  concluante sur la date exacte de sa sortie historique (pas trouvée dans
  les sources consultées cette session), donc pas de date à citer sans
  confirmation, mais l'absence actuelle est certaine (source officielle
  directe, pas une inférence).
- **Prudential plc** (`PRU.L` dans `FTSE_COMPANIES`) : idem, absente des 95
  constituants. AIA Group (`1299.HK`, dans la liste Hang Seng ci-dessus) est
  l'ex-filiale asiatique de Prudential plc, scindée et introduite en bourse
  séparément à Hong Kong en 2010 — AIA et Prudential plc sont aujourd'hui
  deux sociétés cotées indépendantes sans lien capitalistique, à ne pas
  fusionner ni confondre malgré l'historique commun (comme Aberdeen
  Group/Standard Life dans le rapport FTSE 100).

**Aucun autre chevauchement** trouvé en comparant nom par nom les 95
constituants Hang Seng contre `CAC40_COMPANIES`, `DAX_COMPANIES`,
`NASDAQ_COMPANIES`, `DOW_COMPANIES` et `FTSE_COMPANIES`. Cas particuliers
vérifiés et écartés car ce sont des entreprises distinctes malgré un nom ou
un secteur proche :
- "JD.com" (Hang Seng, `9618.HK`, e-commerce) ≠ "JD Sports Fashion" (FTSE,
  `JD.L`, distribution d'articles de sport) — sociétés sans rapport.
- "PDD Holdings" (`PDD`, dans `NASDAQ_COMPANIES`, ADR Nasdaq de Pinduoduo/
  Temu) n'est PAS un constituant Hang Seng actuel (pas de double cotation HK
  primaire dans la liste officielle) — pas de chevauchement malgré la
  tentation de le supposer vu la nationalité chinoise de l'entreprise.
- NetEase (`9999.HK`, Hang Seng) n'est PAS dans `NASDAQ_COMPANIES` (son ADR
  américain NTES n'y figure pas) — pas de chevauchement.
- Baidu (`9888.HK`, Hang Seng) : même vérification, `BIDU` absent de
  `NASDAQ_COMPANIES`.

## 6. Secteur financier (banques / assureurs / réassureurs)

Définition du projet rappelée : uniquement banques de dépôt/crédit et
assureurs/réassureurs qui souscrivent du risque — PAS les gestionnaires
d'actifs, sociétés d'investissement (trusts), opérateurs de marché ou
courtiers/plateformes.

Le sous-indice "Financials" de Hang Seng Indexes Company compte 10 lignes
dans l'Appendix 1 officiel : HSBC Holdings, CCB (China Construction Bank),
AIA Group, ICBC (Industrial and Commercial Bank of China), HKEX (Hong Kong
Exchanges and Clearing), Bank of China, Ping An, China Life, CM Bank (China
Merchants Bank), BOC Hong Kong. Neuf de ces dix passent le test du projet
(banque de dépôt/crédit ou assureur souscripteur de risque) ; un est
explicitement exclu.

**Liste finale des tickers financiers pour `HANGSENG_COMPANIES` (8, HSBC
étant déjà comptée côté FTSE) :**

- `0939.HK` — China Construction Bank : banque de réseau chinoise (l'une des
  4 grandes banques d'État chinoises)
- `1299.HK` — AIA Group : assureur vie (Asie-Pacifique, ex-filiale
  Prudential plc scindée en 2010)
- `1398.HK` — Industrial and Commercial Bank of China (ICBC) : banque de
  réseau, plus grande banque du monde par le total de bilan
- `3988.HK` — Bank of China : banque de réseau chinoise (grande banque
  d'État)
- `2318.HK` — Ping An Insurance (Group) Company of China : assureur composite
  (vie + dommages) qui souscrit du risque ; possède aussi Ping An Bank
  (filiale bancaire), ce qui renforce le classement financier plutôt que
  l'affaiblit
- `2628.HK` — China Life Insurance Company : assureur vie, plus grand
  assureur vie chinois par actifs
- `3968.HK` — China Merchants Bank : banque de réseau chinoise
- `2388.HK` — BOC Hong Kong (Holdings) : banque de réseau hongkongaise
  (filiale de Bank of China, licence bancaire hongkongaise distincte)

**Explicitement exclu du traitement financier (méthodologie standard) :**

- `0388.HK` — Hong Kong Exchanges and Clearing (HKEX) : opérateur de
  marché/chambre de compensation, même traitement que LSEG (FTSE), Deutsche
  Börse (DAX) et Euronext (CAC40) — standard malgré un profil de bilan
  atypique. Explicitement demandé par la consigne, confirmé constituant
  Hang Seng actuel (sous-indice Financials selon la classification de Hang
  Seng Indexes Company elle-même, mais hors du périmètre "banque/assureur"
  du projet).

**Cas limite signalé, à trancher par un humain plutôt que tranché ici :**

- `0267.HK` — CITIC Limited : conglomérat d'État chinois diversifié
  (possède notamment CITIC Bank, CITIC Securities et CITIC Trust comme
  filiales, mais aussi des activités minières, immobilières, industrielles
  et de négoce). Hang Seng Indexes Company elle-même le classe dans le
  sous-indice "Energy, Materials, Industrials and Conglomerates", **pas**
  dans "Financials" — signal que même le compilateur d'indice ne le
  considère pas comme un établissement financier pur. Je ne l'ai PAS mis
  dans la liste financière ci-dessus (méthodologie standard par défaut),
  mais le poids réel de ses activités bancaires/financières internes
  pourrait justifier un reclassement — à confirmer/infirmer manuellement,
  même logique que St. James's Place (`STJ.L`) dans le rapport FTSE 100.

**Pas dans le sous-indice Financials mais vérifié explicitement par
prudence** (nom pouvant évoquer une activité financière) :

- `0823.HK` — Link Real Estate Investment Trust (Link REIT) : classé par
  Hang Seng Indexes Company dans "Properties & Construction", pas
  "Financials". C'est un REIT (société d'investissement immobilier cotée),
  donc explicitement exclu par la définition du projet ("sociétés
  d'investissement (trusts)") — même traitement que les REIT déjà exclues
  côté FTSE (`LAND.L`, `SGRO.L`, `BBOX.L`, `LMP.L`) et Unibail-Rodamco-
  Westfield/Vonovia côté CAC40/DAX (standard, pas financier).

## 7. Tickers — niveaux de confiance

**Vérifiés individuellement via un appel réel à l'API Yahoo Finance
(`query1.finance.yahoo.com/v8/finance/chart/<ticker>`) cette session — haute
confiance (20 tickers, dont le ticker de l'indice) :**

| Ticker | `longName` confirmé |
|---|---|
| `^HSI` | HANG SENG INDEX (`currency: HKD`) |
| `0700.HK` | Tencent Holdings Limited |
| `0005.HK`* | HSBC Holdings plc (*chevauchement FTSE, pas dans la liste Hang Seng) |
| `0001.HK` | CK Hutchison Holdings Limited |
| `0002.HK` | CLP Holdings Limited |
| `0388.HK` | Hong Kong Exchanges and Clearing Limited |
| `9988.HK` | Alibaba Group Holding Limited |
| `1299.HK` | AIA Group Limited |
| `2318.HK` | Ping An Insurance (Group) Company of China, Ltd. |
| `1398.HK` | Industrial and Commercial Bank of China Limited |
| `3988.HK` | Bank of China Limited |
| `1810.HK` | Xiaomi Corporation |
| `3690.HK` | Meituan |
| `3750.HK` | Contemporary Amperex Technology Co., Limited (CATL) |
| `6160.HK` | BeOne Medicines AG |
| `6181.HK` | Laopu Gold Co., Ltd. |
| `1347.HK` | Hua Hong Grace Semiconductor Limited |
| `2338.HK` | Weichai Power Co., Ltd. |
| `1519.HK` | J&T Global Express Limited |
| `0941.HK` | China Mobile Limited |
| `0981.HK` | Semiconductor Manufacturing International Corporation |

Un test négatif (`700.HK` sans zero-padding → 404) confirme que la règle de
padding n'est pas optionnelle (voir §2). L'échantillon couvre volontairement
les cas les plus à risque : codes à 1-2 chiffres (`1`, `2`, `5`), plusieurs
tailles de code différentes, et des entrées récemment ajoutées/renommées
(Hua Hong Grace et Weichai Power entrées le 07/09/2026 ; BeOne Medicines
= ex-BeiGene renommé).

**Haute-moyenne confiance (74 tickers restants, non appelés individuellement
cette session)** : le code numérique de chacun vient directement de
l'Appendix 1 du communiqué officiel Hang Seng Indexes Company (source
primaire, pas une reconstruction ni une supposition de format), recoupé pour
~29 d'entre eux avec la page constituants en direct d'aastocks.com (§1). Le
risque résiduel porte uniquement sur l'application uniforme de la règle de
zero-padding à 4 chiffres (§2) à ces codes précis — règle déjà confirmée sur
un échantillon de 20 codes couvrant toutes les longueurs (1 à 4 chiffres),
donc jugée fiable, mais **pas vérifiée un par un individuellement** pour ces
74-là. À confirmer au premier run réel (le garde-fou existant côté frontend,
`docs/index.html`, signale déjà les tickers manquants après un run — même
filet de sécurité que pour NASDAQ/FTSE).

**Aucun ticker signalé comme structurellement douteux** au-delà du point
générique ci-dessus — contrairement au FTSE 100 (où `BT-A.L` avait un format
inhabituel), aucune des 94 entrées Hang Seng ne présente de format de ticker
qui s'écarte de la règle uniforme `<code zero-paddé 4 chiffres>.HK`.

## 8. Taux sans risque HKD / FRED

**Aucune série FRED utilisable trouvée pour un taux 10 ans HKD — confirmé,
pas une supposition.**

1. Hong Kong n'est pas membre de l'OCDE, donc la famille de séries
   "IRLTLT01xxM156N" (utilisée pour France/Royaume-Uni dans le code actuel)
   n'a par construction pas d'équivalent hongkongais officiel dans cette
   famille.
2. Test direct : `https://fred.stlouisfed.org/series/IRLTLT01HKM156N` →
   **HTTP 404 Not Found**. La série n'existe pas.
3. Recherche complémentaire : la catégorie FRED dédiée à Hong Kong
   (`fred.stlouisfed.org/categories/32727`, ~50 séries) ne contient **aucune**
   série de taux d'intérêt long terme ou de rendement obligataire domestique
   hongkongais — seulement prix immobiliers, inflation, PIB, démographie,
   taux de change, commerce, ratios bancaires, et détentions d'actifs
   américains par des entités hongkongaises (Treasuries/corporate bonds).
   Aucune piste alternative trouvée (pas de série BIS/Banque mondiale
   relayée sous un autre identifiant FRED pour ce point précis).

**Conséquence pour le code, confirmée plutôt qu'à corriger** : si
`RISK_FREE_SERIES_BY_CURRENCY` n'obtient pas d'entrée `"HKD"` (ce qui est le
choix correct, faute de série existante), le code actuel gère déjà ce cas
proprement sans modification nécessaire — vérifié en lisant `main()` :

```python
risk_free_rate_by_currency = {
    currency: fetch_risk_free_rate(series_id)
    for currency, series_id in RISK_FREE_SERIES_BY_CURRENCY.items()
}
...
risk_free_rate = risk_free_rate_by_currency.get(currency)
```

`risk_free_rate_by_currency.get("HKD")` renverra `None` (clé absente du
dict, comportement par défaut de `.get()`), `estimate_wacc(None, ...)`
renvoie `None` par construction (voir son docstring : "None si une donnée
nécessaire manque"), et l'appelant retombe alors sur
`COST_OF_CAPITAL_PROXY` (8,0%) — exactement le comportement documenté et
voulu pour une devise sans taux sans risque disponible. **Aucune ligne de
code à changer** pour ce point ; juste ne pas ajouter de clé `"HKD"` à
`RISK_FREE_SERIES_BY_CURRENCY` (et surtout ne pas y mettre un faux ID de
série qui échouerait silencieusement de toute façon).

## 9. Exclusions volontaires / contexte historique

Le communiqué du 21/08/2026 (source primaire) documente **zéro sortie** pour
le Hang Seng Index lui-même à cette révision — seulement 2 entrées (Hua Hong
Grace Semiconductor, Weichai Power), total 93→95. Il n'y a donc pas de
"constituant retiré ce trimestre" à lister pour l'indice principal (à la
différence du Hang Seng TECH Index et du Hang Seng Biotech Index, revus dans
le même communiqué, qui ont chacun eu une sortie — Tongcheng Travel et
HUTCHMED respectivement — mais ces deux indices ne sont pas celui demandé
ici).

Pour un historique plus profond des sorties passées du Hang Seng Index
(au-delà de ce seul trimestre), une recherche complémentaire serait
nécessaire — non menée exhaustivement cette session, à ne pas confondre avec
une liste réputée complète. Point notable trouvé en cours de recherche (§5) :
Standard Chartered et Prudential plc, bien que cotées à Hong Kong, ne sont
plus des constituants actuels de l'indice — leur date de sortie historique
exacte n'a pas pu être confirmée par les sources consultées cette session,
seule leur absence actuelle (au 21/08/2026) est certaine.

## 10. Suggestions pour l'intégration (non appliquées, hors périmètre de cette recherche)

Pour mémoire uniquement — aucun de ces changements n'a été fait, cette
recherche ne touchant que ce nouveau fichier :

- `INDEX_NAMES["HANGSENG"] = "Hang Seng"`
- `INDEX_CURRENCY["HANGSENG"] = "HKD"`
- `INDEX_YFINANCE_TICKERS["HANGSENG"] = "^HSI"` (vérifié §3)
- `FINANCIAL_SECTOR_TICKERS` : ajouter les 8 tickers du §6
- `FTSE_COMPANIES` : ajouter `"also_indices": ["HANGSENG"]` à l'entrée HSBC
  (`HSBA.L`) plutôt que dupliquer HSBC dans `HANGSENG_COMPANIES`
- `RISK_FREE_SERIES_BY_CURRENCY` : ne PAS ajouter de clé `"HKD"` (§8)
