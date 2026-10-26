# gold_bot/ftmo_guard.py
# Garde-fou FTMO 2-Step, distinct du CircuitBreaker (shared_risk.py) --
# sémantique différente, voir docs/superpowers/research/gold_bot_ftmo_check/
# ftmo_simulator.py (recherche qui a vérifié ces règles officiellement sur
# ftmo.com/en/trading-objectives/ le 2026-10-26) :
# - jour calendaire CET/CEST (pas UTC comme CircuitBreaker) ;
# - plancher QUOTIDIEN, rebasé chaque jour CET sur le SOLDE (balance, pas
#   équité) constaté à ce moment-là ;
# - plancher GLOBAL, fixé une seule fois à la construction sur l'ISC
#   (capital de départ) et qui ne bouge JAMAIS -- contrairement au
#   plancher quotidien, aucun rebaselining ;
# - les deux planchers comparent l'ÉQUITÉ courante (balance + P&L
#   flottant), pas le solde -- règle officielle FTMO : une position
#   ouverte en perte doit pouvoir déclencher le garde-fou avant sa
#   clôture, exactement comme CircuitBreaker (voir broker.py et
#   bot.py:40-45).
# N'importe pas shared_risk.py : ce module est un concern gold_bot
# uniquement (FTMO ne concerne pas ibkr_bot) -- _load_state/_save_state
# ci-dessous sont une copie volontaire du même petit utilitaire atomique
# que shared_risk.py, même convention que cette dernière documente déjà
# pour sa propre raison de ne dépendre d'aucun package bot.
import json
import os
from datetime import date, datetime, timedelta, timezone

# CET = UTC+1 toute l'année ici -- ne distingue PAS le passage à l'heure
# d'été CEST (UTC+2). Même limite, documentée de la même façon, que
# docs/superpowers/research/gold_bot_ftmo_check/ftmo_simulator.py::
# CET_OFFSET_HOURS -- pas corrigée ici non plus : une marge de sécurité
# de ±1h sur l'heure de rollover du jour FTMO existe déjà par ailleurs
# via la marge de sécurité de 1 point sous les seuils officiels (voir
# DEFAULT_DAILY_LOSS_PCT/DEFAULT_OVERALL_LOSS_PCT ci-dessous).
CET_OFFSET_HOURS = 1

# Marge de sécurité de 1 point sous les seuils OFFICIELS FTMO (5%
# quotidien / 10% global) -- validée explicitement par l'utilisateur
# le 2026-10-26 pour absorber la latence du bot (un contrôle par cycle,
# ~60s) et le slippage d'exécution réel. CE NE SONT PAS les seuils
# officiels eux-mêmes -- voir ftmo_simulator.py pour ceux-ci.
DEFAULT_DAILY_LOSS_PCT = 0.04
DEFAULT_OVERALL_LOSS_PCT = 0.09

DEFAULT_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ftmo_guard_state.json")


def _cet_day(ts_utc: datetime) -> date:
    return (ts_utc + timedelta(hours=CET_OFFSET_HOURS)).date()


def _load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(data: dict, path: str) -> None:
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


class FtmoGuard(object):
    """Garde-fou FTMO 2-Step. `check(balance, equity)` renvoie "daily",
    "overall", ou None. Une fois déclenché un jour CET donné, reste
    déclenché (et renvoie la MÊME raison) pour le reste de ce jour CET,
    même si l'équité remonte entre deux appels -- même contrat que
    CircuitBreaker._tripped_today (shared_risk.py), pour la même raison :
    un verdict ne doit pas se dé-déclencher silencieusement.

    Si les deux planchers sont franchis au même appel, "overall" est
    rapporté en priorité -- décision prise ici (non spécifiée par
    l'utilisateur) : un franchissement global signifie que l'évaluation
    entière est perdue, une information plus grave et plus actionnable
    que "juste" la journée en cours. Dans les deux cas l'appelant doit de
    toute façon fermer toutes les positions et armer le kill_switch --
    cette priorité n'affecte que le libellé du log, jamais l'action.

    Limite assumée du plancher quotidien : la règle officielle FTMO le
    base sur le solde "à 00:00 CET" exactement, mais ce garde-fou n'est
    interrogé qu'une fois par cycle (~60s, voir gold_bot/loop.py) -- le
    solde utilisé pour rebaser le plancher est donc celui constaté au
    PREMIER appel après le passage au jour CET suivant, pas celui qui
    existait exactement à minuit CET. Écart borné par l'intervalle entre
    deux cycles, dans l'esprit de la marge de sécurité déjà appliquée aux
    seuils eux-mêmes."""

    def __init__(self, isc: float, daily_loss_pct: float = DEFAULT_DAILY_LOSS_PCT,
                 overall_loss_pct: float = DEFAULT_OVERALL_LOSS_PCT, now_fn=None, persist_path=None):
        if isc <= 0:
            raise ValueError("isc (capital de départ) doit être positif")
        if not 0 < daily_loss_pct <= 1:
            raise ValueError("daily_loss_pct doit être compris entre 0 (exclu) et 1 (inclus)")
        if not 0 < overall_loss_pct <= 1:
            raise ValueError("overall_loss_pct doit être compris entre 0 (exclu) et 1 (inclus)")
        self.isc = isc
        self.daily_loss_pct = daily_loss_pct
        self.overall_loss_pct = overall_loss_pct
        self._overall_floor = isc * (1.0 - overall_loss_pct)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._persist_path = persist_path
        self._day = None
        self._daily_floor = None
        self._tripped_today = False
        self._tripped_reason = None
        if persist_path is not None:
            saved = _load_state(persist_path)
            saved_day = saved.get("ftmo_guard_day")
            saved_floor = saved.get("ftmo_guard_daily_floor")
            if saved_day is not None and saved_floor is not None:
                try:
                    self._day = date.fromisoformat(saved_day)
                    self._daily_floor = float(saved_floor)
                    self._tripped_today = bool(saved.get("ftmo_guard_tripped_today", False))
                    self._tripped_reason = saved.get("ftmo_guard_tripped_reason")
                except (ValueError, TypeError):
                    self._day = None
                    self._daily_floor = None
                    self._tripped_today = False
                    self._tripped_reason = None

    @property
    def overall_floor(self) -> float:
        return self._overall_floor

    def _persist(self) -> None:
        if self._persist_path is None:
            return
        _save_state(
            {
                "ftmo_guard_day": self._day.isoformat() if self._day is not None else None,
                "ftmo_guard_daily_floor": self._daily_floor,
                "ftmo_guard_tripped_today": self._tripped_today,
                "ftmo_guard_tripped_reason": self._tripped_reason,
            },
            self._persist_path,
        )

    def check(self, balance: float, equity: float) -> str | None:
        today = _cet_day(self._now_fn())
        if self._day != today:
            self._day = today
            self._daily_floor = balance * (1.0 - self.daily_loss_pct)
            self._tripped_today = False
            self._tripped_reason = None
            self._persist()
        if self._tripped_today:
            return self._tripped_reason
        if equity <= self._overall_floor:
            reason = "overall"
        elif equity <= self._daily_floor:
            reason = "daily"
        else:
            return None
        self._tripped_today = True
        self._tripped_reason = reason
        self._persist()
        return reason
