import re
import fitz  # PyMuPDF
from collections import Counter
import json


def identify_repeated_headers_footers(doc, min_repeats=3):
    lines = []
    page_number = 0
    line_number = 0
    pattern_footer = r"(\d+)?\s*(.*)\s*(\d+)?$"

    # Fase 1: raccogli intestazioni/piedipagina
    for page in doc:
        page_number += 1
        line_number = 0
        text = page.get_text()
        page_lines = text.split("\n")
        if not page_lines:
            continue

        for linea in page_lines:
            line_number += 1
            if len(linea) > 5:
                lines.append(linea)
                PrimaRiga   = re.match(pattern_footer, linea).group(2)
                if PrimaRiga:
                    for linea_2 in page_lines:
                        SecondaRiga = re.match(pattern_footer, linea_2).group(2)
                        if SecondaRiga:
                            if (PrimaRiga == SecondaRiga):
                                lines.append(linea)
                                lines.append(linea_2)

    # Conta le righe ripetute
    line_counts = Counter(lines).items()
    repeated = [
        line for line, count in line_counts if count >= min_repeats and line.strip()
    ]

    return repeated

def normalize_line(line):
    return re.sub(r"\bpage\s*\d+\b", "", line, flags=re.IGNORECASE).strip().lower()

def clean_text(text, repeated_lines):
    cleaned_lines = []
    for line in text.split("\n"):
        norm_line = normalize_line(line)
        if norm_line not in repeated_lines:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def merge_broken_lines(raw):
    out, i, rows = [], 0, [r.strip() for r in raw.splitlines() if r.strip()]
    id_only = re.compile(r"^\d+(?:\.\d+)*\.?$")
    filler = re.compile(r"[.\u2026\u00B7\u2022\u2219\u00B7\u2219 ]{2,}\s*\d{1,4}$")
    while i < len(rows):
        if (
            id_only.fullmatch(rows[i])
            and i + 1 < len(rows)
            and filler.search(rows[i + 1])
        ):
            out.append(f"{rows[i]} {rows[i+1]}")
            i += 2
        else:
            out.append(rows[i])
            i += 1
    return out


# Filler: sequenza di punti/spazi/bullet usata negli indici
_FILLER = r"[.\u2026\u00B7\u2022\u2219\u00B7 ]{4,}"

# Pattern 1: identificativo numerico + titolo + filler + pagina
# Matcha: "1.2 Titolo capitolo ........... 5"
_PATTERN_NUMERICO = re.compile(
    r"\s*"
    r"(?P<identificativo>(?:\d{1,4}(?:\.\d{1,4})*|\d{1,4}\.?))"
    r"\s"
    r"(?P<titolo>(?:[a-zA-Z0-9_ \u00e0\(\)\u201c\u201d\"]|(?:[a-z]\.))*)"
    r"\s?"
    + _FILLER +
    r"\s*(?P<pagina>\d{1,4})\b",
    re.M | re.UNICODE,
)

# Pattern 2: titolo testuale + filler + pagina (senza id numerico iniziale)
# Matcha: "DISPOSIZIONI PRELIMINARI ........... 1"
#         "Sezione I. Il principio dell'approccio ........... 7"
_PATTERN_TESTUALE = re.compile(
    r"^\s*"
    r"(?P<titolo>.{3,120}?)"       # titolo: 3-120 chars, non-greedy
    r"\s*"
    + _FILLER +
    r"\s*(?P<pagina>\d{1,4})"
    r"\s*$",
    re.UNICODE,
)

MAX_INDICE_PAGES = 5


def parser_indice(pdf_path="../data/documento.pdf") -> list[dict]:
    doc = fitz.open(pdf_path)
    repeated_lines = identify_repeated_headers_footers(doc, 3)

    debug_log = []
    indice = []
    in_indice = False

    max_pages = min(MAX_INDICE_PAGES, len(doc))

    for page_num, page in enumerate(doc, start=1):
        if page_num > max_pages:
            break
        text = page.get_text()
        cleaned_text = clean_text(text, repeated_lines)

        for line in merge_broken_lines(cleaned_text):
            line_stripped = line.strip()

            # Avvio del parsing dell'indice quando trovi "Indice"
            if not in_indice and re.search(r"\bindice\b", line_stripped, re.IGNORECASE):
                in_indice = True
                continue

            if in_indice:
                # Prova pattern 1 (numerico)
                match = _PATTERN_NUMERICO.search(line_stripped)
                if match:
                    identificativo = match.group("identificativo")
                    titolo = match.group("titolo").strip().rstrip(".")
                    pagina = int(match.group("pagina"))
                    debug_log.append(f"Match numerico: id={identificativo} titolo={titolo} pag={pagina}")
                    if 0 < pagina < 1500:
                        indice.append({
                            "pagina_indice": page_num,
                            "pagina_destinazione": pagina,
                            "identificativo": identificativo,
                            "titolo": titolo,
                        })
                    continue

                # Prova pattern 2 (testuale, senza id numerico)
                match = _PATTERN_TESTUALE.match(line_stripped)
                if match:
                    titolo = match.group("titolo").strip().rstrip(".")
                    pagina = int(match.group("pagina"))
                    debug_log.append(f"Match testuale: titolo={titolo} pag={pagina}")
                    if 0 < pagina < 1500 and len(titolo) > 2:
                        indice.append({
                            "pagina_indice": page_num,
                            "pagina_destinazione": pagina,
                            "identificativo": "",
                            "titolo": titolo,
                        })

    from utils.blob_storage_client import upload_debug_log
    upload_debug_log("debug_log_parseIndice.txt", "\n".join(debug_log))

    return indice

if __name__ == "__main__":
    indice = parser_indice("../data/documento.pdf")
    with open("./out_parser/indice.json", "w", encoding="utf-8") as f:
        json.dump(indice, f, ensure_ascii=False, indent=2)
    print(json.dumps(indice, ensure_ascii=False, indent=2))
