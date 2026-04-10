from lex_package.llm.factory import build_chat_model
from lex_package.utils.utils import (
    is_definitions_article,
    load_prompt,
    get_article_by_identificativo,
)
from langchain_core.runnables import RunnableConfig
from openai import RateLimitError, APITimeoutError
from langchain_core.messages import HumanMessage, SystemMessage
from lex_package.t.similarity import Correlazione, Correlazione_con_Dettaglio
from lex_package.utils.confronto_metadata import looks_like_document_metadata_quality
from lex_package.utils.utils_comparison import (
    get_best_matching_articles_attuativo,
    get_couples_commas_comparison,
)
from lex_package.utils.embeddings import cosine_similarity
from functools import lru_cache
from pydantic import ValidationError

user_prompt = load_prompt("coefficienti_correlazione.txt")
system_prompt = load_prompt("system.txt")

MAX_CONCURRENCY = 10
# Embeddings are NOT used to drive matching (LLM-only) to keep the system robust
# when embeddings aren't configured in the environment. We still compute cosine
# similarity when vectors are already present in the analyzed JSON for QA.


# --- Lazy initialization per evitare connessione ad Azure all'import --------


@lru_cache(maxsize=1)
def _get_llm():
    return build_chat_model(target="primary", temperature=0.0)


@lru_cache(maxsize=1)
def _get_structured_llm():
    return _get_llm().with_structured_output(Correlazione).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError, ValidationError),
        stop_after_attempt=5,
        wait_exponential_jitter=True,
    )


@lru_cache(maxsize=1)
def _get_commi_comparison_llm():
    return _get_llm().with_structured_output(Correlazione_con_Dettaglio).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError, ValidationError),
        stop_after_attempt=5,
        wait_exponential_jitter=True,
    )


# integrazione su output di confronto  in out_schema_attuativo/confronti/ vs articoli da attuare in out_parser
async def integrazione_confronto_attuativo_confronto_titoli(
    confronti: list, articoli_attuare: list, output_dir: str = "."
):
    articoli_attuare = [a for a in articoli_attuare if not is_definitions_article(a)]

    print(
        "🔎\n\n\nintegrazione confronto attuativo: confronto titoli articoli 🔎\n\n\n",
    )

    indices: list[list[int]] = []
    title_items: list[tuple[str, object]] = []

    for i_attuare, art_attuare in enumerate(articoli_attuare):
        for j_attuativo, art_attuativo in enumerate(confronti):
            indices.append([i_attuare, j_attuativo])
            ta = art_attuare.get("titolo", "") or ""
            tb = art_attuativo.get("titolo", "") or ""
            if looks_like_document_metadata_quality(
                ta
            ) or looks_like_document_metadata_quality(tb):
                title_items.append(
                    ("fixed", {"coefficiente_correlazione": 0}),
                )
            else:
                title_items.append(
                    (
                        "llm",
                        [
                            SystemMessage(system_prompt),
                            HumanMessage(
                                content=f"{system_prompt}"
                                + "ti vengono dati i titoli di due articoli estratti rispettivamente da un regolamento attuativo e da un regolamento da attuare"
                                + "il tuo compito é produrre un numero da 0 a 30 che indichi quanto i due titoli sono simili o correlati"
                                + f"titolo dell'articolo del regolamento da attuare: '{ta}'"
                                + f"titolo dell'articolo del regolamento attuativo: '{tb}'"
                            ),
                        ],
                    ),
                )

    llm_title_batches = [x[1] for x in title_items if x[0] == "llm"]
    if llm_title_batches:
        llm_title_results = await _get_structured_llm().abatch(
            llm_title_batches,
            config=RunnableConfig(max_concurrency=MAX_CONCURRENCY),
        )
        llm_title_results = [r.model_dump() for r in llm_title_results]
    else:
        llm_title_results = []
    it_tit = iter(llm_title_results)
    results: list[dict] = []
    for kind, payload in title_items:
        if kind == "fixed":
            results.append(payload)
        else:
            results.append(next(it_tit))
    counter = 0
    for i, art_attuare in enumerate(articoli_attuare):
        for j, art_attuativo in enumerate(confronti):
            art_attuativo.setdefault("similarita_attuativa_per_titolo", []).append(
                {
                    "titolo_articolo_confrontato": art_attuare.get("titolo", ""),
                    "identificativo_articolo_confrontato": art_attuare.get(
                        "identificativo", ""
                    ),
                    **results[counter],
                }
            )
            counter += 1
    return confronti

    ############


async def integrazione_confronto_attuativo_confronto_commi(
    confronti_similarita_titoli: list, articoli_attuare: list, output_dir: str = "."
):
    print(
        "\n\n\n",
        "integrazione confronto attuativo: starting to compute similarity coefficients for commi 🔎",
        "\n\n\n",
    )

    # Non filtrare i commi con riferimenti: servono tutte le unità foglia (sottocomma)
    # per il prodotto cartesiano con il documento da attuare. Il vecchio filtro
    # scartava ogni comma che avesse almeno un sottocomma con riferimenti,
    # riducendo drasticamente gli estratti in seconda meta.

    for a in confronti_similarita_titoli:
        matches = get_best_matching_articles_attuativo(  # get best matching articles with given article
            a.get("similarita_attuativa_per_titolo", {})
        )
        for m in matches:
            articolo_attuare_che_matcha = get_article_by_identificativo(
                m.get("identificativo_articolo_confrontato", ""), articoli_attuare
            )
            if articolo_attuare_che_matcha:
                a.setdefault("best_matches", []).append(
                    {
                        "titolo_articolo": articolo_attuare_che_matcha.get("titolo", ""),
                        "identificativo_articolo": articolo_attuare_che_matcha.get(
                            "identificativo", ""
                        ),
                        "coppie_commi": get_couples_commas_comparison(
                            a, articolo_attuare_che_matcha
                        ),
                    }
                )

    META_MSG = (
        "Sezione metadati/qualità del documento (scheda, informazioni sul documento, ecc.): "
        "confronto non rilevante; coefficiente 0."
    )
    commi_items: list[tuple[str, object]] = []
    for a in confronti_similarita_titoli:
        for m in a["best_matches"]:
            for c in m.get("coppie_commi", []):
                emb_a = c.get("embedding_attuativo", []) or []
                emb_b = c.get("embedding_attuare", []) or []
                c["embedding_cosine"] = cosine_similarity(emb_a, emb_b)
                if c.get("metadata_quality_pair"):
                    commi_items.append(
                        (
                            "fixed",
                            {
                                "coefficiente_correlazione": 0,
                                "dettaglio": META_MSG,
                            },
                        )
                    )
                else:
                    commi_items.append(
                        (
                            "llm",
                            [
                                SystemMessage(system_prompt),
                                HumanMessage(
                                    content=""
                                    + "ti vengono dati due commi estratti rispettivamente da un regolamento e dal corrispondente regolamento attuativo"
                                    + "il tuo compito é produrre un numero da 0 a 30 che indichi quanto i due commi sono simili o correlati"
                                    + f"contenuto del comma del regolamento attuativo: '{c.get('contenuto_comma_attuativo', '')}'"
                                    + f"contenuto del comma del regolamento da attuare: '{c.get('contenuto_comma_attuare', '')}'"
                                    + "produci inoltre un testo che riassuma in che modo il comma attuativo attui il comma da attuare nel contesto italiano"
                                ),
                            ],
                        )
                    )

    llm_commi_batches = [x[1] for x in commi_items if x[0] == "llm"]
    print("🥔🥔🥔 len of commi LLM batches is: ", len(llm_commi_batches))
    if llm_commi_batches:
        llm_commi_results = await _get_commi_comparison_llm().abatch(
            llm_commi_batches,
            config=RunnableConfig(max_concurrency=MAX_CONCURRENCY),
        )
        llm_commi_results = [r.model_dump() for r in llm_commi_results]
    else:
        llm_commi_results = []
    it_commi = iter(llm_commi_results)
    results: list[dict] = []
    for kind, payload in commi_items:
        if kind == "fixed":
            results.append(payload)
        else:
            results.append(next(it_commi))

    # ricostruzione delle batches in confronti
    counter = 0
    for a in confronti_similarita_titoli:
        for m in a["best_matches"]:
            for c in m.get("coppie_commi", []):
                c["risultato_confronto"] = results[counter]
                counter += 1

    return confronti_similarita_titoli


def select_best_matches(confronti):
    """
    • Mantiene, per ciascun `m`, solo la coppia di commi con coefficiente massimo
    • Se quel coefficiente è < 15, aggiunge un messaggio d’avviso in
      `c["risultato_confronto"]["dettagliio"]`
    """
    AVVISO = (
        "Non é stato trovato un comma che matcha con similarita' sufficiente "
        "da poter essere sicuri della correlazione."
    )

    for a in confronti:
        for m in a["best_matches"]:
            coppie_commi = m["coppie_commi"]

            # niente da fare se la lista è vuota
            if not coppie_commi:
                continue
            coppie_commi_cleaned = []
            identificativi_attuativo = {
                cc["identificativo_comma_attuativo"] for cc in coppie_commi
            }
            for i in identificativi_attuativo:
                match_singolo_comma_attuativo = [
                    x for x in coppie_commi if x["identificativo_comma_attuativo"] == i
                ]
                migliore = max(
                    match_singolo_comma_attuativo,
                    key=lambda c: (c.get("risultato_confronto", {}) or {}).get(
                        "coefficiente_correlazione", 0
                    ),
                )
                # Keep a QA field for downstream inspection (LLM score only).
                migliore["score_combinato"] = float(
                    (migliore.get("risultato_confronto", {}) or {}).get(
                        "coefficiente_correlazione", 0
                    )
                )
                if migliore["risultato_confronto"]["coefficiente_correlazione"] < 15:
                    migliore["risultato_confronto"]["dettaglio"] = AVVISO
                    # Rif-Contenuto deve restare il testo del secondo documento (da attuare):
                    # non azzerare contenuto_comma_attuare.

                coppie_commi_cleaned.append(migliore)
            m["coppie_commi"] = coppie_commi_cleaned

    return confronti
