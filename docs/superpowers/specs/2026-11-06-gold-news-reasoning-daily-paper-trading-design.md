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
- Workflow planifié quotidien (jours ouvrés, voir §4.0 pour le choix
  de GitHub Actions plutôt qu'une routine cloud) qui gère un historique
  de positions simulées sur XAUUSD : ouverture, réévaluation active,
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

### 4.0 Révision de mécanisme (2026-11-06, après coup) : routine cloud abandonnée, GitHub Actions

Le mécanisme décrit plus bas dans ce §4 (une routine cloud planifiée
dont le PROMPT fait tout le travail, mettant à jour `trig_01KzB6rZrwxUW1yoike8KLrP`
en place) a été TESTÉ EN RÉEL le 2026-11-06 et s'est révélé irréalisable :
le sandbox réseau de cette routine cloud bloque tout accès sortant
direct, y compris `yfinance` via Bash (`CONNECT tunnel failed, 403`) ET
`WebFetch` vers une API JSON externe (`getaddrinfo ENOTFOUND` — domaine
hors de la liste réseau autorisée de la session). Seul `WebSearch`
fonctionne dans cet environnement, mais il ne renvoie que du texte
approximatif, incompatible avec l'exigence de précision du §3 (détecter
un franchissement de stop exact).

**Nouveau mécanisme : un workflow GitHub Actions planifié**, comme
`.github/workflows/indices.yml` déjà existant dans ce dépôt (même
patron : `actions/checkout`, `actions/setup-python`, `pip install -r
requirements.txt`, script Python, `git add`/`commit`/`push`). Un
environnement GitHub Actions a un accès réseau internet complet (pas de
sandbox restrictif) : `yfinance` fonctionne sans contournement, et
l'appel à l'API Claude (raisonnement + `web_search`) se fait en HTTP
direct via `requests` (même patron que `indices_score.py:4390-4411`,
pas le SDK `anthropic` qui n'est pas une dépendance de ce dépôt),
authentifié par le secret GitHub déjà existant `secrets.CLAUDE_API_KEY`
(déjà utilisé par `indices.yml` — aucun nouveau secret à créer).

**Conséquence positive** : tout ce qui était auparavant calculé par le
modèle dans un prompt (clamp du stop à 3%-15%, `return_pct`,
`buyhold_return_pct`, comparaison des plus-hauts/plus-bas au stop,
`max_close_date`) devient du VRAI CODE PYTHON déterministe et testable
par `pytest` — le modèle n'est plus appelé QUE pour les deux jugements
qualitatifs qui nécessitent un vrai raisonnement (l'appel directionnel
à l'ouverture, et la décision garder/clôturer à la réévaluation). Ceci
élimine un risque réel trouvé pendant les tests manuels du 2026-11-06 :
le modèle avait, une fois sur deux tentatives, ajouté à tort une entrée
de journal le jour même d'une clôture (corrigé dans le prompt à
l'époque, mais un calcul fait en code n'a plus ce risque du tout).

**Fichiers** (voir le plan d'implémentation pour le détail complet) :
- Nouveau module `gold_news_reasoning_daily.py` (racine du dépôt,
  à côté de `indices_score.py`) : fonctions pures testables (fetch OHLC,
  normalisation de l'appel directionnel, clamp du stop, calcul des
  rendements, détection de franchissement de stop, `max_close_date`) +
  deux fonctions d'appel à l'API Claude (nouvelle entrée, réévaluation).
- Nouveau workflow `.github/workflows/gold_news_reasoning_daily.yml`,
  cron quotidien jours ouvrés.
- La routine cloud `trig_01KzB6rZrwxUW1yoike8KLrP` est DÉSACTIVÉE
  (`enabled: false`, pas supprimée — l'API ne permet pas de suppression)
  une fois le workflow GitHub Actions validé en réel, pour éviter deux
  mécanismes concurrents sur le même fichier de suivi.

Le reste de ce §4 (la logique métier elle-même : priorités, bornes,
formules) reste BINDING et inchangé — seul CE QUI L'EXÉCUTE change.

### 4.1 Récupération des données de prix (quantitatif, PAS WebSearch)

Le cache statique `docs/superpowers/research/gold_bot_ftmo_check/data/
xauusd_5min_raw.json` s'arrête au 2026-10-26 et n'est plus mis à jour —
inutilisable comme source vivante. `WebSearch` ne renvoie que des
extraits de texte, pas des OHLC précis et fiables pour détecter un
franchissement de stop. Le script utilise donc `yfinance` (déjà une
dépendance du dépôt, `requirements.txt:2`), exécuté dans l'environnement
GitHub Actions (réseau internet complet, voir §4.0) :

```python
import yfinance as yf
data = yf.download("GC=F", period="2mo", interval="1d")
# repli si GC=F echoue ou renvoie une serie vide : "XAUUSD=X"
# (XAUUSD=X a echoue le 2026-11-06 lors du test manuel -- GC=F est
# le ticker confirme fonctionnel, garde en premier)
```

Récupère les clôtures/plus-hauts/plus-bas journaliers depuis la
dernière vérification (ou depuis `entry_date` si c'est la première
réévaluation) — nécessaire pour détecter un franchissement de stop
INTRAJOURNALIER (voir §4.3), pas seulement sur la clôture du jour.
`WebSearch` (via l'appel API avec l'outil `web_search`) reste réservé à
la partie qualitative (actualité macro, §4.2/4.4) — jamais utilisé pour
une valeur de prix qui déclenche une décision de clôture.

### 4.2 Aucune position ouverte → décision d'ouverture

Un appel à l'API Claude (raisonnement + `web_search` sur l'actualité
macro/géopolitique récente) demande l'appel directionnel
(`haussier`/`baissier`/`neutre`, nuances possibles) sur l'horizon des 3
prochaines semaines. Le CODE (pas le modèle) normalise cet appel en
`achat`/`vente`/`None` (voir le plan pour la table de correspondance
exacte des nuances). Si haussier/baissier (y compris nuancé) :
- Ouvre une position (`direction = achat` si haussier, `vente` si
  baissier) à `entry_price` = dernière clôture connue (yfinance).
- Le modèle propose une distance de stop (en %) dans sa réponse ; le
  CODE la CLAMPE à une distance de **3% à 15%** du prix d'entrée (en
  valeur absolue) avant de calculer `safety_stop_price` — jamais
  appliquée telle quelle si elle sort de cette fourchette, toujours une
  correction silencieuse et loggée (pas un garde-fou qui bloque tout le
  run). Le modèle justifie ce choix dans `raison_ouverture` en fonction
  de la volatilité récente qu'il observe.
- `max_close_date` = `entry_date` + 21 jours calendaires (3 semaines),
  calculé par le CODE.
Si neutre (ou appel non reconnu après normalisation) : aucune position
créée, rien n'est écrit dans `positions` pour ce jour-là. Cette étape
4.2 s'exécute aussi le jour même où une position vient d'être clôturée
par l'étape 4.3 (pas besoin d'attendre le jour ouvré suivant pour
réévaluer une nouvelle entrée) — dans ce cas, le commit du jour contient
à la fois la clôture et, le cas échéant, la nouvelle ouverture.

### 4.3 Position ouverte → réévaluation

1. **Vérifier le stop d'abord** (priorité la plus haute, comme pour les
   indices — calcul 100% déterministe par le CODE, AUCUN appel modèle
   nécessaire pour cette étape) : pour chaque jour depuis la dernière
   vérification, si le PLUS BAS du jour (achat) ou le PLUS HAUT du jour
   (vente) a franchi `safety_stop_price` : `status = "closed"`,
   `close_date` = ce jour, `close_price = safety_stop_price` (pas le
   cours de clôture réel de ce jour — on clôture AU niveau du stop,
   comme un vrai ordre stop), `close_reason = "stop_securite"`.
2. **Sinon, si `today >= max_close_date`** (déterministe, AUCUN appel
   modèle) : clôture forcée, `close_price` = dernière clôture connue,
   `close_reason = "delai_max"`.
3. **Sinon, réévaluation active par le modèle** (actu + prix depuis
   l'ouverture ou la dernière réévaluation, via un appel API avec
   `web_search`) : décide `garder` ou `clôturer` (prise de profit,
   retournement de thèse). Si `clôturer` : `close_price` = dernière
   clôture connue, `close_reason = "cloture_anticipee_ia"`. Si
   `garder` : le CODE ajoute une entrée à `journal` (date, prix du
   jour, décision, raison en une phrase prise dans la réponse du
   modèle), la position reste `"open"`. Si l'appel API échoue (clé
   absente, erreur, timeout, réponse non parseable) : traité comme
   `garder` par défaut (pas de clôture forcée sur un simple échec
   technique), avec une entrée de journal notant l'échec.
4. **Si clôturée (n'importe laquelle des 3 raisons ci-dessus)** : le
   CODE calcule `return_pct`, puis `buyhold_price_at_close` (clôture
   réelle à `close_date`, donnée yfinance) et `buyhold_return_pct`
   (voir §3).

(L'ordre a changé par rapport à la version précédente de ce spec :
`delai_max` passe avant la réévaluation qualitative — les deux
conditions déterministes d'abord, dans n'importe quel ordre entre elles
puisqu'elles ne peuvent pas être vraies toutes les deux le même jour en
pratique, puis seulement si aucune des deux ne s'applique, l'unique
appel modèle coûteux de cette étape.)

### 4.4 Écriture et commit

Le script écrit `docs/gold_news_reasoning_tracking.json` (le fichier
entier, réécrit en totalité). Le WORKFLOW (pas le script) fait le
commit, même patron que `.github/workflows/indices.yml` : `git add`
ciblé sur ce seul fichier (jamais `git add -A`), commit avec un message
court type `chore(gold-bot): suivi quotidien test prospectif IA or`
SEULEMENT si le fichier a changé (`git diff --staged --quiet ||
git commit ...`), `git pull --rebase --autostash` puis `git push`. Seul
le cas "aucune position ouverte ET appel neutre" ne modifie pas le
fichier : aucun commit ce jour-là.

## 5. Coût et fréquence

Un appel Claude + `web_search` par jour ouvré DANS LE CAS GÉNÉRAL — en
pratique souvent zéro appel modèle du tout certains jours (clôture par
stop ou délai max, 100% déterministe, voir §4.3) et un seul appel les
autres jours (soit l'ouverture §4.2, soit la réévaluation qualitative
§4.3.3 — jamais les deux le même jour sauf clôture+réouverture
immédiate, donc au plus 2 appels ce jour précis). Coût mensuel attendu
plus bas que l'estimation initiale du 2026-11-06 (qui supposait un
appel systématique tous les jours ouvrés), grâce aux deux chemins
déterministes gratuits ajoutés par le passage à du code réel (§4.0).
Explicitement accepté par l'utilisateur après avoir chiffré qu'une
cadence par cycle du vrai bot (toutes les 60 secondes) aurait coûté
~500x plus cher pour un gain d'information nul (une thèse sur 3 semaines
ne change pas minute par minute) — cette cadence plus fine reste
différée à une itération future explicitement voulue par l'utilisateur,
pas abandonnée.

## 6. Devenir de l'ancien système

Deux systèmes précédents sont remplacés par celui-ci :
- La routine cloud hebdomadaire ORIGINALE (fichiers
  `docs/superpowers/experiments/*-gold-news-reasoning-test.json`, un
  seul pari enregistré le 2026-11-06) — déjà remplacée le même jour par
  la version "paper-trading quotidien en routine cloud", qui elle-même
  s'est révélée irréalisable (§4.0).
- La routine cloud `trig_01KzB6rZrwxUW1yoike8KLrP` (mise à jour en
  place le 2026-11-06 avant la découverte du blocage réseau) est
  DÉSACTIVÉE (`enabled: false`) une fois le workflow GitHub Actions
  validé en réel — un seul mécanisme actif à la fois.

Le fichier déjà écrit par le tout premier système
(`docs/superpowers/experiments/2026-11-06-gold-news-reasoning-test.json`,
le seul pari enregistré à ce jour) reste en l'état comme archive — non
migré, non vérifié par ce nouveau système (il sera vérifié manuellement
le 2026-11-27 comme prévu initialement, indépendamment de ce
changement).

## 7. Tests

Contrairement à la version précédente de ce spec (routine cloud
prompt-only), ce chantier touche désormais du VRAI CODE PYTHON
(`gold_news_reasoning_daily.py`) — la densité de tests `pytest` suit
celle déjà en place pour `indices_score.py`/`tests/gold_bot/`
(fonctions pures testées unitairement avec des cas limites explicites :
bornes exactes du clamp 3%/15%, chaque `close_reason` déclenché dans le
bon ordre de priorité, direction achat vs vente pour chaque formule,
franchissement de stop sur plusieurs jours avec le bon jour retenu,
dégradation propre si `yfinance` ou l'appel API échoue). Les deux
fonctions d'appel à l'API Claude sont testées avec un `requests.post`
mocké (même patron que les tests déjà existants pour les fonctions IA
de `indices_score.py`, si de tels tests existent — sinon, au minimum un
test par chemin de succès/échec). Validation finale par un run manuel
réel du workflow (`workflow_dispatch`) avant d'activer le cron, puis
désactivation de la routine cloud obsolète (§6).
