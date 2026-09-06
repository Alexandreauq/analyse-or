# Coût du capital réel par entreprise (WACC) — design

Branche : `wacc-entreprise`
Suite de la refonte Indices (remplace le proxy fixe `COST_OF_CAPITAL_PROXY
= 8.0` par un WACC calculé par entreprise, utilisé à la fois par le
facteur Rentabilité et par le DCF des repères d'entrée/sortie).

## Contexte

`COST_OF_CAPITAL_PROXY = 8.0` (une valeur unique pour les 5 entreprises)
est jugé trop générique. L'utilisateur demande un WACC calculé par
entreprise, avec deux précisions apportées en cours de discussion :
- le taux sans risque doit être celui du pays de l'entreprise valorisée
  (France, pas les États-Unis) ;
- la capitalisation boursière doit entrer en compte via une prime de
  taille sur le coût des fonds propres.

## Objectif

Calculer un WACC par entreprise (CAPM + prime de taille, Vernimmen) et
l'utiliser partout où `COST_OF_CAPITAL_PROXY` est utilisé aujourd'hui —
facteur Rentabilité et taux d'actualisation du DCF. Si une donnée
nécessaire manque, repli sur `COST_OF_CAPITAL_PROXY` pour cette
entreprise (jamais d'exception, jamais de valeur fantaisiste).

## Formule

```
Re (coût des fonds propres) = Rf_France + β × prime_marché + prime_taille(capitalisation)
Rd (coût de la dette, après IS) = DEBT_INTEREST_RATE_PROXY × (1 − taux_IS)
WACC = capitalisation/(capitalisation + dette totale) × Re
     + dette totale/(capitalisation + dette totale) × Rd
```

## Décisions actées

- **Rf** : taux souverain français 10 ans (OAT), FRED série
  `IRLTLT01FRM156N` ("Long-Term Government Bond Yields: 10-Year: Main
  (Including Benchmark) for France", mensuelle, ~3,68% en juin 2026 au
  moment de la conception). Pas le taux US (`DFII10`/`DGS10` déjà utilisés
  côté Or) — une entreprise du CAC 40, valorisée en euros, doit être
  actualisée avec un taux sans risque en euros.
- **β (bêta)** : champ `beta` déjà fourni par yfinance (`info.get("beta")`)
  — le bêta brut (endetté) de l'action, pas un bêta désendetté puis
  réendetté à la structure financière de l'entreprise (ce que recommande
  Vernimmen pour plus de rigueur, mais qui nécessiterait un échantillon de
  comparables non disponible ici). Simplification à documenter.
- **Prime de risque marché** : constante fixe **5,0%** (hypothèse
  académique/praticienne standard, non calculée dynamiquement).
- **Prime de taille** : bandes sur la capitalisation boursière
  (capitalisation = cours actuel × actions en circulation, déjà
  disponibles) :

  | Capitalisation | Prime |
  |---|---|
  | > 50 Md€ | +0,0 pt |
  | 10 à 50 Md€ | +0,5 pt |
  | 2 à 10 Md€ | +1,5 pt |
  | ≤ 2 Md€ | +3,0 pt |

  Reprend l'esprit des tables de prime de taille standard (type
  Ibbotson/Duff & Phelps, non reproductibles ici faute de source
  gratuite) sans prétendre à leur précision.
- **Rd (coût de la dette)** : réutilise le taux proxy déjà assumé pour
  l'ICR (`_score_leverage`/`icr`, actuellement un littéral `0.03` inline)
  plutôt que de tenter d'extraire les charges financières réelles de
  yfinance — ce poste n'est déjà pas fiablement isolé chez ces
  entreprises (commentaire existant : "proxy frais financiers si non
  isolés"). Extrait en constante nommée partagée `DEBT_INTEREST_RATE_PROXY
  = 3.0` pour éviter deux littéraux `0.03`/`3.0` à synchroniser
  manuellement (petit refactor DRY au passage, comportement inchangé pour
  l'ICR).
- **Repli** : si `risk_free_rate`, `beta`, capitalisation, dette totale ou
  taux d'IS manque/est invalide pour une entreprise, `estimate_wacc(...)`
  renvoie `None` et l'appelant retombe sur `COST_OF_CAPITAL_PROXY` pour
  cette seule entreprise — jamais de WACC partiellement fabriqué.
- **Transparence** : le WACC réellement utilisé (calculé ou replié) est
  exposé comme nouveau champ top-level `wacc` dans `docs/indices.json`,
  au même endroit que `current_price`/`fair_value`/`entry_price`/`exit_price`.

## Pipeline de données

### Nouvelles données brutes à exposer

`extract_ratios` calcule déjà `tax_rate[latest]` et `total_debt[latest]`
en interne mais ne les renvoie pas — ajouter au dict renvoyé :
`"tax_rate": tax_rate[latest]`, `"total_debt": total_debt[latest]`.

`fetch_company_financials` doit exposer le bêta yfinance :
`ratios["beta"] = info.get("beta")`.

### Récupération du taux sans risque (une seule fois par run, pas par entreprise)

```python
FRED_RISK_FREE_SERIES = "IRLTLT01FRM156N"  # OAT 10 ans (France), FRED/OCDE, mensuel


def fetch_risk_free_rate() -> float | None:
    """Dernier taux OAT 10 ans publié (FRED, mensuel, ~1-2 mois de
    décalage) — taux sans risque pour le CAPM. None si la clé API FRED
    est absente ou en cas d'échec réseau/API : toutes les entreprises
    retombent alors sur COST_OF_CAPITAL_PROXY pour ce run."""
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return None
    try:
        start = (datetime.today() - timedelta(days=120)).strftime("%Y-%m-%d")
        resp = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": FRED_RISK_FREE_SERIES,
                "api_key": api_key,
                "file_type": "json",
                "observation_start": start,
            },
            timeout=15,
        )
        resp.raise_for_status()
        obs = [o for o in resp.json()["observations"] if o["value"] != "."]
        if not obs:
            return None
        return float(obs[-1]["value"])
    except Exception:
        return None
```

Appelée une seule fois dans `main()`, avant la boucle par entreprise
(contrairement aux autres données, le taux sans risque est commun aux 5
entreprises pour un run donné) :

```python
def main():
    risk_free_rate = fetch_risk_free_rate()
    companies = []
    for company in COMPANIES:
        try:
            companies.append(
                build_company_entry(company["ticker"], company["name"], risk_free_rate)
            )
        except Exception as e:
            print(f"Erreur pour {company['ticker']} ({company['name']}) : {e}")
    ...
```

### Calcul du WACC

```python
MARKET_RISK_PREMIUM = 5.0        # % prime de risque marché (hypothèse fixe)
DEBT_INTEREST_RATE_PROXY = 3.0   # % taux d'intérêt proxy sur la dette totale
                                  # (déjà utilisé pour l'ICR — extrait en
                                  # constante nommée partagée)

SIZE_PREMIUM_BANDS = [
    (50_000_000_000, 0.0),
    (10_000_000_000, 0.5),
    (2_000_000_000, 1.5),
    (0, 3.0),
]


def _size_premium(market_cap: float) -> float:
    """Prime de taille (points ajoutés au coût des fonds propres) selon
    la capitalisation boursière — parcourt les bandes de la plus grande à
    la plus petite, renvoie la première dont le seuil est dépassé."""
    for threshold, premium in SIZE_PREMIUM_BANDS:
        if market_cap > threshold:
            return premium
    return SIZE_PREMIUM_BANDS[-1][1]


def estimate_wacc(
    risk_free_rate: float | None,
    beta: float | None,
    market_cap: float | None,
    total_debt: float | None,
    tax_rate: float | None,
) -> float | None:
    """WACC par entreprise (CAPM + prime de taille, Vernimmen). None si une
    donnée nécessaire manque/est invalide — le repli sur
    COST_OF_CAPITAL_PROXY se fait chez l'appelant, pas ici."""
    if (
        risk_free_rate is None or _is_missing(risk_free_rate)
        or beta is None or _is_missing(beta)
        or market_cap is None or _is_missing(market_cap) or market_cap <= 0
        or total_debt is None or _is_missing(total_debt) or total_debt < 0
        or tax_rate is None or _is_missing(tax_rate)
    ):
        return None
    cost_of_equity = risk_free_rate + beta * MARKET_RISK_PREMIUM + _size_premium(market_cap)
    cost_of_debt_after_tax = DEBT_INTEREST_RATE_PROXY * (1 - tax_rate)
    total_capital = market_cap + total_debt
    equity_weight = market_cap / total_capital
    debt_weight = total_debt / total_capital
    return equity_weight * cost_of_equity + debt_weight * cost_of_debt_after_tax
```

(`total_capital` ne peut jamais être nul ici : `market_cap > 0` est déjà
garanti par le garde-fou juste au-dessus, et `total_debt >= 0`.)

### Refactor DRY : `icr` réutilise la même constante

Dans `extract_ratios`, remplacer le littéral inline :

```python
icr = ebit[latest] / (total_debt[latest] * 0.03) if total_debt[latest] else 10.0
```

par :

```python
icr = ebit[latest] / (total_debt[latest] * DEBT_INTEREST_RATE_PROXY / 100) if total_debt[latest] else 10.0
```

Comportement strictement inchangé (`0.03 == 3.0 / 100`), juste la même
hypothèse nommée à un seul endroit désormais.

### Branchement dans `estimate_dcf_price` / `estimate_valuation_targets` / `build_company_entry`

`estimate_dcf_price` prend désormais le taux d'actualisation en
paramètre plutôt que de lire `COST_OF_CAPITAL_PROXY` directement — seule
sa signature et sa 1ère ligne de calcul changent, le reste de la fonction
(déjà écrit et testé) ne bouge pas :

```python
def estimate_dcf_price(
    fcf: float, cagr_ebitda: float, net_debt: float, shares_outstanding: float,
    discount_rate_pct: float,
) -> float | None:
    ...
    discount_rate = discount_rate_pct / 100
    ...
```

`estimate_valuation_targets` prend le coût du capital en paramètre
supplémentaire et le transmet :

```python
def estimate_valuation_targets(data: dict, cost_of_capital: float) -> dict:
    dcf_price = estimate_dcf_price(
        data["fcf"], data["cagr_ebitda"], data["net_debt"], data["shares_outstanding"],
        cost_of_capital,
    )
    ...
```

`build_company_entry` calcule le WACC (avec repli) une fois, et l'utilise
pour les deux consommateurs :

```python
def build_company_entry(ticker: str, name: str, risk_free_rate: float | None) -> dict:
    data = fetch_company_financials(ticker)
    sector = data["sector"]

    market_cap = (
        data["current_price"] * data["shares_outstanding"]
        if data["current_price"] is not None else None
    )
    wacc = estimate_wacc(
        risk_free_rate, data["beta"], market_cap, data["total_debt"], data["tax_rate"]
    )
    cost_of_capital = wacc if wacc is not None else COST_OF_CAPITAL_PROXY

    try:
        news = fetch_news(name)
    except Exception as e:
        print(f"Erreur récupération news pour {name} : {e}")
        news = []

    factors = [
        score_rentabilite(data["roce"], data["roe"], cost_of_capital),
        ...  # 6 autres facteurs inchangés
    ]
    composite = compute_composite(factors)

    valuation_targets = estimate_valuation_targets(data, cost_of_capital)

    return {
        ...  # champs existants inchangés
        "wacc": cost_of_capital,
    }
```

## Schéma JSON

Chaque objet de `companies[]` gagne un champ top-level `wacc` (float,
toujours présent — vaut `COST_OF_CAPITAL_PROXY` en cas de repli, jamais
`null`).

## Secrets / configuration

`FRED_API_KEY` (déjà un secret existant, utilisé par `daily.yml` pour le
volet Or) doit être ajouté à `.github/workflows/indices.yml`, variable
d'environnement du step qui exécute `indices_score.py` — même clé, pas de
nouveau secret à créer.

## Méthodologie (`Methodologie_Analyse_Indices.md`)

- §1 (Rentabilité) : remplacer la mention "proxy simplifié : taux sans
  risque + prime de risque actions, sans calcul de bêta complet au v1"
  par la description du WACC réel (CAPM + prime de taille), avec la
  formule et les constantes ci-dessus.
- Nouvelle note "Simplification" : le bêta utilisé est le bêta brut
  (endetté) fourni par yfinance, pas un bêta désendetté puis réendetté à
  la structure financière de chaque entreprise (ce que recommande
  Vernimmen pour plus de rigueur) — nécessiterait un échantillon de
  comparables non disponible ici.
- "Hors périmètre (v1)" : retirer la ligne "Calcul complet du coût du
  capital (bêta désendetté, prime de risque de marché) — proxy simplifié
  au v1", qui n'est plus exacte — un WACC par entreprise est désormais
  calculé, avec la simplification ci-dessus sur le bêta.

## Tests

Mêmes conventions que les sous-projets précédents :

- `_size_premium` : les 4 bandes, plus les bornes exactes (ex :
  capitalisation strictement égale à 50 Md€ ne doit pas obtenir la
  meilleure tranche, cf. `>` strict).
- `estimate_wacc` : cas nominal ; chacune des 5 données manquante/NaN
  individuellement → `None` ; capitalisation nulle/négative → `None` ;
  dette négative → `None`.
- `fetch_risk_free_rate` : mockée (pas d'appel réseau réel dans les
  tests) — clé API absente → `None` sans appel réseau ; succès ; échec
  HTTP/réseau → `None` ; aucune observation exploitable → `None`.
- `icr` (test existant) : aucune régression après le refactor DRY —
  réutiliser le test déjà en place, la valeur attendue ne change pas.
- `estimate_dcf_price` (tests existants) : mis à jour pour passer
  `discount_rate_pct` explicitement au lieu de compter sur
  `COST_OF_CAPITAL_PROXY` implicite.
- `build_company_entry` (tests existants) : signature mise à jour
  (`risk_free_rate` en 3ème argument), `_fake_ratios()` complété
  (`tax_rate`, `total_debt`, `beta`), nouvelle assertion sur la présence
  et la valeur de `entry["wacc"]`.

## Risques connus

- Le bêta yfinance peut être absent pour une entreprise ponctuellement —
  dégrade vers `COST_OF_CAPITAL_PROXY` pour cette entreprise seule,
  jamais d'échec de tout le run.
- `IRLTLT01FRM156N` est mensuelle avec 1-2 mois de décalage — le taux
  sans risque utilisé peut être légèrement daté par rapport au marché du
  jour ; acceptable pour un WACC recalculé quotidiennement mais pas
  temps réel.
- La prime de taille est une simplification par bandes, pas une table
  académique reproduite fidèlement — à revoir si la grille s'étend
  au-delà des 5 pilotes (des petites capitalisations changeraient
  nettement le résultat).
