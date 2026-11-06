# Paper-trading quotidien — test prospectif IA sur l'or — Implementation Plan

> **For agentic workers:** Ce plan N'EST PAS un plan de code TDD classique — aucun fichier `gold_bot/*.py` n'est créé ou modifié (voir le spec, section 2, "Hors scope"). Comme la version hebdomadaire déjà en place qu'il remplace, ce chantier modifie EN PLACE une routine cloud planifiée existante (`trig_01KzB6rZrwxUW1yoike8KLrP`) dont le PROMPT fait tout le travail — aucun code Python nouveau dans le dépôt. Exécution directe par le contrôleur (pas de subagent-driven-development — rien à committer/diffuser/relire en git pour la routine elle-même), précédée de tests manuels réels du nouveau prompt avant de le mettre en production. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le pari directionnel hebdomadaire unique par une vraie gestion active de positions simulées sur XAUUSD (ouverture, réévaluation quotidienne, clôture), avec un repère buy&hold, pour tester le raisonnement IA comme s'il était réellement utilisé.

**Architecture:** Même routine cloud planifiée (`trig_01KzB6rZrwxUW1yoike8KLrP`), mise à jour en place : nouveau cron (quotidien, lundi-vendredi), nouveau prompt qui lit/écrit `docs/gold_news_reasoning_tracking.json` au lieu de produire un nouveau fichier par semaine. Les données de prix (clôtures/plus-hauts/plus-bas) viennent de `yfinance`, jamais de `WebSearch` — `WebSearch` reste réservé au raisonnement qualitatif (actualité).

**Tech Stack:** Routine cloud planifiée (`RemoteTrigger`), `yfinance` (déjà une dépendance du dépôt, `requirements.txt:2`), aucun code Python nouveau dans le dépôt.

**Spec:** `docs/superpowers/specs/2026-11-06-gold-news-reasoning-daily-paper-trading-design.md`

## Global Constraints

- Jamais d'exécution réelle, jamais de compte réel, aucun lien avec `gold_bot/` ou FTMO (spec §2).
- Une seule position ouverte à la fois sur XAUUSD.
- Les prix (clôtures, plus-hauts, plus-bas) viennent TOUJOURS de `yfinance` (ticker `XAUUSD=X`, repli `GC=F`) — `WebSearch` n'est jamais utilisé pour une valeur de prix qui déclenche une décision de clôture, uniquement pour l'actualité qualitative (spec §4.1).
- Stop de sécurité fixé par l'IA à l'ouverture, borné à une distance de 3% à 15% du prix d'entrée (spec §4.2) — jamais modifié après l'ouverture.
- `max_close_date` = `entry_date` + 21 jours calendaires (3 semaines), fixée à l'ouverture (spec §3).
- `close_reason` ∈ `"stop_securite" | "cloture_anticipee_ia" | "delai_max"`, vérifiés dans cet ordre de priorité à chaque réévaluation (spec §4.3).
- `buyhold_return_pct` est TOUJOURS brut (jamais ajusté par `direction`) — c'est le repère passif, pas une version retournée du pari (spec §3).
- Schéma exact de `docs/gold_news_reasoning_tracking.json` : voir spec §3 (repris intégralement dans le prompt ci-dessous).
- Un seul commit par exécution, sur `docs/gold_news_reasoning_tracking.json` uniquement (jamais `git add -A`), aucun commit si rien n'a changé (spec §4.4).

---

## Le prompt complet de la routine

Ce texte est utilisé tel quel à la Task 1 (test manuel) et à la Task 2 (mise à jour de la routine) — une seule source de vérité, pas de divergence entre le test et la production.

```
Tu es un analyste qui raisonne sur l'or (XAUUSD) a partir de l'actualite
recente et du comportement du prix/volume -- PAS de formule mecanique,
un jugement contextuel comme un trader humain le ferait. Ceci est un
test prospectif methodologique en paper-trading (voir
docs/superpowers/specs/2026-11-06-gold-news-reasoning-daily-paper-trading-design.md) :
aucune position n'est reelle, aucun compte n'est touche, seul le
fichier docs/gold_news_reasoning_tracking.json est modifie.

ETAPE 1 -- Donnees de prix (QUANTITATIF, toujours via yfinance, JAMAIS
via WebSearch pour un prix, un plus-haut ou un plus-bas) :
Execute via Bash un script Python qui recupere les cours journaliers
(clotures + plus-hauts + plus-bas) de XAUUSD :

    import yfinance as yf
    data = yf.download("XAUUSD=X", period="2mo", interval="1d")

Si cette requete echoue ou renvoie une serie vide, reessaie avec le
ticker "GC=F" (futures COMEX, proxy correct). Si yfinance n'est pas
installe dans l'environnement, installe-le d'abord
(pip install -q yfinance) puis reessaie. Garde ces donnees pour les
etapes suivantes -- elles sont la SEULE source pour toute valeur de
prix, plus-haut ou plus-bas utilisee dans ce qui suit.

ETAPE 2 -- Lire l'etat actuel :
Lis docs/gold_news_reasoning_tracking.json (s'il n'existe pas encore,
pars de la structure vide {"positions": []}). Determine s'il existe
une position avec "status": "open" (il ne peut y en avoir qu'une seule
a la fois).

ETAPE 3A -- SI une position est ouverte, reevalue-la dans CET ORDRE DE
PRIORITE (arrete-toi a la premiere condition qui s'applique) :

  1. Verifie le stop : pour chaque jour de bourse ecoule depuis la
     derniere entree de "journal" de cette position (ou depuis
     entry_date s'il n'y a pas encore de journal), si le PLUS BAS du
     jour (direction "achat") ou le PLUS HAUT du jour (direction
     "vente") a franchi safety_stop_price : cloture la position --
     status="closed", close_date=ce jour (le premier jour ou le
     franchissement a eu lieu, pas forcement aujourd'hui), close_price
     = safety_stop_price (exactement la valeur du stop, PAS le cours
     de cloture reel de ce jour), close_reason="stop_securite". Va a
     l'ETAPE 4.
  2. Sinon, cherche via WebSearch l'actualite macro/geopolitique la
     plus pertinente pour l'or depuis l'ouverture (ou la derniere
     reevaluation), et decide : GARDER la position, ou la CLOTURER par
     anticipation (prise de profit ou retournement de these). Si
     CLOTURER : status="closed", close_date=aujourd'hui,
     close_price=derniere cloture connue (yfinance),
     close_reason="cloture_anticipee_ia". Va a l'ETAPE 4. Si GARDER :
     ajoute une entree au tableau "journal" de cette position
     ({"date": aujourd'hui, "price": derniere cloture connue,
     "decision": "garder", "raison": "une phrase"}), ne touche a rien
     d'autre sur cette position, va a l'ETAPE 5.
  3. Sinon, si aujourd'hui >= max_close_date : status="closed",
     close_date=aujourd'hui, close_price=derniere cloture connue,
     close_reason="delai_max". Va a l'ETAPE 4.

ETAPE 3B -- SINON (aucune position ouverte), decide d'une nouvelle
entree. Cherche via WebSearch l'actualite macro/geopolitique la plus
pertinente pour l'or recemment (banques centrales, inflation, risque
geopolitique, flux ETF). Fais un appel directionnel sur l'horizon des
3 prochaines semaines : haussier, baissier, ou neutre (nuances
possibles : leger_haussier, neutre_prudent, etc., comme un vrai
jugement nuance), avec une confiance (faible/moyenne/haute) et une
raison en une ou deux phrases.

  - Si neutre (ou toute nuance neutre) : n'ouvre aucune position, ne
    modifie rien au fichier aujourd'hui.
  - Si haussier ou baissier (y compris nuance) : ouvre une nouvelle
    position avec EXACTEMENT ces champs :
      "id": "XAUUSD-{date du jour, format YYYY-MM-DD}"
      "status": "open"
      "entry_date": date du jour (YYYY-MM-DD)
      "entry_price": derniere cloture connue (donnees yfinance de l'ETAPE 1)
      "direction": "achat" si haussier/nuance haussiere, "vente" si baissier/nuance baissiere
      "safety_stop_price": fixe toi-meme, a une distance de entry_price
        COMPRISE ENTRE 3% ET 15% (valeur absolue) -- si ton jugement
        naturel sort de cette fourchette, ramene-le a la borne la plus
        proche (3% ou 15%) plutot que de l'appliquer tel quel. Pour un
        achat, safety_stop_price < entry_price. Pour une vente,
        safety_stop_price > entry_price.
      "safety_stop_pct": (safety_stop_price / entry_price - 1) pour un
        achat, (entry_price / safety_stop_price - 1) pour une vente --
        toujours une valeur NEGATIVE.
      "confiance": ton jugement (faible/moyenne/haute)
      "raison_ouverture": une ou deux phrases
      "max_close_date": date du jour + 21 jours calendaires
      "close_date": null, "close_price": null, "close_reason": null,
      "return_pct": null, "buyhold_price_at_close": null,
      "buyhold_return_pct": null, "journal": []

ETAPE 4 -- SI une position a ete cloturee a l'ETAPE 3A (n'importe
laquelle des 3 raisons) :
  - "return_pct" = (close_price / entry_price - 1) pour un achat,
    (entry_price / close_price - 1) pour une vente.
  - "buyhold_price_at_close" = la cloture REELLE du marche (donnees
    yfinance) a close_date -- ATTENTION, si close_reason est
    "stop_securite", close_price vaut la valeur du stop (pas le cours
    reel de ce jour), donc buyhold_price_at_close est DIFFERENT de
    close_price dans ce cas precis. Dans les 2 autres cas
    (cloture_anticipee_ia, delai_max), close_price EST deja la cloture
    reelle de ce jour, donc buyhold_price_at_close == close_price.
  - "buyhold_return_pct" = (buyhold_price_at_close / entry_price - 1)
    -- TOUJOURS BRUT, jamais ajuste par direction (repere passif, pas
    une version retournee du pari).
  - Apres avoir cloture, repasse IMMEDIATEMENT a l'ETAPE 3B pour voir
    si une nouvelle position doit s'ouvrir aujourd'hui meme (ne pas
    attendre le jour ouvre suivant).

ETAPE 5 -- Ecriture et commit :
Reecris le fichier ENTIER docs/gold_news_reasoning_tracking.json avec
le tableau "positions" mis a jour (format exact :
{"positions": [...]}). Si le seul resultat de ce passage est "aucune
position ouverte ET appel neutre" (donc aucun changement au fichier) :
NE FAIS AUCUN commit. Sinon : git add docs/gold_news_reasoning_tracking.json
(jamais git add -A), commit avec un message court type
"chore(gold-bot): suivi quotidien test prospectif IA or", push sur
main. N'ecris et ne commite AUCUN autre fichier.
```

---

### Task 1 : Valider le prompt par 2 tests manuels réels

**Files:** Aucun fichier committé dans le dépôt réel pour les tests eux-mêmes — scripts jetables dans le scratchpad, comme pour la version hebdomadaire. Un fichier de suivi JETABLE est utilisé pour le test de l'ÉTAPE 3A (voir Step 3 ci-dessous), jamais le vrai `docs/gold_news_reasoning_tracking.json`.

- [ ] **Step 1 : Vérifier que `yfinance` fonctionne pour XAUUSD dans un environnement proche de celui de la routine**

Lancer localement (ou sur le VPS/l'environnement disponible) :

```bash
python3 -c "
import yfinance as yf
data = yf.download('XAUUSD=X', period='2mo', interval='1d')
print(data.tail(10))
print('lignes:', len(data))
"
```

Si la série est vide ou lève une exception, retenter avec `'GC=F'` à la
place de `'XAUUSD=X'`. Noter lequel des deux tickers a fonctionné — le
prompt ci-dessus accepte déjà les deux dans cet ordre, donc aucun
changement de prompt n'est nécessaire quel que soit le résultat, mais
le consigner permet de savoir à quoi s'attendre dans les logs de la
future routine.

- [ ] **Step 2 : Test manuel réel de l'ÉTAPE 3B (ouverture) — scénario "aucune position ouverte"**

Appel direct à l'API Claude (même patron que le test manuel qui a
trouvé le bug `max_tokens` le 2026-11-05, et que le test du prompt
hebdomadaire le 2026-11-06) avec le prompt complet ci-dessus, sur un
fichier de suivi initialement vide (`{"positions": []}`) écrit dans le
scratchpad. Utiliser `web_search` comme outil (le prompt s'appuie sur
une vraie recherche d'actualité) et laisser l'appel utiliser Bash pour
le fetch `yfinance` réel (ou, si l'environnement de test manuel n'a pas
d'accès Bash réel pour l'IA, exécuter soi-même le fetch yfinance de
l'ÉTAPE 1 et fournir son résultat dans le contexte du test, en notant
clairement cette différence par rapport à l'exécution réelle de la
routine qui, elle, a Bash).

Vérifier :
- Le résultat écrit respecte le schéma EXACT de l'ÉTAPE 3B (tous les
  champs présents, `null` pour les champs de clôture).
- Si l'appel est haussier/baissier : `safety_stop_price` est bien du
  bon côté de `entry_price` selon `direction`, et sa distance
  (`abs(safety_stop_pct)`) est bien comprise entre 3% et 15%.
- `entry_price` correspond bien à une clôture réelle récente de
  l'ÉTAPE 1 (pas une valeur approximative inventée par le modèle).
- Si l'appel est neutre : aucune position n'est créée, le fichier
  reste `{"positions": []}`.

Si le JSON final n'est pas exploitable (même défaut que le veto avant
son correctif `max_tokens` du 2026-11-05), ajuster le prompt (plus
concis, ou augmenter le budget si la routine cloud le permet) et
refaire ce test jusqu'à au moins 2 essais réussis de suite.

- [ ] **Step 3 : Test manuel réel de l'ÉTAPE 3A (réévaluation/clôture) — scénario "position déjà ouverte"**

Ce scénario ne peut pas encore être testé sur une vraie position (il
n'en existe aucune à ce stade). Créer un fichier de suivi JETABLE dans
le scratchpad avec UNE position `"open"` fictive mais réaliste, par
exemple :

```json
{"positions": [{
  "id": "XAUUSD-2026-10-20", "status": "open",
  "entry_date": "2026-10-20", "entry_price": 4050.0, "direction": "achat",
  "safety_stop_price": 3900.0, "safety_stop_pct": -0.037,
  "confiance": "moyenne", "raison_ouverture": "test manuel",
  "max_close_date": "2026-11-10",
  "close_date": null, "close_price": null, "close_reason": null,
  "return_pct": null, "buyhold_price_at_close": null, "buyhold_return_pct": null,
  "journal": []
}]}
```

(Choisir des dates et un `safety_stop_price` tels qu'au moins un des 3
cas de clôture soit probable avec les vraies données yfinance
récupérées au Step 1 — par exemple si le plus bas réel depuis le
20/10 est descendu sous 3900, le cas `stop_securite` doit se
déclencher ; sinon, re-exécuter ce test avec `max_close_date` dans le
passé pour forcer le cas `delai_max` et valider au moins le calcul
arithmétique.)

Refaire le même appel API avec le prompt complet, pointé sur ce fichier
jetable. Vérifier :
- La priorité est respectée (stop vérifié avant la réévaluation
  qualitative, elle-même avant le délai max).
- Si clôture par `stop_securite` : `close_price == safety_stop_price`
  exactement (pas le cours réel du jour).
- `return_pct` calculé correctement selon la formule du prompt
  (vérifier à la main avec une calculatrice).
- `buyhold_price_at_close` est la vraie clôture du marché à
  `close_date` (différente de `close_price` si `close_reason` est
  `stop_securite`), et `buyhold_return_pct` est BRUT (pas inversé si
  `direction` est `"vente"`).
- Si le résultat est "garder" : une entrée a été ajoutée à `journal`
  avec les 4 champs attendus, et aucun autre champ de la position n'a
  changé.

- [ ] **Step 4 : Consigner les résultats**

Noter dans un message de suivi (pas de fichier committé) : quel ticker
yfinance a fonctionné, si les 2 tests ont produit un JSON exploitable
du premier coup ou après ajustement du prompt, et les valeurs
observées pour chaque vérification ci-dessus.

---

### Task 2 : Mettre à jour la routine existante en place

**Outils** : `RemoteTrigger` (charger via `ToolSearch select:RemoteTrigger` avant l'appel).

- [ ] **Step 1 : Relire l'état actuel de la routine**

`{action: "get", trigger_id: "trig_01KzB6rZrwxUW1yoike8KLrP"}` — confirmer qu'elle existe toujours avec l'ancien cron `0 7 * * 1` et l'ancien prompt, avant de la modifier (éviter d'écraser un changement externe inattendu).

- [ ] **Step 2 : Mettre à jour le cron et le prompt**

`{action: "update", trigger_id: "trig_01KzB6rZrwxUW1yoike8KLrP", body: {...}}` avec :
- `cron_expression`: `"0 7 * * 1-5"` (inchangé en heure, étendu du lundi seul à lundi-vendredi).
- Le prompt complet de la section "Le prompt complet de la routine" ci-dessus, inséré dans `job_config.ccr.events[0].data.message.content` (même structure d'événement que l'actuelle — voir le corps JSON de la routine existante, section `job_config.ccr`).
- `job_config.ccr.session_context.allowed_tools` : inchangé, `["Bash", "Read", "Write", "Glob", "Grep", "WebSearch"]` (déjà suffisant : `Bash` pour `yfinance`/`git`, `WebSearch` pour l'actualité).
- `job_config.ccr.session_context.model` : inchangé, `"claude-sonnet-5-5"`.
- `name` : inchangé, `"Gold bot - test prospectif IA raisonnement actu/prix (or)"`.

- [ ] **Step 3 : Confirmer la mise à jour**

`{action: "get", trigger_id: "trig_01KzB6rZrwxUW1yoike8KLrP"}` — vérifier que `cron_expression` et le prompt dans `derived_state.prompt` reflètent bien les nouvelles valeurs, et que `enabled` est toujours `true`.

---

### Task 3 : Lancer une première exécution réelle de bout en bout

**Outils** : `RemoteTrigger`.

- [ ] **Step 1 : Lancer une exécution manuelle**

`{action: "run", trigger_id: "trig_01KzB6rZrwxUW1yoike8KLrP"}`.

- [ ] **Step 2 : Lire le log de l'exécution**

`{action: "list_runs", trigger_id: "trig_01KzB6rZrwxUW1yoike8KLrP"}` puis `{action: "get_run_log", session_id: "..."}` sur la session la plus récente. Confirmer :
- `yfinance` a bien été utilisé pour le prix (chercher l'appel Bash correspondant dans le log), pas un prix approximatif sorti d'un résultat `WebSearch`.
- Une décision a bien été prise (ouverture ou neutre, puisqu'aucune position n'existe encore à ce stade — Task 1 n'a utilisé que des fichiers jetables, le vrai fichier `docs/gold_news_reasoning_tracking.json` n'existe pas encore en prod).
- Aucune erreur ni échec silencieux dans le log.

- [ ] **Step 3 : Vérifier le résultat committé**

```bash
git pull
cat docs/gold_news_reasoning_tracking.json
```

Confirmer que le fichier correspond exactement au schéma de la section
3 du spec, que le commit est unique et ne touche que ce fichier
(`git show --stat HEAD`), et que le message de commit est conforme.

---

## Hors scope de ce plan (à traiter séparément)

- Cadence supra-quotidienne (intraday, à chaque cycle du vrai bot) — explicitement différée par l'utilisateur à une itération future (spec §2, §5).
- Toute décision de construire un moteur de trading réel autour de ce raisonnement — uniquement après plusieurs semaines de positions simulées clôturées, même exigence que pour le test actions.
- Track 1 (fenêtre de blackout étendue par l'IA sur le compte FTMO réel) — chantier séparé, déjà terminé et déployé (`docs/superpowers/plans/2026-11-06-gold-bot-ai-extended-news-blackout.md`).
- Migration du pari déjà enregistré par l'ancien système (`docs/superpowers/experiments/2026-11-06-gold-news-reasoning-test.json`) — reste en l'état comme archive, vérifié manuellement le 2026-11-27 indépendamment de ce changement (spec §6).
