from lex_package.utils.utils import load_prompt, normalize_string
from langchain_core.messages import HumanMessage, SystemMessage
from lex_package.t.analisi_articolo import Analisi_emendativa
from lex_package.llm.factory import build_chat_model
from langchain_core.runnables import RunnableConfig
from openai import RateLimitError, APITimeoutError
from lex_package.utils.utils import extract_integer


# Ho spostato la configurazione dei modelli sull'helper condiviso per avere le
# stesse variabili d'ambiente sia in locale che sui pod Kubernetes.
# --- Lazy initialization per evitare connessione ad Azure all'import --------
from functools import lru_cache


@lru_cache(maxsize=1)
def _get_llm_primary():
    return build_chat_model(target="primary", temperature=0)


@lru_cache(maxsize=1)
def _get_llm_fallback_raw():
    return build_chat_model(target="fallback", temperature=0)


@lru_cache(maxsize=1)
def _get_llm_for_analisi_riferimenti():
    return _get_llm_primary().with_structured_output(Analisi_emendativa).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError),
        wait_exponential_jitter=False,
        stop_after_attempt=10,
    )


@lru_cache(maxsize=1)
def _get_llm_2():
    return _get_llm_primary().with_structured_output(Analisi_emendativa).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError),
        wait_exponential_jitter=False,
        stop_after_attempt=10,
    )


@lru_cache(maxsize=1)
def _get_llm_fallback():
    return _get_llm_fallback_raw().with_structured_output(Analisi_emendativa)


# ──────────────────────────────────────────────────────────────────────────────
# FUNZIONE DI INVOCATION CON FALLBACK
# ──────────────────────────────────────────────────────────────────────────────
async def _invoke_with_fallback_batch(llm, fallback_llm, batches, cfg):
    """
    Prova ad eseguire la batch con `llm`.
    Se arriva RateLimitError con Retry-After > 1 s,
    rilancia la stessa batch con il deployment di riserva.
    """
    try:
        return await llm.abatch(batches, config=cfg)

    except RateLimitError as e:
        delay = None
        if getattr(e, "response", None):
            try:
                delay = float(e.response.headers.get("Retry-After"))
            except (TypeError, ValueError):
                pass

        if delay and delay > 1:
            print(f"[Retry-After = {delay}s] – uso deployment di fallback")
            return await fallback_llm.abatch(batches, config=cfg)

        raise  # ritenta con il retry interno o propaga l’errore


MAX_CONCURRENCY = 5


# ──────────────────────────────────────────────────────────────────────────────
# FUNZIONE PRINCIPALE
# ──────────────────────────────────────────────────────────────────────────────
async def confronto_emendativo(
    articoli_emendare,
    articoli_emendativa,
    output_file_path_str: str | None = None,
):
    """
    articoli_1 → documento da emendare
    articoli_2 → documento emendativo
    """
    print("Starting amendment analysis 🔎")
    system_prompt = load_prompt("system.txt")

    codice_documento = articoli_emendare[0]["Codice Documento"]

    # 1️⃣  Filtra i commi rilevanti del documento 2
    for x in articoli_emendativa:
        if x.get("Codice Articolo", "") in {"", codice_documento}:
            x["similarita"] = 1

    indici_record_con_estratto = []
    batches = []
    estrattoPulito = ""

    for i, x in enumerate(articoli_emendativa):
        if x.get("similarita") == 1 and (
            x.get("Tipo").lower() == "comma" or x.get("Tipo").lower() == "sottocomma"
        ):
            estratto = ""
            if x.get("Tipo").lower() == "comma":
                estratto = " ".join(x.get("Contenuto Comma", "").split()[:10])
            if x.get("Tipo").lower() == "sottocomma":
                estratto = " ".join(x.get("Contenuto Sottocomma", "").split()[:10])

                x["estratto"] = estratto

                estrattoPulito = (
                    estratto.replace("seguente", "")
                    .replace("seguenti", "")
                    .replace("«", "")
                    .replace(" bis", "bis")
                )

                indici_record_con_estratto.append(i)
                print(
                    "🥔🥔🥔🥔🥔 ESTRATTO: ",
                    x.get("Tipo"),
                    " - ",
                    estrattoPulito,
                    "  -> ",
                    len(batches),
                )

                batches.append(
                    [
                        SystemMessage(content=system_prompt),
                        HumanMessage(
                            content=(
                                "estrai i riferimenti ad un altro articolo/paragrafo dal contenuto del testo "
                                "che ti viene fornito e indica la motivazione (tipo di operazione). "
                                f"Contenuto del testo: '{estrattoPulito}'"
                                "Nel caso di indicazioni multiple per i paragrafi, recupera il primo valore possibile."
                                "Leggi i seguenti esempi e comprendi il metodo di estrazione dei dati:"
                                "'l’articolo 10 è così modificato', articolo=10, paragrafo non riconosciuto"
                                "'il paragrafo 1 è così modificato', articolo non riconosciuto, paragrafo=1"
                                "'i paragrafi da 2 a 5 sono sostituiti dai ', articolo non riconosciuto, paragrafo=2, perché devi recuperare il primo valore."
                                "'l’articolo 11 è così modificato', articolo=11, paragrafo non riconosciuto"
                                "'al paragrafo 2 è aggiunto il comma : «Una controparte', articolo non riconosciuto, paragrafo=2"
                                "'al paragrafo 3 sono aggiunti i commi seguenti: IT 28/71', articolo non riconosciuto, paragrafo=3"
                                "'è inserito il  paragrafo: 3bis. In deroga al', articolo non riconosciuto, paragrafo=3bis"
                                "'è inserito il paragrafo : 12bis. L’ABE istituisce una', articolo non riconosciuto, paragrafo=12bis"
                                "'il paragrafo 15 è così modificato: i) al primo comma', articolo non riconosciuto, paragrafo=15"
                            )
                        ),
                    ]
                )

    # 2️⃣  Prima batch con fallback
    print(" confronto_emendativo 🥔🥔🥔", len(batches))
    results = await _invoke_with_fallback_batch(
        _get_llm_for_analisi_riferimenti(),
        _get_llm_fallback(),
        batches,
        RunnableConfig(max_concurrency=MAX_CONCURRENCY),
    )

    # 3️⃣  Post-processing e nuova batch (analisi impatto)
    batches = []
    indici_commi_con_riferimento_dettaglio = []
    n_art_default = 0

    for j, idx in enumerate(indici_record_con_estratto):
        sc = articoli_emendativa[
            idx
        ]  # comma o sottocomma emendativo il cui  estratto é stato elaborato
        # print("😉😉😉", sc.get("Tipo"))

        # aggiungi al record emendativo elaborato il risultato elaborazione llm
        riferimenti_emendativa = results[j].model_dump(exclude_none=True)
        sc["analisi_emendativa"] = riferimenti_emendativa

        ref = riferimenti_emendativa.get("riferimenti_emendativa", {})

        n_art = ref.get("n_articolo")
        n_par = ref.get("n_paragrafo")

        if sc.get("Tipo") == "Sottocomma":
            if sc.get("Identificativo Sottocomma") == 0:
                print(
                    "   🥔😉  Indice: ",
                    j,
                    "  Articolo: ",
                    sc.get("Articolo"),
                    " Comma: ",
                    sc.get("Identificativo Comma"),
                    " --> AZZERO --> ",
                    n_art,
                )
                if n_art:
                    n_art_default = n_art
                else:
                    n_art_default = False
            else:
                if (not n_art) and (n_par) and (n_art_default):
                    n_art = n_art_default
                    sc["analisi_emendativa"]["riferimenti_emendativa"]["n_articolo"] = (
                        n_art_default
                    )
                print(
                    "     😉 Articolo: ",
                    sc.get("Articolo"),
                    " Comma: ",
                    sc.get("Identificativo Comma"),
                    " Sottocomma: ",
                    sc.get("Identificativo Sottocomma"),
                    "  --> ",
                    n_art,
                    "-",
                    n_par,
                    "!",
                )

        #        if (sc.get("Tipo") == "Sottocomma"):
        #            print (" SIAMO in un Sottocomma --> ", sc.get("Identificativo Sottocomma"))
        #            if (n_art and (sc.get("Identificativo Sottocomma") == 0)):
        #                n_art_default = n_art
        #            if ((not n_art) and n_par and (sc.get("Identificativo Sottocomma") != 0)):
        #                print ("     Sottocomma ", sc.get("Identificativo Sottocomma"), "  --> UTILIZZA ", n_art_default)
        #                n_art = n_art_default
        #                sc["analisi_emendativa"]["riferimenti_emendativa"]["n_articolo"] = n_art
        if not n_art:
            ##### Da inserire comunque un commento in "descrizione"
            # print("Nessun Riferimento")
            ##### Da inserire comunque un commento in "descrizione"
            ##### Da inserire comunque un commento in "descrizione"
            continue

        # trova il testo corrispondente nel doc originale
        for a in articoli_emendare:
            match_comma = (
                (n_art)
                and a.get("Tipo") == "Comma"
                and extract_integer(a.get("Articolo", "").rstrip())
                == extract_integer(f"Articolo {n_art}")
                and (a.get("Identificativo Comma") == n_par)
            )
            #                and (not n_par or a.get("Identificativo Comma") == n_par)
            match_art = (
                n_art
                and (not n_par)
                and a.get("Tipo") == "Articolo"
                and extract_integer(a.get("Articolo", "").rstrip())
                == extract_integer(f"Articolo {n_art}")
            )
            # if match_comma or (match_art and not n_par):
            if (match_comma) or (match_art):
                # sc["analisi_emendativa"]["riferimento_dettaglio"] = a.get("Contenuto Comma") or a.get("Contenuto Articolo", "")
                sc["riferimento_dettaglio"] = (
                    a.get("Contenuto Comma")
                    or a.get("Contenuto Articolo", "")
                    or a.get("Contenuto", "")
                )
                indici_commi_con_riferimento_dettaglio.append(idx)

                contenuto_emendativo = ""

                if sc.get("Tipo") == "Comma":
                    contenuto_emendativo = sc.get("Contenuto Comma", "")
                else:
                    contenuto_emendativo = sc.get("Contenuto Sottocomma", "")

                batches.append(
                    [
                        SystemMessage(content=system_prompt),
                        HumanMessage(
                            content=(
                                "In base al tipo di modifica, scrivi una frase (< 100 parole) che spieghi l’impatto "
                                "del cambiamento dal testo emendato al testo emendativo.\n"
                                f"tipo di modifica: {riferimenti_emendativa.get('motivazione')}\n"
                                f"contenuto emendativo: '{contenuto_emendativo}'\n"
                                f"contenuto originario: '{a.get('Contenuto Comma', '') or a.get('Contenuto Articolo', '')}'"
                            )
                        ),
                    ]
                )
                break  # trovato il match, esci dal ciclo articoli_1

    # 4️⃣  Seconda batch con fallback
    print("👩🏼‍🦱👩🏻‍🦱👩🏽‍🦱", len(batches))
    if batches:
        risultati_impatti = await _invoke_with_fallback_batch(
            _get_llm_2(),
            _get_llm_fallback(),
            batches,
            RunnableConfig(max_concurrency=MAX_CONCURRENCY),
        )

        # ricostruzione
        for j, idx in enumerate(indici_commi_con_riferimento_dettaglio):
            # print(" ==> ", idx, "-", articoli_2[idx]["Articolo"],"-", articoli_2[idx]["Identificativo Comma"]," - ",j,json.dumps(articoli_2[idx]["analisi_emendativa"], ensure_ascii=False, indent=2))
            articoli_emendativa[idx]["Descrizione_riferimento"] = risultati_impatti[
                j
            ].dict()
    print("Finished amendment analysis ✅")
    return articoli_emendativa


# TODO: Verifica solo che n_articolo sia sempre definito e che le lunghezze di results-e‐indici_commi_con_estratto coincidano; in caso contrario aggiungi log o if di sicurezza.
