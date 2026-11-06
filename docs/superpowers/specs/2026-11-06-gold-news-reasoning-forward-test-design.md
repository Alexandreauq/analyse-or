# Test prospectif IA — raisonnement actu/prix sur l'or — design

## 1. Contexte et objectif

Le projet a déjà un test équivalent sur les actions (`docs/superpowers/experiments/*-ia-news-reasoning-test.json`, premier lot daté 2026-10-11, résultats vérifiés à partir du 2026-11-08) : des paris directionnels datés, basés sur un raisonnement actu+volume sans formule mécanique, vérifiés plusieurs semaines plus tard — jamais utilisés pour trader réellement, purement pour savoir si ce type de raisonnement a une vraie valeur prédictive.

Objectif ici : la même méthodologie, appliquée à l'or (XAUUSD), comme première étape avant d'envisager un jour un moteur de trading piloté par ce raisonnement (compte "signaux" séparé, dry_run — voir hors-scope). On ne construit PAS ce moteur dans ce plan : on valide d'abord si le raisonnement a un edge, exactement comme pour les actions.

## 2. Hors scope (explicite)

- Pas de nouveau moteur `decide_and_act_*`, pas d'exécution simulée ou réelle, pas de stop/cible/taille de position.
- Pas de nouveau compte MetaApi/FTMO. Les prix nécessaires viennent du cache déjà utilisé par la recherche (`gold_bot_ftmo_check/data/`) ou d'un fetch direct réutilisant les identifiants déjà provisionnés (lecture seule).
- Pas de décision automatique ni de trade réel tant que les résultats ne sont pas vérifiés (même principe que le test actions — vérification humaine du résultat avant toute conclusion).
- Pas de lien avec le veto d'entrée (`ai_shadow.py`) ni avec le Track 1 (blackout étendu) — projet totalement indépendant.

## 3. Mécanisme

### 3.1 Cadence et déclenchement

Routine planifiée (cloud routine, même mécanisme déjà utilisé pour "Gold bot - cadran macro hebdomadaire (fantome)"), hebdomadaire — un seul instrument (XAUUSD) suivi en continu, donc une cadence hebdomadaire est cohérente avec celle déjà choisie pour le cadran macro (contrairement au test actions, qui scanne beaucoup de tickers d'un coup et n'a donc pas besoin d'une cadence régulière par ticker individuel).

### 3.2 Contenu du pari

À chaque exécution, la routine :
1. Récupère le prix actuel (clôture la plus récente) et un résumé du volume/de la volatilité récente de XAUUSD (même source que le cadran macro existant).
2. Demande à Claude (avec l'outil `web_search`, même famille d'appel que `ai_shadow.py`/le cadran macro) de chercher l'actualité macro/géopolitique pertinente pour l'or sur la période récente, et de faire UN appel directionnel (`haussier`/`baissier`/`neutre`, avec nuances possibles comme le test actions : `leger_haussier`, `neutre_prudent`, etc.) avec confiance et raison.
3. Écrit un nouveau fichier daté `docs/superpowers/experiments/YYYY-MM-DD-gold-news-reasoning-test.json`, MÊME SCHÉMA que les fichiers actions existants :

```json
{
  "date_test": "YYYY-MM-DD",
  "date_verification_prevue": "YYYY-MM-DD",
  "methodologie": "...",
  "paris": [
    {"ticker": "XAUUSD", "prix_entree": ..., "appel": "...", "confiance": "...", "raison": "..."}
  ]
}
```

(Un seul élément dans `paris` ici, contrairement au test actions qui en a plusieurs par lot — un seul instrument suivi.) `date_verification_prevue` fixée à 2-4 semaines après `date_test`, même horizon que le test actions.

### 3.3 Vérification

Manuelle, au moment de `date_verification_prevue` : le prix a-t-il bougé dans le sens du pari sur l'horizon prévu ? Vérifié : le test actions n'a pas de script de vérification automatisée (confirmé par recherche dans le repo) — la vérification s'y fait à la main, lecture directe du prix à la date prévue face au pari enregistré. Même processus ici, pas de nouveau script de vérification dans ce plan.

## 4. Coût

Un seul appel réel par semaine (Opus + recherche web, même ordre de coût qu'un cycle du cadran macro déjà en place). Négligeable face à l'activité déjà existante.

## 5. Critère de succès (avant d'envisager la suite)

Comme pour le test actions : accumuler plusieurs semaines de paris datés et vérifiés avant de tirer une conclusion sur la valeur prédictive du raisonnement. Aucune décision de construire un moteur de trading autour de ce raisonnement (compte "signaux" dry_run) ne sera prise avant d'avoir au moins quelques semaines de résultats vérifiés — même exigence que celle déjà suivie pour le test actions (résultats attendus à partir du 2026-11-08) et pour la promotion du veto d'entrée aujourd'hui (shadow pendant des semaines avant d'être jugé).

## 6. Tests requis (liste, pas de code — voir le plan)

Ce chantier n'ajoute aucune fonction testable au sens unitaire classique (c'est une routine planifiée, pas du code `gold_bot/`) :
- Vérifier que le prompt produit bien un JSON exploitable dans le format attendu (même type de test que celui qui a trouvé le bug `max_tokens` du veto — un test manuel réel avant la première exécution planifiée, pas un test automatisé répété).
- Vérifier que le fichier daté est bien écrit au bon chemin, avec le bon schéma (comparaison directe avec un fichier actions existant).
