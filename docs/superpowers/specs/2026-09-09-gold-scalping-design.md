# Module Scalping Or (1min) — design

## Contexte et objectif

Le module "Or" du site n'a aujourd'hui qu'un volet macro-fondamental
(score composite quotidien, `gold_score.py` → `docs/score.json`) plus un
graphique "temps réel" qui n'est en fait qu'un widget TradingView
embarqué (OANDA:XAUUSD) — aucune donnée intraday ne transite par le
pipeline Python du projet.

Ce chantier ajoute un second volet, **Scalping**, dans le même écran Or :
des signaux d'entrée/sortie sur bougies 1 minute, dérivés des
connaissances du cours d'Analyse Technique L3 Dauphine synthétisées dans
`C:\Users\alexa\OneDrive\Desktop\finance\Analyse technique\Synthese_pour_scoring_technique.md`
(document hors dépôt, propriété de l'utilisateur — le présent spec en
extrait les règles opérationnelles, pas le contenu pédagogique complet).

## Contrainte fondatrice : ce projet ne peut pas faire de vrai temps réel serveur

Le site est statique (Python → JSON → GitHub Pages), avec un cron
GitHub Actions à cadence quotidienne (et déjà sujet à des retards de
plusieurs heures observés en pratique). Cette cadence est incompatible
avec du scalping 1 minute. **Décision validée avec l'utilisateur :** le
calcul se fait entièrement **côté navigateur**, sans passer par le
pipeline Python — le module Scalping est un sous-système autonome,
purement frontend.

## Portée

**Dans le périmètre (v1) :**
- Nouveau fichier `docs/scalping.js`, chargé depuis `docs/index.html`.
- Bascule Long terme / Scalping dans l'écran Or existant (`#or`).
- Récupération de bougies 1min XAU/USD via l'API **Twelve Data** (voir
  "Source de données" ci-dessous), en JS côté client.
- Moteur de signal combinant 4 familles de règles du cours : **Tendance**,
  **Support/Résistance**, **Indicateurs techniques** (RSI, MACD,
  Bollinger), **Chandeliers japonais** — selon la logique de confluence
  détaillée plus bas.
- Affichage d'un signal (Achat/Vente/Neutre) avec **Entrée + Stop-loss +
  TP (objectif unique)** quand une confluence se forme, recalculé à
  chaque nouvelle bougie 1min clôturée, uniquement pendant que l'onglet
  Scalping est visible/actif.

**Hors périmètre (v1), décidé explicitement :**
- Volume et configurations volume+prix (or spot = marché OTC, pas de
  vrai volume tradé — fiabilité incertaine sur cet actif, cf. mise en
  garde du cours lui-même sur le "tick volume").
- Vagues d'Elliott (jugées peu fiables par le professeur même à
  l'échelle normale — encore plus bruitées sur du 1min).
- TP multiples (TP1/TP2, sortie partielle) — un seul niveau de TP,
  cohérent avec le module Indices existant (`entry_price`/`exit_price`).
- Tout calcul serveur/pipeline Python — aucune donnée scalping ne
  transite par `gold_score.py`, `indices_score.py`, ni par GitHub
  Actions.
- Masquage de la clé API (acceptée comme visible côté client — clé
  gratuite en lecture seule, risque limité à un épuisement de quota).

## Source de données — Twelve Data

- **Pourquoi** : offre XAU/USD intraday (y compris 1min), CORS activé
  pour un usage navigateur direct (contrairement à beaucoup d'API
  marché pensées pour un usage serveur), palier gratuit (800
  requêtes/jour, 8/minute) suffisant pour un usage personnel avec
  polling limité à l'onglet visible.
- **Endpoint** : `GET https://api.twelvedata.com/time_series` avec
  `symbol=XAU/USD`, `interval=1min`, `apikey=<clé>`, `outputsize`
  suffisant pour couvrir la fenêtre de calcul des pivots (voir
  ci-dessous — au moins ~60 bougies pour avoir assez de recul).
- **Clé API** : gratuite, créée par l'utilisateur sur twelvedata.com,
  intégrée directement dans `docs/scalping.js` (acceptée comme visible
  côté client, cf. "Hors périmètre").
- **Dégradation** : toute erreur (quota dépassé, réseau, réponse
  invalide) affiche un message d'erreur explicite ("Scalping
  indisponible pour l'instant") — jamais de signal basé sur une donnée
  périmée ou absente, jamais d'exception qui casse le reste de la page.

## Moteur de signal — logique de confluence

Fidèle au principe répété dans le cours : *"les indicateurs confirment,
ne déclenchent jamais seuls."* Un signal n'est émis que si un
déclencheur **structurel** (prix/tendance/niveau) est confirmé par au
moins un **indicateur** et un **pattern de bougie** allant dans le même
sens.

### 1. Détection des pivots (base de la tendance et des S/R)

Une bougie `i` est un **pivot haut** si son High est strictement le plus
élevé parmi les `K` bougies avant et les `K` bougies après elle (`K = 3`
en v1 — ajustable). Symétrique pour un **pivot bas** sur les Low. Un
pivot n'est confirmé qu'une fois les `K` bougies suivantes closes (donc
avec un délai de `K` minutes — inhérent à toute détection de pivot, à
assumer explicitement plutôt qu'à essayer de l'éliminer).

### 2. Tendance courte

- **Haussière** : les 2 derniers pivots bas confirmés sont strictement
  croissants ET les 2 derniers pivots hauts confirmés sont strictement
  croissants (règle du cours : "creux croissants d'abord, puis sommets
  croissants" — en v1, on exige les deux pour classifier "haussier"
  franc plutôt que "en formation").
- **Baissière** : symétrique (pivots hauts puis pivots bas décroissants).
- **Neutre** : ni l'un ni l'autre.

### 3. Support/Résistance dynamiques

- **Support** = dernier pivot bas confirmé sous le prix courant, non
  cassé depuis (pas de clôture sous ce niveau depuis sa formation).
- **Résistance** = dernier pivot haut confirmé au-dessus du prix
  courant, non cassé depuis.
- **Cassure (breakout)** : clôture au-delà du niveau.
- **Rebond** : prix qui s'approche du niveau (dans une tolérance à
  définir en implémentation, ex. quelques dixièmes de $) puis repart en
  sens inverse.

### 4. Indicateurs (formules du cours, cf. synthèse §6)

- **RSI(14)** : `100 − 100/(1+RS)`, `RS = moyenne(hausses)/moyenne(baisses)`
  sur 14 bougies. Surachat > 70-80, Survente < 20-30.
- **MACD(12,26,9)** : `EMA12 − EMA26`, ligne signal = `EMA9` de cet écart.
- **Bollinger(20,2)** : `MM20 ± 2×écart-type(20)`.
- Rôle dans la confluence : confirmation directionnelle uniquement (RSI
  pas extrême contre le sens du trade envisagé, MACD orienté dans le bon
  sens) — jamais un déclencheur autonome, conformément au cours.

### 5. Chandeliers (sous-ensemble du catalogue, v1)

Priorité aux figures jugées "efficace/très efficace" par le cours plutôt
qu'au catalogue complet (~20 figures) — YAGNI, extensible plus tard :
**Marteau, Étoile filante, Englobante haussière, Englobante baissière,
Étoile du Matin, Étoile du Soir, Pénétrante, Nuage noir.** Détection sur
les 2-3 dernières bougies closes, retourne un sens (haussier/baissier)
ou rien.

### 6. Combinaison → signal

```
structurel_achat  := tendance == "haussière" ET (rebond sur support OU cassure de résistance)
structurel_vente  := tendance == "baissière" ET (rebond sur résistance OU cassure de support)

confirmation_achat := RSI < 70 ET MACD haussier ET pattern_bougie == haussier
confirmation_vente := RSI > 30 ET MACD baissier ET pattern_bougie == baissier

SI structurel_achat ET confirmation_achat  → signal ACHAT
SINON SI structurel_vente ET confirmation_vente → signal VENTE
SINON → aucun signal (neutre)
```

### 7. Stop-loss et TP

- **Stop-loss** : niveau du pivot invalidant (support pour un achat,
  résistance pour une vente) avec une petite marge de sécurité au-delà.
- **TP** : prochain niveau de S/R significatif dans le sens du trade
  (pivot suivant au-delà de l'entrée) ; si aucun niveau clair n'existe
  dans la fenêtre de calcul, repli sur un multiple fixe du risque (ex.
  1,5-2× la distance entrée-stop) — valeur exacte à figer en
  implémentation.

## Interface

- Dans l'écran `#or`, un bascule (boutons ou onglets, même pattern
  visuel que l'existant) entre **Long terme** (contenu actuel) et
  **Scalping** (nouveau).
- Vue Scalping : cours actuel, statut du signal (Achat/Vente/Neutre),
  si signal actif → Entrée/Stop-loss/TP, et un état "Neutre — en attente
  de confluence" sinon. Rafraîchi à chaque bougie 1min tant que l'onglet
  est visible (API `document.visibilityState` pour suspendre le polling
  en arrière-plan).
- Avertissement explicite dans l'UI (cohérent avec le reste du site) :
  pas un conseil d'investissement, signaux probabilistes.

## Tests

Ce dépôt n'a pas d'infrastructure de test JS (constaté sur les modules
frontend précédents — vérification manuelle uniquement jusqu'ici). Ce
module introduit cependant une logique de calcul non triviale
(indicateurs, détection de pivots, pattern matching de bougies) plus
risquée à valider seulement à l'œil qu'un simple ajout d'UI. **Décision
à confirmer en revue de spec** : ajouter des tests JS unitaires purs
(sans framework, fonctions testées isolément avec des données
synthétiques connues) pour au moins RSI/MACD/Bollinger et la détection
de pivots, exécutables via Node en CI-like local (`node
docs/scalping.test.js` ou équivalent) — à trancher précisément dans le
plan d'implémentation. Le pattern matching de bougies et la logique de
confluence resteront vérifiés manuellement (comme le reste du frontend),
sauf si la revue de spec demande davantage.

## Risques connus, assumés

- Détection de pivots avec délai de confirmation (`K` bougies) —
  inhérent à la méthode, pas un bug.
- Tick volume non fiable sur l'or spot → volume explicitement exclu du
  v1 plutôt que d'utiliser une donnée trompeuse.
- Quota API gratuit (800 req/jour) pourrait être serré si le site a
  plusieurs visiteurs simultanés sur l'onglet Scalping — pas un problème
  pour un usage personnel, à surveiller si le trafic change.
- Aucun signal n'a jamais été vérifié en conditions de marché réelles à
  ce stade (design uniquement) — la première vérification réelle aura
  lieu à l'implémentation, avec le marché de l'or réellement ouvert
  (session forex 24/5, pas 24/7 — le week-end le module affichera
  probablement une absence de données fraîches, à gérer proprement).
