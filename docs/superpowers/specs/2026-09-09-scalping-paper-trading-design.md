# Suivi en paper-trading des signaux Scalping Or — design

## Contexte et contrainte fondatrice

Le module Scalping Or (`docs/scalping.js`, mergé le 2026-09-09) affiche un
signal Achat/Vente/Neutre en direct dans le navigateur, mais n'a jamais
été évalué en conditions réelles — zéro historique de performance. Avant
d'envisager quoi que ce soit d'automatisé sur de l'argent réel (explicitement
hors de question à ce stade — voir l'échange qui précède ce design), le but
de ce chantier est de faire tourner le moteur de signal en paper-trading
(argent fictif) pendant 7 jours, sans intervention manuelle, pour obtenir
un vrai bilan chiffré : combien de signaux, quel taux de réussite, quel
gain/perte cumulé.

Contrainte architecturale de départ : le module Scalping est délibérément
client-side-only et ne "vit" (ne poll l'API, ne recalcule un signal) que
pendant qu'un onglet de navigateur est ouvert et visible. Rien dans
l'architecture actuelle ne peut suivre des signaux sans surveillance
pendant une semaine — il faut une nouvelle brique automatisée.

## Portée

**Dans le périmètre (v1, essai de 7 jours) :**
- Un nouveau workflow GitHub Actions tournant toutes les 5 minutes
  (plancher technique de GitHub Actions) pendant la durée de l'essai.
- Un script Node qui réutilise **verbatim** `fetchGoldCandles` et
  `computeSignal` de `docs/scalping.js` (via `require`) — zéro
  réécriture, zéro risque de divergence entre ce que le navigateur montre
  et ce que le suivi évalue.
- Un moteur de paper-trading à une seule position ouverte à la fois :
  ouverture sur nouveau signal, clôture sur SL touché / TP touché / durée
  max de 2h dépassée / fin de l'essai (filet de sécurité).
- Un fichier de résultats JSON (`docs/scalping_tracking.json`), committé
  par le workflow.
- Une nouvelle section "Suivi" dans l'écran Or, consultable à tout moment
  pendant et après l'essai.
- Un rapport de synthèse en fin d'essai (conversation, pas un livrable
  code).

**Hors périmètre, explicitement :**
- Aucun trading réel, aucune intégration broker/exchange — paper-trading
  uniquement.
- Pas de simulation multi-positions/portefeuille — une position à la fois.
- Pas de dimensionnement de position (taille, levier) — suivi en prix brut
  ($ et %) uniquement.
- Pas de prolongation au-delà de 7 jours sans une décision explicite de
  l'utilisateur.
- Le moteur de signal du navigateur (`docs/scalping.js`) n'est pas modifié
  par ce chantier — seulement réutilisé.

## Architecture

### Workflow — `.github/workflows/scalping_tracker.yml`

- `schedule: cron: "*/5 * * * *"`, plus `workflow_dispatch: {}` pour les
  tests manuels.
- `concurrency: { group: scalping-tracker, cancel-in-progress: false }` —
  même logique que `indices.yml`, évite deux runs concurrents sur le même
  fichier de suivi.
- `permissions: contents: write` (commit du JSON) et `actions: write`
  (nécessaire pour l'auto-désactivation en fin d'essai, voir plus bas —
  à vérifier en implémentation ; si le token n'a pas ce droit, l'échec de
  cette étape est non-bloquant, voir "Risques connus").
- Étapes : checkout, `actions/setup-node` (Node 20), `node
  scalping_tracker.js` avec `TWELVE_DATA_API_KEY` injecté depuis
  `secrets.TWELVE_DATA_API_KEY` (nouveau secret à créer — distinct de la
  clé en dur côté navigateur), puis commit + `git pull --rebase
  --autostash` + push du JSON modifié (même idiome que `indices.yml`).

### Script — `scalping_tracker.js` (racine du repo, à côté de
`gold_score.py`/`indices_score.py`)

Pseudocode du run :

1. Charger `docs/scalping_tracking.json` (le créer avec `trial_start:
   null, trial_ended: false, positions: []` s'il n'existe pas).
2. Si `trial_start` est `null`, le fixer à maintenant (ISO 8601) — ne
   jamais l'écraser ensuite.
3. `trialElapsed = now - trial_start >= 7 jours`.
4. `fetchGoldCandles(apiKey)` — en cas d'échec (réseau, quota, réponse
   invalide, cf. les validations déjà présentes dans `docs/scalping.js`),
   logger l'erreur et **arrêter le run sans modifier le fichier** ; le
   prochain run (5 min plus tard) réessaiera. Aucune donnée périmée ou
   fictive n'est utilisée pour décider d'un mouvement de position.
5. **S'il y a une position ouverte :** examiner le high/low de toutes les
   bougies dont l'horodatage est postérieur à `entry_time` (pas
   seulement les bougies "nouvelles depuis le dernier run" — `outputsize=90`
   couvre largement l'intervalle entre deux runs de 5 min, donc aucune
   bougie n'est manquée même si un run est retardé).
   - Achat : SL touché si un `low` ≤ `stop_loss` ; TP touché si un `high`
     ≥ `take_profit`.
   - Vente : symétrique (`high` ≥ stop_loss cassé à la hausse ; `low` ≤
     take_profit).
   - **Règle de désambiguïsation documentée** : si le SL et le TP sont
     tous les deux techniquement touchés dans la fenêtre examinée (une
     bougie a pu franchir les deux), on suppose le **SL touché en
     premier** — hypothèse prudente standard en backtesting, qui évite de
     surestimer la performance. Ce n'est pas un bug si un résultat
     "aurait pu" être un gain avec l'autre ordre.
   - Sinon, si `now - entry_time ≥ 2h` : clôture forcée au dernier prix
     connu, `close_reason: "max_duration"`.
   - Sinon, si `trialElapsed` : clôture forcée au dernier prix connu,
     `close_reason: "trial_end"` (filet de sécurité — ne devrait quasiment
     jamais se déclencher vu le plafond de 2h).
   - Sinon : rien, la position reste ouverte.
   - À la clôture (quelle que soit la raison) : `return_usd = close_price
     - entry_price` (signe inversé pour une vente : `entry_price -
     close_price`), `return_pct = return_usd / entry_price * 100`.
6. **S'il n'y a pas de position ouverte et que `!trialElapsed` :** appeler
   `computeSignal(candles)`. Si `status` est `achat`/`vente`, ouvrir une
   nouvelle position (`entry_price = signal.price`, `stop_loss =
   signal.stopLoss`, `take_profit = signal.takeProfit`, `trend_at_entry =
   signal.trend`, `pattern_at_entry = signal.pattern?.name ?? null`).
7. **Si `trialElapsed` et qu'il n'y a plus de position ouverte :** marquer
   `trial_ended: true` dans le JSON, et tenter (best-effort, non-bloquant
   si ça échoue) de désactiver le workflow lui-même via `gh workflow
   disable scalping_tracker.yml` pour arrêter les runs futurs — sinon le
   cron continuerait à se déclencher indéfiniment sans plus rien faire
   d'utile (coût négligeable mais pas nul).
8. Sauvegarder le JSON, commit, push.

### Schéma — `docs/scalping_tracking.json`

```json
{
  "trial_start": "2026-09-09T14:32:00Z",
  "trial_ended": false,
  "positions": [
    {
      "id": "scalp-2026-09-09T14:35:00Z",
      "direction": "achat",
      "status": "closed",
      "entry_time": "2026-09-09T14:35:00Z",
      "entry_price": 3452.10,
      "stop_loss": 3450.60,
      "take_profit": 3454.85,
      "trend_at_entry": "baissier",
      "pattern_at_entry": "Marteau",
      "close_time": "2026-09-09T15:10:00Z",
      "close_price": 3454.85,
      "close_reason": "tp_hit",
      "return_usd": 2.75,
      "return_pct": 0.080
    }
  ]
}
```

### UI — section "Suivi"

Troisième bouton dans `.or-tabbar`, à côté de "Long terme"/"Scalping" :
"Suivi". Charge `docs/scalping_tracking.json` à la demande (même logique
paresseuse que le reste du site). Affiche :
- Un résumé : nombre de positions, taux de réussite (positions clôturées
  avec `return_usd > 0` / total clôturé), rendement % moyen par position,
  gain/perte cumulé en $.
- La liste des positions (direction, entrée, sortie, raison de clôture,
  résultat), la position en cours distinguée visuellement si l'essai
  tourne encore.
- Un bandeau "Essai terminé" si `trial_ended` est `true`.

## Tests

Contrairement au module `scalping.js` (jamais réellement exécuté avec
Node dans ce sandbox), `scalping_tracker.js` tournera pour de vrai sur les
runners GitHub Actions (Node y est préinstallé). Le plan d'implémentation
doit inclure un fichier de test dédié (`scalping_tracker.test.js`,
exécutable localement si Node devient disponible, et en tout cas
exécutable par les runners CI) couvrant la logique de décision de cycle
de vie d'une position (conditions d'ouverture/clôture, règle de
désambiguïsation SL/TP, calcul du résultat) sur des données de bougies
synthétiques — un vrai cycle TDD, contrairement au reste de ce module.

## Gestion des erreurs

- Échec `fetchGoldCandles` → run interrompu proprement, aucune écriture,
  erreur loggée dans la sortie du workflow.
- Push en conflit avec un autre commit concurrent → `git pull --rebase
  --autostash` avant push, comme `indices.yml`.
- Échec de l'auto-désactivation du workflow (permissions insuffisantes) →
  non-bloquant, signalé dans le rapport de fin d'essai à l'utilisateur
  comme une action manuelle de repli ("pense à désactiver/supprimer le
  workflow").

## Risques connus, assumés

- Granularité de 5 minutes : un mouvement qui toucherait puis repasserait
  un niveau entre deux runs pourrait ne pas être détecté au moment exact —
  atténué par l'examen du high/low de **toutes** les bougies depuis
  l'entrée à chaque run (pas seulement le dernier point), donc un
  décalage de timing du cron ne fait pas manquer un mouvement, seulement
  retarder sa détection de quelques minutes.
- Retards de déclenchement du cron GitHub Actions (déjà observés sur ce
  projet, jusqu'à plusieurs heures certains jours) — un run retardé
  détecte quand même correctement les niveaux touchés depuis l'entrée ;
  seul le déclenchement de la clôture à 2h pile peut glisser de quelques
  minutes.
- Quota Twelve Data : ~288 appels/jour à 5 min d'intervalle, large marge
  sous 800/jour.
- La désactivation automatique du workflow est du best-effort, pas une
  garantie — voir "Gestion des erreurs".
