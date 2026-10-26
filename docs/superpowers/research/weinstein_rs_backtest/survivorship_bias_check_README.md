# Biais de survivance — vérification réelle (pas supposée)

RECHERCHE / EXPLORATOIRE — PAS DU CODE DE PRODUCTION. Complète
`README.md` (moteur principal), `walk_forward_robustness_README.md` et
`oversold_validation_README.md` (chantiers parallèles de la même session).

## Contexte

Toute la recherche de cette session (signal Weinstein+RS, walk-forward
renforcé, `oversold`) repose sur un univers construit à partir de
`docs/indices.json` — supposé être la composition **actuelle** des 8
indices du périmètre, appliquée rétroactivement sur 2020-2026. L'hypothèse
répétée à chaque étape ("biais présent mais jugé symétrique") n'avait
jamais été vérifiée concrètement. Tâche ici : la vérifier pour de vrai.

## Méthodologie et sa limite

1. Cherché une source fiable et datée des sorties d'indice depuis
   2020-10, pour 2 indices représentatifs (France/Europe et US) :
   - **CAC40** : le tableau "Historique de la composition" sur
     [fr.wikipedia.org/wiki/CAC_40](https://fr.wikipedia.org/wiki/CAC_40)
     donne des dates précises et exploitables → **retenu**.
   - **DOW** : [en.wikipedia.org/wiki/Historical_components_of_the_Dow_Jones_Industrial_Average](https://en.wikipedia.org/wiki/Historical_components_of_the_Dow_Jones_Industrial_Average)
     donne 3 vagues de changements datées → **retenu**.
   - **FTSE100** (3ᵉ indice envisagé pour contraste, car structurellement
     le plus exposé — voir plus bas) : ni la page Wikipedia (liste "Past
     constituents" non datée) ni une recherche web ciblée (résultats
     fragmentés sur plusieurs sites de presse financière, dates parfois
     contradictoires d'un article à l'autre) n'ont donné une liste fiable
     sans un travail de reconstitution manuelle disproportionné pour ce
     chantier → **non retenu, angle mort explicite** (voir Limites).
2. Pour chaque sortie trouvée (CAC40 : 7, dont 1 ticker — Vivendi —
   compté une seule fois malgré 2 sorties/1 retour ; DOW : 4), vérifié
   dans `docs/indices.json` si le ticker est **complètement absent** de
   l'univers suivi, ou s'il est encore présent sous une **autre**
   étiquette d'indice déjà incluse dans l'univers de backtest complet
   (`walk_forward_eval.py` ne filtre jamais par indice nominal — il prend
   tout ce qui existe dans `docs/price_history.json`).
3. Script : `survivorship_bias_check.py` (reproductible, aucune clé API
   requise pour cette partie — seulement `docs/indices.json` et
   `docs/price_history.json` déjà dans le repo).

## Résultat : le biais est bien plus petit que l'hypothèse de travail

**9 des 10 sorties vérifiées ne sont PAS absentes du backtest.** Elles
sont toujours suivies sous un indice plus large déjà inclus dans
l'univers complet, avec un historique de prix couvrant 2020-10-30 →
2026-10-26 (confirmé dans `docs/price_history.json`) :

| Ticker | Sortie de | Date | Toujours suivi sous |
|---|---|---|---|
| ATO.PA (Atos) | CAC40 | 2021-09 | SBF120 |
| VIV.PA (Vivendi) | CAC40 | 2023/2024 | SBF120 |
| WLN.PA (Worldline) | CAC40 | 2023-12 | SBF120 |
| ALO.PA (Alstom) | CAC40 | 2024-03 | SBF120 |
| TEP.PA (Teleperformance) | CAC40 | 2025-09 | SBF120 |
| EDEN.PA (Edenred) | CAC40 | 2025-12 | SBF120 |
| INTC (Intel) | DOW | 2024-11 | NASDAQ |
| DOW (Dow Inc) | DOW | 2024-11 | SP500 |
| VZ (Verizon) | DOW | 2026-06 | SP500 |

Raison structurelle : ce projet suit aussi SBF120 (120 valeurs
françaises, super-ensemble du CAC40) et SP500/NASDAQ (super-ensembles du
DOW). Une entreprise qui sort de son indice "vedette" par simple
rétrogradation (perte de rang, pas disparition économique) reste quasi
toujours attrapée par l'indice plus large — elle n'est jamais vraiment
sortie de l'univers suivi.

**1 seule exception trouvée : Walgreens Boots Alliance (WBA)**, sortie
du DOW le 2024-02-26. Totalement absente de `docs/indices.json` (aucun
indice suivi ne la couvre) et de `docs/price_history.json` (zéro ligne).
Raison : l'entreprise est passée au capital privé en 2025 (rachat par
Sycamore Partners) — une disparition économique réelle, pas un simple
changement d'étiquette. Tentative de reconstruction de son historique
pour la réintégrer et mesurer l'effet précis : **échec, pas une limite de
méthode mais une vraie absence de donnée accessible** — Yahoo Finance
(`yfinance`, avec le contournement SSL Norton déjà en place dans
`trader_scanner_factor.py`) renvoie `404 Quote not found` pour WBA, et
Stooq renvoie aussi `404` : les deux sources gratuites ont purgé
l'historique de ce titre après son retrait de la cote.

## Pourquoi ce cas unique ne change pas le résultat d'ensemble

WBA est 1 ticker sur ~1300 dans l'univers de backtest, avec un plafond
de `MAX_NEW_ENTRIES_PER_WEEK = 3` dans `backtest_engine.py` — même
réintégré, son poids dans n'importe quelle fenêtre walk-forward serait
structurellement marginal. Ne pas avoir pu le chiffrer exactement ne
remet donc pas en cause le constat global : sur l'échantillon vérifié
(10 sorties, 2 indices, France + US), le biais de survivance est réel
mais **très localisé** (1 cas sur 10), pas le phénomène diffus et
potentiellement important que l'hypothèse "présent mais symétrique"
laissait craindre à chaque étape de cette session.

## Limites qui restent entières

- **FTSE100 non vérifié** (voir Méthodologie) — c'est l'indice le plus
  exposé en théorie : ce projet ne suit aucun indice britannique plus
  large en super-ensemble (pas de "SBF120 anglais"), donc une sortie du
  FTSE100 pourrait être une vraie disparition de l'univers, contrairement
  à ce qui a été montré ici pour la France et les US. Angle mort
  explicite, pas une extrapolation du résultat CAC40/DOW vers le
  Royaume-Uni.
- **6 indices non vérifiés du tout** sur les 8 du périmètre annoncé
  (SMI, IBEX35, FTSEMIB, et les indices hors périmètre explicite mais
  présents dans `docs/indices.json` : AEXALL, EUROSTOXX50, HANGSENG,
  NIKKEI225, SP500, SBF120 eux-mêmes). Rien ne garantit que le même
  schéma de rattrapage par un indice plus large s'applique partout —
  SMI/IBEX35/FTSEMIB n'ont pas de super-ensemble visible dans
  `docs/indices.json` non plus.
- Le cas WBA montre qu'une disparition **économique réelle** (faillite,
  retrait de cote, passage au privé) n'est, par construction, jamais
  rattrapée par un indice plus large — seules les rétrogradations de
  rang le sont. Le vrai risque résiduel de biais de survivance porte
  donc sur CE type de sortie (disparitions réelles), pas sur les
  simples changements d'indice vedette — et ce risque n'a pas pu être
  quantifié ici par manque de données gratuites accessibles.

## Verdict

Le biais de survivance de ce backtest est **réel mais nettement plus
petit** que l'hypothèse de travail répétée toute la session ("présent
mais symétrique, jamais mesuré"). Sur l'échantillon vérifié, il ne
touche que les disparitions économiques réelles (passage au privé,
faillite), pas les simples rétrogradations d'indice — et celles-ci
semblent rares (1 cas identifié sur 10 sorties vérifiées). Ne pas
étendre cette conclusion au-delà de ce qui a été vérifié : le FTSE100 et
6 autres indices du périmètre restent un angle mort non testé.
