# Remplacement du moteur de signal du bot Or par le suivi de tendance (figures chartistes) — design

## Statut

Validé par l'utilisateur le 2026-09-29, prêt pour le plan d'implémentation.

## 1. Contexte et objectif

Le bot Or est en production réelle depuis le 2026-09-28 (`dry_run: false`,
compte MetaApi dédié). L'audit pré-lancement du 2026-09-27
(`docs/superpowers/specs/2026-09-10-bot-trading-or-design.md` pour le
design d'origine, mémoire `project_strategy_audit.md` pour l'audit) a
confirmé le moteur contre-tendance actuel (`gold_bot/confluence.py::
compute_signal`, reversal sur chandelier + confluence tendance/S-R/
Bollinger/RSI/MACD) trop rare pour être utile : ~4 trades/90 jours en
backtest réel, confirmé en production (aucun signal en ~36h de
fonctionnement réel). Trois leviers de réglage testés empiriquement le
27/09 (cible au-delà du niveau opposé, seuil R:R abaissé, tolérance de
proximité élargie) ont tous été rejetés — conclusion : ce n'est pas un
problème de paramètre, il faut un moteur de signal différent.

L'utilisateur demande maintenant (29/09) le remplacement complet par le
moteur de suivi de tendance déjà construit et testé côté site,
`docs/chart_patterns.js` (Double Top/Bottom, Tête-Épaule ×2, Triangles
×3), jamais porté côté bot jusqu'ici (décision du 22/09 de garder le bot
pur contre-tendance, explicitement revue le 27/09).

## 2. Périmètre

### Dans le périmètre

- Portage fidèle de `detectChartPatterns` (`docs/chart_patterns.js`) en
  Python, `gold_bot/chart_patterns.py`, avec les mêmes 9 cas de test que
  `docs/chart_patterns.test.js`.
- **Remplacement complet** de `gold_bot/confluence.py::compute_signal` —
  le moteur contre-tendance sur chandelier (RSI/MACD/Bollinger/pattern de
  chandelier) est retiré, pas conservé en option. Suppression du code
  mort associé (fonctions et constantes plus utilisées par personne).
- Nouvelle méthode entrée/stop-loss/take-profit basée sur
  `breakoutPrice`/`patternHeight` (mesure de la figure).
- Le filtre garde-fou de ratio risque/rendement minimum
  (`meets_minimum_risk_reward`) reste actif, réutilisé tel quel.
- Validation empirique par backtest réel (`gold_bot/backtest.py`, déjà
  existant, aucune modification requise) avant tout déploiement en
  production.

### Explicitement hors périmètre

- Tout changement aux garde-fous eux-mêmes (`risk.py`, `bot.py::
  decide_and_act`, coupe-circuit, plafond de levier, arrondi au pas
  broker, garde marché fermé/données périmées, black-out macro,
  dry_run/kill_switch) — seule la détection du signal change.
- Élargissement de la fenêtre de bougies (`fetch_gold_candles`,
  `outputsize=90`) — gardée à 90 pour cette version ; à reconsidérer
  seulement si le backtest de validation montre une fréquence encore
  insuffisante (voir §8).
- Coexistence des deux moteurs (option "ajout" explicitement écartée par
  l'utilisateur au profit du remplacement complet).

## 3. Architecture

### 3.1 Nouveau module `gold_bot/chart_patterns.py`

Portage direct, aucune divergence de comportement avec la version JS :

```python
# gold_bot/chart_patterns.py
# Détection de figures chartistes (Double Top/Bottom, Tête-Épaule,
# Triangles) — portage fidèle de docs/chart_patterns.js::detectChartPatterns
# (même comportement, mêmes 9 cas de test). Module autonome, sans
# dépendance vers gold_bot.confluence, comme son équivalent JS n'a pas
# de dépendance vers docs/scalping.js.

CHARTPATTERN_PIVOT_K = 5  # plus grand que l'ancien K=3 (tendance/S-R
                          # ponctuel) : une figure chartiste s'étale sur
                          # bien plus de bougies qu'un niveau ponctuel.
CHARTPATTERN_HEIGHT_TOLERANCE = 1.0  # $ de tolérance pour juger deux
                                      # sommets/creux "de hauteur comparable"


def _height_close(a: float, b: float) -> bool:
    return abs(a - b) <= CHARTPATTERN_HEIGHT_TOLERANCE


def detect_chart_patterns(pivots: list[dict], trend: str, price: float) -> dict | None:
    """Détecte une des 7 figures chartistes reconnues à partir de `pivots`
    (déjà calculés par l'appelant avec K=CHARTPATTERN_PIVOT_K), `trend`
    (classify_trend, gold_bot.confluence) et `price` (dernière clôture,
    pour juger si une cassure est confirmée). Renvoie la première figure
    trouvée (ordre : retournement d'abord, puis continuation) ou None.
    Ne lève jamais d'exception — pivots insuffisants renvoie simplement None."""
    highs = [p for p in pivots if p["type"] == "high"]
    lows = [p for p in pivots if p["type"] == "low"]

    # --- Double Top (retournement baissier) ---
    if trend == "haussier" and len(highs) >= 2:
        h1, h2 = highs[-2:]
        between = [l for l in lows if h1["index"] < l["index"] < h2["index"]]
        if between and _height_close(h1["price"], h2["price"]):
            neckline = between[-1]["price"]
            if price < neckline:
                return {
                    "name": "Double Top", "direction": "baissier", "kind": "retournement",
                    "breakoutPrice": neckline, "extremityPrice": max(h1["price"], h2["price"]),
                    "patternHeight": max(h1["price"], h2["price"]) - neckline,
                }

    # --- Double Bottom (retournement haussier) ---
    if trend == "baissier" and len(lows) >= 2:
        l1, l2 = lows[-2:]
        between = [h for h in highs if l1["index"] < h["index"] < l2["index"]]
        if between and _height_close(l1["price"], l2["price"]):
            neckline = between[-1]["price"]
            if price > neckline:
                return {
                    "name": "Double Bottom", "direction": "haussier", "kind": "retournement",
                    "breakoutPrice": neckline, "extremityPrice": min(l1["price"], l2["price"]),
                    "patternHeight": neckline - min(l1["price"], l2["price"]),
                }

    # --- Tête-Épaule (retournement baissier) ---
    if trend == "haussier" and len(highs) >= 3:
        s1, head, s2 = highs[-3:]
        troughs = [l for l in lows if s1["index"] < l["index"] < s2["index"]]
        if len(troughs) >= 2 and _height_close(s1["price"], s2["price"]) \
                and head["price"] > s1["price"] and head["price"] > s2["price"]:
            neckline = troughs[-1]["price"]
            if price < neckline:
                return {
                    "name": "Tête-Épaule", "direction": "baissier", "kind": "retournement",
                    "breakoutPrice": neckline, "extremityPrice": head["price"],
                    "patternHeight": head["price"] - neckline,
                }

    # --- Tête-Épaule inversée (retournement haussier) ---
    if trend == "baissier" and len(lows) >= 3:
        s1, head, s2 = lows[-3:]
        peaks = [h for h in highs if s1["index"] < h["index"] < s2["index"]]
        if len(peaks) >= 2 and _height_close(s1["price"], s2["price"]) \
                and head["price"] < s1["price"] and head["price"] < s2["price"]:
            neckline = peaks[-1]["price"]
            if price > neckline:
                return {
                    "name": "Tête-Épaule inversée", "direction": "haussier", "kind": "retournement",
                    "breakoutPrice": neckline, "extremityPrice": head["price"],
                    "patternHeight": neckline - head["price"],
                }

    # --- Triangle ascendant (continuation haussière) ---
    if trend == "haussier" and len(highs) >= 2 and len(lows) >= 2:
        h1, h2 = highs[-2:]
        l1, l2 = lows[-2:]
        if _height_close(h1["price"], h2["price"]) and l2["price"] > l1["price"]:
            resistance = h2["price"]
            if price > resistance:
                return {
                    "name": "Triangle ascendant", "direction": "haussier", "kind": "continuation",
                    "breakoutPrice": resistance, "extremityPrice": l1["price"],
                    "patternHeight": resistance - l1["price"],
                }

    # --- Triangle descendant (continuation baissière) ---
    if trend == "baissier" and len(highs) >= 2 and len(lows) >= 2:
        h1, h2 = highs[-2:]
        l1, l2 = lows[-2:]
        if _height_close(l1["price"], l2["price"]) and h2["price"] < h1["price"]:
            support = l2["price"]
            if price < support:
                return {
                    "name": "Triangle descendant", "direction": "baissier", "kind": "continuation",
                    "breakoutPrice": support, "extremityPrice": h1["price"],
                    "patternHeight": h1["price"] - support,
                }

    # --- Triangle symétrique (continuation, sens déterminé par la cassure) ---
    if len(highs) >= 2 and len(lows) >= 2:
        h1, h2 = highs[-2:]
        l1, l2 = lows[-2:]
        converging = h2["price"] < h1["price"] and l2["price"] > l1["price"]
        if converging:
            if trend == "haussier" and price > h2["price"]:
                return {
                    "name": "Triangle symétrique", "direction": "haussier", "kind": "continuation",
                    "breakoutPrice": h2["price"], "extremityPrice": l1["price"],
                    "patternHeight": h1["price"] - l1["price"],
                }
            if trend == "baissier" and price < l2["price"]:
                return {
                    "name": "Triangle symétrique", "direction": "baissier", "kind": "continuation",
                    "breakoutPrice": l2["price"], "extremityPrice": h1["price"],
                    "patternHeight": h1["price"] - l1["price"],
                }

    return None
```

### 3.2 `gold_bot/confluence.py` — remplacement de `compute_signal`

**Constantes supprimées** (plus utilisées par personne une fois
`compute_signal` remplacé) : `SCALP_PIVOT_K`, `SCALP_RSI_PERIOD`,
`SCALP_MACD_FAST/SLOW/SIGNAL`, `SCALP_BOLLINGER_PERIOD/MULT`,
`SCALP_LEVEL_PROXIMITY`, `SCALP_STOP_BUFFER`.

**Constantes conservées** : `SCALP_TAKEPROFIT_RISK_MULTIPLE = 1.5`
(seuil minimum du filtre R:R, toujours utilisé par
`meets_minimum_risk_reward`, INCHANGÉE), `SCALP_NEWS_BLACKOUT_MINUTES`/
`SCALP_HIGH_IMPACT_EVENTS_UTC` (garde macro, inchangée).

**Constante redéfinie** :
```python
SCALP_MIN_CANDLES = 2 * chart_patterns.CHARTPATTERN_PIVOT_K + 1  # 11
```
(plancher minimal pour qu'`detect_pivots` puisse trouver ne serait-ce
qu'un pivot avec K=5 ; le vrai filtrage — assez de pivots pour une
figure complète — est déjà géré à l'intérieur de
`detect_chart_patterns`, qui renvoie `None` proprement si les pivots
disponibles ne suffisent pas.)

**Nouvelle constante** :
```python
CHARTPATTERN_STOP_BUFFER = chart_patterns.CHARTPATTERN_HEIGHT_TOLERANCE  # réutilise l'échelle existante ($1), pas un nouveau nombre magique
```

**Fonctions supprimées** (code mort une fois `compute_signal`
remplacé, avec leurs tests dans `tests/gold_bot/test_confluence.py`) :
`compute_rsi`, `_ema_last`, `_ema_series`, `compute_macd`,
`compute_bollinger`, `_body_size`, `_is_bullish`, `_upper_wick`,
`_lower_wick`, `match_candlestick_pattern`, `current_levels`.

**Fonctions conservées, inchangées** : `detect_pivots`, `classify_trend`
(réutilisées avec `CHARTPATTERN_PIVOT_K` au lieu de l'ancien
`SCALP_PIVOT_K`), `is_news_blackout`, `meets_minimum_risk_reward`,
`fetch_gold_candles`, `_parse_float`.

**Nouveau `compute_signal`** :

```python
import gold_bot.chart_patterns as chart_patterns


def compute_signal(candles: list[dict]) -> dict:
    """Moteur de suivi de tendance par figures chartistes (portage de
    docs/chart_patterns.js::detectChartPatterns, voir gold_bot/chart_patterns.py)
    — remplace le moteur contre-tendance sur chandelier utilisé jusqu'au
    2026-09-29, retiré de ce fichier (confirmé trop passif en production
    réelle : aucun signal en ~36h avec dry_run désactivé, cohérent avec
    le backtest du 27/09 : ~4 trades/90 jours). Ne lève jamais
    d'exception -- `candles` trop court ou aucune figure détectée renvoie
    neutre avec tous les prix à None, de même qu'en pleine fenêtre de
    black-out macro (voir is_news_blackout)."""
    price = candles[-1]["close"] if candles else None
    if not price or len(candles) < SCALP_MIN_CANDLES:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}
    as_of = datetime.fromisoformat(candles[-1]["time"].replace(" ", "T")).replace(tzinfo=timezone.utc)
    if is_news_blackout(as_of):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}

    pivots = detect_pivots(candles, chart_patterns.CHARTPATTERN_PIVOT_K)
    trend = classify_trend(pivots)
    pattern = chart_patterns.detect_chart_patterns(pivots, trend, price)
    if pattern is None:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": trend, "pattern": None}

    direction = "achat" if pattern["direction"] == "haussier" else "vente"
    breakout = pattern["breakoutPrice"]
    height = pattern["patternHeight"]
    if direction == "achat":
        stop_loss = breakout - CHARTPATTERN_STOP_BUFFER
        take_profit = breakout + height
    else:
        stop_loss = breakout + CHARTPATTERN_STOP_BUFFER
        take_profit = breakout - height

    if not meets_minimum_risk_reward(price, stop_loss, take_profit, direction):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": trend, "pattern": None}

    return {"status": direction, "price": price, "entry": price, "stop_loss": stop_loss,
            "take_profit": take_profit, "trend": trend, "pattern": pattern}
```

Note : `entry` reste le prix courant (`price`), pas `breakoutPrice` — au
moment où la figure est détectée, la cassure a déjà eu lieu (les
conditions `price < neckline`/`price > resistance` etc. dans
`detect_chart_patterns` ne renvoient une figure qu'une fois la cassure
confirmée) ; entrer au prix courant est la même convention que l'ancien
moteur.

### 3.3 Aucun changement à `gold_bot/bot.py`, `gold_bot/loop.py`, `gold_bot/backtest.py`

`bot.py::decide_and_act` consomme `signal["status"]/["entry"]/
["stop_loss"]/["take_profit"]` — forme inchangée. `backtest.py` appelle
`confluence.compute_signal`/`confluence.SCALP_MIN_CANDLES`/
`confluence.is_news_blackout` génériquement et ne lit que
`signal["pattern"]["name"]` (toujours présent, la figure a juste plus de
champs qu'avant) — aucune modification requise dans ces deux fichiers.

## 4. Tests

- **`tests/gold_bot/test_chart_patterns.py`** (nouveau) : les 9 cas
  exacts de `docs/chart_patterns.test.js` (Double Top détecté/rejeté
  pour écart de hauteur, Double Bottom, Tête-Épaule ×2, Triangles ×3
  avec cassure haussière et baissière pour le symétrique), transcrits
  fidèlement (mêmes pivots, même tendance, même prix, mêmes valeurs
  attendues).
- **`tests/gold_bot/test_confluence.py`** : suppression des tests des
  fonctions retirées (`compute_rsi`, `compute_macd`, `compute_bollinger`,
  `match_candlestick_pattern` — y compris les fixtures de bougies
  méticuleusement calibrées `_build_bearish_then_hammer_candles`/
  `_build_bullish_then_shooting_star_candles`, plus nécessaires,
  `current_levels`). Tests conservés pour `detect_pivots`,
  `classify_trend`, `is_news_blackout`, `meets_minimum_risk_reward`,
  `fetch_gold_candles`. Nouveaux tests d'intégration pour
  `compute_signal` : au moins un cas bout-en-bout (bougies synthétiques
  produisant les pivots d'un Triangle ascendant → signal "achat" avec
  stop/cible cohérents avec la mesure de la figure), un cas "aucune
  figure" → neutre, un cas black-out macro → neutre (déjà couvert par un
  test existant à adapter), un cas fenêtre trop courte → neutre.

## 5. Validation avant déploiement (non négociable)

Une fois Tests 1-2 en place et verts :
1. Backtest réel 90 jours (`gold_bot/backtest.py`, sur le VPS, même
   méthode que le 27/09) avec le nouveau moteur — fréquence, taux de
   réussite, P&L net, R:R moyen réalisé.
2. Résultats présentés à l'utilisateur avant tout déploiement — pas de
   décision unilatérale de déployer si les chiffres sont mauvais, même
   sous pression de temps (cohérent avec le refus du 27/09 de déployer
   un réglage non validé empiriquement).
3. Déploiement = `git pull` + `systemctl restart gold-bot-loop` sur le
   VPS (contrairement à `state.json`/`.env`, le code Python n'est chargé
   qu'au démarrage du process — un redémarrage est nécessaire ici,
   contrairement au changement de `dry_run` du 28/09).

## 6. Points arbitrés

- **Remplacement complet, pas coexistence** — décision explicite de
  l'utilisateur (29/09), plus simple à raisonner/tester/déployer qu'une
  logique de priorité entre deux moteurs.
- **Les 7 figures portées, pas seulement les 3 de continuation** — reste
  fidèle au moteur du site déjà testé plutôt que d'inventer une variante
  réduite ; les figures de retournement (Double Top/Bottom, Tête-Épaule)
  sont aussi susceptibles de se déclencher plus souvent que l'ancien
  moteur candlestick-only.
- **RSI/MACD/Bollinger abandonnés, pas recollés en confirmation
  supplémentaire** — c'était la confirmation du moteur contre-tendance
  retiré ; les rajouter recréerait le sur-filtrage diagnostiqué le
  27/09 (RSI/MACD seuls coupaient déjà la moitié des configurations
  valides dans l'ancien moteur).
- **Fenêtre de 90 bougies conservée pour cette version** — pas de
  certitude qu'elle soit suffisante pour laisser le temps à une figure
  de se former ; si le backtest de validation (§5) montre une fréquence
  encore trop faible, élargir la fenêtre (`fetch_gold_candles`,
  `SIGNAL_WINDOW_SIZE`) est le levier suivant à tester empiriquement —
  pas un problème à deviner à l'avance.
- **Stop de cassure = tolérance de hauteur existante ($1), pas un
  nouveau nombre magique** — même principe que le reste du fichier
  (réutiliser une constante déjà justifiée plutôt qu'en inventer une).
