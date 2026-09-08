# Suivi de performance des signaux — design

## Contexte et objectif

Le volet Indices (CAC40+DAX, `indices_score.py`) émet un signal "entrée"
(score composite favorable + cours proche du repère d'entrée) et un
repère de sortie, mais rien ne mesure aujourd'hui si suivre ces signaux
"à la lettre" aurait réellement été rentable pour un investisseur lambda.
Ce chantier ajoute un suivi de positions fictives : chaque signal
"entrée" nouvellement déclenché ouvre une position simulée, suivie
jusqu'à sa clôture, avec deux benchmarks pour juger si le signal apporte
une vraie valeur ajoutée.

**Contrainte fondatrice :** `indices_history.json` ne contient que
`{date, ticker, composite}` depuis 3 jours — aucun prix ni type d'alerte
n'a été historisé jusqu'ici. Un vrai backtest du passé est donc
impossible ; ce chantier est un suivi **prospectif**, qui commence à
compter à partir de sa mise en production.

## Portée

**Dans le périmètre :**
- Ouverture automatique d'une position fictive à chaque signal "entrée"
  nouvellement déclenché (une seule position ouverte à la fois par
  ticker).
- Clôture automatique sur objectif atteint, stop-loss (-20%), ou délai
  max (6 mois) — dans cet ordre de priorité.
- Deux benchmarks par position clôturée :
  1. **Indice** (CAC40 ou DAX selon le ticker) sur la même période
     réellement tenue.
  2. **"Tenir les 6 mois pleins"** — le même titre, sans sortie
     anticipée, résolu à la date `entry_date + 6 mois` même si la
     position réelle a clôturé plus tôt.
- Une page/section sur le site présentant les stats agrégées et le
  détail des positions.

**Hors périmètre (v1) :**
- Backtest du passé (données absentes).
- Email récapitulatif périodique (uniquement affichage site pour
  l'instant).
- Gestion active d'un ticker qui sort de l'indice en cours de position
  ouverte (voir "Cas limites").
- Dimensionnement de position en euros/nombre d'actions — uniquement du
  rendement en %.

## Modèle de données — `docs/signal_tracking.json`

Nouveau fichier, structure `{"positions": [...]}`, une entrée par
position (ouverte ou clôturée) :

```json
{
  "id": "BN.PA-2026-09-08",
  "ticker": "BN.PA",
  "name": "Danone",
  "index": "CAC40",
  "status": "open",

  "entry_date": "2026-09-08",
  "entry_price": 100.0,
  "target_exit_price": 130.0,
  "index_price_at_entry": 7850.0,

  "close_date": null,
  "close_price": null,
  "close_reason": null,
  "return_pct": null,

  "index_price_at_close": null,
  "index_return_pct": null,

  "shadow_close_date": "2027-03-08",
  "shadow_resolved": false,
  "shadow_price": null,
  "shadow_return_pct": null
}
```

- `id` : `{ticker}-{entry_date}` — identifiant stable, unique (un ticker
  ne peut avoir qu'une position ouverte à la fois, donc pas de collision
  possible pour une même date).
- `close_reason` ∈ `"objectif_atteint" | "stop_loss" | "delai_max"`.
- `shadow_close_date` est fixée **à l'ouverture** (`entry_date` + 6 mois
  calendaires — `dateutil.relativedelta(months=6)`, pas une
  approximation en jours, pour rester correct autour des mois inégaux/
  années bissextiles), indépendamment de quand la position réelle
  clôture.
- `index` reprend la clé d'indice de la société (`CAC40`/`DAX`), pour
  savoir quel niveau d'indice utiliser comme benchmark.

## Logique quotidienne

Nouvelle fonction `update_signal_tracking(companies, index_names)`,
appelée dans `main()` après le calcul des scores/alertes de la
journée (a besoin de `current_price`, `entry_price`, `exit_price` et
des alertes à jour de chaque société), écrit `docs/signal_tracking.json`.
Dégrade toujours vers "ne rien changer" en cas d'erreur (même contrat
que `_attach_alerts_and_update_history`) — ne fait jamais échouer le
run.

1. **Récupérer les niveaux d'indice du jour** (CAC40 : `^FCHI`,
   DAX : `^GDAXI`, via yfinance) une fois, réutilisés pour toutes les
   positions concernées. `None` si le fetch échoue — les positions dont
   le benchmark indice en dépend aujourd'hui sont alors traitées sans ce
   benchmark plutôt que de faire échouer tout le run.
2. **Ouvrir les nouvelles positions** : pour chaque société dont le
   signal "entrée" vient d'apparaître aujourd'hui (même détection que
   `newly_triggered_entree` dans `_attach_alerts_and_update_history`) et
   qui n'a pas déjà de position `"open"` : créer l'entrée avec
   `entry_price = current_price`, `target_exit_price = exit_price`,
   `index_price_at_entry` = niveau du jour de son indice,
   `shadow_close_date = entry_date + 6 mois`.
3. **Vérifier les clôtures** : pour chaque position `"open"` dont le
   ticker est présent dans `companies` aujourd'hui (voir cas limite
   ci-dessous sinon) :
   - `current_price <= entry_price * 0.8` → `stop_loss`
   - sinon `current_price >= target_exit_price` → `objectif_atteint`
   - sinon `today >= entry_date + 6 mois` → `delai_max`
   - sinon : rien, reste ouverte.
   - Si une condition est vraie : `status = "closed"`, `close_date`,
     `close_price = current_price`, `close_reason`, `return_pct`
     calculé, `index_price_at_close`/`index_return_pct` calculés à
     partir du niveau d'indice du jour. Si la clôture est due à
     `delai_max`, `shadow_close_date` tombe le même jour : résoudre le
     benchmark fantôme dans la foulée (`shadow_price = close_price`,
     `shadow_return_pct = return_pct`, `shadow_resolved = true`) plutôt
     que d'attendre un jour de plus pour rien.
4. **Résoudre les benchmarks fantômes en attente** : pour toute position
   (ouverte ou déjà clôturée) où `shadow_resolved` est faux et
   `today >= shadow_close_date` : si le ticker est présent dans
   `companies` aujourd'hui, calculer `shadow_price`/`shadow_return_pct`
   et passer `shadow_resolved = true`. Sinon, laisser en attente (retenté
   le jour suivant).

**Dépendance** : `python-dateutil` (pour `relativedelta`) n'est pas dans
`requirements.txt` aujourd'hui — probablement déjà présent en
transitif via pandas/yfinance, mais à ajouter explicitement puisque le
code en dépend directement désormais.

## Cas limites

- **Ticker retiré de l'indice pendant qu'une position est ouverte** (vu
  aujourd'hui même avec Porsche AG/Sartorius/Covestro) : le ticker
  n'apparaît plus dans `companies`, donc l'étape 3 ne peut plus évaluer
  ses conditions de clôture. La position reste `"open"` indéfiniment,
  sans clôture forcée automatique en v1 — un cas rare, documenté plutôt
  que traité activement. Pourra être géré plus tard (ex : clôture forcée
  au dernier cours connu) si ça devient fréquent en pratique.
- **Échec du fetch d'indice** : dégrade en laissant
  `index_price_at_entry`/`index_price_at_close` à `None` pour les
  positions concernées ce jour-là plutôt que de bloquer tout le run.
- **Réapparition du signal "entrée" après clôture** : autorisée et
  voulue — une nouvelle position s'ouvre normalement, distincte de la
  précédente (id différent, `entry_date` différente).

## Frontend

Nouvelle section sur le site Indices (à définir précisément dans le plan
d'implémentation — probablement un onglet dépliant au même niveau que
"Actu"/"Détail du calcul", ou une entrée dédiée au niveau de la liste des
indices) affichant :
- **Stats agrégées** (sur les positions clôturées) : nombre de positions
  clôturées, taux de réussite (% à `return_pct > 0`), rendement moyen,
  écart moyen vs indice (alpha), écart moyen vs "tenir 6 mois pleins"
  (valeur de la sortie anticipée) — uniquement sur les positions dont le
  benchmark concerné est déjà résolu.
- **Tableau des positions** (ouvertes et clôturées) : ticker, dates
  d'entrée/sortie, prix, rendement, raison de clôture, comparaison aux
  deux benchmarks.

## Tests

Suivre la densité de tests déjà en place dans `tests/test_indices_score.py` :
- Ouverture : nouvelle position créée sur signal "entrée" inédit ; pas de
  doublon si une position est déjà ouverte sur ce ticker ; nouvelle
  position autorisée après clôture d'une précédente sur le même ticker.
- Clôture : chacune des 3 conditions déclenche la bonne raison, dans le
  bon ordre de priorité (stop-loss avant objectif avant délai) ; calcul
  du rendement correct.
- Benchmark indice : calculé à l'ouverture et à la clôture ; dégrade
  proprement (`None`) si le fetch d'indice échoue.
- Benchmark fantôme : résolu uniquement à `shadow_close_date` atteinte,
  y compris pour une position déjà clôturée avant cette date ; résolu
  immédiatement si la clôture réelle coïncide avec le délai max.
- Cas limite : ticker absent de `companies` (sorti de l'indice) —
  position ouverte laissée intacte, pas d'exception.
- Dégradation générale : fichier `signal_tracking.json` absent/corrompu
  au démarrage → repart d'un état vide, jamais d'exception qui ferait
  échouer `main()`.
