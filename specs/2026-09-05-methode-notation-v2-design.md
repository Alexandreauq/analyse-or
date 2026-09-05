# Méthode de notation v2 (dynamique récente, actualité récente) — design

Branche : à créer (ex : `notation-v2`)
Sous-projet 2/3 de la refonte Indices (1: actus enrichies — livré ; 3:
cours + point d'entrée/sortie — à venir, brainstormé séparément).

## Contexte

La méthode de notation actuelle (`Methodologie_Analyse_Indices.md`, 5
facteurs pondérés) juge une entreprise uniquement sur ses comptes annuels
publiés (rentabilité, structure financière, croissance 5 ans, génération
de cash, valorisation relative) — jugée trop statique par l'utilisateur :
elle ignore la tendance récente du marché sur le titre, l'évolution des
derniers résultats trimestriels par rapport à la tendance de fond, et le
ton des actualités récentes.

## Objectif

Ajouter 2 facteurs pondérés au score composite existant :
- **Dynamique récente** : combine la tendance du cours de bourse
  (cours actuel vs moyenne mobile 200 jours) et l'accélération des
  résultats (croissance du dernier trimestre publié vs le CAGR 5 ans déjà
  calculé).
- **Actualité récente** : moyenne du ton (positif/neutre/négatif) des
  actus des ~2 dernières semaines, tel que classé par Claude — réutilise
  et étend l'appel IA déjà en place pour le résumé des actus (sous-projet
  1), sans appel API supplémentaire.

## Hors périmètre

- Toute donnée de marché autre que prix/MM200 (volumes, volatilité
  implicite, etc.).
- Sentiment de marché externe (réseaux sociaux, forums) — uniquement les
  actus déjà collectées via le flux Google News existant.
- Réévaluation du choix déjà acté de ne pas scraper le contenu réel des
  articles (cf. `specs/2026-09-05-actus-resume-ia-design.md`, "Risques
  connus") — le sentiment est classé à partir du titre + résumé titre-seul
  déjà généré, pas du contenu de l'article.

## Pondération

Les 2 nouveaux facteurs reçoivent 10% chacun ; les 5 facteurs existants
sont réduits proportionnellement (facteur d'échelle 0.8) pour que le total
reste 100% :

| Facteur | Poids v1 | Poids v2 |
|---|---|---|
| Rentabilité / création de valeur | 30% | 24% |
| Structure financière / solvabilité | 25% | 20% |
| Croissance | 20% | 16% |
| Génération de cash | 15% | 12% |
| Valorisation relative | 10% | 8% |
| **Dynamique récente** | — | **10%** |
| **Actualité récente** | — | **10%** |
| **Total** | 100% | **100%** |

`WEIGHTS` dans `indices_score.py` passe de 5 à 7 clés avec ces valeurs.
Les bornes d'interprétation du score composite (`interpret()`, -15/+15,
+15/+50, etc.) sont inchangées : le composite reste sur la même échelle
-100/+100 (somme pondérée × 10), seule la composition des facteurs change.

## Pipeline de données

### Dynamique récente

`indices_score.py`, dans `fetch_company_financials` (qui a déjà accès à
`history`, la série de clôtures journalières déjà récupérée pour les
multiples de valorisation) :

```python
current_price = float(history.iloc[-1]) if len(history) else None
ma200 = float(history.tail(200).mean()) if len(history) else None
ecart_pct_ma200 = (
    (current_price - ma200) / ma200 * 100
    if current_price is not None and ma200 else None
)
```

Nouvelle fonction `extract_quarterly_growth(quarterly_financials) -> float | None`
(fonction pure, testable isolément comme `extract_ratios`) :

```python
def extract_quarterly_growth(quarterly_financials) -> float | None:
    """CA du dernier trimestre publié vs le même trimestre il y a un an
    (%). None si moins de 5 trimestres sont disponibles (yfinance
    n'expose généralement que les 4-5 derniers) ou si une valeur est
    manquante/NaN."""
    cols = list(quarterly_financials.columns)
    if len(cols) < 5:
        return None
    revenue = get_row(quarterly_financials, "Total Revenue", "Operating Revenue")
    latest, year_ago = revenue[cols[0]], revenue[cols[4]]
    if _is_missing(latest) or _is_missing(year_ago) or year_ago <= 0:
        return None
    return (latest / year_ago - 1) * 100
```

**Simplification assumée** : `cols[4]` est traité comme "le même
trimestre il y a un an" en supposant une cadence trimestrielle régulière
sans trou dans les colonnes renvoyées par yfinance. Si une entreprise a un
calendrier fiscal irrégulier (trimestre manquant, exercice décalé), `cols[4]`
pourrait être un trimestre différent — dégradation silencieuse vers une
comparaison légèrement inexacte plutôt qu'une erreur, jugé acceptable
pour un signal secondaire à 10% de poids (pas de vérification de date
explicite au v1).

`fetch_company_financials` appelle `t.quarterly_financials` (nouvel appel
yfinance, même ticker déjà ouvert) et passe le résultat à
`extract_quarterly_growth`. Pas de try/except dédié pour cet appel — même
traitement que les appels `t.financials`/`t.balance_sheet`/`t.cashflow`
existants : une panne yfinance sur ce point fait échouer toute l'entrée de
l'entreprise pour le jour, protégée par le try/except déjà en place par
entreprise dans `main()`. Cohérent avec le comportement actuel (pas un
nouveau point de fragilité, juste un appel de plus au même endroit).

Nouvelle fonction de scoring :

```python
PRICE_MOMENTUM_SCALE = 20.0    # % d'écart vs MM200 pour un score plein
QUARTERLY_ACCEL_SCALE = 10.0   # points d'écart de croissance pour un score plein


def score_dynamique_recente(
    ecart_pct_ma200: float | None,
    quarterly_yoy_growth_ca: float | None,
    cagr_ca: float,
) -> FactorResult:
    """Moyenne de 2 sous-signaux (chacun mis à l'échelle -10/+10
    indépendamment avant moyenne, car unités différentes) : tendance du
    cours vs MM200, et accélération du dernier trimestre publié vs la
    tendance 5 ans déjà calculée (cagr_ca). Si un sous-signal manque,
    la moyenne ne porte que sur celui disponible ; si aucun n'est
    disponible, score neutre 0.0."""
    sub_scores = []
    raw_parts = []
    if ecart_pct_ma200 is not None:
        sub_scores.append(_clamp(ecart_pct_ma200 / PRICE_MOMENTUM_SCALE * 10))
        raw_parts.append(f"Cours {ecart_pct_ma200:+.1f}% vs MM200")
    if quarterly_yoy_growth_ca is not None:
        acceleration = quarterly_yoy_growth_ca - cagr_ca
        sub_scores.append(_clamp(acceleration / QUARTERLY_ACCEL_SCALE * 10))
        raw_parts.append(
            f"CA dernier trim. {quarterly_yoy_growth_ca:+.1f}% vs an dernier "
            f"(tendance 5 ans {cagr_ca:+.1f}%)"
        )
    score = sum(sub_scores) / len(sub_scores) if sub_scores else 0.0
    raw_value = " — ".join(raw_parts) if raw_parts else "Données insuffisantes"
    return FactorResult("Dynamique récente", score, WEIGHTS["dynamique_recente"], raw_value)
```

### Actualité récente

`summarize_news_item` (introduite au sous-projet 1) change de signature
de retour : `str` → `dict` avec les clés `"summary"` et `"sentiment"`
(int, `-1`/`0`/`1`), toujours présentes, `{"summary": "", "sentiment": 0}`
sur tout échec (clé API absente, erreur réseau, réponse HTTP non-200,
JSON malformé) — même contrat "ne lève jamais" qu'aujourd'hui.

Le prompt est étendu pour demander une réponse structurée en une seule
requête (pas d'appel API supplémentaire) :

```python
    ...
    prompt += (
        "\n\nRéponds uniquement avec un objet JSON valide, sans texte "
        "autour, de la forme : "
        '{"summary": "...", "sentiment": -1|0|1} '
        "où sentiment vaut -1 si l'actu est plutôt défavorable pour "
        "l'entreprise, 0 si neutre ou mixte, 1 si plutôt favorable."
    )
    try:
        resp = requests.post(...)  # inchangé
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = text.split("\n", 1)[1]
        parsed = json.loads(text)
        summary = str(parsed.get("summary", "")).strip()
        sentiment = parsed.get("sentiment", 0)
        if sentiment not in (-1, 0, 1):
            sentiment = 0
        return {"summary": summary, "sentiment": sentiment}
    except Exception:
        return {"summary": "", "sentiment": 0}
```

`fetch_news` attache maintenant `item["summary"]` ET `item["sentiment"]`
(au lieu de juste `summary`) à partir du dict renvoyé.

Nouvelle fonction d'agrégation. Nécessite `timedelta` en plus de
`datetime`, déjà importé depuis le module `datetime` en tête de fichier —
ajouter `timedelta` à cet import existant plutôt que d'en créer un
nouveau :

```python
NEWS_SENTIMENT_WINDOW_DAYS = 14


def score_actualite_recente(news_items: list[dict]) -> FactorResult:
    """Moyenne du sentiment des actus datées de moins de 14 jours, mise à
    l'échelle -10/+10. Neutre (0.0) si aucune actu récente exploitable —
    ni erreur, ni biais optimiste/pessimiste par défaut."""
    cutoff = datetime.now() - timedelta(days=NEWS_SENTIMENT_WINDOW_DAYS)
    recent_sentiments = []
    for item in news_items:
        try:
            item_date = datetime.strptime(item["date"], "%Y-%m-%d")
        except (ValueError, TypeError, KeyError):
            continue
        if item_date >= cutoff:
            recent_sentiments.append(item.get("sentiment", 0))
    if not recent_sentiments:
        return FactorResult(
            "Actualité récente", 0.0, WEIGHTS["actualite_recente"],
            "Aucune actualité récente exploitable",
        )
    avg_sentiment = sum(recent_sentiments) / len(recent_sentiments)
    score = _clamp(avg_sentiment * 10)
    return FactorResult(
        "Actualité récente", score, WEIGHTS["actualite_recente"],
        f"Ton moyen des {len(recent_sentiments)} actualités récentes : {avg_sentiment:+.2f}",
    )
```

### Ordre d'exécution dans `build_company_entry`

Contrainte structurante : le facteur "Actualité récente" dépend des news
déjà récupérées (leur `sentiment`), donc **les news doivent être
récupérées avant que la liste complète des facteurs ne soit construite**
— inversion par rapport à l'ordre actuel (aujourd'hui : facteurs d'abord,
composite, puis news en dernier, séparément et sans impact sur le score).

```python
def build_company_entry(ticker: str, name: str) -> dict:
    data = fetch_company_financials(ticker)
    sector = data["sector"]

    try:
        news = fetch_news(name)
    except Exception as e:
        print(f"Erreur récupération news pour {name} : {e}")
        news = []

    factors = [
        score_rentabilite(data["roce"], data["roe"], COST_OF_CAPITAL_PROXY),
        score_structure_financiere(data["net_debt_ebitda"], data["icr"], sector),
        score_croissance(data["cagr_ca"], data["cagr_ebitda"]),
        score_generation_cash(data["fcf_conversion"]),
        score_valorisation(
            data["current_ev_ebitda"], data["avg_ev_ebitda_5y"],
            data["current_pe"], data["avg_pe_5y"], data["cagr_ebitda"],
        ),
        score_dynamique_recente(
            data["ecart_pct_ma200"], data["quarterly_yoy_growth_ca"], data["cagr_ca"],
        ),
        score_actualite_recente(news),
    ]
    composite = compute_composite(factors)
    ...
```

Le try/except autour de `fetch_news` (déjà en place) garantit qu'une
panne totale du flux RSS dégrade vers `news = []`, ce qui fait à son tour
retomber `score_actualite_recente([])` sur son cas "aucune actu
exploitable" (neutre 0.0) — pas de nouvelle fragilité introduite, la
dépendance se dégrade proprement de bout en bout.

## Schéma JSON

`companies[].factors[]` gagne 2 entrées (mêmes clés que les 5 existantes :
`name`, `score`, `weight`, `raw_value`) : `"Dynamique récente"` et
`"Actualité récente"`.

`companies[].news[]` gagne un champ `sentiment` (int, `-1`/`0`/`1`) à côté
de `title`/`date`/`link`/`source`/`summary`.

Aucun changement requis côté front-end (`docs/index.html`) : `renderCompanyDetail`
boucle déjà génériquement sur `company.factors` (name/weight/score/raw_value)
et n'a pas besoin de connaître les 2 nouveaux facteurs pour les afficher.
Le champ `sentiment` par actu n'est pas affiché individuellement (pas
demandé) — il alimente uniquement l'agrégation côté backend.

## Méthodologie (`Methodologie_Analyse_Indices.md`)

- Mettre à jour tous les poids listés (§1 à §5) selon le tableau ci-dessus.
- Ajouter §6 "Dynamique récente — poids 10%" et §7 "Actualité récente —
  poids 10%" décrivant les formules ci-dessus en langage méthodologique
  (pas de code), dans le même style que les sections existantes.
- Ajouter une note sous §7 rappelant explicitement la décision actée du
  sous-projet 1 : le sentiment est classé à partir du titre (et du résumé
  titre-seul déjà généré), pas du contenu réel de l'article, puisque le
  scraping réel a été abandonné.

## Tests

Dans `tests/test_indices_score.py`, mêmes conventions que les sous-projets
précédents (fixtures, pas d'appel réseau réel) :

- `extract_quarterly_growth` : moins de 5 trimestres → `None` ; croissance
  correcte avec 5+ trimestres ; valeur manquante (NaN) sur l'un des deux
  trimestres → `None`.
- `score_dynamique_recente` : les 2 sous-signaux disponibles (moyenne
  correcte) ; un seul disponible (moyenne sur celui-là seul) ; aucun
  disponible (0.0, "Données insuffisantes").
- `summarize_news_item` : mise à jour des tests existants pour le nouveau
  format de retour (`dict` avec `summary`/`sentiment`) ; nouveau test
  vérifiant le parsing d'une réponse JSON avec code fences (` ```json ``` `) ;
  nouveau test vérifiant le repli `{"summary": "", "sentiment": 0}` sur
  JSON malformé.
- `score_actualite_recente` : actus toutes récentes (moyenne correcte) ;
  mélange récent/ancien (seules les récentes comptent) ; liste vide
  (neutre, message dédié) ; toutes anciennes (neutre).
- `build_company_entry` : test d'intégration mis à jour vérifiant que les
  7 facteurs sont présents (pas 5), et que `factors[5].name ==
  "Dynamique récente"` / `factors[6].name == "Actualité récente"`.

## Risques connus

- La classification de sentiment par IA reste basée sur un titre (et un
  résumé lui-même généré à partir du titre, cf. sous-projet 1) — la
  précision du signal est plafonnée par cette limite déjà actée, pas une
  nouvelle limite introduite ici.
- `quarterly_financials` peut manquer de profondeur historique pour
  certaines entreprises (yfinance n'est pas garanti au-delà de 4-5
  trimestres) — dégrade vers le sous-signal prix seul dans "Dynamique
  récente", comportement déjà couvert par la conception ci-dessus.
- Le format JSON demandé à Claude peut occasionnellement échouer à
  parser (modèle économique, pas de mode "structured output" strict) —
  dégrade vers `{"summary": "", "sentiment": 0}`, cohérent avec le
  contrat "ne lève jamais" déjà en place.
