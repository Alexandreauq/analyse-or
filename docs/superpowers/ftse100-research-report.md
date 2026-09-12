# FTSE 100 — recherche pour ajout d'indice (analyse-or)

Recherche uniquement — aucun fichier du repo n'a été modifié. Ce document
fournit tout ce qu'il faut pour que quelqu'un d'autre colle `FTSE_COMPANIES`
dans `indices_score.py` en connaissance de cause.

## 1. Sources et fraîcheur

1. **Source primaire** : Wikipédia, article "FTSE 100 Index" (anglais),
   récupéré le 2026-09-12 via l'API `action=raw` (wikitext brut, pas le
   rendu HTML tronqué). La légende du tableau des constituants indique
   explicitement : *"The following table lists the FTSE 100 companies
   after the changes on 19 June 2026."* → **version au 19/06/2026**.
   Total : **100 lignes** dans le tableau.
2. **Source de recoupement n°1** : holdings de l'ETF **iShares Core FTSE
   100 UCITS ETF (ISF)**, via stockanalysis.com/quote/lon/ISF/holdings/ —
   as-of **28/08/2026** (plus récent que Wikipédia). Le top 25 des lignes
   affichées (HSBC, Shell, AstraZeneca, Rolls-Royce, Unilever, British
   American Tobacco, BP, Rio Tinto, GSK, Barclays, Lloyds, Glencore, BAE
   Systems, National Grid, NatWest, RELX, Anglo American, LSEG, Standard
   Chartered, Compass, Diageo, Reckitt, Haleon, SSE, Tesco) correspond
   intégralement aux entrées du tableau Wikipédia — aucune divergence sur
   les plus grosses capitalisations. La page payante ne montre que 25/111
   lignes en clair (111 = léger écart de méthodologie ETF vs indice, pas
   une alerte).
3. **Source de recoupement n°2** : topforeignstocks.com "Complete List of
   Constituents of the FTSE 100 Index", as-of **07/12/2025** (~6 mois plus
   ancien que Wikipédia — 2 réajustements trimestriels d'écart). Confirme
   100 lignes à cette date-là et le format de ticker Yahoo (`XXX.L`) pour
   la quasi-totalité des lignes communes. Les écarts entre cette liste et
   celle de Wikipédia s'expliquent entièrement par les mouvements
   trimestriels connus (voir §4) — aucune divergence non expliquée.

**Accord sur le nombre de constituants** : 100 sur les trois sources (99
sur la liste finale ci-dessous après retrait volontaire du doublon
Coca-Cola Europacific Partners, voir §3).

Environnement de recherche : sandbox sans accès yfinance/Wikipédia direct
(bloqué comme documenté pour le Nasdaq-100) — tout ce document vient de
WebSearch/WebFetch, pas d'un run yfinance réel. Comme pour le Nasdaq-100,
la vérification définitive des tickers se fera au premier run GitHub
Actions.

## 2. Liste Python `FTSE_COMPANIES` (99 entrées, triée alphabétiquement)

Coca-Cola Europacific Partners (CCEP) est un constituant FTSE 100 réel
mais n'est **pas dupliqué** ici — voir §3, c'est le même choix déjà fait
pour Airbus (CAC40/DAX) et les 9 chevauchements NASDAQ/DOW.

```python
FTSE_COMPANIES = [
    {"ticker": "III.L", "name": "3i Group"},
    {"ticker": "ABDN.L", "name": "Aberdeen Group"},
    {"ticker": "ADM.L", "name": "Admiral Group"},
    {"ticker": "AAF.L", "name": "Airtel Africa"},
    {"ticker": "ALW.L", "name": "Alliance Witan"},
    {"ticker": "AAL.L", "name": "Anglo American"},
    {"ticker": "ANTO.L", "name": "Antofagasta"},
    {"ticker": "ABF.L", "name": "Associated British Foods"},
    {"ticker": "AZN.L", "name": "AstraZeneca"},
    {"ticker": "AUTO.L", "name": "Auto Trader Group"},
    {"ticker": "AV.L", "name": "Aviva"},
    {"ticker": "BAB.L", "name": "Babcock International"},
    {"ticker": "BA.L", "name": "BAE Systems"},
    {"ticker": "BARC.L", "name": "Barclays"},
    {"ticker": "BTRW.L", "name": "Barratt Redrow"},
    {"ticker": "BEZ.L", "name": "Beazley"},
    {"ticker": "BP.L", "name": "BP"},
    {"ticker": "BATS.L", "name": "British American Tobacco"},
    {"ticker": "BLND.L", "name": "British Land"},
    {"ticker": "BT-A.L", "name": "BT Group"},
    {"ticker": "BNZL.L", "name": "Bunzl"},
    {"ticker": "BRBY.L", "name": "Burberry Group"},
    {"ticker": "CNA.L", "name": "Centrica"},
    {"ticker": "CCH.L", "name": "Coca-Cola HBC"},
    {"ticker": "CPG.L", "name": "Compass Group"},
    {"ticker": "CCC.L", "name": "Computacenter"},
    {"ticker": "CTEC.L", "name": "Convatec Group"},
    {"ticker": "CRDA.L", "name": "Croda International"},
    {"ticker": "DCC.L", "name": "DCC"},
    {"ticker": "DGE.L", "name": "Diageo"},
    {"ticker": "DPLM.L", "name": "Diploma"},
    {"ticker": "EDV.L", "name": "Endeavour Mining"},
    {"ticker": "ENT.L", "name": "Entain"},
    {"ticker": "EXPN.L", "name": "Experian"},
    {"ticker": "FCIT.L", "name": "F&C Investment Trust"},
    {"ticker": "FRES.L", "name": "Fresnillo"},
    {"ticker": "GAW.L", "name": "Games Workshop"},
    {"ticker": "GLEN.L", "name": "Glencore"},
    {"ticker": "GSK.L", "name": "GSK"},
    {"ticker": "HLN.L", "name": "Haleon"},
    {"ticker": "HLMA.L", "name": "Halma"},
    {"ticker": "HSX.L", "name": "Hiscox"},
    {"ticker": "HWDN.L", "name": "Howden Joinery Group"},
    {"ticker": "HSBA.L", "name": "HSBC Holdings"},
    {"ticker": "ICG.L", "name": "ICG"},
    {"ticker": "IGG.L", "name": "IG Group"},
    {"ticker": "IHG.L", "name": "IHG Hotels & Resorts"},
    {"ticker": "IMI.L", "name": "IMI"},
    {"ticker": "IMB.L", "name": "Imperial Brands"},
    {"ticker": "INF.L", "name": "Informa"},
    {"ticker": "IAG.L", "name": "International Airlines Group"},
    {"ticker": "ITRK.L", "name": "Intertek Group"},
    {"ticker": "INVP.L", "name": "Investec"},
    {"ticker": "JD.L", "name": "JD Sports Fashion"},
    {"ticker": "KGF.L", "name": "Kingfisher"},
    {"ticker": "LAND.L", "name": "Land Securities Group"},
    {"ticker": "LGEN.L", "name": "Legal & General"},
    {"ticker": "BGEO.L", "name": "Lion Finance Group"},
    {"ticker": "LLOY.L", "name": "Lloyds Banking Group"},
    {"ticker": "LSEG.L", "name": "London Stock Exchange Group"},
    {"ticker": "LMP.L", "name": "LondonMetric Property"},
    {"ticker": "MNG.L", "name": "M&G"},
    {"ticker": "MKS.L", "name": "Marks & Spencer Group"},
    {"ticker": "MRO.L", "name": "Melrose Industries"},
    {"ticker": "MTLN.L", "name": "Metlen Energy & Metals"},
    {"ticker": "NG.L", "name": "National Grid"},
    {"ticker": "NWG.L", "name": "NatWest Group"},
    {"ticker": "NXT.L", "name": "Next"},
    {"ticker": "PSON.L", "name": "Pearson"},
    {"ticker": "PSH.L", "name": "Pershing Square Holdings"},
    {"ticker": "PSN.L", "name": "Persimmon"},
    {"ticker": "PCT.L", "name": "Polar Capital Technology Trust"},
    {"ticker": "PRU.L", "name": "Prudential"},
    {"ticker": "RKT.L", "name": "Reckitt"},
    {"ticker": "REL.L", "name": "RELX"},
    {"ticker": "RTO.L", "name": "Rentokil Initial"},
    {"ticker": "RIO.L", "name": "Rio Tinto"},
    {"ticker": "RR.L", "name": "Rolls-Royce Holdings"},
    {"ticker": "SGE.L", "name": "Sage Group"},
    {"ticker": "SBRY.L", "name": "Sainsbury's"},
    {"ticker": "SDR.L", "name": "Schroders"},
    {"ticker": "SMT.L", "name": "Scottish Mortgage Investment Trust"},
    {"ticker": "SGRO.L", "name": "Segro"},
    {"ticker": "SVT.L", "name": "Severn Trent"},
    {"ticker": "SHEL.L", "name": "Shell plc"},
    {"ticker": "SN.L", "name": "Smith & Nephew"},
    {"ticker": "SMIN.L", "name": "Smiths Group"},
    {"ticker": "SPX.L", "name": "Spirax Group"},
    {"ticker": "SSE.L", "name": "SSE"},
    {"ticker": "STJ.L", "name": "St. James's Place"},
    {"ticker": "STAN.L", "name": "Standard Chartered"},
    {"ticker": "SDLF.L", "name": "Standard Life"},
    {"ticker": "TSCO.L", "name": "Tesco"},
    {"ticker": "BBOX.L", "name": "Tritax Big Box REIT"},
    {"ticker": "ULVR.L", "name": "Unilever"},
    {"ticker": "UU.L", "name": "United Utilities"},
    {"ticker": "VOD.L", "name": "Vodafone Group"},
    {"ticker": "WEIR.L", "name": "Weir Group"},
    {"ticker": "WTB.L", "name": "Whitbread"},
]
```

Compte : **99 entreprises** (100 constituants réels du FTSE 100 moins
Coca-Cola Europacific Partners, déjà suivie côté NASDAQ_COMPANIES — le
FTSE est donc à 99/100 par ce choix, pas par erreur, même logique que
DAX 39/40 et Dow 21/30).

Notes de nommage : `HSBA.L`→"HSBC Holdings" et `SHEL.L`→"Shell plc"
reprennent exactement la convention donnée dans la consigne. "Standard
Life" (SDLF.L) est l'ex-Phoenix Group Holdings (renommé le 02/03/2026,
voir §4). "ICG" = Intermediate Capital Group. "Lion Finance Group"
(BGEO.L) = ex-Bank of Georgia Group (renommé en 02/2024). "Aberdeen
Group" (ABDN.L) = ex-abrdn plc / ex-Standard Life Aberdeen (renommé le
13/03/2025 — sans lien capitalistique avec "Standard Life" plc actuel,
malgré le nom historique partagé, à ne pas confondre).

## 3. Chevauchements avec les indices déjà suivis

**Un seul chevauchement trouvé : Coca-Cola Europacific Partners.**

- Nom : Coca-Cola Europacific Partners plc
- Déjà suivie dans : `NASDAQ_COMPANIES` (`indices_score.py`, ligne ~228),
  ticker actuel `"CCEP"` (sans suffixe, ticker US/Nasdaq), sans
  `also_indices`.
- Constituant FTSE 100 confirmé (sortie du tableau Wikipédia : "Coca-Cola
  Europacific Partners | CCEP | Beverages") — société domiciliée et cotée
  à titre principal à Londres depuis sa fusion 2023 (Coca-Cola European
  Partners + Coca-Cola Amatil), tout en gardant une cotation secondaire
  Nasdaq et Euronext Amsterdam sous le même ticker CCEP.
- Action recommandée (à faire par quelqu'un d'autre, pas par cette
  recherche) : ajouter `"also_indices": ["FTSE"]` à l'entrée CCEP dans
  `NASDAQ_COMPANIES`, ne pas dupliquer dans `FTSE_COMPANIES`.

**Aucun autre chevauchement.** Vérifié nom par nom contre `CAC40_COMPANIES`,
`DAX_COMPANIES`, `NASDAQ_COMPANIES` et `DOW_COMPANIES` — pas d'autre
société du FTSE 100 qui corresponde à une entreprise déjà suivie ailleurs
(ex. Ferrovial et Linde plc, présentes dans NASDAQ_COMPANIES, ne sont pas
cotées à Londres et ne sont pas des constituants FTSE 100 ; Airbus n'est
pas un constituant FTSE 100).

## 4. Secteur financier (banques / assureurs / réassureurs)

Définition du projet rappelée : uniquement banques de dépôt/crédit et
assureurs/réassureurs qui souscrivent du risque — PAS les gestionnaires
d'actifs, sociétés d'investissement (trusts), opérateurs de marché ou
courtiers/plateformes.

**Confirmations demandées explicitement dans la consigne :**

| Société (seed list) | Constituant FTSE 100 actuel ? | Statut |
|---|---|---|
| HSBC Holdings | Oui | Financier — banque (voir tableau ci-dessous) |
| Barclays | Oui | Financier — banque |
| Lloyds Banking Group | Oui | Financier — banque |
| NatWest Group | Oui | Financier — banque |
| Standard Chartered | Oui | Financier — banque |
| Prudential plc | Oui | Financier — assureur vie |
| Legal & General | Oui | Financier — assureur vie |
| Aviva | Oui | Financier — assureur composite |
| Phoenix Group Holdings | Renommée **Standard Life plc** (ticker SDLF, effectif 02/03/2026) | Financier — assureur vie/retraite, toujours constituant sous le nouveau nom |
| M&G plc | Oui | Financier — voir note ci-dessous (hybride assurance vie/gestion d'actifs) |
| Admiral Group | Oui | Financier — assureur (auto/habitation) |
| Beazley | Oui | Financier — (ré)assureur spécialisé Lloyd's |
| Hiscox | Oui | Financier — assureur spécialisé Lloyd's/Bermudes |

Les trois assureurs du marché de Lloyd's (Beazley, Hiscox, et dans une
moindre mesure Admiral qui n'est pas un souscripteur Lloyd's mais un
assureur retail classique) sont bien tous des constituants FTSE 100
**actuels** au 19/06/2026 — confirmé sur le tableau Wikipédia à jour.

**Liste finale des tickers financiers (15) avec raison :**

- `HSBA.L` — HSBC Holdings : banque de réseau + banque d'investissement
- `BARC.L` — Barclays : banque de réseau + banque d'investissement
- `LLOY.L` — Lloyds Banking Group : banque de réseau (UK pure-play)
- `NWG.L` — NatWest Group : banque de réseau (ex-Royal Bank of Scotland)
- `STAN.L` — Standard Chartered : banque internationale marchés émergents
- `INVP.L` — Investec : banque spécialisée (private banking/banque
  d'affaires), structure dual-listed Londres/Johannesburg
- `BGEO.L` — Lion Finance Group : groupe bancaire (ex-Bank of Georgia
  Group, banque de réseau Géorgie/Arménie/Biélorussie)
- `PRU.L` — Prudential : assureur vie (recentré Asie/Afrique depuis la
  scission de M&G en 2019)
- `LGEN.L` — Legal & General : assureur vie + retraite/rentes
- `AV.L` — Aviva : assureur composite (vie + non-vie + épargne)
- `MNG.L` — M&G : hybride assureur vie (rentes, with-profits, retraite —
  segment "Life" hérité de l'ex-Prudential UK) + gestionnaire d'actifs ;
  inclus comme financier du fait du poids réel du bilan d'assurance-vie,
  mais à surveiller si yfinance ne fournit pas les bons agrégats
  (comparable au traitement DAX de Munich Re/Allianz)
- `SDLF.L` — Standard Life (ex-Phoenix Group Holdings) : consolidateur
  d'assurance vie et de retraites (livres fermés)
- `ADM.L` — Admiral Group : assureur (auto/habitation/animalier), pas un
  Lloyd's mais un souscripteur retail direct
- `BEZ.L` — Beazley : (ré)assureur spécialisé du marché de Lloyd's
  (cyber, marine, spécialités)
- `HSX.L` — Hiscox : assureur spécialisé Lloyd's/Bermudes (biens de
  valeur, spécialités, réassurance)

**Cas limite signalé, à trancher par un humain plutôt que tranché ici :**

- `STJ.L` — St. James's Place : conseil en gestion de patrimoine dont les
  produits sont juridiquement enveloppés dans des polices d'assurance
  vie réglementées (St. James's Place UK plc est agréée par la
  Prudential Regulation Authority comme assureur), mais le métier
  économique est la gestion de patrimoine/conseil, pas la souscription
  de risque d'assurance classique. Je ne l'ai PAS mise dans la liste
  financière ci-dessus — à confirmer/infirmer manuellement si le
  méthodologie adaptée (ROE, levier, P/E+P/B) est jugée plus pertinente
  que la grille standard pour cette valeur.

**Explicitement exclues du traitement financier** (gestion d'actifs,
trusts d'investissement, courtier, opérateur de marché — pas des
banques/assureurs au sens du projet, même si Wikipédia les étiquette
"Financial services") :

- `III.L` 3i Group — private equity / société d'investissement
- `ABDN.L` Aberdeen Group — gestionnaire d'actifs (ex-abrdn/Standard Life
  Aberdeen)
- `ALW.L` Alliance Witan, `FCIT.L` F&C Investment Trust, `SMT.L` Scottish
  Mortgage Investment Trust, `PCT.L` Polar Capital Technology Trust,
  `PSH.L` Pershing Square Holdings — trusts d'investissement fermés
- `ICG.L` ICG (Intermediate Capital Group) — gestionnaire de dette
  privée
- `IGG.L` IG Group — courtier/plateforme de trading (spread betting/CFD),
  pas une banque de dépôt ni un assureur (même logique que PayPal/Visa
  exclus côté Nasdaq)
- `LSEG.L` London Stock Exchange Group — opérateur de marché/données,
  même traitement que Deutsche Börse (DAX) et Euronext (CAC40) : standard
  malgré un profil de bilan atypique
- `SDR.L` Schroders — gestionnaire d'actifs

## 5. Tickers incertains — à vérifier en production

Comme pour NASDAQ_COMPANIES, aucun ticker n'a pu être vérifié via un
appel yfinance réel dans ce sandbox (réseau bloqué). Confiance différenciée
ci-dessous :

**Vérifiés individuellement via recherche ciblée (Yahoo Finance) — haute
confiance :**
BT-A.L (BT Group — tiret, PAS un point : Yahoo n'accepte pas un second
point dans le ticker, donc "BT.A" devient "BT-A.L", à ne pas confondre
avec le code EPIC LSE natif), AV.L (Aviva), BA.L (BAE Systems), BP.L
(BP), RR.L (Rolls-Royce Holdings), ADM.L (Admiral Group — distinct du
ADM américain d'Archer-Daniels-Midland, qui n'a pas de suffixe .L),
ABDN.L (Aberdeen Group), MTLN.L (Metlen Energy & Metals), BGEO.L (Lion
Finance Group).

**Haute confiance par motif (mais pas vérifiés un par un cette session)** —
tickers EPIC à 2 lettres qui portent un point de complément côté LSE
(ex. code natif "NG." affiché "NG..L" par certaines sources) : d'après le
motif confirmé sur AV./BA./BP./RR. ci-dessus, Yahoo Finance élimine
systématiquement ce point de complément plutôt que de le dupliquer :
- `NG.L` National Grid
- `SN.L` Smith & Nephew
- `UU.L` United Utilities
- `JD.L` JD Sports Fashion

**Non vérifiés individuellement cette session (tickers standards,
sociétés cotées à Londres depuis longtemps, format `XXXX.L` classique
sans particularité connue)** — à confirmer au premier run réel comme le
reste du Nasdaq-100 : BAB.L (Babcock International), BRBY.L (Burberry
Group), CCC.L (Computacenter), PCT.L (Polar Capital Technology Trust),
IGG.L (IG Group), et plus généralement toute entrée de la liste dont le
ticker fait 3-4 lettres et suit le motif standard.

**Cas particulier à noter, pas un risque de ticker mais un risque de
confusion de nom** : "Aberdeen Group plc" (ABDN.L, gestionnaire d'actifs)
et "Standard Life plc" (SDLF.L, ex-Phoenix Group, assureur) ont un passé
commun (fusion 2017 "Standard Life Aberdeen") mais sont aujourd'hui deux
sociétés cotées totalement distinctes et sans lien capitalistique — ne
pas les fusionner ni les confondre lors de l'implémentation.

## 6. Exclusions volontaires (constituants historiques, plus dans l'indice)

Pour mémoire (différence entre la version topforeignstocks du 07/12/2025
et la version Wikipédia du 19/06/2026, entièrement expliquée par les
rotations trimestrielles normales de l'indice — pas une anomalie) :
Ashtead Group, Berkeley Group Holdings, Easyjet, Hargreaves Lansdown
(rachat/retrait de cote 2025), Hikma Pharmaceuticals, Mondi, Rightmove,
DS Smith (racheté par International Paper en 2025), Taylor Wimpey, Unite
Group, WPP — tous sortis de l'indice entre les deux dates et donc
volontairement absents de la liste finale.
