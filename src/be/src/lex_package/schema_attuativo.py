from lex_package.llm.factory import build_chat_model
from lex_package.t.typed_dicts import Riferimento
from lex_package.t.comparazione_normativa import M
from lex_package.utils.utils import is_definitions_article, load_prompt
from langchain_core.runnables import RunnableConfig
from openai import RateLimitError, APITimeoutError
from langchain_core.messages import HumanMessage, SystemMessage
from lex_package.utils.utils import extract_integer
from functools import lru_cache

MAX_CONCURRENCY = 5


# --- Lazy initialization per evitare connessione ad Azure all'import --------


@lru_cache(maxsize=1)
def _get_llm():
    return build_chat_model(target="primary", temperature=0.0)


@lru_cache(maxsize=1)
def _get_structured_llm():
    return _get_llm().with_structured_output(M).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError),
        stop_after_attempt=5,
        wait_exponential_jitter=True,
    )

[system_prompt, user_prompt] = [
    load_prompt("system.txt"),
    load_prompt("analisi.txt"),
]


async def confronto_attuativo(art_attuativo: list, art_attuare: list):
    """
    articoli_1 --- documento attuativo es 8.
    articoli_2 --- documento da attuare es 7.
    """

    art_attuativo = [a for a in art_attuativo if not is_definitions_article(a)]
    art_attuare = [a for a in art_attuare if not is_definitions_article(a)]

    print("Starting... confronto schema attuativo 🔎")

    codice_doc_attuare = (
        art_attuare[0].get("codicedocumento", "2022/2555") if art_attuare else "2022/2555"
    )

    ## get the regerences and the matches
    for a in art_attuativo:
        for c in a.get("contenuto_parsato") or []:
            for sc in c.get("contenuto_parsato_2", []):
                refs = sc.get("riferimenti", [])
                if len(refs) > 0:
                    for r in refs:
                        if r.get("codice_documento") == codice_doc_attuare:
                            # prendi i matches nel documento da attuare _articoli_2 con i riferimenti
                            r.update({"matches": get_matches(r, art_attuare)})

    batches = []
    for a in art_attuativo:
        for c in a.get("contenuto_parsato") or []:
            for sc in c.get("contenuto_parsato_2", []):
                refs = sc.get("riferimenti", [])
                for r in refs:
                    for m in r.get("matches", []):
                        batches.append(
                            [
                                SystemMessage(content=system_prompt),
                                HumanMessage(
                                    content=f"{user_prompt}."
                                    + f"compara il contenuto del testo attuativo del paragrafo {c.get('identificativo', '')} {sc.get('identificativo', '')}  dell'articolo {a.get('titolo', '')};"
                                    + f"che ha contenuto: '{sc.get('contenuto', '')}';"
                                    + f"con il contenuto del testo da attuare, proveniente da articolo: {m.get('Match - Identificativo Articolo', '')}, comma: {m.get('Match - Identificativo Comma', '')}"
                                    + f", che é: {m.get('Match - Contenuto', '')};"
                                ),
                            ]
                        )
    print("  ---⬇️--- Numero di valutazioni:", len(batches))
    results = await _get_structured_llm().abatch(
        batches, config=RunnableConfig(max_concurrency=MAX_CONCURRENCY)
    )

    dicts: list[dict] = []
    for x in results:
        dicts.append(x.model_dump(exclude_none=True))

    counter = 0
    for a in art_attuativo:
        for c in a.get("contenuto_parsato") or []:
            for sc in c.get("contenuto_parsato_2", []):
                refs = sc.get("riferimenti", [])
                for r in refs:
                    for m in r.get("matches", []):
                        m.update(dicts[counter])
                        counter += 1

    return art_attuativo


def get_matches(r: Riferimento, art_attuare: list):
    # ritorna lista con articoli e commi in doc 2 che corrispondono al riferimento 2
    n_articolo = r.get("n_articolo") or ""
    n_paragrafo = r.get("n_paragrafo") or ""
    matches = []

    for a in art_attuare:
        if extract_integer(a.get("identificativo")) == n_articolo:
            if n_paragrafo:
                for c in a.get("contenuto_parsato") or []:
                    if f"{c.get('identificativo', '')}" == n_paragrafo:
                        print("🥔🥔🥔 trovato con articolo e paragrafo")
                        matches.append(
                            {
                                "Tipo Match": "il match é un comma: nel riferimento viene indicato articolo e comma",
                                "Match - Titolo Articolo": a.get("titolo", ""),
                                "Match - Identificativo Articolo": a.get("identificativo", ""),
                                "Match - Identificativo Comma": c.get("identificativo", ""),
                                "Match - Contenuto": c.get("contenuto", ""),
                            }
                        )

            else:
                print("🥔🥔🥔 trovato con articolo senza paragrafo")
                matches.append(
                    {
                        "Tipo Match": "il match é un articolo: nel riferimento viene indicato solo l'articolo",
                        "Match - Titolo Articolo": a.get("titolo", ""),
                        "Match - Identificativo Articolo": a.get("identificativo", ""),
                        "Match - Identificativo Comma": "",
                        "Match - Contenuto": a.get("contenuto", ""),
                    }
                )
    if len(matches) == 0:
        matches.append(
            {
                "Tipo Match": "il match non é stato trovato: il riferimento non corrisponde a nessun articolo o comma del documento attuativo",
            }
        )

    return matches
