# gold_bot/ai_discretion.py
# Decision IA discretionnaire sur l'entree Fibonacci (compte FTMO reel)
# -- voir docs/superpowers/specs/2026-11-13-gold-bot-ai-discretionary-
# fibonacci-entry-design.md. Remplace le veto binaire de ai_shadow.py
# (qui ne peut qu'accepter/bloquer le sens deja choisi par les regles) :
# ici l'IA choisit elle-meme achat/vente/rien, SANS JAMAIS recevoir le
# sens que les regles auraient choisi (voir _build_prompt, qui n'a
# aucun parametre direction/bias) -- c'est ce qui aurait pu empecher
# l'incident du 2026-11-06 (vente a contre-tendance dans un marche en V).
# decide_and_act_ichimoku garde son propre veto ai_shadow, inchange :
# ce module ne concerne que decide_and_act_fibonacci.
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

DISCRETION_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_discretion_log.jsonl")
MODEL = "claude-opus-5-5"
MAX_CANDLES_IN_PROMPT = 12
REQUEST_TIMEOUT_SECONDS = 40.0
WEB_SEARCH_MAX_USES = 2
MAX_TOKENS = 1536
# Anti-rafale (eviter un enchainement rapide de decisions si le marche
# touche plusieurs niveaux coup sur coup) -- PAS un garde-fou de cout
# comme les 24h de ai_shadow.SHADOW_COOLDOWN : le declencheur Fibonacci
# est deja naturellement rare (~4-10x/semaine).
DISCRETION_COOLDOWN = timedelta(hours=4)

# Bornes dures, appliquees apres coup, independantes de ce que dit l'IA
# -- voir la spec, section "Garde-fous".
TP_MULTIPLIER_BOUNDS = (0.6, 2.0)
SL_MULTIPLIER_BOUNDS = (0.7, 1.3)
MIN_RISK_REWARD = 1.0

_VALID_ACTIONS = {"achat", "vente", "rien"}
_ACTION_JSON_RE = re.compile(r"\{[^{}]*\"action\"[^{}]*\}")

_last_call_at: dict[tuple, datetime] = {}
_cooldown_lock = threading.Lock()


def clamp_multipliers(tp_multiplicateur: float, sl_multiplicateur: float) -> tuple[float, float]:
    """Borne les deux multiplicateurs independamment a leurs bornes
    respectives -- TP_MULTIPLIER_BOUNDS/SL_MULTIPLIER_BOUNDS."""
    tp = min(max(tp_multiplicateur, TP_MULTIPLIER_BOUNDS[0]), TP_MULTIPLIER_BOUNDS[1])
    sl = min(max(sl_multiplicateur, SL_MULTIPLIER_BOUNDS[0]), SL_MULTIPLIER_BOUNDS[1])
    return tp, sl


def resolve_risk_reward(baseline_take_profit_distance: float, baseline_stop_loss_distance: float,
                         tp_multiplicateur: float, sl_multiplicateur: float) -> dict:
    """Applique les multiplicateurs (bornes via clamp_multipliers) aux
    distances de reference des regles, calcule le R:R resultant. Renvoie
    toujours un dict complet, meme quand "accepted" est False -- la
    distance ajustee reste utile pour le journal meme quand le trade est
    rejete (comparaison a posteriori)."""
    tp_mult, sl_mult = clamp_multipliers(tp_multiplicateur, sl_multiplicateur)
    take_profit_distance = baseline_take_profit_distance * tp_mult
    stop_loss_distance = baseline_stop_loss_distance * sl_mult
    risk_reward = take_profit_distance / stop_loss_distance if stop_loss_distance > 0 else 0.0
    return {
        "take_profit_distance": take_profit_distance,
        "stop_loss_distance": stop_loss_distance,
        "risk_reward": risk_reward,
        "accepted": risk_reward >= MIN_RISK_REWARD,
    }


def _parse_discretion_verdict(text: str) -> dict | None:
    """Meme convention que ai_shadow._parse_verdict (regex sur le
    dernier bloc JSON contenant la cle attendue, normalisation
    case/espaces) -- mais la cle pivot est "action", pas "verdict", et
    un "achat"/"vente" sans tp_multiplicateur/sl_multiplicateur
    numerique (ou avec un booleen, piege Python ou bool est une
    sous-classe de int) est traite comme invalide : on ne devine jamais
    une valeur manquante."""
    matches = _ACTION_JSON_RE.findall(text)
    if not matches:
        return None
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    for key in ("action", "confiance"):
        value = parsed.get(key)
        if isinstance(value, str):
            parsed[key] = value.strip().lower()
    action = parsed.get("action")
    if action not in _VALID_ACTIONS:
        return None
    if action in ("achat", "vente"):
        for mult_key in ("tp_multiplicateur", "sl_multiplicateur"):
            value = parsed.get(mult_key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
    return parsed


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


def _format_buffer_pct(value: float | None) -> str:
    return f"{value * 100:.2f}%" if value is not None else "inconnu"


def _build_prompt(symbol: str, *, level: float, close: float, high: float, low: float, atr: float,
                   trend_strength: float | None, trend_strength_short: float | None, hour_utc: int,
                   candles: list[dict], baseline_stop_loss_distance: float,
                   baseline_take_profit_distance: float, ftmo_buffer: dict) -> str:
    """AUCUN parametre direction/bias -- garantie structurelle que le
    sens que les regles auraient choisi ne peut pas fuiter dans le
    prompt (voir test_build_prompt_signature_has_no_direction_or_bias_
    parameter). Les distances TP/SL de reference sont signees sans
    direction (des valeurs absolues de distance), donc ne fuitent rien
    non plus."""
    trend_line = (
        f"force de tendance glissante 20 jours={trend_strength}, " if trend_strength is not None else ""
    )
    trend_short_line = (
        f"force de tendance glissante 5 jours={trend_strength_short}, "
        if trend_strength_short is not None else ""
    )
    return (
        f"Un moteur de trading mecanique sur l'or ({symbol}) vient de detecter qu'un "
        f"niveau de retracement de Fibonacci a ete touche et rejete. Contrairement a "
        f"d'habitude, ce n'est PAS un signal avec un sens deja determine : c'est a TOI "
        f"de decider s'il faut acheter, vendre, ou ne rien faire du tout, uniquement a "
        f"partir du contexte brut ci-dessous.\n\n"
        f"Contexte chiffre : niveau={level}, cloture={close}, plus haut={high}, "
        f"plus bas={low}, ATR={atr}, {trend_line}{trend_short_line}heure UTC={hour_utc}.\n\n"
        f"Dernieres bougies 5min (plus recente en dernier) :\n{_describe_candles(candles)}\n\n"
        f"Marge restante avant les planchers de perte FTMO : "
        f"{_format_buffer_pct(ftmo_buffer.get('daily_pct_remaining'))} (journalier), "
        f"{_format_buffer_pct(ftmo_buffer.get('overall_pct_remaining'))} (global). "
        f"Plus cette marge est faible, plus la prudence doit l'etre.\n\n"
        f"Un trade mecanique prendrait un stop a une distance de reference de "
        f"{baseline_stop_loss_distance:.2f} et un objectif a {baseline_take_profit_distance:.2f} "
        f"(sans indication de sens). Tu peux ajuster ces distances via des multiplicateurs "
        f"si le contexte le justifie (volatilite, actualite).\n\n"
        f"Cherche s'il y a une actualite majeure en cours ou tres recente qui pourrait "
        f"expliquer ce mouvement de prix (decision de banque centrale, donnee macro, "
        f"evenement geopolitique).\n\n"
        f"Ceci est une VRAIE decision : le trade qui sera reellement ouvert est celui que "
        f"TU choisis. Si tu ne vois rien d'exploitable, choisis \"rien\" sans etat d'ame.\n\n"
        f"Termine ta reponse par un bloc JSON seul sur sa derniere ligne, exactement sous "
        f'cette forme : {{"action": "achat ou vente ou rien", "confiance": "faible ou '
        f'moyenne ou haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, '
        f'"raison": "une phrase"}}'
    )


def _append_log(entry: dict, path: str = DISCRETION_LOG_PATH) -> None:
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def judge_fibonacci_entry(
    symbol: str, state: dict, candles: list[dict], *,
    baseline_stop_loss_distance: float, baseline_take_profit_distance: float,
    ftmo_buffer: dict, log_path: str = DISCRETION_LOG_PATH, now: datetime | None = None,
) -> dict | None:
    """Decide elle-meme le sens d'entree (achat/vente/rien) sur un
    signal Fibonacci deja confirme par les regles -- SANS jamais
    recevoir le sens que les regles auraient choisi (voir _build_prompt,
    aucun parametre direction/bias). Renvoie un dict {"action",
    "confiance", "stop_loss_distance", "take_profit_distance", "raison"}
    UNIQUEMENT quand un trade doit reellement s'ouvrir (appel reussi,
    verdict valide, action != "rien", garde-fou R:R accepte) ; None dans
    tous les autres cas (echec API, verdict invalide, action "rien",
    garde-fou R:R rejete, cooldown actif) -- l'appelant traite toujours
    None comme "aucun trade ce cycle", sans jamais se rabattre sur le
    sens des regles. Jamais d'exception propagee."""
    now = now or datetime.now(timezone.utc)
    level = state.get("level")
    cooldown_key = (symbol, round(level, 2) if level is not None else None)
    with _cooldown_lock:
        last = _last_call_at.get(cooldown_key)
        if last is not None and now - last < DISCRETION_COOLDOWN:
            return None
        _last_call_at[cooldown_key] = now

    entry: dict = {
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": symbol,
        "state": state,
        "ftmo_buffer": ftmo_buffer,
        "regle_contrefactuelle": {
            "direction": state.get("bias"),
            "stop_loss_distance": baseline_stop_loss_distance,
            "take_profit_distance": baseline_take_profit_distance,
        },
    }
    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            entry["error"] = "ANTHROPIC_API_KEY absent"
            entry["action_reelle"] = "erreur"
            _append_log(entry, log_path)
            return None

        client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            output_config={"effort": "high"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES}],
            messages=[{"role": "user", "content": _build_prompt(
                symbol,
                level=state.get("level"), close=state.get("close"), high=state.get("high"),
                low=state.get("low"), atr=state.get("atr"),
                trend_strength=state.get("trend_strength"), trend_strength_short=state.get("trend_strength_short"),
                hour_utc=state.get("hour_utc"), candles=candles,
                baseline_stop_loss_distance=baseline_stop_loss_distance,
                baseline_take_profit_distance=baseline_take_profit_distance, ftmo_buffer=ftmo_buffer,
            )}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        verdict = _parse_discretion_verdict(text)
        entry["raw_text"] = text
        entry["verdict"] = verdict
        if verdict is None:
            entry["error"] = "verdict JSON non trouve ou invalide dans la reponse"
            entry["action_reelle"] = "erreur"
            _append_log(entry, log_path)
            return None

        if verdict["action"] == "rien":
            entry["action_reelle"] = "aucune_ia"
            _append_log(entry, log_path)
            return None

        resolved = resolve_risk_reward(
            baseline_take_profit_distance, baseline_stop_loss_distance,
            verdict.get("tp_multiplicateur"), verdict.get("sl_multiplicateur"),
        )
        entry["resolved"] = resolved
        if not resolved["accepted"]:
            entry["action_reelle"] = "rejete_garde_fou_rr"
            _append_log(entry, log_path)
            return None

        entry["action_reelle"] = "execute"
        _append_log(entry, log_path)
        return {
            "action": verdict["action"],
            "confiance": verdict.get("confiance"),
            "stop_loss_distance": resolved["stop_loss_distance"],
            "take_profit_distance": resolved["take_profit_distance"],
            "raison": verdict.get("raison"),
        }
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {e}"
        entry["action_reelle"] = "erreur"
        _append_log(entry, log_path)
        return None
