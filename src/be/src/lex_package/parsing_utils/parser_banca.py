import re
import fitz  # PyMuPDF
import json
from collections import Counter
from pathlib import Path
import os



_HEADER_RE = re.compile(
    r"""
    ^\s*                                         # inizio pagina + spazi
    (?:                                          # uno o più blocchi HEADER
        (?:Parte|Titolo|Capitolo|Sezione)        # keyword obbligatoria
        [^\n]*                                   # tutto fino a fine riga
        (?:\n|$)+                                # newline/e o fine stringa
    )+                                           # header formato da ≥1 righe
    """,
    flags=re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)

_HEADER_RE_INTERA = re.compile(
    r"""^                    # (opz) inizio stringa
        \s*Titolo\s+         # "Titolo" + spazi
        .+?                  #     qualunque testo (non greedy)
        \s+Versione\s+       # "Versione"
        \d+(?:\.\d+)+        #     6.1   |  12.3.4 …
        \s+Stato\s+\w+       # "Stato" + una parola
        \s+Data\s+di\s+Pubblicazione\s+
        \d{2}/\d{2}/\d{4}    #     04/12/2024
        \s*                  # (opz) spazi finali
    """,
    flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

#      [-–]   Aggiungiamo i "due meno"?
#      Regex aggiornata: Titolo e Capitolo sono opzionali per supportare
#      documenti AVC-style (PARTE → Sezione, senza Titolo/Capitolo).
_HEADER_RE_IntestazioneBdI = re.compile(
    r"""(?P<Header>
        (?P<Parte>Parte\s+(?:Prima|Seconda|Terza|Quarta|Quinta|Sesta|Settima|Ottava|Nona|Decima|[IVXLC]+)\b[^\n\r]*)[\n\r]+
        (?:(?P<Titolo>Titolo\s*[^\n\r]+)[\n\r]+)?
        (?:(?P<Capitolo>Capitolo\s*[^\n\r]+)[\n\r]+)?
        (?:(?P<Allegato>Allegato\s*[^\n\r]+)[\n\r]+)?
        (?P<Sezione>Sezione\s*[^\n\r]+)
    )""",
    flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
)


_CAPS_TITLE_RE = re.compile(
    r"^[A-ZÀ-ÖØ-ÝÆŒ0-9 ,'.\-–]{5,}$"  # ≥5 car. (evita righe vuote o ‘SEZIONE I’)
)


def identify_repeated_headers_footers(doc, min_repeats=3):
    line_positions = {}
    ListaRigheRipetute = {}
    pattern_footer = r"(\d+)?\s*(.*)\s*(\d+)?$"

    for i, page in enumerate(doc):
        blocks = page.get_text("blocks")
        height = page.rect.height
        
        for block in blocks:
            y0 = block[1]
            text = block[4].strip()

            if len(text) < 5:
                continue
            pos = 'top' if y0 < height * 0.2 else 'bottom' if y0 > height * 0.90 else 'middle'
            if pos in ['top', 'bottom']:
                line_positions.setdefault(text, []).append(pos)
                firstmatch = re.match(pattern_footer, text)
                if firstmatch:
                    PrimaRiga   = firstmatch.group(2)
                    if PrimaRiga:
                        for linea_2 in line_positions:
                            SecondMatch = re.match(pattern_footer, linea_2)
                            if SecondMatch:
                                SecondaRiga = SecondMatch.group(2)
                                if SecondaRiga:
                                    if (PrimaRiga == SecondaRiga):
                                        line_positions.setdefault(text, []).append(pos)
                                        line_positions.setdefault(linea_2, []).append(pos)

    # #####visualizzo le più ripetute
    # sorted_lines = sorted(repeated, key=lambda x: x[1], reverse=True)
    # for rank, (line, count) in enumerate(sorted_lines[:10], start=1):
    #     print(f"(Regolamento) RIPETIZIONI: {rank}. '{line}' - {count} ripetizioni")

    ListaRigheRipetute = [
            line for line, pos_list in line_positions.items()
            if len(pos_list) >= min_repeats and (
                pos_list.count("top") >= min_repeats or pos_list.count("bottom") >= min_repeats
            )
        ]
#    print("@##################################################################@")
#    print(f" 🍆🍆🍆 {ListaRigheRipetute}")
#    print("@##################################################################@")
    return ListaRigheRipetute



    # for page_number in range(doc.page_count):
    #      if page_number == 36:  # Skip the first two pages
    #         for line in doc[page_number].get_text().split("\n"):
    #             if len(line.strip()) > 5:
    #                 lines.append(line.strip())
    # print(lines)
    # exit(0)


def clean_text(text, repeated_lines):
    return "\n".join(
        line.strip()
        for line in text.split("\n")
        if line.strip() and line.strip() not in repeated_lines
    )

def is_numeric_with_dots(s):
    # Definisci il pattern che accetta solo numeri e punti
    pattern = r'^[0-9.]+$'
    return bool(re.match(pattern, s))

def detect_start_page(pdf_path):
    doc = fitz.open(pdf_path)
    repeated_lines = identify_repeated_headers_footers(doc, min_repeats=5)

    for page_num, page in enumerate(doc, start=1):
        blocks = page.get_text("blocks")
        blocks = sorted(blocks, key=lambda b: (round(b[1]), round(b[0])))

        lines = []
        for b in blocks:
            text = b[4].strip()
            if not text:
                continue
            if (
                text in repeated_lines and
                not re.match(r"^TITOLO [IVXLC]+$", text, re.IGNORECASE) and
                not re.match(r"^Capitolo \d+$", text, re.IGNORECASE) and
                not re.match(r"^SEZIONE [IVXLC]+( - .+)?$", text, re.IGNORECASE)
            ):
                continue
            lines.append(text)

        for i in range(len(lines) - 2):
            # Pattern 1: canonical BdI — TITOLO + Capitolo + UPPERCASE TITLE
            if (re.match(r"^TITOLO [IVXLC]+$", lines[i], re.IGNORECASE) and
                re.match(r"^Capitolo \d+$", lines[i+1], re.IGNORECASE) and
                re.match(r"^[A-ZÀÈÉÙÒÌ][A-ZÀÈÉÙÒÌ0-9 \-',\.]{4,}$", lines[i+2])):
                print(f"[INFO] Pagina {page_num}: {len(lines)} righe dopo blocchi")
                print(f"[FOUND] Sequenza di inizio (TITOLO+Capitolo) trovata a pagina {page_num}, righe {i+1}-{i+3}:")
                print(f"         ➤ {lines[i]}")
                print(f"         ➤ {lines[i+1]}")
                print(f"         ➤ {lines[i+2]}")
                return page_num

        # Pattern 2: AVC-style — pagina che inizia con DISPOSIZIONI PRELIMINARI o PARTE
        for i in range(len(lines)):
            if "..." in lines[i]:
                continue
            if (re.match(r"^DISPOSIZIONI\s+PRELIMINARI\s*$", lines[i], re.IGNORECASE) or
                re.match(r"^PARTE\s+(?:PRIMA|SECONDA|TERZA|QUARTA|QUINTA|SESTA|SETTIMA|OTTAVA|NONA|DECIMA|[IVXLC]+)\s*(?:[-–]\s*.+)?$", lines[i], re.IGNORECASE)):
                print(f"[INFO] Pagina {page_num}: {len(lines)} righe dopo blocchi")
                print(f"[FOUND] Sequenza di inizio (AVC-style) trovata a pagina {page_num}:")
                print(f"         ➤ {lines[i]}")
                return page_num

    print("[INFO] Nessuna sequenza di inizio trovata.")
    return None

def is_sezione(line):
    stripped = line.strip()
    if re.match(r"^SEZIONE\s+[IVXLC]+", stripped, re.IGNORECASE):
        result = re.match(r"^SEZIONE\s+[IVXLC]+", stripped, re.IGNORECASE)
    elif re.match(r"^ALLEGATO\s+[A-Z0-9]{1}", stripped, re.IGNORECASE):
        result = re.match(r"^ALLEGATO\s+[A-Z0-9]{1}", stripped, re.IGNORECASE)
    elif re.match(r"^PARTE\s+(?:PRIMA|SECONDA|TERZA|QUARTA|QUINTA|SESTA|SETTIMA|OTTAVA|NONA|DECIMA|[IVXLC]+)\s*$", stripped):
        result = re.match(r"^PARTE\s+(?:PRIMA|SECONDA|TERZA|QUARTA|QUINTA|SESTA|SETTIMA|OTTAVA|NONA|DECIMA|[IVXLC]+)\s*$", stripped)
    elif re.match(r"^DISPOSIZIONI\s+PRELIMINARI", stripped, re.IGNORECASE):
        result = re.match(r"^DISPOSIZIONI\s+PRELIMINARI", stripped, re.IGNORECASE)
    else:
        result = None
    return result

def is_paragrafo(line):
    return re.match(r"^\d+(\.\d+)*\.?\s+[^\n]{1,100}", line.strip()) is not None

def aggiungi_sezione_se_nuova(sezioni, current_sezione):
    identificativi_esistenti = {p["titolo"] for p in sezioni}
    
    if current_sezione["titolo"] not in identificativi_esistenti:
        sezioni.append(current_sezione)
    return sezioni

def aggiungi_paragrafo_se_nuovo(current_sezione, current_paragrafo):
    identificativi_esistenti = {p["identificativo"] for p in current_sezione.get("contenuto_parsato", [])}
    
    if current_paragrafo["identificativo"] not in identificativi_esistenti:
        #current_sezione.setdefault("contenuto_parsato", []).append(current_paragrafo)
        current_sezione["contenuto_parsato"].append(current_paragrafo)
    return current_sezione

def parser_pdf(pdf_path, start_page):
    """
    start_page: numero di pagina 1-based restituito da detect_start_page().
    Internamente convertiamo a 0-based per l'indicizzazione di fitz.
    """
    TOP_RATIO = 0.16
    BOTTOM_RATIO = 0.90
    doc = fitz.open(pdf_path)
    path = Path(pdf_path)
    file_name = path.name
    repeated_lines = identify_repeated_headers_footers(doc, min_repeats=5)
    sezioni = []
    debug_log = []  # Lista per contenere i log
    current_sezione = None
    current_paragrafo = None
    numero_pagina = 0
    Titolo_Paragrafo = ""
    TitoloDueRighe = False
    TitoloDueRighe_numeroriga = 0

    TitoloParte = ""
    TitoloTitolo = ""
    TitoloCapitolo = ""
    TitoloAllegato = ""
    TitoloSezione = ""
    titolo_sezione = ""

    start_index = max(start_page - 1, 0)

    for page in doc[start_index:]:
        TitoloCompleto = ""
        full = page.rect
        #clip = fitz.Rect(full.x0, full.y0, full.x1, full.y1 * TOP_RATIO)    # --> Versione Jacopo
        clip = fitz.Rect(full.x0, full.y0 + full.height * TOP_RATIO, full.x1, full.y1 * BOTTOM_RATIO)
        ###########################################################################################################
        ### NON considero le linee che sono nel footer ==> da verificare efficacia nei documenti utilizzati <== ###
        ###########################################################################################################
        _STRUCTURAL_RE = re.compile(
            r"^(?:PARTE|SEZIONE|DISPOSIZIONI|ALLEGATO)\b", re.IGNORECASE
        )
        top_clip = fitz.Rect(full.x0, full.y0, full.x1, full.y0 + full.height * TOP_RATIO)
        top_blocks = page.get_text("dict", clip=top_clip)["blocks"]
        top_structural = []
        for tb in top_blocks:
            if tb["type"] == 0:
                for tl in tb["lines"]:
                    for ts in tl["spans"]:
                        if _STRUCTURAL_RE.match(ts["text"].strip()):
                            top_structural.append(tb)
                            break
                    else:
                        continue
                    break

        blocks = top_structural + page.get_text("dict", clip=clip)["blocks"]
        blocks = sorted(blocks, key=lambda b: (round(b["bbox"][1]), round(b["bbox"][0])))
        numero_pagina += 1
        lines = []
        lines_span = []

        page_text = page.get_text()
        debug_log.append("   ### Pagina N " + str(numero_pagina + start_page))
        Trovato = _HEADER_RE_IntestazioneBdI.search(page_text)
        if Trovato:
            TitoloCompleto = Trovato.group("Header")
            TitoloParte = Trovato.group("Parte")
            TitoloTitolo = Trovato.group("Titolo")
            TitoloCapitolo = Trovato.group("Capitolo")
            TitoloAllegato = Trovato.group("Allegato")
            TitoloSezione = Trovato.group("Sezione")
            debug_log.append("   ###### " + str(TitoloCompleto))

#        for b in blocks:
#            if b["type"] == 0:  # Se il blocco è di testo
#            text = b[4].strip()
#            if not text:
#                continue
#            if text in repeated_lines and not is_sezione(text) and not is_paragrafo(text):
#                debug_log.append("                 Il testo è poco significativo e ripetuto --> " + str(text))
#                continue
            
        for b in blocks:
            if b["type"] == 0:  # Se il blocco è di testo
                for line in b["lines"]:
                    for span in line["spans"]:
                        text = span["text"].strip()
                        size_line = span["size"]
                        if not text:
                            continue
                        if text in repeated_lines and not is_sezione(text) and not is_paragrafo(text):
                            debug_log.append("                 Il testo è poco significativo e ripetuto --> " + str(text))
                            continue
                        if size_line <= 9:
                            debug_log.append("                 Il testo è una nota a piè di pagina --> " + str(text))
                            continue
                        lines_span.append(span)

        for span in lines_span:
            line = span["text"].strip()
            size_line = span["size"]
            bold_line = "bold" in span["font"].lower()
            debug_log.append("                 -------> Text=" + str(line) + "   Size=" + str(size_line) + "   Bold=" + str(bold_line))

            lines.append(line)

#        for line_idx in range(len(lines)):
#            line = lines[line_idx]
            line_idx = lines_span.index(span)


            # 0️⃣  SEZIONE — priorità massima (prima di paragrafo e bold) -------
            if is_sezione(line):
                debug_log.append("        E' una nuova sezione --> " + str(line))
                if current_sezione:
                    sezioni = aggiungi_sezione_se_nuova(sezioni, current_sezione)
                    debug_log.append("                     Viene inserita nell'elenco delle sezioni")

                titolo_sezione = line.strip()

                if TitoloCompleto:
                    titolo_sezione = TitoloCompleto.replace("\n", " ").strip()
                    debug_log.append("        HEADER RILEVATO a pagina" + str(numero_pagina + start_page) + "->" + str(titolo_sezione))
                else:
                    next_idx = line_idx + 1
                    if next_idx < len(lines) and _CAPS_TITLE_RE.search(lines[next_idx]):
                        titolo_sezione = lines[next_idx].strip()
                        debug_log.append("        HEADER NON RILEVATO a pagina" + str(numero_pagina + start_page) + "->" + str(titolo_sezione) + " ALTERNATIVO")
                        lines[next_idx] = ""

                current_sezione = {"codicedocumento": file_name, "titolo": titolo_sezione, "page": numero_pagina + start_page, "contenuto_parsato": []}
                current_paragrafo = None
                continue

            # 1️⃣  PARAGRAFO ---------------------------------------------------
            if is_paragrafo(line):
                # Identifica PARAGRAFI con il titolo su un'unica riga
                m = re.match(r"^(?P<id>\d+(?:\.\d+)*\.?)\s+(?P<title>.+)", line.strip())
                #                print("##### E' un paragrafo (pagina ", numero_pagina,") ==>", line.strip())
                if m:
                    current_paragrafo = {
                        "identificativo": str(m.group("id").strip()) + str(m.group("title").strip()),
                        "titolo_articolo": titolo_sezione,
                        "titoloParte_articolo":    TitoloParte,
                        "titoloTitolo_articolo":   TitoloTitolo,
                        "titoloCapitolo_articolo": TitoloCapitolo,
                        "titoloAllegato_articolo": TitoloAllegato,
                        "titoloSezione_articolo":  TitoloSezione,
                        "contenuto_parsato_2": [{"contenuto": ""}],      #    ⓐ inizializziamo SUBITO il contenitore con stringa vuota
                    }
                    debug_log.append("               Identifico Paragrafo su UNA RIGA" + str(m.group("id").strip()) + str(m.group("title").strip()))
                    if current_sezione:
                        current_sezione = aggiungi_paragrafo_se_nuovo(current_sezione, current_paragrafo)
                        #current_sezione["contenuto_parsato"].append(current_paragrafo)
                        debug_log.append("                          Inserisco nella Sezione il Contenuto Parsato")
                    
                    TitoloDueRighe_numeroriga = line_idx
                continue
            
            elif bold_line :
                # Per identificare PARAGRAFI con il titolo su due righe successive
                if TitoloDueRighe == False:
                    if current_paragrafo:
                        if (TitoloDueRighe_numeroriga + 1 == line_idx):
                            current_paragrafo["identificativo"] += str(line)
                    if is_numeric_with_dots(line):
                        Titolo_Paragrafo = line
                        TitoloDueRighe = True
                        TitoloDueRighe_numeroriga = line_idx
                else:
                    if (TitoloDueRighe_numeroriga + 1 == line_idx):
                        TitoloDueRighe = False
                        Titolo_Paragrafo = Titolo_Paragrafo + str(line)
                        current_paragrafo = {
                            "identificativo": str(Titolo_Paragrafo),
                            "titolo_articolo": titolo_sezione,
                            "titoloParte_articolo": TitoloParte,
                            "titoloTitolo_articolo": TitoloTitolo,
                            "titoloCapitolo_articolo": TitoloCapitolo,
                            "titoloAllegato_articolo": TitoloAllegato,
                            "titoloSezione_articolo": TitoloSezione,
                            "pagina": numero_pagina + start_page,
                            # ⓐ inizializziamo SUBITO il contenitore con stringa vuota
                            "contenuto_parsato_2": [{"contenuto": ""}],
                        }
                        debug_log.append("               Identifico Paragrafo su DUE RIGHE" + Titolo_Paragrafo)
                        if current_sezione:
                            current_sezione = aggiungi_paragrafo_se_nuovo(current_sezione, current_paragrafo)
                            # current_sezione["contenuto_parsato"].append(current_paragrafo)
                            debug_log.append("                          Inserisco nella Sezione il Contenuto Parsato")
                continue

            # 3️⃣  testo libero (accodato all’ultimo paragrafo) -------------------------
            if current_paragrafo:
                acc = current_paragrafo["contenuto_parsato_2"][0]["contenuto"]
                current_paragrafo["contenuto_parsato_2"][0]["contenuto"] = (
                    acc + (" " if acc else "") + line.strip()
                )
                debug_log.append("                                       Inserisco nel Contenuto Parsato della Sezione")
            else:
                if current_sezione:
                    debug_log.append("                          Inserisco il testo all'interno della SEZIONE, Paragrafo '0' -->")
                    current_paragrafo = {
                        "identificativo": "0",
                        "titolo_articolo": titolo_sezione,
                        "titoloParte_articolo": TitoloParte,
                        "titoloTitolo_articolo": TitoloTitolo,
                        "titoloCapitolo_articolo": TitoloCapitolo,
                        "titoloAllegato_articolo": TitoloAllegato,
                        "titoloSezione_articolo": TitoloSezione,
                        "pagina": numero_pagina + start_page,
                        # ⓐ inizializziamo SUBITO il contenitore con stringa vuota
                        "contenuto_parsato_2": [{"contenuto": ""}],
                    }
                    current_sezione = aggiungi_paragrafo_se_nuovo(current_sezione, current_paragrafo)
                    # current_sezione["contenuto_parsato"].append(current_paragrafo)
                else:
                    debug_log.append("                                  NON è presente NEMMENO la SEZIONE!")

    if current_sezione:
        sezioni = aggiungi_sezione_se_nuova(sezioni, current_sezione)
        # sezioni.append(current_sezione)

    # Salva log su file
    _SRCDIR = Path(__file__).resolve().parents[2]

    log_dir = Path(_SRCDIR / "out_parser")
    log_dir.mkdir(parents=True, exist_ok=True)

    log_path = os.path.join(log_dir, "debug_log_analisiBanca.txt")

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(debug_log))

    return sezioni

# if __name__ == "__main__":
#     pdf_path = "../data/documento.pdf"
#     start_page = detect_start_page(pdf_path)
#     print(f"Pagina di inizio rilevata: {start_page}")
#     sezioni = parser_pdf(pdf_path, start_page)

    # with open("output.json", "w", encoding="utf-8") as f:
    #     json.dump(sezioni, f, ensure_ascii=False, indent=2)

    # print(json.dumps(sezioni, ensure_ascii=False, indent=2))
