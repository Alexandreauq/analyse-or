# Analyse financière par entreprise (Vernimmen) — design

Suite de la refonte Indices (WACC, historique/alertes, mergés). Ajoute,
pour chacune des 5 entreprises pilotes, une analyse financière écrite
(comptes annuels + trimestriels) façon Vernimmen, générée par Claude
Opus 5, accessible depuis une page dédiée sur sa fiche.

## Contexte

L'utilisateur demande un fichier ou lien par entreprise avec une analyse
financière des comptes annuels et trimestriels. Précisions apportées en
discussion :
- L'analyse doit être **générée par nous** à partir des données déjà
  collectées (pas un simple lien externe), en s'appuyant sur la
  synthèse Vernimmen déjà utilisée comme référence méthodologique du
  reste du scoring Indices.
- Format : une **page dédiée** par entreprise (nouvelle route), pas une
  section repliable sur la fiche existante — accessible par un bouton.
- Modèle : **Claude Opus 5**, choisi explicitement par l'utilisateur
  après avoir vu le coût estimé (voir "Fréquence de génération"
  ci-dessous) — qualité maximale, pas Sonnet/Haiku.
- L'utilisateur enverra plus tard des exemples d'analyses financières
  pour calibrer le format/la profondeur : on construit le pipeline
  maintenant avec notre propre jugement basé sur le Vernimmen, on
  affinera le prompt ensuite (aucun changement d'architecture attendu,
  seulement le contenu du prompt système).

## Objectif

Générer, pour chaque entreprise, une analyse financière structurée
(diagnostic global, structure financière, rentabilité, trésorerie/FCF,
dynamique trimestrielle récente, synthèse) à partir des comptes annuels
(jusqu'à ~4 ans, limite yfinance déjà connue) et des derniers trimestres
publiés, puis l'exposer sur une page dédiée par entreprise.

## Fréquence de génération : uniquement au nouveau trimestre

**Décision actée après discussion sur le coût.** Une génération
quotidienne coûterait environ 20-25$/mois pour les 5 entreprises pilotes
avec Opus 5 (et proportionnellement plus pour une extension future aux
40 valeurs du CAC 40 — estimé 150-200$/mois au même rythme). Ce rythme
est de toute façon injustifié : les comptes ne changent que quelques
fois par an (à chaque trimestre publié), pas tous les jours comme le
score composite.

**Mécanisme retenu :** avant d'écraser `docs/indices.json`, le run lit
son propre contenu de la veille (déjà commité par le run précédent —
aucun nouveau fichier d'état nécessaire). Pour chaque entreprise, on
compare la date du dernier trimestre publié (déjà disponible via
`quarterly_financials`) à celle stockée hier :
- **Identique** → l'analyse et sa date de trimestre sont recopiées
  telles quelles depuis le JSON de la veille. Aucun appel Claude.
- **Différente, ou absente (première fois)** → régénération via Opus 5,
  nouvelle date de trimestre stockée.

Coût réel attendu avec ce mécanisme : ~4-5 régénérations par entreprise
et par an (pas 365) → de l'ordre de quelques centimes/mois pour 5
entreprises, ~2-3$/mois même pour une extension à 40 valeurs.

## Pipeline de données

### Contexte financier textuel (nouveau)

`fetch_company_financials` ne garde aujourd'hui que le **dernier**
exercice de chaque poste (via `extract_ratios`) — insuffisant pour une
analyse de tendance. Une nouvelle fonction construit un texte structuré
à partir des séries complètes déjà chargées (`financials`,
`balance_sheet`, `cashflow`, `quarterly_financials`), avant qu'elles ne
sortent de portée dans `fetch_company_financials` :

```python
def build_financial_narrative_context(
    financials, balance_sheet, cashflow, quarterly_financials,
) -> str:
    """Formate les séries annuelles (jusqu'à ~4 ans, le plus récent en
    premier) et les derniers trimestres en un texte structuré, destiné à
    être injecté dans le prompt Claude — pas de calcul ici, seulement de
    la mise en forme brute. Une ligne 'non disponible' remplace toute
    valeur manquante plutôt que de faire échouer le formatage."""
    revenue = get_row(financials, "Total Revenue", "Operating Revenue")
    ebitda = get_row(financials, "EBITDA", "Normalized EBITDA")
    ebit = get_row(financials, "EBIT", "Operating Income", "Total Operating Income As Reported")
    net_income = get_row(financials, "Net Income", "Net Income Common Stockholders")
    equity = get_row(balance_sheet, "Stockholders Equity", "Common Stock Equity")
    total_debt = get_row(balance_sheet, "Total Debt")
    cash = get_row(balance_sheet, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
    op_cash_flow = get_row(cashflow, "Operating Cash Flow")
    capex = get_row(cashflow, "Capital Expenditure")

    def _fmt(value) -> str:
        return "non disponible" if _is_missing(value) else f"{value:,.0f}"

    lines = ["Comptes annuels (le plus récent en premier) :"]
    for col in financials.columns:
        net_debt = (
            total_debt[col] - cash[col]
            if not _is_missing(total_debt[col]) and not _is_missing(cash[col]) else None
        )
        fcf = (
            op_cash_flow[col] + capex[col]
            if not _is_missing(op_cash_flow[col]) and not _is_missing(capex[col]) else None
        )
        lines.append(
            f"- {col.date() if hasattr(col, 'date') else col} : CA {_fmt(revenue[col])}, "
            f"EBITDA {_fmt(ebitda[col])}, EBIT {_fmt(ebit[col])}, "
            f"résultat net {_fmt(net_income[col])}, capitaux propres {_fmt(equity[col])}, "
            f"dette nette {_fmt(net_debt)}, FCF {_fmt(fcf)}"
        )

    quarterly_revenue = get_row(quarterly_financials, "Total Revenue", "Operating Revenue")
    lines.append("\nDerniers trimestres publiés (le plus récent en premier) :")
    for col in quarterly_financials.columns:
        lines.append(
            f"- {col.date() if hasattr(col, 'date') else col} : CA {_fmt(quarterly_revenue[col])}"
        )

    return "\n".join(lines)
```

### Date du dernier trimestre (nouveau champ brut)

```python
def latest_quarter_date(quarterly_financials) -> str | None:
    """Date du trimestre le plus récent publié, format ISO (YYYY-MM-DD).
    None si aucune colonne (yfinance en panne pour ce ticker)."""
    cols = list(quarterly_financials.columns)
    if not cols:
        return None
    col = cols[0]
    return col.date().isoformat() if hasattr(col, "date") else str(col)
```

`fetch_company_financials` expose ces deux éléments dans son dict de
retour :

```python
ratios["financial_context"] = build_financial_narrative_context(
    financials, balance_sheet, cashflow, quarterly_financials
)
ratios["latest_quarter_date"] = latest_quarter_date(quarterly_financials)
```

### Génération de l'analyse (Claude Opus 5)

```python
ANTHROPIC_MODEL_ANALYSIS = "claude-opus-5"

FINANCIAL_ANALYSIS_SYSTEM_PROMPT = """Tu es un analyste financier qui \
applique la méthode du Vernimmen (synthèse du diagnostic financier : \
rentabilité économique et financière, structure financière et \
solvabilité, analyse de la trésorerie et du free cash-flow, dynamique \
récente) à une entreprise cotée. Rédige une analyse structurée en \
français, factuelle, sans conseil d'investissement ni recommandation \
d'achat/vente, à partir des seules données fournies."""


def generate_financial_analysis(
    company_name: str, financial_context: str, ratios_summary: str,
) -> str | None:
    """Génère l'analyse financière via Claude Opus 5 (thinking adaptatif,
    effort élevé — tâche de raisonnement/rédaction, pas de classification
    simple). None si la clé API est absente ou en cas d'échec — jamais
    d'exception, même contrat que summarize_news_item."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    prompt = (
        f"Entreprise : {company_name}\n\n{financial_context}\n\n"
        f"Ratios déjà calculés (ne pas les recalculer, les interpréter) :\n"
        f"{ratios_summary}\n\n"
        "Rédige une analyse structurée avec ces sections, dans cet "
        "ordre : diagnostic global (2-3 phrases), structure financière "
        "et solvabilité, rentabilité économique et financière, analyse "
        "de la trésorerie et du free cash-flow, dynamique récente "
        "(dernier trimestre vs tendance), synthèse.\n\n"
        "Réponds uniquement avec un objet JSON valide, sans texte "
        'autour, de la forme : {"analysis_html": "..."} où la valeur '
        "est le texte de l'analyse en HTML, en utilisant uniquement "
        "les balises <h3>, <p>, <ul>, <li>, <strong> (aucune autre "
        "balise, aucun style inline, aucun script)."
    )

    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL_ANALYSIS,
                "max_tokens": 8000,
                "system": FINANCIAL_ANALYSIS_SYSTEM_PROMPT,
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        text_block = next(
            (b["text"] for b in data["content"] if b.get("type") == "text"), None
        )
        if text_block is None:
            return None
        text = text_block.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = text.split("\n", 1)[1]
        parsed = json.loads(text)
        return parsed.get("analysis_html")
    except Exception:
        return None
```

Différences volontaires par rapport à `summarize_news_item` (même
fichier) : `thinking`/`output_config.effort` (Opus 5, tâche de
raisonnement, contrairement à la classification simple des actus),
`max_tokens` beaucoup plus haut (8000 contre 150 — texte long),
`timeout` plus généreux (120s contre 20s — le thinking adaptatif à
effort élevé peut prendre significativement plus de temps), et
extraction du bloc `type == "text"` par recherche plutôt que
`content[0]` en dur (la réponse peut contenir un bloc `thinking` avant
le texte final).

### Câblage : carry-forward ou régénération

```python
def load_previous_company_analyses() -> dict:
    """Lit le docs/indices.json du run précédent (déjà commité) pour en
    extraire, par ticker, l'analyse financière et la date de trimestre
    qu'elle couvre. {} si le fichier n'existe pas encore ou est
    illisible — jamais d'exception."""
    if not os.path.exists(OUTPUT_JSON_PATH):
        return {}
    try:
        with open(OUTPUT_JSON_PATH, encoding="utf-8") as fh:
            previous = json.load(fh)
        return {
            c["ticker"]: {
                "financial_analysis_html": c.get("financial_analysis_html"),
                "financial_analysis_quarter": c.get("financial_analysis_quarter"),
            }
            for c in previous.get("companies", [])
        }
    except Exception:
        return {}
```

Dans `build_company_entry` (signature inchangée — la comparaison se
fait avec les données déjà présentes dans `data`, pas un nouveau
paramètre), après le calcul du WACC et avant la construction des
`factors` :

```python
    previous = previous_analyses.get(ticker, {})
    current_quarter = data["latest_quarter_date"]
    if (
        current_quarter is not None
        and current_quarter == previous.get("financial_analysis_quarter")
        and previous.get("financial_analysis_html")
    ):
        financial_analysis_html = previous["financial_analysis_html"]
        financial_analysis_quarter = previous["financial_analysis_quarter"]
    else:
        ratios_summary = (
            f"ROCE {data['roce']:.1f}%, ROE {data['roe']:.1f}%, "
            f"dette nette/EBITDA {data['net_debt_ebitda']:.1f}x, "
            f"ICR {data['icr']:.1f}x, CAGR CA {data['cagr_ca']:+.1f}%/an, "
            f"CAGR EBITDA {data['cagr_ebitda']:+.1f}%/an, "
            f"conversion FCF/EBITDA {data['fcf_conversion']:.0f}%, "
            f"coût du capital {cost_of_capital:.1f}%"
        )
        financial_analysis_html = generate_financial_analysis(
            name, data["financial_context"], ratios_summary
        )
        financial_analysis_quarter = current_quarter
```

`build_company_entry` reçoit `previous_analyses: dict` comme nouveau
4e paramètre (résolu une seule fois dans `main()`, comme
`risk_free_rate`, pas recalculé par entreprise) :

```python
def build_company_entry(
    ticker: str, name: str, risk_free_rate: float | None, previous_analyses: dict,
) -> dict:
```

`main()` charge `previous_analyses` une seule fois, **avant** la boucle
(donc avant que `docs/indices.json` soit écrasé) :

```python
def main():
    risk_free_rate = fetch_risk_free_rate()
    previous_analyses = load_previous_company_analyses()
    companies = []
    for company in COMPANIES:
        try:
            companies.append(
                build_company_entry(
                    company["ticker"], company["name"], risk_free_rate, previous_analyses,
                )
            )
        except Exception as e:
            print(f"Erreur pour {company['ticker']} ({company['name']}) : {e}")
```

Le dict retourné par `build_company_entry` gagne 2 clés top-level :
`"financial_analysis_html"` (`str | None`) et
`"financial_analysis_quarter"` (`str | None`).

## Schéma JSON

Chaque entrée de `docs/indices.json["companies"]` gagne :
`"financial_analysis_html": "<h3>...</h3><p>...</p>..." | null`,
`"financial_analysis_quarter": "2026-06-30" | null`.

## Frontend (`docs/index.html`)

- Un bouton "Analyse financière complète" sur la fiche entreprise
  (`renderCompanyDetail`), à côté d'Actu / Détail du calcul, qui navigue
  vers `#indices/<ticker>/analyse` (`location.hash = ...`).
- `parseRoute()` gagne un cas : `hash` de la forme
  `indices/<ticker>/analyse` → `{ screen: 'indices', ticker, view:
  'analyse' }`.
- Nouvelle fonction `renderCompanyAnalysis(company)` : affiche
  `company.financial_analysis_html` tel quel (même confiance déjà
  accordée aux résumés d'actus/`raw_value` des facteurs — contenu
  entièrement généré par notre propre pipeline, jamais d'entrée
  utilisateur), avec un lien retour vers la fiche entreprise. Si
  `financial_analysis_html` est `null` (génération jamais aboutie,
  clé API absente...), afficher un message neutre plutôt qu'une page
  vide ou une erreur JS.

## Secrets / configuration

Aucun nouveau secret : réutilise `ANTHROPIC_API_KEY` déjà configuré
dans `indices.yml` (`secrets.CLAUDE_API_KEY`) pour les résumés d'actus.

## Méthodologie (`Methodologie_Analyse_Indices.md`)

Nouvelle section décrivant : la trame Vernimmen utilisée (diagnostic
global, structure financière, rentabilité, trésorerie/FCF, dynamique
récente, synthèse), le modèle (Opus 5), et surtout le mécanisme de
régénération au trimestre (pas quotidien) avec le raisonnement coût
qui le justifie.

## Tests

`tests/test_indices_score.py`, nouveaux tests couvrant :
- `build_financial_narrative_context` : formatage correct sur des
  fixtures multi-années/trimestres, valeurs manquantes → "non
  disponible" plutôt qu'une exception.
- `latest_quarter_date` : cas normal, aucune colonne → `None`.
- `generate_financial_analysis` : clé API absente → `None` sans appel
  réseau (mock de `requests.post`, comme `fetch_risk_free_rate`) ; appel
  réussi → extraction correcte du bloc `text` même avec un bloc
  `thinking` précédent dans `content` ; JSON malformé / clé API en
  erreur / exception réseau → `None`, jamais de levée.
- `load_previous_company_analyses` : fichier absent → `{}` ; JSON
  corrompu → `{}` ; fichier présent → dict correctement indexé par
  ticker.
- Câblage dans `build_company_entry` : même date de trimestre que la
  veille → analyse recopiée, **aucun appel** à
  `generate_financial_analysis` (vérifié par mock/spy) ; date différente
  ou absente → appel effectué, nouvelle date stockée.

## Risques connus

- **Coût si le prompt grossit** : le contexte financier textuel reste
  volontairement compact (formatage brut, pas de prose) ; si les futurs
  exemples de l'utilisateur poussent vers des analyses beaucoup plus
  longues, le coût par régénération (et donc le budget mensuel estimé)
  augmentera proportionnellement — à surveiller après calibrage.
- **Latence du run CI** : jusqu'à 5 appels Opus 5 à effort élevé
  peuvent significativement rallonger `indices.yml` le jour où toutes
  les entreprises publient leur trimestre en même temps (rare en
  pratique, les calendriers de publication étant étalés) — timeout
  généreux (120s/appel) mais pas de garde-fou de délai global sur le
  job.
- **Contenu HTML non sanitisé côté frontend** : même risque déjà
  accepté pour les résumés d'actus et les `raw_value` des facteurs —
  contenu entièrement généré par notre propre pipeline (jamais
  d'entrée utilisateur non filtrée), donc pas un vecteur XSS réel ici,
  mais à garder en tête si le prompt était un jour exposé à un contenu
  externe non maîtrisé.
- **Première génération pour les 5 entreprises** : au premier run après
  merge, `previous_analyses` sera vide pour toutes → 5 appels Opus 5 le
  même jour (coût ponctuel, pas récurrent).
