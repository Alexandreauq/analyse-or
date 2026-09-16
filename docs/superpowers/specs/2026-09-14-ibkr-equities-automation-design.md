# Automatisation de l'investissement réel sur les signaux "entrée" (actions, IBKR) — design

## Statut

**Plan A ("le cerveau" : state.py, signals.py, sizing.py, contracts.py,
gateway.py, portfolio.py) implémenté, revu et fusionné dans `main` le
2026-09-15** — structurellement incapable de passer un ordre réel (garde-fou
vérifié à la revue de branche), toujours en dry-run. Sections 4.3/4.6/4.7,
initialement marquées **[Proposé — non validé]**, sont validées par
l'implémentation réelle (elle les a suivies telles quelles, revues à
plusieurs reprises). Section 9.9 a été révisée le 2026-09-15 (garde-fou de
solde en devise de base, pas par devise — voir 9.9 pour le détail). Section
4.5 : la source des données pour Plan B est tranchée (git pull sur le clone
local, pas un fetch HTTPS séparé).

**Reste à faire : Plan B** (orchestrateur `daily.py`, `journal.py`,
`notify.py`, déploiement VPS — sections 4.5/5.5/7/8, toujours
**[Proposé — non validé]** sauf indication contraire ci-dessus) — à régler
normalement lors de l'écriture de son plan d'implémentation, pas
individuellement avec l'utilisateur, sauf point structurant nouveau. Reste
aussi une vérification manuelle non bloquante (coûts de données de marché,
9.2) à faire avant le lancement en réel, pas avant le plan Plan B.

Ce document est le compte rendu structuré d'une session de brainstorming
menée avec l'utilisateur : tous les paramètres de la section « Paramètres
de trading » et de la section « Périmètre v1 » ont été décidés
explicitement par lui. Les points marqués **[Proposé — non validé]**
ailleurs dans le document sont des propositions techniques de détail
d'implémentation (structure de fichiers, résolution des contrats,
stratégie de test...), jamais individuellement discutées avec
l'utilisateur — elles se règlent normalement lors de l'écriture du plan
d'implémentation, à la différence des 9 points structurants de la
section 9, tous explicitement arbitrés.

## 1. Contexte et objectif

Le module Indices score fondamentalement ~710 entreprises réparties sur
10 indices boursiers (`indices_score.py` → `docs/indices.json`, exécuté
une fois par jour par `.github/workflows/indices.yml`). Quand les
conditions d'entrée d'une entreprise sont réunies, une alerte de type
`entree` apparaît.

Depuis le 2026-09-09, ces signaux font l'objet d'un **paper-trading**
automatique : `update_signal_tracking()` ouvre une position
hypothétique par nouveau signal, la suit, et la clôture selon des
règles fixes (voir `docs/signal_tracking.json` et
`docs/superpowers/specs/2026-09-08-signal-performance-tracking-design.md`).
Ce dispositif mesure la qualité du signal, sans argent.

**L'objectif de ce chantier est de passer à l'argent réel** : acheter
réellement, chez Interactive Brokers, les actions dont le signal
« entrée » se déclenche, et les revendre selon **exactement les mêmes
règles de sortie** que le paper-trading. L'exigence structurante qui en
découle, et qui contraint plusieurs choix ci-dessous : **la performance
réelle doit rester directement comparable à la performance observée en
paper-trading**. Toute divergence de logique (fréquence d'évaluation,
règle de sortie, source de prix de décision) casserait cette
comparabilité et doit donc être justifiée explicitement.

Ce chantier est un **nouveau module**, indépendant :

- Il ne modifie pas `indices_score.py` ni le paper-trading, qui
  continuent de tourner exactement comme aujourd'hui.
- Il n'écrit jamais dans `docs/signal_tracking.json` (voir
  « Journalisation »).
- Il est distinct du bot Or (`gold_bot/`, MetaApi/MT5, boucle continue
  sur le VPS Hetzner) dont il reprend en revanche les conventions de
  code, de déploiement et de garde-fous — voir
  `docs/superpowers/specs/2026-09-10-bot-trading-or-design.md`.
- Il est aussi distinct de la synchronisation IBKR en lecture seule déjà
  en production (`portfolio_sync.py`, Flex Web Service, jeton
  structurellement incapable de passer un ordre — voir
  `docs/superpowers/specs/2026-09-10-portefeuille-ibkr-sync-design.md`).
  Le compte IBKR existe donc déjà et est déjà interrogé en lecture ;
  ce chantier y ajoute, pour la première fois, un chemin d'écriture.

## 2. Périmètre v1

### Dans le périmètre

- **8 indices** : CAC40, DAX, NASDAQ, DOW, FTSE, SMI, IBEX35, FTSEMIB.
- **4 devises** : EUR (CAC40, DAX, IBEX35, FTSEMIB), USD (NASDAQ, DOW),
  GBP (FTSE), CHF (SMI).
- Achat réel à l'ouverture d'un nouveau signal « entrée », vente réelle
  selon les règles de sortie du paper-trading.
- Un job **batch quotidien** sur le VPS Hetzner existant (178.105.186.220),
  celui qui héberge déjà `gold_bot`.
- Un journal d'exécution réel **séparé** du paper-trading.

### Explicitement hors périmètre

- **Nikkei 225 et Hang Seng** : exclus de la v1. JPY et HKD ajoutent une
  complexité de change et d'horaires de marché (session asiatique,
  décalage horaire complet avec le batch quotidien) jugée prématurée
  pour un démarrage. Ces deux indices continuent d'être scorés et
  paper-tradés comme aujourd'hui — seul le passage au réel les exclut.
- **Couverture de change (hedging FX)** : le spread de change est accepté
  comme un coût. Aucun instrument de couverture, aucune gestion de
  l'exposition devise. Sujet à traiter séparément si le besoin se
  confirme un jour.
- **Actions fractionnées** : jamais. Le support IBKR des fractions hors
  actions américaines est incertain, et la v1 ne dépend d'aucune
  hypothèse invérifiée à ce sujet.
- **Mode semi-automatique** : pas de confirmation manuelle par trade. Le
  bot ouvre et ferme seul, même philosophie que `gold_bot`. (Un
  interrupteur d'urgence global existe — voir section 5.2 — mais il coupe
  le bot, il ne valide pas les trades un par un.)
- **Effet de levier / compte sur marge** : compte cash uniquement.
- **Toute autre source de signal** que l'alerte `entree` produite par
  `indices_score.py` (pas de signal de vente à découvert, pas de
  renforcement de position, pas de moyenne à la baisse).
- **Modification du site public** : la v1 ne prévoit pas d'affichage web
  du bot actions. (Un bloc analogue au tableau de bord du bot Or est
  envisageable plus tard, hors périmètre ici.)

## 3. Paramètres de trading (décidés par l'utilisateur)

### 3.1 Taille de position : 500 € fixes

Chaque position vaut **500 € de budget nominal**, pas un pourcentage du
capital — contrairement à `gold_bot`, qui dimensionne à 5 % du solde.

Raison explicite : le volume de signaux « entrée » arrive **en rafales
imprévisibles**. L'ajout des seuls Nikkei et Hang Seng a produit 22
signaux « entrée » simultanés dès leur premier run. Un pourcentage du
capital ferait donc varier la taille de position d'un facteur énorme
selon le nombre de signaux du jour, ce qui est exactement le contraire
de ce qu'on veut : un montant par idée, stable et comparable d'un jour
à l'autre.

### 3.2 Conversion de devise

Les 500 € sont convertis en équivalent devise locale **au moment de
l'achat**, via la gestion multi-devises native d'IBKR. Le spread de
change est un coût accepté. Pas de couverture.

Exemple : signal sur une action cotée en CHF → 500 € convertis au taux
EUR/CHF du moment → budget en CHF → arrondi (voir ci-dessous).

**Mécanisme confirmé (vérifié le 2026-09-14, voir 9.1)** : IBKR route les
conversions sous 25 000 $ via **IDEAL** (distinct d'IDEALPRO, réservé aux
montants au-delà de ce seuil) — conversion automatique à un spread de
0,03 %, ou FX manuel avec une commission minimale de l'ordre de 2 $. Les
conversions de ~500 € sont donc normalement traitées, à un coût marginal
(quelques centimes à ~2 $ par conversion), pas refusées. Le périmètre à
8 indices / 4 devises (section 2) est confirmé sur cette base.

### 3.3 Arrondi aux actions entières

Nombre d'actions achetées = **partie entière inférieure** de
`budget_en_devise_locale / prix_unitaire`. Le reliquat **reste en cash** :
il n'est ni réinvesti ailleurs le même jour, ni reporté sur un futur
signal.

Conséquences assumées :

- Une action chère ne fait **jamais** sauter un signal : on en achète
  simplement moins.
- **Cas limite explicite** : si le prix d'une action dépasse le budget
  converti, le calcul donne **0 action**. Dans ce cas, **aucun ordre
  n'est passé du tout** — la position ne s'ouvre pas. L'événement est
  journalisé comme `signal_ignore_prix_unitaire_superieur_au_budget`,
  avec le ticker, le prix unitaire et le budget converti. Ce n'est pas
  une erreur : c'est un résultat normal, qui doit être visible dans le
  résumé quotidien, pas silencieux. La place ainsi libérée sous le
  plafond de 10 positions **reste disponible** pour le signal suivant du
  classement (voir 3.5).

### 3.4 Plafond : 10 positions simultanées

Au plus **10 positions ouvertes en même temps**, tous indices confondus,
soit au plus **5 000 €** déployés simultanément. Budget total déclaré
disponible sur le compte : **3 000 à 10 000 €**.

Le plafond porte sur le **nombre de positions ouvertes par ce bot**, pas
sur le nombre d'actions ni sur les positions préexistantes du compte que
l'utilisateur aurait ouvertes lui-même. *(Voir 9.5 : la v1 propose de
n'agir que sur les positions que le bot a lui-même ouvertes.)*

### 3.5 Sursouscription (plus de signaux que de places libres)

Un jour donné, si le nombre de nouveaux signaux « entrée » dépasse le
nombre de places libres sous le plafond de 10 :

1. Les nouveaux signaux du jour sont **classés par score composite
   décroissant** (le score -100/+100 déjà calculé par `indices_score.py`,
   champ `score` de chaque entreprise dans `docs/indices.json`).
2. Les places libres sont remplies dans cet ordre.
3. Les signaux qui ne rentrent pas sont **perdus**, pas mis en file
   d'attente pour le lendemain. Raison : le prix d'entrée doit être celui
   du jour du signal ; un achat différé serait un trade différent, non
   comparable au paper-trading.

Un signal perdu par sursouscription est journalisé comme
`signal_ignore_plafond_atteint` (ticker, score, rang) — même exigence de
visibilité que le cas 0 action.

### 3.6 Règles de sortie : identiques au paper-trading

Reproduction fidèle de `_close_eligible_positions()` dans
`indices_score.py`. **Ordre de priorité strict**, première condition
remplie gagne :

| Priorité | Condition | Constante / source | Raison de clôture |
|---|---|---|---|
| 1 | `prix_courant <= prix_entree * (1 - 0,20)` | `SIGNAL_STOP_LOSS_PCT = -20.0` | `stop_loss` |
| 2 | `prix_courant >= target_exit_price` | `exit_price` de l'entreprise, **figé au jour du signal** | `objectif_atteint` |
| 3 | `aujourd_hui >= date_entree + 6 mois` | `SIGNAL_SHADOW_DELAY_MONTHS = 6` | `delai_max` |

Précisions reprises telles quelles du code existant :

- **`target_exit_price` est figé à l'ouverture**, pas recalculé chaque
  jour. Il vient de `company["exit_price"]`, lui-même produit par
  `estimate_entry_exit_prices()` : moyenne des repères disponibles entre
  un repère de valorisation (juste valeur + marge pondérée par le bêta)
  et un repère technique (MM200 + marge + ajustement de momentum). C'est
  un **niveau de prix absolu**, indépendant du prix d'entrée — il est donc
  repris verbatim du paper-trading, sans transposition.
- **Le stop-loss, lui, est relatif au prix d'entrée** : le bot réel
  utilise son **prix d'exécution réel** comme référence, pas le prix
  d'entrée du paper-trading. C'est une divergence assumée et faible
  (quelques dixièmes de pourcent), mais elle doit être documentée dans le
  journal pour que la comparaison paper/réel reste interprétable.
- **Aucune clôture sur donnée périmée** : si le ticker n'apparaît plus
  dans les données du jour, ou si son prix courant est manquant, la
  position est **laissée intacte** et réévaluée le lendemain. Jamais de
  vente déclenchée par une donnée absente.
- `SIGNAL_SHADOW_DELAY_MONTHS` sert dans le paper-trading à la fois de
  délai max et de date du benchmark « tenir 6 mois pleins ». **Le
  benchmark fantôme n'est pas reproduit dans le bot réel** : c'est un
  instrument de mesure du signal, pas une règle de trading. Il continue
  d'exister uniquement côté paper-trading.

### 3.7 Exécution

Entièrement autonome : ouverture **et** fermeture sans confirmation
manuelle par trade.

## 4. Architecture

### 4.1 Vue d'ensemble

```
06:00 UTC ─ GitHub Actions (indices.yml)
            └─> indices_score.py
                ├─> docs/indices.json          (scores, prix, alertes du jour)
                └─> docs/signal_tracking.json  (paper-trading, source des NOUVEAUX signaux)
                        │ commit + push sur main
                        ▼
14:45 UTC ─ VPS Hetzner : systemd timer ─> python -m ibkr_bot.daily
            │
            ├─ 1. préflight : Gateway IBKR authentifié ? données du jour fraîches ?
            ├─ 2. réconciliation : positions réelles du compte vs journal local
            ├─ 3. sorties : applique les règles 3.6 aux positions ouvertes par le bot
            ├─ 4. entrées : nouveaux signaux du jour -> classement -> plafond -> sizing
            ├─ 5. exécution des ordres via le Gateway
            └─ 6. journalisation + résumé quotidien par email
                        │
                        ▼
            ibkr_bot/real_trading_log.jsonl + ibkr_bot/positions.json
```

En continu, à côté : **Gateway IBKR** (service persistant, localhost) —
voir 4.4.

### 4.2 Pourquoi un batch quotidien et pas une boucle continue

`gold_bot` tourne en boucle (une évaluation par minute) parce que
MetaApi/MT5 est une API HTTPS quasi sans état : chaque appel est
autonome. L'API IBKR ne fonctionne pas comme ça — elle exige un
**Gateway persistant et authentifié** (TWS ou IBKR Client Portal Gateway),
avec ré-authentification périodique (historiquement ~24 h, avec 2FA).
Ce n'est pas un simple appel REST à la demande.

Mais la raison décisive est ailleurs, et elle est **méthodologique** : la
donnée de paper-tracking que ce système doit reproduire **n'a jamais été
évaluée plus d'une fois par jour**. `indices.yml` tourne une fois par
jour ; `update_signal_tracking()` ouvre et ferme sur les prix de ce run
unique. Une boucle intraday introduirait un comportement (sortie
déclenchée sur un pic intraday, par exemple) que les données de
paper-trading existantes n'ont jamais mesuré — et détruirait donc la
comparabilité qui justifie tout le chantier.

**Conclusion : la fréquence d'exécution de la logique de trading est
quotidienne. La persistance du Gateway est un sujet séparé** (un service
qui tourne en continu, mais que la logique métier n'interroge qu'une
fois par jour).

### 4.3 Structure de fichiers **[Validé par l'implémentation, Plan A]**

Miroir de `gold_bot/`, adapté à un batch plutôt qu'à une boucle :

```
ibkr_bot/
  __init__.py
  daily.py       # orchestrateur du batch quotidien (point d'entrée : python -m ibkr_bot.daily)
  signals.py     # lecture indices.json / signal_tracking.json, détection des nouveaux signaux, classement
  sizing.py      # conversion 500 € -> devise locale, arrondi entier, cas 0 action
  portfolio.py   # plafond de 10, règles de sortie 3.6, réconciliation
  gateway.py     # client HTTP du Gateway IBKR (seul module qui parle à IBKR)
  contracts.py   # résolution ticker yfinance -> contrat IBKR (conid), cache local
  state.py       # kill_switch / dry_run (copie conforme de gold_bot/state.py)
  journal.py     # écriture du journal réel et de l'état des positions
  notify.py      # résumé quotidien par email (même SMTP que gold_bot/notify.py)
```

Fichiers générés à l'exécution, **gitignorés** comme ceux de `gold_bot` :

```
ibkr_bot/state.json              # kill_switch, dry_run
ibkr_bot/positions.json          # positions ouvertes par le bot (état de travail)
ibkr_bot/real_trading_log.jsonl  # journal append-only de toutes les décisions et exécutions
ibkr_bot/conid_cache.json        # correspondances ticker -> conid IBKR déjà résolues
ibkr_bot/latest_account.json     # dernier instantané du compte (soldes par devise)
```

Deux principes de découpage repris de `gold_bot`, à conserver :

1. **Un seul module parle au courtier** (`gateway.py`). `portfolio.py`
   et `sizing.py` décident, ne passent aucun ordre, et sont donc
   testables sans réseau ni mock complexe.
2. **`daily.py` est le seul endroit où un ordre réel part**, et il
   revérifie lui-même `kill_switch`/`dry_run` avant chaque envoi, même si
   l'appelant l'a déjà fait (défense en profondeur — c'est exactement ce
   que fait `gold_bot/loop.py:execute_steps`).

### 4.4 Le Gateway IBKR

**Décidé (2026-09-14) : IBKR Client Portal Web API Gateway** (le paquet
Java fourni par IBKR), lancé en service systemd `ibkr-gateway.service`, à
l'écoute sur **127.0.0.1 uniquement** (jamais exposé sur Internet, pas
d'ouverture de port dans ufw). Le batch quotidien l'interroge en HTTP
local.

Choisi plutôt que TWS/IB Gateway + l'API socket (`ib_insync`) : pas
d'interface graphique à maintenir, footprint plus léger sur un petit VPS,
et API HTTP — donc testable avec exactement les mêmes conventions
`monkeypatch`/faux-objet-réponse que `tests/gold_bot/test_broker.py`.

**Ré-authentification** : c'est la vraie contrainte opérationnelle. La
session du Gateway expire (historiquement ~24 h) et sa réactivation peut
exiger une validation 2FA sur le téléphone de l'utilisateur. Il n'existe
pas de moyen propre de rendre cela entièrement automatique ; c'est une
servitude structurelle de l'API IBKR, pas un manque du design. Elle est
traitée comme telle en 5.5 (préflight, abandon propre, alerte email).

### 4.5 Articulation avec `indices.yml`

Le bot **ne recalcule jamais le score**. Il consomme le résultat déjà
publié.

**Source des données — tranché avec l'utilisateur le 2026-09-15** :
`ibkr_bot.daily` fait un `git pull` en début de batch sur le clone déjà
présent sur le VPS (`gold_bot` tourne depuis `/home/goldbot/analyse-or`)
et lit les fichiers de ce clone local. Réutilise l'infrastructure déjà en
place, pas de nouvelle dépendance réseau.

**Garde de fraîcheur, non négociable** : le batch vérifie que
`docs/indices.json`.`updated` vaut la date du jour. Si ce n'est pas le
cas (workflow en échec, `git pull` bloqué, push retardé), **aucun ordre
n'est passé ce jour-là** — ni entrée ni sortie. Le bot journalise
`donnees_perimees` et l'événement remonte dans le résumé quotidien.
Trader sur les scores d'hier reviendrait à ouvrir des positions sur des
signaux déjà consommés par le paper-trading.

**Horaire du batch : 14:45 UTC** (décidé, voir 9.6). Ce n'est
pas un détail cosmétique, c'est la seule fenêtre où les marchés des 8
indices sont ouverts **simultanément toute l'année** :

| Marchés | Été (UTC) | Hiver (UTC) |
|---|---|---|
| Europe (Euronext, Xetra, LSE, SIX, BME, Borsa Italiana) | 07:00–15:30 | 08:00–16:30 |
| États-Unis (NYSE, Nasdaq) | 13:30–20:00 | 14:30–21:00 |
| **Intersection** | **13:30–15:30** | **14:30–16:30** |

L'intersection commune aux deux régimes horaires est **14:30–15:30 UTC**.
14:45 UTC laisse une marge des deux côtés. Lancer le batch juste après
`indices.yml` (vers 07:00 UTC) ne marcherait pas : les marchés américains
seraient fermés, et les ordres soit resteraient en attente jusqu'à
l'ouverture (exécution à un prix imprévisible, plusieurs heures plus
tard), soit seraient rejetés.

**Conséquence assumée** : le prix d'exécution réel (14:45 UTC) diffère du
prix utilisé par le paper-trading (cours récupéré vers 06:00 UTC, en
pratique la clôture de la veille). Cet écart est une **source de
divergence structurelle entre réel et paper**, au même titre que le
slippage et les frais. Il doit être journalisé explicitement (prix
paper / prix réel / écart en %) pour que la comparaison reste lisible
plutôt que trompeuse.

### 4.6 Détection d'un NOUVEAU signal du jour **[Validé par l'implémentation, Plan A]**

Le problème : une alerte `entree` reste affichée plusieurs jours tant que
les conditions sont réunies. Il ne faut acheter qu'**une fois**, le jour
de son apparition.

Le code existant résout déjà ce problème deux fois :

- `_attach_alerts_and_update_history()` compare les alertes du jour à
  celles de la veille (`previous_alert_kinds`) et ne retient dans
  `newly_triggered_entree` que les entreprises dont `entree` **n'était
  pas** présent hier.
- `_open_new_signal_positions()` refuse en plus d'ouvrir une position sur
  un ticker qui en a déjà une `open`.

**Proposition : ne pas réimplémenter cette détection, mais la lire dans
`docs/signal_tracking.json`.** Les positions de ce fichier dont
`status == "open"` et `entry_date == <date du jour>` sont, par
construction, exactement l'ensemble des nouveaux signaux du jour, déjà
dédoublonnés par le paper-trading. Chaque position y porte déjà
`ticker`, `index`, `entry_price`, `target_exit_price` — c'est-à-dire tout
ce dont le bot réel a besoin, y compris le `target_exit_price` figé qu'il
doit reprendre verbatim.

Avantages : une seule implémentation de la détection, donc aucune
possibilité de divergence silencieuse entre paper et réel ; et le lien
paper↔réel devient traçable position par position (même `id`
`<ticker>-<date>`).

Limite à documenter : le paper-trading saute un signal si une position
paper est déjà ouverte sur ce ticker. Le bot réel, lui, peut ne pas avoir
pris ce signal (plafond de 10 atteint ce jour-là, ou 0 action achetable).
Les deux ensembles de positions ouvertes **divergent donc légitimement**.
La détection des *nouveaux signaux* vient du paper ; l'état des
*positions réelles* vient exclusivement du journal du bot réel et de la
réconciliation IBKR. Ces deux états ne doivent jamais être confondus.

Le score composite servant au classement (3.5) est lu dans
`docs/indices.json`, en rapprochant par `ticker`.

### 4.7 Résolution des contrats IBKR **[Validé par l'implémentation, Plan A]**

`docs/indices.json` identifie les entreprises par **ticker yfinance**
(`MC.PA`, `SAP.DE`, `ABBN.SW`, `III.L`, `A2A.MI`, `ANA.MC`, `ADBE`…).
IBKR travaille avec un `conid` numérique, résolu par une recherche
symbole + bourse + devise. Correspondance des suffixes à prévoir :

| Suffixe yfinance | Bourse | Devise | Indices concernés |
|---|---|---|---|
| `.PA` | Euronext Paris | EUR | CAC40 |
| `.DE` | Xetra | EUR | DAX |
| `.MI` | Borsa Italiana | EUR | FTSEMIB |
| `.MC` | BME (Madrid) | EUR | IBEX35 |
| `.L` | LSE | GBP / **GBp** | FTSE |
| `.SW` | SIX (Zurich) | CHF | SMI |
| *(aucun)* | NYSE / Nasdaq | USD | NASDAQ, DOW |

Deux pièges à traiter explicitement dans l'implémentation :

1. **Pence vs livres (LSE)** — piège avéré, déjà rencontré et corrigé
   dans ce dépôt le 2026-09-13. yfinance renvoie les cours londoniens en
   **pence (GBp)** ; `indices_score.py` les divise par 100 à la source
   (voir le commentaire autour de `history = history / 100.0`), donc
   **`docs/indices.json` contient des livres (GBP)**. IBKR, lui, cote et
   exécute les actions LSE en **GBp**. Tout calcul de taille de position
   et toute comparaison de prix sur un ticker `.L` doit donc convertir
   explicitement, sinon l'erreur est d'un facteur 100 — soit 100 fois
   trop d'actions achetées. Ce point mérite un test dédié, pas un
   commentaire.
2. **Ambiguïté de résolution** : une recherche symbole peut renvoyer
   plusieurs contrats (cotations multiples, ADR, dérivés). Proposition :
   n'accepter un contrat que si bourse **et** devise attendues
   correspondent, et refuser le signal en le journalisant
   (`contrat_non_resolu`) plutôt que d'acheter un instrument deviné. Les
   `conid` résolus sont mis en cache (`conid_cache.json`) pour éviter de
   refaire la recherche chaque jour.

### 4.8 Type d'ordre

**Décidé (2026-09-14) : ordre au marché (MKT)**, passé dans la fenêtre
14:30–15:30 UTC où toutes les places concernées sont ouvertes et
liquides, sur des constituants d'indices majeurs. C'est le choix le plus
simple et celui dont le comportement se rapproche le plus du
paper-trading (qui suppose une exécution immédiate au prix observé) —
préféré à un ordre limite pour éviter le risque de non-exécution
silencieuse, qui ferait diverger le réel du paper sans trace évidente,
pire qu'un léger slippage.

### 4.9 Journalisation

**Fichier nouveau et séparé** : `ibkr_bot/real_trading_log.jsonl`,
append-only, une ligne JSON par exécution du batch (mêmes conventions que
`gold_bot/decisions_log.jsonl`).

**`docs/signal_tracking.json` n'est jamais écrit par ce bot.** Le
paper-trading doit rester une donnée « propre » — sans slippage, sans
frais de change, sans exécution partielle — pour continuer à mesurer la
qualité du *signal* indépendamment de la qualité de l'*exécution*.
Mélanger les deux rendrait les deux inutilisables.

Chaque ligne du journal contient au minimum : horodatage, mode
(`dry_run` ou réel), état du préflight, liste des sorties évaluées et
exécutées, liste des signaux du jour avec leur rang et leur sort
(`achete`, `signal_ignore_plafond_atteint`,
`signal_ignore_prix_unitaire_superieur_au_budget`, `contrat_non_resolu`,
`erreur_execution`), et pour chaque ordre réellement passé : ticker,
conid, devise, taux de change appliqué, budget converti, nombre
d'actions, prix de référence utilisé pour le sizing, prix d'exécution
obtenu, commission si disponible, et **le prix retenu par le
paper-trading pour le même signal** (pour rendre l'écart réel/paper
directement lisible).

`ibkr_bot/positions.json` tient à part l'état de travail des positions
ouvertes **par le bot** : `id` (repris du paper : `<ticker>-<date>`),
ticker, conid, devise, quantité, prix d'exécution réel, date d'entrée,
`target_exit_price` figé, date limite à 6 mois.

**Résumé quotidien par email** : une fois par jour, jamais par trade —
même logique et même configuration SMTP que `gold_bot/notify.py` (qui
réutilise déjà les identifiants SMTP des alertes Indices). Le résumé
couvre : achats, ventes, signaux ignorés et pourquoi, erreurs, et état
du Gateway.

## 5. Sécurité et garde-fous

### 5.1 Identifiants

Miroir exact du pattern `gold_bot` (voir `deploy/README.md`) : un fichier
`.env` sur le VPS, `chmod 600`, propriété de l'utilisateur système dédié,
**jamais commité, jamais loggé**.

**[Proposé — non validé]** : un **utilisateur système séparé**
(`ibkrbot`) plutôt que de réutiliser `goldbot`. Raison : un bug ou une
compromission du bot Or ne doit pas donner accès aux identifiants d'un
compte-titres réel, et réciproquement. Coût : un clone du dépôt et un
venv de plus sur le VPS — négligeable.

Variables attendues **[Proposé — non validé]** :

```
IBKR_GATEWAY_URL=https://127.0.0.1:5000   # Gateway local, jamais exposé
IBKR_ACCOUNT_ID=...                       # compte sur lequel trader
SMTP_USER=...
SMTP_PASSWORD=...
MAIL_TO=...
```

(Pas de `IBKR_BOT_API_TOKEN` : l'interrupteur d'urgence est un fichier
d'état modifié en SSH, pas une route API — voir 5.2.)

Le **login et le mot de passe IBKR ne figurent pas dans le `.env`** : ils
sont saisis à la main dans l'interface d'authentification du Gateway
(localhost), une fois par session. C'est contraignant, mais c'est aussi
la meilleure propriété de sécurité du dispositif — les identifiants
maîtres du compte-titres ne sont jamais stockés sur le VPS, sous aucune
forme.

Le jeton Flex existant (`portfolio_sync.py`) reste totalement séparé et
conserve son rôle de lecture seule.

### 5.2 Interrupteur d'urgence

**Décidé (2026-09-14) : option A — fichier d'état, pas d'API HTTPS.**
L'interrupteur est `ibkr_bot/state.json` (`kill_switch`, `dry_run`),
modifié en SSH, exactement comme la bascule `dry_run` du bot Or décrite
en `deploy/README.md` §9.

Justification, retenue explicitement après l'incident du 2026-09-14 sur
`gold_bot` (jeton `BOT_API_TOKEN` perdu, récupération nécessitant un
accès root Hetzner) : un batch quotidien n'a qu'**une seule fenêtre
d'action par jour**, connue à l'avance — pas d'urgence à la minute comme
pour un bot qui trade en continu, toujours plusieurs heures pour couper
avant le prochain batch. Ajouter un second service HTTPS (port,
certificat, jeton, surface d'attaque) pour un besoin que la cadence
quotidienne rend non urgent aurait été de la complexité non justifiée, et
un secret de plus à perdre. Pas de service FastAPI dédié pour ce bot.

Les propriétés de `gold_bot` sont conservées : l'interrupteur ne peut que
**bloquer** des actions futures, jamais en déclencher ; il n'existe
aucune route ni aucun fichier capable de forcer un achat ; et `dry_run`
n'est jamais modifiable autrement qu'en SSH.

### 5.3 Mode simulation par défaut

Comme `gold_bot` : **`dry_run: true` par défaut**. Le batch évalue tout,
journalise exactement ce qu'il aurait fait (ordres, tailles, conversions,
sorties), et **n'appelle jamais la route de passage d'ordre**. Le passage
au réel est un changement d'état sur le VPS, en SSH, pas un
redéploiement de code.

Cette phase de simulation a ici une valeur particulière : elle permet de
comparer, sur plusieurs semaines et **sans risque**, les décisions du bot
réel à celles du paper-trading, et de détecter toute divergence de
logique avant qu'elle ne coûte de l'argent.

### 5.4 Réconciliation avant toute décision

Reprise du principe de `gold_bot.reconcile_positions()` : le bot **ne se
fie jamais à son seul état local**. À chaque batch, avant toute décision,
il interroge IBKR pour l'état réel des positions du compte, et le
rapproche de `positions.json` :

- Position dans le journal, absente chez IBKR (vendue à la main par
  l'utilisateur, par exemple) → marquée `cloturee_hors_bot` dans le
  journal, retirée du décompte des 10. Le bot ne la « rouvre » jamais.
- Position chez IBKR, absente du journal → **ignorée** : c'est une
  position de l'utilisateur, le bot n'y touche pas (voir 9.5).
- Quantité divergente → la quantité IBKR fait foi ; l'écart est
  journalisé comme anomalie et remonté dans le résumé quotidien.

C'est aussi ce qui rend le batch **idempotent** : une relance le même jour
après un plantage ne peut pas racheter une position déjà ouverte.

### 5.5 Pannes du Gateway et gestion des erreurs **[Proposé — non validé]**

**Préflight.** Le batch commence par vérifier l'état d'authentification
du Gateway. S'il n'est pas authentifié :

- **3 tentatives espacées de 10 minutes** (le batch reste dans la fenêtre
  14:45 → 15:05 UTC, encore dans la plage d'ouverture commune).
- Si l'échec persiste : **abandon complet du batch du jour**. Aucun ordre,
  ni entrée ni sortie. Journalisation `gateway_indisponible` et **email
  d'alerte immédiat** (pas seulement dans le résumé quotidien : c'est le
  seul cas où l'utilisateur doit agir le jour même, en réauthentifiant le
  Gateway).
- **Les signaux du jour sont perdus**, cohérent avec la règle 3.5 (pas de
  file d'attente, pas d'achat différé). **Les sorties, elles, sont
  simplement décalées au lendemain** : une position dont la condition de
  sortie est remplie reste ouverte un jour de plus. C'est une divergence
  réelle avec le paper-trading, à journaliser comme telle.

**Échec d'un ordre individuel.** Un ordre en échec (rejet, contrat non
résolu, cash insuffisant) **n'interrompt pas le reste du batch** — même
principe que `gold_bot/loop.py:execute_steps`, qui consigne chaque étape
réussie ou non sans jamais s'arrêter à la première erreur. Chaque échec
est journalisé individuellement avec sa cause, et le batch continue avec
les signaux suivants.

**Pas de reprise automatique d'un ordre échoué.** Un ordre rejeté n'est ni
réessayé dans le batch, ni reporté au lendemain — le signal est perdu,
comme en 3.5.

**Aucune décision sur donnée incomplète.** Si le prix courant d'une
position manque, on ne la vend pas (voir 3.6). Si les données du jour sont
périmées, on ne fait rien du tout (voir 4.5). C'est le principe déjà posé
pour `gold_bot` : mieux vaut ne rien faire qu'agir sur une donnée
douteuse.

## 6. Coûts

**Connus et acceptés :**

- **Commissions par trade** IBKR, sur chaque achat et chaque vente. Sur
  des ordres de 500 €, les commissions minimales par ordre (qui existent
  sur toutes les places européennes) pèsent proportionnellement plus que
  sur de gros ordres — c'est un coût structurel de la stratégie, pas une
  anomalie.
- **Spread de change** à chaque conversion EUR → devise locale, et
  potentiellement au retour. Explicitement accepté (pas de hedging).

**Non résolu — abonnements aux données de marché.** IBKR facture des
abonnements de données de marché par place (Euronext, Xetra, LSE, SIX,
Borsa Italiana, BME). Un mécanisme de **dispense** existe : au-delà d'un
certain volume de commissions généré sur un marché dans le mois (ordre de
grandeur usuellement cité : 20-30 $/mois), l'abonnement de ce marché est
offert. Comme ce bot trade activement, ce coût pourrait souvent retomber
à 0 € — mais **ce n'est garanti ni tous les mois, ni sur toutes les places
du périmètre** (un mois sans signal sur le SMI, par exemple, ne génère
aucune commission suisse).

**Les montants exacts hors dispense n'ont pas pu être vérifiés** : le site
IBKR bloque la récupération automatique de ses pages tarifaires.

> **À faire manuellement avant tout lancement en réel** : se connecter au
> compte IBKR (ou contacter le support) et relever, pour chacune des 6
> places européennes concernées plus les places américaines, le coût
> mensuel de l'abonnement de données et le seuil exact de dispense.
> Aucun chiffre n'est avancé ici — ce document ne contient délibérément
> aucun montant inventé.

**Piste d'atténuation à évaluer** **[Proposé — non validé]** : les
**décisions** du bot (entrées et sorties) reposent sur les prix de
`docs/indices.json`, produits par yfinance — pas sur les données IBKR.
Les données de marché IBKR ne seraient donc nécessaires que pour
l'**exécution** (prix de référence du sizing, exécution d'un ordre au
marché). Ce découplage a un double mérite : il maintient la comparabilité
avec le paper-trading (même source de prix pour décider), et il pourrait
réduire le besoin d'abonnements payants. **À vérifier** : un ordre au
marché peut-il être passé sans abonnement de données sur la place
concernée ?

**Déjà engagés, inchangés** : VPS Hetzner (partagé avec `gold_bot`),
compte IBKR **cash** (pas de marge, pas de levier — minimum de dépôt 0 $,
vérifié le 2026-09-14 sur
`interactivebrokers.com/en/accounts/required-minimums.php`). L'accès Web
API fonctionne dès que le compte est ouvert et financé, sans approbation
spéciale pour les fonctionnalités de base (vérifié le 2026-09-14 sur
`interactivebrokers.com/campus/ibkr-api-page/getting-started/`).

## 7. Tests **[Proposé — non validé]**

Mêmes conventions que le reste du dépôt : `pytest` + `monkeypatch`, TDD,
aucun appel réseau réel (voir `tests/gold_bot/` comme modèle — notamment
le faux objet réponse de `tests/gold_bot/test_broker.py`).

**`tests/ibkr_bot/test_gateway.py`** — client IBKR entièrement mocké
(faux objet réponse HTTP) : URL appelée, en-têtes, corps de l'ordre,
propagation des erreurs HTTP. Aucun test ne doit pouvoir toucher un
Gateway réel.

**`tests/ibkr_bot/test_sizing.py`** — le cœur du risque d'erreur
silencieuse :
- arrondi entier vers le bas, reliquat laissé en cash ;
- **cas 0 action** : prix unitaire > budget converti → aucun ordre, motif
  journalisé (test explicite, c'est le cas limite le plus facile à
  implémenter de travers en achetant 1 action « au moins ») ;
- conversion de devise pour EUR / USD / GBP / CHF ;
- **cas pence (`.L`)** : prix en GBP dans `indices.json` vs GBp chez IBKR
  — vérifier que la quantité calculée n'est pas 100× trop grande.

**`tests/ibkr_bot/test_portfolio.py`** —
- plafond de 10 : 0, 1, exactement 10, plus de 10 positions ouvertes ;
- sursouscription : classement par score décroissant, remplissage des
  places, signaux surnuméraires perdus et journalisés ;
- **ordre de priorité des sorties** : un cas où stop-loss *et* objectif
  seraient tous deux remplis doit sortir en `stop_loss` ; un cas où
  objectif *et* délai max sont remplis doit sortir en `objectif_atteint` ;
- prix manquant ou ticker disparu → position **laissée intacte** ;
- réconciliation : position disparue chez IBKR, position inconnue du
  journal, quantité divergente.

**`tests/ibkr_bot/test_signals.py`** —
- extraction des nouveaux signaux du jour depuis `signal_tracking.json` ;
- un signal déjà acheté la veille n'est pas racheté ;
- rapprochement du score depuis `indices.json` ;
- `indices.json` périmé → aucun signal retourné, aucun ordre.

**`tests/ibkr_bot/test_daily.py`** — l'orchestrateur, avec un faux
gateway :
- `dry_run: true` → **aucun appel de passage d'ordre**, journal complet
  malgré tout (c'est le test le plus important de toute la suite) ;
- `kill_switch: true` → aucune action, ni entrée ni sortie ;
- préflight en échec → 3 tentatives puis abandon, email d'alerte ;
- un ordre en échec n'interrompt pas les suivants ;
- relance du batch le même jour après plantage → pas de double achat.

**Test de non-régression transversal** **[Validé par l'implémentation, Plan A]** : un
test qui rejoue un historique de signaux à travers **la logique de sortie
du bot réel** et **`_close_eligible_positions()` du paper-trading**, et
vérifie que les décisions de sortie sont identiques à prix identiques.
C'est la garantie mécanique que les deux logiques ne divergent pas — la
promesse centrale de ce chantier, qu'aucun test unitaire pris isolément
ne protège.

**Validation avant le réel** : plusieurs semaines en `dry_run` sur le
VPS, avec comparaison quotidienne des décisions simulées aux positions du
paper-trading. Même démarche que le bot Or, avec ici un critère de
sortie explicite : les décisions doivent coïncider, et tout écart doit
s'expliquer par une cause connue et documentée (plafond de 10, 0 action,
horaire d'exécution).

## 8. Déploiement **[Proposé — non validé]**

Miroir de `deploy/README.md`, sur le même VPS :

- `deploy/ibkr-gateway.service` — Gateway IBKR, `Restart=on-failure`,
  écoute sur 127.0.0.1 uniquement, **aucune ouverture de port dans ufw**.
- `deploy/ibkr-bot-daily.service` + `deploy/ibkr-bot-daily.timer` —
  `Type=oneshot`, déclenché à 14:45 UTC. Un **timer systemd** plutôt
  qu'une entrée cron : cohérent avec les services systemd déjà en place
  pour `gold_bot`, et `journalctl` donne l'historique d'exécution
  gratuitement.
- Utilisateur dédié `ibkrbot`, clone du dépôt, venv, `.env` en `chmod 600`
  (voir 5.1).
- Mise à jour par `git pull` + redémarrage, comme `deploy/deploy.sh`.

Note d'exploitation : la ré-authentification du Gateway (2FA) est une
**action manuelle récurrente**, potentiellement quotidienne. C'est la
principale servitude opérationnelle de ce chantier, et elle doit être
acceptée en connaissance de cause avant le lancement — pas découverte
après.

## 9. Points arbitrés le 2026-09-14

Tous les points bloquants ont été tranchés avec l'utilisateur (en
continuité de la session de brainstorming initiale). Un seul reste une
vérification manuelle à faire avant lancement, sans bloquer l'écriture du
plan d'implémentation.

### 9.1 Change dans un compte cash — résolu, non bloquant

Vérifié le 2026-09-14 (voir 3.2) : IBKR route les conversions sous
25 000 $ via **IDEAL**, pas IDEALPRO — spread de 0,03 % en automatique,
ou quelques dollars en manuel. Les conversions de ~500 € sont traitées
normalement. Le périmètre à 8 indices / 4 devises (section 2) est
confirmé, aucun repli sur un périmètre EUR-only.

### 9.2 Coûts de données de marché — non bloquant, à vérifier avant lancement

Reste un point ouvert, mais **n'empêche pas l'écriture du plan
d'implémentation** : les montants exacts par place et le seuil de
dispense doivent être relevés manuellement sur le compte IBKR (ou auprès
du support) avant le passage en réel, pas avant le plan. Voir section 6
pour la piste d'atténuation (décisions sur prix yfinance, IBKR requis
seulement pour l'exécution) et sa question associée (ordre au marché
possible sans abonnement data ?).

### 9.3 Choix du Gateway — résolu

**Client Portal Web API Gateway.** Voir 4.4.

### 9.4 Interrupteur d'urgence — résolu

**Option A : fichier d'état + SSH, pas de service HTTPS dédié.** Décidé
explicitement à la lumière de l'incident `BOT_API_TOKEN` du 2026-09-14
sur `gold_bot` — un jeton de moins à perdre, une cadence quotidienne qui
ne justifie pas la réactivité d'une API. Voir 5.2.

### 9.5 Périmètre d'action sur le compte — résolu

Le bot **n'agit que sur les positions qu'il a lui-même ouvertes** (le
comportement de sécurité, section 5.4, est indépendant de la réponse).
Le tableau de bord (hors périmètre v1, voir section 2) affichera en
lecture seule l'ensemble des positions du compte, même celles que le bot
n'a pas ouvertes, sur le même principe que le tableau de bord MT5 de
`gold_bot`. Le plafond de 10 (3.4) s'entend comme 10 positions **du
bot**, pas 10 positions au total sur le compte.

### 9.6 Horaire du batch — résolu

**14:45 UTC**, confirmé. Conséquence assumée : le prix d'exécution réel
diffère structurellement du prix de décision du paper-trading (cours de
clôture de la veille) — voir 4.5.

### 9.7 Type d'ordre — résolu

**Ordre au marché (MKT).** Voir 4.8.

### 9.8 Comportement en cas de vente impossible — résolu

**Décalage au batch suivant, accepté explicitement**, en connaissance de
cause qu'un stop-loss à -20 % pourrait rester ouvert un jour de plus en
cas de panne technique — propriété déjà partagée avec le paper-trading
(évalué une fois par jour lui aussi). Pas de mécanisme d'urgence pour
forcer une vente.

### 9.9 Financement et suivi du solde — résolu, révisé le 2026-09-15

**Garde-fou de solde ajouté**, mais pas par devise. La première version
de cette décision (« solde disponible par devise ») contredisait 9.1/3.2 :
puisqu'IBKR convertit automatiquement au moment de l'achat via IDEAL, il
n'y a pas besoin de cash pré-converti par devise — l'exiger aurait
bloqué la plupart des signaux sur un compte financé normalement (en EUR),
comme l'a confirmé la revue finale du Plan A avec des données réelles
(le signal le mieux noté du jour, en USD, aurait été rejeté à tort sur un
compte avec 8 000 € de cash disponible).

**Décision corrigée, tranchée par l'utilisateur pendant la revue de Plan A** :
le garde-fou compare le budget cumulé du classement (à raison de 500 €
par position retenue, un majorant sûr — l'arrondi aux actions entières ne
fait que réduire la dépense réelle, jamais l'augmenter) au solde total
disponible dans la **devise de base du compte**, pas devise par devise.
Implémenté par `ibkr_bot.gateway.base_currency_cash()` (lit l'agrégat
`"BASE"` du ledger IBKR) et le garde-fou de `ibkr_bot.portfolio.select_entries`.

## 10. Prochaine étape

Tous les points de la section 9 sont arbitrés. **Reste, avant le
lancement en réel (pas avant le plan d'implémentation)** : la
vérification manuelle des coûts de données de marché (9.2/section 6).

**Prochaine étape immédiate : invoquer `superpowers:writing-plans` pour
transformer cette spec en plan d'implémentation détaillé, tâche par
tâche.** Aucun code n'a encore été écrit.
