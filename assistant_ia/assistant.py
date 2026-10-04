# assistant_ia/assistant.py
# Boucle d'outils avec Claude (voir spec §5). Le client Anthropic est
# injectable (parametre `client`) pour que les tests ne fassent jamais
# de vrai appel reseau/paye — meme principe que `gw` injectable dans
# ibkr_bot/gold_bot. `tool_dispatch` est injectable separement de
# `tools.dispatch_tool` pour isoler les tests de cette boucle de la
# logique des outils eux-memes (deja testee dans test_tools.py).
import anthropic

import assistant_ia.tools as tools
import assistant_ia.usage as usage

MODEL = "claude-opus-5-5"
MAX_TOOL_ITERATIONS = 6

SYSTEM_PROMPT = """Tu es l'assistant du site "AI Investment" (analyse-or). \
Tu reponds UNIQUEMENT a partir des outils fournis — n'invente jamais un \
chiffre, un score ou un statut que tu n'as pas obtenu par un outil.

Quand on te demande de comparer ou d'expliquer un score, argumente \
TOUJOURS facteur par facteur (ROCE, dette nette/EBITDA, croissance, etc.) \
en citant les valeurs exactes renvoyees par les outils — jamais juste \
le score brut.

Pour toute suggestion de navigation (voir une fiche, une section) ou \
tout choix a proposer a l'utilisateur en cas d'ambiguite (plusieurs \
entreprises possibles), utilise l'outil proposer_lien — jamais un lien \
ecrit dans le texte de ta reponse. Appelle-le plusieurs fois s'il y a \
plusieurs choix a proposer.

Si une information manque ou qu'une question est ambigue entre plusieurs \
entreprises, dis-le explicitement et propose les choix via proposer_lien \
plutot que de deviner silencieusement.

Reponds en texte brut, sans formatage markdown (pas de **gras**, pas de \
listes a tirets, pas de titres) — utilise des phrases completes et des \
retours a la ligne simples."""

NIVEAU_DEFAUT = "technique"
# Deux niveaux seulement : meme reponse, meme chiffres, explication differente.
CONSIGNES_NIVEAU = {
    "technique": (
        "Niveau de reponse : technique. Utilise le vocabulaire d'analyse "
        "financiere (ROCE, ICR, dette nette/EBITDA, score composite), cite les "
        "valeurs exactes renvoyees par les outils et reste dense."
    ),
    "grand_public": (
        "Niveau de reponse : grand public. Ecris pour quelqu'un qui n'est pas "
        "analyste : phrases courtes, une idee par phrase, et explique chaque "
        "sigle ou terme technique en quelques mots la premiere fois (par "
        "exemple : ROCE, la rentabilite de l'argent investi dans l'entreprise). "
        "Prefere les comparaisons du quotidien, commence par la conclusion en "
        "une phrase. Garde exactement les memes chiffres que les outils."
    ),
}


def construit_system(niveau) -> str:
    """Prompt systeme complet pour un niveau. Un niveau inconnu, absent ou
    non textuel retombe sur NIVEAU_DEFAUT : jamais d'erreur pour le visiteur."""
    if not isinstance(niveau, str) or niveau not in CONSIGNES_NIVEAU:
        niveau = NIVEAU_DEFAUT
    return SYSTEM_PROMPT + "\n\n" + CONSIGNES_NIVEAU[niveau]


def _construit_messages(question: str, history: list[dict]) -> list[dict]:
    return list(history) + [{"role": "user", "content": question}]


def run_assistant_loop(
    question: str, history: list[dict], manual_positions: list[dict] | None = None,
    *, niveau=NIVEAU_DEFAUT, client=None, tool_dispatch=None,
):
    """Genere les evenements de la conversation : {"type": "texte", ...}
    au fil du streaming, {"type": "liens", "liens": [...]} une fois tous
    les appels proposer_lien collectes, puis {"type": "fin"}. N'accede
    jamais au reseau en dehors de l'API Anthropic elle-meme (voir
    tool_dispatch, qui appelle assistant_ia.tools, qui lit les fichiers
    publics/API bots — jamais ce module directement)."""
    if client is None:
        client = anthropic.Anthropic()
    if tool_dispatch is None:
        tool_dispatch = tools.dispatch_tool

    messages = _construit_messages(question, history)
    system_text = construit_system(niveau)

    liens_collectes: list[dict] = []

    for iteration in range(MAX_TOOL_ITERATIONS):
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=16000,
                system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
                tools=tools.TOOL_DEFINITIONS,
                output_config={"effort": "high"},
                messages=messages,
            ) as stream:
                for bloc in stream:
                    if bloc.type == "text" and bloc.text:
                        yield {"type": "texte", "texte": bloc.text}
                reponse = stream.get_final_message()
        except Exception as e:
            yield {"type": "erreur", "detail": str(e)}
            yield {"type": "fin"}
            return

        usage.record_usage(reponse.usage.input_tokens, reponse.usage.output_tokens, model=MODEL)

        if reponse.stop_reason == "refusal":
            yield {"type": "texte", "texte": "Je ne peux pas repondre a cette question."}
            yield {"type": "fin"}
            return

        if reponse.stop_reason != "tool_use":
            if reponse.stop_reason == "max_tokens":
                yield {"type": "texte", "texte": "\n\n[reponse tronquee par la limite de longueur]"}
            if liens_collectes:
                yield {"type": "liens", "liens": liens_collectes}
            yield {"type": "fin"}
            return

        messages.append({"role": "assistant", "content": reponse.content})
        resultats_outils = []
        for bloc in reponse.content:
            if bloc.type != "tool_use":
                continue
            if bloc.name == "resume_portefeuille":
                tool_input = {**bloc.input, "positions_manuelles": manual_positions or []}
            else:
                tool_input = bloc.input
            resultat = tool_dispatch(bloc.name, tool_input)
            if bloc.name == "proposer_lien" and "erreur" not in resultat:
                liens_collectes.append(resultat)
            resultats_outils.append({
                "type": "tool_result", "tool_use_id": bloc.id,
                "content": str(resultat),
            })
        messages.append({"role": "user", "content": resultats_outils})

    if liens_collectes:
        yield {"type": "liens", "liens": liens_collectes}
    yield {
        "type": "texte",
        "texte": "\n\nJe n'ai pas pu rassembler toute l'information necessaire en une fois — essaie de reformuler en plusieurs questions plus precises.",
    }
    yield {"type": "fin"}
