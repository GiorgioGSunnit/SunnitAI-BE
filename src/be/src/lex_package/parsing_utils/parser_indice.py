import json
import re
from collections import Counter

import fitz  # PyMuPDF


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
_INDEX_TITLE_RE = re.compile(r"^\s*indice\s*$", re.IGNORECASE)
_NOISE_LINE_RE = re.compile(
    r"(titolo\s+.+versione|stato\s+approvato|interno\s+[–-]\s+internal|questo documento è proprietà|indice\.\s*\d+)",
    re.IGNORECASE,
)

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

_PATTERN_TESTUALE_NO_FILLER = re.compile(
    r"^\s*"
    r"(?P<titolo>.{3,120}?)"
    r"\s+(?P<pagina>\d{1,4})"
    r"\s*$",
    re.UNICODE,
)

_NO_FILLER_KEYWORD_RE = re.compile(
    r"\b(titolo|capitolo|sezione|allegato|premessa|appendice|parte)\b",
    re.IGNORECASE,
)

MAX_INDEX_SCAN_PAGES = 12
MAX_NON_TOC_PAGES = 3


def _find_index_start(doc) -> int | None:
    for page_num in range(1, min(MAX_INDEX_SCAN_PAGES, len(doc)) + 1):
        text = doc[page_num - 1].get_text()
        for raw_line in text.splitlines():
            if _INDEX_TITLE_RE.match(raw_line.strip()):
                return page_num
    return None


def _parse_toc_candidate(line: str) -> dict | None:
    clean = line.strip()
    if not clean or _NOISE_LINE_RE.search(clean):
        return None

    match = _PATTERN_NUMERICO.search(clean)
    if match:
        identificativo = match.group("identificativo").strip()
        titolo = match.group("titolo").strip().rstrip(".")
        pagina = int(match.group("pagina"))
        if 0 < pagina < 1500 and len(titolo) > 2:
            return {
                "identificativo": identificativo,
                "titolo": titolo,
                "pagina_destinazione": pagina,
            }

    match = _PATTERN_TESTUALE.match(clean)
    used_no_filler = False
    if not match:
        match = _PATTERN_TESTUALE_NO_FILLER.match(clean)
        used_no_filler = match is not None
    if not match:
        return None

    titolo = match.group("titolo").strip().rstrip(".")
    pagina = int(match.group("pagina"))
    if used_no_filler and not _NO_FILLER_KEYWORD_RE.search(titolo):
        return None
    if 0 < pagina < 1500 and len(titolo) > 2:
        return {
            "identificativo": "",
            "titolo": titolo,
            "pagina_destinazione": pagina,
        }
    return None


def parser_indice(pdf_path="../data/documento.pdf") -> list[dict]:
    doc = fitz.open(pdf_path)
    repeated_lines = identify_repeated_headers_footers(doc, 3)

    debug_log = []
    indice = []
    index_start_page = _find_index_start(doc)
    if index_start_page is None:
        return indice

    pending: dict | None = None
    pages_without_toc = 0
    max_pages = min(len(doc), index_start_page + MAX_INDEX_SCAN_PAGES)

    for page_num, page in enumerate(doc, start=1):
        if page_num < index_start_page or page_num > max_pages:
            continue
        text = page.get_text()
        cleaned_text = clean_text(text, repeated_lines)
        page_entries_start = len(indice)
        page_has_toc = False

        for line in merge_broken_lines(cleaned_text):
            line_stripped = line.strip()
            if not line_stripped or _INDEX_TITLE_RE.match(line_stripped):
                continue

            parsed = _parse_toc_candidate(line_stripped)
            if parsed is None:
                if pending is not None and len(line_stripped) > 2 and not _NOISE_LINE_RE.search(line_stripped):
                    pending["parts"].append(line_stripped)
                continue

            page_has_toc = True
            if pending is not None:
                parsed["titolo"] = " ".join(pending["parts"] + [parsed["titolo"]]).strip()
                if pending["identificativo"]:
                    parsed["identificativo"] = pending["identificativo"]
                pending = None

            debug_log.append(
                f"Match indice: id={parsed['identificativo']} titolo={parsed['titolo']} pag={parsed['pagina_destinazione']}"
            )
            indice.append(
                {
                    "pagina_indice": page_num,
                    "pagina_destinazione": parsed["pagina_destinazione"],
                    "identificativo": parsed["identificativo"],
                    "titolo": parsed["titolo"],
                }
            )

            id_only_match = re.match(r"^(?P<ident>\d+(?:\.\d+)*\.?)\s+(?P<title>.+)$", line_stripped)
            if id_only_match and not re.search(r"\s[.\u2026]{2,}\s*\d{1,4}\s*$", line_stripped):
                pending = {
                    "identificativo": id_only_match.group("ident"),
                    "parts": [id_only_match.group("title").strip()],
                }

        page_added = len(indice) - page_entries_start
        if page_has_toc and page_added > 0:
            pages_without_toc = 0
        elif indice:
            pages_without_toc += 1
            if pages_without_toc >= MAX_NON_TOC_PAGES:
                break

    cleaned_indice = []
    seen = set()
    for item in indice:
        key = (item["identificativo"].strip(), item["titolo"].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        cleaned_indice.append(item)
    indice = cleaned_indice

    # Debug log — written locally (Azure blob upload removed)
    try:
        import logging as _logging
        _logging.getLogger("lex_package.parser_indice").debug(
            "parse_indice debug log:\n%s", "\n".join(debug_log)
        )
    except Exception:
        pass

    return indice

if __name__ == "__main__":
    indice = parser_indice("../data/documento.pdf")
    with open("./out_parser/indice.json", "w", encoding="utf-8") as f:
        json.dump(indice, f, ensure_ascii=False, indent=2)
    print(json.dumps(indice, ensure_ascii=False, indent=2))
