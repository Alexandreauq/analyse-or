# Gold bot — décision IA discrétionnaire sur l'entrée Fibonacci (FTMO)

## Contexte

Le moteur Fibonacci (`gold_bot/fibonacci_reversion.py`, compte FTMO réel)
calcule aujourd'hui lui-même le sens d'entrée (achat/vente) à partir d'un
niveau de retracement touché + un filtre de tendance OR(5j/20j). Depuis le
2026-11-06, un veto IA (`gold_bot/ai_shadow.py::shadow_judge_entry`, appelé
avec `real_decision=True`) est branché **pour de vrai** sur ce signal, en
mode fail-open : seul un verdict explicite `"opposerait"` bloque le trade ;
`None` (erreur/timeout/API absente) ou `"confirme"` laissent passer. Depuis
l'incident du 2026-11-10, une confiance `"faible"` réduit en plus la taille
de moitié (`LOW_CONFIDENCE_SIZE_FACTOR`).

**Ce veto ne peut jamais inverser le sens** — il ne fait qu'accepter ou
bloquer le sens déjà choisi par la règle. Le 2026-11-06, la règle avait
elle-même proposé une vente à contre-tendance pendant un marché en V (l'or
sortait d'un krach suivi d'un rebond de +5,5%) ; le filtre de tendance
20 jours seul était structurellement aveugle à cette forme ; le veto IA du
jour (log réel, `ai_shadow_log.jsonl`) n'a d'ailleurs rien pu juger ce
matin-là (échec de parsing JSON). Le trade a perdu 392,75$ et a déclenché
le coupe-circuit journalier.

Une idée explorée en conversation (2026-11-13) : faire tourner une IA en
continu (toutes les 60s, 24h/24) pour qu'elle détecte elle-même les
opportunités. Chiffré et **rejeté** : ≈10 080 appels/semaine, Opus 5.5 +
recherche web ≈ 0,0385$/appel ⇒ **≈ 388$/semaine**, alors que l'essentiel
de ces appels tomberait sur un marché qui ne fait rien. Le déclencheur
technique existant (gratuit, continu) est conservé comme réveil ; seul le
*rôle* de l'IA à ce réveil change.

**État du compte FTMO au moment de cette spec** : `kill_switch=true` depuis
le 2026-11-10 (plancher de perte **global** à 9% franchi, pas le
journalier) — vérifié en direct (`https://goldbot.fr/ftmo-status`,
`ftmo_guard_state.json`). Ce blocage est un problème **indépendant** de ce
chantier ; il n'empêche pas de coder/tester ce module, seulement la mise en
production réelle tant qu'il n'est pas résolu séparément.

## Objectif

Remplacer, uniquement pour `decide_and_act_fibonacci` (FTMO), le veto
binaire actuel par une vraie décision directionnelle : l'IA choisit
elle-même achat/vente/rien, **sans connaître le sens que la règle aurait
choisi**, et peut moduler le TP/SL de l'entrée. Décision utilisateur
explicite (2026-11-13) : déploiement direct en conditions réelles une fois
le compte débloqué, **sans** période d'observation en dry-run/fantôme
préalable — contrairement au précédent établi sur ce projet (veto shadow,
test news-reasoning actions), assumé en connaissance de cause.

## Portée

**Dans le périmètre :**
- Le point de décision d'entrée de `decide_and_act_fibonacci` (FTMO,
  XAUUSD) uniquement.
- L'IA décide : sens (achat/vente/rien) + multiplicateurs TP et SL
  appliqués aux distances que la règle aurait posées.
- Le réflexe existant "confiance faible → taille divisée par deux" est
  conservé, porté dans le nouveau module.

**Hors périmètre, explicitement :**
- `decide_and_act_ichimoku` et son veto IA réel actuel : **inchangés**,
  aucune modification de `ai_shadow.py` pour ce moteur.
- La gestion de position après ouverture (pas de ré-évaluation IA en cours
  de route ; TP/SL fixés à l'entrée ne sont pas revisités par ce module).
- `gold_bot/ftmo_guard.py` (coupe-circuit 4%/9%) : aucune modification,
  consulté uniquement en lecture pour donner du contexte à l'IA.
- `gold_bot/ai_news_watch.py` (prudence post-actualité) : aucune
  interaction, reste un garde-fou séparé, vérifié avant ce module dans
  `decide_and_act_fibonacci`.
- Résoudre le compte FTMO actuellement bloqué (`kill_switch=true` depuis le
  10/11) : chantier séparé.
- Tout déploiement multi-compte ou sur un autre symbole/moteur.

## Architecture

Nouveau module `gold_bot/ai_discretion.py`, fonction principale :

```python
def judge_fibonacci_entry(
    symbol: str, state: dict, candles: list[dict], *,
    baseline_stop_loss: float, baseline_take_profit: float, entry_price: float,
    ftmo_buffer: dict, log_path: str = DISCRETION_LOG_PATH,
    now: datetime | None = None,
) -> dict | None:
```

Appelée depuis `gold_bot/bot.py::decide_and_act_fibonacci`, **au même
emplacement** où `ai_shadow.shadow_judge_entry(..., real_decision=True)`
est appelé aujourd'hui (après tous les garde-fous existants : distance
entrée-stop, coupe-circuit, calcul de taille) — cet appel existant est
**retiré** pour Fibonacci (remplacé, pas gardé en double). L'appel
Ichimoku à `ai_shadow.shadow_judge_entry` reste inchangé dans son propre
chemin de code.

Le déclencheur reste **exactement** le calcul actuel de
`fibonacci_reversion.py` (niveau touché + filtre de tendance OR 5j/20j,
gratuit, toutes les 60s) : aucun changement sur la détection elle-même.

La règle continue de calculer, comme aujourd'hui, son propre
`direction`/`stop_loss`/`distance`/`take_profit` (`TARGET_R * distance`
pour la jambe partielle). Ce résultat sert à deux choses **seulement** :
1. Baseline pour les multiplicateurs TP/SL de l'IA (`baseline_stop_loss`,
   `baseline_take_profit` passés en paramètres).
2. Journalisé à côté de la vraie décision IA, pour comparaison a
   posteriori — gratuit, aucun appel supplémentaire.

**Ce `direction` n'est jamais transmis à l'IA dans le prompt**, sous
aucune forme, pour ne pas l'ancrer sur le sens déjà calculé par la règle.

## Contrat d'appel IA

- Modèle : `claude-opus-5-5`.
- Effort : `"high"` (monté d'un cran par rapport au veto actuel à
  `"medium"` — l'IA décide pour de vrai, plus seulement un avis journalisé).
- `web_search` conservé, `max_uses=2` (contexte actualité — c'est
  précisément ce qui a manqué lors de l'incident FOMC du 2026-10-28).
- `max_tokens=1536` (le veto actuel a dû monter de 1024 à 4096 après
  un incident de troncature ; prévoir large dès le départ plutôt que
  découvrir la même limite en conditions réelles).

**Entrée fournie dans le prompt** :
- Bougies récentes (même format que `ai_shadow._describe_candles`).
- `level`, `close`, `high`, `low`, `atr`, `trend_strength`,
  `trend_strength_short`, `hour_utc` (mêmes champs que `state` actuel).
- `ftmo_buffer` : marge restante avant les deux planchers FTMO (ex.
  `{"daily_pct_remaining": 0.021, "overall_pct_remaining": 0.045}`),
  calculée côté appelant à partir de `circuit_breaker.overall_floor`
  (propriété publique existante) et du plancher journalier courant — à
  exposer publiquement sur `FtmoGuard` si ce n'est pas déjà le cas (détail
  à trancher au moment du plan).
- **Jamais** le sens que la règle aurait choisi, ni `stop_loss`/
  `take_profit` de la règle en valeur absolue (seulement les distances
  normalisées servent de référence interne au bornage, côté code, pas
  dans le prompt).

**Sortie attendue**, bloc JSON en fin de réponse (même convention que
`ai_shadow._parse_verdict`, regex sur le dernier bloc `{...}`) :

```json
{"action": "achat | vente | rien", "confiance": "faible | moyenne | haute",
 "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "une phrase"}
```

Les multiplicateurs s'appliquent aux **distances** que la règle aurait
posées (`baseline_take_profit`/`baseline_stop_loss`), jamais à des prix
absolus inventés par l'IA — réduit la surface d'erreur possible.

## Garde-fous (appliqués après coup, indépendants de ce que dit l'IA)

- `tp_multiplicateur` borné à **[0.6, 2.0]**.
- `sl_multiplicateur` borné à **[0.7, 1.3]** (bande plus serrée : un stop
  plus large est un risque réel immédiat plus grand).
- Le R:R résultant (distance TP bornée / distance SL bornée) doit rester
  **≥ 1.0** après bornage. Si la combinaison choisie par l'IA tombe
  sous ce seuil malgré le bornage individuel des deux multiplicateurs,
  **le trade est annulé** (pas d'ajustement automatique supplémentaire),
  journalisé comme `"rejete_garde_fou_rr"`.
- Pas de seuil de confiance qui bloque l'exécution — une confiance
  `"faible"` peut quand même agir (décision utilisateur, approche "override
  complet" sans palier de confiance sur l'action elle-même).
- **Conservé** : confiance `"faible"` → taille divisée par deux
  (`LOW_CONFIDENCE_SIZE_FACTOR = 0.5`, porté tel quel depuis
  `ai_shadow.py`). Mécanisme indépendant des multiplicateurs TP/SL, ne
  les remplace pas.

## Gestion d'erreur (fail-closed)

Toute défaillance de l'appel IA (`ANTHROPIC_API_KEY` absente, timeout,
erreur API, JSON de verdict absent/invalide) ⇒ **aucun trade ce cycle**.
Pas de repli sur le sens que la règle aurait choisi — un tel repli viderait
le sens même de ce chantier (ne plus faire confiance aveuglément au sens
de la règle). Toujours journalisé (état brut + erreur), jamais
d'exception propagée hors de `judge_fibonacci_entry` (même garantie que
`shadow_judge_entry`).

## Cooldown

Cooldown court, **4 heures**, par `(symbol, level arrondi à 2 décimales)`
— volontairement plus court que les 24h de `ai_shadow.py` (qui existaient
pour une raison de coût qui ne s'applique plus ici, vu la rareté naturelle
du déclencheur, ~4-10x/semaine). Ce cooldown-ci est anti-rafale : éviter un
enchaînement rapide de décisions si le marché touche plusieurs niveaux
coup sur coup dans une séance agitée, pas une mesure de coût.

## Journalisation

Nouveau fichier `gold_bot/ai_discretion_log.jsonl` (gitignoré, même
convention que `ai_shadow_log.jsonl`/`decisions_log.jsonl`). Chaque entrée :
`timestamp`, `symbol`, `state` (snapshot), `regle_contrefactuelle`
(`{"direction", "stop_loss", "take_profit"}` — ce que la règle aurait fait,
jamais exécuté, pour comparaison a posteriori), `raw_text`, `verdict` brut
de l'IA, multiplicateurs appliqués après bornage, R:R final,
`action_reelle` (`"execute"`, `"rejete_garde_fou_rr"`, `"aucune_ia"`,
`"erreur"`), et `error` le cas échéant.

## Intégration avec l'existant

- `ftmo_guard.py` : aucune modification. Lu en entrée (buffer) uniquement.
- `ai_shadow.py` : l'appel pour Fibonacci est retiré de
  `decide_and_act_fibonacci` ; le fichier et son usage pour Ichimoku restent
  identiques, aucune modification du fichier lui-même n'est requise par ce
  chantier (sauf si le plan juge plus propre d'extraire
  `LOW_CONFIDENCE_SIZE_FACTOR`/`_describe_candles` en utilitaire partagé —
  détail d'implémentation, à trancher au plan).
- `ai_news_watch.py` : aucune interaction ; reste vérifié avant, comme
  aujourd'hui.

## Déploiement

Indépendant du déblocage du compte FTMO (chantier séparé). Ce module peut
être codé et testé (mock complet, aucun appel réseau réel) avant, pendant
ou après ce déblocage. **Décision utilisateur explicite et documentée** :
une fois le compte de nouveau actif, mise en production **directe**, sans
période d'observation dry-run/fantôme séparée — contrairement au
précédent établi sur ce projet pour les décisions IA non backtestables.

## Tests requis (TDD)

- Mock complet de `anthropic.Anthropic` (même patron que
  `tests/gold_bot/test_ai_shadow.py`/`test_ichimoku_reversion.py`) : aucun
  appel réseau réel dans la suite.
- Bornage TP/SL : table de cas couvrant les bornes et leur franchissement
  dans les deux sens (`tp_multiplicateur`/`sl_multiplicateur` en dehors de
  [0.6,2.0]/[0.7,1.3]), et le rejet R:R < 1.0 après bornage.
- Fail-closed : `ANTHROPIC_API_KEY` absente, timeout, erreur API, JSON de
  verdict absent/invalide ⇒ aucun trade, jamais d'exception propagée.
- **Garde contre l'ancrage** : un test qui vérifie explicitement que le
  texte du prompt envoyé à l'IA ne contient JAMAIS le `direction` calculé
  par la règle, ni ses `stop_loss`/`take_profit` en valeur absolue.
- Cooldown : deux déclenchements rapprochés sur le même
  `(symbol, level)` dans la fenêtre de 4h ne déclenchent qu'un seul appel
  réel.
- Confiance faible → taille divisée par deux : comportement porté et
  testé à l'identique de l'existant.
- Journalisation : forme de chaque entrée (`action_reelle` correctement
  renseigné dans les 4 cas).
