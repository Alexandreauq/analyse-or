# Analyse IA du portefeuille — fusion dans l'onglet "Analyse" + cartes par entreprise + courbe d'évolution (v2)

## Contexte et relation avec la spec précédente

Ce document **étend et remplace partiellement** `docs/superpowers/specs/2026-11-11-portfolio-ai-analysis-design.md`
(mergée dans `main` le 2026-11-11, commit `0312965`, puis corrigée par la
revue finale, commit `17e1d7f`). Cette première version a été déployée en
production, puis testée par l'utilisateur, qui a demandé trois changements
après usage réel :

1. Le format "une phrase par ligne" est trop pauvre — il veut une vraie
   analyse par entreprise, cadrée comme "si je garde cette ligne à mon prix
   d'achat, est-ce toujours une bonne idée, et quelles sont les actus
   négatives s'il y en a".
2. Les deux onglets séparés ("Analyse" et "Analyse IA") doivent être
   **fusionnés en un seul**, avec la dernière analyse IA affichée
   **automatiquement** à la prochaine visite (pas seulement un indicateur de
   fraîcheur — tout le contenu).
3. Des **graphiques d'évolution** : la courbe de performance déjà existante
   (inchangée), ET une nouvelle courbe traçant comment le jugement global de
   l'IA sur le portefeuille évolue au fil des analyses successives.

Tout ce qui n'est pas explicitement changé ci-dessous (jeton, limites de
débit/concurrence, plafond de dépense partagé, séparation calcul JS pur /
jugement IA côté Python, historique append-only jamais commité, branche
dédiée) reste identique à la spec v1 — ce document ne répète pas ces
points, il documente seulement le delta.

## Changements au contrat JSON du résultat IA

Ancien contrat (v1) :
```json
{
  "resume_global": "...",
  "points_attention": [{"ticker": "...", "raison": "..."}],
  "par_ligne": [{"ticker": "...", "reco": "garder", "justification": "une phrase"}],
  "comparaison_precedente": "...",
  "horodatage": "..."
}
```

Nouveau contrat (v2) :
```json
{
  "resume_global": "...",
  "sante_globale": 42,
  "par_ligne": [
    {"ticker": "...", "reco": "garder", "analyse": "paragraphe complet"}
  ],
  "comparaison_precedente": "...",
  "horodatage": "..."
}
```

- `points_attention` est **supprimé** du contrat — son contenu (alerte/actu
  négative par ligne) est désormais dans le paragraphe `analyse` de chaque
  entrée de `par_ligne`, pas dans une liste séparée.
- `justification` (une phrase) devient `analyse` (un paragraphe complet,
  plusieurs phrases) — cadré explicitement par la consigne de prompt :
  "au prix d'achat vs le prix actuel, est-ce toujours une bonne idée de
  garder cette ligne ? Quelles actualités négatives as-tu trouvées, s'il y
  en a ? Si tu n'en as trouvé aucune, dis-le explicitement."
- `sante_globale` (nouveau) : entier de -100 à +100, même échelle que les
  scores d'entreprises déjà affichés partout ailleurs sur le site — un
  jugement global de l'IA sur la qualité/le risque du portefeuille à cet
  instant. **Optionnel côté parsing** (voir `_parse_resultat` ci-dessous) :
  une réponse qui omet ce champ reste exploitable pour le texte, mais ce
  point de données est alors absent de la courbe d'évolution.

## Côté serveur — `assistant_ia/portfolio_analysis.py`

### `_parse_resultat` (modifié)

- Retire `parsed.setdefault("points_attention", [])`.
- Ajoute : si `sante_globale` est présent mais n'est pas un entier (ou un
  booléen, qui est un sous-type d'`int` en Python — piège déjà connu dans ce
  fichier, voir les gardes `isinstance(x, (int, float)) and not
  isinstance(x, bool)` existantes), le remettre à `None` plutôt que de
  rejeter toute la réponse. `parsed.setdefault("sante_globale", None)` si la
  clé est totalement absente.
- Chaque entrée de `par_ligne` garde exactement les mêmes clés qu'avant
  (`ticker`, `reco`) plus `analyse` à la place de `justification` — aucune
  validation de longueur/contenu sur `analyse` (un texte vide reste valide,
  rendu comme tel côté frontend).

### `_build_prompt` (modifié)

Remplacer la section "Pour chaque position..." par :

```
Pour chaque position, donne :
- une recommandation parmi exactement ces quatre valeurs : "garder",
  "renforcer", "alleger", "vendre" ;
- une analyse complète (plusieurs phrases, pas une seule) qui repond
  explicitement a la question : au prix d'achat de l'utilisateur compare au
  prix actuel, est-ce toujours une bonne idee de garder cette ligne a ce
  niveau ? Mentionne les actualites negatives recentes que tu as trouvees
  s'il y en a, et dis explicitement "aucune actualite negative notable
  trouvee" si ta recherche n'en a pas trouve. Mentionne aussi les points
  positifs pertinents (resultats, catalyseurs) s'il y en a.

Donne aussi :
- un score global "sante_globale", un entier de -100 (portefeuille tres
  risque ou mal positionne) a +100 (portefeuille sain et bien positionne),
  ta propre appreciation globale ;
- un resume global de 2 a 3 phrases sur l'equilibre du portefeuille
  (concentration, risque, qualite generale).
```

Et le bloc JSON final demandé :
```json
{
  "resume_global": "...",
  "sante_globale": 0,
  "par_ligne": [{"ticker": "...", "reco": "garder", "analyse": "..."}],
  "comparaison_precedente": "..."
}
```

Le reste de `_build_prompt` (description des positions, des métriques, de
la comparaison avec l'analyse précédente, la consigne de recherche web
ciblée) est **inchangé**.

### Nouvelles fonctions de lecture de l'historique

```python
def derniere_analyse_complete(path: str = HISTORY_PATH) -> dict | None:
    """Le resultat complet (pas seulement l'horodatage) de la derniere
    analyse reussie, ou None si aucune n'existe encore. Reutilise
    _lit_derniere_analyse, deja tolerante aux fichiers absents/corrompus."""
    derniere = _lit_derniere_analyse(path)
    return derniere.get("resultat") if derniere else None


def historique_sante(path: str = HISTORY_PATH, limite: int = 200) -> list[dict]:
    """Liste chronologique (plus ancienne en premier) de
    {"horodatage": str, "sante_globale": int | None} pour chaque analyse
    du journal -- lecture seule, jamais les champs lourds (texte, snapshot
    de positions). Une ligne corrompue est ignoree (meme contrat que
    _lit_derniere_analyse), jamais une exception. `limite` borne le volume
    renvoye (les plus recentes), pas de pagination necessaire pour un usage
    personnel a cette frequence."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lignes = fh.readlines()
    except Exception:
        return []
    points = []
    for ligne in lignes:
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            entree = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        if not isinstance(entree, dict):
            continue
        resultat = entree.get("resultat") or {}
        points.append({
            "horodatage": entree.get("horodatage"),
            "sante_globale": resultat.get("sante_globale") if isinstance(resultat, dict) else None,
        })
    return points[-limite:]
```

`last_analysis_timestamp` (v1) est **retirée** — plus aucun appelant après
ce chantier (`derniere_analyse_complete` la remplace partout, y compris
pour en dériver l'horodatage côté frontend via `resultat.horodatage`).

## Routes — `assistant_ia/api.py`

### `GET /portfolio-analysis/last` (contrat modifié, retro-incompatible — acceptable, aucun client externe)

Ancienne réponse : `{"horodatage": str | None}`.
Nouvelle réponse :
```json
{"disponible": true, "resultat": {"resume_global": "...", "sante_globale": 42, "par_ligne": [...], "comparaison_precedente": "...", "horodatage": "..."}}
```
ou `{"disponible": false, "resultat": null}` si aucune analyse n'existe
encore. Même enveloppe `{disponible, resultat}` que `POST
/portfolio-analysis` (sans `erreur`, puisqu'une absence d'historique n'est
pas un échec) — permet au frontend de réutiliser le même code de rendu pour
les deux routes.

```python
@app.get("/portfolio-analysis/last")
def portfolio_analysis_last(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    resultat = portfolio_analysis.derniere_analyse_complete()
    return {"disponible": resultat is not None, "resultat": resultat}
```

### `GET /portfolio-analysis/history` (nouvelle)

```python
@app.get("/portfolio-analysis/history")
def portfolio_analysis_history(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    return {"historique": portfolio_analysis.historique_sante()}
```

Réponse : `{"historique": [{"horodatage": "2026-11-05T...", "sante_globale": 30}, {"horodatage": "2026-11-11T...", "sante_globale": 42}]}`.
Lecture seule, pas d'appel IA, pas de limite de débit dédiée au-delà du
jeton (même raisonnement que l'ancien `/last`).

`POST /portfolio-analysis` est **inchangée** (même validation, mêmes
limites, même délégation à `run_portfolio_analysis` via `run_in_threadpool`).

## Frontend — `docs/index.html`

### Suppression de la section carrousel "Analyse IA"

L'entrée `{ title: 'Analyse IA', ... }` dans `portfolioActionsSections`
(`renderPortfolioActionsTab`) est **retirée**. Le carrousel repasse de 6 à
5 sections : Ajouter une position / Tes positions / Opportunités / Analyse
/ Bot Actions.

### La section "Analyse" héberge maintenant tout

```js
{
  title: 'Analyse',
  desc: 'Concentration, santé du portefeuille selon le modèle, attribution de performance, dividendes, et l\'avis de l\'assistant IA sur chaque ligne.',
  html: portfolioAnalysisHtml(positions, companiesByTicker, currencyByIndex, priceHistoryByTicker, dividendHistoryByTicker) + portfolioAiSubsectionHtml(),
  wire: () => {
    wirePortfolioPerformanceBenchmarkSelectors(positions, closedPositions, companiesByTicker, currencyByIndex, priceHistoryByTicker);
    wirePortfolioAiSubsection(positions, closedPositions, companiesByTicker, currencyByIndex, dividendHistoryByTicker);
  },
},
```

`portfolioAnalysisHtml` elle-même n'est **pas modifiée** — son contenu
(performance, concentration, santé selon le modèle, dividendes) reste
identique, on concatène juste la nouvelle sous-section IA après.

### Remplacement des fonctions de rendu/branchement IA

Suppression de `portfolioAiAnalysisSectionHtml`, `_portfolioAiAnalysisResultHtml`,
`wirePortfolioAiAnalysisSection` (v1). Remplacées par :

```js
function portfolioAiSubsectionHtml() {
  return `
    <h3 class="portfolio-analysis-title">Avis de l'IA</h3>
    <div id="portfolioAiFreshness" class="hero-sub"></div>
    <button class="toggle-btn" type="button" id="portfolioAiAnalysisBtn">Lancer l'analyse IA</button>
    <div id="portfolioAiChart"></div>
    <div id="portfolioAiAnalysisResult"></div>
  `;
}
```

(Pas de "Chargement…" initial cette fois : contrairement à v1, cette
sous-section se peuple automatiquement au chargement si un jeton est déjà
mémorisé — voir `wirePortfolioAiSubsection` — donc un état vide neutre par
défaut est plus correct qu'un message de chargement qui pourrait rester
affiché indéfiniment si aucun jeton n'est mémorisé.)

```js
function _portfolioAiRecoLabel(reco) {
  const labels = { garder: 'Garder', renforcer: 'Renforcer', alleger: 'Alléger', vendre: 'Vendre', a_verifier: 'À vérifier' };
  return labels[reco] || reco;
}

function _portfolioAiSanteLabel(sante) {
  if (typeof sante !== 'number') return '';
  const color = sante >= 0 ? 'var(--green-500)' : 'var(--rust)';
  return `<span class="price-value" style="color:${color}">${sante > 0 ? '+' : ''}${sante}</span>`;
}

// positionsByTicker : Map ticker -> position ouverte (pour le prix d'achat),
// companiesByTicker : deja disponible partout ailleurs dans ce fichier (pour le prix actuel/devise).
function _portfolioAiCardsHtml(parLigne, positionsByTicker, companiesByTicker, currencyByIndex) {
  if (!parLigne.length) {
    return '<div class="list-empty">Aucune recommandation disponible.</div>';
  }
  return parLigne.map(l => {
    const position = positionsByTicker.get(l.ticker);
    const company = companiesByTicker[l.ticker];
    const currency = company ? (currencyByIndex[company.index] || 'EUR') : 'EUR';
    const symbol = CURRENCY_SYMBOL[currency] || currency;
    const prixHtml = position && company
      ? `<span class="hero-sub">Achat ${position.buy_price.toLocaleString('fr-FR')} ${symbol} — actuel ${(company.current_price ?? 0).toLocaleString('fr-FR')} ${symbol}</span>`
      : '';
    return `
      <div class="portfolio-total-card">
        <div class="portfolio-position-main">
          <a class="company-name" href="#indices/${encodeURIComponent(l.ticker)}">${escHtml(company ? company.name : l.ticker)}</a>
          ${prixHtml}
        </div>
        <span class="price-value">${escHtml(_portfolioAiRecoLabel(l.reco))}</span>
        <p class="hero-sub">${escHtml(l.analyse || l.justification || '')}</p>
      </div>`;
  }).join('');
}

function _portfolioAiAnalysisResultHtml(resultat, positionsByTicker, companiesByTicker, currencyByIndex) {
  const comparaisonHtml = resultat.comparaison_precedente
    ? `<h3 class="portfolio-analysis-title">Depuis la dernière fois</h3><div class="hero-sub">${escHtml(resultat.comparaison_precedente)}</div>`
    : '';
  return `
    <div class="portfolio-totals">
      <div class="portfolio-total-card">
        <span class="portfolio-total-label">Résumé</span>
        <div class="hero-sub">${escHtml(resultat.resume_global || '')}</div>
        ${typeof resultat.sante_globale === 'number' ? `<span class="portfolio-total-label">Santé jugée par l'IA</span>${_portfolioAiSanteLabel(resultat.sante_globale)}` : ''}
      </div>
    </div>
    <h3 class="portfolio-analysis-title">Par entreprise</h3>
    <div class="portfolio-ai-cards">${_portfolioAiCardsHtml(resultat.par_ligne || [], positionsByTicker, companiesByTicker, currencyByIndex)}</div>
    ${comparaisonHtml}
  `;
}

function _portfolioAiFreshnessLabel(horodatage) {
  if (!horodatage) return 'Aucune analyse encore.';
  const jours = Math.floor((Date.now() - new Date(horodatage).getTime()) / 86400000);
  if (jours <= 0) return 'Dernière analyse : aujourd\'hui.';
  return `Dernière analyse : il y a ${jours} jour${jours > 1 ? 's' : ''}.`;
}

// Miroir simplifie de performanceCurveSvg (L2530) : une seule ligne, plage
// fixe -100..100 (echelle du score, jamais variable comme pnlPct) --
// jamais de benchmark a superposer ici.
function aiJudgmentCurveSvg(points) {
  const usable = points.filter(p => typeof p.sante_globale === 'number' && p.horodatage);
  if (usable.length < 2) {
    return '<div class="empty">Pas encore assez d\'analyses passées pour tracer une évolution (au moins deux nécessaires).</div>';
  }
  const width = 600, height = 160, padding = 28;
  const minVal = -100, maxVal = 100, range = maxVal - minVal;
  const minDate = usable[0].horodatage, maxDate = usable[usable.length - 1].horodatage;
  const dateSpanMs = new Date(maxDate) - new Date(minDate) || 1;
  const x = d => padding + ((new Date(d) - new Date(minDate)) / dateSpanMs) * (width - padding * 2);
  const y = v => padding + (height - padding * 2) - ((v - minVal) / range) * (height - padding * 2);
  const points_ = usable.map(p => `${x(p.horodatage).toFixed(1)},${y(p.sante_globale).toFixed(1)}`).join(' ');
  const last = usable[usable.length - 1];
  const color = last.sante_globale >= 0 ? 'var(--green-500)' : 'var(--rust)';
  const zeroY = y(0).toFixed(1);
  return `
    <svg viewBox="0 0 ${width} ${height}" class="portfolio-perf-chart" role="img" aria-label="Évolution du jugement IA sur le portefeuille">
      <line x1="${padding}" y1="${zeroY}" x2="${width - padding}" y2="${zeroY}" stroke="var(--muted)" stroke-dasharray="4 3" />
      <polyline points="${points_}" fill="none" stroke="${color}" stroke-width="2" />
    </svg>
    <div class="portfolio-perf-legend"><span style="color:${color}">● Santé jugée par l'IA (${last.sante_globale > 0 ? '+' : ''}${last.sante_globale})</span></div>`;
}
```

```js
function wirePortfolioAiSubsection(positions, closedPositions, companiesByTicker, currencyByIndex, dividendHistoryByTicker) {
  const freshnessEl = document.getElementById('portfolioAiFreshness');
  const chartEl = document.getElementById('portfolioAiChart');
  const btn = document.getElementById('portfolioAiAnalysisBtn');
  const resultEl = document.getElementById('portfolioAiAnalysisResult');
  if (!btn || !resultEl) return;

  const positionsByTicker = new Map(positions.map(p => [p.ticker, p]));

  const renderResult = (resultat) => {
    resultEl.innerHTML = _portfolioAiAnalysisResultHtml(resultat, positionsByTicker, companiesByTicker, currencyByIndex);
    if (freshnessEl) freshnessEl.textContent = _portfolioAiFreshnessLabel(resultat.horodatage);
  };

  const refreshChart = async (token) => {
    if (!chartEl) return;
    try {
      const res = await fetch('https://goldbot.fr:8445/portfolio-analysis/history', { headers: { 'X-Bot-Token': token } });
      if (!res.ok) return;
      const data = await res.json();
      chartEl.innerHTML = aiJudgmentCurveSvg(data.historique || []);
    } catch (e) { /* le reste de la sous-section reste utilisable sans le graphique */ }
  };

  // Jamais de prompt() force a l'ouverture du panneau (meme convention que
  // fetchIbkrBotDashboard, L3239 et le comportement v1 de cette section) :
  // on ne lit/affiche que si un jeton est deja memorise.
  const tokenDejaMemorise = localStorage.getItem('aiAssistantToken');
  if (tokenDejaMemorise) {
    fetch('https://goldbot.fr:8445/portfolio-analysis/last', { headers: { 'X-Bot-Token': tokenDejaMemorise } })
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data && data.disponible) {
          renderResult(data.resultat);
          btn.textContent = 'Relancer l\'analyse IA';
        } else if (freshnessEl) {
          freshnessEl.textContent = 'Aucune analyse encore.';
        }
      })
      .catch(() => { if (freshnessEl) freshnessEl.textContent = ''; });
    refreshChart(tokenDejaMemorise);
  } else if (freshnessEl) {
    freshnessEl.textContent = 'Aucune analyse encore.';
  }

  btn.addEventListener('click', async () => {
    const jeton = getAiAssistantToken();
    if (!jeton) return;
    btn.disabled = true;
    const texteInitial = btn.textContent;
    btn.textContent = 'Analyse en cours…';
    try {
      const concentration = computePortfolioConcentration(positions, companiesByTicker, currencyByIndex);
      const health = computePortfolioHealth(positions, companiesByTicker);
      const attribution = computePortfolioAttribution(positions, companiesByTicker);
      const dividendsReceived = computeDividendsReceived(
        [...positions, ...closedPositions], dividendHistoryByTicker, companiesByTicker, currencyByIndex);
      const projectedIncome = computeProjectedDividendIncome(
        positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex);
      const totals = computePortfolioTotals(positions, companiesByTicker, currencyByIndex);
      const payload = buildPortfolioAnalysisPayload(
        positions, totals, concentration, health, attribution, dividendsReceived, projectedIncome);

      const res = await fetch('https://goldbot.fr:8445/portfolio-analysis', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Bot-Token': jeton },
        body: JSON.stringify(payload),
      });
      if (res.status === 401) {
        localStorage.removeItem('aiAssistantToken');
        resultEl.innerHTML = '<div class="empty">Ton jeton d\'accès est invalide. Recharge la page pour en saisir un nouveau.</div>';
        return;
      }
      if (res.status === 429 || res.status === 503 || res.status === 400) {
        let detail = "L'analyse n'est pas possible pour le moment.";
        try { const corps = await res.json(); if (corps && corps.detail) detail = corps.detail; } catch (e) { /* message par défaut */ }
        resultEl.innerHTML = `<div class="empty">${escHtml(detail)}</div>`;
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.disponible) {
        resultEl.innerHTML = `<div class="empty">${escHtml(data.erreur || "Analyse indisponible pour le moment.")}</div>`;
        return;
      }
      renderResult(data.resultat);
      btn.textContent = 'Relancer l\'analyse IA';
      refreshChart(jeton);
    } catch (e) {
      resultEl.innerHTML = '<div class="empty">Analyse indisponible pour le moment.</div>';
    } finally {
      btn.disabled = false;
      if (btn.textContent === 'Analyse en cours…') btn.textContent = texteInitial;
    }
  });
}
```

Notes de conception sur ce code :
- `l.analyse || l.justification` dans `_portfolioAiCardsHtml` : tolérance
  défensive si jamais une très vieille entrée d'historique (v1, avant ce
  chantier) était un jour réaffichée — coût nul, cohérent avec le style
  défensif déjà partout dans ce fichier. Pas une vraie migration de
  données : l'historique de production est quasi vide à ce stade (la
  fonctionnalité v1 vient tout juste d'être déployée), donc aucun script de
  migration n'est nécessaire.
- Le bouton passe de "Lancer l'analyse IA" à "Relancer l'analyse IA" dès
  qu'une analyse (ancienne ou fraîche) est affichée — signal visuel qu'une
  analyse existe déjà, cohérent avec l'esprit "la dernière analyse reste
  affichée" demandé.
- Pas de nouvelle classe CSS obligatoire : `.portfolio-total-card` (déjà
  utilisée ailleurs dans ce fichier, voir `portfolioTotalsHtml`) est
  réutilisée pour les cartes par entreprise plutôt que d'introduire une
  nouvelle classe `.portfolio-ai-cards` à styliser — ce sélecteur
  n'existe que comme conteneur de layout (pas de nouvelle règle CSS
  nécessaire si `.portfolio-total-card` s'empile proprement en colonne par
  défaut ; si un style spécifique s'avère nécessaire à l'implémentation,
  l'implémenteur l'ajoute en suivant les conventions CSS déjà en place dans
  ce fichier, sans légiférer davantage ici).

## Tests

**Python** (`tests/assistant_ia/test_portfolio_analysis.py`, extension) :
- `_parse_resultat` : `sante_globale` entier valide conservé ; absent →
  `None` ; valeur non-entière (chaîne, booléen) → `None`, reste du résultat
  conservé ; `points_attention` n'est plus requis/produit.
- `_build_prompt` : le texte demande `sante_globale` et le mot "analyse"
  (pas "justification") dans le bloc JSON exemple.
- `derniere_analyse_complete` : fichier absent → `None` ; fichier avec une
  entrée → son `resultat` exact ; dernière ligne corrompue → la précédente.
- `historique_sante` : fichier absent → `[]` ; plusieurs entrées → liste
  dans l'ordre chronologique avec `sante_globale` extrait de chacune ;
  entrée sans `resultat.sante_globale` → `None` pour cette entrée (pas
  d'exception, pas d'entrée sautée) ; ligne corrompue → ignorée.

Extension de `tests/assistant_ia/test_api.py` :
- `GET /portfolio-analysis/last` : jeton manquant → 401 ; aucune analyse →
  `{"disponible": false, "resultat": null}` ; une analyse existe → son
  contenu exact.
- `GET /portfolio-analysis/history` : jeton manquant → 401 ; réponse =
  exactement ce que renvoie `portfolio_analysis.historique_sante()`
  (monkeypatché).

**JavaScript** : aucun test nouveau requis (convention déjà établie :
`docs/index.html` n'est jamais testé unitairement). Vérification manuelle
en navigateur avant commit, comme pour les chantiers précédents de cette
session.

## Branche de travail

`feature/portfolio-ai-analysis-v2-merged-tab`, worktree déjà créé. Jamais
sur `main` directement, fusion seulement après validation explicite de
l'utilisateur (même règle que le chantier v1).
