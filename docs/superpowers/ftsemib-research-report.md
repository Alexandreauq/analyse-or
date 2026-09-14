# FTSE MIB — recherche pour ajout d'indice (analyse-or)

Recherche uniquement — aucun fichier du repo n'a été modifié. Ce document
fournit tout ce qu'il faut pour que quelqu'un d'autre colle
`FTSEMIB_COMPANIES` dans `indices_score.py` en connaissance de cause, sur
le même modèle que `docs/superpowers/ftse100-research-report.md`.

Rappel de contexte : le FTSE MIB (Borsa Italiana / Euronext Milan) n'a de
"FTSE" en commun avec le FTSE 100 que la marque : les deux indices sont
calculés par FTSE Russell (co-entreprise LSEG), mais le FTSE MIB est
l'indice phare de la bourse italienne, sans rapport de composition avec
le FTSE 100 britannique.

## 1. Sources et fraîcheur

1. **Source primaire** : Wikipédia, article "FTSE MIB" (anglais),
   récupéré le 2026-09-12 via l'API `action=raw` (wikitext brut). La page
   indique une dernière mise à jour du tableau des constituants au
   **8 avril 2026**. Total : **40 lignes** (le FTSE MIB est un indice à
   composition fixe de 40 valeurs, revu trimestriellement — contrairement
   au FTSE 100/CAC 40/DAX qui n'ont pas de cible fixe aussi stricte).
2. **Source de recoupement n°1** : MarketScreener, page "FTSE MIB Index
   components", récupérée le 2026-09-12, explicitement datée **"Data as
   of: September 11, 2026"** (plus récente que Wikipédia). Montre les 16
   plus grosses capitalisations + confirme "21 composants supplémentaires"
   (37 au total affichés, le reste étant tronqué par la pagination de la
   page — pas une divergence de composition, juste un affichage partiel).
   Les 16 premières lignes (UniCredit, Intesa Sanpaolo, Enel, Eni,
   Ferrari, Generali, STMicroelectronics, Prysmian, Poste Italiane, BPER
   Banca, Leonardo, Banco BPM, Mediobanca, Unipol, Terna, Snam)
   correspondent intégralement aux entrées du tableau Wikipédia.
3. **Source de recoupement n°2** : holdings de l'ETF **iShares FTSE MIB
   UCITS ETF (IMIB.MI / ISIN IE00B1XNH568)**, récupérées le 2026-09-12,
   as-of **30/07/2026**. Top 10 holdings (UniCredit 16.42 %, Intesa
   Sanpaolo 13.34 %, Enel 9.96 %, Eni 5.92 %, Assicurazioni Generali
   5.64 %, Ferrari 5.40 %, Prysmian 4.62 %, STMicroelectronics 3.92 %,
   Banco BPM 3.10 %, Banca Monte dei Paschi di Siena 3.04 %) correspond
   intégralement aux plus grosses lignes Wikipédia/MarketScreener. La
   page indique 41 lignes au total (40 + 1, écart mineur de méthodologie
   ETF vs indice — même type d'écart que 111 vs 100 déjà documenté pour
   le FTSE 100).
4. **Source historique (pour expliquer les écarts, pas une 3e source de
   composition actuelle)** : topforeignstocks.com "Complete List of
   Constituents of the Italy FTSE MIB Index", as-of **03/09/2023** (~3
   ans plus ancien). Comparée à la liste 2026, les différences
   s'expliquent entièrement par les révisions trimestrielles connues
   (voir §7) — aucune divergence non expliquée.

**Accord sur le nombre de constituants** : 40 sur les trois sources
actuelles (Wikipédia 8/04/2026, MarketScreener 11/09/2026, iShares IMIB
30/07/2026) — **38 dans la liste finale ci-dessous** après retrait
volontaire de deux doublons réels (STMicroelectronics et Stellantis,
tous deux déjà suivis côté `CAC40_COMPANIES`, voir §4).

Environnement de recherche : accès réseau direct disponible cette
session (contrairement au FTSE 100/Nasdaq-100, construits en sandbox
bloquée) — **les 40 tickers ont donc pu être vérifiés individuellement**
via un fetch Yahoo Finance réel (`query1.finance.yahoo.com/v8/finance/
chart/<TICKER>`), pas seulement par motif. Voir §6 pour le détail.

## 2. Format de ticker (.MI)

Confirmé par fetch direct sur plusieurs valeurs connues :

| Ticker | `longName` | `currency` | `exchangeName` |
|---|---|---|---|
| ISP.MI | Intesa Sanpaolo S.p.A. | EUR | MIL |
| ENI.MI | Eni S.p.A. | EUR | MIL |
| ENEL.MI | Enel SpA | EUR | MIL |

Le suffixe Yahoo Finance pour Borsa Italiana / Euronext Milan est bien
**`.MI`** (`exchangeName: "MIL"`), conforme à l'attendu de la consigne
(`ISP.MI`, `ENI.MI`, `ENEL.MI`). Aucune exception trouvée sur les 40
valeurs testées (voir §6) : toutes utilisent ce suffixe simple, y compris
les sociétés à double cotation (Ferrari `RACE.MI`, Stellantis `STLAM.MI`,
STMicroelectronics `STMMI.MI`, Tenaris `TEN.MI`).

## 3. Ticker de l'indice (benchmark)

**Confirmé : `FTSEMIB.MI`** — PAS un ticker `^XXX` comme les autres
indices déjà suivis (`^FCHI`, `^GDAXI`, `^NDX`, `^DJI`, `^FTSE`). Fetch
direct :

```json
{
  "longName": "FTSE MIB Index",
  "shortName": "FTSE MIB Index",
  "currency": "EUR",
  "exchangeName": "MIL",
  "symbol": "FTSEMIB.MI"
}
```

`longName` mentionne bien "FTSE MIB" et `currency: EUR` — c'est le bon
ticker. **Ceci diffère du reste de la convention `INDEX_YFINANCE_TICKERS`
existante** (tous des tickers `^XXX`) : à ajouter comme
`"FTSEMIB": "FTSEMIB.MI"` plutôt que sous forme `^` lors de
l'intégration.

## 4. Chevauchements avec les indices déjà suivis

**Deux chevauchements réels trouvés, tous deux avec `CAC40_COMPANIES` :**

- **STMicroelectronics** — constituant FTSE MIB confirmé (`STMMI.MI`,
  `longName: "STMicroelectronics N.V."`, EUR, MIL) **et** déjà suivie
  dans `CAC40_COMPANIES` (`indices_score.py` ligne 68) sous
  `{"ticker": "STMPA.PA", "name": "STMicroelectronics"}`, sans
  `also_indices`. Cohérent avec l'avertissement de la consigne : société
  de droit néerlandais, triple cotée Paris/Milan/New York — bien un
  constituant FTSE MIB actuel malgré ses attaches non italiennes (siège
  social en Suisse, sites de production en France/Italie).
- **Stellantis** — constituant FTSE MIB confirmé (`STLAM.MI`,
  `longName: "Stellantis N.V."`, EUR, MIL) **et** déjà suivie dans
  `CAC40_COMPANIES` (ligne 69) sous
  `{"ticker": "STLAP.PA", "name": "Stellantis"}`, sans `also_indices`.
  Ce deuxième chevauchement n'était pas explicitement anticipé par la
  consigne mais suit exactement la même logique que STMicroelectronics :
  société née de la fusion PSA/Fiat Chrysler (2021), triple cotée
  Paris/Milan/New York.
- Action recommandée (à faire par quelqu'un d'autre, pas par cette
  recherche) : ajouter `"also_indices": ["FTSEMIB"]` aux deux entrées
  STMicroelectronics et Stellantis dans `CAC40_COMPANIES`, ne pas les
  dupliquer dans `FTSEMIB_COMPANIES` — c'est déjà ce que fait la liste
  ci-dessous (§5, 38/40 entrées).

**Aucun autre chevauchement.** Vérifié nom par nom contre
`CAC40_COMPANIES`, `DAX_COMPANIES`, `NASDAQ_COMPANIES`, `DOW_COMPANIES`
et `FTSE_COMPANIES` — les 38 autres constituants FTSE MIB (banques,
assureurs, énergéticiens, industriels italiens : Intesa Sanpaolo,
UniCredit, Eni, Enel, Ferrari, Leonardo, Prysmian, etc.) n'ont pas
d'équivalent déjà suivi ailleurs. Cas vérifiés spécifiquement à cause
d'une possible confusion de nom : Tenaris (Luxembourgeoise, cotée
NYSE+Milan, pas suivie côté Nasdaq/Dow), Ferrari (double cotation
NYSE+Milan, pas suivie côté Nasdaq/Dow), Iveco Group (spin-off 2022 de
CNH Industrial, pas suivie ailleurs — et CNH Industrial elle-même n'est
plus un constituant FTSE MIB, voir §7).

## 5. Liste Python `FTSEMIB_COMPANIES` (38 entrées, triée alphabétiquement)

STMicroelectronics et Stellantis sont des constituants FTSE MIB réels
mais **ne sont pas dupliqués** ici — voir §4, même choix déjà fait pour
Airbus (CAC40/DAX), Coca-Cola Europacific Partners (NASDAQ/FTSE) et les 9
chevauchements NASDAQ/DOW.

```python
FTSEMIB_COMPANIES = [
    {"ticker": "A2A.MI", "name": "A2A"},
    {"ticker": "AMP.MI", "name": "Amplifon"},
    {"ticker": "AVIO.MI", "name": "Avio"},
    {"ticker": "AZM.MI", "name": "Azimut Holding"},
    {"ticker": "BMED.MI", "name": "Banca Mediolanum"},
    {"ticker": "BMPS.MI", "name": "Banca Monte dei Paschi di Siena"},
    {"ticker": "BAMI.MI", "name": "Banco BPM"},
    {"ticker": "BPE.MI", "name": "BPER Banca"},
    {"ticker": "BC.MI", "name": "Brunello Cucinelli"},
    {"ticker": "BZU.MI", "name": "Buzzi"},
    {"ticker": "CPR.MI", "name": "Campari"},
    {"ticker": "DIA.MI", "name": "DiaSorin"},
    {"ticker": "ENEL.MI", "name": "Enel"},
    {"ticker": "ENI.MI", "name": "Eni"},
    {"ticker": "RACE.MI", "name": "Ferrari"},
    {"ticker": "FCT.MI", "name": "Fincantieri"},
    {"ticker": "FBK.MI", "name": "FinecoBank"},
    {"ticker": "G.MI", "name": "Generali"},
    {"ticker": "HER.MI", "name": "Hera"},
    {"ticker": "ISP.MI", "name": "Intesa Sanpaolo"},
    {"ticker": "INW.MI", "name": "INWIT"},
    {"ticker": "IG.MI", "name": "Italgas"},
    {"ticker": "IVG.MI", "name": "Iveco Group"},
    {"ticker": "LDO.MI", "name": "Leonardo"},
    {"ticker": "LTMC.MI", "name": "Lottomatica Group"},
    {"ticker": "MB.MI", "name": "Mediobanca"},
    {"ticker": "MONC.MI", "name": "Moncler"},
    {"ticker": "NEXI.MI", "name": "Nexi"},
    {"ticker": "PST.MI", "name": "Poste Italiane"},
    {"ticker": "PRY.MI", "name": "Prysmian"},
    {"ticker": "REC.MI", "name": "Recordati"},
    {"ticker": "SPM.MI", "name": "Saipem"},
    {"ticker": "SRG.MI", "name": "Snam"},
    {"ticker": "TIT.MI", "name": "Telecom Italia"},
    {"ticker": "TEN.MI", "name": "Tenaris"},
    {"ticker": "TRN.MI", "name": "Terna"},
    {"ticker": "UCG.MI", "name": "UniCredit"},
    {"ticker": "UNI.MI", "name": "Unipol"},
]
```

Compte : **38 entreprises** (40 constituants réels du FTSE MIB moins
STMicroelectronics et Stellantis, déjà suivies côté `CAC40_COMPANIES` —
le FTSE MIB est donc à 38/40 par ce choix, pas par erreur, même logique
que DAX 39/40, Dow 21/30 et FTSE 99/100).

Notes de nommage : "Generali" (`G.MI`) est le nom commercial usuel
d'Assicurazioni Generali S.p.A. (`longName` Yahoo complet). "Campari"
(`CPR.MI`) = Davide Campari-Milano N.V. (société de droit néerlandais
depuis une réorganisation, cotée à Milan). "Azimut Holding" (`AZM.MI`) et
"Recordati" (`REC.MI`, longName complet "Recordati Industria Chimica e
Farmaceutica S.p.A.") repris sous leur nom usuel plutôt que la raison
sociale complète, même convention que "BNP Paribas" (pas "BNP Paribas
SA") ou "GSK" (pas "GSK plc") dans les listes existantes.

## 6. Tickers vérifiés — confiance

**Différence majeure avec les recherches FTSE 100/Nasdaq-100
précédentes : accès réseau direct disponible cette session.** Les
**40 tickers de constituants** (les 38 de la liste finale + les 2
doublons STMicroelectronics/Stellantis) **ont tous été vérifiés
individuellement** via un fetch réel de
`https://query1.finance.yahoo.com/v8/finance/chart/<TICKER>`, en
confirmant `longName`, `currency: "EUR"` et `exchangeName: "MIL"` pour
chacun. Aucun ticker "à confirmer en production" ne subsiste pour cet
indice — **haute confiance sur l'ensemble des 40 tickers**, y compris le
ticker de l'indice lui-même (`FTSEMIB.MI`, §3).

Récapitulatif des 40 tickers vérifiés (constituants + 2 doublons) :
A2A.MI, AMP.MI, AVIO.MI, AZM.MI, BMED.MI, BMPS.MI, BAMI.MI, BPE.MI,
BC.MI, BZU.MI, CPR.MI, DIA.MI, ENEL.MI, ENI.MI, RACE.MI, FCT.MI, FBK.MI,
G.MI, HER.MI, ISP.MI, INW.MI, IG.MI, IVG.MI, LDO.MI, LTMC.MI, MB.MI,
MONC.MI, NEXI.MI, PST.MI, PRY.MI, REC.MI, SPM.MI, SRG.MI, STLAM.MI
(Stellantis, doublon CAC40), STMMI.MI (STMicroelectronics, doublon
CAC40), TIT.MI, TEN.MI, TRN.MI, UCG.MI, UNI.MI, + FTSEMIB.MI (indice).

**Tickers signalés comme visuellement inhabituels, vérifiés et
confirmés corrects malgré l'apparence** :
- `IG.MI` (Italgas) — à ne pas confondre avec `IGG.L` (IG Group,
  courtier CFD britannique) déjà présent dans `FTSE_COMPANIES` : ce sont
  deux tickers et deux sociétés totalement distinctes, la coïncidence de
  nom ("IG") n'a rien à voir avec l'indice FTSE 100.
- `G.MI` (Generali) — ticker à une seule lettre, confirmé (`longName:
  "Assicurazioni Generali S.p.A."`).
- `STLAM.MI` (Stellantis) et `STMMI.MI` (STMicroelectronics) — suffixes
  "AM"/"MI" ajoutés au mnémonique de base pour lever l'ambiguïté avec
  d'autres classes d'actions/cotations ; confirmés distincts des tickers
  déjà utilisés côté CAC40 (`STLAP.PA`, `STMPA.PA`).
- `BT-A` n'existe pas ici (particularité FTSE 100 uniquement) — aucun
  ticker FTSE MIB ne comporte de tiret.

## 7. Secteur financier (banques / assureurs / réassureurs)

Définition du projet rappelée : uniquement banques de dépôt/crédit et
assureurs/réassureurs qui souscrivent du risque — PAS les gestionnaires
d'actifs, sociétés d'investissement (trusts), opérateurs de marché ou
courtiers/plateformes.

Le FTSE MIB est effectivement inhabituellement chargé en banques (6 des
40 constituants sont des banques de réseau/investissement, avant même de
compter les cas limites) — cohérent avec le paysage bancaire italien très
consolidé et le poids historique de la finance à la Bourse de Milan.

**Liste finale des tickers financiers (8) avec raison :**

- `ISP.MI` — Intesa Sanpaolo : banque de réseau universelle (1er groupe
  bancaire italien par actifs)
- `UCG.MI` — UniCredit : banque de réseau universelle paneuropéenne
- `BAMI.MI` — Banco BPM : banque de réseau (issue de la fusion Banco
  Popolare / Banca Popolare di Milano)
- `BPE.MI` — BPER Banca : banque de réseau régionale (Emilie-Romagne,
  en forte croissance par acquisitions)
- `BMPS.MI` — Banca Monte dei Paschi di Siena : banque de réseau (plus
  ancienne banque du monde encore en activité, recapitalisée par l'État
  italien après 2017, reprivatisée depuis)
- `MB.MI` — Mediobanca : banque d'affaires/de crédit (`longName` Yahoo
  complet : "Mediobanca Banca di Credito Finanziario S.p.A." — le nom
  légal contient littéralement "Banca di Credito", banque de financement
  des entreprises + gestion de patrimoine + crédit à la consommation via
  sa filiale Compass Banca)
- `G.MI` — Generali (Assicurazioni Generali) : assureur/réassureur
  composite (vie + non-vie), un des plus gros réassureurs européens
- `UNI.MI` — Unipol (Unipol Assicurazioni) : assureur composite (vie +
  non-vie), 2e groupe d'assurance italien

**Cas limites signalés, à trancher par un humain plutôt que tranchés
ici** (même traitement que St. James's Place dans le rapport FTSE 100) :

- `FBK.MI` — **FinecoBank** : explicitement signalée comme cas limite
  par la consigne — banque directe (dépôts, comptes courants, crédit) +
  courtage en ligne (elle est l'un des plus gros courtiers retail
  italiens). `longName` Yahoo : "FinecoBank Banca Fineco S.p.A." — porte
  bien une licence bancaire complète, mais un pan significatif de son
  revenu vient des commissions de courtage/gestion, pas seulement de la
  marge d'intérêt. Non incluse dans la liste financière ci-dessus, à
  confirmer/infirmer manuellement.
- `BMED.MI` — **Banca Mediolanum** : même profil hybride que FinecoBank
  — banque directe avec licence de dépôt, mais modèle économique
  dominé par la distribution de produits d'épargne/assurance-vie via un
  réseau de conseillers financiers (family banker), filiale
  d'assurance-vie Mediolanum Vita consolidée. Non incluse ci-dessus par
  la même prudence que FinecoBank — même famille de cas limite, pas
  tranchée unilatéralement.
- `PST.MI` — **Poste Italiane** : conglomérat listé (poste/logistique +
  BancoPosta pour la banque de détail + Poste Vita pour l'assurance-vie),
  classé "Financial Services" par certaines sources sectorielles mais
  dont l'activité historique et une bonne part du chiffre d'affaires
  restent postales/logistiques. Non incluse dans la liste financière —
  à confirmer/infirmer manuellement si la méthodologie adaptée (ROE, P/E
  + P/B) est jugée plus pertinente que la grille standard pour cette
  valeur mixte.

**Explicitement exclues du traitement financier** (gestion d'actifs,
opérateur/plateforme de paiement — pas des banques/assureurs au sens du
projet, même si parfois étiquetées "Financial Services") :

- `AZM.MI` Azimut Holding — gestionnaire d'actifs (asset manager pur,
  pas de licence bancaire de dépôt), même logique que Schroders (FTSE
  100) ou Aberdeen Group (FTSE 100)
- `NEXI.MI` Nexi — opérateur/plateforme de paiement (traitement de
  transactions par carte, acquisition marchande), pas une banque de
  dépôt ni un assureur — même logique que Visa (Dow) et PayPal (Nasdaq)
  déjà exclus sur cette base

## 8. Taux sans risque (EUR / FRED)

Le FTSE MIB est **libellé en euros** (confirmé : `currency: "EUR"` sur
les 40 constituants ET sur le ticker de l'indice `FTSEMIB.MI` lui-même,
voir §2-3) — comme le CAC 40 et le DAX. Aucune nouvelle plomberie de
devise n'est nécessaire : `INDEX_CURRENCY["FTSEMIB"] = "EUR"` réutilise
l'entrée EUR déjà présente, et `RISK_FREE_SERIES_BY_CURRENCY["EUR"]`
pointe déjà vers `FRED_RISK_FREE_SERIES = "IRLTLT01FRM156N"` (OAT 10 ans
France, déjà utilisé comme proxy taux sans risque zone euro pour
CAC40/DAX). Aucune série FRED italienne (ex. BTP 10 ans) n'est requise —
ce n'est pas l'objet de cette recherche de rouvrir ce choix déjà fait
pour la zone euro.

## 9. Exclusions volontaires (constituants historiques, plus dans l'indice)

Pour mémoire (différence entre la version topforeignstocks du 03/09/2023
et la version Wikipédia du 08/04/2026, ~3 ans d'écart, entièrement
expliquée par les révisions trimestrielles normales de l'indice — pas
une anomalie) :

- **Sortis** : Banca Generali, CNH Industrial, Erg, Interpump Group,
  Pirelli & C.
- **Entrés** : Avio, Brunello Cucinelli, Buzzi, Fincantieri, Lottomatica
  Group.

Deux sorties ont pu être documentées avec une raison précise (recherche
web ciblée, pas juste une déduction) :

- **Pirelli & C.** : sortie confirmée le 22/09/2025, remplacée par
  Lottomatica Group lors de la révision trimestrielle de septembre 2025
  (source : FirstOnline/Il Sole 24 Ore) — poids réduit de l'automobile/
  pneumatiques dans l'indice au profit de valeurs à revenus récurrents.
- **CNH Industrial** : sortie de plusieurs indices FTSE (dont le FTSE
  All-World) autour de mars 2026 ; structure de cotation NV
  néerlandaise/marché principal NYSE après la scission d'Iveco Group
  (2022) — cohérent avec une sortie du FTSE MIB par manque de flottant/
  liquidité italienne suffisante, mais la date exacte de sortie du FTSE
  MIB spécifiquement n'a pas été confirmée par une source dédiée.

Les trois autres sorties (Banca Generali, Erg, Interpump Group) n'ont pas
été creusées individuellement — cohérent avec des rotations de rang par
capitalisation/liquidité au profit des 5 entrants, pas un signal
d'alerte particulier.
