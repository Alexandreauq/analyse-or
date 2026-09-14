# IBEX 35 — recherche pour ajout d'indice (analyse-or)

Recherche uniquement — aucun fichier du repo n'a été modifié (ni
`indices_score.py`, ni `docs/index.html`, ni aucun autre fichier existant).
Ce document fournit tout ce qu'il faut pour que quelqu'un d'autre colle
`IBEX35_COMPANIES` dans `indices_score.py` en connaissance de cause.

## 1. Sources et fraîcheur

1. **Source primaire** : Wikipédia, article "IBEX 35" (anglais), récupéré
   le 2026-09-12 via l'API `action=raw` (wikitext brut). Le tableau des
   constituants est explicitement daté : *"As of 23 September 2024, the
   following 35 companies make up the index"*, avec en référence l'avis
   officiel BME *"Composition of IBEX 35® Index as from September 23rd,
   2024"*. Total : **35 lignes** (l'IBEX 35 a exactement 35 constituants
   par construction, pas d'écart possible comme pour le FTSE 100 ou le Dow).
2. **Source de recoupement n°1 (la plus autorisante)** : le document
   officiel BME *"Composición histórica / Historical Constituents — IBEX
   35®"* (PDF, `bolsasymercados.es/dam/descargas/indices/composicion-ibex-35-es-en.pdf`),
   millésime **"Junio 2026 - June 2026"** — c'est-à-dire mis à jour après
   la revue de juin 2026. Ce document liste **chaque revue semestrielle/
   trimestrielle depuis 1991** avec ses inclusions/exclusions. Les lignes
   131 à 136 (23/12/2024, 23/06/2025, 22/09/2025, 22/12/2025, 23/03/2026,
   22/06/2026) indiquent toutes "-" en inclusions ET en exclusions : **la
   composition n'a pas bougé depuis la revue extraordinaire du
   22/07/2024** (entrée de Puig Brands, sortie de Meliá Hotels
   International — ligne 130 du tableau BME). Or la revue de septembre
   2026 (normalement 3e vendredi du mois, donc effective vers le
   21/09/2026) n'a pas encore eu lieu à la date de cette recherche
   (2026-09-12). **La composition Wikipédia du 23/09/2024 est donc
   toujours l'exacte composition en vigueur aujourd'hui** — confirmé par
   la source la plus autorisante possible (BME, l'opérateur de l'indice
   lui-même), pas seulement par recoupement indirect.
3. **Source de recoupement n°2** : investing.com, page "Spain 35
   Components" (`investing.com/indices/spain-35-components`), consultée le
   2026-09-12. Liste les 35 mêmes sociétés sans aucune divergence (mêmes
   35 noms, y compris les entrées les plus récentes/atypiques — Puig
   Brands, Corporación Acciona Energías Renovables, Redeia Corporación).

**Accord sur le nombre de constituants et les noms** : 35 sur 35 sur les
trois sources, aucune divergence non expliquée (contrairement au FTSE 100
où topforeignstocks datait de 6 mois plus tôt que Wikipédia — ici, le
document BME couvre explicitement l'absence de changement jusqu'à fin
juin 2026, donc il n'y a pas d'ambiguïté de fraîcheur du tout).

Contrairement aux recherches Nasdaq-100/FTSE 100 précédentes, l'accès
réseau (WebFetch/WebSearch) a fonctionné normalement dans cette session —
**tous les 35 tickers ont pu être vérifiés individuellement** via un appel
direct à `https://query1.finance.yahoo.com/v8/finance/chart/<TICKER>.MC`
(voir §5), un niveau de confiance supérieur aux recherches précédentes.

## 2. Format des tickers et ticker de l'indice

- **Suffixe confirmé** : `.MC` (Bolsa de Madrid / Mercado Continuo
  Español), comme attendu. Vérifié sur `SAN.MC` → JSON Yahoo Finance :
  `longName: "Banco Santander, S.A."`, `currency: "EUR"`,
  `exchangeName: "MCE"`. Même vérification faite sur `ITX.MC` →
  `"Industria de Diseño Textil, S.A."` (Inditex) et `IBE.MC` →
  `"Iberdrola, S.A."`, toutes deux `currency: EUR`, `exchangeName: MCE`.
- **Ticker de l'indice lui-même** : `^IBEX` confirmé via
  `https://query1.finance.yahoo.com/v8/finance/chart/%5EIBEX` →
  `longName: "IBEX 35"`, `currency: "EUR"`, `exchangeName: "MCE"`,
  `instrumentType: "INDEX"`. À utiliser dans `INDEX_YFINANCE_TICKERS`
  (aux côtés de `^FCHI`, `^GDAXI`, `^NDX`, `^DJI`, `^FTSE`).

## 3. Liste Python `IBEX35_COMPANIES` (32 entrées, triée alphabétiquement)

3 des 35 constituants réels de l'IBEX 35 ne sont **pas dupliqués** ici —
voir §4, ce sont de vrais chevauchements avec des indices déjà suivis,
même choix déjà fait pour Airbus (CAC40/DAX), Coca-Cola Europacific
Partners (NASDAQ/FTSE) et les 9 chevauchements NASDAQ/DOW.

```python
IBEX35_COMPANIES = [
    {"ticker": "ANA.MC", "name": "Acciona"},
    {"ticker": "ANE.MC", "name": "Acciona Energía"},
    {"ticker": "ACX.MC", "name": "Acerinox"},
    {"ticker": "ACS.MC", "name": "ACS"},
    {"ticker": "AENA.MC", "name": "Aena"},
    {"ticker": "AMS.MC", "name": "Amadeus IT Group"},
    {"ticker": "SAB.MC", "name": "Banco Sabadell"},
    {"ticker": "SAN.MC", "name": "Banco Santander"},
    {"ticker": "BKT.MC", "name": "Bankinter"},
    {"ticker": "BBVA.MC", "name": "BBVA"},
    {"ticker": "CABK.MC", "name": "CaixaBank"},
    {"ticker": "CLNX.MC", "name": "Cellnex Telecom"},
    {"ticker": "ENG.MC", "name": "Enagás"},
    {"ticker": "ELE.MC", "name": "Endesa"},
    {"ticker": "FDR.MC", "name": "Fluidra"},
    {"ticker": "GRF.MC", "name": "Grifols"},
    {"ticker": "IBE.MC", "name": "Iberdrola"},
    {"ticker": "ITX.MC", "name": "Inditex"},
    {"ticker": "IDR.MC", "name": "Indra Sistemas"},
    {"ticker": "COL.MC", "name": "Inmobiliaria Colonial"},
    {"ticker": "ROVI.MC", "name": "Laboratorios Rovi"},
    {"ticker": "LOG.MC", "name": "Logista"},
    {"ticker": "MAP.MC", "name": "Mapfre"},
    {"ticker": "MRL.MC", "name": "Merlin Properties"},
    {"ticker": "NTGY.MC", "name": "Naturgy"},
    {"ticker": "PUIG.MC", "name": "Puig Brands"},
    {"ticker": "RED.MC", "name": "Redeia"},
    {"ticker": "REP.MC", "name": "Repsol"},
    {"ticker": "SCYR.MC", "name": "Sacyr"},
    {"ticker": "SLR.MC", "name": "Solaria Energía y Medio Ambiente"},
    {"ticker": "TEF.MC", "name": "Telefónica"},
    {"ticker": "UNI.MC", "name": "Unicaja Banco"},
]
```

Compte : **32 entreprises** (35 constituants réels de l'IBEX 35 moins
ArcelorMittal, Ferrovial et International Airlines Group, déjà suivies
respectivement côté CAC40, NASDAQ et FTSE — l'IBEX 35 est donc à 32/35
par ce choix, pas par erreur, même logique que DAX 39/40, Dow 21/30 et
FTSE 99/100).

Notes de nommage : "ACS" (ACS.MC) reprend le nom d'usage boursier — le
nom légal complet est "ACS, Actividades de Construcción y Servicios,
S.A.", Wikipédia elle-même affiche "ACS" comme texte du lien vers la page
"ACS Group". "Redeia" (RED.MC) est l'ex-Red Eléctrica de España /
Red Eléctrica Corporación, renommé Redeia Corporación en 2021 (le
gestionnaire du réseau électrique espagnol, Red Eléctrica, en est
maintenant une filiale). "Unicaja Banco" pour éviter toute ambiguïté avec
"Unicaja" tout court (le groupe de caisses d'épargne historique).

## 4. Chevauchements avec les indices déjà suivis

**Trois chevauchements trouvés — le plus complexe rencontré jusqu'ici sur
ce projet (le FTSE 100 n'en avait trouvé qu'un seul, CCEP) :**

- **ArcelorMittal** — `MTS.MC` sur Bolsa de Madrid (nom Yahoo Finance :
  "ArcelorMittal S.A.", vérifié individuellement, `currency: EUR`,
  `exchangeName: MCE`). Déjà suivie dans `CAC40_COMPANIES`
  (`indices_score.py`, ligne ~62) sous le ticker `"MT.PA"` (Euronext
  Paris), sans `also_indices`. ArcelorMittal (domiciliée au Luxembourg)
  est multi-cotée : Euronext Amsterdam (cotation de référence), Paris,
  Bruxelles, Luxembourg, **et Madrid** (héritage des activités espagnoles
  d'Aceralia, absorbée lors de la création du groupe en 2006). Action
  recommandée : ajouter `"also_indices": ["IBEX35"]` (ou le nom de clé
  finalement choisi, voir §7) à l'entrée `MT.PA` dans `CAC40_COMPANIES`,
  ne pas dupliquer dans `IBEX35_COMPANIES`.
- **Ferrovial** — `FER.MC` sur Bolsa de Madrid (nom Yahoo Finance :
  "Ferrovial N.V.", vérifié individuellement, `currency: EUR`,
  `exchangeName: MCE`). Déjà suivie dans `NASDAQ_COMPANIES`
  (`indices_score.py`, ligne ~242) sous le ticker `"FER"` (sans suffixe,
  cotation Nasdaq), sans `also_indices`. Ferrovial a redomicilié son siège
  social aux Pays-Bas en 2023 et a fait de sa cotation Nasdaq (US) sa
  cotation de référence depuis 2024-2025, tout en conservant des
  cotations secondaires à Amsterdam et Madrid sous le même groupe —
  exactement le même schéma que Coca-Cola Europacific Partners
  (CCEP, NASDAQ/FTSE) déjà documenté dans le rapport FTSE. Action
  recommandée : ajouter `"also_indices": ["IBEX35"]` à l'entrée `FER`
  dans `NASDAQ_COMPANIES`, ne pas dupliquer dans `IBEX35_COMPANIES`.
- **International Airlines Group (IAG)** — `IAG.MC` sur Bolsa de Madrid
  (nom Yahoo Finance : "International Consolidated Airlines Group S.A.",
  vérifié individuellement, `currency: EUR`, `exchangeName: MCE`). C'est
  la **même société** que `"IAG.L"` déjà suivie dans `FTSE_COMPANIES`
  (`indices_score.py`, ligne ~402, nom "International Airlines Group",
  sans `also_indices`) — IAG est le holding né de la fusion British
  Airways/Iberia en 2011, à double cotation primaire Londres ET Madrid
  depuis sa création (pas une cotation secondaire comme les deux cas
  ci-dessus, un vrai dual-listing). Action recommandée : ajouter
  `"also_indices": ["IBEX35"]` à l'entrée `IAG.L` dans `FTSE_COMPANIES`,
  ne pas dupliquer dans `IBEX35_COMPANIES`.

**Aucun autre chevauchement.** Vérifié nom par nom contre
`CAC40_COMPANIES`, `DAX_COMPANIES`, `NASDAQ_COMPANIES`, `DOW_COMPANIES`
et `FTSE_COMPANIES` — aucune autre des 35 sociétés de l'IBEX 35 ne
correspond à une entreprise déjà suivie ailleurs. Notamment : Amadeus IT
Group, Cellnex Telecom, Grifols, Iberdrola et Inditex — les multinationales
espagnoles les plus connues en dehors des banques — ne sont cotées qu'à
Madrid et n'apparaissent dans aucun autre indice suivi par ce projet.

## 5. Secteur financier (banques / assureurs / réassureurs)

Définition du projet rappelée : uniquement banques de dépôt/crédit et
assureurs/réassureurs qui souscrivent du risque — PAS les gestionnaires
d'actifs, sociétés d'investissement (trusts), opérateurs de marché ou
courtiers/plateformes.

L'IBEX 35 est effectivement très concentré en banques par rapport aux
autres indices déjà suivis (6 banques sur 35 constituants, soit 17 % —
contre 4/40 pour le CAC40 et 4/39 pour le DAX) :

| Société | Ticker | Statut |
|---|---|---|
| Banco Santander | SAN.MC | Financier — banque de réseau + banque d'investissement, 1er groupe bancaire espagnol par capitalisation |
| BBVA | BBVA.MC | Financier — banque de réseau + banque d'investissement, forte exposition Amérique latine/Turquie |
| CaixaBank | CABK.MC | Financier — banque de réseau (1er réseau de dépôts en Espagne après la fusion avec Bankia en 2021) |
| Banco Sabadell | SAB.MC | Financier — banque de réseau (a résisté à l'OPA hostile de BBVA en 2024-2025) |
| Bankinter | BKT.MC | Financier — banque de réseau (banque privée + retail) |
| Unicaja Banco | UNI.MC | Financier — banque de réseau (née de la fusion Unicaja/Liberbank en 2021) |
| Mapfre | MAP.MC | Financier — assureur composite (vie + non-vie), souscripteur de risque classique, présence internationale forte (Amérique latine) |

**Liste finale des tickers financiers à ajouter à `FINANCIAL_SECTOR_TICKERS`
(7) :**

```python
"SAN.MC", "BBVA.MC", "CABK.MC", "SAB.MC", "BKT.MC", "UNI.MC",  # banques
"MAP.MC",  # assureur (Mapfre)
```

**Explicitement exclues du traitement financier** (immobilier coté,
infrastructure, gestion de voyage — pas des banques/assureurs au sens du
projet, même si leur bilan est atypique) :

- `MRL.MC` Merlin Properties et `COL.MC` Inmobiliaria Colonial —
  sociétés foncières cotées (SOCIMI, l'équivalent espagnol des REIT),
  même traitement que Land Securities/Segro (FTSE) et Vonovia (DAX) :
  méthodologie standard malgré un profil de bilan atypique.
- `CLNX.MC` Cellnex Telecom — opérateur d'infrastructures de
  télécommunications (pylônes/tours), pas un établissement financier
  malgré un bilan très endetté ; même traitement que Deutsche Börse (DAX)
  et Euronext/LSEG (CAC40/FTSE) : atypique mais standard.
- `AMS.MC` Amadeus IT Group — système de distribution mondial pour le
  voyage (GDS), pas financier malgré son rôle de plateforme de paiement
  dans le secteur touristique.

Aucun cas limite comparable à St. James's Place (FTSE) n'a été identifié
dans l'IBEX 35 : pas de gestionnaire d'actifs coté dans l'indice
actuellement (contrairement au FTSE avec Schroders/3i/les trusts fermés),
donc pas de zone grise à trancher ici.

## 6. Taux sans risque (devise EUR)

L'IBEX 35 est libellé en EUR (confirmé §2 : tous les tickers `.MC` et
`^IBEX` renvoient `currency: "EUR"`) — **aucune nouvelle plomberie de
devise n'est nécessaire**. `INDEX_CURRENCY["IBEX35"] = "EUR"` et
`RISK_FREE_SERIES_BY_CURRENCY["EUR"]` (déjà égal à
`FRED_RISK_FREE_SERIES = "IRLTLT01FRM156N"`, l'OAT 10 ans française)
couvrent directement ce nouvel indice sans modification.

Sur la pertinence de réutiliser le taux OAT français comme proxy pour des
entreprises espagnoles : raisonnable pour cette méthodologie. Le spread
souverain France/Espagne (OAT/Bono) s'est resserré ces dernières années
et reste de l'ordre de quelques dizaines de points de base sur le 10 ans
— un écart mineur au regard de la marge d'erreur déjà inhérente à
l'utilisation d'un taux sans risque unique pour toute une zone monétaire
(le même choix a déjà été fait implicitement pour le DAX, dont les
entreprises sont allemandes et non françaises). Ce n'est pas le proxy
théoriquement le plus pur (un Bono espagnol 10 ans existe et serait plus
exact), mais il est cohérent avec la convention EUR déjà en place et ne
justifie pas à lui seul l'ajout d'une 4e série FRED pour ce projet.

## 7. Tickers — confiance et vérification

Contrairement aux recherches Nasdaq-100 et FTSE 100 précédentes (réseau
bloqué en sandbox à l'époque), **les 35 tickers de constituants ont pu
être vérifiés individuellement** via un appel direct à l'endpoint Yahoo
Finance (`chart/<TICKER>.MC`), confirmant `longName` et `currency: EUR`
pour chacun. Aucun ticker inhabituel ou surprenant identifié — tous
suivent le motif standard `XXXX.MC`, sans particularité comparable au
`BT-A.L` du FTSE (pas de tiret, pas de suffixe de classe d'action parmi
les 35).

**Tickers vérifiés individuellement (haute confiance, les 35) :**
ACS.MC (ACS), ACX.MC (Acerinox), AMS.MC (Amadeus IT Group), ANA.MC
(Acciona), ANE.MC (Acciona Energía), BBVA.MC (BBVA), BKT.MC (Bankinter),
CABK.MC (CaixaBank), CLNX.MC (Cellnex Telecom), COL.MC (Inmobiliaria
Colonial), AENA.MC (Aena), ELE.MC (Endesa), ENG.MC (Enagás), FDR.MC
(Fluidra), FER.MC (Ferrovial — non dupliqué, voir §4), GRF.MC (Grifols),
IAG.MC (International Airlines Group — non dupliqué, voir §4), IBE.MC
(Iberdrola), IDR.MC (Indra Sistemas), ITX.MC (Inditex), LOG.MC (Logista),
MAP.MC (Mapfre), MRL.MC (Merlin Properties), MTS.MC (ArcelorMittal — non
dupliqué, voir §4), NTGY.MC (Naturgy), PUIG.MC (Puig Brands), RED.MC
(Redeia), REP.MC (Repsol), ROVI.MC (Laboratorios Rovi), SAB.MC (Banco
Sabadell), SAN.MC (Banco Santander), SCYR.MC (Sacyr), SLR.MC (Solaria
Energía y Medio Ambiente), TEF.MC (Telefónica), UNI.MC (Unicaja Banco).
`^IBEX` (ticker de l'indice) également vérifié individuellement.

Il n'y a donc **aucun ticker en confiance "moyenne"** pour cette
recherche — un net progrès méthodologique par rapport aux rapports
Nasdaq-100/FTSE 100, entièrement dû à un environnement réseau qui a
fonctionné normalement cette fois-ci plutôt qu'à un travail de
vérification différent. Le premier run réel en production reste
malgré tout la vérification définitive, comme pour tout indice.

## 8. Exclusions notables (constituants historiques, plus dans l'indice)

Le seul changement de composition depuis la version datée du §1 (et le
seul changement depuis juillet 2024, confirmé par le document BME
"Junio 2026") :

- **Meliá Hotels International (MEL)** — sortie de l'indice le
  22/07/2024, remplacée par **Puig Brands (PUIG)** lors de son
  introduction en bourse. Revue classée "extraordinaire" par BME (pas une
  revue trimestrielle normale).

Pour mémoire, quelques consolidations bancaires antérieures expliquent
pourquoi le secteur financier de l'IBEX 35 compte "seulement" 6 banques
aujourd'hui malgré son poids (contexte, pas une anomalie à corriger) :
**Bankia (BKIA)** a fusionné dans CaixaBank en 2021 (absorption, sortie
de l'indice) ; **Banco Popular (POP)** a été racheté par Banco Santander
pour 1 € en 2017 après résolution bancaire (sortie de l'indice la même
année) ; **Liberbank** a fusionné avec Unicaja Banco en 2021 (Liberbank
n'était de toute façon plus un constituant IBEX 35 à cette date). Ces
mouvements sont antérieurs à la fenêtre "juin 2026 sans changement" du
§1 et n'affectent donc pas la liste actuelle, mais expliquent la
concentration bancaire actuelle par consolidation plutôt que par
addition de nouvelles banques.

## 9. Point ouvert pour l'intégration (pas tranché par cette recherche)

Cette recherche utilise `"IBEX35"` comme nom de clé provisoire dans les
exemples ci-dessus (`also_indices`, `FINANCIAL_SECTOR_TICKERS`), par
parallélisme avec le nom demandé pour la liste Python
(`IBEX35_COMPANIES`). Les clés existantes dans `INDEX_NAMES`/
`INDEX_CURRENCY`/`INDEX_YFINANCE_TICKERS` sont toutes courtes et sans
suffixe numérique (`"CAC40"` fait exception avec son "40", mais `"DAX"`,
`"NASDAQ"`, `"DOW"`, `"FTSE"` n'en ont pas alors que leurs indices
comptent respectivement ~40/100/30 valeurs). Le choix final entre
`"IBEX35"` et `"IBEX"` comme clé d'indice est laissé à qui fera
l'intégration réelle dans `indices_score.py` — un détail cosmétique sans
impact sur les données de cette recherche.
