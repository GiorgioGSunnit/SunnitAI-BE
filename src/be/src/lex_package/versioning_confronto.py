from lex_package.utils.utils import load_prompt, clean_json_response
from langchain_core.messages import HumanMessage, SystemMessage
from lex_package.t.analisi_articolo import Analisi_emendativa
from lex_package.llm.factory import build_chat_model
from langchain_core.runnables import RunnableConfig
from openai import RateLimitError, APITimeoutError
from lex_package.parsing_utils.parser_articolo import nojunkchars, noforbiddenchars
from lex_package.t.similarity_minimal import Similarity, DescriptionSet
from lex_package.parsing_utils.parser_banca import _HEADER_RE_IntestazioneBdI
from functools import lru_cache
import Levenshtein


# --- Lazy initialization per evitare connessione ad Azure all'import --------


@lru_cache(maxsize=1)
def _get_llm():
    return build_chat_model(target="primary", temperature=0)


@lru_cache(maxsize=1)
def _get_llm_fallback_raw():
    return build_chat_model(target="fallback", temperature=0)


@lru_cache(maxsize=1)
def _get_llm_for_analisi_riferimenti():
    return _get_llm().with_structured_output(Analisi_emendativa).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError),
        stop_after_attempt=5,
        wait_exponential_jitter=True,
    )


@lru_cache(maxsize=1)
def _get_llm_2():
    return _get_llm().with_structured_output(Analisi_emendativa).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError),
        stop_after_attempt=5,
        wait_exponential_jitter=True,
    )


@lru_cache(maxsize=1)
def _get_llm_Description():
    return _get_llm().with_structured_output(DescriptionSet).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError),
        stop_after_attempt=5,
        wait_exponential_jitter=True,
    )


@lru_cache(maxsize=1)
def _get_llm_similarita():
    return _get_llm().with_structured_output(Similarity).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError),
        stop_after_attempt=5,
        wait_exponential_jitter=True,
    )


@lru_cache(maxsize=1)
def _get_llm_similarita_fallback():
    return _get_llm_fallback_raw().with_structured_output(Similarity)


@lru_cache(maxsize=1)
def _get_llm_Description_fallback():
    return _get_llm_fallback_raw().with_structured_output(DescriptionSet)

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


@lru_cache(maxsize=1)
def _get_llm_primary():
    """Alias per compatibilità."""
    return _get_llm()


@lru_cache(maxsize=1)
def _get_llm_fallback():
    return _get_llm_fallback_raw().with_structured_output(Analisi_emendativa)

def levenshtein(s1, s2):
    if len(s1) < len(s2):
        return levenshtein(s2, s1)

    # s1 è la stringa più lunga
    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions  = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


async def confronto_versioning(
    articoli_1,
    articoli_2,
    output_file_path_str: str | None = None,
):
    print(" 🔎🔎🔎 Starting VERSIONING Analysis 🔎🔎🔎")

    prompt_emendativa = load_prompt("versioning.txt")
    system_prompt = load_prompt("system.txt")

    codice_documento = articoli_1[0]["Codice Documento"]

    indici_commi_con_estratto = []
    batches = []
    debug_log = []  # Lista per contenere i log

    documento_1_clean = []
    documento_2_clean = []
    ConteggioConfermato = 0
    ConteggioEliminato = 0
    ConteggioInserito = 0
    ConteggioAggiornato = 0
    ConteggioDaVerificare = 0

    ContARTICOLOConfermato = 0
    ContARTICOLOEliminato = 0
    ContARTICOLOInserito = 0
    ContARTICOLOAggiornato = 0
    ContCommmaConfermato = 0
    ContCommmaEliminato = 0
    ContCommmaInserito = 0
    ContCommmaAggiornato = 0
    ContSottoCommaConfermato = 0
    ContSottoCommaEliminato = 0
    ContSottoCommaInserito = 0
    ContSottoCommaAggiornato = 0

    TotaleArticoli = len(articoli_2)
    NumeroArticoli = 0
    IdentificativoArticolo_1 = ""
    IdentificativoArticolo_2 = ""
    IdentificativoArticolo_w = ""

    QuanteVolteArticolo = 0
    QuanteVolteComma = 0

    articolo1_hash = ""
    articolo2_hash = ""

    for i, x in enumerate(articoli_2):  # def estratto for sottocommi with similarita 1

        QuanteVolteArticolo = 0
        QuanteVolteComma = 0

        # print (" documento2 indice: ", i)

        IdentificativoArticolo_2 =  str(x.get("Articolo", "")).replace(" ", "").replace("art.", "Articolo").replace("Art.", "Articolo").replace("articolo", "Articolo")

        # print(" Identificativo Articolo Documento2: ", IdentificativoArticolo_2)

        if ( x.get("Tipo", "") == "Articolo"):
            #            if (("Articolo" in IdentificativoArticolo_2) or (len(IdentificativoArticolo_2)>4)) :  #Considero solo gli Articoli
            #   E' STATA ELIMINATA LA VERIFICA DELLA PRESENZA DELLA PAROLA CHIAVE "ARTICOLO"
            ###############################################################################################################################
            if ((len(IdentificativoArticolo_2)>4)) :  #Considero solo gli Articoli

                articolo2_tipo      = x.get("Tipo", "")
                articolo2_hash      = x.get("Hash", "")
                articolo2_contenuto = nojunkchars(x.get("Contenuto", ""))
                articolo2_articolo  = x.get("Articolo", "")
                NumeroArticoli += 1
                debug_log.append("  ##### Articolo N:" + str(NumeroArticoli) + " Tipo: " + str(articolo2_tipo) + " Hash: " + str(articolo2_hash) + " Nome:" + str(articolo2_articolo))

                if (articolo2_hash == 0):
                    x["similarita1"] = 1
                    x["motivo"] = "Eliminato"
                    x["Contenuto"] = x.get("Contenuto")
                    ContARTICOLOEliminato +=1
                    debug_log.append("  ##### Hash zero ==> Eliminato")
                else:
                    for j,y in enumerate(articoli_1):
                        # print (" documento1 indice: ", j)
                        IdentificativoArticolo_1 =  str(y.get("Articolo", "")).replace(" ", "").replace("art.", "Articolo").replace("Art.", "Articolo").replace("articolo", "Articolo")
                        # print(" Identificativo Articolo Documento1: ", IdentificativoArticolo_1)

                        if ((y.get("Tipo", "") == "Articolo") and (IdentificativoArticolo_1 == IdentificativoArticolo_2)):
                            QuanteVolteArticolo +=1

                            #                            print("   MATCH! Documento2: ", IdentificativoArticolo_2, " --> Documento1: ", IdentificativoArticolo_1)
                            articolo1_hash = y.get("Hash", "")
                            articolo1_contenuto = nojunkchars(y.get("Contenuto", ""))
                            debug_log.append("  ##### Hash Diverso da zero ==> Corrispondenza con Documento1 ==> " + str(articolo1_hash) + " IdentificativoArticolo_1 = " + str(IdentificativoArticolo_1) + " ==> IdentificativoArticolo_2 = " + str(IdentificativoArticolo_2) + "  QuanteVolteArticolo=" + str(QuanteVolteArticolo))

                            if (articolo1_contenuto == articolo2_contenuto):
                                x["similarita1"] = 1
                                x["motivo"] = "Confermato"
                                x["relazione_pagina"] = y.get("Pagina", "")
                                x["relazione_articolo"] = y.get("Articolo", "")
                                x["relazione_Parte"] = y.get("Parte", "")
                                x["relazione_Titolo"] = y.get("Titolo", "")
                                x["relazione_Capitolo"] = y.get("Capitolo", "")
                                x["relazione_Allegato"] = y.get("Allegato", "")
                                x["relazione_Sezione"] = y.get("Sezione", "")
                                x["relazione_contenuto"] = y.get("Contenuto", "#VUOTO#")
                                x["relazione_hash"] = y.get("Hash", "")
                                y["similarita1"] = 1
                                y["motivo"] = "Confermato"
                                #                                print("    🌽 Per Articolo CONFERMATO", NumeroArticoli, "[",articolo2_hash,"] - [",articolo1_hash,"]-->  <--")
                                ContARTICOLOConfermato +=1
                                debug_log.append("        Articolo CONFERMATO  ==> " + str(ContARTICOLOConfermato))
                            else:
                                if (articolo1_hash == 0):
                                    x["similarita1"] = 1
                                    x["motivo"] = "Inserito"
                                    ContARTICOLOInserito +=1
                                    debug_log.append("        Articolo INSERITO  ==> IdentificativoArticolo_2 = " + str(IdentificativoArticolo_2)  + "  " + str(ContARTICOLOInserito))
                                else:
                                    x["similarita1"] = -1
                                    x["motivo"] = "Da Verificare"
                                    x["relazione_pagina"] = y.get("Pagina", "")
                                    x["relazione_articolo"] = y.get("Articolo", "")
                                    x["relazione_Parte"] = y.get("Parte", "")
                                    x["relazione_Titolo"] = y.get("Titolo", "")
                                    x["relazione_Capitolo"] = y.get("Capitolo", "")
                                    x["relazione_Allegato"] = y.get("Allegato", "")
                                    x["relazione_Sezione"] = y.get("Sezione", "")
                                    x["relazione_contenuto"] = y.get("Contenuto Articolo", "")
                                    x["relazione_hash"] = y.get("Hash", "")
                                    y["similarita1"] = -1
                                    y["motivo"] = "Da Verificare"
                                    ConteggioDaVerificare += 1
                                    debug_log.append("        Articolo DA VERIFICARE  ==> " + str(ConteggioDaVerificare))
                    if ("similarita1" not in x):
                        x["similarita1"] = 1
                        x["motivo"] = "Inserito"
                        ContARTICOLOInserito +=1
                        debug_log.append("        Articolo INSERITO  ==> " + str(ContARTICOLOInserito) + "    (non c'è match con Documento1)")
                for l, w in enumerate(articoli_2):                                                     
                    if ( w.get("Tipo", "") == "Comma"):  #Consideriamo i Commi
                        QuanteVolteComma = 0
                        IdentificativoArticolo_w = str(w.get("Articolo", "")).replace(" ", "").replace("art.", "Articolo").replace("Art.", "Articolo").replace("articolo", "Articolo")
                        if ((len(IdentificativoArticolo_w)>4)) :  #Considero solo gli Articoli o comunque titoli significativi
                            if (str(IdentificativoArticolo_w) == IdentificativoArticolo_2):
                                if (x.get("similarita1","") == 1):
                                    if (x.get("motivo")=="Eliminato") or (x.get("motivo")=="Inserito"):
                                        # Estendo ai commi i risultati già definiti per gli articoli, per i quali si è inserito "similartità1=1"
                                        w["similarita1"] = 1
                                        w["motivo"] = x["motivo"]
                                        w["Contenuto"] = w["Contenuto Comma"]
                                        w["Descrizione"] = "Ereditato"
                                        debug_log.append("                 Estendo la motivazione anche al comma(" + str(w.get("Identificativo Comma")) + ")")
                                    else:
                                        for n,t in enumerate(articoli_1):
                                            if ((t.get("Tipo", "") == "Comma") 
                                                    and (t.get("Articolo", "") == x["relazione_articolo"]) 
                                                    and (nojunkchars(t.get("Identificativo Comma", "")) == nojunkchars(w.get("Identificativo Comma", "")))
                                                ):
                                                debug_log.append("  #####        ###   " + str(t.get("Tipo", "")) +"=="+ str(w.get("Tipo", "")) + " and " + str(t.get("Articolo", "")) + "==" + str(x.get("relazione_articolo")) + " and " + str(t.get("Identificativo Comma", "")) + " == " + str(w.get("Identificativo Comma", "")))
                                                if (nojunkchars(t.get("Contenuto Comma", "-")) == nojunkchars(w.get("Contenuto Comma", ""))):
                                                    w["similarita1"] = 1
                                                    w["motivo"] = "Confermato"
                                                    w["Contenuto"] = w.get("Contenuto Comma") if (w.get("Contenuto Comma") != "") else "#VUOTO#" 
                                                    w["relazione_pagina"] = t.get("Pagina", "")
                                                    w["relazione_articolo"] = t.get("Articolo", "")
                                                    w["relazione_Parte"] = t.get("Parte", "")
                                                    w["relazione_Titolo"] = t.get("Titolo", "")
                                                    w["relazione_Capitolo"] = t.get("Capitolo", "")
                                                    w["relazione_Allegato"] = t.get("Allegato", "")
                                                    w["relazione_Sezione"] = t.get("Sezione", "")
                                                    w["relazione_comma"] = t.get("Identificativo Comma", "")
                                                    w["relazione_contenuto"] = t.get("Contenuto Comma") if (t.get("Contenuto Comma") != "") else "#VUOTO#"
                                                    w["relazione_hash"] = t.get("Hash", "")
                                                    t["similarita1"] = 1
                                                    t["motivo"] = "Confermato"
                                                    ContCommmaConfermato +=1                                        
                                                    debug_log.append("                 Il Comma " + str(w.get("Identificativo Comma")) + " è stato CONFERMATO [" + str(t.get("Hash", "")) +"] - [" + str(w.get("Hash", "")) + "]  -->" + str(ContCommmaConfermato))
                                                else:
                                                    w["similarita1"] = 1
                                                    w["motivo"] = "ERRORE o Spazio"
                                                    w["Contenuto"] = (w.get("Contenuto Comma", "")) if (w.get("Contenuto Comma") != "") else "#VUOTO#"
                                                    w["Contenuto Comma"] = (w.get("Contenuto Comma", "")) if (w.get("Contenuto Comma") != "") else "#VUOTO#"
                                                    w["relazione_contenuto"] = (t.get("Contenuto Comma", "")) if (t.get("Contenuto Comma") != "") else "#VUOTO#"
                                                    debug_log.append("                 ERRORE!!!  Il Comma " + str(w.get("Identificativo Comma")) + " è stato CONFERMATO [" + str(t.get("Hash", "")) +"] - [" + str(w.get("Hash", "")) + "]  -->" + str(ContCommmaConfermato))
                                                    ##### 🚳 #####
                                if (x.get("similarita1","") == -1):
                                    # "similarità1 = -1" sono per quei commi i cui articoli sono stati identificati come "Da Verificare"
                                    if  (w.get("Hash","") == 0):
                                        w["similarita1"] = 1
                                        w["motivo"] = "Eliminato"
                                        w["Contenuto"] = w["Contenuto Comma"]
                                        ContCommmaEliminato +=1
                                        debug_log.append("                 Il Comma " + str(w.get("Identificativo Comma")) + " è stato ELIMINATO -->" + str(ContCommmaEliminato))
                                    else:
                                        for m,z in enumerate(articoli_1):
                                            if ((z.get("Tipo", "") == w.get("Tipo", "")) 
                                                 and (z.get("Articolo", "") == x["relazione_articolo"]) 
                                                 and (nojunkchars(z.get("Identificativo Comma", "")) == nojunkchars(w.get("Identificativo Comma", "")))
                                               ):
                                                QuanteVolteComma +=1
                                                debug_log.append("  #####        Corrispondenza con Comma ==> Corrispondenza con Documento1 ==> " + str(articolo1_hash) + "   QuanteVolteComma=" + str(QuanteVolteComma))
                                                debug_log.append("  #####        ###   " + str(z.get("Tipo", "")) +"=="+ str(w.get("Tipo", "")) + " and " + str(z.get("Articolo", "")) + "==" + str(x.get("relazione_articolo")) + " and " + str(z.get("Identificativo Comma", "")) + " == " + str(w.get("Identificativo Comma", "")))
                                                if (nojunkchars(z.get("Contenuto Comma", "-")) == nojunkchars(w.get("Contenuto Comma", ""))):
                                                    w["similarita1"] = 1
                                                    w["motivo"] = "Confermato"
                                                    w["Contenuto"] = w.get("Contenuto Comma")
                                                    w["relazione_pagina"] = z.get("Pagina", "")
                                                    w["relazione_articolo"] = z.get("Articolo", "")
                                                    w["relazione_Parte"] = z.get("Parte", "")
                                                    w["relazione_Titolo"] = z.get("Titolo", "")
                                                    w["relazione_Capitolo"] = z.get("Capitolo", "")
                                                    w["relazione_Allegato"] = z.get("Allegato", "")
                                                    w["relazione_Sezione"] = z.get("Sezione", "")
                                                    w["relazione_comma"] = z.get("Identificativo Comma", "")
                                                    w["relazione_contenuto"] = z.get("Contenuto Comma")
                                                    w["relazione_hash"] = z.get("Hash", "")
                                                    z["similarita1"] = 1
                                                    z["motivo"] = "Confermato"
                                                    ContCommmaConfermato +=1                                        
                                                    debug_log.append("                 Il Comma " + str(w.get("Identificativo Comma")) + " è stato CONFERMATO [" + str(z.get("Hash", "")) +"] - [" + str(w.get("Hash", "")) + "]  -->" + str(ContCommmaConfermato))
                                                else:
                                                    if (z.get(("Hash", "")) == 0):
                                                        w["similarita1"] = 1
                                                        w["motivo"] = "Inserito"
                                                        w["Contenuto"] = w["Contenuto Comma"]
                                                        ContCommmaInserito +=1
                                                        debug_log.append("                 Il Comma " + str(w.get("Identificativo Comma")) + " è stato INSERITO -->" + str(ContCommmaInserito))
                                                    else:
                                                        w["similarita1"] = 1
                                                        w["motivo"] = "Aggiornato"
                                                        w["Contenuto"] = w["Contenuto Comma"]
                                                        w["relazione_pagina"] = z.get("Pagina", "")
                                                        w["relazione_articolo"] = z.get("Articolo", "")
                                                        w["relazione_Parte"] = z.get("Parte", "")
                                                        w["relazione_Titolo"] = z.get("Titolo", "")
                                                        w["relazione_Capitolo"] = z.get("Capitolo", "")
                                                        w["relazione_Allegato"] = z.get("Allegato", "")
                                                        w["relazione_Sezione"] = z.get("Sezione", "")
                                                        w["relazione_comma"] = z.get("Identificativo Comma", "")
                                                        w["relazione_contenuto"] = z.get("Contenuto Comma", "") if (z.get("Contenuto Comma") != "") else z.get("Contenuto")
                                                        w["relazione_hash"] = z.get("Hash", "")
                                                        z["similarita1"] = 1
                                                        z["motivo"] = "Aggiornato"
                                                        ContCommmaAggiornato +=1
                                                        debug_log.append("                 Il Comma " + str(w.get("Identificativo Comma")) + " è stato AGGIORNATO [" + str(z.get("Hash", "")) + "] - [" + str(w.get("Hash", "")) + "]  -->" + str(ContCommmaAggiornato))
                                        if ("similarita1" not in w):
                                            w["similarita1"] = 1
                                            w["motivo"] = "Inserito"
                                            ContCommmaInserito +=1
                                        # else:
                                        #    print ("Do Nothing")
                        else:
                            w["similarita1"] = 1
                            w["motivo"] = "Scartato"
                    elif ( w.get("Tipo", "") == "Sottocomma"):
                        w["similarita1"] = 1
                        w["motivo"] = "Scartato"

            else:
                print (" record ", IdentificativoArticolo_2, " NON considerato valido")
                x["similarita1"] = 1
                x["motivo"] = "Scartato"  

    debug_log.append(" ESTENDO a Commi e Sottocommi i risultati degli Articoli")
    for j,soloarticoli in enumerate(articoli_1):
        if ((soloarticoli.get("Tipo", "") == "Articolo") and "similarita1" in y):
            for j, solocommi in enumerate(articoli_1):
                if (
                    (solocommi.get("Tipo", "") == "Comma")
                    or (solocommi.get("Tipo", "") == "Sottocomma")
                ) and (
                    solocommi.get("Articolo", "") == soloarticoli.get("Articolo", "")
                ):
                    solocommi["similarita1"] = soloarticoli.get("similarita1", "")
                    solocommi["motivo"] = soloarticoli.get("motivo", "")

    numeroaggiunti = 0
    debug_log.append(" ######## VERIFICO Articoli e Commi provenienti dal documento1 (precedente)" + str(len(articoli_1)))
    for j,recuperi in enumerate(articoli_1):
        if ("similarita1" not in recuperi):
            if ((recuperi.get("Tipo", "") == "Articolo") or recuperi.get("Tipo", "") == "Comma"):   #==> Solamente gli ARTICOLI
                debug_log.append(" AGGIUNGO (da documento1)" + str(recuperi.get("Tipo")) + " " + str(recuperi.get("Articolo", "")) + " - " + str(recuperi.get("Identificativo Comma", "")))
                recuperi["similarita1"] = 1
                recuperi["motivo"] = "Eliminato"
                numeroaggiunti +=1
                # articoli_2.append(y)
                # I record del documento1 considerati Eliminati verranno inseriti dopo la verifica effettuata dall'AI
    debug_log.append(" ######## VERIFICATI Articoli e Commi provenienti dal documento1 (precedente)" + str(numeroaggiunti))

    indiciDaUsare = []                          
    batches = []     
    testoDaValutare = ""                  

    # Recupero i record "Inseriti" nel secondo documento e "Eliminati" nel primo documento, verificando che non ci siano sovrapposizioni ma esclusioni dovute esclusivamente al titolo
    for i,SimiliDoc2 in enumerate(articoli_2):
        if ((SimiliDoc2.get("motivo", "") == "Inserito") and (SimiliDoc2.get("Tipo", "") == "Articolo")): # or (y.get("Tipo", "") == "Comma"))):     #==> Recuperiamo solamente gli Articoli
            for j,SimiliDoc1 in enumerate(articoli_1):
                if (SimiliDoc1.get("motivo", "") == "Eliminato") and (SimiliDoc2.get("Tipo", "") == SimiliDoc1.get("Tipo", "")):
                    debug_log.append(" VERIFICO SE (" + SimiliDoc2.get('Tipo', '') + " doc1)" + str(SimiliDoc2.get("Pagina", ""))  + " = (" + SimiliDoc1.get('Tipo', '') + " doc2)" + str(SimiliDoc1.get("Pagina", ""))) 
                    debug_log.append("                                   Titoli Articoli" + str(SimiliDoc2.get("Articolo", ""))  + " = " + str(SimiliDoc1.get("Articolo", "")))
                    debug_log.append("                                   Titoli Commi" + str(SimiliDoc2.get("Identificativo Comma", ""))  + " = " + str(SimiliDoc1.get("Identificativo Comma", "")))
                    # CONTROLLO Similarità dei titoli
                    indiciDaUsare.append([i , j])
                    batches.append(
                                        [
                                            SystemMessage(content=system_prompt),
                                            HumanMessage(
                                                content="\n".join([
                                                    "Valuta la corrispondenza dei due titoli relativi ai due documenti legislativi che ti vengono forniti, assegnando alla corrispondenza un valore tra 0 e 30.",
                                                    f"I titoli sono generalmente costituiti da una 'Parte', un 'Titolo' e un 'Capitolo', possono esserci anche una 'Sezione' e un 'Allegato': nella tua valutazione controlla attentamente i valori collegati a queste componenti.",
                                                    f"titolo del {SimiliDoc2.get('Tipo', '')} del documento2: {SimiliDoc2.get('Articolo', '')}\n",
                                                    f"titolo del {SimiliDoc1.get('Tipo', '')} del documento1: {SimiliDoc1.get('Articolo', '')}\n",
                                                ])
                                            ),
                                        ]
                                    )     
    debug_log.append(" INTERROGO AI per Articoli Eliminati: " + str(len(batches)))
    if batches:
        risultati_similarita = await _invoke_with_fallback_batch(
            _get_llm_similarita(),
            _get_llm_similarita_fallback(),
            batches,
            RunnableConfig(max_concurrency=MAX_CONCURRENCY),
        )       
        for j, idx in enumerate(risultati_similarita):
            Indice2, Indice1 = indiciDaUsare[j]
            if idx.coefficiente_correlazione >= 20 and articoli_2[Indice2]["similarita1"] < idx.coefficiente_correlazione:
                debug_log.append(" Un COEFFICIENTE (" + str(idx.coefficiente_correlazione) + ") per " + str(articoli_2[Indice2].get("Pagina", ""))  + " = (pagina doc2)" + str(articoli_1[Indice1].get("Pagina", "")) + " Titoli Articoli" + str(articoli_2[Indice2].get("Articolo", ""))  + " = " + str(articoli_1[Indice1].get("Articolo", "")))
                debug_log.append("                     " + str(articoli_2[Indice2].get("Identificativo Comma", ""))  + " = (pagina doc2)" + str(articoli_1[Indice1].get("Identificativo Comma", "")))
                if articoli_2[Indice2].get("Hash", "-") == articoli_1[Indice1].get("Hash", ""):
                    debug_log.append("             HASH UGUALI")
                    articoli_2[Indice2]["relazione_pagina"] = articoli_1[Indice1].get("Pagina", "")
                    articoli_2[Indice2]["relazione_articolo"] = articoli_1[Indice1].get("Articolo", "")
                    articoli_2[Indice2]["relazione_Parte"] = articoli_1[Indice1].get("Parte", "")
                    articoli_2[Indice2]["relazione_Titolo"] = articoli_1[Indice1].get("Titolo", "")
                    articoli_2[Indice2]["relazione_Capitolo"] = articoli_1[Indice1].get("Capitolo", "")
                    articoli_2[Indice2]["relazione_Allegato"] = articoli_1[Indice1].get("Allegato", "")
                    articoli_2[Indice2]["relazione_Sezione"] = articoli_1[Indice1].get("Sezione", "")
                    articoli_2[Indice2]["relazione_comma"] = articoli_1[Indice1].get("Identificativo Comma", "")
                    articoli_2[Indice2]["relazione_contenuto"] = articoli_1[Indice1].get("Contenuto", "")
                    articoli_2[Indice2]["relazione_hash"] = articoli_1[Indice1].get("Hash", "")
                    articoli_2[Indice2]["similarita1"] = idx.coefficiente_correlazione
                    articoli_2[Indice2]["motivo"] = "Confermato"
                    articoli_1[Indice1]["similarita1"] = idx.coefficiente_correlazione
                    articoli_1[Indice1]["motivo"] = "Confermato"
                else:
                    debug_log.append("             HASH DIVERSI")
                    if  articoli_2[Indice2]["Tipo"] == "Articolo":
                        articoli_2[Indice2]["relazione_pagina"] = articoli_1[Indice1].get("Pagina", "")
                        articoli_2[Indice2]["relazione_articolo"] = articoli_1[Indice1].get("Articolo", "")
                        articoli_2[Indice2]["relazione_Parte"] = articoli_1[Indice1].get("Parte", "")
                        articoli_2[Indice2]["relazione_Titolo"] = articoli_1[Indice1].get("Titolo", "")
                        articoli_2[Indice2]["relazione_Capitolo"] = articoli_1[Indice1].get("Capitolo", "")
                        articoli_2[Indice2]["relazione_Allegato"] = articoli_1[Indice1].get("Allegato", "")
                        articoli_2[Indice2]["relazione_Sezione"] = articoli_1[Indice1].get("Sezione", "")
                        articoli_2[Indice2]["relazione_comma"] = articoli_1[Indice1].get("Identificativo Comma", "")
                        articoli_2[Indice2]["relazione_contenuto"] = articoli_1[Indice1].get("Contenuto", "")
                        articoli_2[Indice2]["relazione_hash"] = articoli_1[Indice1].get("Hash", "")
                        articoli_2[Indice2]["similarita1"] = idx.coefficiente_correlazione
                        articoli_2[Indice2]["motivo"] = "Da Verificare II"
                        articoli_1[Indice1]["similarita1"] = idx.coefficiente_correlazione
                        articoli_1[Indice1]["motivo"] = "Da Verificare II"

    # Si associano i Commi relativi all'Articolo per il quale si ha corrispondenza di similarità.
    # Nel caso i Commi abbiano lo stesso nome, ok, altrimenti si fa intervenire l'AI per valutare la similarità.
    for indice, articolodavalutare in enumerate(articoli_2):
        if ( articolodavalutare.get("Tipo", "") == "Articolo"):
            if ( articolodavalutare.get("motivo", "") == "Da Verificare II"):
                for l, commadavalutare in enumerate(articoli_2):                                                     
                    if ( commadavalutare.get("Tipo", "") == "Comma"):  #Consideriamo i Commi del documento2
                        if (str(commadavalutare.get("Articolo", "")) == articolodavalutare.get("Articolo")):
                            debug_log.append(
                                f"{commadavalutare.get('Articolo', '')}-{commadavalutare.get('Identificativo Comma', '')}-->{commadavalutare.get('motivo', '')}"
                            )
                            for m,record_doc1 in enumerate(articoli_1):
                                CalcoloDistanza = 0
                                MatchCorretto = False
                                levenshteinMigliore = 100
                                if ((record_doc1.get("Tipo", "-") == commadavalutare.get("Tipo", ""))):
                                    #                                    debug_log.append("                                                       Il tipo è uguale")
                                    if (record_doc1.get("Articolo", "-") == articolodavalutare.get("relazione_articolo", "")):
                                        #                                        debug_log.append("                                                       Trovato l'Articolo")
                                        CalcoloDistanza = levenshtein(nojunkchars(record_doc1.get("Identificativo Comma", "-")), nojunkchars(commadavalutare.get("Identificativo Comma", "")))
                                        if (CalcoloDistanza == 0):
                                            #                                            debug_log.append("                                                       Trovato il Comma")
                                            debug_log.append(
                                                f"{commadavalutare.get('Articolo', '')}-{commadavalutare.get('Identificativo Comma', '')}-->   DA VERIFICARE -->{CalcoloDistanza}"
                                            )
                                            MatchCorretto == True
                                            if (nojunkchars(commadavalutare.get("Contenuto Comma", "-")) == nojunkchars(record_doc1.get("Contenuto Comma", ""))):
                                                debug_log.append(
                                                    f"{commadavalutare.get('Articolo', '')}-{commadavalutare.get('Identificativo Comma', '')}-->   CONFERMATO!"
                                                )
                                                commadavalutare["similarita1"] = 1
                                                commadavalutare["motivo"] = "Confermato"
                                                commadavalutare["relazione_pagina"] = record_doc1.get("Pagina", "")
                                                commadavalutare["relazione_articolo"] = record_doc1.get("Articolo", "")
                                                commadavalutare["relazione_Parte"] = record_doc1.get("Parte", "")
                                                commadavalutare["relazione_Titolo"] = record_doc1.get("Titolo", "")
                                                commadavalutare["relazione_Capitolo"] = record_doc1.get("Capitolo", "")
                                                commadavalutare["relazione_Allegato"] = record_doc1.get("Allegato", "")
                                                commadavalutare["relazione_Sezione"] = record_doc1.get("Sezione", "")
                                                commadavalutare["relazione_comma"] = record_doc1.get("Identificativo Comma", "")
                                                commadavalutare["relazione_hash"] = record_doc1.get("Hash", "")
                                                record_doc1["similarita1"] = 1
                                                record_doc1["motivo"] = "Confermato"
                                                if (commadavalutare.get("Tipo", "") == "Articolo"):
                                                    commadavalutare["relazione_contenuto"] = record_doc1.get("Contenuto", "")
                                                    commadavalutare["Contenuto"] = commadavalutare.get("Contenuto")
                                                    debug_log.append(" AGGIUNGO (da documento1) il contenuto dell'Articolo ==> " + str(commadavalutare.get("Contenuto", "")))
                                                else:
                                                    commadavalutare["relazione_contenuto"] = record_doc1.get("Contenuto Comma", "")
                                                    commadavalutare["Contenuto"] = commadavalutare.get("Contenuto Comma")
                                                    debug_log.append(" AGGIUNGO (da documento1) il contenuto del Comma ==> " + str(commadavalutare.get("Contenuto Comma", "")))
                                            else:
                                                debug_log.append(
                                                    f"{commadavalutare.get('Articolo', '')}-{commadavalutare.get('Identificativo Comma', '')}-->   AGGIORNATO!"
                                                )
                                                commadavalutare["similarita1"] = 1
                                                commadavalutare["motivo"] = "Aggiornato"
                                                commadavalutare["Contenuto"] = commadavalutare["Contenuto Comma"]
                                                commadavalutare["relazione_pagina"] = record_doc1.get("Pagina", "")
                                                commadavalutare["relazione_articolo"] = record_doc1.get("Articolo", "")
                                                commadavalutare["relazione_Parte"] = record_doc1.get("Parte", "")
                                                commadavalutare["relazione_Titolo"] = record_doc1.get("Titolo", "")
                                                commadavalutare["relazione_Capitolo"] = record_doc1.get("Capitolo", "")
                                                commadavalutare["relazione_Allegato"] = record_doc1.get("Allegato", "")
                                                commadavalutare["relazione_Sezione"] = record_doc1.get("Sezione", "")
                                                commadavalutare["relazione_comma"] = record_doc1.get("Identificativo Comma", "")
                                                commadavalutare["relazione_contenuto"] = record_doc1.get("Contenuto Comma", "") if (record_doc1.get("Contenuto Comma") != "") else record_doc1.get("Contenuto")
                                                commadavalutare["relazione_hash"] = record_doc1.get("Hash", "")
                                                record_doc1["similarita1"] = 1
                                                record_doc1["motivo"] = "Aggiornato"   
                                        elif ((MatchCorretto == False) and (CalcoloDistanza < (len(nojunkchars(commadavalutare.get("Identificativo Comma", "")))*0.1)) and (CalcoloDistanza < levenshteinMigliore)):   # NON è presente un match perfetto e la distanza di levenshtein è inferiore al 10% della lunghezza del testo considerato (l'identificativo del Comma)
                                            debug_log.append(
                                                f"{commadavalutare.get('Articolo', '')}-{commadavalutare.get('Identificativo Comma', '')}-->   DA VERIFICARE PROSSIMO-->{CalcoloDistanza}"
                                            )
                                            levenshteinMigliore = CalcoloDistanza
                                            commadavalutare["similarita1"] = 1
                                            commadavalutare["motivo"] = "Aggiornato"
                                            commadavalutare["Contenuto"] = commadavalutare["Contenuto Comma"]
                                            commadavalutare["relazione_pagina"] = record_doc1.get("Pagina", "")
                                            commadavalutare["relazione_articolo"] = record_doc1.get("Articolo", "")
                                            commadavalutare["relazione_Parte"] = record_doc1.get("Parte", "")
                                            commadavalutare["relazione_Titolo"] = record_doc1.get("Titolo", "")
                                            commadavalutare["relazione_Capitolo"] = record_doc1.get("Capitolo", "")
                                            commadavalutare["relazione_Allegato"] = record_doc1.get("Allegato", "")
                                            commadavalutare["relazione_Sezione"] = record_doc1.get("Sezione", "")
                                            commadavalutare["relazione_comma"] = record_doc1.get("Identificativo Comma", "")
                                            commadavalutare["relazione_contenuto"] = record_doc1.get("Contenuto Comma", "") if (record_doc1.get("Contenuto Comma") != "") else record_doc1.get("Contenuto")
                                            commadavalutare["relazione_hash"] = record_doc1.get("Hash", "")
                                            record_doc1["similarita1"] = 1
                                            record_doc1["motivo"] = "Aggiornato"  

    indiciDaUsare = []                          
    batches = []     
    testoDaValutare = ""                  

    for indice, articolodadescrivere in enumerate(articoli_2):  # def estratto for sottocommi with similarita 1
        if articolodadescrivere.get('Contenuto', ''):
            testoDaValutare = articolodadescrivere.get('Contenuto', '')
        else:
            testoDaValutare = articolodadescrivere.get('Contenuto Comma', '')
        testoinvocazione = "\n".join(
            [
                "In base al tipo di azione fatta sul comma, scrivi una frase (< 100 parole) che spieghi ",
                "l’impatto del cambiamento dal testo originale al testo aggiornato.\n tipo di modifica: ",
                articolodadescrivere.get("motivo", ""),
                "\n   contenuto aggiornato: '",
                testoDaValutare,
                "'",
                "\n    contenuto originario: '",
                articolodadescrivere.get("relazione_contenuto", ""),
                "'",
            ]
        )
        articolodadescrivere["Invocazione"] = testoinvocazione

        if (articolodadescrivere.get("Tipo", "") == "Comma") and (
            articolodadescrivere.get("motivo", "") == "Aggiornato"
        ):
            indiciDaUsare.append(indice)
            batches.append(
                    [
                        SystemMessage(content=system_prompt),
                        HumanMessage(
                            content="\n".join([
                                "In base al tipo di modifica, scrivi una frase (< 100 parole) che spieghi l’impatto ",
                                "del cambiamento dal testo emendato al testo emendativo.\n",
                                f"tipo di modifica: {articolodadescrivere.get('motivo')}\n",
                                f"contenuto aggiornato: '{testoDaValutare}'\n",
                                f"contenuto originario: '{articolodadescrivere.get('relazione_contenuto')}'",
                            ])
                        ),
                    ]
                )        

    print("Elenco batches: ", len(articoli_2) , " -> ", len(batches))

    if batches:
        risultati_impatti = await _invoke_with_fallback_batch(
            _get_llm_2(),
            _get_llm_fallback_raw(),
            batches,
            RunnableConfig(max_concurrency=MAX_CONCURRENCY),
        )       
        for j, idx in enumerate(risultati_impatti):
            Indice = indiciDaUsare[j]
            articoli_2[Indice]["Descrizione"] = idx.dettaglio    #idx.model_dump(exclude_none=True)[dettaglio]

    debug_log.append(" AGGIUNGO Articoli e Commi provenienti dal documento1 (precedente)")
    for j,articoliecommi_doc1 in enumerate(articoli_1):
        if ((articoliecommi_doc1.get("Tipo", "") == "Articolo" or articoliecommi_doc1.get("Tipo", "") == "Comma") and articoliecommi_doc1.get("motivo", "") == "Eliminato"):
            debug_log.append(" AGGIUNGO (da documento1)" + str(articoliecommi_doc1.get("Tipo")) + " " + str(articoliecommi_doc1.get("Articolo", "")) + " - " + str(articoliecommi_doc1.get("Comma", "")) + "]  -->" + str(ContCommmaAggiornato))
            articoliecommi_doc1["relazione_pagina"] = articoliecommi_doc1.get("Pagina", "")
            articoliecommi_doc1["relazione_articolo"] = articoliecommi_doc1.get("Articolo", "")
            articoliecommi_doc1["relazione_Parte"] = articoliecommi_doc1.get("Parte", "")
            articoliecommi_doc1["relazione_Titolo"] = articoliecommi_doc1.get("Titolo", "")
            articoliecommi_doc1["relazione_Capitolo"] = articoliecommi_doc1.get("Capitolo", "")
            articoliecommi_doc1["relazione_Allegato"] = articoliecommi_doc1.get("Allegato", "")
            articoliecommi_doc1["relazione_Sezione"] = articoliecommi_doc1.get("Sezione", "")
            articoliecommi_doc1["relazione_comma"] = articoliecommi_doc1.get("Identificativo Comma", "")
            articoliecommi_doc1["relazione_hash"] = articoliecommi_doc1.get("Hash", "")
            articoliecommi_doc1["Articolo"] = ""
            articoliecommi_doc1["Titolo Articolo"] = ""
            articoliecommi_doc1["Identificativo Comma"] = ""
            articoliecommi_doc1["Hash"] = ""

            if (articoliecommi_doc1.get("Tipo", "") == "Articolo"):
                articoliecommi_doc1["relazione_contenuto"] = articoliecommi_doc1.get("Contenuto", "")
                debug_log.append(" AGGIUNGO (da documento1) il contenuto dell'Articolo: Ultimo Atto ==> " + str(articoliecommi_doc1.get("Contenuto", "")))
            else:
                articoliecommi_doc1["relazione_contenuto"] = articoliecommi_doc1.get("Contenuto Comma", "")
                debug_log.append(" AGGIUNGO (da documento1) il contenuto del Comma: Ultimo Atto ==> " + str(articoliecommi_doc1.get("Contenuto Comma", "")))

            articoliecommi_doc1["Contenuto Comma"] = ""
            articoliecommi_doc1["Contenuto"] = ""
            articoli_2.append(articoliecommi_doc1) 
            # I record del documento1 considerati Eliminati verranno inseriti dopo la verifica effettuata dall'AI

    for j,articoliecommi_tutti in enumerate(articoli_2):
        articoliecommi_tutti["Tipo"] = "Capitolo" if (articoliecommi_tutti.get("Tipo", "") == "Articolo") else "Paragrafo"

    debug_log.append("       ======>>> Inserisco le Descrizioni ai Record Inseriti o Eliminati <<<======     ")

    indiciConSpecifiche = []                          
    batchesConSpecifiche = []     
    testoDaValutare = ""                  

    for indice_art2, articoloEliminato_Inserito in enumerate(articoli_2):
        if articoloEliminato_Inserito.get("motivo", "") == "Inserito":
            if articoloEliminato_Inserito.get("Contenuto"):
                testoDaValutare = articoloEliminato_Inserito.get("Contenuto", "")
                debug_log.append("            =>>> Inserito")
            else:
                testoDaValutare = articoloEliminato_Inserito.get("Contenuto Comma", "")
                debug_log.append("            =>>> Inserito")
        elif articoloEliminato_Inserito.get("motivo", "") == "Eliminato":     
            testoDaValutare = articoloEliminato_Inserito.get("relazione_contenuto", "")
            debug_log.append("            =>>> Eliminato")
        elif (articoloEliminato_Inserito.get("motivo", "") == "Confermato") and ((articoloEliminato_Inserito.get("Tipo", "") == "Comma") or (articoloEliminato_Inserito.get("Tipo", "") == "Paragrafo")):     
            testoDaValutare = articoloEliminato_Inserito.get("Contenuto Comma", "")
            debug_log.append("            =>>> Confermato")
        else:
            debug_log.append("       ======>>> " + str(articoloEliminato_Inserito.get("motivo", "")) + " NON E' RECUPERATO!")
            continue
        TipoRecord = articoloEliminato_Inserito.get("Tipo", "")
        MotivoRecord = articoloEliminato_Inserito.get("motivo", "")
        testoinvocazione = "\n".join([f"Il {TipoRecord} seguente è stato {MotivoRecord}.",
        "Valuta l'impatto di questo cambiamento all'interno del documento: scrivi una frase (< 100 parole) che sintetizzi il significato del testo ",
        f"sottolineando che sia stato {MotivoRecord} dal corpo completo del documento.",
        f"Il contenuto del {TipoRecord} è il seguente: '{testoDaValutare}'"])

        debug_log.append(" ===> INVOCO AI x " + str(testoinvocazione))

        indiciConSpecifiche.append(indice_art2)
        batchesConSpecifiche.append(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(
                        content="\n".join([
                            f"Il {TipoRecord} seguente è stato {MotivoRecord}.",
                            "Valuta l'impatto di questo cambiamento all'interno del documento, nel suo complesso e scrivi una frase (< 100 parole) che spieghi l’impatto di tale cambiamento.",
                            f"\n Il contenuto del {TipoRecord} è il seguente: '{testoDaValutare}'",
                        ])
                    ),
                ]
            )        

    print("Elenco batches: ", len(articoli_2) , " -> ", len(batchesConSpecifiche))

    if batchesConSpecifiche:
        risultati_ConSpecifiche = await _invoke_with_fallback_batch(
            _get_llm_Description(),
            _get_llm_Description_fallback(),
            batchesConSpecifiche,
            RunnableConfig(max_concurrency=MAX_CONCURRENCY),
        )       
        for j, idx_ConSpecifiche in enumerate(risultati_ConSpecifiche):
            IndiceSpecifico = indiciConSpecifiche[j]
            articoli_2[IndiceSpecifico]["Descrizione"] = idx_ConSpecifiche.DescrizioneCambiamento

    try:
        from utils.blob_storage_client import upload_debug_log
        upload_debug_log("debug_log_versioning.txt", "\n".join(debug_log))
    except Exception:
        pass

    return articoli_2
