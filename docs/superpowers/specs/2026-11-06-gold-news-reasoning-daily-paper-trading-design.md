# Test prospectif IA sur l'or — passage au paper-trading quotidien — design

## 1. Contexte et objectif

Le test prospectif actuel (`docs/superpowers/plans/2026-11-06-gold-news-reasoning-forward-test.md`,
routine `trig_01KzB6rZrwxUW1yoike8KLrP`) fait un seul pari directionnel
daté par semaine sur XAUUSD (haussier/baissier/neutre, horizon fixe de
3 semaines), enregistré dans un fichier `docs/superpowers/experiments/
<date>-gold-news-reasoning-test.json`, vérifié manuellement des semaines
plus tard. Un seul pari a été enregistré à ce jour (2026-11-06).

L'utilisateur veut tester cette méthodologie **comme si elle était
réellement utilisée** — pas un avis statique une fois par semaine, mais
une position suivie activement dans le temps (ouverture, gestion,
clôture), comme le ferait un vrai gérant discrétionnaire, pour juger si
le raisonnement de l'IA a une vraie valeur ajoutée.

## 2. Portée

**Dans le périmètre :**
- Routine cloud quotidienne (jours ouvrés) qui gère un historique de
  positions simulées sur XAUUSD : ouverture, réévaluation active,
  clôture (stop de sécurité, décision IA anticipée, ou délai max).
- Un seul fichier de suivi persistant (`docs/gold_news_reasoning_tracking.json`),
  qui remplace la production de nouveaux fichiers hebdomadaires.
- Comparaison de chaque position clôturée à un repère buy&hold passif
  sur XAUUSD, sur la même période exacte.

**Hors périmètre (explicite) :**
- Aucun compte réel, aucune exécution, aucun lien avec `gold_bot/` ou
  FTMO — toujours purement observationnel (voir la règle du projet :
  jamais de promotion vers un rôle réel sans plusieurs semaines de
  preuve prospective).
- Plusieurs positions ouvertes en parallèle — une seule à la fois.
- Cadence supra-quotidienne (intraday, à chaque cycle du vrai bot) —
  explicitement différée par l'utilisateur à une itération future,
  une fois la cadence quotidienne validée.
- Ne modifie pas le test équivalent sur les actions
  (`docs/superpowers/experiments/*-ia-news-reasoning-test.json`,
  méthodologie différente, pas concerné).
- Les 2 fichiers déjà écrits par l'ancien système
  (`docs/superpowers/experiments/2026-11-06-gold-news-reasoning-test.json`
  et son plan/spec) restent en l'état, comme archive historique — non
  migrés dans le nouveau fichier de suivi (schémas trop différents pour
  une migration automatique qui ait un sens, et il n'y a qu'un seul pari
  à migrer).

## 3. Modèle de données — `docs/gold_news_reasoning_tracking.json`

Nouveau fichier, structure `{"positions": [...]}`, une entrée par
position (ouverte ou clôturée). Même esprit que `docs/signal_tracking.json`
(indices), adapté à un seul ticker et à une gestion active plutôt qu'un
objectif de sortie fixe :

```json
{
  "positions": [
    {
      "id": "XAUUSD-2026-11-10",
      "status": "open",

      "entry_date": "2026-11-10",
      "entry_price": 4080.0,
      "direction": "achat",

      "safety_stop_price": 3975.0,
      "safety_stop_pct": -0.0257,
      "confiance": "moyenne",
      "raison_ouverture": "une ou deux phrases",

      "max_close_date": "2026-12-01",

      "close_date": null,
      "close_price": null,
      "close_reason": null,
      "return_pct": null,

      "buyhold_price_at_close": null,
      "buyhold_return_pct": null,

      "journal": [
        {"date": "2026-11-11", "price": 4090.0, "decision": "garder", "raison": "une phrase"}
      ]
    }
  ]
}
```

- `id` : `XAUUSD-{entry_date}` — identifiant stable. Comme une seule
  position est ouverte à la fois, pas de collision possible.
- `direction` ∈ `"achat" | "vente"` — une position n'est créée QUE si
  l'appel directionnel du jour est haussier ou baissier (y compris les
  nuances type `leger_haussier` → `achat`). Un appel neutre n'ouvre
  aucune position (voir §4.1) — pas d'entrée dans `positions` pour ce
  jour-là, seulement une ligne de log de la routine (pas de fichier).
- `safety_stop_price`/`safety_stop_pct` : fixés une seule fois, à
  l'ouverture, par l'IA elle-même (voir §4.2 pour les bornes). Pour un
  achat, `safety_stop_price < entry_price` ; pour une vente,
  `safety_stop_price > entry_price`. `safety_stop_pct` est toujours
  negatif (perte exprimée en %, quel que soit le sens de la position) :
  `(safety_stop_price / entry_price - 1)` pour un achat,
  `(entry_price / safety_stop_price - 1)` pour une vente.
- `max_close_date` : fixée à l'ouverture, `entry_date` + 3 semaines
  (`dateutil.relativedelta(weeks=3)`, même raison que pour les indices :
  rester correct même si l'implémentation change un jour pour des mois).
- `close_reason` ∈ `"stop_securite" | "cloture_anticipee_ia" | "delai_max"`.
- `return_pct` : `(close_price / entry_price - 1)` pour un achat,
  `(entry_price / close_price - 1)` pour une vente — toujours le
  rendement RÉEL de la position simulée, signe inclus.
- `buyhold_price_at_close`/`buyhold_return_pct` : le prix de clôture
  réel de XAUUSD à `close_date`, et son % de variation BRUT depuis
  `entry_price` (`close_price_marche / entry_price - 1`, **jamais**
  ajusté par `direction` — c'est le repère passif "aurait-on mieux fait
  de juste détenir de l'or sur cette période", pas une version
  retournée du pari). Un achat qui gagne +3% pendant que l'or a fait
  +5% sur la même période a quand même sous-performé le repère, même si
  la position simulée est gagnante dans l'absolu.
- `journal` : une ligne par jour de réévaluation où la position reste
  ouverte (pas de ligne le jour de la clôture elle-même — ce jour est
  entièrement décrit par les champs `close_*`). Permet de comprendre a
  posteriori pourquoi l'IA a gardé une position plutôt que de ne garder
  que l'état final.

## 4. Logique quotidienne (jours ouvrés)

Nouvelle routine planifiée (remplace en place `trig_01KzB6rZrwxUW1yoike8KLrP` :
même id, cron et prompt mis à jour — pas de nouvelle routine créée),
cron `0 7 * * 1-5` (inchangé en heure, étendu du lundi seul à
lundi-vendredi).

### 4.1 Récupération des données de prix (quantitatif, PAS WebSearch)

Le cache statique `docs/superpowers/research/gold_bot_ftmo_check/data/
xauusd_5min_raw.json` s'arrête au 2026-10-26 et n'est plus mis à jour —
inutilisable comme source vivante. `WebSearch` ne renvoie que des
extraits de texte, pas des OHLC précis et fiables pour détecter un
franchissement de stop. La routine utilise donc `yfinance` (déjà une
dépendance du dépôt, `requirements.txt:2`) via Bash :

```python
import yfinance as yf
data = yf.download("XAUUSD=X", period="1mo", interval="1d")
# repli si XAUUSD=X echoue ou renvoie une serie vide : "GC=F" (futures COMEX, proxy correct)
```

Récupère les clôtures/plus-hauts/plus-bas journaliers depuis la
dernière vérification (ou depuis `entry_date` si c'est la première
réévaluation) — nécessaire pour détecter un franchissement de stop
INTRAJOURNALIER (voir §4.3), pas seulement sur la clôture du jour.
`WebSearch` reste réservé à la partie qualitative (actualité macro,
§4.2/4.4) — jamais utilisé pour une valeur de prix qui déclenche une
décision de clôture.

### 4.2 Aucune position ouverte → décision d'ouverture

Même raisonnement que l'ancien système (actu macro/géopolitique via
`WebSearch`, comportement récent du prix/volume via yfinance) pour
l'appel directionnel (`haussier`/`baissier`/`neutre`, nuances
possibles). Si haussier/baissier (y compris nuancé) :
- Ouvre une position (`direction = achat` si haussier, `vente` si
  baissier) à `entry_price` = dernière clôture connue.
- L'IA fixe elle-même `safety_stop_price`, **bornée à une distance de
  3% à 15% du prix d'entrée** (en valeur absolue) — une distance
  hors de ces bornes est rejetée et remplacée par la borne la plus
  proche (jamais un garde-fou qui bloque tout le run, toujours une
  correction silencieuse et loggée). L'IA justifie ce choix dans
  `raison_ouverture` en fonction de la volatilité récente qu'elle
  observe.
- `max_close_date` = `entry_date` + 3 semaines.
Si neutre : aucune position créée, rien n'est écrit dans `positions`
pour ce jour-là. Cette étape 4.2 s'exécute aussi le jour même où une
position vient d'être clôturée par l'étape 4.3 (pas besoin d'attendre
le jour ouvré suivant pour réévaluer une nouvelle entrée) — dans ce
cas, le commit du jour contient à la fois la clôture et, le cas
échéant, la nouvelle ouverture.

### 4.3 Position ouverte → réévaluation

1. **Vérifier le stop d'abord** (priorité la plus haute, comme pour les
   indices) : pour chaque jour depuis la dernière vérification, si le
   PLUS BAS du jour (achat) ou le PLUS HAUT du jour (vente) a franchi
   `safety_stop_price` : `status = "closed"`, `close_date` = ce jour,
   `close_price = safety_stop_price` (pas le cours de clôture réel de
   ce jour — on clôture AU niveau du stop, comme un vrai ordre stop),
   `close_reason = "stop_securite"`.
2. **Sinon, réévaluation active par l'IA** (actu + prix depuis
   l'ouverture ou la dernière réévaluation) : décide `garder` ou
   `clôturer` (prise de profit, retournement de thèse). Si `clôturer` :
   `close_price` = dernière clôture connue, `close_reason =
   "cloture_anticipee_ia"`. Si `garder` : ajoute une entrée à
   `journal` (date, prix du jour, décision, raison en une phrase), la
   position reste `"open"`.
3. **Sinon, si `today >= max_close_date`** : clôture forcée,
   `close_price` = dernière clôture connue, `close_reason = "delai_max"`.
4. **Si clôturée (n'importe laquelle des 3 raisons ci-dessus)** :
   calculer `return_pct`, puis `buyhold_price_at_close` (clôture XAUUSD
   réelle à `close_date`) et `buyhold_return_pct` (voir §3).

### 4.4 Écriture et commit

Écrit `docs/gold_news_reasoning_tracking.json` (le fichier entier,
réécrit en totalité comme les autres fichiers d'état de ce projet), un
seul commit avec un message court type `chore(gold-bot): suivi
quotidien test prospectif IA or`, push sur `main`. N'écrit et ne commit
AUCUN autre fichier. Seul le cas "aucune position ouverte ET appel
neutre" ne modifie pas le fichier : aucun commit ce jour-là. Dans tous
les autres cas (ouverture, clôture, ou simple ajout au `journal` d'une
position gardée), le fichier change et un commit est fait.

## 5. Coût et fréquence

Un appel Opus + `web_search` par jour ouvré (environ 21-22/mois, contre
~4/mois aujourd'hui) — même ordre de grandeur de coût unitaire que le
test actuel et que `ai_shadow`/`ai_news_watch`, juste ~5x plus fréquent.
Explicitement accepté par l'utilisateur après avoir chiffré qu'une
cadence par cycle du vrai bot (toutes les 60 secondes) aurait coûté
~500x plus cher pour un gain d'information nul (une thèse sur 3 semaines
ne change pas minute par minute) — cette cadence plus fine reste
différée à une itération future explicitement voulue par l'utilisateur,
pas abandonnée.

## 6. Devenir de l'ancien système

La routine `trig_01KzB6rZrwxUW1yoike8KLrP` est mise à jour en place
(même id, cron et prompt remplacés) plutôt que créer une seconde
routine — un seul mécanisme de test prospectif sur l'or à la fois, pas
deux qui tournent en parallèle avec des schémas différents. Le fichier
déjà écrit par l'ancien système
(`docs/superpowers/experiments/2026-11-06-gold-news-reasoning-test.json`,
le seul pari enregistré à ce jour) reste en l'état comme archive — non
migré, non vérifié par ce nouveau système (il sera vérifié manuellement
le 2026-11-27 comme prévu initialement, indépendamment de ce
changement).

## 7. Tests (densité à suivre, comme pour `tests/gold_bot/test_confluence.py`)

Ce chantier ne touche aucun fichier `gold_bot/*.py` (toute la logique
vit dans le prompt d'une routine cloud, comme le système actuel et le
cadran macro hebdomadaire) — pas de suite `pytest` à écrire pour la
routine elle-même. La seule vérification possible est manuelle : lancer
la routine une fois (`RemoteTrigger action: "run"`), lire son
`get_run_log`, et valider à la main que `docs/gold_news_reasoning_tracking.json`
est écrit avec le schéma exact ci-dessus, que `yfinance` a bien été
utilisé pour les prix (pas de prix approximatif sorti d'un résultat
`WebSearch`), et qu'un commit unique et propre a été fait — même
méthode de validation que le test manuel de l'ancien système (Task 1 du
plan `2026-11-06-gold-news-reasoning-forward-test.md`).
