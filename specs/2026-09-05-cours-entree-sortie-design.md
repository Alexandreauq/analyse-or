# Cours + repères d'entrée/sortie — design

Branche : à créer (ex : `entree-sortie`)
Sous-projet 3/3 de la refonte Indices (1: actus enrichies — livré ; 2:
méthode de notation v2 — livré).

## Contexte

L'onglet Indices affiche aujourd'hui le score composite et son détail par
facteur, mais jamais le cours de bourse lui-même ni un repère de prix
concret. L'utilisateur veut voir, pour chaque entreprise : le cours actuel,
et un repère de prix d'entrée/sortie jugé favorable.

## Objectif

Afficher pour chaque entreprise : cours actuel, repère d'entrée (prix
jugé attractif à l'achat) et repère de sortie (prix jugé attractif à la
vente). Purement informatif — **pas** un 8ème facteur de score, le
scoring reste celui des 7 facteurs déjà en place.

## Hors périmètre

- Toute recommandation d'achat/vente explicite ou horodatée ("achetez
  maintenant") — uniquement des repères de valorisation, formulés avec
  prudence (cf. "Décisions actées" ci-dessous).
- Réévaluation des actifs à la valeur de marché pour l'approche
  patrimoniale (actif net comptable simple, pas d'actif net réévalué).
- Bêta désendetté / coût du capital par entreprise — réutilise le proxy
  `COST_OF_CAPITAL_PROXY` déjà existant (8%), pas de calcul dédié au v1.

## Décisions actées

- Le repère d'entrée/sortie combine 2 approches, chacune moyennée
  indépendamment (repli sur celle disponible si l'autre manque, jamais
  d'erreur) :
  1. **Valorisation** : une "juste valeur" par action, elle-même moyenne
     de 3 méthodes (DCF simplifié, actif net, multiples — voir
     ci-dessous), ± 30% (marge de sécurité/prime, réutilise le seuil déjà
     établi comme "prime/décote significative" dans `VALUATION_PREMIUM_SCALE`
     du facteur Valorisation existant).
  2. **Technique** : la moyenne mobile 200 jours comme repère d'entrée
     (support), MM200 × 1,20 comme repère de sortie (réutilise le +20%
     déjà établi comme échelle complète dans `PRICE_MOMENTUM_SCALE` du
     facteur Dynamique récente).
- Toujours formulé comme repère prudent ("repère de valorisation
  historique"), jamais comme conseil d'investissement — même prudence
  déjà appliquée au facteur Valorisation et aux résumés IA des actus.

## Pipeline de données

Tout dans `indices_score.py`.

### Nouvelles données brutes à exposer

`extract_ratios` calcule déjà `fcf`, `net_debt_latest` et `equity[latest]`
en interne mais ne les renvoie pas — ajouter au dict renvoyé :

```python
    return {
        "roce": roce,
        "roe": roe,
        "net_debt_ebitda": net_debt_ebitda,
        "icr": icr,
        "cagr_ca": cagr_ca,
        "cagr_ebitda": cagr_ebitda,
        "fcf_conversion": fcf_conversion,
        "current_ev_ebitda": current_ev_ebitda,
        "avg_ev_ebitda_5y": avg_ev_ebitda_5y,
        "current_pe": current_pe,
        "avg_pe_5y": avg_pe_5y,
        "fcf": fcf,
        "net_debt": net_debt_latest,
        "equity": equity[latest],
    }
```

`fetch_company_financials` calcule déjà `current_price` et `ma200` en
interne (utilisés aujourd'hui uniquement pour dériver `ecart_pct_ma200`)
mais ne les renvoie pas non plus, et `shares_outstanding` n'est pas
renvoyé — ajouter :

```python
    ratios = extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding)
    ratios["sector"] = info.get("sector")
    ratios["ecart_pct_ma200"] = ecart_pct_ma200
    ratios["quarterly_yoy_growth_ca"] = extract_quarterly_growth(quarterly_financials)
    ratios["current_price"] = current_price
    ratios["ma200"] = ma200
    ratios["shares_outstanding"] = shares_outstanding
    return ratios
```

### Les 3 méthodes de valorisation

```python
DCF_PROJECTION_YEARS = 5
DCF_GROWTH_FLOOR = -5.0      # % croissance FCF minimum projetée
DCF_GROWTH_CAP = 15.0        # % croissance FCF maximum projetée
DCF_TERMINAL_GROWTH = 2.0    # % croissance perpétuelle (valeur terminale)


def estimate_dcf_price(
    fcf: float, cagr_ebitda: float, net_debt: float, shares_outstanding: float
) -> float | None:
    """Prix par action implicite d'un DCF simplifié : projette le FCF actuel
    sur 5 ans au taux de croissance historique de l'EBITDA (plafonné entre
    -5% et +15%/an pour éviter d'extrapoler un chiffre bruité de façon
    absurde), actualise au coût du capital (COST_OF_CAPITAL_PROXY), ajoute
    une valeur terminale à croissance perpétuelle de 2%. None si le FCF de
    départ n'est pas positif (DCF non pertinent) ou si le nombre d'actions
    est nul/inconnu."""
    if fcf <= 0 or not shares_outstanding:
        return None
    growth = _clamp(cagr_ebitda, DCF_GROWTH_FLOOR, DCF_GROWTH_CAP) / 100
    discount_rate = COST_OF_CAPITAL_PROXY / 100
    terminal_growth = DCF_TERMINAL_GROWTH / 100

    pv_fcf = 0.0
    fcf_t = fcf
    for year in range(1, DCF_PROJECTION_YEARS + 1):
        fcf_t = fcf_t * (1 + growth)
        pv_fcf += fcf_t / (1 + discount_rate) ** year

    terminal_value = fcf_t * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / (1 + discount_rate) ** DCF_PROJECTION_YEARS

    enterprise_value = pv_fcf + pv_terminal
    equity_value = enterprise_value - net_debt
    return equity_value / shares_outstanding


def estimate_asset_based_price(equity: float, shares_outstanding: float) -> float | None:
    """Valeur comptable par action (capitaux propres / actions en
    circulation) — approche patrimoniale simplifiée, sans réévaluation des
    actifs à la valeur de marché (hors périmètre v1). None si les capitaux
    propres sont négatifs ou nuls (base non significative comme plancher
    de valorisation) ou si le nombre d'actions est nul/inconnu."""
    if not shares_outstanding or equity <= 0:
        return None
    return equity / shares_outstanding


def estimate_multiple_based_price(
    current_price: float, current_ev_ebitda: float, avg_ev_ebitda_5y: float
) -> float | None:
    """Prix impliqué par un retour du multiple EV/EBITDA actuel à sa
    moyenne 5 ans, en supposant que le prix varie proportionnellement au
    multiple — approximation qui ignore l'effet de la dette nette fixe,
    documentée comme telle (cf. Methodologie_Analyse_Indices.md), plutôt
    que de reconstruire précisément EV et capitalisation. None si le
    multiple actuel est nul/absent."""
    if not current_ev_ebitda:
        return None
    return current_price * (avg_ev_ebitda_5y / current_ev_ebitda)


def estimate_fair_value(
    dcf_price: float | None, asset_price: float | None, multiple_price: float | None
) -> float | None:
    """Moyenne des méthodes de valorisation disponibles (DCF, actif net,
    multiples) — ignore celles indisponibles (None) ; None si aucune des
    3 n'est calculable."""
    prices = [p for p in (dcf_price, asset_price, multiple_price) if p is not None]
    return sum(prices) / len(prices) if prices else None
```

### Combinaison en repères d'entrée/sortie

```python
VALUATION_MARGIN_OF_SAFETY = 0.30   # ±30%, cohérent avec VALUATION_PREMIUM_SCALE
TECHNICAL_EXIT_MARGIN = 0.20        # +20% au-dessus de la MM200, cohérent avec PRICE_MOMENTUM_SCALE


def estimate_entry_exit_prices(fair_value: float | None, ma200: float | None) -> dict:
    """Combine repère de valorisation (juste valeur ± 30%) et repère
    technique (MM200 / MM200 × 1,20) en moyennant ceux disponibles.
    Renvoie {"entry": float | None, "exit": float | None} — None des deux
    côtés si ni la valorisation ni la MM200 ne sont disponibles."""
    entry_candidates = []
    exit_candidates = []
    if fair_value is not None:
        entry_candidates.append(fair_value * (1 - VALUATION_MARGIN_OF_SAFETY))
        exit_candidates.append(fair_value * (1 + VALUATION_MARGIN_OF_SAFETY))
    if ma200 is not None:
        entry_candidates.append(ma200)
        exit_candidates.append(ma200 * (1 + TECHNICAL_EXIT_MARGIN))
    entry = sum(entry_candidates) / len(entry_candidates) if entry_candidates else None
    exit_price = sum(exit_candidates) / len(exit_candidates) if exit_candidates else None
    return {"entry": entry, "exit": exit_price}
```

### Orchestration et branchement dans `build_company_entry`

```python
def estimate_valuation_targets(data: dict) -> dict:
    """Combine DCF, actif net et multiples en une juste valeur, puis en
    repères d'entrée/sortie. Toujours ces 3 clés en sortie, valeurs à None
    si non calculables (jamais d'exception)."""
    dcf_price = estimate_dcf_price(
        data["fcf"], data["cagr_ebitda"], data["net_debt"], data["shares_outstanding"]
    )
    asset_price = estimate_asset_based_price(data["equity"], data["shares_outstanding"])
    multiple_price = (
        estimate_multiple_based_price(
            data["current_price"], data["current_ev_ebitda"], data["avg_ev_ebitda_5y"]
        )
        if data["current_price"] is not None else None
    )
    fair_value = estimate_fair_value(dcf_price, asset_price, multiple_price)
    entry_exit = estimate_entry_exit_prices(fair_value, data["ma200"])
    return {
        "fair_value": fair_value,
        "entry_price": entry_exit["entry"],
        "exit_price": entry_exit["exit"],
    }
```

Dans `build_company_entry`, appeler `estimate_valuation_targets(data)` et
ajouter au dict renvoyé (au même niveau que `score`/`factors`/`news`, pas
imbriqué) :

```python
"current_price": data["current_price"],
"fair_value": valuation_targets["fair_value"],
"entry_price": valuation_targets["entry_price"],
"exit_price": valuation_targets["exit_price"],
```

Aucune de ces fonctions ne lève jamais — chaque sous-méthode renvoie
`None` sur donnée insuffisante, et les fonctions de combinaison ignorent
les `None` plutôt que de planter, cohérent avec le reste du fichier.

## Schéma JSON

Chaque objet de `companies[]` gagne 4 champs top-level (siblings de
`score`/`factors`/`news`) : `current_price`, `fair_value`, `entry_price`,
`exit_price` — tous `float | null`, jamais absents.

## Front-end (`docs/index.html`)

Nouvelle section dans `renderCompanyDetail`, affichée en permanence
(pas derrière un toggle, contrairement à "Actu"/"Détail du calcul"),
insérée entre le `<section class="hero">` et le `<div class="divider">` :

```html
    <div class="price-panel">
      <div class="price-row">
        <span class="price-label">Cours actuel</span>
        <span class="price-value">${formatPrice(company.current_price)}</span>
      </div>
      <div class="price-row">
        <span class="price-label">Repère d'entrée</span>
        <span class="price-value" style="color:var(--gold)">${formatPrice(company.entry_price)}</span>
      </div>
      <div class="price-row">
        <span class="price-label">Repère de sortie</span>
        <span class="price-value" style="color:var(--rust)">${formatPrice(company.exit_price)}</span>
      </div>
      <p class="price-disclaimer">Repères indicatifs basés sur la valorisation
        historique (DCF, actif net, multiples) et la tendance technique —
        pas un conseil d'investissement.</p>
    </div>
```

Nouvelle fonction JS `formatPrice(v)` (partagée, définie une fois au
niveau du script) :

```javascript
function formatPrice(v) {
  return v != null ? `${v.toLocaleString('fr-FR', {maximumFractionDigits:2})} €` : 'indisponible';
}
```

Nouvelles règles CSS (style cohérent avec `.tech-row`/`.tech-label`/`.tech-value`
déjà utilisées pour la section technique de l'écran Or) :

```css
.price-panel { margin: 16px 0; }
.price-row { display:flex; justify-content:space-between; font-size:14px; padding: 4px 0; }
.price-label { color: var(--muted); }
.price-value { font-weight: 500; }
.price-disclaimer { color: var(--muted); font-size: 11px; line-height: 1.5; margin: 8px 0 0; }
```

## Tests

Dans `tests/test_indices_score.py`, mêmes conventions que les
sous-projets précédents :

- `estimate_dcf_price` : cas nominal (FCF positif, croissance dans la
  plage) ; FCF négatif ou nul → `None` ; croissance extrême plafonnée
  (vérifier que le prix ne varie plus au-delà du plafond/plancher) ;
  `shares_outstanding` nul → `None`.
- `estimate_asset_based_price` : cas nominal ; capitaux propres négatifs
  → `None` ; `shares_outstanding` nul → `None`.
- `estimate_multiple_based_price` : cas nominal ; `current_ev_ebitda` nul
  → `None`.
- `estimate_fair_value` : les 3 méthodes disponibles (moyenne) ; 2 sur 3
  disponibles (moyenne sur les 2) ; aucune disponible → `None`.
- `estimate_entry_exit_prices` : valorisation + technique disponibles
  (moyenne des 2 candidats de chaque côté) ; un seul type disponible ;
  aucun disponible → `{"entry": None, "exit": None}`.
- `estimate_valuation_targets` : test d'intégration légère avec un dict
  `data` complet, vérifiant les 3 clés de sortie.
- `build_company_entry` : test mis à jour vérifiant la présence de
  `current_price`/`fair_value`/`entry_price`/`exit_price` dans l'entrée
  renvoyée.

## Risques connus

- Le DCF simplifié repose sur une seule année de FCF projetée à un taux
  constant (pas de convergence progressive vers la croissance terminale)
  — une simplification déjà cohérente avec le niveau de rigueur accepté
  ailleurs dans ce pilote (proxy de coût du capital, FCF lui-même
  approximé). Sensible aux données d'une seule année si celle-ci est
  atypique.
- L'approche multiples suppose une relation proportionnelle simplifiée
  entre prix et multiple EV/EBITDA (ignore l'effet de la dette nette
  fixe) — déjà documenté comme telle dans le code.
- Les repères peuvent être très éloignés du cours actuel pour une
  entreprise dont la valorisation de marché diverge fortement de ces 3
  approches (ex : forte prime de croissance non captée par un DCF à
  hypothèses prudentes) — c'est un repère de comparaison, pas une cible
  garantie.
