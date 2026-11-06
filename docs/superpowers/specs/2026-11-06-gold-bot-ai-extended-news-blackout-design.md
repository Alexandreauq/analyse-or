# Fenêtre de blackout étendue par l'IA — design

## 1. Contexte et problème

Le 2026-11-06, le moteur Fibonacci (compte FTMO réel) a ouvert une vente perdante à 13h55 UTC, 10 minutes après la fin de la fenêtre de blackout NFP (publication à 13h30 UTC, fenêtre ±15min via `confluence.SCALP_NEWS_BLACKOUT_MINUTES`). Le marché digérait encore le choc (bougie de 13h30 : amplitude 4,7× la normale) quand la fenêtre fixe s'est fermée. Les deux garde-fous existants ont chacun un trou pour ce cas précis :

- `confluence.is_news_blackout(date)` : fenêtre calendaire FIXE (15min pour CPI/Emploi/BOE, 100min pour FOMC/BCE) — ne sait pas si le marché a réellement digéré, seulement si on est dans la fenêtre prévue.
- `confluence.is_volatility_shock(candles)` : réactif, mais ne regarde QUE la dernière bougie — ne protège pas contre les bougies qui suivent un choc pendant qu'il se digère encore.

Ni l'un ni l'autre ne couvre la fenêtre grise "le blackout calendaire vient de finir, mais le marché est-il vraiment calme ?" Le veto IA déjà branché aujourd'hui sur `decide_and_act_fibonacci` (voir le chantier du même jour) ne couvre pas non plus ce cas : il juge un signal déjà confirmé, pas la question "devrait-on même évaluer un signal maintenant ?"

## 2. Objectif

Un jugement IA, déclenché UNIQUEMENT dans la fenêtre grise qui suit la fin d'un blackout calendaire, qui décide si la prudence doit être prolongée au-delà de la fenêtre fixe. Ne peut JAMAIS raccourcir une fenêtre de blackout déjà active — uniquement l'étendre après coup. Fail-open vers le comportement actuel (fenêtre fixe déjà terminée → trade autorisé) si l'IA échoue, timeout, ou renvoie un verdict non exploitable.

## 3. Hors scope (explicite)

- Ne remplace pas `is_news_blackout`/`is_volatility_shock` — vient STRICTEMENT en supplément, après qu'ils ont déjà laissé passer.
- Ne s'applique qu'à `decide_and_act_fibonacci` dans cette première version (seul moteur sur un vrai compte aujourd'hui). `decide_and_act_ichimoku` pourra être étendu plus tard si ce moteur est un jour déployé sur un compte réel — pas dans ce plan.
- Ne modifie jamais `SCALP_NEWS_BLACKOUT_MINUTES`/`PRESS_CONFERENCE_BLACKOUT_MINUTES` eux-mêmes (paramètres déjà calibrés, pas remis en cause ici).
- Ne touche jamais aux positions déjà ouvertes (uniquement un garde-fou sur l'OUVERTURE d'un nouveau trade, comme `is_news_blackout`/`is_volatility_shock` existants pour les nouvelles entrées).

## 4. Mécanisme

### 4.1 Détection de la "fenêtre grise"

Nouvelle fonction dans `gold_bot/confluence.py` :

```python
POST_NEWS_GRACE_MINUTES = 60  # fenetre grise apres la fin d'un blackout SCALP (15min)
PRESS_CONFERENCE_POST_NEWS_GRACE_MINUTES = 80  # apres la fin d'un blackout FOMC/BCE (100min)

def minutes_since_blackout_ended(date: datetime) -> tuple[float, float] | None:
    """(minutes ecoulees depuis la fin de la derniere fenetre de
    blackout calendaire la plus recente avant `date`, grace applicable
    en minutes pour CE type d'evenement) -- ou None si `date` est
    encore DANS une fenetre de blackout (is_news_blackout(date) est
    True -- rien a faire, le garde-fou existant bloque deja), ou si
    aucun evenement qualifiant n'est trouve avant `date` dans les
    listes connues. Le 2e element du tuple (la grace) depend du TYPE
    d'evenement retenu (SCALP -> POST_NEWS_GRACE_MINUTES, press-
    conference -> PRESS_CONFERENCE_POST_NEWS_GRACE_MINUTES), pour que
    l'appelant compare directement sans dupliquer la logique de type."""
```

Logique : parcourt `SCALP_HIGH_IMPACT_EVENTS_UTC` (fin de fenêtre = événement + `SCALP_NEWS_BLACKOUT_MINUTES`, grâce = `POST_NEWS_GRACE_MINUTES`) et `PRESS_CONFERENCE_EVENTS_UTC` (fin de fenêtre = événement + `PRESS_CONFERENCE_BLACKOUT_MINUTES`, grâce = `PRESS_CONFERENCE_POST_NEWS_GRACE_MINUTES`), ne considère que les fenêtres déjà terminées avant `date`, renvoie `(minutes écoulées, grâce applicable)` pour la fenêtre dont la fin est la PLUS RÉCENTE parmi les deux types. Renvoie `None` si `is_news_blackout(date)` est `True` (encore dans une fenêtre — pas de question à poser) ou si aucune fenêtre qualifiante n'existe.

### 4.2 Jugement IA

Nouveau module `gold_bot/ai_news_watch.py` (responsabilité distincte du veto d'entrée `ai_shadow.py` — juge le CALME du marché, pas un signal de trading précis) :

```python
def judge_post_news_calm(symbol: str, candles: list[dict], minutes_since_blackout: float, *,
                          log_path: str = ..., now: datetime | None = None) -> dict | None:
    """Appelle Claude (meme patron que ai_shadow.shadow_judge_entry :
    web_search, max_tokens=4096, max_retries=0, timeout 40s, cooldown,
    jamais d'exception propagee) pour juger si le marche a reellement
    digere l'actualite dont la fenetre de blackout calendaire vient de
    se terminer. Renvoie {"verdict": "calme ou encore_agite", "confiance":
    ..., "raison": ...} ou None (echec reel). Cooldown/cache par
    (symbol,) seul -- un seul evenement de marche a la fois par symbole,
    pas besoin de direction/niveau ici (contrairement a shadow_judge_entry,
    cette fonction ne juge pas un trade specifique)."""
```

Prompt : contexte = bougies récentes + `minutes_since_blackout` + type d'événement (déduit par l'appelant). Question : "le marché semble-t-il avoir digéré cette actualité, ou montre-t-il encore des signes de bruit/volatilité anormale ?" Sortie structurée identique en forme au veto (`verdict`/`confiance`/`raison`), avec des valeurs différentes (`"calme"`/`"encore_agite"` au lieu de `"confirme"`/`"opposerait"`, pour qu'un verdict de ce module ne soit jamais confondu avec un verdict du veto d'entrée dans les logs).

### 4.3 Intégration dans `decide_and_act_fibonacci`

Au point où `confluence.is_news_blackout(now)` est déjà vérifié (avant évaluation du signal) :

```python
if confluence.is_news_blackout(now):
    ... (inchange)

grace_info = confluence.minutes_since_blackout_ended(now)  # None si pas de fenetre grise
if grace_info is not None:
    elapsed, grace = grace_info
    if elapsed <= grace:
        verdict = ai_news_watch.judge_post_news_calm(symbol, candles, elapsed)
        if verdict is not None and verdict.get("verdict") == "encore_agite":
            return {"action": "aucune", "reason": "prudence post-actualite (IA)"}
```

Fail-open : `verdict is None` (échec/timeout/non-parseable) ou `"calme"` → le code continue normalement (comportement IDENTIQUE à aujourd'hui, le blackout fixe a déjà fini). Seul `"encore_agite"` bloque.

## 5. Coût et fréquence

Ne se déclenche QUE dans la fenêtre grise (60 ou 80 minutes après la fin d'un blackout calendaire) — avec ~20 événements SCALP/an + ~15 événements press-conférence/an pour XAUUSD, c'est quelques dizaines d'occasions par an, pas par semaine. Cooldown propre (même durée que `SHADOW_COOLDOWN`, 24h) empêche un rappel payant si plusieurs cycles tombent dans la même fenêtre grise pour le même événement.

## 6. Tests requis (liste, pas de code — voir le plan)

- `confluence.minutes_since_blackout_ended` : None pendant un blackout actif ; None longtemps après (hors grâce) ; valeur correcte juste après la fin d'une fenêtre SCALP ; valeur correcte après une fenêtre press-conférence ; le plus récent des deux types choisi si les deux qualifient.
- `ai_news_watch.judge_post_news_calm` : même suite que `shadow_judge_entry` (succès/échec/non-parseable/cooldown/cache réutilisé/jamais d'exception), adaptée au vocabulaire `calme`/`encore_agite`.
- `decide_and_act_fibonacci` : fenêtre grise + IA dit "encore_agite" → bloqué ; IA dit "calme" → passe ; IA renvoie None → passe (fail-open) ; hors fenêtre grise → l'IA n'est jamais appelée (pas de régression de coût) ; reproduction directe du cas du 06/11 (13h55, 10min après la fin du blackout NFP) avec un mock qui renvoie "encore_agite" → bloqué, là où le code actuel laissait passer.
