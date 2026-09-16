# Profils de risque (1-5) pour le bot Or — Spec

## Statut

VALIDÉ (architecture + échelle confirmées par l'utilisateur le 2026-09-15).

## Contexte

Le bot Or (`gold_bot/`) tourne aujourd'hui avec un seul jeu de paramètres
de risque, tous des valeurs par défaut jamais surchargées en pratique :
- `risk.compute_position_size(..., risk_pct=0.05)` — 5% du solde risqué
  par position (appelé sans `risk_pct` explicite dans `gold_bot/bot.py`).
- `risk.CircuitBreaker(threshold_pct=0.10)` — coupe-circuit journalier à
  10% de perte (instancié sans `threshold_pct` explicite dans
  `gold_bot/loop.py`).

L'utilisateur veut pouvoir choisir un niveau de prise de risque parmi 5
profils numérotés 1 (très prudent) à 5 (agressif), pour le compte réel
MetaApi existant.

**Décision d'architecture (confirmée) :** un seul profil actif à la fois
sur le compte réel existant — pas de comptes MetaApi multiples en
parallèle (qui coûteraient jusqu'à +36$/mois pour 4 comptes
supplémentaires et exigeraient de paramétrer par profil chaque chemin de
fichier d'état, la boucle et le déploiement). Le profil choisi règle
uniquement `risk_pct` et `threshold_pct` — **le moteur de confluence
(`gold_bot/confluence.py`, constantes `SCALP_*`) reste strictement
identique quel que soit le profil actif** : tous les profils reçoivent
exactement les mêmes signaux d'entrée/sortie, seule la taille de position
et la tolérance de perte journalière changent. C'est une décision
délibérée pour garder le changement minimal et facile à valider en
dry-run — un profil agressif n'accepte pas des signaux plus marginaux, il
mise simplement plus gros sur les mêmes signaux.

## Échelle des 5 profils

| Profil | `risk_pct` | `threshold_pct` |
|---|---|---|
| 1 (très prudent) | 2% | 5% |
| 2 (prudent) | 3.5% | 7.5% |
| 3 (actuel/standard, **défaut**) | 5% | 10% |
| 4 (offensif) | 7.5% | 13.5% |
| 5 (agressif) | 10% | 17.5% |

Un profil invalide (absent, hors de `[1, 5]`, ou non-entier) se résout
vers le profil 3 (repli explicite, jamais d'exception — même convention
que `sector_risk_profile()` dans `indices_score.py` qui replie vers
`"standard"`).

## Conception

### 1. `gold_bot/state.py` — nouveau champ `risk_profile`

Ajout d'une clé `risk_profile` (entier, défaut `3`) au dict par défaut de
`state.py`, avec le même contrat de robustesse que `dry_run`/`kill_switch`
(fichier absent/corrompu → défauts, jamais d'exception). C'est un
changement dans un fichier existant, pas un nouveau module.

### 2. `gold_bot/risk.py` — nouveau resolver

```python
RISK_PROFILE_PARAMS: dict[int, dict[str, float]] = {
    1: {"risk_pct": 0.02, "threshold_pct": 0.05},
    2: {"risk_pct": 0.035, "threshold_pct": 0.075},
    3: {"risk_pct": 0.05, "threshold_pct": 0.10},
    4: {"risk_pct": 0.075, "threshold_pct": 0.135},
    5: {"risk_pct": 0.10, "threshold_pct": 0.175},
}
DEFAULT_RISK_PROFILE = 3


def risk_profile_params(profile) -> dict[str, float]:
    """Résout un profil de risque (1-5) vers ses paramètres. Tout profil
    invalide (absent, hors plage, non-entier) replie sur le profil 3 —
    jamais d'exception, même convention que sector_risk_profile()."""
    if not isinstance(profile, int) or profile not in RISK_PROFILE_PARAMS:
        profile = DEFAULT_RISK_PROFILE
    return RISK_PROFILE_PARAMS[profile]
```

### 3. `gold_bot/loop.py` — lecture à chaque cycle, pas seulement au démarrage

`state.load_state()` est déjà rappelé plusieurs fois par cycle pour
`dry_run`/`kill_switch` (voir `gold_bot/loop.py:47,137,171`) — **pas
seulement une fois au démarrage du process**. `risk_profile` doit suivre
exactement le même contrat : un changement de profil via l'API doit
prendre effet au cycle suivant, sans redémarrage du service.

`CircuitBreaker` est instancié une seule fois dans `main()`
(`gold_bot/loop.py:188`) et vit tout le `while True`, car il porte l'état
du solde de début de journée (`_starting_balance`/`_day`). Recréer
l'objet à chaque cycle perdrait cet état. `threshold_pct` est un attribut
d'instance simple relu à chaque appel de `can_open_position()`
(`gold_bot/risk.py:80`), donc la bonne approche est de **muter
`circuit_breaker.threshold_pct`** en début de cycle plutôt que de
recréer l'objet :

```python
current_state = state.load_state(state.STATE_PATH)
params = risk.risk_profile_params(current_state.get("risk_profile"))
circuit_breaker.threshold_pct = params["threshold_pct"]
```

Et `risk_pct` doit être passé explicitement (au lieu du défaut implicite
actuel) à l'appel de `risk.compute_position_size(...)` dans
`gold_bot/bot.py` — ce qui demande de faire transiter `risk_pct` depuis
`loop.py` jusqu'à l'appel dans `bot.py` (paramètre de fonction
supplémentaire sur le chemin d'appel concerné, pas un import global).

### 4. `gold_bot/api.py` — nouvel endpoint `POST /profile`

**Déviation explicite et documentée** par rapport à l'invariant actuel du
module (« seul kill_switch peut être modifié, jamais dry_run ») : ce
nouvel endpoint ajoute une DEUXIÈME route de mutation, mais reste plus
restreint que le kill switch — c'est un réglage de routine, pas un
interrupteur d'urgence lié à de l'argent réel non protégé, donc l'exposer
en HTTPS authentifié (même jeton `BOT_API_TOKEN` que `/dashboard`
aujourd'hui) est cohérent avec le fait qu'on veut pouvoir l'ajuster
souvent depuis le site, sans SSH. Le docstring en tête de fichier doit
être mis à jour pour refléter ce changement d'invariant.

```python
@app.post("/profile")
def set_profile(payload: dict, x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    profile = payload.get("profile")
    if not isinstance(profile, int) or profile not in risk.RISK_PROFILE_PARAMS:
        raise HTTPException(status_code=422, detail="profile doit être un entier entre 1 et 5")
    current = state.load_state(state.STATE_PATH)
    current["risk_profile"] = profile
    state.save_state(current, state.STATE_PATH)
    return {"risk_profile": profile}
```

`GET /status` et `GET /dashboard` doivent aussi exposer `risk_profile`
dans leur réponse (même pattern que `kill_switch`/`dry_run` déjà
présents dans `/status`), pour que le futur affichage frontend (panneau
"Bot Or" de l'onglet Portfolio) puisse afficher le profil actif sans
appel supplémentaire.

### 5. Frontend (`docs/index.html`, panneau Bot Or)

Hors scope de cette spec : l'affichage/le contrôle du profil dans
l'interface (probablement un sélecteur 1-5 à côté du statut
dry_run/kill_switch déjà affiché) sera une itération ultérieure, une fois
l'endpoint disponible et testé en dry-run. Le plan d'implémentation qui
suit cette spec couvre uniquement le backend (`state.py`, `risk.py`,
`loop.py`, `bot.py`, `api.py`).

## Tests

- `risk_profile_params()` : les 5 profils retournent les bonnes valeurs ;
  profil absent/`None`/hors plage `[1,5]`/non-entier replie sur 3 ;
  jamais d'exception.
- `state.py` : `risk_profile` suit le même contrat de robustesse que
  `dry_run`/`kill_switch` (fichier absent/corrompu → défaut 3).
- `loop.py` : le `CircuitBreaker` partagé voit son `threshold_pct` changer
  entre deux cycles simulés si `risk_profile` change dans `state.json`
  entre les deux (test d'intégration léger, pas besoin de MetaApi réel).
- `bot.py` : `risk_pct` explicite transmis à `compute_position_size` (pas
  la valeur par défaut de la fonction) — test qui vérifierait qu'une
  taille de position calculée avec profil 1 diffère de celle calculée
  avec profil 5 pour les mêmes `balance`/`entry`/`stop_loss`.
- `api.py` : `POST /profile` avec jeton valide + profil 1-5 → 200 et
  persistance dans `state.json` ; profil invalide → 422 ; jeton
  invalide/absent → 401 (même comportement que `/kill`/`/resume`) ;
  `GET /status` reflète `risk_profile` après un changement.

## Non-objectifs

- Pas de comptes MetaApi multiples, pas de profils actifs en parallèle.
- Pas de changement aux seuils du moteur de confluence
  (`SCALP_TAKEPROFIT_RISK_MULTIPLE`, `SCALP_LEVEL_PROXIMITY`, etc.) — ils
  restent identiques pour tous les profils.
- Pas d'affichage/contrôle frontend dans cette spec (voir §5 ci-dessus).
- Pas de changement au comportement du kill switch/dry_run existants
  (restent SSH-only, non exposés en mutation HTTPS au-delà de
  `/kill`/`/resume`).
