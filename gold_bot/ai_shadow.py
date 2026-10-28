# gold_bot/ai_shadow.py
# Veto IA en mode FANTOME sur les signaux d'entree Fibonacci -- voir
# discussion du 2026-10-28 : la seule piste "IA sur le trading" jugee
# assez solide pour etre construite, et UNIQUEMENT en mode fantome
# (jamais branchee sur une vraie decision tant qu'elle n'a pas fait ses
# preuves sur plusieurs semaines en conditions reelles -- un backtest
# honnete de ce genre de raisonnement est methodologiquement impossible,
# voir docs/superpowers/experiments/*-ia-news-reasoning-test.json pour
# le meme principe applique aux actions).
#
# Appelee uniquement quand decide_and_act_fibonacci a DEJA confirme un
# vrai signal d'entree (direction determinee) -- jamais a chaque cycle,
# pour rester rare et peu couteux (quelques fois par semaine d'apres
# l'activite reelle du bot). Ne doit JAMAIS pouvoir affecter la vraie
# decision ni bloquer la boucle de trading : toute erreur (reseau, API,
# timeout, reponse malformee) est capturee et journalisee, jamais levee.
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

SHADOW_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_shadow_log.jsonl")
MODEL = "claude-opus-5-5"
MAX_CANDLES_IN_PROMPT = 12
REQUEST_TIMEOUT_SECONDS = 25.0
WEB_SEARCH_MAX_USES = 3

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


def _build_prompt(symbol: str, direction: str, state: dict, candles: list[dict]) -> str:
    return (
        f"Un moteur de trading mecanique sur l'or ({symbol}) vient de detecter un signal "
        f"d'entree reel ({direction}) sur un rebond de retracement de Fibonacci.\n\n"
        f"Contexte chiffre : niveau={state.get('level')}, cloture={state.get('close')}, "
        f"plus haut={state.get('high')}, plus bas={state.get('low')}, ATR={state.get('atr')}, "
        f"force de tendance glissante 20 jours={state.get('trend_strength')}, "
        f"heure UTC={state.get('hour_utc')}.\n\n"
        f"Dernieres bougies 5min (plus recente en dernier) :\n{_describe_candles(candles)}\n\n"
        f"Cherche s'il y a une actualite majeure en cours ou tres recente qui pourrait "
        f"expliquer ce mouvement de prix (decision de banque centrale, donnee macro, "
        f"evenement geopolitique). Juge si ce signal ressemble a un vrai rebond technique "
        f"ou a du bruit cause par une actualite -- meme principe que l'incident reel du "
        f"2026-10-28 (un signal technique authentique declenche par une conference de "
        f"presse de la Fed, pas un vrai retournement).\n\n"
        f"Ceci est un TEST EN MODE FANTOME : ton avis n'affecte AUCUN trade reel, "
        f"sois honnete et direct, dis clairement si tu n'as rien trouve de pertinent.\n\n"
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


def shadow_judge_entry(symbol: str, direction: str, state: dict, candles: list[dict],
                        log_path: str = SHADOW_LOG_PATH) -> dict | None:
    """Appelle Claude en mode FANTOME pour juger un signal d'entree reel
    deja confirme par le moteur mecanique -- ne change jamais la vraie
    decision, ne doit jamais etre appelee avant que le signal soit
    confirme. Journalise systematiquement (succes, echec, ou verdict
    non parseable) dans `log_path`. Renvoie le verdict structure
    ({"verdict", "confiance", "raison"}) si tout s'est bien passe,
    None dans tous les autres cas (jamais d'exception propagee)."""
    entry: dict = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": symbol,
        "direction": direction,
        "state": state,
    }
    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            entry["error"] = "ANTHROPIC_API_KEY absent"
            _append_log(entry, log_path)
            return None

        client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            output_config={"effort": "high"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES}],
            messages=[{"role": "user", "content": _build_prompt(symbol, direction, state, candles)}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        verdict = _parse_verdict(text)
        entry["raw_text"] = text
        entry["verdict"] = verdict
        if verdict is None:
            entry["error"] = "verdict JSON non trouve ou invalide dans la reponse"
        _append_log(entry, log_path)
        return verdict
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {e}"
        _append_log(entry, log_path)
        return None
