# Test prospectif IA — raisonnement actu/prix sur l'or — Implementation Plan

> **For agentic workers:** Ce plan N'EST PAS un plan de code TDD classique — aucun fichier `gold_bot/*.py` n'est créé ou modifié (voir le spec, section 2, "Hors scope"). Comme le cadran macro hebdomadaire déjà en place (`gold_bot/macro_dial_log.jsonl`, aucun code Python ne le produit — une routine planifiée dont le PROMPT fait tout le travail), ce chantier est une mise en place opérationnelle (routine planifiée) précédée d'un test manuel réel. Exécution directe par le contrôleur (pas de subagent-driven-development — rien à committer/diffuser/relire en git, pas de cycle pytest). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reproduire pour l'or (XAUUSD) la méthodologie déjà en cours sur les actions (`docs/superpowers/experiments/*-ia-news-reasoning-test.json`) : un pari directionnel daté, basé sur un raisonnement actu+prix sans formule mécanique, enregistré avant de connaître le résultat, vérifié 2-4 semaines plus tard.

**Architecture:** Une routine cloud planifiée hebdomadaire (même mécanisme que "Gold bot - cadran macro hebdomadaire (fantome)", trigger `trig_019R5p28eGTBjU5g3Cf5mdf1`) dont le prompt complet fait tout le travail : récupérer le prix/volume récent de XAUUSD, chercher l'actualité pertinente via `web_search`, faire un appel directionnel structuré, écrire un fichier daté au même schéma que les fichiers actions existants.

**Tech Stack:** Routine cloud planifiée (`RemoteTrigger`), aucun code Python nouveau.

**Spec:** `docs/superpowers/specs/2026-11-06-gold-news-reasoning-forward-test-design.md`

## Global Constraints

- Jamais d'exécution réelle ou simulée, jamais de nouveau compte MetaApi/FTMO (voir spec, section 2).
- Même schéma JSON que `docs/superpowers/experiments/*-ia-news-reasoning-test.json` existants (`date_test`, `date_verification_prevue`, `methodologie`, `paris` — un seul élément `{"ticker": "XAUUSD", "prix_entree", "appel", "confiance", "raison"}`).
- `date_verification_prevue` = `date_test` + 3 semaines (milieu de la fourchette 2-4 semaines déjà utilisée pour les actions).
- Cadence hebdomadaire, même famille de coût qu'une exécution du cadran macro existant (un seul appel Opus + recherche web par semaine).

---

### Task 1 : Valider le prompt par un test manuel réel

**Files:** Aucun fichier committé — script jetable, comme celui qui a trouvé le bug `max_tokens` du veto le 2026-11-05.

- [ ] **Step 1 : Récupérer le prix actuel et le contexte récent de XAUUSD**

Récupérer la clôture la plus récente de XAUUSD et un résumé des 5-10 derniers jours de bourse (même donnée que celle déjà utilisée aujourd'hui pour l'investigation de l'incident réel — via `gold_bot_ftmo_check/data/xauusd_5min_raw.json` ou un fetch direct si ce cache n'est pas à jour).

- [ ] **Step 2 : Composer et envoyer le prompt réel**

Prompt (à adapter légèrement si le résultat du test ci-dessous révèle un problème de format) :

```
Tu es un analyste qui raisonne sur l'or (XAUUSD) a partir de
l'actualite recente et du comportement du prix/volume -- PAS de
formule mecanique, un jugement contextuel comme un trader humain le
ferait.

Prix de cloture le plus recent : {prix}, date {date}.
Comportement recent (7-10 derniers jours de bourse) : {resume_prix}.

Cherche l'actualite macro/geopolitique la plus pertinente pour l'or
sur cette periode (banques centrales, inflation, risque geopolitique,
flux ETF). Fais un appel directionnel sur l'horizon des 3 prochaines
semaines : haussier, baissier, neutre (nuances possibles : leger_haussier,
neutre_prudent, etc., comme un vrai jugement nuance).

Termine ta reponse par un bloc JSON seul sur sa derniere ligne,
exactement sous cette forme :
{"appel": "...", "confiance": "faible ou moyenne ou haute", "raison": "une ou deux phrases"}
```

- [ ] **Step 3 : Vérifier que la réponse produit un JSON exploitable**

Si le bloc JSON final n'est pas exploitable (même défaut que le veto avant son correctif `max_tokens` du 2026-11-05 — recherche web + raisonnement consommant le budget avant le JSON final), ajuster le prompt (demander explicitement un raisonnement plus concis, ou augmenter le budget si la routine cloud le permet) et refaire ce test jusqu'à obtenir un JSON exploitable de façon fiable (au moins 2 essais réussis de suite).

- [ ] **Step 4 : Composer le fichier daté complet et vérifier son schéma**

```json
{
  "date_test": "2026-11-06",
  "date_verification_prevue": "2026-11-27",
  "methodologie": "Test prospectif (pas un backtest) : lecture reelle d'actualites + prix/volume du jour pour l'or, raisonnement IA sans formule mecanique, pari directionnel date et enregistre avant de connaitre le resultat. Meme methodologie que docs/superpowers/experiments/*-ia-news-reasoning-test.json (actions), appliquee a XAUUSD. Objectif : verifier si ce raisonnement contextuel a une vraie valeur predictive sur 2-4 semaines pour l'or specifiquement, avant d'envisager un futur moteur de trading autour de ce raisonnement.",
  "paris": [
    {"ticker": "XAUUSD", "prix_entree": <prix reel du Step 1>, "appel": "<reponse reelle du Step 2>", "confiance": "<reponse reelle>", "raison": "<reponse reelle>"}
  ]
}
```

Comparer ce fichier champ par champ avec un fichier actions existant (ex. `docs/superpowers/experiments/2026-11-02-ia-news-reasoning-test.json`) pour confirmer un schéma identique.

---

### Task 2 : Créer la routine planifiée

**Outils** : `RemoteTrigger` (charger via `ToolSearch select:RemoteTrigger` avant l'appel).

- [ ] **Step 1 : Composer le corps de la routine**

Cron hebdomadaire (même jour/heure que le cadran macro existant si connu, sinon un jour de semaine en dehors des horaires de publications macro à fort impact — éviter CPI/NFP/FOMC/BCE/BOE, voir `gold_bot/confluence.py::SCALP_HIGH_IMPACT_EVENTS_UTC`/`PRESS_CONFERENCE_EVENTS_UTC` pour les dates à éviter ce mois-ci). Modèle `claude-sonnet-5-5` par défaut (cohérent avec le reste des routines planifiées de ce projet), dépôt `https://github.com/Alexandreauq/analyse-or`, outils `Bash, Read, Write, Glob, Grep` (lecture du cache de prix existant + écriture du fichier daté).

Prompt de la routine : le même prompt validé à la Task 1, complété par l'instruction explicite d'écrire le fichier résultant à `docs/superpowers/experiments/<date-du-jour>-gold-news-reasoning-test.json` au schéma validé, et de commiter ce seul fichier (`git add` + `git commit`, message cohérent avec les commits `chore(gold-bot): cadran macro hebdomadaire (fantome)` déjà utilisés pour la routine sœur).

- [ ] **Step 2 : Créer la routine via `RemoteTrigger`**

`{action: "create", body: {...}}` avec le corps composé à l'étape précédente.

- [ ] **Step 3 : Lancer une première exécution manuelle pour vérifier le pipeline complet**

`{action: "run", trigger_id: "..."}`, puis `{action: "get_run_log", session_id: "..."}` pour confirmer : le fichier daté a bien été écrit et commité, au schéma attendu, avec un appel directionnel exploitable (pas un échec silencieux).

---

## Hors scope de ce plan (à traiter séparément)

- Toute vérification du résultat de ce pari (manuelle, à `date_verification_prevue`).
- Toute décision de construire un moteur de trading (compte "signaux" dry_run) autour de ce raisonnement — seulement après plusieurs semaines de paris vérifiés, même exigence que pour le test actions.
- Track 1 (fenêtre de blackout étendue par l'IA) — plan séparé, `docs/superpowers/plans/2026-11-06-gold-bot-ai-extended-news-blackout.md`.
