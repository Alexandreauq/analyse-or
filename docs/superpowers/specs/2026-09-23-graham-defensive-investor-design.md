# Badge "Investisseur défensif" (Benjamin Graham) — design

## Statut

Validé par l'utilisateur le 2026-09-23, prêt pour le plan d'implémentation.

## 1. Contexte et objectif

Deuxième référence citée par l'utilisateur après avoir consulté des
professionnels de la finance (la première, Stan Weinstein, a donné le
badge de phase — voir `docs/superpowers/specs/2026-09-23-weinstein-stage-analysis-design.md`) :
Benjamin Graham, *L'investisseur intelligent*. Le spike du 2026-09-23 a
confirmé que la philosophie générale (marge de sécurité) est déjà
présente dans `estimate_entry_exit_prices`, mais a trouvé 3 trous
concrets par rapport aux critères chiffrés de l'"investisseur défensif"
de Graham — le profil le plus prudent et le plus quantifiable du livre :

- Le P/B (price-to-book) n'existe que pour le profil financier
  (`extract_ratios_financial`) — absent pour ~600 des 710 sociétés
  (profil standard), ce qui empêche tout calcul du "Graham Number"
  (P/E × P/B ≤ 22,5) pour la majorité de l'univers.
- Aucun suivi des dividendes nulle part dans le pipeline.
- Aucun ratio de liquidité court terme (actif circulant / passif
  circulant) — le modèle actuel ne regarde que des ratios de levier
  moyen terme (dette nette/EBITDA, ICR).

Objectif : un badge "Investisseur défensif" par société, réutilisant au
maximum les données déjà récupérées, adapté honnêtement aux limites
réelles des données disponibles (yfinance ne fournit que ~4 exercices,
pas les 10-20 ans que Graham utilise) plutôt que d'ignorer les critères
infaisables tels quels.

**Vision à plus long terme** (mentionnée par l'utilisateur, pas dans le
périmètre de cette spec) : plusieurs profils d'investisseur
sélectionnables un jour (défensif, entreprenant...). Cette spec ne
construit que le défensif, mais les noms choisis (`graham_defensive_*`,
pas `graham_*`) laissent la place à d'autres profils plus tard sans
renommage.

## 2. Périmètre

### Dans le périmètre

- P/B (`current_pb`/`avg_pb_5y`) calculé pour le **profil standard**
  aussi (déjà présent pour le profil financier) — même méthode,
  symétrique.
- Ratio de liquidité court terme (actif circulant / passif circulant),
  nouveau calcul, **profil standard uniquement** (voir §2 hors
  périmètre pour la raison).
- Historique de dividendes réel (nouvel appel réseau `yfinance`
  `Ticker.dividends`), pour calculer une série d'années consécutives
  avec au moins un versement.
- 7 critères Graham adaptés (détail §6), combinés en un badge
  `graham_defensive: bool` + le détail de chaque critère, par société.
- Badge affiché à côté du score composite (site + `docs/indices.json`),
  purement informatif.

### Explicitement hors périmètre

- Le profil "investisseur entreprenant" de Graham (situations
  spéciales, décotes sur l'actif net...) — trop qualitatif pour un
  criblage automatique, mentionné par l'utilisateur comme une suite
  possible mais pas cette spec.
- Toute modification du score composite ou du déclenchement des
  alertes — même décision que pour Weinstein : badge informatif, non
  bloquant, tant qu'il n'a pas été observé dans la durée.
- Le critère "taille adéquate" de Graham — considéré acquis par
  construction (l'univers suivi est composé des constituants de 10
  indices actions, déjà de grandes capitalisations), aucun calcul.
- Le ratio de liquidité pour les profils financier/trust — ces
  entreprises n'ont pas de notion d'"actif circulant" au sens
  classique (bilan bancaire structuré différemment) ; leur badge
  Graham repose sur les 6 autres critères seulement (voir §6.7).
- Relire le livre pour d'autres raffinements au-delà des 7 critères
  chiffrés déjà identifiés au spike.

## 3. Architecture

### 3.1 Vue d'ensemble

```
fetch_company_financials() (indices_score.py, déjà existant)
  |
  |-- NOUVEAU : t.dividends (nouvel appel réseau, historique complet
  |   des versements de dividendes)
  v
NOUVEAU : compute_dividend_streak_years(dividends) -> int
  |
extract_ratios()/extract_ratios_financial() (existants, étendus)
  |-- NOUVEAU côté extract_ratios (standard) : current_pb/avg_pb_5y
  |   (même calcul que extract_ratios_financial, jusqu'ici absent ici)
  |-- NOUVEAU côté extract_ratios (standard) : current_ratio (actif
  |   circulant / passif circulant, balance_sheet)
  |-- NOUVEAU (les deux profils) : "aucune perte sur les exercices
  |   disponibles" dérivé de net_income déjà extrait, pas de nouvel
  |   appel
  v
NOUVEAU : compute_graham_defensive_badge(ratios, dividend_streak_years) -> dict
  |-- combine les 6-7 critères applicables au profil de la société
  v
docs/indices.json (nouveaux champs par société, voir §4.2)
```

### 3.2 Pourquoi pas d'autre approche

- **Estimer la régularité des dividendes à partir de `dividendYield`
  seul** (déjà dans `info`, aucun appel réseau supplémentaire) :
  rejeté — décision explicite de l'utilisateur, un rendement ponctuel
  ne dit rien sur la régularité, qui est le cœur du critère Graham.
- **Calculer le ratio de liquidité aussi pour les profils
  financier/trust** en réutilisant des postes de bilan approximants :
  rejeté — un bilan bancaire n'a pas d'équivalent significatif à
  "actif circulant/passif circulant" (la quasi-totalité du bilan d'une
  banque est technique­ment "circulante"), le ratio n'aurait pas de
  sens comparable au profil standard.

## 4. Modèle de données

### 4.1 Champs internes ajoutés à `ratios`

Côté `extract_ratios` (profil standard) — symétrique à ce qui existe
déjà côté `extract_ratios_financial` :
- `current_pb`, `avg_pb_5y` : mêmes calculs que dans
  `extract_ratios_financial` (market_cap / equity_value, par exercice
  disponible), `0.0` si aucun exercice exploitable (même convention
  que `current_pe`/`avg_pe_5y` existants).
- `current_ratio` : actif circulant du dernier exercice / passif
  circulant du dernier exercice. `0.0` si l'une des deux lignes est
  absente (même convention que le reste du fichier pour une donnée
  manquante — jamais de division par zéro : passif circulant à 0 ou
  manquant → `current_ratio = 0.0`).

Communs aux deux profils (nouveau, dérivé de `net_income` déjà
extrait, sans nouvel appel) :
- `no_loss_years` : `bool`, `True` si `net_income` est positif sur
  TOUS les exercices disponibles dans `financials` (généralement 4,
  jamais les 10 ans de Graham — voir §6.3).

Nouveau, depuis le nouvel appel `t.dividends` (les deux profils) :
- `dividend_streak_years` : `int`, nombre d'années consécutives
  jusqu'à l'année en cours incluse avec au moins un versement de
  dividende. `0` si aucun dividende versé l'année en cours ou l'an
  dernier (la série est considérée rompue), ou si `t.dividends` est
  vide/indisponible.

### 4.2 `docs/indices.json`

Nouveau champ par société :

```json
{
  "graham_defensive": {
    "eligible": true,
    "criteria": {
      "structure_financiere": true,
      "stabilite_benefices": true,
      "dividendes": true,
      "croissance_benefices": true,
      "valorisation_pe": true,
      "valorisation_graham_number": true
    },
    "dividend_streak_years": 14
  }
}
```

`criteria` ne contient que les critères applicables au profil de la
société : **6 clés pour le profil standard** (`structure_financiere`
inclus) ; **5 clés pour financier/trust** (`structure_financiere`
exclu — voir §2/§6.2). Jamais 7 : "taille adéquate" n'est jamais un
champ (voir §2). `eligible` est `True` seulement si TOUTES les clés
présentes dans `criteria` sont `True` (test strict, comme Graham — voir
§6.8 pour la justification de ne pas compter un score sur N).

## 5. Récupération des dividendes

Nouvelle fonction, appelée depuis `fetch_company_financials` juste
après la récupération de `history` :

```python
def fetch_dividend_history(ticker_obj) -> "pd.Series":
    """Historique complet des dividendes versés (yfinance
    Ticker.dividends, Series indexée par date d'ex-dividende, valeur =
    montant versé). Série vide si l'entreprise n'a jamais versé de
    dividende, ou si l'appel échoue — jamais d'exception."""
    try:
        return ticker_obj.dividends
    except Exception:
        return pd.Series(dtype=float)
```

`ticker_obj` est le même objet `history_source`/`t` déjà utilisé pour
`.history(period="6y")` — pas de `yf.Ticker(...)` supplémentaire, un
seul objet, deux propriétés différentes lues dessus.

### 5.1 Calcul du streak — algorithme précis

```python
def compute_dividend_streak_years(dividends: "pd.Series", today: "date | None" = None) -> int:
    """Nombre d'années consécutives avec au moins un versement, en
    remontant depuis l'année en cours. Tolère que le dividende de
    l'année en cours ne soit pas encore tombé : démarre le décompte à
    l'année en cours SI elle a déjà un versement, sinon à l'année
    dernière — mais si NI l'année en cours NI l'année dernière n'ont de
    versement, le streak est 0 (série considérée rompue), même s'il y a
    eu des versements par le passé. `today` injectable pour les tests."""
    today = today or date.today()
    years_paid = {d.year for d in dividends.index}
    start_year = today.year if today.year in years_paid else today.year - 1
    streak = 0
    year = start_year
    while year in years_paid:
        streak += 1
        year -= 1
    return streak
```

## 6. Les 7 critères adaptés

### 6.1 Taille adéquate — non calculé (§2)

### 6.2 Structure financière solide (profil standard uniquement)

Graham : actif circulant ≥ 2x passif circulant, dette long terme ≤
fonds de roulement. Adapté au premier ratio seul (le second demande
une décomposition de la dette par échéance non disponible chez
yfinance de façon fiable) :

```python
GRAHAM_CURRENT_RATIO_MIN = 2.0

structure_financiere = current_ratio >= GRAHAM_CURRENT_RATIO_MIN
```

### 6.3 Stabilité des bénéfices (les deux profils)

Graham : aucune perte sur 10 ans. Adapté à la fenêtre réellement
disponible (~4 exercices) — assumé explicitement comme une version
réduite du critère, pas une équivalence :

```python
stabilite_benefices = no_loss_years  # déjà calculé, voir §4.1
```

### 6.4 Historique de dividendes (les deux profils)

```python
GRAHAM_DIVIDEND_STREAK_MIN_YEARS = 10

dividendes = dividend_streak_years >= GRAHAM_DIVIDEND_STREAK_MIN_YEARS
```

10 ans choisi (pas les 20 ans de Graham) — reste sélectif pour cet
univers sans être quasi-inatteignable ; valeur ajustable, isolée dans
une constante nommée.

### 6.5 Croissance des bénéfices (les deux profils)

Graham : +33% sur 10 ans. Réutilise `cagr_net_income` déjà calculé
(sur la fenêtre réelle ~2 ans après lissage, voir le commentaire
existant de `extract_ratios`/`extract_ratios_financial`) — le seuil
est adapté au taux annualisé équivalent plutôt qu'au cumul sur 10 ans
littéral :

```python
GRAHAM_EARNINGS_GROWTH_CAGR_MIN_PCT = 2.9  # ≈ (1.33)^(1/10) - 1, annualisé

croissance_benefices = cagr_net_income >= GRAHAM_EARNINGS_GROWTH_CAGR_MIN_PCT
```

### 6.6 P/E modéré (les deux profils)

```python
GRAHAM_PE_MAX = 15.0

valorisation_pe = 0 < current_pe <= GRAHAM_PE_MAX
```

(`current_pe > 0` exclu explicitement : un P/E négatif ou nul dans ce
pipeline signifie une donnée manquante/non exploitable, pas une
valorisation exceptionnellement basse.)

### 6.7 P/B modéré / "Graham Number" (les deux profils)

```python
GRAHAM_NUMBER_MAX = 22.5

graham_number = current_pe * current_pb if current_pb > 0 else None
valorisation_graham_number = graham_number is not None and 0 < graham_number <= GRAHAM_NUMBER_MAX
```

### 6.8 Combinaison en badge

```python
def compute_graham_defensive_badge(ratios: dict, is_financial: bool, is_trust: bool) -> dict:
    criteria = {}
    if not (is_financial or is_trust):
        criteria["structure_financiere"] = (
            ratios.get("current_ratio", 0.0) >= GRAHAM_CURRENT_RATIO_MIN
        )
    criteria["stabilite_benefices"] = ratios.get("no_loss_years", False)
    criteria["dividendes"] = (
        ratios.get("dividend_streak_years", 0) >= GRAHAM_DIVIDEND_STREAK_MIN_YEARS
    )
    criteria["croissance_benefices"] = (
        ratios.get("cagr_net_income", 0.0) >= GRAHAM_EARNINGS_GROWTH_CAGR_MIN_PCT
    )
    current_pe = ratios.get("current_pe", 0.0)
    criteria["valorisation_pe"] = 0 < current_pe <= GRAHAM_PE_MAX
    current_pb = ratios.get("current_pb", 0.0)
    graham_number = current_pe * current_pb if current_pb > 0 else None
    criteria["valorisation_graham_number"] = (
        graham_number is not None and 0 < graham_number <= GRAHAM_NUMBER_MAX
    )
    return {"eligible": all(criteria.values()), "criteria": criteria}
```

Test strict (`all(criteria.values())`, pas un compte sur N) — décision
explicite de l'utilisateur pour rester cohérent avec Weinstein et
éviter de reproduire le problème de calibrage déjà identifié sur le
score composite principal (seuils trop permissifs → badge qui ne
filtre presque rien).

## 7. Tests

- `fetch_dividend_history` : historique réel, série vide (jamais versé
  de dividende), exception réseau → `[]` sans lever.
- `compute_dividend_streak_years` : streak en cours (versement cette
  année), streak toléré (versement l'an dernier, pas encore cette
  année), streak rompu (aucun versement l'an dernier ni cette année,
  même avec un historique ancien), historique vide, versements
  irréguliers dans le passé mais streak actuel valide (un trou ancien
  ne doit pas empêcher de compter le streak récent).
- `current_pb`/`avg_pb_5y` côté profil standard : cas nominal,
  exercice sans `equity` exploitable exclu (même garde que le P/E déjà
  en place).
- `current_ratio` : cas nominal, passif circulant absent/nul → `0.0`.
- `no_loss_years` : tous positifs → `True`, une seule perte sur la
  fenêtre → `False`.
- `compute_graham_defensive_badge` : éligible (tous critères vrais),
  non éligible (un seul critère faux suffit), critère
  `structure_financiere` absent du dict pour un profil
  financier/trust, valeurs manquantes (`ratios` incomplet) dégradent
  vers `False` sans exception.
