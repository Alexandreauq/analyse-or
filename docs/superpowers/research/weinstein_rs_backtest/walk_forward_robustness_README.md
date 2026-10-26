# Renforcement de la validation walk-forward (Weinstein+RS)

**Recherche / exploratoire — pas du code de production.** Complète
`walk_forward_eval.py` (6/7 fenêtres non chevauchantes, voir ci-dessous),
jamais un remplacement. Script : `walk_forward_robustness.py`.

## Constat préalable : le nombre de fenêtres fixes a déjà changé

`walk_forward_eval.py` a été exécuté une première fois avec des données
s'arrêtant à **2026-W41**, donnant **6** fenêtres fixes non chevauchantes
(5/6 battent le buy&hold). `docs/price_history.json` s'est enrichi de 3
semaines depuis (mises à jour automatiques du batch quotidien) : il va
désormais jusqu'à **2026-W44** (314 semaines). Avec cette donnée plus
récente, le même découpage produit **7** fenêtres fixes, pas 6 — la 7ᵉ
étant une fenêtre partielle récente et quasi neutre (écart ≈ 0.0 pt).
**Le chiffre "5/6" communiqué précédemment est donc déjà légèrement
périmé : c'est "5/7" avec les données d'aujourd'hui**, sans changement
qualitatif (même unique fenêtre perdante, celle ancrée sur la reprise
post-COVID 2020-2021).

## Axe A — fenêtres glissantes mensuelles (seuil baseline 0.70)

61 fenêtres d'environ 1 an, ancrées chaque mois entre 2020-10-26 et
2025-10-26 (au lieu de 7 fenêtres fixes).

**⚠️ Ces fenêtres se chevauchent massivement (chevauchement de 11 mois
sur 12 entre deux fenêtres consécutives) : ce ne sont PAS 61 échantillons
indépendants.** Elles documentent plutôt une seule trajectoire continue
vue sous 61 angles de départ différents — utile pour voir *où* se
concentre la sur/sous-performance dans le temps, pas pour multiplier la
preuve statistique par 61.

**Résultat : 47/61 fenêtres (77%) battent le buy&hold.** Écart moyen
+13.0 pts, médiane +12.9 pts, écart-type 22.1 pts (min -53.8, max +54.6).

**Mais le détail dans le temps est net et informatif** : les 9 premières
fenêtres glissantes (toutes celles ancrées entre 2020-10-26 et
2021-06-26) sont **toutes négatives** (écart de -9 à -54 pts) — c'est
exactement la période de reprise en V post-COVID. Dès l'ancrage
2021-07-26, **toutes les fenêtres suivantes sans exception (52/52) sont
positives**, du +2.9 au +54.6, jusqu'à aujourd'hui.

**Interprétation honnête** : il n'y a pas 61 régimes indépendants ici,
il y en a deux au sens propre — (1) la reprise en V 2020-21, où la
stratégie perd nettement car elle entre en retard sur un marché qui
monte en synchronisation totale (son filtre de phase + son plafond de
3 entrées/semaine la pénalisent structurellement dans ce cas précis),
et (2) tout le reste depuis mi-2021, où elle bat systématiquement le
buy&hold sur chaque fenêtre glissante testée. Le fait que la bascule
soit nette et explicable (un régime de marché identifiable, pas du
bruit) est un point en faveur du signal plutôt qu'un hasard de
découpage — mais ça reste une seule transition de régime observée, pas
une preuve répétée.

## Axe B — sensibilité au seuil RS_TOP_PERCENTILE (0.60 à 0.80)

Le README documente ce seuil (0.70 = top 30%) comme un choix arbitraire
("la borne la plus inclusive de la fourchette donnée"), donc un candidat
naturel au surajustement. Testé sur les 7 fenêtres fixes d'origine :

| Seuil | Fenêtres battues | Écart moyen (pts) | Trades totaux |
|---|---|---|---|
| 0.60 | 5/7 | 6.73 | 843 |
| 0.65 | 5/7 | 6.73 | 843 |
| 0.70 (baseline) | 5/7 | 6.82 | 838 |
| 0.75 | 5/7 | 7.02 | 832 |
| 0.80 | 5/7 | 7.47 | 816 |

**Résultat : quasiment aucune sensibilité.** Le ratio de fenêtres
battues (5/7) ne change jamais sur toute la plage testée, l'écart moyen
varie à peine (6.73 → 7.47 pts), et les écarts fenêtre par fenêtre sont
presque identiques d'un seuil à l'autre (ex. fenêtre 2 : +40.0 à 0.60,
+43.2 à 0.80). **Ce n'est pas la signature d'un surajustement** — un
seuil cherry-pické donnerait typiquement un pic étroit de performance
autour de la valeur choisie, pas ce plateau plat sur 20 points de
percentile.

## Verdict global

Ce renforcement **augmente la confiance** dans le signal Weinstein+RS
par rapport à l'évaluation initiale à 6 points, sur deux axes précis et
limités :

1. **Pas de surajustement détecté sur le seuil RS_TOP_PERCENTILE** —
   un paramètre documenté comme arbitraire résiste à ±10 points de
   percentile sans que le résultat ne bouge significativement.
2. **La sous-performance n'est pas dispersée au hasard** — elle est
   concentrée sur une unique période identifiable (reprise post-COVID),
   explicable mécaniquement par la construction du signal, et absente
   de toutes les fenêtres glissantes depuis mi-2021.

**Ce que ça ne change PAS** : il reste structurellement **deux régimes
observés**, pas un grand échantillon — les 61 fenêtres glissantes ne
sont pas des preuves indépendantes, et le biais de survivance déjà
identifié (même univers des deux côtés) n'est pas re-testé ici. L'écart
non résolu entre les chiffres de cette reconstruction et ceux mémorisés
de la session d'origine (voir README.md principal) reste entier et
orthogonal à ce travail. Un signal plus robuste qu'avant, donc, mais
toujours pas une certitude statistique — et toujours vulnérable si un
futur régime de marché ressemble au second cas (reprise en V) plutôt
qu'au premier.
