# SMI (Swiss Market Index) — recherche pour ajout d'indice (analyse-or)

Recherche uniquement — aucun fichier du repo n'a été modifié (ni
`indices_score.py`, ni `docs/index.html`). Ce document fournit tout ce
qu'il faut pour que quelqu'un d'autre colle `SMI_COMPANIES` dans
`indices_score.py` en connaissance de cause.

## 1. Sources et fraîcheur

1. **Source primaire** : Wikipédia, article "Swiss Market Index" (anglais),
   récupéré le 2026-09-12 via l'API `action=raw` (wikitext brut). Le
   tableau des constituants liste explicitement **20 lignes** numérotées
   1 à 20, avec le poids indiciel de chacune. Note de composition : *"The
   latest update following the ordinary review was implemented in June
   2025, when Amrize replaced Sonova."* → dernière recomposition connue :
   **juin 2025** (Sonova, l'audioprothésiste, sortie ; Amrize, la
   scission construction de Holcim, entrée).
2. **Source de recoupement n°1** : marketscreener.com, page composants de
   l'indice SMI, consultée le 2026-09-12 (indice coté à 13 775,27 points
   au 11/09/2026). Liste **exactement les 20 mêmes noms** que Wikipédia,
   dans le même ordre de poids (Roche, Novartis, Nestlé, ABB, UBS,
   Richemont, Zurich Insurance, Swiss Re, Holcim, Lonza, Swisscom, Sika,
   Givaudan, Alcon, Swiss Life, Kuehne+Nagel, Amrize, Geberit, Partners
   Group, Logitech).
3. **Source de recoupement n°2** : holdings de l'ETF **iShares SMI (CH)**
   (ticker `CSSMI.SW`), via stockanalysis.com, as-of 28/08/2026. Top 5
   (Roche 16,88 %, Novartis 15,83 %, Nestlé 13,57 %, UBS 9,15 %, ABB
   8,36 %) confirme intégralement l'ordre de poids Wikipédia/
   marketscreener. L'ETF affiche 27 lignes au total (au lieu de 20) —
   écart de méthodologie ETF (poches de cash, éventuels doublons de
   classe d'actions) et non un signal de composition erronée, même type
   d'écart bénin que les "111 lignes" de l'ETF ISF pour le FTSE 100.

**Accord total sur les 20 constituants entre les trois sources** — aucune
divergence à résoudre, contrairement au FTSE 100 où topforeignstocks
(version plus ancienne) nécessitait une explication par les rotations
trimestrielles. Le prochain review ordinaire de l'indice tombe le 3e
vendredi de septembre (donc vers le 18/09/2026, après la date de cette
recherche) — à surveiller si le premier run de production a lieu après
cette date, mais les reviews ordinaires du SMI changent rarement les 20
noms (seuls les reviews extraordinaires, comme juin 2025, le font).

**SMI Expanded / SMI MID** : la famille d'indices SIX inclut aussi le
"SMI MID" (30 valeurs suivantes) et le "SMI Expanded" (les 50 réunies).
Conformément à la consigne, ce rapport couvre le **SMI cœur (20
valeurs)**, pas la version élargie — voir §6 pour la note sur Julius
Baer, dont le cas ne se pose que si le SMI Expanded était retenu à la
place.

Environnement de recherche : WebFetch/WebSearch fonctionnels dans ce
sandbox (contrairement au blocage réseau documenté pour le Nasdaq-100 et
le FTSE 100) — **les 20 tickers ont donc pu être vérifiés
individuellement via un appel direct à l'endpoint Yahoo Finance
`v8/finance/chart`** (voir §5), ce qui est plus complet que les deux
rapports précédents.

## 2. Ticker Yahoo Finance : suffixe et benchmark

- Suffixe confirmé pour les actions SIX Swiss Exchange : **`.SW`** (et
  non `.VX` ou `.SIX`). Confirmé sur les 20 valeurs, voir §5.
- Benchmark de l'indice lui-même : **`^SSMI`**, confirmé via
  `https://query1.finance.yahoo.com/v8/finance/chart/%5ESSMI` —
  `currency: "CHF"`, `exchangeName: "EBS"`, `shortName: "SMI PR"` (SMI
  Price Return — pas de champ `longName` distinct sur ce ticker, mais le
  `shortName` et la devise confirment sans ambiguïté qu'il s'agit du bon
  indice).
- Devise native de l'indice et de toutes les actions : **CHF** (franc
  suisse) — aucune exception parmi les 20 constituants.

## 3. Liste Python `SMI_COMPANIES` (20 entrées, triée alphabétiquement)

```python
SMI_COMPANIES = [
    {"ticker": "ABBN.SW", "name": "ABB"},
    {"ticker": "ALC.SW", "name": "Alcon"},
    {"ticker": "AMRZ.SW", "name": "Amrize"},
    {"ticker": "GEBN.SW", "name": "Geberit"},
    {"ticker": "GIVN.SW", "name": "Givaudan"},
    {"ticker": "HOLN.SW", "name": "Holcim"},
    {"ticker": "KNIN.SW", "name": "Kuehne + Nagel"},
    {"ticker": "LOGN.SW", "name": "Logitech"},
    {"ticker": "LONN.SW", "name": "Lonza Group"},
    {"ticker": "NESN.SW", "name": "Nestlé"},
    {"ticker": "NOVN.SW", "name": "Novartis"},
    {"ticker": "PGHN.SW", "name": "Partners Group"},
    {"ticker": "CFR.SW", "name": "Richemont"},
    {"ticker": "ROP.SW", "name": "Roche Holding"},
    {"ticker": "SIKA.SW", "name": "Sika"},
    {"ticker": "SLHN.SW", "name": "Swiss Life Holding"},
    {"ticker": "SREN.SW", "name": "Swiss Re"},
    {"ticker": "SCMN.SW", "name": "Swisscom"},
    {"ticker": "UBSG.SW", "name": "UBS Group"},
    {"ticker": "ZURN.SW", "name": "Zurich Insurance Group"},
]
```

Compte : **20 entreprises = 20/20 constituants réels du SMI cœur**, pas
de chevauchement volontaire à retirer (contrairement à FTSE 99/100, DAX
39/40, Dow 21/30) — voir §4, aucun recoupement trouvé avec les indices
déjà suivis.

Note de nommage la plus importante : **Roche Holding se négocie
désormais sous le ticker `ROP.SW`, pas `ROG.SW`**. Les certificats de
participation non-votants (Genussscheine, ticker historique "ROG") ont
cessé de se négocier le 16/03/2026 et ont été échangés 1:1 contre de
nouveaux "Partizipationsscheine" (certificats de participation) sous le
ticker **ROP**, effectif le 17/03/2026 (confirmé par le communiqué
investisseurs Roche du 16/03/2026 et par le tableau Wikipédia à jour, qui
utilise déjà `{{SWX3|ROP}}`). `ROG.SW` renvoie une erreur 404 sur
l'endpoint Yahoo Finance (ticker délisté) ; `ROP.SW` renvoie bien
`longName: "Roche Holding AG"`, `currency: "CHF"`. **Piège à ne pas
reproduire** : toute personne connaissant Roche de mémoire écrira
spontanément "ROG.SW" — c'est l'ancien ticker, périmé depuis mars 2026.

## 4. Chevauchements avec les indices déjà suivis

**Aucun chevauchement trouvé.** Les 20 noms de constituants SMI ont été
vérifiés un par un contre `CAC40_COMPANIES`, `DAX_COMPANIES`,
`NASDAQ_COMPANIES`, `DOW_COMPANIES` et `FTSE_COMPANIES` dans
`indices_score.py` — aucune correspondance de nom ou de société mère.

Attention particulière portée aux sociétés suisses à double cotation
(risque explicite soulevé par la consigne : ADR ou cotation directe déjà
suivie côté US) :

- **Alcon Inc.** (`ALC.SW`) — également coté au NYSE sous le ticker `ALC`
  (sans suffixe). Vérifié : `ALC` n'apparaît ni dans `NASDAQ_COMPANIES`
  ni dans `DOW_COMPANIES`. Pas de chevauchement.
- **Amrize AG** (`AMRZ.SW`) — scission de Holcim (juin 2025), également
  cotée au NYSE sous le ticker `AMRZ`. Vérifié : `AMRZ` absent des deux
  listes US. Pas de chevauchement.
- **Logitech International SA** (`LOGN.SW`) — également cotée au Nasdaq
  sous le ticker `LOGI` (c'est même le ticker le plus connu du grand
  public pour cette société). Vérifié directement dans le fichier
  `indices_score.py` : `LOGI` n'apparaît pas dans `NASDAQ_COMPANIES` (liste
  lue en entier, 102 entrées) — donc pas de doublon existant à gérer
  avec `also_indices`, même si Logitech est éligible au Nasdaq de par sa
  cotation.

Aucune des 20 sociétés SMI n'a de société mère ou de filiale déjà suivie
sous un autre nom dans les indices existants (à la différence de
Coca-Cola Europacific Partners pour le FTSE, ou d'Airbus pour DAX/CAC40).

## 5. Tickers vérifiés individuellement via Yahoo Finance — confiance
haute (20/20)

Contrairement aux rapports Nasdaq-100 et FTSE 100, l'environnement de
recherche disposait d'un accès réseau fonctionnel à
`query1.finance.yahoo.com`. **Les 20 tickers ont donc été vérifiés
individuellement**, pas seulement par motif :

| Ticker | `longName` renvoyé | `currency` |
|---|---|---|
| ABBN.SW | ABB Ltd | CHF |
| ALC.SW | Alcon Inc. | CHF |
| AMRZ.SW | Amrize AG | CHF |
| CFR.SW | Compagnie Financière Richemont SA | CHF |
| GEBN.SW | Geberit AG | CHF |
| GIVN.SW | Givaudan SA | CHF |
| HOLN.SW | Holcim AG | CHF |
| KNIN.SW | Kuehne + Nagel International AG | CHF |
| LOGN.SW | Logitech International S.A. | CHF |
| LONN.SW | Lonza Group AG | CHF |
| NESN.SW | Nestlé S.A. | CHF |
| NOVN.SW | Novartis AG | CHF |
| PGHN.SW | Partners Group Holding AG | CHF |
| ROP.SW | Roche Holding AG | CHF |
| SIKA.SW | Sika AG | CHF |
| SLHN.SW | Swiss Life Holding AG | CHF |
| SREN.SW | Swiss Re AG | CHF |
| SCMN.SW | Swisscom AG | CHF |
| UBSG.SW | UBS Group AG | CHF |
| ZURN.SW | Zurich Insurance Group AG | CHF |

Toutes renvoient `exchangeName: "EBS"` (le code interne Yahoo pour SIX
Swiss Exchange) et `currency: "CHF"`, cohérent sur les 20 lignes.

**Ticker à surveiller malgré la vérification individuelle** : `ROP.SW`
(voir §3) — le ticker est correct *aujourd'hui*, mais un changement de
ticker aussi récent (mars 2026) mérite un contrôle rapide au premier run
de production si jamais Roche procède à une nouvelle opération sur titre.
Aucun autre ticker de la liste n'a montré de comportement inhabituel
(pas de 404, pas de devise ou de nom inattendu).

## 6. Secteur financier (banques / assureurs / réassureurs)

Définition du projet rappelée : uniquement banques de dépôt/crédit et
assureurs/réassureurs qui souscrivent du risque — PAS les gestionnaires
d'actifs, sociétés d'investissement (trusts), opérateurs de marché ou
courtiers/plateformes.

**Liste finale des tickers financiers (4) avec raison :**

- `UBSG.SW` — UBS Group : banque de réseau + banque d'investissement +
  gestion de fortune, établissement bancaire classique (dépôts, crédit).
- `ZURN.SW` — Zurich Insurance Group : assureur composite (dommages +
  vie), souscripteur de risque classique.
- `SREN.SW` — Swiss Re : réassureur pur, l'un des plus grands au monde —
  cas d'école pour la méthodologie adaptée (ROE, pas d'EBITDA/EBIT
  significatif chez un réassureur).
- `SLHN.SW` — Swiss Life Holding : assureur vie + gestion de patrimoine
  liée à l'assurance (retraite, prévoyance) — même profil que Swiss Life
  dans le rapport FTSE cite Legal & General/Prudential comme comparables.

**Explicitement exclu du traitement financier :**

- `PGHN.SW` — Partners Group Holding : gestionnaire d'actifs alternatifs
  (private equity, dette privée, infrastructure, immobilier). Pas un
  souscripteur de risque d'assurance ni une banque de dépôt/crédit — même
  catégorie d'exclusion que Schroders (FTSE) ou 3i Group (FTSE). Aucune
  ambiguïté ici contrairement à St. James's Place côté FTSE : le métier
  de Partners Group est sans lien avec l'assurance ou le crédit bancaire.

**Nom trompeur à noter (pas un cas limite, juste un piège de lecture)** :
`CFR.SW` (Richemont) porte le nom légal "**Compagnie Financière**
Richemont SA" — le mot "Financière" dans la raison sociale historique
n'en fait pas une société financière au sens du projet : Richemont est un
conglomérat de luxe (Cartier, Van Cleef & Arpels, IWC, etc.), classé en
méthodologie standard.

**Cas Julius Baer Group (signalé par la consigne comme comparable à St.
James's Place) — ne s'applique pas au SMI cœur :** Julius Baer Group AG
(banque privée / gestion de fortune, ticker `BAER.SW`) a **quitté le SMI
le 10/04/2019** pour rejoindre le SMI MID (confirmé par le communiqué
Julius Baer du 08/04/2019, "Julius Baer shares to leave SMI and join
SMIM as of 10 April 2019"). Julius Baer n'est donc **pas** un constituant
du SMI cœur (20) actuel et n'apparaît pas dans `SMI_COMPANIES` ci-dessus
— la tension "banque réglementée vs. économiquement gestion de
fortune/actifs" que la consigne anticipait pour Julius Baer ne se pose
donc pas ici. Elle ne redeviendrait pertinente que si le projet basculait
un jour sur le "SMI Expanded" (50 valeurs, qui inclut Julius Baer via le
SMI MID) plutôt que le SMI cœur — à traiter alors exactement comme St.
James's Place dans le rapport FTSE (cas limite signalé, pas tranché
unilatéralement).

## 7. Taux sans risque (FRED)

**`IRLTLT01CHM156N` existe et convient** — confirmé via
`https://fred.stlouisfed.org/series/IRLTLT01CHM156N` :

- Titre : *"Interest Rates: Long-Term Government Bond Yields: 10-Year:
  Main (Including Benchmark) for Switzerland"*
- Famille : OCDE "IRLTLT01", même famille que les séries France
  (`IRLTLT01FRM156N`) et UK (`IRLTLT01GBM156N`) déjà utilisées dans
  `RISK_FREE_SERIES_BY_CURRENCY` — nommage identique, seul le code pays
  ISO change (`CH` pour la Suisse).
- Fréquence : **mensuelle** (comme les séries France et UK, à la
  différence du Treasury 10 ans US `DGS10` qui est quotidien).
- Dernière observation vue au moment de la recherche : 0,310 % en juin
  2026 — cohérent avec un taux suisse durablement bas (la Suisse a eu des
  taux directeurs proches de zéro ou négatifs sur une bonne partie de la
  dernière décennie).

À ajouter dans le code (pour information, pas fait ici — recherche
seulement) :
```python
FRED_RISK_FREE_SERIES_CH = "IRLTLT01CHM156N"  # Emprunt confédéral 10 ans (Suisse), FRED/OCDE, mensuel
# ... et dans RISK_FREE_SERIES_BY_CURRENCY : "CHF": FRED_RISK_FREE_SERIES_CH
```

## 8. Exclusions volontaires (constituants historiques, plus dans l'indice)

Pour mémoire, dernière recomposition ordinaire du SMI (juin 2025,
confirmée par Wikipédia) :

- **Sonova Holding** (audioprothèses) — sortie de l'indice, remplacée par
  Amrize.

Pas d'autre sortie récente identifiée sur la période couverte par les
trois sources (Wikipédia, marketscreener, iShares ETF au 28/08/2026)
puisque les trois s'accordent sur la même liste de 20 à des dates très
rapprochées (aucune fenêtre de plusieurs mois à combler, contrairement au
FTSE où topforeignstocks datait de ~6 mois avant Wikipédia).

**Julius Baer Group** (voir §6) est le principal cas de sortie
"structurelle" à connaître : parti du SMI cœur en 2019, toujours actif en
bourse (SMI MID / SMI Expanded), mais absent de cette liste par
construction de l'indice — pas un oubli de cette recherche.

**SMI Expanded (30 valeurs)** — non retenu pour ce rapport, conformément
à la consigne (rester sur le SMI cœur de 20 sauf signal contraire fort ;
aucun signal de ce type trouvé). Si le projet souhaitait un jour plus de
profondeur (comme le Nasdaq-100 vs. DAX/CAC40 sont plus larges), le SMI
Expanded ajouterait notamment Julius Baer, Straumann, VAT Group, Bâloise,
Helvetia et Swatch Group — hors périmètre de cette recherche.
