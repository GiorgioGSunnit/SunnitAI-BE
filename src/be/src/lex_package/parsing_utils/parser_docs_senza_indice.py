import re
import fitz  # PyMuPDF
import os
from .parser_indice import identify_repeated_headers_footers, normalize_line


# from pathlib import Path
# import sys

# _SRCDIR = Path(__file__).resolve().parents[1]
# sys.path.insert(0, str(_SRCDIR))

# doc = fitz.open(_SRCDIR / "data" / "F.inal Report on GL on loan origination and monitoring_COR_IT.pdf")
# page = doc[0]
# d = page.get_text("dict")  # dizionario

# for block in d["blocks"]:
#     for line in block["lines"]:
#         for span in line["spans"]:
#             text = span["text"]
#             font = span["font"]
#             size = span["size"]
#             print(f"'{text}' → font={font}, size={size}pt")


def parser_contenuto(pdf_path: str) -> list[dict]:
    doc = fitz.open(pdf_path)
    repeated_lines = identify_repeated_headers_footers(doc)

    capitoli = []
    current_chapter = None
    current_subtitle = None
    current_item = None
    chapter_detected = False

    # chapter_pattern = re.compile(r"^\d+\.\s+[A-Z].+")
    # subtitle_pattern = re.compile(r"^[A-Z][a-z]{5-50}$")
    # numbered_paragraph_pattern = re.compile(r"\n?(\d+)\.\s+(.*?)(?=\n\d+\.|\Z)", re.DOTALL)

    chapter_pattern = re.compile(r"^\s*(\d+)\.\s+([A-Z][^\n]{5,60})$", re.MULTILINE)
    subtitle_pattern = re.compile(r"^(?!\d+\.\s)[A-Z][\w\s,\-()\/']{5,60}$", re.UNICODE)
    numbered_paragraph_pattern = re.compile(
        r"\n?(\d+)\.\s+(.{60,})(?=\n\d+\.|\Z)", re.DOTALL
    )

    debug_log = []  # Lista per contenere i log

    for page_num, page in enumerate(doc):
        blocks = [
            b for b in page.get_text("blocks") if b[1] > 50
        ]  # Ignora intestazioni
        blocks = sorted(
            blocks, key=lambda b: (b[1], b[0])
        )  # Ordinamento top-down, poi sinistra-destra

        i = 0
        while i < len(blocks):
            x, y, _, _, text, *_ = blocks[i]
            text = text.strip()
            debug_log.append(f"[Page {page_num + 1}] Block {i}: '{text}'")

            if (
                not text
                or normalize_line(text) in repeated_lines
                or re.fullmatch(r"\d+", text)
            ):
                debug_log.append("  → Ignorato (vuoto, ripetuto o numero pagina)")
                i += 1
                continue

            if chapter_pattern.match(text) and not chapter_detected:
                if current_chapter:
                    capitoli.append(current_chapter)
                current_chapter = {
                    "codicedocumento": "",
                    "page": page_num,
                    "identificativo": "",
                    "titolo": text,
                    "codicearticolo": "",
                    "contenuto": "",
                    "contenuto_parsato": [],
                }
                debug_log.append(f"  → Capitolo identificato: {text}")
                current_subtitle = None
                current_item = None
                chapter_detected = True
                i += 1
                continue

            if numbered_paragraph_pattern.match(text):
                match = numbered_paragraph_pattern.match(text)
                number = match.group(1) + "."
                content = match.group(2)
                debug_log.append(f"  → Paragrafo {number} iniziale: {content}")
                i += 1
                chapter_detected = False

                while i < len(blocks):
                    _, _, _, _, next_text, *_ = blocks[i]
                    next_text = next_text.strip()
                    if (
                        chapter_pattern.match(next_text)
                        or subtitle_pattern.match(next_text)
                        or numbered_paragraph_pattern.match(next_text)
                    ):
                        break
                    content += " " + next_text
                    debug_log.append(f"    → Esteso con: {next_text}")
                    i += 1

                current_item = {
                    "identificativo": number,
                    "contenuto": content.strip(),
                    "flag": False,
                }
                if current_subtitle:
                    current_subtitle["contenuto_parsato_2"].append(current_item)
                elif current_chapter:
                    if not current_chapter["contenuto_parsato"]:
                        current_subtitle = {
                            "identificativo": "",
                            "contenuto": "",
                            "flag": False,
                            "titolo_articolo": "",
                            "contenuto_parsato_2": [],
                        }
                        current_chapter["contenuto_parsato"].append(current_subtitle)
                    else:
                        current_subtitle = current_chapter["contenuto_parsato"][-1]
                    current_subtitle["contenuto_parsato_2"].append(current_item)

            elif subtitle_pattern.match(text):
                current_subtitle = {
                    "identificativo": "",
                    "contenuto": "",
                    "flag": False,
                    "titolo_articolo": text,
                    "contenuto_parsato_2": [],
                }
                if current_chapter:
                    current_chapter["contenuto_parsato"].append(current_subtitle)
                debug_log.append(f"  → Sottotitolo identificato: {text}")
                current_item = None
                i += 1

            else:
                if current_item:
                    current_item["contenuto"] += " " + text
                    debug_log.append(f"    → Aggiunto a paragrafo attivo: {text}")
                i += 1

    if current_chapter:
        capitoli.append(current_chapter)

    from utils.blob_storage_client import upload_debug_log
    upload_debug_log("debug_log.txt", "\n".join(debug_log))

    return capitoli


if __name__ == "__main__":
    import json

    pdf_path = "../data/documento.pdf"
    contenuto = parser_contenuto(pdf_path)
    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    output_path = f"./out_parser/{pdf_name}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(contenuto, f, ensure_ascii=False, indent=2)
    print(json.dumps(contenuto, ensure_ascii=False, indent=2))
