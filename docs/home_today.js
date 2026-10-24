// docs/home_today.js
// Les 4 infos du jour de l'accueil (tuiles desktop / carte « Aujourd'hui »
// mobile) : score Or, meilleur score Indices, performance réelle des
// signaux, statut des bots. Logique pure (aucun accès réseau ni DOM) : les
// données sont chargées et passées par index.html (loadHomeToday). Toute
// donnée absente donne « — » / « Indisponible », jamais NaN ni undefined.
// Chargé tel quel par index.html, avec module.exports pour Node
// (docs/home_today.test.js).
// Voir docs/superpowers/specs/2026-10-24-accueil-agent-redesign-design.md (4.3, 4.4).

const HOME_TODAY_MISSING = "Indisponible pour l'instant.";

// Nombre signé à la française : '+98,6', '−23,4' (vrai signe moins U+2212).
function formatSigned(n, digits = 1) {
  if (n == null || !Number.isFinite(n)) return '—';
  const abs = Math.abs(n).toLocaleString('fr-FR', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  if (n > 0) return '+' + abs;
  if (n < 0) return '−' + abs;
  return abs;
}

// Même calcul que buildSignalStatsHtml (index.html) sur le périmètre du bot :
// rendements des positions dont l'indice n'est pas hors périmètre, réels pour
// les clôturées, valorisés au cours du jour pour les ouvertes.
function computeSignalSummary(positions, currentReturn, currentIndexReturn, outOfScope) {
  const inScope = (positions || []).filter(p => !outOfScope.has(p.index));
  const avg = arr => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null;
  const returns = inScope.map(currentReturn).filter(r => r != null);
  const alphas = inScope.map(p => {
    const r = currentReturn(p);
    const idx = currentIndexReturn(p);
    return r != null && idx != null ? r - idx : null;
  }).filter(a => a != null);
  return {
    winRate: returns.length ? returns.filter(r => r > 0).length / returns.length * 100 : null,
    avgAlpha: avg(alphas),
    openCount: inScope.filter(p => p.status === 'open').length,
  };
}

function orItem(score) {
  const ok = score && Number.isFinite(score.composite_score);
  const interpretation = ok ? (score.interpretation || '') : '';
  const short = interpretation.split(' — ')[0].trim().toLowerCase();
  return {
    key: 'or', label: 'Or · score macro', href: '#or',
    value: ok ? formatSigned(score.composite_score) : '—',
    detail: ok && interpretation ? interpretation : HOME_TODAY_MISSING,
    tone: ok ? (score.composite_score < 0 ? 'neg' : 'pos') : 'neutral',
    short: { label: 'Or', sub: ok && short ? short : 'indisponible' },
  };
}

function topItem(companies) {
  const scored = (companies || []).filter(c => c && Number.isFinite(c.score));
  if (!scored.length) {
    return { key: 'top', label: 'Indices · top du jour', href: '#indices', value: '—', detail: HOME_TODAY_MISSING,
      tone: 'neutral', short: { label: 'Top du jour', sub: 'indisponible' } };
  }
  const best = scored.reduce((a, b) => (b.score > a.score ? b : a));
  return {
    key: 'top', label: 'Indices · top du jour', href: '#indices',
    value: formatSigned(best.score),
    detail: `${best.name}, meilleur score sur ${scored.length.toLocaleString('fr-FR')} entreprises notées.`,
    tone: best.score < 0 ? 'neg' : 'pos',
    short: { label: 'Top du jour', sub: best.name },
  };
}

function signauxItem(signals) {
  const ok = signals && signals.winRate != null && Number.isFinite(signals.winRate);
  const alpha = signals && signals.avgAlpha != null ? `, ${formatSigned(signals.avgAlpha)} % vs indice en moyenne` : '';
  return {
    key: 'signaux', label: 'Signaux · perf réelle', href: '#indices/suivi',
    value: ok ? `${Math.round(signals.winRate)} %` : '—',
    detail: ok ? `de réussite${alpha}. ${signals.openCount} en cours.` : HOME_TODAY_MISSING,
    tone: ok ? 'gold' : 'neutral',
    short: { label: 'Signaux', sub: ok ? 'réussite' : 'indisponible' },
  };
}

function botsItem(botOr, botActions, labels) {
  let value = 'Indisponible';
  if (botOr) value = botOr.kill_switch ? 'Or coupé' : (botOr.dry_run ? 'Or en simulation' : 'Or actif');
  const actionsLabel = botActions && botActions.statut ? ((labels || {})[botActions.statut] || botActions.statut) : null;
  return {
    key: 'bots', label: 'Bots · statut', href: '#portefeuille',
    value,
    detail: actionsLabel ? `Bot Actions : ${actionsLabel}` : 'Bot Actions : statut indisponible',
    tone: value === 'Or actif' ? 'gold' : 'neutral',
    short: { label: 'Bots', sub: value },
  };
}

// Toujours 4 éléments, dans l'ordre or, top, signaux, bots.
function buildHomeToday(input) {
  return [
    orItem(input.score),
    topItem(input.companies),
    signauxItem(input.signals),
    botsItem(input.botOr, input.botActions, input.botActionsLabels),
  ];
}

// Version courte d'un élément pour la carte « Aujourd'hui » mobile.
function homeTodayShort(item) {
  return item.short;
}

function homeTagline(companyCount) {
  const nombre = Number.isFinite(companyCount) && companyCount > 0
    ? `${companyCount.toLocaleString('fr-FR')} entreprises mondiales`
    : 'des centaines d\'entreprises mondiales';
  return `L'IA note chaque jour l'or et ${nombre}, repère les points d'entrée et suit ton portefeuille.`;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { formatSigned, computeSignalSummary, buildHomeToday, homeTodayShort, homeTagline };
}
