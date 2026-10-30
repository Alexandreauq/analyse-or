# Analyse financière IA à la demande — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer la génération proactive de `financial_analysis_html`
pour les 710 entreprises par une génération paresseuse déclenchée par
le premier visiteur qui consulte une fiche dont le trimestre a changé,
avec des plafonds $ (jour/semaine/mois) et une fréquence max par
ticker.

**Architecture:** `build_company_entry` (batch quotidien) arrête
d'appeler `generate_financial_analysis` et expose un nouveau champ
`latest_quarter_date` (toujours à jour) à côté de
`financial_analysis_quarter` (le trimestre que décrit le texte en
cache). Un nouveau mode CLI `indices_score.py --generate-one <ticker>`
fait tout le travail de génération + persistance + commit git pour un
seul ticker. Une nouvelle route FastAPI sur le backend déjà en ligne de
`assistant_ia` (port 8445) délègue à ce mode CLI via un sous-processus,
pour respecter la contrainte "lecture seule" du module. Le frontend
(`docs/index.html`) détecte la péremption et appelle cette route à la
demande.

**Tech Stack:** Python 3.12, FastAPI (assistant_ia), subprocess,
pytest, JavaScript vanilla (docs/index.html).

**Spec:** `docs/superpowers/specs/2026-10-30-financial-analysis-on-demand-design.md`

## Global Constraints

- Le score et les `factors` restent calculés quotidiennement pour
  toutes les entreprises, inchangés — seule la génération du texte
  `financial_analysis_html` devient paresseuse.
- Aucun plafond ($ ou fréquence) ne doit jamais lever d'exception ou
  bloquer le visiteur : tout dépassement renvoie un repli propre
  (ancien contenu ou indisponibilité), jamais une erreur HTTP 500.
- `INDICES_AI_DAILY_BUDGET_USD = 15.0` (déjà en place, ne pas y
  toucher) ; `INDICES_AI_WEEKLY_BUDGET_USD = 30.0` ;
  `INDICES_AI_MONTHLY_BUDGET_USD = 75.0` ; cooldown par ticker :
  `INDICES_AI_TICKER_COOLDOWN_HOURS = 24`.
- Aucun test ne doit jamais écrire dans un vrai fichier de plafond/
  cooldown du dépôt (`indices_ai_usage_today.json`,
  `indices_ai_usage_week.json`, `indices_ai_usage_month.json`,
  `indices_ai_ticker_cooldown.json`) — toujours un chemin `tmp_path`
  ou un monkeypatch des fonctions, même discipline que l'existant.
- `assistant_ia/api.py` reste "lecture seule" : la nouvelle route
  délègue toute écriture (fichier + git) au sous-processus
  `indices_score.py --generate-one`, jamais directement.

---

### Task 1: Extraire `build_ratios_summary` de `build_company_entry`

**Files:**
- Modify: `indices_score.py:6107-6152` (corps de `build_company_entry`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Produces: `build_ratios_summary(data: dict, cost_of_capital: float) -> str`
  — reprend tel quel les trois branches `is_financial`/`is_trust`/
  générique actuellement inline dans `build_company_entry`, aucun
  changement de comportement.
- Consumes (Task 2): `build_company_entry` appelle cette fonction au
  lieu d'inliner les branches.

- [ ] **Step 1: Écrire les tests de la fonction extraite**

```python
def test_build_ratios_summary_formats_the_generic_profile():
    data = {
        "is_financial": False, "is_trust": False,
        "roce": 12.3, "roce_available": True,
        "roe": 8.5,
        "structure_available": True, "net_debt_ebitda": 1.2, "icr": 6.0,
        "cagr_ca": 4.1, "cagr_ebitda": 5.2,
        "fcf_conversion_available": True, "fcf_conversion": 72,
    }
    out = indices_score.build_ratios_summary(data, cost_of_capital=7.5)
    assert "ROCE 12.3%" in out
    assert "ROE 8.5%" in out
    assert "dette nette/EBITDA 1.2x" in out
    assert "ICR 6.0x" in out
    assert "coût du capital 7.5%" in out


def test_build_ratios_summary_marks_unavailable_generic_ratios():
    data = {
        "is_financial": False, "is_trust": False,
        "roce": 0.0, "roce_available": False,
        "roe": 8.5,
        "structure_available": False, "net_debt_ebitda": 0.0, "icr": 0.0,
        "cagr_ca": 4.1, "cagr_ebitda": 5.2,
        "fcf_conversion_available": False, "fcf_conversion": 0,
    }
    out = indices_score.build_ratios_summary(data, cost_of_capital=7.5)
    assert "ROCE indisponible" in out
    assert "dette nette/EBITDA indisponible" in out
    assert "ICR indisponible" in out
    assert "conversion FCF/EBITDA indisponible" in out


def test_build_ratios_summary_formats_the_financial_sector_profile():
    data = {
        "is_financial": True, "is_trust": False,
        "roe": 9.0, "leverage_ratio": 6.5,
        "cagr_ca": 3.0, "cagr_net_income": 4.0, "cash_conversion": 90,
    }
    out = indices_score.build_ratios_summary(data, cost_of_capital=7.0)
    assert "Profil financier" in out
    assert "ROE 9.0%" in out
    assert "capitaux propres/actif total 6.5%" in out


def test_build_ratios_summary_formats_the_trust_profile():
    data = {
        "is_financial": False, "is_trust": True,
        "roe": 5.0, "leverage_ratio": 50.0,
        "cagr_ca": 1.0, "cagr_net_income": 2.0, "current_pb": 0.85,
    }
    out = indices_score.build_ratios_summary(data, cost_of_capital=6.0)
    assert "Profil trust d'investissement" in out
    assert "0.85x" in out


def test_build_ratios_summary_marks_unavailable_trust_pb():
    data = {
        "is_financial": False, "is_trust": True,
        "roe": 5.0, "leverage_ratio": 50.0,
        "cagr_ca": 1.0, "cagr_net_income": 2.0, "current_pb": None,
    }
    out = indices_score.build_ratios_summary(data, cost_of_capital=6.0)
    assert "indisponible" in out
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_indices_score.py -k build_ratios_summary -v`
Expected: FAIL avec `AttributeError: module 'indices_score' has no
attribute 'build_ratios_summary'`

- [ ] **Step 3: Extraire la fonction**

Juste avant `def build_company_entry(` (ligne ~6055), ajouter :

```python
def build_ratios_summary(data: dict, cost_of_capital: float) -> str:
    """Résumé textuel des ratios déjà calculés, injecté dans le prompt
    de generate_financial_analysis (« ne pas les recalculer, les
    interpréter »). Extrait tel quel de build_company_entry (audit
    2026-10-30) pour être réutilisable par le mode --generate-one sans
    dupliquer la logique."""
    if data["is_financial"]:
        # Profil financier (voir FINANCIAL_SECTOR_TICKERS) : ROE/ratio
        # de levier/CAGR résultat net plutôt que ROCE/dette nette-
        # EBITDA/ICR/CAGR EBITDA, absents pour ces entreprises.
        return (
            f"[Profil financier — méthodologie adaptée, EBITDA/EBIT non "
            f"disponibles pour cette entreprise] "
            f"ROE {data['roe']:.1f}%, capitaux propres/actif total "
            f"{data['leverage_ratio']:.1f}%, CAGR CA {data['cagr_ca']:+.1f}%/an, "
            f"CAGR résultat net {data['cagr_net_income']:+.1f}%/an, "
            f"conversion cash (OCF/résultat net) {data['cash_conversion']:.0f}%, "
            f"coût du capital {cost_of_capital:.1f}%"
        )
    if data["is_trust"]:
        # Profil trust d'investissement (voir TRUST_TICKERS) : décote/
        # prime sur NAV (proxy P/B) plutôt que ROCE/dette nette-EBITDA,
        # pas de notion de conversion de cash d'exploitation.
        pb_text = f"{data['current_pb']:.2f}x" if data["current_pb"] else "indisponible"
        return (
            f"[Profil trust d'investissement — méthodologie adaptée, "
            f"EBITDA/EBIT non disponibles, pas de cycle d'exploitation "
            f"classique] ROE {data['roe']:.1f}%, capitaux propres/actif "
            f"total {data['leverage_ratio']:.1f}%, CAGR CA "
            f"{data['cagr_ca']:+.1f}%/an, CAGR résultat net "
            f"{data['cagr_net_income']:+.1f}%/an, P/B (proxy décote/prime "
            f"sur NAV) {pb_text}, coût du capital {cost_of_capital:.1f}%"
        )
    # "indisponible" plutôt que le chiffre de repli (audit 2026-09-13,
    # même motif que les FactorResult ci-dessous) : sans ça, Claude
    # recevrait un faux "ROCE 0.0%"/"ICR 10.0x" et pourrait écrire une
    # analyse contredisant le facteur affiché juste à côté (qui dit
    # maintenant explicitement "Donnée indisponible").
    roce_text = f"{data['roce']:.1f}%" if data["roce_available"] else "indisponible"
    structure_available = data["structure_available"]
    net_debt_ebitda_text = f"{data['net_debt_ebitda']:.1f}x" if structure_available else "indisponible"
    icr_text = f"{data['icr']:.1f}x" if structure_available else "indisponible"
    fcf_conversion_text = f"{data['fcf_conversion']:.0f}%" if data["fcf_conversion_available"] else "indisponible"
    return (
        f"ROCE {roce_text}, ROE {data['roe']:.1f}%, "
        f"dette nette/EBITDA {net_debt_ebitda_text}, "
        f"ICR {icr_text}, CAGR CA {data['cagr_ca']:+.1f}%/an, "
        f"CAGR EBITDA {data['cagr_ebitda']:+.1f}%/an, "
        f"conversion FCF/EBITDA {fcf_conversion_text}, "
        f"coût du capital {cost_of_capital:.1f}%"
    )
```

Puis dans `build_company_entry`, remplacer les lignes 6107-6152 (les
trois branches inline qui calculent `ratios_summary`) par :

```python
        ratios_summary = build_ratios_summary(data, cost_of_capital)
```

(Le `if/else` englobant à la ligne 6099/6106 — qui décide de
régénérer ou pas — reste inchangé à ce stade ; il sera retiré à la
Task 2.)

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/test_indices_score.py -k build_ratios_summary -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Lancer la suite complète (régression) et commit**

Run: `python -m pytest -q`
Expected: tous les tests passent (aucun changement de comportement
pour `build_company_entry`, juste une extraction).

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "refactor(indices): extrait build_ratios_summary de build_company_entry"
```

---

### Task 2: Retirer l'appel proactif à `generate_financial_analysis`

**Files:**
- Modify: `indices_score.py:6093-6166` (bloc de décision trimestre
  dans `build_company_entry`), `indices_score.py:6271-6272` (dict
  `entry`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Produces: nouveau champ `entry["latest_quarter_date"]` (toujours le
  trimestre réellement observé ce run, même si l'analyse n'est pas
  régénérée) — consommé par le frontend (Task 7) et par le mode
  `--generate-one` (Task 5, pour éviter une double lecture).
- `entry["financial_analysis_quarter"]` change de sens : il ne
  représente plus "le trimestre du dernier run" mais "le trimestre que
  décrit le texte actuellement en cache" (peut être périmé).

- [ ] **Step 1: Écrire le test de régression**

```python
def test_build_company_entry_exposes_latest_quarter_date_separately_from_cached_analysis(monkeypatch):
    appels = []
    monkeypatch.setattr(indices_score, "generate_financial_analysis",
                         lambda *a, **k: appels.append(a) or "<p>ne devrait jamais arriver</p>")
    ratios = _fake_ratios()
    ratios["latest_quarter_date"] = "2026-09-30"  # different de previous_analyses ci-dessous (2026-06-30)
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: ratios)
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])

    entry = indices_score.build_company_entry(
        "BN.PA", "Danone", risk_free_rate=3.68,
        previous_analyses={"BN.PA": {
            "financial_analysis_html": "<p>ancienne analyse</p>",
            "financial_analysis_quarter": "2026-06-30",
        }},
    )

    assert appels == []
    assert entry["financial_analysis_html"] == "<p>ancienne analyse</p>"
    assert entry["financial_analysis_quarter"] == "2026-06-30"  # perime, mais conserve tel quel
    assert entry["latest_quarter_date"] == "2026-09-30"  # toujours a jour, independamment du texte
```

Réutilise `_fake_ratios()`/le style des tests existants juste au-dessus
dans ce fichier (`test_build_company_entry_carries_forward_analysis_when_quarter_unchanged`,
ligne 5386) — ne pas inventer une nouvelle fixture.

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python -m pytest tests/test_indices_score.py -k exposes_latest_quarter_date_separately -v`
Expected: FAIL (`entry["latest_quarter_date"]` lève `KeyError`,
`entry["financial_analysis_html"]` vaut encore
`"<p>Nouvelle analyse.</p>"` et non l'ancienne, le champ est encore
régénéré aujourd'hui quand le trimestre change)

- [ ] **Step 3: Simplifier le bloc de décision**

Remplacer les lignes 6093-6166 (tout le bloc qui va de
`previous = previous_analyses.get(ticker, {})` jusqu'à la fin du
`else:` qui appelait `generate_financial_analysis`) par :

```python
    previous = previous_analyses.get(ticker, {})
    current_quarter = data["latest_quarter_date"]
    # generate_financial_analysis n'est plus jamais appelée ici (audit
    # 2026-10-30, voir docs/superpowers/specs/2026-10-30-financial-analysis-on-demand-design.md) :
    # financial_analysis_html n'alimente ni le score (les factors
    # ci-dessous sont calculés indépendamment) ni les réponses de
    # l'assistant IA (déjà exclu de fiche_entreprise/comparer_entreprises
    # côté assistant_ia/tools.py) -- son seul lecteur réel est l'affichage
    # humain de la fiche détaillée du site. On garde donc simplement
    # l'ancien texte tel quel (potentiellement périmé par rapport à
    # current_quarter) ; seule la route à la demande (indices_score.py
    # --generate-one, voir le mode CLI plus bas) le régénère, pour le
    # ticker précis qu'un visiteur consulte.
    financial_analysis_html = previous.get("financial_analysis_html")
    financial_analysis_quarter = previous.get("financial_analysis_quarter")
```

- [ ] **Step 4: Exposer `latest_quarter_date` dans l'entrée**

Ligne ~6271-6272 (dict `entry`, juste après
`"financial_analysis_quarter": financial_analysis_quarter,`), ajouter :

```python
        "latest_quarter_date": current_quarter,
```

- [ ] **Step 5: Lancer le test pour vérifier qu'il passe**

Run: `python -m pytest tests/test_indices_score.py -k exposes_latest_quarter_date_separately -v`
Expected: PASS

- [ ] **Step 6: Supprimer les deux tests devenus faux et lancer la suite complète**

Deux tests existants supposent que `build_company_entry` régénère (ou
tente de régénérer) l'analyse au changement de trimestre — ce
comportement n'existe plus du tout après ce Step, les supprimer
intégralement (pas les adapter, un test affaibli serait trompeur) :

- `test_build_company_entry_regenerates_analysis_when_quarter_changed`
  (ligne ~5407 avant ce changement)
- `test_build_company_entry_keeps_previous_analysis_when_generation_fails`
  (ligne ~5451 avant ce changement)

Les deux autres tests voisins (`test_build_company_entry_carries_forward_analysis_when_quarter_unchanged`,
`test_build_company_entry_carries_forward_when_latest_quarter_date_is_none`)
restent valides tels quels — ne pas y toucher.

Run: `python -m pytest -q`

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): build_company_entry ne regenere plus financial_analysis_html"
```

---

### Task 3: Plafonds $/semaine et $/mois

**Files:**
- Modify: `indices_score.py:4130-4180` (bloc des constantes/fonctions
  de plafond, juste après `INDICES_AI_PRICE_PER_MTOK_USD`)
- Test: `tests/test_indices_score.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `_indices_ai_any_budget_exceeded(daily_path=..., weekly_path=..., monthly_path=...) -> bool`
  et `_record_all_indices_ai_usage(model, input_tokens, output_tokens, daily_path=..., weekly_path=..., monthly_path=...) -> None`
  — remplacent, aux 4 sites d'appel existants (Task 4), les anciens
  `_indices_ai_budget_exceeded()`/`_record_indices_ai_usage(...)` sans
  changer leur comportement individuel (toujours présents, inchangés).

- [ ] **Step 1: Écrire les tests des nouvelles fonctions**

```python
def test_indices_ai_weekly_budget_exceeded_is_true_at_or_above_the_cap(tmp_path):
    path = str(tmp_path / "indices_ai_usage_week.json")
    # 2 appels Opus a 1M tokens de sortie chacun = 40$, au-dessus du
    # plafond de 30$ (indices_score.INDICES_AI_WEEKLY_BUDGET_USD).
    for _ in range(2):
        indices_score._record_indices_ai_usage_period(
            "claude-opus-5-5", 0, 1_000_000, path, indices_score._week_key())

    assert indices_score._indices_ai_weekly_budget_exceeded(path=path) is True


def test_indices_ai_weekly_budget_exceeded_is_false_under_the_cap(tmp_path):
    path = str(tmp_path / "indices_ai_usage_week.json")
    indices_score._record_indices_ai_usage_period(
        "claude-haiku-5-5", 1_000_000, 0, path, indices_score._week_key())

    assert indices_score._indices_ai_weekly_budget_exceeded(path=path) is False


def test_indices_ai_monthly_budget_exceeded_is_true_at_or_above_the_cap(tmp_path):
    path = str(tmp_path / "indices_ai_usage_month.json")
    for _ in range(4):
        indices_score._record_indices_ai_usage_period(
            "claude-opus-5-5", 0, 1_000_000, path, indices_score._month_key())

    assert indices_score._indices_ai_monthly_budget_exceeded(path=path) is True


def test_load_indices_ai_usage_period_resets_on_a_new_period_key(tmp_path):
    import json as _json
    path = str(tmp_path / "indices_ai_usage_week.json")
    with open(path, "w", encoding="utf-8") as fh:
        _json.dump({"period_key": "2026-W01", "total_usd": 99.0}, fh)

    assert indices_score._load_indices_ai_usage_period(path, "2026-W02")["total_usd"] == 0.0


def test_week_key_follows_the_iso_calendar():
    import datetime as _dt

    class _FauxDate(_dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 1, 1)  # ISO : semaine 1 de 2026 (le 2026-01-01 est un jeudi)

    original = indices_score.date
    indices_score.date = _FauxDate
    try:
        assert indices_score._week_key() == "2026-W01"
    finally:
        indices_score.date = original


def test_month_key_formats_year_and_month():
    import datetime as _dt

    class _FauxDate(_dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 3, 15)

    original = indices_score.date
    indices_score.date = _FauxDate
    try:
        assert indices_score._month_key() == "2026-03"
    finally:
        indices_score.date = original


def test_record_all_indices_ai_usage_updates_all_three_windows(tmp_path):
    daily = str(tmp_path / "daily.json")
    weekly = str(tmp_path / "weekly.json")
    monthly = str(tmp_path / "monthly.json")

    indices_score._record_all_indices_ai_usage(
        "claude-opus-5-5", 1_000_000, 0,
        daily_path=daily, weekly_path=weekly, monthly_path=monthly)

    assert indices_score._load_indices_ai_usage(path=daily)["total_usd"] == 4.0
    assert indices_score._load_indices_ai_usage_period(weekly, indices_score._week_key())["total_usd"] == 4.0
    assert indices_score._load_indices_ai_usage_period(monthly, indices_score._month_key())["total_usd"] == 4.0


def test_indices_ai_any_budget_exceeded_checks_all_three_windows(tmp_path):
    daily = str(tmp_path / "daily.json")
    weekly = str(tmp_path / "weekly.json")
    monthly = str(tmp_path / "monthly.json")
    # Seul le plafond semaine est dépassé ici.
    for _ in range(2):
        indices_score._record_indices_ai_usage_period(
            "claude-opus-5-5", 0, 1_000_000, weekly, indices_score._week_key())

    assert indices_score._indices_ai_any_budget_exceeded(
        daily_path=daily, weekly_path=weekly, monthly_path=monthly) is True
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_indices_score.py -k "weekly_budget or monthly_budget or period_resets or week_key or month_key or record_all_indices_ai_usage or any_budget_exceeded" -v`
Expected: FAIL (fonctions inexistantes)

- [ ] **Step 3: Implémenter**

Juste après le bloc existant (après la fonction
`_indices_ai_budget_exceeded`, ligne ~4180), ajouter :

```python
# Plafonds $/semaine et $/mois (2026-10-30, voir
# docs/superpowers/specs/2026-10-30-financial-analysis-on-demand-design.md) :
# le plafond jour seul ne protege pas contre une accumulation etalee sur
# plusieurs jours (ex. la generation a la demande d'un grand nombre de
# tickers distincts sur quelques jours) -- ces deux plafonds s'ajoutent
# au plafond jour, pas a sa place. Volontairement inferieurs a 7x/30x le
# plafond jour : la demande reelle attendue (quelques tickers par jour)
# est bien plus basse que le plafond jour lui-meme.
INDICES_AI_WEEKLY_BUDGET_USD = 30.0
INDICES_AI_MONTHLY_BUDGET_USD = 75.0
INDICES_AI_WEEKLY_USAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "indices_ai_usage_week.json")
INDICES_AI_MONTHLY_USAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "indices_ai_usage_month.json")


def _week_key() -> str:
    iso_year, iso_week, _ = date.today().isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _month_key() -> str:
    return date.today().strftime("%Y-%m")


def _load_indices_ai_usage_period(path: str, period_key: str) -> dict:
    """Generalisation de _load_indices_ai_usage a une cle de periode
    arbitraire (semaine ISO ou mois) -- meme contrat (jamais
    d'exception, degrade a zero sur fichier absent/corrompu/perime)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {"period_key": period_key, "total_usd": 0.0}
    if not isinstance(data, dict) or data.get("period_key") != period_key:
        return {"period_key": period_key, "total_usd": 0.0}
    total = data.get("total_usd")
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        return {"period_key": period_key, "total_usd": 0.0}
    return {"period_key": period_key, "total_usd": float(total)}


def _record_indices_ai_usage_period(
    model: str, input_tokens: int, output_tokens: int, path: str, period_key: str,
) -> None:
    prices = INDICES_AI_PRICE_PER_MTOK_USD.get(model)
    if prices is None:
        return
    cout = (input_tokens / 1_000_000) * prices["input"] + (output_tokens / 1_000_000) * prices["output"]
    nouveau_total = _load_indices_ai_usage_period(path, period_key)["total_usd"] + cout
    try:
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump({"period_key": period_key, "total_usd": nouveau_total}, fh)
        os.replace(tmp_path, path)
    except Exception:
        pass


def _indices_ai_weekly_budget_exceeded(path: str = INDICES_AI_WEEKLY_USAGE_PATH) -> bool:
    return _load_indices_ai_usage_period(path, _week_key())["total_usd"] >= INDICES_AI_WEEKLY_BUDGET_USD


def _indices_ai_monthly_budget_exceeded(path: str = INDICES_AI_MONTHLY_USAGE_PATH) -> bool:
    return _load_indices_ai_usage_period(path, _month_key())["total_usd"] >= INDICES_AI_MONTHLY_BUDGET_USD


def _indices_ai_any_budget_exceeded(
    daily_path: str = INDICES_AI_USAGE_PATH,
    weekly_path: str = INDICES_AI_WEEKLY_USAGE_PATH,
    monthly_path: str = INDICES_AI_MONTHLY_USAGE_PATH,
) -> bool:
    """Point d'entree unique pour les 4 sites d'appel (summarize_news_item/
    generate_financial_analysis ici, summarize_commodity_news_item/
    generate_commodity_analysis dans commodity_news.py) : aucun des trois
    plafonds ne peut etre depasse sans que l'appel soit bloque."""
    return (
        _indices_ai_budget_exceeded(path=daily_path)
        or _indices_ai_weekly_budget_exceeded(path=weekly_path)
        or _indices_ai_monthly_budget_exceeded(path=monthly_path)
    )


def _record_all_indices_ai_usage(
    model: str, input_tokens: int, output_tokens: int,
    daily_path: str = INDICES_AI_USAGE_PATH,
    weekly_path: str = INDICES_AI_WEEKLY_USAGE_PATH,
    monthly_path: str = INDICES_AI_MONTHLY_USAGE_PATH,
) -> None:
    """Enregistre le cout d'un appel sur les trois fenetres a la fois --
    point d'entree unique symmetrique a _indices_ai_any_budget_exceeded."""
    _record_indices_ai_usage(model, input_tokens, output_tokens, path=daily_path)
    _record_indices_ai_usage_period(model, input_tokens, output_tokens, weekly_path, _week_key())
    _record_indices_ai_usage_period(model, input_tokens, output_tokens, monthly_path, _month_key())
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/test_indices_score.py -k "weekly_budget or monthly_budget or period_resets or week_key or month_key or record_all_indices_ai_usage or any_budget_exceeded" -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Mettre à jour `.gitignore`**

Ajouter, à côté de `indices_ai_usage_today.json` :

```
indices_ai_usage_week.json
indices_ai_usage_month.json
```

- [ ] **Step 6: Suite complète et commit**

Run: `python -m pytest -q`

```bash
git add indices_score.py tests/test_indices_score.py .gitignore
git commit -m "feat(indices): plafonds de depense IA semaine/mois en plus du plafond jour"
```

---

### Task 4: Cooldown par ticker + branchement sur les 4 sites d'appel

**Files:**
- Modify: `indices_score.py:4180` (après le bloc de la Task 3),
  `indices_score.py:4198-4199`, `4265`, `4299-4310`, `4357`
  (summarize_news_item, generate_financial_analysis)
- Modify: `commodity_news.py:26-40`, `65`, `131`, `210`, `254`
  (summarize_commodity_news_item, generate_commodity_analysis)
- Test: `tests/test_indices_score.py`, `tests/test_commodity_news.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `_ticker_cooldown_active(ticker, path=...) -> bool`,
  `_record_ticker_generated(ticker, path=...) -> None`.
- Modifies: `generate_financial_analysis(company_name, financial_context, ratios_summary, ticker=None)`
  — nouveau paramètre optionnel ; `ticker=None` désactive le cooldown
  (comportement des 7 tests existants qui appellent la fonction avec 3
  arguments, inchangé). Le mode `--generate-one` (Task 5) est le seul
  appelant à passer `ticker`.

- [ ] **Step 1: Écrire les tests du cooldown et du branchement**

```python
def test_ticker_cooldown_is_not_active_when_the_file_is_absent(tmp_path):
    path = str(tmp_path / "cooldown.json")

    assert indices_score._ticker_cooldown_active("AAA", path=path) is False


def test_ticker_cooldown_is_active_right_after_recording(tmp_path):
    path = str(tmp_path / "cooldown.json")
    indices_score._record_ticker_generated("AAA", path=path)

    assert indices_score._ticker_cooldown_active("AAA", path=path) is True


def test_ticker_cooldown_does_not_affect_other_tickers(tmp_path):
    path = str(tmp_path / "cooldown.json")
    indices_score._record_ticker_generated("AAA", path=path)

    assert indices_score._ticker_cooldown_active("BBB", path=path) is False


def test_ticker_cooldown_degrades_to_false_on_corrupt_file(tmp_path):
    path = str(tmp_path / "cooldown.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")

    assert indices_score._ticker_cooldown_active("AAA", path=path) is False


def test_generate_financial_analysis_skips_the_call_when_ticker_cooldown_is_active(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(indices_score, "_ticker_cooldown_active", lambda ticker, **k: ticker == "AAA")

    def fail_if_called(*a, **k):
        raise AssertionError("no network call expected when the ticker cooldown is active")

    monkeypatch.setattr(indices_score.requests, "post", fail_if_called)
    result = indices_score.generate_financial_analysis("Nom", "contexte", "ratios", ticker="AAA")

    assert result is None


def test_generate_financial_analysis_ignores_cooldown_when_no_ticker_given(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(indices_score, "_ticker_cooldown_active", lambda ticker, **k: True)

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"type": "text", "text": '{"analysis_html": "<p>OK</p>"}'}]}

    monkeypatch.setattr(indices_score.requests, "post", lambda *a, **k: _FakeResponse())
    result = indices_score.generate_financial_analysis("Nom", "contexte", "ratios")

    assert result == "<p>OK</p>"
```

(Style identique aux tests existants de `generate_financial_analysis`
juste au-dessus dans ce fichier, ligne ~5249 : un petit `_FakeResponse`
inline monkeypatché sur `indices_score.requests.post` — ce fichier
n'utilise pas `unittest.mock.patch`.)

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_indices_score.py -k ticker_cooldown -v`
Expected: FAIL (fonctions/paramètre inexistants)

- [ ] **Step 3: Implémenter le cooldown dans `indices_score.py`**

Après le bloc de la Task 3 (après `_record_all_indices_ai_usage`),
ajouter :

```python
# Frequence max par ticker (2026-10-30, meme design) : evite qu'un pic
# de trafic sur UNE seule entreprise consomme le plafond $ a lui seul,
# meme si le trimestre a re-change entre-temps.
INDICES_AI_TICKER_COOLDOWN_HOURS = 24
INDICES_AI_TICKER_COOLDOWN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "indices_ai_ticker_cooldown.json")


def _ticker_cooldown_active(ticker: str, path: str = INDICES_AI_TICKER_COOLDOWN_PATH) -> bool:
    """True si ce ticker a deja ete regenere il y a moins de
    INDICES_AI_TICKER_COOLDOWN_HOURS. Fichier absent/corrompu/entree
    absente -> False (jamais de blocage par defaut), jamais d'exception."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    horodatage = data.get(ticker)
    if not isinstance(horodatage, str):
        return False
    try:
        dernier = datetime.fromisoformat(horodatage)
    except ValueError:
        return False
    return datetime.now() - dernier < timedelta(hours=INDICES_AI_TICKER_COOLDOWN_HOURS)


def _record_ticker_generated(ticker: str, path: str = INDICES_AI_TICKER_COOLDOWN_PATH) -> None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data[ticker] = datetime.now().isoformat()
    try:
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp_path, path)
    except Exception:
        pass
```

- [ ] **Step 4: Mettre à jour les deux autouse fixtures de test AVANT de brancher les sites d'appel**

Fait exprès avant les Steps 5-7 ci-dessous : si les sites d'appel sont
rebranchés sur `_indices_ai_any_budget_exceeded`/
`_record_all_indices_ai_usage` avant que l'autouse fixture ne les
patch, toute exécution de test entre les deux écrirait réellement dans
`indices_ai_usage_today.json`/`_week.json`/`_month.json` du dépôt.

`tests/test_indices_score.py`, dans `_no_real_indices_ai_usage_writes`,
ajouter les deux nouvelles lignes (garder les deux existantes) :

```python
    monkeypatch.setattr(indices_score, "_record_indices_ai_usage", lambda *a, **k: None)
    monkeypatch.setattr(indices_score, "_indices_ai_budget_exceeded", lambda *a, **k: False)
    monkeypatch.setattr(indices_score, "_record_all_indices_ai_usage", lambda *a, **k: None)
    monkeypatch.setattr(indices_score, "_indices_ai_any_budget_exceeded", lambda *a, **k: False)
```

`tests/test_commodity_news.py`, dans `_no_real_indices_ai_usage_writes`,
remplacer :

```python
    monkeypatch.setattr(commodity_news, "_record_indices_ai_usage", lambda *a, **k: None)
    monkeypatch.setattr(commodity_news, "_indices_ai_budget_exceeded", lambda *a, **k: False)
```

par :

```python
    monkeypatch.setattr(commodity_news, "_record_all_indices_ai_usage", lambda *a, **k: None)
    monkeypatch.setattr(commodity_news, "_indices_ai_any_budget_exceeded", lambda *a, **k: False)
```

(Les deux tests existants `test_summarize_commodity_news_item_skips_the_call_when_budget_is_exceeded`
et `test_generate_commodity_analysis_skips_the_call_when_budget_is_exceeded`
monkeypatchent déjà `_indices_ai_budget_exceeded` directement sur
`commodity_news` pour forcer le cas dépassé — les renommer en
`_indices_ai_any_budget_exceeded` au même endroit, dans ces deux tests
eux-mêmes.)

Run: `python -m pytest tests/test_indices_score.py tests/test_commodity_news.py -q`
Expected: PASS (ce changement seul ne modifie aucun comportement, les
nouvelles fonctions patchées ne sont encore appelées par personne).

```bash
git add tests/test_indices_score.py tests/test_commodity_news.py
git commit -m "test(indices): protege les futurs sites d'appel des plafonds semaine/mois/cooldown"
```

- [ ] **Step 5: Brancher le cooldown et le paramètre `ticker` sur `generate_financial_analysis`**

Ligne ~4299-4310, remplacer :

```python
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
    if _indices_ai_budget_exceeded():
        return None
```

par :

```python
def generate_financial_analysis(
    company_name: str, financial_context: str, ratios_summary: str,
    ticker: str | None = None,
) -> str | None:
    """Génère l'analyse financière via Claude Opus 5.5 (thinking
    adaptatif — tâche de raisonnement/rédaction, pas de classification
    simple). None si la clé API est absente ou en cas d'échec — jamais
    d'exception, même contrat que summarize_news_item.

    `ticker` (optionnel, 2026-10-30) : active le cooldown par ticker en
    plus des plafonds $ -- None desactive cette verification (les
    appelants existants qui ne passent que 3 arguments ne sont pas
    affectes)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    if _indices_ai_any_budget_exceeded():
        return None
    if ticker is not None and _ticker_cooldown_active(ticker):
        return None
```

Puis ligne ~4357 (l'enregistrement d'usage dans cette même fonction),
remplacer :

```python
        _record_indices_ai_usage(ANTHROPIC_MODEL_ANALYSIS, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
```

par :

```python
        _record_all_indices_ai_usage(ANTHROPIC_MODEL_ANALYSIS, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        if ticker is not None:
            _record_ticker_generated(ticker)
```

- [ ] **Step 6: Brancher `_indices_ai_any_budget_exceeded`/`_record_all_indices_ai_usage` sur `summarize_news_item`**

Ligne ~4198-4199 : remplacer `if _indices_ai_budget_exceeded():` par
`if _indices_ai_any_budget_exceeded():`. Ligne ~4265 : remplacer
`_record_indices_ai_usage(ANTHROPIC_MODEL, ...)` par
`_record_all_indices_ai_usage(ANTHROPIC_MODEL, ...)` (mêmes arguments).

- [ ] **Step 7: Mettre à jour l'import et les 2 sites d'appel dans `commodity_news.py`**

Lignes 26-40, remplacer le bloc d'import :

```python
from indices_score import (  # noqa: F401 — NEWS_MAX_ITEMS réexporté, utilisé indirectement via parse_news_rss
    NEWS_RSS_URL,
    NEWS_MAX_ITEMS,
    parse_news_rss,
    fetch_article_text,
    ANTHROPIC_API_URL,
    NEWS_IMPORTANCE_LEVELS,
    # Meme plafond de depense quotidien que indices_score.py (pas un
    # compteur separe) : les deux pipelines tirent sur le meme compte
    # Anthropic reel -- voir le commentaire au-dessus de
    # INDICES_AI_DAILY_BUDGET_USD dans indices_score.py pour le contexte
    # complet (epuisement de credit du 2026-10-30).
    _indices_ai_budget_exceeded,
    _record_indices_ai_usage,
)
```

par :

```python
from indices_score import (  # noqa: F401 — NEWS_MAX_ITEMS réexporté, utilisé indirectement via parse_news_rss
    NEWS_RSS_URL,
    NEWS_MAX_ITEMS,
    parse_news_rss,
    fetch_article_text,
    ANTHROPIC_API_URL,
    NEWS_IMPORTANCE_LEVELS,
    # Memes plafonds (jour + semaine + mois, voir indices_score.py) que
    # indices_score.py -- pas des compteurs separes : les deux pipelines
    # tirent sur le meme compte Anthropic reel.
    _indices_ai_any_budget_exceeded,
    _record_all_indices_ai_usage,
)
```

Ligne ~65 (`summarize_commodity_news_item`) : remplacer
`if _indices_ai_budget_exceeded():` par `if _indices_ai_any_budget_exceeded():`.
Ligne ~131 : remplacer `_record_indices_ai_usage(ANTHROPIC_MODEL_NEWS, ...)`
par `_record_all_indices_ai_usage(ANTHROPIC_MODEL_NEWS, ...)`.
Ligne ~210 (`generate_commodity_analysis`) : remplacer
`if _indices_ai_budget_exceeded():` par `if _indices_ai_any_budget_exceeded():`.
Ligne ~254 : remplacer `_record_indices_ai_usage(ANTHROPIC_MODEL_ANALYSIS, ...)`
par `_record_all_indices_ai_usage(ANTHROPIC_MODEL_ANALYSIS, ...)`.

- [ ] **Step 8: Lancer tous les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/test_indices_score.py tests/test_commodity_news.py -v`
Expected: PASS (tous, y compris les nouveaux)

- [ ] **Step 9: Ajouter `indices_ai_ticker_cooldown.json` au `.gitignore`, suite complète, commit**

```bash
git add indices_score.py commodity_news.py tests/test_indices_score.py tests/test_commodity_news.py .gitignore
```

Run: `python -m pytest -q`

```bash
git commit -m "feat(indices): cooldown par ticker + plafonds semaine/mois branches sur les 4 sites d'appel"
```

---

### Task 5: Mode CLI `indices_score.py --generate-one <ticker>`

**Files:**
- Modify: `indices_score.py` (imports en tête, nouvelle fonction,
  `if __name__ == "__main__":`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Produces: `generate_one(ticker: str) -> dict` (retourne
  `{"html": str | None, "genere": bool}`), appelée par le bloc
  `if __name__ == "__main__":` quand `--generate-one` est passé, et
  testée directement. C'est cette fonction que la route FastAPI (Task
  6) déclenche via `python3 indices_score.py --generate-one <ticker>`,
  dont le stdout est le JSON de ce même dict.

- [ ] **Step 1: Écrire les tests de `generate_one`**

```python
def test_generate_one_returns_cached_html_without_calling_opus_when_quarter_unchanged(tmp_path, monkeypatch):
    indices_json = tmp_path / "indices.json"
    indices_json.write_text(json.dumps({"companies": [
        {"ticker": "AAA", "name": "Entreprise Test", "index": "CAC40",
         "financial_analysis_html": "<p>deja a jour</p>",
         "financial_analysis_quarter": "2026-09-30"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(indices_json))
    monkeypatch.setattr(indices_score, "fetch_company_financials",
                         lambda ticker: {**_fake_ratios(), "latest_quarter_date": "2026-09-30"})
    appels_opus = []
    monkeypatch.setattr(indices_score, "generate_financial_analysis",
                         lambda *a, **k: appels_opus.append(a) or "<p>ne devrait jamais arriver</p>")

    result = indices_score.generate_one("AAA")

    assert appels_opus == []
    assert result == {"html": "<p>deja a jour</p>", "genere": False}


def test_generate_one_calls_opus_and_patches_the_file_when_quarter_changed(tmp_path, monkeypatch):
    indices_json = tmp_path / "indices.json"
    indices_json.write_text(json.dumps({"companies": [
        {"ticker": "AAA", "name": "Entreprise Test", "index": "CAC40",
         "financial_analysis_html": "<p>ancienne</p>",
         "financial_analysis_quarter": "2026-06-30"},
        {"ticker": "BBB", "name": "Autre Entreprise", "index": "CAC40",
         "financial_analysis_html": "<p>ne doit pas changer</p>",
         "financial_analysis_quarter": "2026-06-30"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(indices_json))
    monkeypatch.setattr(indices_score, "fetch_company_financials",
                         lambda ticker: {**_fake_ratios(), "latest_quarter_date": "2026-09-30"})
    monkeypatch.setattr(indices_score, "generate_financial_analysis",
                         lambda *a, **k: "<p>nouvelle analyse</p>")
    monkeypatch.setattr(indices_score, "_git_commit_and_push", lambda *a, **k: None)
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda *a, **k: 2.5)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda *a, **k: 1.0)

    result = indices_score.generate_one("AAA")

    assert result == {"html": "<p>nouvelle analyse</p>", "genere": True}
    patched = json.loads(indices_json.read_text(encoding="utf-8"))
    aaa = next(c for c in patched["companies"] if c["ticker"] == "AAA")
    bbb = next(c for c in patched["companies"] if c["ticker"] == "BBB")
    assert aaa["financial_analysis_html"] == "<p>nouvelle analyse</p>"
    assert aaa["financial_analysis_quarter"] == "2026-09-30"
    assert bbb["financial_analysis_html"] == "<p>ne doit pas changer</p>"  # non touchée


def test_generate_one_returns_unavailable_for_an_unknown_ticker(tmp_path, monkeypatch):
    indices_json = tmp_path / "indices.json"
    indices_json.write_text(json.dumps({"companies": []}), encoding="utf-8")
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(indices_json))

    result = indices_score.generate_one("INCONNU")

    assert result == {"html": None, "genere": False}


def test_generate_one_keeps_old_html_when_opus_generation_fails(tmp_path, monkeypatch):
    indices_json = tmp_path / "indices.json"
    indices_json.write_text(json.dumps({"companies": [
        {"ticker": "AAA", "name": "Entreprise Test", "index": "CAC40",
         "financial_analysis_html": "<p>ancienne analyse valide</p>",
         "financial_analysis_quarter": "2026-06-30"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(indices_json))
    monkeypatch.setattr(indices_score, "fetch_company_financials",
                         lambda ticker: {**_fake_ratios(), "latest_quarter_date": "2026-09-30"})
    monkeypatch.setattr(indices_score, "generate_financial_analysis", lambda *a, **k: None)
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda *a, **k: 2.5)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda *a, **k: 1.0)

    result = indices_score.generate_one("AAA")

    assert result == {"html": "<p>ancienne analyse valide</p>", "genere": False}
```

Réutilise `_fake_ratios()` (déjà défini ligne 1998 de ce fichier,
fixture déjà consommée par les tests existants de
`build_company_entry`) — ne pas en inventer une nouvelle.

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_indices_score.py -k generate_one -v`
Expected: FAIL (`generate_one` inexistante)

- [ ] **Step 3: Ajouter les imports nécessaires en tête de fichier**

Ligne 18 (`import json`), ajouter avant, dans l'ordre alphabétique :

```python
import argparse
```

Ligne 21 (`import smtplib`), ajouter juste après :

```python
import subprocess
import sys
```

- [ ] **Step 4: Implémenter `_git_commit_and_push` et `generate_one`**

Juste avant `def main():` (ligne ~6567), ajouter :

```python
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def _git_commit_and_push(ticker: str) -> None:
    """Commit + push docs/indices.json depuis le repertoire du depot
    (indices_score.py est a sa racine). git pull --rebase --autostash
    d'abord : le run quotidien (indices.yml) peut avoir pousse entre le
    moment ou generate_one a lu le fichier et celui ou il le reecrit --
    meme precaution que la section "Publier" de indices.yml/daily.yml.
    N'importe quelle etape qui echoue leve CalledProcessError, remonte
    tel quel a l'appelant (generate_one ne catch pas cette exception :
    une regeneration non publiee doit etre visible dans les logs, pas
    silencieusement avalee)."""
    subprocess.run(["git", "add", "docs/indices.json"], cwd=_REPO_ROOT, check=True, timeout=30)
    resultat = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=_REPO_ROOT, timeout=30)
    if resultat.returncode == 0:
        return  # rien a committer (le contenu patche est identique a l'existant)
    subprocess.run(
        ["git", "commit", "-m", f"Analyse financiere a la demande : {ticker}"],
        cwd=_REPO_ROOT, check=True, timeout=30)
    subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=_REPO_ROOT, check=True, timeout=60)
    subprocess.run(["git", "push"], cwd=_REPO_ROOT, check=True, timeout=60)


def generate_one(ticker: str) -> dict:
    """Génère (ou renvoie le cache existant pour) financial_analysis_html
    pour UN SEUL ticker -- voir le mode CLI --generate-one, appelé par
    la route FastAPI de assistant_ia via un sous-processus (le module
    reste ainsi "lecture seule" pour ses propres besoins). Ne lève
    jamais d'exception liée au contenu (ticker inconnu, échec Opus) --
    toujours {"html": ..., "genere": bool} ; une erreur git réelle,
    elle, remonte (voir _git_commit_and_push)."""
    try:
        with open(OUTPUT_JSON_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return {"html": None, "genere": False}

    companies = payload.get("companies", [])
    entry = next((c for c in companies if c.get("ticker") == ticker), None)
    if entry is None:
        return {"html": None, "genere": False}

    data = fetch_company_financials(ticker)
    current_quarter = data["latest_quarter_date"]
    if current_quarter is not None and current_quarter == entry.get("financial_analysis_quarter") \
            and entry.get("financial_analysis_html"):
        return {"html": entry["financial_analysis_html"], "genere": False}

    currency = INDEX_CURRENCY.get(entry.get("index"), "EUR")
    fetched_rate = fetch_risk_free_rate(RISK_FREE_SERIES_BY_CURRENCY.get(currency, FRED_RISK_FREE_SERIES))
    risk_free_rate = fetched_rate if fetched_rate is not None else load_risk_free_rate_cache().get(currency)
    fx_rate_to_usd = fetch_fx_rate_to_usd(currency)

    market_cap = (
        data["current_price"] * data["shares_outstanding"]
        if data["current_price"] is not None else None
    )
    wacc = estimate_wacc(
        risk_free_rate, data["beta"], market_cap, data["total_debt"], data["tax_rate"],
        fx_rate_to_usd, data.get("implied_cost_of_debt"),
    )
    cost_of_capital = wacc if wacc is not None else COST_OF_CAPITAL_PROXY
    ratios_summary = build_ratios_summary(data, cost_of_capital)

    generated = generate_financial_analysis(entry["name"], data["financial_context"], ratios_summary, ticker=ticker)
    if generated is None:
        return {"html": entry.get("financial_analysis_html"), "genere": False}

    entry["financial_analysis_html"] = generated
    entry["financial_analysis_quarter"] = current_quarter
    entry["latest_quarter_date"] = current_quarter
    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, allow_nan=False)
    _git_commit_and_push(ticker)
    return {"html": generated, "genere": True}
```

- [ ] **Step 5: Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/test_indices_score.py -k generate_one -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Brancher le mode CLI**

Remplacer les lignes finales :

```python
if __name__ == "__main__":
    main()
```

par :

```python
if __name__ == "__main__":
    _parser = argparse.ArgumentParser()
    _parser.add_argument("--generate-one", metavar="TICKER")
    _args = _parser.parse_args()
    if _args.generate_one:
        print(json.dumps(generate_one(_args.generate_one)))
        sys.exit(0)
    main()
```

- [ ] **Step 7: Vérifier manuellement le mode CLI (sans toucher au vrai docs/indices.json du dépôt)**

Run : `cp docs/indices.json /tmp/indices_test.json && python3 -c "
import indices_score
indices_score.OUTPUT_JSON_PATH = '/tmp/indices_test.json'
import json
print(json.dumps(indices_score.generate_one('<un vrai ticker de COMPANIES>')))
"`

Expected : un JSON `{"html": ..., "genere": true|false}` cohérent avec
le contenu de `/tmp/indices_test.json` (ticker déjà à jour -> `genere:
false` sans toucher au fichier ; sinon, nécessite `ANTHROPIC_API_KEY`
dans l'environnement pour tester le chemin `genere: true` réel — à
faire consciemment, ça dépense un vrai appel Opus).

- [ ] **Step 8: Suite complète et commit**

Run: `python -m pytest -q`

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): mode CLI --generate-one pour la generation a la demande"
```

---

### Task 6: Route `GET /financial-analysis/{ticker}` (assistant_ia)

**Files:**
- Modify: `assistant_ia/api.py`
- Test: `tests/assistant_ia/test_api.py`

**Interfaces:**
- Consumes: `indices_score.py --generate-one <ticker>` (Task 5) via
  `subprocess.run`, en changeant de répertoire vers la racine du dépôt
  sur le VPS.
- Produces: `GET /financial-analysis/{ticker}` -> `{"html": str | None, "disponible": bool}`,
  code 200 dans tous les cas (jamais de 4xx/5xx côté visiteur pour un
  échec de génération — seul un ticker absent de l'URL serait un 404
  FastAPI standard, non applicable ici puisque `{ticker}` est une
  chaîne libre).

- [ ] **Step 1: Écrire les tests de la route**

```python
def test_financial_analysis_returns_the_subprocess_result(client, monkeypatch):
    monkeypatch.setattr(api, "_generate_one_subprocess",
                         lambda ticker: {"html": "<p>Analyse</p>", "genere": True})

    response = client.get("/financial-analysis/AAA")

    assert response.status_code == 200
    assert response.json() == {"html": "<p>Analyse</p>", "disponible": True}


def test_financial_analysis_reports_unavailable_when_nothing_was_generated(client, monkeypatch):
    monkeypatch.setattr(api, "_generate_one_subprocess",
                         lambda ticker: {"html": None, "genere": False})

    response = client.get("/financial-analysis/AAA")

    assert response.status_code == 200
    assert response.json() == {"html": None, "disponible": False}


def test_financial_analysis_reports_unavailable_on_subprocess_timeout(client, monkeypatch):
    import subprocess as sp

    def _leve_timeout(ticker):
        raise sp.TimeoutExpired(cmd="indices_score.py", timeout=150)

    monkeypatch.setattr(api, "_generate_one_subprocess", _leve_timeout)

    response = client.get("/financial-analysis/AAA")

    assert response.status_code == 200
    assert response.json() == {"html": None, "disponible": False}


def test_financial_analysis_reports_unavailable_on_malformed_subprocess_output(client, monkeypatch):
    def _sortie_invalide(ticker):
        raise ValueError("JSON invalide")

    monkeypatch.setattr(api, "_generate_one_subprocess", _sortie_invalide)

    response = client.get("/financial-analysis/AAA")

    assert response.status_code == 200
    assert response.json() == {"html": None, "disponible": False}


def test_financial_analysis_rate_limits_each_visitor(client, monkeypatch):
    monkeypatch.setattr(api, "_generate_one_subprocess",
                         lambda ticker: {"html": "<p>x</p>", "genere": False})
    monkeypatch.setattr(api, "_frequence_analyse", api.limits.RateLimiter(2, 60))

    codes = [client.get("/financial-analysis/AAA").status_code for _ in range(3)]

    assert codes == [200, 200, 429]


def test_financial_analysis_answers_503_when_a_generation_is_already_running(client, monkeypatch):
    monkeypatch.setattr(api, "_frequence_analyse", api.limits.RateLimiter(100, 60))
    monkeypatch.setattr(api, "_concurrence_analyse", api.limits.ConcurrencyLimiter(1))
    api._concurrence_analyse.tente()  # une génération déjà en cours occupe la seule place

    response = client.get("/financial-analysis/AAA")

    assert response.status_code == 503
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/assistant_ia/test_api.py -k financial_analysis -v`
Expected: FAIL (route inexistante)

- [ ] **Step 3: Implémenter**

Ligne ~26 (`app.add_middleware(CORSMiddleware, ...)`), ajouter `"GET"`
aux méthodes autorisées :

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://alexandreauq.github.io"],
    allow_methods=["POST", "GET"],
    allow_headers=["X-Bot-Token", "Content-Type"],
)
```

Après le bloc des garde-fous existants (`_concurrence`/`_frequence`,
ligne ~46-47), ajouter :

```python
# Garde-fous dédiés à /financial-analysis -- séparés de ceux de /ask
# (une conversation ouverte et un ticker ponctuel n'ont pas le même
# profil d'abus). Concurrence à 1 : évite toute course entre deux
# sous-processus qui committeraient en même temps (voir
# indices_score.py::_git_commit_and_push, qui fait déjà un pull --rebase
# avant de pousser, mais une seule génération à la fois reste plus sûr
# et plus simple qu'un retry).
ANALYSE_MAX_CONCURRENT = int(os.environ.get("AI_FINANCIAL_ANALYSIS_MAX_CONCURRENT", "1"))
ANALYSE_RATE_MAX_PAR_MINUTE = int(os.environ.get("AI_FINANCIAL_ANALYSIS_RATE_PAR_MINUTE", "10"))
ANALYSE_SUBPROCESS_TIMEOUT_S = 150  # generate_financial_analysis a son propre timeout HTTP a 120s

_concurrence_analyse = limits.ConcurrencyLimiter(ANALYSE_MAX_CONCURRENT)
_frequence_analyse = limits.RateLimiter(ANALYSE_RATE_MAX_PAR_MINUTE, 60)

_REPO_ROOT = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))


def _generate_one_subprocess(ticker: str) -> dict:
    """Délègue toute la génération + écriture + commit git à
    indices_score.py --generate-one, en sous-processus -- ce module
    reste ainsi "lecture seule" (voir Global Constraints du plan). Lève
    (jamais n'avale) sur sortie invalide/timeout : l'appelant (la route)
    décide du repli visiteur."""
    resultat = subprocess.run(
        ["python3", "indices_score.py", "--generate-one", ticker],
        cwd=_REPO_ROOT, capture_output=True, text=True,
        timeout=ANALYSE_SUBPROCESS_TIMEOUT_S,
    )
    return json.loads(resultat.stdout)


@app.get("/financial-analysis/{ticker}")
def financial_analysis(ticker: str):
    if not _frequence_analyse.autorise(ticker):
        raise HTTPException(status_code=429, detail="Trop de demandes pour ce ticker : réessaie dans une minute.")
    if not _concurrence_analyse.tente():
        raise HTTPException(status_code=503, detail="Une génération est déjà en cours : réessaie dans un instant.")
    try:
        try:
            resultat = _generate_one_subprocess(ticker)
            html = resultat.get("html")
            disponible = bool(resultat.get("html"))
        except Exception:
            html, disponible = None, False
    finally:
        _concurrence_analyse.libere()
    return {"html": html, "disponible": disponible}
```

Ajouter `import subprocess` et `import os` en tête du fichier si
absents (`os` est déjà importé ; ajouter `subprocess` juste après).

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/assistant_ia/test_api.py -v`
Expected: PASS (tous, y compris les existants de `/ask`, non affectés)

- [ ] **Step 5: Suite complète et commit**

Run: `python -m pytest -q`

```bash
git add assistant_ia/api.py tests/assistant_ia/test_api.py
git commit -m "feat(assistant_ia): route GET /financial-analysis/<ticker> a la demande"
```

---

### Task 7: Frontend — chargement à la demande

**Files:**
- Modify: `docs/index.html` (fonction `renderCompanyAnalysis`, ligne
  ~6510)

**Interfaces:**
- Consumes: `company.latest_quarter_date` (Task 2),
  `company.financial_analysis_quarter`/`financial_analysis_html`
  (inchangés), `GET https://goldbot.fr:8445/financial-analysis/<ticker>`
  (Task 6).

Pas de test automatisé pour ce fichier (pas de suite JS dans ce
projet, comportement déjà vérifié manuellement pour les autres panneaux
du site) ; vérification manuelle à l'étape 3.

- [ ] **Step 1: Lire le code actuel**

Confirmer le contenu exact de `renderCompanyAnalysis` (ligne ~6510)
avant d'éditer — il a pu légèrement bouger depuis l'écriture de ce
plan.

- [ ] **Step 2: Remplacer la fonction**

```javascript
async function renderCompanyAnalysis(company) {
  const estPerime = company.financial_analysis_quarter !== company.latest_quarter_date
    || !company.financial_analysis_html;
  const enTete = `
    <div class="back-link-row">
      <a href="${companyBackTarget.hash}" class="back-link">← ${companyBackTarget.label}</a>
      <a href="#indices/${encodeURIComponent(company.ticker)}" class="back-link">← ${company.name}</a>
    </div>
    <h2>Analyse financière complète</h2>
  `;
  if (!estPerime) {
    document.getElementById('indicesContent').innerHTML = `
      ${enTete}
      <div class="ai-analysis">${company.financial_analysis_html}</div>
    `;
    return;
  }
  document.getElementById('indicesContent').innerHTML = `
    ${enTete}
    <div class="ai-analysis ai-analysis-loading">Génération de l'analyse en cours…</div>
  `;
  try {
    const reponse = await fetch(`https://goldbot.fr:8445/financial-analysis/${encodeURIComponent(company.ticker)}`);
    const resultat = await reponse.json();
    const contenuWrap = document.querySelector('#indicesContent .ai-analysis');
    if (!contenuWrap) return;  // le visiteur a changé de page pendant l'attente
    contenuWrap.classList.remove('ai-analysis-loading');
    contenuWrap.innerHTML = resultat.disponible
      ? resultat.html
      : '<p>Analyse non disponible pour le moment.</p>';
  } catch (erreur) {
    const contenuWrap = document.querySelector('#indicesContent .ai-analysis');
    if (contenuWrap) {
      contenuWrap.classList.remove('ai-analysis-loading');
      contenuWrap.innerHTML = '<p>Analyse non disponible pour le moment.</p>';
    }
  }
}
```

Le seul appelant existant de `renderCompanyAnalysis` (le gestionnaire
de route qui l'invoque sur navigation) n'a pas besoin de changer :
JavaScript tolère d'appeler une fonction désormais `async` sans
`await` côté appelant (la promesse est simplement ignorée, le rendu se
termine en tâche de fond) — vérifier malgré tout qu'aucun code
existant ne dépend d'un retour synchrone de `renderCompanyAnalysis`
(grep `renderCompanyAnalysis(` dans `docs/index.html`).

- [ ] **Step 3: Vérifier manuellement dans un navigateur**

Ouvrir `docs/index.html` localement (ou le site publié après
déploiement), naviguer vers la fiche détaillée d'une entreprise dont
`financial_analysis_quarter === latest_quarter_date` (cas déjà en
cache — doit s'afficher instantanément, sans requête réseau visible
dans l'onglet Network des devtools) puis, si possible, vers une
entreprise périmée (doit afficher "Génération de l'analyse en
cours…" puis le résultat ou le message d'indisponibilité).

- [ ] **Step 4: Commit**

```bash
git add docs/index.html
git commit -m "feat(frontend): charge l'analyse financiere a la demande si perimee"
```

---

## Déploiement (hors code — voir § Plan de déploiement de la spec)

Après la Task 7 : vérifier que le VPS (`/home/assistantia/analyse-or`)
a des identifiants git avec droit de push sur `main` (point ouvert
explicitement signalé dans la spec), puis déployer dans l'ordre
indiqué par la spec : route + mode CLI en premier (Tasks 5-6) avec
`build_company_entry` encore inchangé (Task 2 pas encore mergée/
déployée), vérifier que la route répond correctement sur des tickers
déjà en cache, et ne merger/déployer la Task 2 (retrait de l'appel
proactif) qu'après cette vérification. Ce plan liste les tâches dans
un ordre de dépendances de CODE (Task 2 doit précéder Task 5 car
`generate_one` lit le nouveau champ `latest_quarter_date`) ; l'ordre
de DÉPLOIEMENT réel peut nécessiter un déploiement en deux temps
distinct de l'ordre d'implémentation, à la discrétion de qui exécute
ce plan.
