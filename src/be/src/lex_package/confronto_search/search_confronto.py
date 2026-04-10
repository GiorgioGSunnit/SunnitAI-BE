from .chunk_retriever import search_chunks
from langchain_core.messages import HumanMessage, SystemMessage
from lex_package.llm.factory import build_chat_model
from langchain_core.runnables import RunnableConfig
from openai import RateLimitError, APITimeoutError
import logging
import os
import json
from .utils import index_contents, insert_deep_with_path
from functools import lru_cache

user_prompt = """ scrivi un confronto tra due testi normativi: uno é un testo generale europeo, l'altro un testo interno
a cassa depositi e prestiti (cdp) che deve applicare/attuare quanto scritto nel primo testo. nel confronto analizzi se e come il testo di cdp 
é correlato ed é conforme a quanto espresso nel testo europeo

la risposta deve contenere solo il contenuto del confronto, esempio: 
" 
Il testo interno di CDP è chiaramente correlato e conforme al testo normativo europeo. Entrambi i testi enfatizzano l'importanza della compliance e dell'allineamento operativo con le normative superiori. CDP, attraverso la sua Divisione di Business, si impegna a seguire le linee guida stabilite, garantendo che le sue strategie commerciali siano in linea con gli orientamenti europei. Questo approccio non solo assicura la conformità normativa, ma contribuisce anche a una maggiore coerenza e sicurezza nel settore finanziario."
"""

system_prompt = "sei un analista normativo esperto nell'analizzare e confrontare testi normativi e individuare se sono correlati e compliant tra loro"


logger = logging.getLogger("lex_package.analisi_parallel")


# --- Lazy initialization per evitare connessione ad Azure all'import --------


@lru_cache(maxsize=1)
def _get_llm():
    return build_chat_model(target="primary", temperature=0)


@lru_cache(maxsize=1)
def _get_structured_llm():
    return _get_llm().with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError),
        stop_after_attempt=5,
        wait_exponential_jitter=True,
    )


MAX_CONCURRENCY = 5


async def confronto_searchai(articoli_da_attuare, nome_doc_attuativo):
    """
    riceve:
    - articoli_da_attuare: json che é output dell'analisi del documento
      esterno, da attuare
    - nome_doc_attuativo: nome del documento attuativo, interno, indicizzato da searchai
    """

    st_search_texts, i = index_contents(  # search texts dei sottocommi
        ["contenuto_parsato", "contenuto_parsato_2", "search_text"], articoli_da_attuare
    )
    st_contenuti, _i = index_contents(  # contenuti dei sottocommi
        ["contenuto_parsato", "contenuto_parsato_2", "contenuto"], articoli_da_attuare
    )

    """
    _batches = []
    j = []
    print("🍑 lunghezza lista search_texts: ", len(st_search_texts))
    # i sono gli indici dei sottocommi nel json del risultato dell'analisi
    # j[k] é il numero di risposte di searchai per search_texts[k]
    print("🍑 starting queries con searchai...")

    for _j, st_search_text in enumerate(st_search_texts):
        # fai quesry a searchai per ottenere i risultati correlati al search_text del sottocomma
        searchai_search = search_chunks(
            search_text=st_search_text,
            document_name=nome_doc_attuativo,
            top=3,
        )
        if searchai_search and searchai_search.get("value"):
            searchai_values, i = index_contents(
                ["value", "@search.captions", "text"], searchai_search
            )
        else:
            print("❌ Nessun risultato trovato da searchai per:", st_search_text)
            searchai_values = [""]

        j.append(len(searchai_values))  # aggiungo lunghezza risposte di searchai a j
        for searchai_value in searchai_values:
            _batches.append(
                {
                    "searchai_value": searchai_value,
                    "st_search_text": st_search_text,
                    "st_contenuto": st_contenuti[_j],
                }
            )
    # Crea la directory temp_res se non esiste e scrivi _batches.json per reference
    os.makedirs("temp_res", exist_ok=True)
    with open("temp_res/_batches.json", "w", encoding="utf-8") as f:
        json.dump(_batches, f, ensure_ascii=False, indent=2)

    # Trasformazione con lambda e map
    print("🍑 numero batches: ", len(_batches))
    print("🍑 starting confronto con llm...")
    batches = list(
        map(
            lambda batch: [
                SystemMessage(content=system_prompt),
                HumanMessage(
                    content=f"{user_prompt} contenuto del testo interno applicativo:'{batch['searchai_value']}';"
                    + f"contenuto del testo esterno da applicare: '{batch['st_contenuto']}';"
                ),
            ],
            _batches,
        )
    )

    confronti_texts = [
        getattr(x, "content", "")
        for x in await _get_structured_llm().abatch(
            batches, config=RunnableConfig(max_concurrency=MAX_CONCURRENCY)
        )
    ]
    print("👩‍🏫 numero confronti_texts usciti da llm: ", len(confronti_texts))

    # Aggiungi i risultati del confronto ai batch corrispondenti
    for m, b in enumerate(_batches):
        b["confronto"] = confronti_texts[m]

    with open("temp_res/_batches_con_confronti.json", "w", encoding="utf-8") as f:
        json.dump(_batches, f, ensure_ascii=False, indent=2)
    """

    # Leggi i batch con confronti dal file temporaneo
    batch_file_path = "temp_res/_batches_con_confronti.json"
    if os.path.exists(batch_file_path):
        with open(batch_file_path, "r", encoding="utf-8") as f:
            _batches = json.load(f)

        # Ricostruisci j (numero di risultati per ogni st_search_text)
        # Raggruppa per st_search_text e conta gli elementi
        search_text_groups = {}
        for batch in _batches:
            st_search_text = batch["st_search_text"]
            if st_search_text not in search_text_groups:
                search_text_groups[st_search_text] = 0
            search_text_groups[st_search_text] += 1

        j = list(search_text_groups.values())
        print(f"📊 Caricati {len(_batches)} batch con {len(j)} gruppi di search_text")
    else:
        print(
            "❌ File batch con confronti non trovato. Esegui prima la parte commentata."
        )
        return None

    results_grouped = []
    current_pos = 0
    # j contiene il numero di risultati di searchai per ogni st_search_text
    for count in j:
        # Estrai il blocco di risultati corrispondente dall'array _batches
        group = _batches[current_pos : current_pos + count]
        results_grouped.append(group)
        current_pos += count

    print("✅ confronto ended")

    # scrivi file di backup per vedere i results_grouped
    with open("temp_res/batches_grouped.json", "w", encoding="utf-8") as f:
        json.dump(results_grouped, f, ensure_ascii=False, indent=2)

    return insert_deep_with_path(
        results_grouped,
        i,
        articoli_da_attuare,
        ["contenuto_parsato", "contenuto_parsato_2", "confronti"],
    )
