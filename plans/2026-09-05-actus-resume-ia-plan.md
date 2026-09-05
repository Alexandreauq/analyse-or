# Actus enrichies (source, résumé IA) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For each company news item in `docs/indices.json`, add the article's source and an AI-generated 1-2 sentence summary/context (based on the real article content when reachable), and display the news title in the app's accent color instead of default blue link color.

**Architecture:** Extend the existing news pipeline in `indices_score.py` (`parse_news_rss` → `fetch_news` → `build_company_entry`) with two new pure-ish functions: `fetch_article_text` (resolves the Google News redirect, fetches the page, extracts main text via `trafilatura`) and `summarize_news_item` (calls the Anthropic Messages API directly via `requests`, no SDK). Both are designed to never raise — any failure degrades to `None`/`""` so one broken article never drops a company's score or its other news items. Front-end changes are confined to `renderCompanyDetail` in `docs/index.html` (the only place per-company news is rendered).

**Tech Stack:** Python 3.12, `requests` (already a dependency), `trafilatura` (new dependency, main-content extraction), Anthropic Messages API (`claude-haiku-4-5-20251001`, called via raw HTTP — no `anthropic` SDK package added), vanilla JS/CSS (`docs/index.html`).

**Spec:** `specs/2026-09-05-actus-resume-ia-design.md`

## Global Constraints

- `fetch_article_text` and `summarize_news_item` must never raise — every failure path returns `None` (article text) or `""` (summary), matching the existing per-item/per-company tolerance already established in `build_company_entry`/`fetch_news`.
- No new dependency for the Anthropic call itself — use `requests` (already imported in `indices_score.py`), matching the file's existing style of calling external HTTP APIs directly (see `fetch_news`, FRED calls in `gold_score.py`).
- `trafilatura` is the only new pip dependency, added to `requirements.txt`.
- Extracted article text is truncated to 4000 characters before being sent to the summarization call (cost/latency control).
- `ANTHROPIC_API_KEY` is read from the environment; if absent, `summarize_news_item` returns `""` without making any network call (keeps local/dev runs working without the secret).
- The news title's link color in the front end must use `var(--gold)` (the app's single existing accent color), not a new color.
- Model: `claude-haiku-4-5-20251001`, `max_tokens: 150`.

---

## Task 1: `parse_news_rss` extracts the `source` field

**Files:**
- Modify: `indices_score.py:415-428` (`parse_news_rss`)
- Test: `tests/test_indices_score.py` (news RSS fixtures section, near existing `test_parse_news_rss_*` tests)

**Interfaces:**
- Produces: `parse_news_rss(xml_bytes: bytes) -> list[dict]` — each dict now has an added `"source": str` key (empty string if the `<source>` tag is absent from that `<item>`). Existing keys (`title`, `date`, `link`) unchanged.

- [ ] **Step 1: Write the failing tests**

Add these two tests right after the existing `test_parse_news_rss_limits_to_five_items` test in `tests/test_indices_score.py`:

```python
SAMPLE_RSS_WITH_SOURCE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>LVMH annonce une hausse de ses ventes</title>
  <link>https://example.com/article1</link>
  <pubDate>Thu, 04 Sep 2026 10:00:00 GMT</pubDate>
  <source url="https://www.lemonde.fr">Le Monde.fr</source>
</item>
</channel></rss>
"""


def test_parse_news_rss_extracts_source():
    items = parse_news_rss(SAMPLE_RSS_WITH_SOURCE)
    assert items[0]["source"] == "Le Monde.fr"


def test_parse_news_rss_defaults_source_to_empty_string_when_absent():
    items = parse_news_rss(SAMPLE_RSS)  # fixture existante, sans tag <source>
    assert items[0]["source"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "extracts_source or defaults_source" -v`
Expected: FAIL — `KeyError: 'source'`

- [ ] **Step 3: Implement**

In `indices_score.py`, replace the body of `parse_news_rss`:

```python
def parse_news_rss(xml_bytes: bytes) -> list[dict]:
    """Extrait titre/date/lien/source des N premiers <item> d'un flux RSS Google News."""
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall(".//item")[:NEWS_MAX_ITEMS]:
        title = item.findtext("title", default="")
        link = item.findtext("link", default="")
        pub_date_raw = item.findtext("pubDate", default="")
        source = item.findtext("source", default="")
        try:
            date_str = parsedate_to_datetime(pub_date_raw).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            date_str = ""
        items.append({"title": title, "date": date_str, "link": link, "source": source})
    return items
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "parse_news_rss" -v`
Expected: PASS (all 4 `parse_news_rss` tests, including the 2 pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): extraire la source de chaque actu depuis le flux RSS"
```

---

## Task 2: `fetch_article_text` — récupération + extraction du texte d'un article

**Files:**
- Modify: `indices_score.py` (add `trafilatura` import near top, add new function after `fetch_news`, i.e. after line 435 in current file)
- Modify: `requirements.txt` (add `trafilatura`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `fetch_article_text(url: str) -> str | None`, used by Task 4.

- [ ] **Step 1: Add the dependency**

In `requirements.txt`, add a line:

```
trafilatura
```

(File becomes: `requests`, `yfinance`, `pandas`, `trafilatura`.)

Run: `pip install -r requirements.txt` (so the test in Step 2 can actually import `trafilatura`)

- [ ] **Step 2: Write the failing tests**

Add near the top of `indices_score.py`'s import block is where the new `import trafilatura` goes (Step 3) — first write the tests assuming it exists. Add to `tests/test_indices_score.py`, after the Task 1 tests:

```python
import requests
import indices_score
from indices_score import fetch_article_text


def test_fetch_article_text_returns_extracted_text_on_success(monkeypatch):
    class FakeResponse:
        text = "<html><body><p>Contenu de l'article.</p></body></html>"
        def raise_for_status(self):
            pass
    monkeypatch.setattr(indices_score.requests, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(indices_score.trafilatura, "extract", lambda html: "Contenu de l'article.")
    assert fetch_article_text("https://example.com/article") == "Contenu de l'article."


def test_fetch_article_text_truncates_to_4000_chars(monkeypatch):
    class FakeResponse:
        text = "<html></html>"
        def raise_for_status(self):
            pass
    monkeypatch.setattr(indices_score.requests, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(indices_score.trafilatura, "extract", lambda html: "a" * 5000)
    result = fetch_article_text("https://example.com/article")
    assert len(result) == 4000


def test_fetch_article_text_returns_none_on_http_error(monkeypatch):
    def raise_error(*a, **k):
        raise requests.RequestException("boom")
    monkeypatch.setattr(indices_score.requests, "get", raise_error)
    assert fetch_article_text("https://example.com/article") is None


def test_fetch_article_text_returns_none_when_extraction_is_empty(monkeypatch):
    class FakeResponse:
        text = "<html></html>"
        def raise_for_status(self):
            pass
    monkeypatch.setattr(indices_score.requests, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(indices_score.trafilatura, "extract", lambda html: None)
    assert fetch_article_text("https://example.com/article") is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "fetch_article_text" -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_article_text'`

- [ ] **Step 4: Implement**

In `indices_score.py`, add `import trafilatura` to the import block near the top (after `import requests`):

```python
import requests
import trafilatura
```

Then add this function immediately after `fetch_news` (after line 435, before the `OUTPUT_JSON_PATH` definition):

```python
ARTICLE_TEXT_MAX_CHARS = 4000


def fetch_article_text(url: str) -> str | None:
    """Récupère la page d'un article (la redirection Google News est suivie
    automatiquement par requests) et en extrait le texte principal via
    trafilatura. Renvoie None sur tout échec — statut HTTP, erreur réseau,
    page bloquée (paywall/anti-bot), extraction vide.

    Utilise un `except Exception` volontairement large : contrairement au
    reste du fichier, cette fonction dialogue avec des pages web tierces
    dont les modes d'échec (HTML malformé, timeout, blocage) ne sont pas un
    contrat stable qu'on peut énumérer précisément — le contrat de cette
    fonction est justement de ne jamais lever, quoi qu'il arrive côté page
    externe.
    """
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        text = trafilatura.extract(resp.text)
    except Exception:
        return None
    if not text:
        return None
    return text[:ARTICLE_TEXT_MAX_CHARS]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "fetch_article_text" -v`
Expected: PASS (4/4)

- [ ] **Step 6: Run the full suite to check nothing else broke**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS, no new warnings

- [ ] **Step 7: Commit**

```bash
git add indices_score.py requirements.txt tests/test_indices_score.py
git commit -m "feat(indices): extraire le texte principal d'un article via trafilatura"
```

---

## Task 3: `summarize_news_item` — résumé IA via l'API Anthropic

**Files:**
- Modify: `indices_score.py` (add function after `fetch_article_text`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: nothing from earlier tasks directly (independent function), but is designed to be called with the `str | None` that `fetch_article_text` (Task 2) returns.
- Produces: `summarize_news_item(title: str, company_name: str, article_text: str | None) -> str`, used by Task 4.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_indices_score.py`, after the Task 2 tests:

```python
from indices_score import summarize_news_item


class _FakeAnthropicResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_summarize_news_item_returns_empty_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def fail_if_called(*a, **k):
        raise AssertionError("no network call expected without an API key")

    monkeypatch.setattr(indices_score.requests, "post", fail_if_called)
    assert summarize_news_item("Titre", "LVMH", "Texte de l'article") == ""


def test_summarize_news_item_uses_article_text_when_available(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _FakeAnthropicResponse({"content": [{"text": "Résumé généré."}]})

    monkeypatch.setattr(indices_score.requests, "post", fake_post)
    result = summarize_news_item("Titre", "LVMH", "Contenu réel de l'article")
    assert result == "Résumé généré."
    assert "Contenu réel de l'article" in captured["json"]["messages"][0]["content"]
    assert captured["json"]["model"] == "claude-haiku-4-5-20251001"


def test_summarize_news_item_uses_headline_only_prompt_when_article_text_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _FakeAnthropicResponse({"content": [{"text": "Contexte prudent."}]})

    monkeypatch.setattr(indices_score.requests, "post", fake_post)
    result = summarize_news_item("Titre", "LVMH", None)
    assert result == "Contexte prudent."
    assert "suggère" in captured["json"]["messages"][0]["content"]


def test_summarize_news_item_returns_empty_on_http_failure(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def fake_post(*a, **k):
        raise requests.RequestException("boom")

    monkeypatch.setattr(indices_score.requests, "post", fake_post)
    assert summarize_news_item("Titre", "LVMH", "texte") == ""


def test_summarize_news_item_returns_empty_on_malformed_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        indices_score.requests, "post",
        lambda *a, **k: _FakeAnthropicResponse({"unexpected": "shape"})
    )
    assert summarize_news_item("Titre", "LVMH", "texte") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "summarize_news_item" -v`
Expected: FAIL — `ImportError: cannot import name 'summarize_news_item'`

- [ ] **Step 3: Implement**

Add to `indices_score.py`, immediately after `fetch_article_text`:

```python
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


def summarize_news_item(title: str, company_name: str, article_text: str | None) -> str:
    """Génère un résumé/contexte en français (1-2 phrases) via l'API
    Anthropic. Renvoie "" sur tout échec (clé API absente, erreur réseau,
    réponse HTTP non-200, réponse malformée) — ne lève jamais, même
    justification que fetch_article_text (dialogue avec un service tiers
    dont on ne peut pas énumérer précisément tous les modes d'échec)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ""

    if article_text:
        prompt = (
            f"Voici un article de presse concernant l'entreprise {company_name}, "
            f"titré « {title} ».\n\nContenu de l'article :\n{article_text}\n\n"
            "En 1 à 2 phrases en français, résume le contexte et l'enjeu "
            "principal de cet article pour cette entreprise. Sois factuel et "
            "neutre, sans donner de conseil d'investissement."
        )
    else:
        prompt = (
            f"Voici uniquement le titre d'un article de presse concernant "
            f"l'entreprise {company_name} : « {title} ».\n\n"
            "Le contenu de l'article n'est pas disponible. En 1 phrase en "
            "français, propose un contexte prudent et hypothétique à partir "
            "de ce titre seul (formule-le comme une supposition, par exemple "
            "« Cet article suggère que... », sans jamais affirmer de faits "
            "que le titre seul ne permet pas de confirmer)."
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
                "model": ANTHROPIC_MODEL,
                "max_tokens": 150,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"].strip()
    except Exception:
        return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "summarize_news_item" -v`
Expected: PASS (5/5)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): résumé IA d'une actu via l'API Anthropic (Haiku)"
```

---

## Task 4: Wire `fetch_news` to attach `source` and `summary` per item

**Files:**
- Modify: `indices_score.py:431-435` (`fetch_news`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `fetch_article_text(url: str) -> str | None` (Task 2), `summarize_news_item(title, company_name, article_text) -> str` (Task 3), `parse_news_rss` (Task 1, already produces `source`).
- Produces: `fetch_news(company_name: str) -> list[dict]` — each dict now has `title`, `date`, `link`, `source` (Task 1), and `summary` (new).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_indices_score.py`, after the Task 3 tests:

```python
def test_fetch_news_attaches_source_and_summary_and_isolates_per_item_failures(monkeypatch):
    xml_two_items = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>Titre A</title>
  <link>https://example.com/a</link>
  <pubDate>Thu, 04 Sep 2026 10:00:00 GMT</pubDate>
  <source url="https://a.example.com">Source A</source>
</item>
<item>
  <title>Titre B</title>
  <link>https://example.com/b</link>
  <pubDate>Wed, 03 Sep 2026 08:00:00 GMT</pubDate>
  <source url="https://b.example.com">Source B</source>
</item>
</channel></rss>
"""

    class FakeRssResponse:
        content = xml_two_items

        def raise_for_status(self):
            pass

    monkeypatch.setattr(indices_score.requests, "get", lambda *a, **k: FakeRssResponse())
    # Simule un échec d'extraction sur l'article A (fetch_article_text
    # renvoie None, comme sur un vrai paywall) et un succès sur B — sans
    # jamais lever, conformément au contrat de fetch_article_text.
    monkeypatch.setattr(
        indices_score, "fetch_article_text",
        lambda url: None if url == "https://example.com/a" else "Texte B"
    )
    monkeypatch.setattr(
        indices_score, "summarize_news_item",
        lambda title, name, text: f"Résumé pour {title} (article={text})"
    )

    items = fetch_news("Test SA")

    assert len(items) == 2
    assert items[0]["source"] == "Source A"
    assert items[0]["summary"] == "Résumé pour Titre A (article=None)"
    assert items[1]["source"] == "Source B"
    assert items[1]["summary"] == "Résumé pour Titre B (article=Texte B)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_indices_score.py -k "isolates_per_item_failures" -v`
Expected: FAIL — `KeyError: 'summary'`

- [ ] **Step 3: Implement**

In `indices_score.py`, replace `fetch_news`:

```python
def fetch_news(company_name: str) -> list[dict]:
    params = {"q": company_name, "hl": "fr", "gl": "FR", "ceid": "FR:fr"}
    resp = requests.get(NEWS_RSS_URL, params=params, timeout=15)
    resp.raise_for_status()
    items = parse_news_rss(resp.content)
    for item in items:
        article_text = fetch_article_text(item["link"])
        item["summary"] = summarize_news_item(item["title"], company_name, article_text)
    return items
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_indices_score.py -k "isolates_per_item_failures" -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS, all tests green (should be 52 total: 40 pre-existing + 2 from Task 1 + 4 from Task 2 + 5 from Task 3 + 1 from this task)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): relier extraction d'article et résumé IA dans fetch_news"
```

---

## Task 5: Secret et dépendance dans le workflow CI

**Files:**
- Modify: `.github/workflows/indices.yml:30-31`

**Interfaces:**
- Consumes: `ANTHROPIC_API_KEY` GitHub secret (already added by the user in repo settings).
- Produces: nothing consumed by later tasks — this task only wires existing infrastructure.

- [ ] **Step 1: Edit the workflow**

In `.github/workflows/indices.yml`, replace:

```yaml
      - name: Calculer le score des entreprises pilotes
        run: python3 indices_score.py
```

with:

```yaml
      - name: Calculer le score des entreprises pilotes
        run: python3 indices_score.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

- [ ] **Step 2: Validate YAML syntax**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/indices.yml'))"`
Expected: no output, exit code 0 (if `pyyaml` isn't installed, run `python3 -c "import json,subprocess; print('skip: pyyaml not available, visually double-check indentation instead')"` and manually confirm the `env:` block is indented at the same level as `run:`, two spaces further in than `- name:`)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/indices.yml
git commit -m "ci(indices): passer ANTHROPIC_API_KEY au calcul quotidien"
```

---

## Task 6: Affichage front-end (couleur, source, date, résumé)

**Files:**
- Modify: `docs/index.html` (CSS block near line 122, JS `renderCompanyDetail` near line 388-394)

**Interfaces:**
- Consumes: `company.news[]` items with `title`, `date`, `link`, `source`, `summary` fields (all tasks 1-4, already live in `docs/indices.json` shape by the time this task's code runs against real data — for manual verification this task uses a hand-written fixture, see Step 3).

- [ ] **Step 1: Add the CSS rules**

In `docs/index.html`, after the existing `.cal-kind` rule (currently at line 122, right before the blank line and `.company-row` block), add:

```css
  .news-item { padding: 10px 0; border-bottom: 1px solid var(--border); }
  .news-item:last-child { border-bottom: none; }
  .news-title { color: var(--gold); font-size: 13px; font-weight: 500; line-height: 1.4; }
  .news-meta { color: var(--muted); font-size: 11px; margin-top: 2px; }
  .news-summary { color: var(--muted); font-size: 12px; line-height: 1.5; margin: 4px 0 0; }
```

- [ ] **Step 2: Replace the news rendering markup**

In `docs/index.html`, find this block inside `renderCompanyDetail` (currently lines 388-394):

```javascript
  const newsHtml = (company.news || []).map(n => `
    <div class="cal-row">
      <div class="cal-left">
        <span class="cal-date">${n.date}</span>
        <a href="${n.link}" target="_blank" rel="noopener">${n.title}</a>
      </div>
    </div>`).join('');
```

Replace it with:

```javascript
  const newsHtml = (company.news || []).map(n => `
    <div class="news-item">
      <a class="news-title" href="${n.link}" target="_blank" rel="noopener">${n.title}</a>
      <div class="news-meta">${[n.source, n.date].filter(Boolean).join(' · ')}</div>
      ${n.summary ? `<p class="news-summary">${n.summary}</p>` : ''}
    </div>`).join('');
```

- [ ] **Step 3: Manual verification with a local fixture**

Create a temporary local copy of `docs/indices.json` with one company entry that has `source` and `summary` populated on its news items (do NOT commit this file — it's only for manual browser verification):

```bash
cp docs/indices.json docs/indices.json.bak
python3 -c "
import json
data = json.load(open('docs/indices.json', encoding='utf-8'))
if data['companies']:
    data['companies'][0]['news'] = [
        {'title': 'Titre de test', 'date': '2026-09-05', 'link': 'https://example.com',
         'source': 'Source Test', 'summary': 'Ceci est un résumé de test généré pour vérifier l affichage.'}
    ]
json.dump(data, open('docs/indices.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
"
```

Serve the `docs/` folder locally and open the Indices tab, click into the first company, and confirm visually:
- The news title is gold (not blue).
- Source and date appear together on one line beneath the title, in muted gray.
- The summary text appears below that.

```bash
cd docs && python3 -m http.server 8000
```

Open `http://localhost:8000` in a browser (or use Playwright headless if available in this environment), navigate to Indices → first company, verify the above, then stop the server.

**Restore the real file before committing:**

```bash
mv docs/indices.json.bak docs/indices.json
```

- [ ] **Step 4: Commit**

```bash
git add docs/index.html
git commit -m "feat(indices): afficher source, résumé et titre en couleur d'accent pour les actus"
```

---

## Post-merge verification (not a task — informational)

Once this branch is merged and `indices.yml` runs for real (same pattern as the Indices CAC40 feature's own post-merge check): confirm in the real `docs/indices.json` that at least some `summary` fields are non-empty (proves the Anthropic call succeeds end-to-end with the real secret) and that `source` fields are populated. A high proportion of empty `summary` fields would point to either a missing/invalid `ANTHROPIC_API_KEY` or widespread article-extraction failures (expected to some degree per the spec's "Risques connus", but not for every item).
