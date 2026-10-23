# shared_risk.py
# Coupe-circuit journalier partage entre gold_bot et ibkr_bot -- extrait de
# gold_bot/risk.py (2026-10-23) pour que les deux bots utilisent la MEME
# classe plutot que deux implementations paralleles. gold_bot/risk.py
# re-exporte CircuitBreaker depuis ce module ; tout le reste de risk.py
# (dimensionnement MT5, profils de risque) reste propre a gold_bot.
#
# N'importe NI gold_bot.state NI ibkr_bot.state : les deux sont des copies
# conformes l'une de l'autre (meme interface load_state/save_state), et ce
# module ne doit dependre d'AUCUN des deux packages bots -- sinon l'un
# dependrait indirectement de l'autre pour une simple lecture/ecriture JSON
# generique. _load_state/_save_state ci-dessous sont une troisieme copie
# volontaire de cette meme logique (meme choix que
# ibkr_bot/daily.py::_prix_reference_absent, qui duplique plutot que
# d'importer un symbole prive d'un autre module).
import json
import os
from datetime import date, datetime, timezone


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


class CircuitBreaker:
    """Coupe-circuit journalier : bloque l'ouverture de nouvelles
    positions si la perte cumulée du jour dépasse threshold_pct du solde
    fixé au début de la journée UTC courante. Ne ferme jamais de
    position existante — seul un filtre sur can_open_position().
    `now_fn` est injectable pour les tests (horloge fixe/contrôlable).
    `persist_path`, si fourni, persiste {day, starting_balance,
    tripped_today} — sans lui, l'état reste en mémoire uniquement (perdu
    au redémarrage), ce qui suffit pour les tests mais pas en
    production, où un redémarrage ne doit pas réinitialiser le seuil du
    jour au solde courant.

    `threshold_pct` est un attribut public mutable, en place par
    l'appelant (gold_bot.loop.run_cycle relit le profil de risque actif
    à chaque cycle). Sans le flag `_tripped_today` ci-dessous, relever le
    profil (donc `threshold_pct`) après un déclenchement du coupe-circuit
    le même jour UTC le dé-déclencherait silencieusement, puisque
    can_open_position() recompare toujours la perte au NOUVEAU seuil,
    plus large. `_tripped_today` fige le verdict "déclenché" pour le
    reste de la journée UTC, indépendamment de tout changement ultérieur
    de threshold_pct — il ne se réinitialise qu'au même moment que
    `_starting_balance`/`_day`, au rollover de jour.

    `check()`/`can_open_position()` supposent plusieurs vérifications
    par jour UTC — voir can_open_position_daily() pour un appelant qui
    ne vérifie qu'une fois par jour (ex. un batch quotidien)."""

    def __init__(self, threshold_pct: float = 0.10, now_fn=None, persist_path=None,
                 max_trades_per_day: int | None = None):
        if not 0 < threshold_pct <= 1:
            raise ValueError("threshold_pct doit être compris entre 0 (exclu) et 1 (inclus)")
        self.threshold_pct = threshold_pct
        self.max_trades_per_day = max_trades_per_day
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._persist_path = persist_path
        self._day = None
        self._starting_balance = None
        self._tripped_today = False
        self._trades_opened_today = 0
        if persist_path is not None:
            saved = _load_state(persist_path)
            saved_day = saved.get("circuit_breaker_day")
            saved_balance = saved.get("circuit_breaker_starting_balance")
            if saved_day is not None and saved_balance is not None:
                try:
                    self._day = date.fromisoformat(saved_day)
                    self._starting_balance = float(saved_balance)
                    self._tripped_today = bool(saved.get("circuit_breaker_tripped_today", False))
                    self._trades_opened_today = int(saved.get("circuit_breaker_trades_opened_today", 0))
                except (ValueError, TypeError):
                    self._day = None
                    self._starting_balance = None
                    self._tripped_today = False
                    self._trades_opened_today = 0

    def check(self, current_balance: float) -> None:
        """À appeler avant toute décision — fixe le solde de référence
        du jour s'il n'existe pas encore, si on a changé de jour UTC, OU
        si le solde de référence actuellement retenu n'est pas positif
        alors que le solde courant l'est (corrige un compte pas encore
        financé au premier cycle du jour). Réinitialise aussi le flag de
        déclenchement dans les deux cas."""
        today = self._now_fn().date()
        needs_new_baseline = self._day != today or (
            self._starting_balance is not None and self._starting_balance <= 0 and current_balance > 0
        )
        if needs_new_baseline:
            self._day = today
            self._starting_balance = current_balance
            self._tripped_today = False
            self._trades_opened_today = 0
            self._persist()

    def _persist(self) -> None:
        if self._persist_path is None:
            return
        _save_state(
            {
                "circuit_breaker_day": self._day.isoformat() if self._day is not None else None,
                "circuit_breaker_starting_balance": self._starting_balance,
                "circuit_breaker_tripped_today": self._tripped_today,
                "circuit_breaker_trades_opened_today": self._trades_opened_today,
            },
            self._persist_path,
        )

    def record_trade_opened(self) -> None:
        """À appeler une fois par trade réellement ouvert (une seule
        fois même si l'ouverture se scinde en plusieurs ordres broker).
        Ne vérifie ni ne déclenche le plafond elle-même."""
        self._trades_opened_today += 1
        if self._day is None:
            return
        self._persist()

    def can_open_position(self, current_balance: float) -> bool:
        self.check(current_balance)
        if self._tripped_today:
            return False
        if self.max_trades_per_day is not None and self._trades_opened_today >= self.max_trades_per_day:
            self._tripped_today = True
            self._persist()
            return False
        loss = self._starting_balance - current_balance
        if loss < self._starting_balance * self.threshold_pct:
            return True
        self._tripped_today = True
        self._persist()
        return False
