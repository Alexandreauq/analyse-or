# Backtest Weinstein + force relative + filtre de volume

**Dossier de recherche / exploratoire — PAS du code de production.**
Rien ici n'est branche sur le site, le bot IBKR, le bot gold ou tout
autre systeme live. Conserve uniquement pour ne pas reperdre le moteur
de backtest reconstruit le 2026-10-11, suite a une demande de
l'utilisateur sur ce point precis.

## Contexte

Une session de recherche precedente (2026-10-10/11) avait valide une
strategie sur grandes capitalisations mondiales (~1300 tickers) :

- **Phase Weinstein "Achat"** (transition) : prix > MM30 semaines ET
  pente de la MM30 sur 4 semaines > seuil de bruit ±0.5 %.
- **Filtre de force relative** : rendement sur 13 semaines, percentile
  cross-sectionnel, ne garder que le top 20-30 % de l'univers.
- **Stop-loss** a -10 %, **sortie** a la fin de la phase Achat.
- **Coat de transaction** 0.30 % aller-retour, **budget nominal** 500 €
  par position, **plafonds** 3 nouvelles entrees/jour et 3/secteur,
  **pas de plafond de positions total**.

Resultat rapporte a l'epoque : +99.3 % cumule en validation (2024 ->
aujourd'hui) contre +144.8 % pour un buy & hold equipondere du meme
univers sur la meme periode (pic de 36 positions simultanees, capital
de base 18 000 €).

**Le script qui a produit ces chiffres n'a pas ete retrouve dans le
depot** (aucun commit, aucun fichier conserve — recherche exhaustive
par `git log`, `grep`, `glob` avant de reconstruire). Les fichiers de
ce dossier sont une **reconstruction** de ce moteur, ecrite pour tester
une nouvelle hypothese (filtre de confirmation par le volume), pas une
recuperation du code original.

## Nouvelle hypothese testee : confirmation par le volume

Idee : une vraie cassure haussiere (entree en phase Achat) devrait
s'accompagner d'un volume anormalement eleve. A la semaine d'entree, on
calcule le ratio volume de la semaine / moyenne des N semaines
precedentes, et on teste si exiger un ratio minimum ameliore la
strategie.

- N testes : 8 et 13 semaines.
- Seuils testes : > 1.0 (= pas de filtre, reference), > 1.2, > 1.5, > 2.0.
- Selection stricte apprentissage (debut -> 2023-12-31) / validation
  (2024-01-01 -> aujourd'hui) : le seuil est choisi sur l'apprentissage
  seul (rendement net + Calmar), puis applique sans retouche en
  validation.

### Resultat : le filtre est rejete

| Variante | N | Seuil | Trades (appr.) | Rendement appr. | Calmar appr. | Trades (val.) | Rendement val. |
|---|---|---|---|---|---|---|---|
| **Sans filtre** | – | 1.0 | 405 | **+37.18 %** | **0.598** | 430 | **+112.50 %** |
| N8 | 8 | 1.2 | 327 | +10.59 % | 0.125 | 339 | +110.94 % |
| N8 | 8 | 1.5 | 254 | +4.36 % | 0.068 | 258 | +78.50 % |
| N8 | 8 | 2.0 | 137 | +5.31 % | 0.078 | 158 | +86.81 % |
| N13 | 13 | 1.2 | 328 | +2.09 % | 0.031 | 333 | +107.56 % |
| N13 | 13 | 1.5 | 259 | +8.77 % | 0.139 | 241 | +68.60 % |
| N13 | 13 | 2.0 | 131 | +17.92 % | 0.235 | 151 | +99.13 % |

Sur l'apprentissage, **aucune des 6 combinaisons (N, seuil) n'approche
la version sans filtre**, ni en rendement net ni en Calmar. Le
rendement moyen par trade s'effondre dès qu'on filtre (6.24 % sans
filtre -> aussi bas que 0.34 % filtre), sans amelioration du taux de
reussite (32-36 % dans tous les cas) : le volume eleve a l'entree ne
selectionne pas de meilleurs trades, il en retire surtout beaucoup
(souvent gagnants) sans contrepartie de qualite.

**Conclusion : ne pas ajouter de filtre de volume a l'entree.** La
variante retenue par la procedure (meilleure sur apprentissage) est
"sans filtre", reportee telle quelle en validation.

## Hypotheses de reconstruction (a relire avant de reutiliser ce moteur)

Faute du script d'origine, plusieurs choix ont du etre fixes
explicitement :

1. **Univers** : tous les tickers de `docs/price_history.json` (1295
   apres exclusion des tickers indiciels `^...`, `FTSEMIB.MI`, de
   l'ancien ticker `MT.PA` remplace par `MT.AS`, et des 3 tickers a
   convention de devise non fiable `IHG.L`/`MTLN.L`/`CPG.L`, deja
   connus comme problematiques). Ce total (~1300) colle a l'univers
   annonce, mais couvre en realite plus que les 8 indices cites
   (CAC40/DAX/SP500/Dow/FTSE100/SMI/IBEX35/FTSEMIB) — probablement
   Nikkei225/Hang Seng/Nasdaq/AEX/SBF120 etaient deja inclus a
   l'epoque aussi.
2. **Force relative "top 20-30 %"** : operationnalise en **percentile
   >= 0.70** (= top 30 %, la borne la plus inclusive de la fourchette
   donnee). Non recalibre sur les chiffres connus — choix fixe a
   priori, pas un ajustement a posteriori.
3. **Plafonds "3 entrees/jour" et "3/secteur"** : interpretes **par
   semaine**, puisque les donnees de prix sont hebdomadaires.
4. **Secteurs** : recuperes depuis `docs/indices.json` (`companies[].sector`).
   5 tickers sans secteur connu (trusts d'investissement LSE) regroupes
   sous "Inconnu".
5. **Semaines** : grille ISO (annee, numero de semaine), prix pivote sur
   cette grille (derniere valeur si plusieurs lignes dans la meme
   semaine), volume quotidien somme par semaine ISO. 313 semaines,
   2020-W42 -> 2026-W41.

## Bug trouve et corrige pendant la reconstruction

`compute_entry_signal` (detection de la semaine de transition vers
"Achat") calculait a l'origine `is_achat & (~is_achat.shift(1))`.
`shift(1)` introduit un NaN en premiere ligne, ce qui fait passer la
colonne booleenne en dtype `object` (melange bool/NaN) ; appliquer `~`
a des `True`/`False` Python en contexte `object` donne `-2`/`-1`, qui
sont **tous les deux vrais** au sens Python. Le filtre de transition
etait donc annule silencieusement : 100 % des semaines "Achat" etaient
comptees comme une nouvelle entree (162 374 signaux), au lieu de la
seule semaine de bascule (12 841 signaux apres correction). Corrige en
travaillant avec des tableaux numpy bool purs (voir le commentaire dans
`backtest_engine.py::compute_entry_signal`).

## Ecart avec les chiffres memorises de la session precedente — non resolu

Avec le moteur reconstruit et corrige, **sans filtre de volume** :

- Apprentissage (2020 -> 2023) : +37.2 % net, 405 trades, pic 68
  positions simultanees (capital 34 000 €), drawdown -17.2 %, Calmar 0.60.
- Validation (2024 -> aujourd'hui) : **+112.5 %** net, 430 trades, pic
  79 positions (capital 39 500 €), drawdown -11.1 %, Calmar 2.80.
- Buy & hold equipondere meme univers : apprentissage +69.4 %,
  validation **+71.7 %** (verifie aussi avec un indice rebalance
  chaque semaine et compose : +70.1 %, donc pas un artefact de
  methode de calcul du buy & hold).

Cela ne correspond pas aux chiffres memorises (+99.3 % strategie /
+144.8 % buy & hold en validation, pic 36 positions / 18 000 €).

Piste envisagee avant d'ecrire ce README : l'ecart serait du a une
confusion entre la performance sur la **periode complete** 2021-2026
et la performance sur la **validation seule** 2024-2026 (le +144.8 %
buy & hold aurait ete calcule sur la mauvaise fenetre). **Cette piste a
ete testee et ne tient pas** : le buy & hold equipondere sur la periode
complete (2020-W42 -> 2026-W41) donne **+209.8 %**, ni +144.8 % ni
+71.7 %. L'ecart reste donc **non explique** — probablement du au
script original perdu (parametres ou univers legerement differents de
ceux retenus ici), mais ce n'est pas confirme. A traiter comme un
signal d'alerte si ce moteur est reutilise pour une decision
d'investissement : revalider les hypotheses de reconstruction
ci-dessus avant de faire confiance aux chiffres absolus (le resultat
qualitatif sur le filtre de volume — rejete — est en revanche robuste,
il est coherent sur les deux fenetres N et les 3 seuils testes).

## Comment reutiliser ce moteur

Donnees non committees (voir ci-dessous) :

```
python build_weekly_grid.py <dossier_csv_volume_vps> <dossier_sortie>
python run_analysis.py   # baseline seule, verification des gros trades, buy & hold
python run_grid.py       # grille complete N x seuil, apprentissage + validation
```

- `build_weekly_grid.py` lit `docs/price_history.json` et
  `docs/indices.json` du depot (chemins relatifs, calcules
  automatiquement depuis l'emplacement du script), plus les CSV
  quotidiens de volume par ticker (`<ticker>.csv`, colonnes
  `date,volume`).
- Donnees volume brutes (non committees, 37 Mo) : au moment de cette
  etude, elles se trouvaient sur le VPS de production a
  `/home/goldbot/analyse-or/volume_research/raw/*.csv` (1312 tickers,
  6 ans d'historique quotidien via yfinance). Pas garanti d'y etre
  encore a une date ulterieure — a reconstruire avec
  `yf.Ticker(t).history(period="6y")["Volume"]` si besoin, pour la
  liste de tickers de `docs/price_history.json`.
- Les pickles intermediaires (`data/processed/*.pkl`) et les caches de
  resultats (`data/*.pkl`) sont generes localement par les scripts et
  ne sont pas committes.
