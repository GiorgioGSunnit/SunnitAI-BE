import re
import fitz  # PyMuPDF
from collections import Counter
# from .parser_regolamento import identify_repeated_headers_footers
import json
from pprint import pprint

def identify_repeated_headers_footers_OLD(doc, min_repeats=3):
    lines = []
    for page in doc:
        text = page.get_text()
        page_lines = text.split("\n")
        for line in page_lines:
            if len(line.strip()) > 5:
                lines.append(line.strip())
    line_counts = Counter(lines)
    repeated = [line for line, count in line_counts.items() if count >= min_repeats]
    # print(f"DEBUG: Righe ripetute trovate: {len(repeated)}")
    return repeated

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
                    for linea_2 in page_line:
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

    #####visualizzo le più ripetute
    sorted_lines = sorted(repeated, key=lambda x: x[1], reverse=True)
    for rank, (line, count) in enumerate(sorted_lines[:10], start=1):
        print(f"(BdI) RIPETIZIONI: {rank}. '{line}' - {count} ripetizioni")

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
    filler = re.compile(r"[.\u2026\u00B7\u2022•·∙ ]{2,}\s*\d{1,4}$")
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


def parser_indice(pdf_path="../data/documento.pdf") -> list[dict]:
    doc = fitz.open(pdf_path)  
    repeated_lines = identify_repeated_headers_footers(doc, 3)

    indice = []
    in_indice = False

    # r"^\s*(?P<identificativo>\d{1,4}(?:\.\d{1,4})*)\.?\s*(?P<titolo>.*?)[\s.\u2026\u00B7\u2022•·∙]{2,}\s*(?P<pagina>\d{1,4})\b"

    pattern_indice = re.compile(
        r"""
        \s*
        (?P<identificativo>(?:\d{1,4}(?:\.\d{1,4})*|\d{1,4}\.?))
        \s
        (?P<titolo>(?:[a-zA-Z0-9_ à\(\)\"“”]|(?:[a-z]\.))*)
        \s?
        [.\u2026\u00B7\u2022•·∙]{2,}
        \s*
        (?P<pagina>\d{1,4})
        \b
        """,
        re.X | re.M | re.UNICODE,
    )
    # pattern_indice = re.compile(r"^\s*(?P<identificativo>\d+(\.\d+)*)(\s+|\.)+(.+?)\.{2,}\s+(\d+)$", re.IGNORECASE)
    # pattern_indice = re.compile(
    #     r"^\s*(?P<identificativo>\d+(?:\.\d+)*)\s+(?P<titolo>.*?)\.{3,}\s*(?P<pagina>\d+)$",
    #     re.VERBOSE,
    # )

    for page_num, page in enumerate(doc, start=1):
        if page_num > 2:
            break
        text = page.get_text()
        cleaned_text = clean_text(text, repeated_lines)

        for line in merge_broken_lines(cleaned_text):  # ② linee già ricomposte
            line_stripped = line.strip()

            # for line in cleaned_text.split("\n"):
            #     line_stripped = line.strip()

            # Avvio del parsing dell'indice quando trovi "Indice"
            if not in_indice and re.search(r"\bindice\b", line_stripped, re.IGNORECASE):
                in_indice = True
                print("INDICE TROVATO")
                continue

            if in_indice:
                # print(f"DEBUG righe indice pagina {page_num}:")
                # for l in cleaned_text.split("\n"):
                #     print(f">>> {l}")
                match = pattern_indice.search(line_stripped)
                if match:
                    print(f" 🍆 Match trovato: {match.groups()}")
                    identificativo = match.group("identificativo")
                    titolo = match.group("titolo").strip()
                    titolo = titolo.rstrip(".")
                    pagina = int(match.group("pagina"))

                    if 0 < pagina < 1500:
                        indice.append({
                            "pagina_indice": page_num,
                            "pagina_destinazione": pagina,
                            "identificativo": identificativo,
                            "titolo": titolo
                        })
                    else:
                        print("NO MATCH:", line_stripped)

    return indice

if __name__ == "__main__":
    indice = parser_indice("../data/documento.pdf")
    with open("./out_parser/indice.json", "w", encoding="utf-8") as f:
        json.dump(indice, f, ensure_ascii=False, indent=2)
    print(json.dumps(indice, ensure_ascii=False, indent=2))
