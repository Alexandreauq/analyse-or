# Réconciliation devise de référence / devise de cotation — Design

## Contexte et problème

Audit complet du module Actions/Indices (`indices_score.py`), 2026-09-24,
constat C1 : la devise dans laquelle une société publie ses comptes annuels
(`financialCurrency` côté yfinance — résultat net, capitaux propres, dette,
FCF) n'est jamais réconciliée avec la devise dans laquelle elle est cotée
(`currency` — le prix, dont dérive `market_cap = price * shares_outstanding`).
Pour la plupart des sociétés les deux coïncident, mais pas pour les doubles
cotations / sociétés dont le siège de reporting diffère de la place de
cotation principale.

Vérifié en direct (API Yahoo Finance, via le VPS — accès réseau réel,
indisponible dans ce bac à sable) : AIA (1299.HK) publie ses comptes en USD
mais cote en HKD. Avant correctif : P/E affiché à 129.2x, P/B à 18.6x. Après
conversion manuelle : ~16.5x et ~2.4x — l'écart correspond exactement au taux
HKD/USD (~7.8). Autres sociétés signalées par l'audit comme probablement
touchées (doubles cotations reportant en devise étrangère à leur place
principale) : plusieurs FTSE en USD (Shell, BP, Rio Tinto, AstraZeneca,
Glencore, Anglo American, StanChart, Prudential, Antofagasta, Experian),
plusieurs SMI en USD/EUR (Novartis, UBS, Zurich, Swiss Re, ABB, Alcon,
Logitech, Richemont), STMicro, Tenaris, ArcelorMittal, et des Hang Seng en
CNY/USD.

Ceci fausse : le DCF, la valeur d'actif net, la valorisation par multiple, les
repères d'entrée/sortie, la pondération dette/capitaux propres du WACC, les
critères P/E et nombre de Graham du badge, et le score P/B-vs-NAV du profil
trust.

## Ce qui n'est PAS affecté (borne le correctif)

Les ratios purement internes aux comptes — ROE, ROCE, levier (dette
nette/EBITDA), ICR, croissance CAGR, conversion FCF/EBITDA — sont des rapports
entre deux lignes du même bilan/compte de résultat. Ils restent corrects même
sans conversion de devise, puisque le numérateur et le dénominateur sont dans
la même devise quelle qu'elle soit. Seuls les ratios qui combinent le prix de
marché (`market_cap`, en devise de cotation) avec les comptes (en devise de
référence) sont concernés : P/E, P/B, EV/EBITDA, DCF, valorisation par
multiple, WACC.

## Mécanisme

### Nouvelle fonction : conversion entre deux devises quelconques

`fetch_fx_rate_to_usd` (existant, utilisé pour la prime de taille du WACC) ne
convertit que vers USD. Une nouvelle fonction compose deux appels à cette
fonction existante, en utilisant USD comme pivot (toutes les devises gérées
sont cotées contre USD — pratique de marché standard, pas un contournement) :

```python
def fetch_fx_rate(from_currency: str, to_currency: str) -> float | None:
    """Taux de change pour convertir un montant de `from_currency` vers
    `to_currency`, composé à partir de fetch_fx_rate_to_usd (pivot USD).
    None si l'une des deux devises n'est pas gérée ou si l'appel réseau
    échoue — jamais d'exception."""
    if from_currency == to_currency:
        return 1.0
    rate_from_usd = fetch_fx_rate_to_usd(from_currency)
    rate_to_usd = fetch_fx_rate_to_usd(to_currency)
    if rate_from_usd is None or rate_to_usd is None:
        return None
    return rate_from_usd / rate_to_usd
```

`FX_TICKER_TO_USD` gagne une entrée `"CNY": ("CNY=X", "divide")` (même
convention que JPY/HKD — cotation indirecte), pour couvrir les Hang Seng
reportant en CNY signalées par l'audit.

### Câblage dans `fetch_company_financials`

Juste après le bloc de conversion pence/livre existant (`indices_score.py`,
recherche `if info.get("currency") == "GBp":` — qui normalise déjà
l'*échelle* du prix), avant la purge des NaN de fin de série :

```python
    quote_currency = "GBP" if info.get("currency") == "GBp" else info.get("currency")
    financial_currency = info.get("financialCurrency")
    currency_mismatch_unresolved = False
    if financial_currency and quote_currency and financial_currency != quote_currency:
        # Devise de référence des comptes ≠ devise de cotation (constat
        # C1) : convertit les 3 DataFrames de comptes vers la devise de
        # cotation, à la source, même principe que la conversion
        # pence/livre juste au-dessus — tout calcul en aval
        # (market_cap = price * shares_outstanding combiné à ces
        # comptes) reste cohérent sans replâtrage consommateur par
        # consommateur.
        fx_rate = fetch_fx_rate(financial_currency, quote_currency)
        if fx_rate is not None:
            financials = financials * fx_rate
            balance_sheet = balance_sheet * fx_rate
            cashflow = cashflow * fx_rate
        else:
            currency_mismatch_unresolved = True

    if currency_mismatch_unresolved:
        # Devises non réconciliables (taux introuvable) : dégrade vers
        # shares_outstanding=0.0, qui fait déjà tomber proprement les
        # ratios prix/comptes (P/E, P/B, EV/EBITDA, valorisation — voir
        # extract_ratios, constat C5, déjà corrigé) sans invalider les
        # ratios purement comptables (ROE, ROCE, levier, croissance),
        # qui restent corrects même non convertis (voir "Ce qui n'est
        # PAS affecté" ci-dessus).
        shares_outstanding = 0.0
```

**Cas "devise absente"** : si `financialCurrency` est `None` chez yfinance,
aucune conversion n'est tentée (comportement actuel inchangé, suppose la même
devise que la cotation). Choix délibéré : la majorité des sociétés n'ont pas
ce problème, et dégrader systématiquement dès que le champ manque pénaliserait
inutilement des sociétés correctement mono-devise pour lesquelles yfinance n'a
simplement pas peuplé le champ. L'audit n'a signalé aucune société précise
touchée par ce cas.

**Interaction avec le correctif GBp (2026-09-24)** : aucun conflit — le
correctif GBp agit sur l'*échelle* du prix (pence→livre), celui-ci sur la
*devise* des comptes. Les deux blocs sont séquencés correctement : GBp
normalise `quote_currency` en premier (`"GBP"` si la cotation était en pence),
la conversion C1 l'utilise ensuite pour la comparaison à `financialCurrency`.

## Ce qui ne change pas

- `extract_ratios`/`extract_ratios_financial` : aucune modification — elles
  reçoivent déjà des DataFrames cohérents en devise (convertis à la source) et
  un `shares_outstanding` déjà dégradé si besoin. Le mécanisme de repli C5
  (pool de `shares_outstanding=0.0` → ratios prix/comptes indisponibles) est
  réutilisé tel quel, pas dupliqué.
- Le WACC/coût des fonds propres : aucune modification — `market_cap` est
  déjà en devise de cotation, cohérent avec les comptes désormais convertis
  eux aussi.
- Les prix affichés à l'utilisateur (cours actuel, repères d'entrée/sortie)
  restent dans la devise de cotation réelle, jamais convertis — seuls les
  comptes le sont, pour rejoindre le prix, pas l'inverse.

## Tests

- `fetch_fx_rate` : même devise → 1.0 ; paire USD/HKD composée via le pivot
  USD (les deux legs mockés) → taux correct ; devise non gérée d'un côté →
  None ; échec réseau sur un leg → None.
- `fetch_company_financials` : `financialCurrency` ≠ `currency` avec taux
  disponible → les 3 DataFrames de comptes sont multipliés par le taux (test
  avec un taux simple, ex. 2.0, vérifie que `Net Income` double).
- Même scénario mais `fetch_fx_rate` renvoie `None` → `shares_outstanding`
  devient `0.0` ; test dédié confirmant que ce repli se déclenche bien depuis
  ce chemin précis (distinct des tests C5 existants, qui couvrent déjà la
  cascade en aval une fois `shares_outstanding=0.0`).
- `financialCurrency` absent (`None`) → aucune conversion tentée, comportement
  inchangé (pas de régression sur le cas majoritaire).
- Régression numérique proche du cas réel AIA : comptes en USD
  (`financialCurrency`), cotation en HKD, taux ≈7.8 — vérifie que le P/E après
  conversion est cohérent avec le taux appliqué (pas 7.8x trop élevé comme
  avant ce correctif).

## Déploiement

Comme pour les correctifs précédents de cet audit : merge, puis run manuel du
workflow GitHub Actions pour vérifier sur les vraies données de production
(AIA, IHG.L et quelques autres sociétés signalées par l'audit), avec un
contrôle explicite des P/E/P/B affichés avant/après.
