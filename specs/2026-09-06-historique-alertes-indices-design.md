# Historique de score & alertes par entreprise (Indices) — design

Suite de la refonte Indices (WACC par entreprise, mergé). Apporte à
l'onglet Indices l'équivalent du mécanisme `score_history.json` /
`compute_alerts` déjà en place côté Or, adapté aux 5 entreprises pilotes.

## Contexte

Le volet Or garde un historique quotidien du score composite et en
déduit 4 types d'alertes (veille, chute rapide, positionnement CFTC
extrême, conditions d'entrée réunies). Le volet Indices n'a aucun
équivalent : chaque run écrase `docs/indices.json` sans mémoire du run
précédent, donc aucune notion de tendance ni de franchissement de seuil
n'est possible pour une entreprise donnée.

## Objectif

Pour chacune des 5 entreprises pilotes : conserver un historique
quotidien du score composite, et en déduire 3 types d'alertes
concrètes, affichées sur la page détail de l'entreprise — sans jamais
bloquer un run si l'historique est absent/corrompu.

## Décisions actées

- **3 types d'alertes** (miroir du modèle Or, adapté aux signaux déjà
  disponibles côté Indices — pas d'équivalent CFTC/FOMC ici) :
  - **Veille** — le score composite de l'entreprise vient de franchir
    +15 à la hausse (identique à l'Or).
  - **Risque, chute rapide** — chute de `RAPID_DROP_POINTS` (20) points
    ou plus en moins de `RAPID_DROP_DAYS` (5) jours (mêmes seuils que
    l'Or, réutilisés tels quels).
  - **Entrée, conditions réunies** — score > 15 **et** cours actuel à
    moins de `NEAR_ENTRY_PCT` (5%) du repère d'entrée. Seuil différent
    de celui de l'Or (`NEAR_SUPPORT_PCT = 1.0`, contre la MM200, un
    niveau technique dur) : le repère d'entrée Indices est déjà une
    moyenne valorisation/technique avec ±30% de marge de sécurité — 1%
    serait presque toujours faux. 5% est un choix de jugement, validé
    avec l'utilisateur en amont de ce spec.
  - Sinon, alerte `info` neutre ("Pas de signal actif"), comme l'Or.
- **Historique par ticker, pas global** : contrairement à l'Or (1 seul
  actif, rétention par nombre total d'entrées), l'historique Indices
  mélange 5 tickers dans un seul fichier. La rétention retrime donc
  **par ticker** après chaque ajout (730 entrées par ticker, ~2 ans
  chacune), pour que les 5 entreprises gardent leur fenêtre complète
  indépendamment les unes des autres.
- **Fichier séparé, jamais copié dans `docs/`** : `indices_history.json`
  à la racine du repo, comme `score_history.json` pour l'Or — usage
  strictement interne au calcul des alertes du run suivant, jamais
  exposé au frontend directement (les alertes calculées, elles, sont
  incluses dans `docs/indices.json`).
- **Pas de flag `daily_snapshot`** : le pipeline Or distingue les runs
  horaires (pas de snapshot) du créneau quotidien (snapshot + email) —
  Indices tourne déjà une seule fois par jour (`indices.yml`, 06h00
  UTC), donc chaque run ajoute son entrée à l'historique sans condition.
- **Aucun email** : contrairement à l'Or, pas de notification email pour
  ces alertes — affichage web uniquement, cohérent avec le reste
  d'Indices aujourd'hui.

## Pipeline de données

### Stockage

```python
INDICES_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "indices_history.json"
)
HISTORY_RETENTION_PER_TICKER = 730  # ~2 ans, une entrée par jour et par ticker


def load_indices_history(path=INDICES_HISTORY_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError:
            return []


def append_indices_history(entries: list[dict], path=INDICES_HISTORY_PATH) -> list[dict]:
    """Ajoute les entrées du jour (une par entreprise) et retrimme
    indépendamment chaque ticker à HISTORY_RETENTION_PER_TICKER, pour que
    l'ajout d'une entreprise ne pousse pas hors fenêtre l'historique
    d'une autre."""
    history = load_indices_history(path)
    history.extend(entries)
    by_ticker: dict[str, list[dict]] = {}
    for entry in history:
        by_ticker.setdefault(entry["ticker"], []).append(entry)
    trimmed = []
    for ticker_entries in by_ticker.values():
        trimmed.extend(ticker_entries[-HISTORY_RETENTION_PER_TICKER:])
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(trimmed, fh, ensure_ascii=False, indent=2)
    return trimmed
```

Une entrée : `{"date": "2026-09-06", "ticker": "MC.PA", "composite": 42.3}`.

### Calcul des alertes

```python
RAPID_DROP_POINTS = 20   # même seuil que le volet Or
RAPID_DROP_DAYS = 5      # même fenêtre que le volet Or
NEAR_ENTRY_PCT = 5.0     # écart max (%) au repère d'entrée pour "conditions réunies"


def compute_company_alerts(
    ticker: str, composite: float, current_price: float | None,
    entry_price: float | None, previous_history: list[dict],
) -> list[dict]:
    """previous_history : déjà filtré sur ce seul ticker par l'appelant
    (main()). Ne lève jamais d'exception, toujours au moins 1 alerte
    (info neutre si rien ne se déclenche)."""
    today_str = datetime.today().strftime("%d/%m/%Y")
    alerts = []

    prev_composite = previous_history[-1]["composite"] if previous_history else None

    if prev_composite is not None and prev_composite <= 15 < composite:
        alerts.append({
            "kind": "watch",
            "title": "Score composite a franchi +15",
            "detail": "Surveillance active enclenchée pour cette entreprise.",
            "date": today_str,
        })

    cutoff = datetime.today().date() - timedelta(days=RAPID_DROP_DAYS)
    recent = [
        e for e in previous_history
        if datetime.strptime(e["date"], "%Y-%m-%d").date() >= cutoff
    ]
    if recent:
        max_recent = max(e["composite"] for e in recent)
        drop = composite - max_recent
        if drop <= -RAPID_DROP_POINTS:
            alerts.append({
                "kind": "risque",
                "title": "Chute rapide du score composite",
                "detail": f"Repricing de {drop:+.1f} points en moins de {RAPID_DROP_DAYS} jours.",
                "date": today_str,
            })

    near_entry = (
        current_price is not None and entry_price is not None and entry_price > 0
        and abs(current_price - entry_price) / entry_price * 100 < NEAR_ENTRY_PCT
    )
    if composite > 15 and near_entry:
        alerts.append({
            "kind": "entree",
            "title": "Conditions d'entrée réunies",
            "detail": f"Score favorable, cours à moins de {NEAR_ENTRY_PCT:.0f}% du repère d'entrée.",
            "date": today_str,
        })

    if not alerts:
        alerts.append({
            "kind": "info",
            "title": "Pas de signal actif",
            "detail": "Aucune des conditions de veille, d'entrée ou de risque n'est réunie aujourd'hui.",
            "date": today_str,
        })

    return alerts
```

### Câblage dans `main()`

Après la boucle qui construit les `company_entry` (inchangée) :

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

    history = load_indices_history()
    today_str = datetime.today().strftime("%Y-%m-%d")
    new_entries = []
    for company in companies:
        ticker_history = [e for e in history if e["ticker"] == company["ticker"]]
        company["alerts"] = compute_company_alerts(
            company["ticker"], company["score"], company["current_price"],
            company["entry_price"], ticker_history,
        )
        new_entries.append({
            "date": today_str, "ticker": company["ticker"], "composite": company["score"],
        })
    append_indices_history(new_entries)

    # ... écriture docs/indices.json inchangée (chaque `company` a maintenant "alerts")
```

Ce bloc historique/alertes s'exécute *après* la boucle par entreprise
(déjà protégée individuellement par un `try/except`) — une exception ici
planterait tout le run, y compris la publication du score déjà calculé
avec succès pour les 5 entreprises. Deux garde-fous : `compute_company_alerts`
et `append_indices_history` n'utilisent que des guard clauses (aucun accès
qui puisse lancer `KeyError`/`ZeroDivisionError` de façon non gérée), et
l'ensemble du bloc est en plus entouré d'un `try/except` dans `main()` —
une panne imprévue (disque plein, permissions) dégrade vers "pas
d'alertes ce run" plutôt que de faire échouer toute la publication.

## Schéma JSON

Chaque entrée de `docs/indices.json["companies"]` gagne une clé
top-level `"alerts": [...]`, même forme que celle du volet Or
(`kind`/`title`/`detail`/`date`).

## Frontend (`docs/index.html`)

Un panneau "Alertes" sur `renderCompanyDetail`, réutilisant tel quel le
CSS déjà défini pour l'Or (`.alert-row`, `.alert-head`, `.alert-date`,
`.alert-detail`) et la fonction JS `alertColor(kind)` déjà présente sur
la page (mappe `risque`→rust, `watch`/`entree`→gold) — zéro nouveau
style, juste un nouveau bloc de rendu qui `map()` sur `company.alerts`
de la même façon que le fait déjà le rendu des alertes de l'Or.

## Tests

`tests/test_indices_score.py`, nouveaux tests couvrant :
- `load_indices_history` : fichier absent → `[]` ; JSON corrompu → `[]`.
- `append_indices_history` : ajout simple ; retrim indépendant par
  ticker (mélanger 2 tickers, vérifier qu'ajouter au ticker A ne
  tronque pas l'historique du ticker B) ; volume au-delà de
  `HISTORY_RETENTION_PER_TICKER` réellement tronqué par ticker.
- `compute_company_alerts` : chacun des 3 déclencheurs indépendamment,
  le cas neutre (`info`), et qu'un historique vide ne lève pas
  d'exception (`previous_history=[]`).

## Risques connus

- **Seuil `NEAR_ENTRY_PCT` = 5% arbitraire** : pas de données
  historiques pour le calibrer plus finement (le repère d'entrée
  lui-même n'existe que depuis la sous-fonctionnalité
  `cours-entree-sortie`, quelques jours de recul en pratique). À
  ajuster si l'alerte "conditions d'entrée réunies" se déclenche trop
  souvent ou jamais une fois en production.
- **Historique encore court** : les alertes de chute rapide/franchissement
  ne deviennent pertinentes qu'après quelques jours de runs réels — rien
  à afficher de significatif immédiatement après le merge (comportement
  attendu, pas un bug).
