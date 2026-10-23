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
    tripped_today} localement — sans lui, l'état reste en mémoire
    uniquement (perdu au redémarrage), ce qui suffit pour les tests
    mais pas en production, où un redémarrage ne doit pas réinitialiser
    le seuil du jour au solde courant.

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
        self._last_balance = None
        # Verrou intra-jour dedie a can_open_position_daily() (Fix 3, revue
        # finale de branche) -- champs INDEPENDANTS de _day/_starting_balance/
        # _tripped_today ci-dessus, qui appartiennent exclusivement a
        # check()/can_open_position() (un patron d'appel different, voir
        # leur docstring). Jamais touches ici : voir can_open_position_daily()
        # pour le detail du verrou lui-meme.
        self._daily_ref_day = None
        self._daily_ref_balance = None
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
            saved_last_balance = saved.get("circuit_breaker_last_balance")
            if saved_last_balance is not None:
                try:
                    self._last_balance = float(saved_last_balance)
                except (TypeError, ValueError):
                    self._last_balance = None
            # Meme chargement defensif que _last_balance ci-dessus, dans son
            # propre try/except independant du garde saved_day/saved_balance
            # utilise par check() plus haut (Fix 3, revue finale de branche).
            saved_daily_ref_day = saved.get("circuit_breaker_daily_ref_day")
            saved_daily_ref_balance = saved.get("circuit_breaker_daily_ref_balance")
            if saved_daily_ref_day is not None and saved_daily_ref_balance is not None:
                try:
                    self._daily_ref_day = date.fromisoformat(saved_daily_ref_day)
                    self._daily_ref_balance = float(saved_daily_ref_balance)
                except (TypeError, ValueError):
                    self._daily_ref_day = None
                    self._daily_ref_balance = None

    def check(self, current_balance: float) -> None:
        """À appeler avant toute décision — fixe le solde de référence
        du jour s'il n'existe pas encore, si on a changé de jour UTC, OU
        si le solde de référence actuellement retenu n'est pas positif
        alors que le solde courant l'est. Ce dernier cas corrige un bug
        trouvé le 2026-09-29 (état réel constaté sur le VPS : compte pas
        encore financé au premier cycle du jour, solde de référence figé
        à 0) : sans lui, un compte financé EN COURS de journée UTC après
        un premier contrôle à solde nul gardait indéfiniment un solde de
        référence de 0 jusqu'au rollover du lendemain — can_open_position
        calcule alors `loss = 0 - current_balance`, toujours négatif donc
        toujours sous le seuil, désactivant silencieusement le
        coupe-circuit pour le reste de la journée quelle que soit
        l'ampleur réelle d'une perte. Réinitialise aussi le flag de
        déclenchement dans les deux cas (nouveau jour ou nouveau solde de
        référence) : un déclenchement figé sur un ancien solde de
        référence non pertinent ne doit pas survivre au rebaselining."""
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
                "circuit_breaker_last_balance": self._last_balance,
                "circuit_breaker_daily_ref_day": (
                    self._daily_ref_day.isoformat() if self._daily_ref_day is not None else None),
                "circuit_breaker_daily_ref_balance": self._daily_ref_balance,
            },
            self._persist_path,
        )

    def record_trade_opened(self) -> None:
        """À appeler une fois par trade réellement ouvert (une seule fois
        même si l'ouverture se scinde en plusieurs ordres broker). Ne
        vérifie ni ne déclenche le plafond elle-même : c'est
        can_open_position() qui lit le compteur."""
        self._trades_opened_today += 1
        if self._day is None:
            # Aucun check() encore : pas de baseline, donc rien à persister
            # (_persist() exige _day). Le prochain check() repart de zéro.
            return
        self._persist()

    def can_open_position(self, current_balance: float) -> bool:
        self.check(current_balance)
        # Une fois déclenché un jour donné, reste déclenché pour le
        # reste de ce jour UTC — même si threshold_pct est relevé
        # entre-temps (profil de risque augmenté en cours de journée).
        # Voir la docstring de la classe.
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

    def can_open_position_daily(self, current_balance: float) -> bool:
        """Coupe-circuit pour un appelant qui ne vérifie qu'UNE FOIS par
        période (ex. ibkr_bot/daily.py, un batch par jour) — check() et
        can_open_position() supposent plusieurs vérifications par jour
        UTC (voir leur docstring) : avec un seul appel par jour, check()
        fixerait systématiquement la référence À LA VALEUR QU'IL COMPARE
        DANS LE MÊME APPEL, rendant le coupe-circuit muet pour toujours.

        Compare `current_balance` à une référence dérivée de la valeur
        persistée lors du DERNIER appel (champ dédié `_last_balance`,
        jamais `_starting_balance`/`_day` — deux concepts indépendants,
        pour ne jamais interférer avec check()/can_open_position() si
        jamais utilisés sur le même objet). Premier appel jamais vu
        (démarrage à froid) : fixe la référence et laisse passer, rien à
        comparer encore -- même convention que _starting_balance au
        premier cycle de check().

        VERROU INTRA-JOUR (Fix 3, revue finale de branche 2026-10-23) :
        en production, un batch quotidien peut être relancé manuellement
        PLUSIEURS FOIS le même jour calendaire (voir
        deploy/README-ibkr.md, procédure de redémarrage). Sans verrou,
        un second appel le même jour comparerait `current_balance` à
        `_last_balance` tel qu'avancé par le PREMIER appel de ce même
        jour -- si ce premier appel a déclenché le coupe-circuit, la
        perte réelle du jour deviendrait invisible au second appel
        (comparée à une référence déjà abaissée quelques instants plus
        tôt), contournant silencieusement la protection pour le reste de
        la journée. `_daily_ref_day`/`_daily_ref_balance` gèlent donc la
        référence de comparaison pour TOUS les appels d'un même jour
        calendaire sur la valeur en vigueur AVANT le premier appel de ce
        jour (c'est-à-dire l'ancien `_last_balance`, jamais le résultat
        du jour lui-même) :
          - premier appel JAMAIS vu : `_last_balance` devient
            `current_balance`, qui sert aussi de référence gelée du jour
            (rien d'antérieur à comparer) ; passe toujours.
          - premier appel d'un NOUVEAU jour calendaire (`_daily_ref_day`
            != aujourd'hui) : comparaison normale contre l'ancien
            `_last_balance`, PUIS gel de cette même valeur comme
            référence du jour pour d'éventuels rappels.
          - rappel sur le MÊME jour calendaire (`_daily_ref_day` ==
            aujourd'hui) : comparaison contre la référence GELÉE
            `_daily_ref_balance`, jamais contre `_last_balance` (qui a pu
            être avancé par un appel précédent ce même jour).
        `_last_balance` continue, dans tous les cas, à être avancé à
        `current_balance` à chaque appel -- c'est lui qui devient la
        référence dès qu'un VRAI changement de jour calendaire survient.
        Champs persistés indépendamment de `_tripped_today` (qui reste
        réservé à check()/can_open_position(), un patron d'appel
        différent) : un seul appel par jour ici en régime normal, rien à
        latcher au-delà du jour courant lui-même."""
        today = self._now_fn().date()
        if self._last_balance is None:
            self._last_balance = current_balance
            self._daily_ref_day = today
            self._daily_ref_balance = current_balance
            self._persist()
            return True
        if self._daily_ref_day != today:
            reference = self._last_balance
            self._daily_ref_day = today
            self._daily_ref_balance = reference
        else:
            reference = self._daily_ref_balance
        loss = reference - current_balance
        tripped = loss >= reference * self.threshold_pct
        self._last_balance = current_balance
        self._persist()
        return not tripped
