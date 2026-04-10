import json
import fitz  # PyMuPDF
import pprint
from collections import Counter
from pathlib import Path
import re
import os
from lex_package.parsing_utils.parser_banca import identify_repeated_headers_footers


_SRCDIR = Path(__file__).resolve().parents[2]

_HEADER_RE = re.compile(
    r"""^                    # (opz) inizio stringa
        \s*Titolo\s+         # "Titolo" + spazi
        .+?                  #   qualunque testo (non greedy)
        \s+Versione\s+       # "Versione"
        \d+(?:\.\d+)+        #   6.1   |  12.3.4 …
        \s+Stato\s+\w+       # "Stato" + una parola
        \s+Data\s+di\s+Pubblicazione\s+
        \d{2}/\d{2}/\d{4}    #   04/12/2024
        \s*                  # (opz) spazi finali
    """,
    flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

_FOOTER_RE = re.compile(
    r"""\bQuesto\s+documento\s+è\s+proprietà\s+di\s+Cassa\s+depositi\s+e\s+prestiti
        \s+S\.p\.A\.\s+che\s+se\s+ne\s+riserva\s+tutti\s+i\s+diritti    # frase costante
        (?:\s+\d{1,4})?                                                # ← opz.: numero pagina
        \s*                                                            # spazi finali
    """,
    flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

_ID_TIT_RE = re.compile(
    r"""\s*
        (?P<id>\d+(?:\.\d+)*)        # 1  oppure 1.3  oppure 2.1.4 …
        (?:\s*[.)])?                #   opz.: puntino o parentesi
        [\s\r\n]+                   #   spazi / new-line
        (?P<title>[^\n]+)\n+           #  → titolo fino al primo \n
    """,
    flags=re.VERBOSE,
)

_TITOLOPARAGRAFO_RE = re.compile(r"""(?m)^\s*(?P<id>\d+(?:\.\d+)*)(?:\s*[.)])?[\s|\n|\t|\r]+(?P<title>[^\r\n]+)\n?""",    flags=re.VERBOSE)


def rebuild_table(table):
    """
    Ricostruisce le tabelle unendo righe spezzate.
    """
    ricostruita = []
    riga_corrente = None

    for riga in table:
        if riga[0] is not None:
            if riga_corrente:
                ricostruita.append(riga_corrente)
            riga_corrente = riga.copy()
        else:
            for i, cella in enumerate(riga):
                if cella:
                    if riga_corrente[i]:
                        riga_corrente[i] += ' ' + cella
                    else:
                        riga_corrente[i] = cella

    if riga_corrente:
        ricostruita.append(riga_corrente)

    return ricostruita


def clean_table(table):
    """
    Sostituisce i None con stringhe vuote.
    """
    for i, riga in enumerate(table):
        table[i] = [cella if cella is not None else '' for cella in riga]
    return table


def _detect_page_offset(doc, indice):
    """Rileva l'offset tra la numerazione dell'indice e le pagine reali del PDF.

    Cerca il titolo della prima voce dell'indice nel testo delle prime N pagine
    e calcola la differenza rispetto alla pagina indicata nell'indice.
    """
    if not indice:
        return 0
    first = indice[0]
    target_title = first["titolo"].strip().lower()
    expected_page = first["pagina_destinazione"] - 1  # 0-based

    for page_idx in range(min(len(doc), 10)):
        text = doc[page_idx].get_text().lower()
        # Cerchiamo il titolo ma NON nella pagina INDICE
        if target_title in text and "indice" not in text[:200].lower():
            offset = page_idx - expected_page
            if offset > 0:
                return offset
    return 0


def parser_capitolo(pdf_path, indice):
    doc = fitz.open(pdf_path)
    repeated_lines = identify_repeated_headers_footers(doc, min_repeats=5)
    print(f"############################## PARSER CAPITOLO ###############################")
    print(f"[INFO] Documento PDF caricato: {pdf_path}")

    # Rileva offset pagine (copertina, indice, ecc.)
    page_offset = _detect_page_offset(doc, indice)
    print(f"[INFO] Page offset rilevato: {page_offset}")

    debug_log = []
    result = []
    content_by_page = {}

    formatted_indice = pprint.pformat(indice)
    debug_log.append("##################################################################################################")
    debug_log.append("################################### STRUTTURA INIZIALE ###########################################")
    debug_log.append(f"################################### PAGE OFFSET: {page_offset} ###################################")
    debug_log.append("##################################################################################################")
    debug_log.append(formatted_indice)

    for idx, voce in enumerate(indice):
        id_capitolo = voce["identificativo"]
        titolo = voce["titolo"]
        pagina_start = voce["pagina_destinazione"] - 1 + page_offset

        if idx + 1 < len(indice):
            pagina_end = indice[idx + 1]["pagina_destinazione"] - 1 + page_offset
        else:
            pagina_end = len(doc)

        contenuto_capitolo = []

        debug_log.append("\n\n##############################################################################################")
        debug_log.append("###########       " + id_capitolo + " " + titolo)
        debug_log.append("###########       " + str(pagina_start) + " - " + str (pagina_end))
        debug_log.append("##################################################################################################")

        Titolo_Trovato = False
        TestoIntero = ""
        # Flag: entry testuale senza ID numerico (dal pattern_testuale di parser_indice)
        is_text_entry = not id_capitolo

        # if (pagina_start == pagina_end):
        if pagina_end < len(doc):
            pagina_end +=1

        for page_num in range(pagina_start, pagina_end):
            page = doc[page_num]
            tabs = page.find_tables()

            x0_t = 0
            y0_t = 0
            x1_t = 0
            y1_t = 0

            debug_log.append(f"############################## Analizzo Pagina {page_num} ... ")

            #############################################################################
            ##################### TEST DI GESTIONE TABELLE ##############################
            #############################################################################

            if tabs.tables:
                blocks = [b for b in page.get_text("blocks") if b[6] == 0]
                for i, table in enumerate(tabs.tables):
                    debug_log.append(f"    📌 ############################### Tabella {i+1} ###############################")
                    data = table.extract()
                    debug_log.append("    📌 ###   Contenuto nei blocchi " + str(len(data)))
                    x0_t, y0_t, x1_t, y1_t = table.bbox
                    debug_log.append(f"    📌 ###   Estremi: {x0_t}, {y0_t}, {x1_t}, {y1_t}")

                    # Initialize page in content_by_page if not exists
                    if page_num not in content_by_page:
                        content_by_page[page_num] = {
                            "page_number": page_num,
                            "content": []
                        }

                    # Add table content to the specific page
                    content_by_page[page_num]["content"].append({
                        "type": "table",
                        "index": i,
                        "bbox": [x0_t, y0_t, x1_t, y1_t],
                        "data": data,
                        "used": False
                    })
                debug_log.append(f"    📌 #############################################################################")

            lines = []
            lines_span = []

            blocks = page.get_text("blocks")
            height = page.rect.height

            debug_log.append(f"\n    📝 ############################### {str(len(blocks))} BLOCCHI ############################### ")

            for block in blocks:
                y0 = block[1]
                text = block[4].strip()

                x0_b, y0_b, x1_b, y1_b, text, *_ = block
                debug_log.append(f"    📝 ###   Estremi: {x0_b}, {y0_b}, {x1_b}, {y1_b}")

                pos = 'top' if y0_b < height * 0.1 else 'bottom' if y0_b > height * 0.90 else 'middle'

                # if len(text) < 5:
                #     debug_log.append("                 Il testo è troppo corto --> ") # + str(text) + "<--")
                #     continue
                if pos in ['top', 'bottom']:
                    debug_log.append("    📝 ###        Il testo è agli estremi --> " + str(pos) + "<--=-->" + str(text) + "<--")
                    continue
                if text in repeated_lines:
                    debug_log.append("    📝 ###        Il testo è ripetuto     --> " + str(text) + "<--")
                    continue
                if tabs.tables:
                    for tabella in content_by_page[page_num]["content"]:
                        x0_t, y0_t, x1_t, y1_t = tabella["bbox"]
                        if ((x0_b >= x0_t) and (y0_b >= y0_t) and (x1_b <= x1_t) and (y1_b <= y1_t)):
                            if ((tabella["type"] != "table") or (tabella["used"] == True)):
                                # debug_log.append(f"    📝 ###        Il testo è nella tabella {i+1}     --> GIà USATA")
                                continue
                            # Il block è all'interno della table definita prima
                            if (Titolo_Trovato == True):
                                debug_log.append(f"    📝 ###        Il testo è nella tabella {i+1}     --> USIAMOLA!")
                                TestoIntero += "\n" + str(tabella["data"])
                                tabella["used"] = True
                            continue
                debug_log.append("                 Il testo normale --> " + str(text).replace("\n", " ") + " <--")
                text_stripped = text.strip().replace("\n", " ")

                # --- Riconoscimento titolo: numerico o testuale ---
                match_id = _TITOLOPARAGRAFO_RE.search(text)
                title_found_here = False

                if match_id:
                    debug_log.append(f"    📝 ########## TROVATO Capitolo/Paragrafo: {match_id.group('id')} - {match_id.group('title').strip()}")
                    if ((match_id.group("id") == id_capitolo) and (match_id.group("title").strip() == titolo)):
                        Titolo_Trovato = True
                        identificativo = match_id.group("id")
                        titolo_art = match_id.group("title").strip()
                        title_found_here = True
                    elif (match_id.group("title").strip() == titolo):
                        Titolo_Trovato = True
                        identificativo = match_id.group("id")
                        titolo_art = match_id.group("title").strip()
                        title_found_here = True
                    else:
                        if (idx+1>=len(indice)) or (match_id.group("title").strip() == indice[idx + 1]):
                            if (Titolo_Trovato == True):
                                contenuto_capitolo.append({
                                    "tipo": "testo",
                                    "identificativo": identificativo,
                                    "titolo_articolo": titolo_art,
                                    "contenuto": TestoIntero.replace("\n", " ").replace("- ", "-").strip(),
                                })
                                Titolo_Trovato = False
                                debug_log.append(f"                 SCRIVO testo per {identificativo} {titolo_art} len={len(TestoIntero)}")
                        else:
                            if (Titolo_Trovato == True):
                                TestoIntero += "\n" + text
                elif is_text_entry and not Titolo_Trovato:
                    # Fallback: entry testuale senza ID numerico
                    # Cerca il titolo dell'indice nel testo del blocco
                    if titolo and titolo.lower() in text_stripped.lower():
                        Titolo_Trovato = True
                        identificativo = ""
                        titolo_art = titolo
                        title_found_here = True
                        # Raccogli eventuale testo dopo il titolo nello stesso blocco
                        title_pos = text_stripped.lower().find(titolo.lower())
                        remainder = text_stripped[title_pos + len(titolo):].strip()
                        if remainder:
                            TestoIntero += "\n" + remainder
                        debug_log.append(f"    📝 ########## MATCH TESTUALE: {titolo}")

                if not title_found_here and not match_id:
                    if (Titolo_Trovato == True):
                        TestoIntero += "\n" + text
                        debug_log.append("    📝 ###             Concateno il testo...")
            debug_log.append("    📝 ################################# Terminati Blocchi della Pagina")
        debug_log.append("    📝 ################################# Terminate le Pagine del Capitolo")
        if (Titolo_Trovato == True):
            contenuto_capitolo.append(
                {
                    "tipo": "testo",
                    "identificativo": identificativo,   # ← nuovo campo
                    "titolo_articolo": titolo_art,      # ← nuovo campo
                    "contenuto": TestoIntero.replace("\n", " ")
                    .replace("- ", "-")
                    .strip(),
                }
            ) 
            debug_log.append("    📝 ################################# Salvo in contenuto_capitolo contenuto alla fine delle pagine" + " -----> len(contenuto_capitolo)" + str(len(contenuto_capitolo)) + " -----> " + str(Titolo_Trovato))

        # Salva il risultato finale per la voce dell'indice
        result.append(
            {
                "codicedocumento": "R",
                "page": pagina_start,
                "identificativo": id_capitolo,
                "titolo": titolo,
                "codicearticolo": "",
                "contenuto": "",
                "contenuto_parsato": contenuto_capitolo,
            }
        )
        debug_log.append("    📝 ################################# Salvo in result tutto il contenuto_capitolo " + " -----> len(result)" + str(len(result)) + " -----> " + str(Titolo_Trovato))
        debug_log.append("##################################################################################################")
        debug_log.append("###########       " + str(id_capitolo) + " " + str(titolo) + " SALVATO! " + " -----> len(result)" + str(len(result)))
        debug_log.append("##################################################################################################\n\n")

    formatted_result = pprint.pformat(result)
    debug_log.append("##################################################################################################")
    debug_log.append("####################################### RISULTATO ################################################")
    debug_log.append("##################################################################################################")
    debug_log.append(formatted_result)

    # Salva log su file
    _SRCDIR = Path(__file__).resolve().parents[2]

    log_dir = Path(_SRCDIR / "out_parser")
    log_dir.mkdir(parents=True, exist_ok=True)

    log_path = os.path.join(log_dir, "debug_log_parseCapitolo.txt")

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(debug_log))

    # Create structured JSON content
    structured_content = {
        "document_info": {
            "total_pages": len(doc),
            "processed_chapters": len(indice),
            "pdf_path": pdf_path
        },
        "pages": {}
    }

    # Convert content_by_page to string keys for JSON compatibility
    for page_num, page_data in content_by_page.items():
        structured_content["pages"][str(page_num)] = page_data

    content_log_path = os.path.join(log_dir, "content_log.json")
    with open(content_log_path, "w", encoding="utf-8") as f:
        json.dump(structured_content, f, ensure_ascii=False, indent=2)

    return result
