# gold_bot/ai_shadow.py
# Veto IA sur les signaux d'entree reels -- voir discussion du
# 2026-10-28 : la seule piste "IA sur le trading" jugee assez solide
# pour etre construite. Deux modes selon l'appelant (`real_decision`) :
# FANTOME pour decide_and_act_fibonacci (jamais branche sur la vraie
# decision -- backtest honnete methodologiquement impossible, voir
# docs/superpowers/experiments/*-ia-news-reasoning-test.json pour le
# meme principe applique aux actions) ; REEL pour decide_and_act_ichimoku
# depuis Task 3 du chantier 2026-11-05 (fail-open explicite : seul un
# verdict "opposerait" bloque le trade -- voir le spec de ce chantier).
#
# Appelee quand le moteur appelant a DEJA confirme un vrai signal
# d'entree (direction determinee) -- mais la condition "direction
# determinee" peut rester vraie cycle apres cycle pendant des heures
# tant que le prix reste pres du niveau (meme si un autre garde-fou,
# ex. distance entree-stop trop faible, empeche l'ouverture reelle).
# Incident reel du 2026-10-29 : sans garde-fou de frequence, ça a
# declenche 28 vrais appels factures en une matinee (jusqu'a toutes les
# 2 minutes pendant des heures), tous Opus 5.5 + recherche web + effort
# eleve, 0 succes (chaque appel expirait au bout de 25s) -- cout reel
# sans aucune valeur produite. D'ou le cooldown ci-dessous : au plus un
# appel reel par symbole et par fenetre de SHADOW_COOLDOWN, quel que
# soit le nombre de cycles ou le signal persiste. Ne doit JAMAIS
# pouvoir affecter la vraie decision ni bloquer durablement la boucle
# de trading : toute erreur (reseau, API, timeout, reponse malformee)
# est capturee et journalisee, jamais levee.
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

SHADOW_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_shadow_log.jsonl")
MODEL = "claude-opus-5-5"
MAX_CANDLES_IN_PROMPT = 12
REQUEST_TIMEOUT_SECONDS = 40.0
WEB_SEARCH_MAX_USES = 2
SHADOW_COOLDOWN = timedelta(hours=24)

_last_call_at: dict[tuple[str, str], datetime] = {}  # cle (symbol, engine_label) -- jamais symbol seul
_last_verdict: dict[tuple[str, str], dict] = {}  # dernier verdict REEL (jamais None), reutilise pendant le cooldown
_cooldown_lock = threading.Lock()

_VERDICT_JSON_RE = re.compile(r"\{[^{}]*\"verdict\"[^{}]*\}")


def _describe_candles(candles: list[dict], limit: int = MAX_CANDLES_IN_PROMPT) -> str:
    recent = candles[-limit:]
    lines = []
    for c in recent:
        try:
            lines.append(
                f"{c['time']}: O={c['open']:.2f} H={c['high']:.2f} L={c['low']:.2f} C={c['close']:.2f}"
            )
        except (KeyError, TypeError, ValueError):
            continue
    return "\n".join(lines)


def _build_prompt(symbol: str, direction: str, state: dict, candles: list[dict], *,
                   engine_label: str, real_decision: bool) -> str:
    trend_line = (
        f"force de tendance glissante 20 jours={state.get('trend_strength')}, "
        if state.get("trend_strength") is not None else ""
    )
    consequence_line = (
        "Ceci est une VRAIE decision : si tu juges qu'il faut t'opposer (verdict "
        '"opposerait"), ce trade ne sera PAS execute. Sois honnete et direct, dis '
        "clairement si tu n'as rien trouve de pertinent -- ne t'oppose pas par excès "
        "de prudence sans raison concrete."
        if real_decision else
        "Ceci est un TEST EN MODE FANTOME : ton avis n'affecte AUCUN trade reel, "
        "sois honnete et direct, dis clairement si tu n'as rien trouve de pertinent."
    )
    return (
        f"Un moteur de trading mecanique sur l'or/l'argent ({symbol}) vient de detecter "
        f"un signal d'entree reel ({direction}) sur {engine_label}.\n\n"
        f"Contexte chiffre : niveau={state.get('level')}, cloture={state.get('close')}, "
        f"plus haut={state.get('high')}, plus bas={state.get('low')}, ATR={state.get('atr')}, "
        f"{trend_line}"
        f"heure UTC={state.get('hour_utc')}.\n\n"
        f"Dernieres bougies 5min (plus recente en dernier) :\n{_describe_candles(candles)}\n\n"
        f"Cherche s'il y a une actualite majeure en cours ou tres recente qui pourrait "
        f"expliquer ce mouvement de prix (decision de banque centrale, donnee macro, "
        f"evenement geopolitique). Juge si ce signal ressemble a un vrai rebond technique "
        f"ou a du bruit cause par une actualite -- meme principe que l'incident reel du "
        f"2026-10-28 (un signal technique authentique declenche par une conference de "
        f"presse de la Fed, pas un vrai retournement).\n\n"
        f"{consequence_line}\n\n"
        f"Termine ta reponse par un bloc JSON seul sur sa derniere ligne, exactement sous "
        f'cette forme : {{"verdict": "confirme ou opposerait", "confiance": "faible ou '
        f'moyenne ou haute", "raison": "une phrase"}}'
    )


def _parse_verdict(text: str) -> dict | None:
    matches = _VERDICT_JSON_RE.findall(text)
    if not matches:
        return None
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _append_log(entry: dict, path: str = SHADOW_LOG_PATH) -> None:
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def shadow_judge_entry(symbol: str, direction: str, state: dict, candles: list[dict], *,
                        engine_label: str, real_decision: bool = False,
                        log_path: str = SHADOW_LOG_PATH, now: datetime | None = None) -> dict | None:
    """Appelle Claude en mode FANTOME pour juger un signal d'entree reel
    deja confirme par le moteur mecanique -- ne change jamais la vraie
    decision EN MODE FANTOME, ne doit jamais etre appelee avant que le
    signal soit confirme. Journalise systematiquement (succes, echec,
    ou verdict non parseable) dans `log_path`. Renvoie le verdict
    structure ({"verdict", "confiance", "raison"}) si tout s'est bien
    passe, None si l'appel echoue reellement (API absente, erreur,
    verdict non parseable -- jamais d'exception propagee). Pendant le
    cooldown (voir SHADOW_COOLDOWN), renvoie le DERNIER VERDICT REEL
    obtenu pour ce (symbol, engine_label) plutot que None -- sans ca,
    un signal qui reste confirme cycle apres cycle (le cas normal pour
    un croisement Ichimoku, qui dure toute l'heure de la bougie H1)
    ferait passer un "opposerait" en fail-open dès le cycle suivant,
    1 minute plus tard, annulant le veto reel qu'il venait de poser --
    trouve et reproduit en revue finale de branche, 2026-11-06. Rien
    n'est journalise pour un appel saute, qu'il reutilise un verdict ou
    renvoie None, pour ne pas saturer le journal a chaque cycle."""
    now = now or datetime.now(timezone.utc)
    # Cle (symbol, engine_label), jamais symbol seul : deux moteurs
    # distincts peuvent un jour partager un symbole (ex. XAUUSD sur
    # Fibonacci ET un autre moteur) -- avec une cle symbol seul, un
    # appel fantome consommerait le cooldown d'un vrai veto (ou
    # l'inverse) sans que ce soit visible dans les logs applicatifs.
    # Trouve en re-review de la Task 3 (branchement du premier veto
    # reel, sur Ichimoku/XAGUSD -- pas de collision aujourd'hui avec
    # Fibonacci/XAUUSD, mais l'hypothese implicite devenait une vraie
    # question de securite dès qu'un second consommateur reel existe).
    cooldown_key = (symbol, engine_label)
    with _cooldown_lock:
        last = _last_call_at.get(cooldown_key)
        if last is not None and now - last < SHADOW_COOLDOWN:
            return _last_verdict.get(cooldown_key)
        _last_call_at[cooldown_key] = now
        # Nouvelle fenetre de cooldown : efface l'ancien verdict avant la
        # tentative reelle -- si cette tentative echoue (None), on ne
        # veut jamais rejouer un verdict d'une fenetre precedente par
        # erreur (ex. "opposerait" d'hier qui resterait fige si l'appel
        # du jour timeout).
        _last_verdict.pop(cooldown_key, None)

    entry: dict = {
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": symbol,
        "direction": direction,
        "engine_label": engine_label,
        "real_decision": real_decision,
        "state": state,
    }
    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            entry["error"] = "ANTHROPIC_API_KEY absent"
            _append_log(entry, log_path)
            return None

        # max_retries=0 : le defaut du SDK (2) rejouerait cette meme
        # requete couteuse (Opus + recherche web) jusqu'a 2 fois de plus
        # a chaque timeout -- jusqu'a 3 tentatives reellement facturees
        # pour un seul appel loggue, probablement le plus gros facteur
        # dans l'incident de cout du 2026-10-29 (28 timeouts loggues,
        # potentiellement ~84 tentatives reelles). Un avis fantome rate
        # une fois n'a pas besoin d'etre rejoue ici : le prochain vrai
        # trade (ou demain, apres le cooldown) retentera de lui-meme.
        client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
        response = client.messages.create(
            model=MODEL,
            # 1024 s'est avere trop bas en pratique (incident du
            # 2026-11-05) : la recherche web + le raisonnement interne
            # consomment une partie du budget avant le texte final,
            # coupant parfois le bloc JSON du verdict avant qu'il soit
            # ecrit -- reproduit en direct, usage reel observe ~1600
            # tokens de sortie sur un appel reussi. Marge large plutot
            # qu'un ajustement fin, vu le cout marginal faible face a
            # l'objectif (un verdict exploitable a chaque appel reel,
            # rare -- au plus 1 par symbole/24h).
            max_tokens=4096,
            output_config={"effort": "medium"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES}],
            messages=[{"role": "user", "content": _build_prompt(
                symbol, direction, state, candles, engine_label=engine_label, real_decision=real_decision,
            )}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        verdict = _parse_verdict(text)
        entry["raw_text"] = text
        entry["verdict"] = verdict
        if verdict is None:
            entry["error"] = "verdict JSON non trouve ou invalide dans la reponse"
        else:
            with _cooldown_lock:
                _last_verdict[cooldown_key] = verdict
        _append_log(entry, log_path)
        return verdict
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {e}"
        _append_log(entry, log_path)
        return None
