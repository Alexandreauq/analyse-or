# ibkr_bot/journal.py
# Journalisation du batch reel : une ligne JSON append-only par execution
# dans real_trading_log.jsonl, l'etat de travail des positions ouvertes
# PAR LE BOT dans positions.json, et un instantane des soldes du compte.
#
# Ce module STOCKE et MET EN FORME, il ne decide de rien. Il ne reinvente
# pas non plus le stockage des positions : portfolio.save_positions fait
# deja l'ecriture atomique (Plan A). Son role ici est de decider QUOI
# ecrire, AVEC QUELS CHAMPS (spec 4.9), et de garantir qu'une ecriture
# ratee n'interrompt jamais le batch (meme contrat que
# gold_bot.loop._log_decision / _save_cache).
#
# docs/signal_tracking.json n'est JAMAIS ecrit ici (spec 4.9) : le
# paper-trading doit rester une donnee propre, sans slippage ni frais,
# pour continuer a mesurer la qualite du SIGNAL independamment de la
# qualite de l'EXECUTION.
import json
import os
from datetime import datetime, timezone

import ibkr_bot.portfolio as portfolio
import ibkr_bot.sizing as sizing

REAL_TRADING_LOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "real_trading_log.jsonl")
LATEST_ACCOUNT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "latest_account.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_run(run: dict, path: str = REAL_TRADING_LOG_PATH) -> bool:
    """Ajoute une ligne JSON au journal append-only. Renvoie False (sans
    jamais lever) si l'ecriture echoue : une panne disque sur le journal
    ne doit pas annuler un batch dont les ordres sont deja partis."""
    record = dict(run)
    record.setdefault("timestamp", now_iso())
    try:
        ligne = json.dumps(record, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(ligne + "\n")
        return True
    except Exception as e:
        print(f"Erreur journalisation batch IBKR : {e}")
        return False


def read_runs(path: str = REAL_TRADING_LOG_PATH, day: str | None = None) -> list[dict]:
    """Lignes du journal dont l'horodatage tombe le jour UTC demande
    (aujourd'hui par defaut). Fichier absent/illisible ou lignes
    corrompues -> ignores silencieusement, jamais d'exception. Meme
    contrat que gold_bot.notify.read_todays_decisions."""
    if day is None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    runs: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for ligne in fh:
                ligne = ligne.strip()
                if not ligne:
                    continue
                try:
                    entree = json.loads(ligne)
                except json.JSONDecodeError:
                    continue
                if isinstance(entree, dict) and entree.get("timestamp", "").startswith(day):
                    runs.append(entree)
    except Exception:
        return []
    return runs


def build_order_record(
    *, ticker: str, conid, sens: str, quantite: int,
    devise_compte: str = "", devise_cotation: str = "",
    taux_de_change=None, budget_converti=None, prix_reference_sizing=None,
    prix_paper=None, execution: dict | None = None,
    close_reason: str | None = None, rang: int | None = None,
) -> dict:
    """Une ligne d'ordre du journal, avec TOUS les champs exiges par la
    spec 4.9.

    GARDE-FOU PENCE/LIVRE, centralise ici et nulle part ailleurs :
    `execution["prix_execution_cotation"]` est le prix brut renvoye par
    IBKR, dans la devise de COTATION (GBp pour un ticker .L).
    `prix_execution` en est la traduction en devise de COMPTE (GBP), via
    sizing.from_quotation_price — c'est la seule unite comparable a
    docs/indices.json et la seule lisible dans un email. Les deux sont
    conserves : le brut pour l'audit, le converti pour la comparaison.
    """
    execution = execution or {}
    prix_cotation = execution.get("prix_execution_cotation")
    prix_compte = (sizing.from_quotation_price(prix_cotation, ticker)
                   if isinstance(prix_cotation, (int, float))
                   and not isinstance(prix_cotation, bool) else None)

    ecart = None
    if (isinstance(prix_compte, (int, float))
            and isinstance(prix_paper, (int, float))
            and not isinstance(prix_paper, bool) and prix_paper > 0):
        ecart = round((prix_compte - prix_paper) / prix_paper * 100, 4)

    return {
        "ticker": ticker,
        "conid": conid,
        "sens": sens,
        "quantite": quantite,
        "devise_compte": devise_compte,
        "devise_cotation": devise_cotation,
        "taux_de_change": taux_de_change,
        "budget_converti": budget_converti,
        "prix_reference_sizing": prix_reference_sizing,
        "prix_execution": prix_compte,
        "prix_execution_cotation": prix_cotation,
        "prix_execution_estime": bool(execution.get("prix_execution_estime", False)),
        "commission": execution.get("commission"),
        "prix_paper": prix_paper,
        "ecart_paper_pct": ecart,
        "order_id": execution.get("order_id"),
        "statut": execution.get("statut", "simule"),
        "detail": execution.get("detail"),
        "close_reason": close_reason,
        "rang": rang,
    }


def build_position_record(signal: dict, plan: dict, contrat: dict, quantite: int,
                          prix_execution_reference: float, today: str) -> dict:
    """Etat de travail d'une position ouverte par le bot (spec 4.9).

    `prix_execution_reference` est le prix d'execution reel, deja ramene
    en devise de COMPTE (livres pour le LSE, pas pence) : c'est la
    reference du stop-loss, comparee par portfolio.exit_reason au
    current_price de docs/indices.json, qui est lui aussi en livres.
    `target_exit_price` est repris VERBATIM du paper-trading et jamais
    recalcule (spec 3.6).
    """
    return {
        "id": signal["id"],
        "ticker": signal["ticker"],
        "name": signal.get("name", ""),
        "index": signal.get("index", ""),
        "conid": contrat["conid"],
        "devise": plan["devise_compte"],
        "quantite": quantite,
        "prix_execution_reference": prix_execution_reference,
        "paper_entry_price": signal.get("paper_entry_price"),
        "date_entree": today,
        "target_exit_price": signal["target_exit_price"],
        "date_limite": portfolio.deadline_date(today),
    }


def save_positions_safely(positions: list[dict],
                          path: str = portfolio.POSITIONS_PATH) -> bool:
    """portfolio.save_positions (ecriture atomique, Plan A), mais qui
    renvoie False au lieu de lever. positions.json est un ETAT, pas un
    log : l'appelant doit remonter un echec ici dans run["erreurs"] et
    donc dans l'email, contrairement a append_run."""
    try:
        portfolio.save_positions(positions, path)
        return True
    except Exception as e:
        print(f"Erreur ecriture positions.json : {e}")
        return False


def save_account_snapshot(data: dict, path: str = LATEST_ACCOUNT_PATH) -> bool:
    """Dernier instantane des soldes du compte (spec 4.3). Purement
    informatif : un echec n'interrompt jamais le batch."""
    record = dict(data)
    record.setdefault("fetched_at", now_iso())
    try:
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        print(f"Erreur ecriture instantane du compte : {e}")
        return False


def load_account_snapshot(path: str = LATEST_ACCOUNT_PATH) -> dict:
    """Dernier instantane du compte ecrit par save_account_snapshot.
    Fichier absent/corrompu -> {}, jamais d'exception (meme contrat que
    portfolio.load_positions)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def read_recent_actions(path: str = REAL_TRADING_LOG_PATH, limit: int = 50) -> list[dict]:
    """Les `limit` dernieres actions (achats/ventes) reellement executees
    ou simulees par le bot, tous jours confondus, triees du plus ancien au
    plus recent. Parcourt TOUT le fichier avant de tronquer (comme
    gold_bot.api._read_recent_decisions) : ce fichier grossit lentement
    (~1 ligne de run par jour), pas de souci de performance a moyen terme.

    Chaque entree aplatie ajoute un champ "date" (repris de run["date"]) :
    journal.build_order_record ne porte aucun horodatage propre (une seule
    ligne de run par jour), donc c'est le seul moyen pour l'appelant de
    savoir QUAND une action a ete prise. Une ligne, un run non-dict, ou un
    enregistrement individuel malforme est ignore sans jamais faire
    echouer le reste (meme contrat que gold_bot.api._read_recent_decisions)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return []
    actions: list[dict] = []
    for ligne in lines:
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            run = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        if not isinstance(run, dict):
            continue
        for cle in ("entrees", "sorties"):
            records = run.get(cle)
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                if record.get("statut") not in ("execute", "simule"):
                    continue
                action = dict(record)
                action["date"] = run.get("date")
                actions.append(action)
    return actions[-limit:]
