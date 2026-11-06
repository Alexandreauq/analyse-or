# gold_bot/ai_news_watch.py
# Jugement IA sur le CALME du marche apres la fin d'une fenetre de
# blackout calendaire (voir gold_bot/confluence.py::minutes_since_blackout_ended)
# -- responsabilite distincte du veto d'entree gold_bot/ai_shadow.py
# (qui juge un TRADE precis, pas l'etat general du marche). Branche
# pour de vrai depuis sa creation (decide_and_act_fibonacci) : seul un
# verdict "encore_agite" etend le blackout, "calme"/None (echec reel,
# API absente, timeout, verdict non parseable) laissent le trade se
# faire normalement -- JAMAIS de raccourcissement d'une fenetre de
# blackout deja active, uniquement une extension apres sa fin. Trouve
# le 2026-11-06 : la vente perdante de 13h55 UTC ce jour-la est passee
# 10 minutes apres la fin du blackout NFP (fenetre fixe de 15min),
# pendant que le marche digerait encore le choc -- meme patron
# fail-open/cooldown/log que ai_shadow.shadow_judge_entry (voir ce
# fichier), jamais d'exception propagee, cooldown pour eviter un cout
# repete si plusieurs cycles tombent dans la meme fenetre grise.
from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from datetime import datetime, timedelta, timezone

WATCH_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_news_watch_log.jsonl")
MODEL = "claude-opus-5-5"
MAX_CANDLES_IN_PROMPT = 12
REQUEST_TIMEOUT_SECONDS = 40.0
WEB_SEARCH_MAX_USES = 2
WATCH_COOLDOWN = timedelta(hours=24)

_last_call_at: dict[tuple[str, datetime], datetime] = {}  # cle (symbol, window_end) -- un cooldown distinct PAR EVENEMENT, pas par symbole seul (deux evenements a moins de 24h d'ecart ne doivent jamais partager un verdict)
_last_verdict: dict[tuple[str, datetime], dict] = {}
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


def _build_prompt(symbol: str, candles: list[dict], minutes_since_blackout: float) -> str:
    return (
        f"Une fenetre de blackout calendaire (publication macro a fort impact) sur {symbol} "
        f"s'est terminee il y a {minutes_since_blackout:.0f} minutes.\n\n"
        f"Dernieres bougies 5min (plus recente en dernier) :\n{_describe_candles(candles)}\n\n"
        f"Cherche s'il y a une actualite recente qui pourrait expliquer un mouvement de prix "
        f"inhabituel, et juge si le marche semble avoir reellement digere cette actualite, ou "
        f"s'il montre encore des signes de bruit/volatilite anormale qui justifieraient de "
        f"prolonger la prudence au-dela de la fenetre fixe deja ecoulee.\n\n"
        f"Ceci est une VRAIE decision : si tu juges que le marche est encore agite (verdict "
        f'"encore_agite"), un signal d\'entree reel sera bloque. Si tu juges que le calme est '
        f"revenu (verdict \"calme\"), ou si tu n'as rien trouve de pertinent, le trade se fera "
        f"normalement -- ne t'oppose pas par exces de prudence sans raison concrete.\n\n"
        f"Termine ta reponse par un bloc JSON seul sur sa derniere ligne, exactement sous "
        f'cette forme : {{"verdict": "calme ou encore_agite", "confiance": "faible ou '
        f'moyenne ou haute", "raison": "une phrase"}}'
    )


_VALID_VERDICTS = {"calme", "encore_agite"}


def _normalize_verdict_token(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    decomposed = unicodedata.normalize("NFKD", raw.strip().lower())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_accents.replace(" ", "_")


def _parse_verdict(text: str) -> dict | None:
    matches = _VERDICT_JSON_RE.findall(text)
    if not matches:
        return None
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    normalized = _normalize_verdict_token(parsed.get("verdict"))
    if normalized not in _VALID_VERDICTS:
        return None
    parsed["verdict"] = normalized
    return parsed


def _append_log(entry: dict, path: str = WATCH_LOG_PATH) -> None:
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def judge_post_news_calm(symbol: str, candles: list[dict], minutes_since_blackout: float,
                          window_end: datetime, *,
                          log_path: str = WATCH_LOG_PATH, now: datetime | None = None) -> dict | None:
    """Appelle Claude pour juger si le marche a reellement digere
    l'actualite dont la fenetre de blackout calendaire vient de se
    terminer (voir confluence.minutes_since_blackout_ended). Renvoie
    {"verdict": "calme" ou "encore_agite", "confiance", "raison"} si
    l'appel reussit et produit un verdict exploitable, None dans tous
    les autres cas (API absente, erreur, timeout, verdict non
    parseable -- jamais d'exception propagee). Pendant le cooldown
    (WATCH_COOLDOWN), renvoie le dernier verdict REEL obtenu pour CET
    EVENEMENT (meme symbole ET meme window_end) plutot que None, meme
    raison que ai_shadow.shadow_judge_entry (eviter qu'un verdict
    "encore_agite" soit annule en fail-open au cycle suivant). Cooldown
    par (symbol, window_end) -- window_end identifie l'evenement de
    facon exacte et stable pendant toute sa fenetre grise, deux
    evenements distincts (meme proches dans le temps) ont des
    window_end differents et ne partagent donc jamais de cache."""
    now = now or datetime.now(timezone.utc)
    key = (symbol, window_end)
    with _cooldown_lock:
        last = _last_call_at.get(key)
        if last is not None and now - last < WATCH_COOLDOWN:
            return _last_verdict.get(key)
        _last_call_at[key] = now
        _last_verdict.pop(key, None)

    entry: dict = {
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": symbol,
        "minutes_since_blackout": minutes_since_blackout,
    }
    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            entry["error"] = "ANTHROPIC_API_KEY absent"
            _append_log(entry, log_path)
            return None

        client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            output_config={"effort": "medium"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES}],
            messages=[{"role": "user", "content": _build_prompt(symbol, candles, minutes_since_blackout)}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        verdict = _parse_verdict(text)
        entry["raw_text"] = text
        entry["verdict"] = verdict
        if verdict is None:
            entry["error"] = "verdict JSON non trouve ou invalide dans la reponse"
        else:
            with _cooldown_lock:
                _last_verdict[key] = verdict
        _append_log(entry, log_path)
        return verdict
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {e}"
        _append_log(entry, log_path)
        return None
