"""
Post-process degli XLSX di confronto: righe senza codice documento, colonne da Tipo,
ordine Contenuto tra Comma e Rif-Articolo, export vista con nome leggibile (EXT/INT → 7 caratteri).
Solo stdlib (OOXML via zipfile + ElementTree).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"

GREEN_COLS = {"Pagina", "Articolo", "Titolo", "Comma", "Contenuto", "Titolo Articolo", "Sottocomma"}
BLUE_COLS = {"Rif-Articolo", "Rif-Titolo", "Rif-Comma", "Rif-Contenuto"}
ORANGE_COLS = {"Coefficiente", "Dettaglio", "Descrizione", "Ulteriori"}

ARGB = {
    "green": "FFD8E4BC",
    "blue": "FFB7DEE8",
    "orange": "FFFCD5B4",
}

OUTPUT_SUFFIX = "_confronto_vista.xlsx"
HASH_VS_PATTERN = re.compile(
    r"^([0-9a-f]{32})_vs_([0-9a-f]{32})(?:_.*)?\.xlsx$", re.IGNORECASE
)
_ILLEGAL = re.compile(r'[<>:"/\\|?*\s]+')


def _q(tag: str) -> str:
    return f"{{{NS_MAIN}}}{tag}"


def _col_to_idx(col_letters: str) -> int:
    n = 0
    for c in col_letters.upper():
        if not ("A" <= c <= "Z"):
            break
        n = n * 26 + (ord(c) - ord("A") + 1)
    return n - 1


def _parse_cell_ref(ref: str) -> tuple[int, int] | None:
    m = re.match(r"^([A-Za-z]+)(\d+)$", ref.strip())
    if not m:
        return None
    return _col_to_idx(m.group(1)), int(m.group(2)) - 1


def _idx_to_col(idx: int) -> str:
    s = ""
    i = idx + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(ord("A") + r) + s
    return s


def _read_shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root.findall(_q("si")):
        t_el = si.find(_q("t"))
        if t_el is not None:
            out.append(t_el.text or "")
            continue
        parts: list[str] = []
        for t in si.iter():
            if t.tag == _q("t"):
                parts.append(t.text or "")
        out.append("".join(parts))
    return out


def _cell_value(c: ET.Element, shared: list[str]) -> str | int | float | None:
    t = c.get("t")
    v_el = c.find(_q("v"))
    is_el = c.find(_q("is"))
    if t == "s" and v_el is not None and v_el.text is not None:
        try:
            return shared[int(v_el.text)]
        except (ValueError, IndexError):
            return None
    if t == "inlineStr" and is_el is not None:
        ts = is_el.findall(".//" + _q("t"))
        return "".join((x.text or "") for x in ts)
    if t == "b" and v_el is not None:
        return v_el.text == "1"
    if v_el is not None and v_el.text is not None:
        txt = v_el.text
        if t in (None, "n"):
            try:
                if "." in txt or "e" in txt.lower():
                    return float(txt)
                return int(txt)
            except ValueError:
                return txt
        return txt
    return None


def _first_sheet_path(z: zipfile.ZipFile) -> str:
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheets = wb.find(_q("sheets"))
    if sheets is None:
        raise ValueError("workbook senza sheets")
    first = sheets.find(_q("sheet"))
    if first is None:
        raise ValueError("nessun foglio")
    rid = first.get(f"{{{NS_REL}}}id")
    if not rid:
        raise ValueError("sheet senza r:id")
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    for rel in rels:
        if rel.get("Id") == rid:
            target = rel.get("Target") or ""
            if target.startswith("/"):
                return target.lstrip("/")
            return "xl/" + target.replace("\\", "/")
    raise ValueError("relationship foglio non trovata")


def read_first_sheet_table(path: Path) -> tuple[list[str], list[list[object]]]:
    with zipfile.ZipFile(path, "r") as z:
        shared = _read_shared_strings(z)
        sheet_path = _first_sheet_path(z)
        root = ET.fromstring(z.read(sheet_path))
        sheet_data = root.find(_q("sheetData"))
        if sheet_data is None:
            return [], []

        cells: dict[tuple[int, int], object] = {}
        max_r, max_c = 0, 0
        for row in sheet_data.findall(_q("row")):
            for c in row.findall(_q("c")):
                ref = c.get("r")
                if not ref:
                    continue
                parsed = _parse_cell_ref(ref)
                if not parsed:
                    continue
                col_i, row_i = parsed
                val = _cell_value(c, shared)
                cells[(row_i, col_i)] = val
                max_r = max(max_r, row_i)
                max_c = max(max_c, col_i)

        if max_c < 0:
            return [], []

        def row_vals(r: int) -> list[object]:
            return [cells.get((r, j), "") for j in range(max_c + 1)]

        headers = [str(row_vals(0)[j] or "").strip() for j in range(max_c + 1)]
        data: list[list[object]] = []
        for r in range(1, max_r + 1):
            data.append(row_vals(r))
        return headers, data


def _normalize_col(name: str) -> str:
    return str(name).strip()


def _sanitize_stem(stem: str) -> str:
    s = _ILLEGAL.sub("_", stem)
    return s.strip("_") or "file"


def _display_from_pdf_stem(stem: str) -> str:
    s = _sanitize_stem(stem)
    upper = stem.upper()
    if upper.startswith("EXT") or upper.startswith("INT"):
        return s[:7] if len(s) > 7 else s
    return s


def display_label_from_pdf_filename(pdf_path_or_name: str) -> str:
    """Stem del PDF: EXT*/INT* → primi 7 caratteri del nome sanificato, altrimenti nome completo."""
    p = Path(pdf_path_or_name.replace("\\", "/"))
    stem = p.stem
    if not stem:
        stem = p.name.rsplit(".", 1)[0] if "." in p.name else p.name
    return _display_from_pdf_stem(stem)


def _md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_hash_to_display(pdf_root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not pdf_root.is_dir():
        return mapping
    for pdf in pdf_root.rglob("*.pdf"):
        if not pdf.is_file():
            continue
        try:
            digest = _md5_file(pdf)
        except OSError:
            continue
        disp = _display_from_pdf_stem(pdf.stem)
        if digest not in mapping:
            mapping[digest] = disp
    return mapping


def _find_doc_code_column(columns: list[str]) -> int | None:
    for i, c in enumerate(columns):
        n = _normalize_col(c).lower().replace(" ", "")
        if n in ("codicedocumento", "codice_documento"):
            return i
    return None


def _find_tipo_index(columns: list[str]) -> int | None:
    for i, c in enumerate(columns):
        if _normalize_col(c) == "Tipo":
            return i
    return None


def _reorder_contenuto(columns: list[str]) -> list[str]:
    cols = [_normalize_col(c) for c in columns]
    if "Contenuto" not in cols:
        return list(columns)
    if "Comma" not in cols or "Rif-Articolo" not in cols:
        if "Comma" in cols:
            cols_no = [c for c in cols if c != "Contenuto"]
            i_comma = cols_no.index("Comma")
            return cols_no[: i_comma + 1] + ["Contenuto"] + cols_no[i_comma + 1 :]
        return cols
    cols_no_cont = [c for c in cols if c != "Contenuto"]
    i_comma = cols_no_cont.index("Comma")
    i_rif = cols_no_cont.index("Rif-Articolo")
    before = cols_no_cont[: i_comma + 1]
    between = cols_no_cont[i_comma + 1 : i_rif]
    after_rif = cols_no_cont[i_rif:]
    return before + ["Contenuto"] + between + after_rif


def _output_name_from_input(input_path: Path, hash_map: dict[str, str]) -> str:
    m = HASH_VS_PATTERN.match(input_path.name)
    if m:
        h1, h2 = m.group(1).lower(), m.group(2).lower()
        p1 = hash_map.get(h1, h1[:7])
        p2 = hash_map.get(h2, h2[:7])
        return f"{p1}_vs_{p2}{OUTPUT_SUFFIX}"

    stem = input_path.stem
    if "_vs_" in stem:
        left, right = stem.split("_vs_", 1)

        def map_side(side: str) -> str:
            side = side.strip()
            if re.fullmatch(r"[0-9a-f]{32}", side, re.I):
                return hash_map.get(side.lower(), side[:7])
            return _sanitize_stem(side)

        return f"{map_side(left)}_vs_{map_side(right)}{OUTPUT_SUFFIX}"

    return f"{_sanitize_stem(stem)}{OUTPUT_SUFFIX}"


def _cell_empty(val: object) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    s = str(val).strip()
    return s == "" or s.lower() in ("nan", "none")


def transform_table(
    headers: list[str], rows: list[list[object]]
) -> tuple[list[str], list[list[object]]]:
    headers = [_normalize_col(h) for h in headers]
    doc_i = _find_doc_code_column(headers)
    if doc_i is not None:
        rows = [r for r in rows if _cell_empty(r[doc_i] if doc_i < len(r) else None)]

    tipo_i = _find_tipo_index(headers)
    if tipo_i is None:
        raise ValueError('Colonna "Tipo" non trovata')
    H = len(headers)
    headers = headers[tipo_i:]
    w = len(headers)
    new_rows: list[list[object]] = []
    for r in rows:
        rr = list(r)
        if len(rr) < H:
            rr.extend([""] * (H - len(rr)))
        rr = rr[:H]
        seg = rr[tipo_i : tipo_i + w]
        if len(seg) < w:
            seg = seg + [""] * (w - len(seg))
        new_rows.append(seg[:w])
    rows = new_rows

    order = _reorder_contenuto(headers)
    idx_map = {h: i for i, h in enumerate(headers)}
    col_order = [idx_map[c] for c in order if c in idx_map]
    new_headers = [headers[i] for i in col_order]
    new_rows = [[r[i] for i in col_order] for r in rows]
    return new_headers, new_rows


def _header_style_idx(name: str) -> int:
    n = _normalize_col(name)
    if n in GREEN_COLS:
        return 1
    if n in BLUE_COLS:
        return 2
    if n in ORANGE_COLS:
        return 3
    return 0


def write_xlsx_table(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    strings: list[str] = []
    str_index: dict[str, int] = {}

    def si(s: str) -> int:
        if s not in str_index:
            str_index[s] = len(strings)
            strings.append(s)
        return str_index[s]

    def cell_xml(r: int, c: int, val: object, style: int | None = None) -> str:
        ref = f"{_idx_to_col(c)}{r + 1}"
        st = f' s="{style}"' if style else ""
        if val is None or val == "":
            return f'<c r="{ref}"{st}/>'
        if isinstance(val, bool):
            return f'<c r="{ref}" t="b"{st}><v>{"1" if val else "0"}</v></c>'
        if isinstance(val, int) and not isinstance(val, bool):
            return f'<c r="{ref}"{st}><v>{val}</v></c>'
        if isinstance(val, float):
            if math.isnan(val):
                return f'<c r="{ref}"{st}/>'
            return f'<c r="{ref}"{st}><v>{val}</v></c>'
        s = str(val)
        idx = si(s)
        return f'<c r="{ref}" t="s"{st}><v>{idx}</v></c>'

    nrows = 1 + len(rows)
    ncols = len(headers)
    dim = f"A1:{_idx_to_col(ncols - 1)}{nrows}"

    row_xml_parts: list[str] = []
    row_xml_parts.append('<row r="1">')
    for c, h in enumerate(headers):
        st = _header_style_idx(h)
        row_xml_parts.append(cell_xml(0, c, h, st if st else None))
    row_xml_parts.append("</row>")
    for ri, r in enumerate(rows, start=2):
        row_xml_parts.append(f'<row r="{ri}">')
        for c in range(ncols):
            v = r[c] if c < len(r) else ""
            row_xml_parts.append(cell_xml(ri - 1, c, v, None))
        row_xml_parts.append("</row>")

    sheet_body = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{NS_MAIN}" '
        f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="{dim}"/>'
        f'<sheetData>{"".join(row_xml_parts)}</sheetData>'
        f"</worksheet>"
    )

    sst_parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>']
    sst_parts.append(
        f'<sst xmlns="{NS_MAIN}" count="{len(strings)}" uniqueCount="{len(strings)}">'
    )
    for s in strings:
        sst_parts.append(f"<si><t>{xml_escape(s)}</t></si>")
    sst_parts.append("</sst>")
    sst_xml = "".join(sst_parts)

    styles_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{NS_MAIN}">
  <fills count="5">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="{ARGB["green"]}"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="{ARGB["blue"]}"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="{ARGB["orange"]}"/></patternFill></fill>
  </fills>
  <fonts count="1"><font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font></fonts>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="4">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="0" fillId="2" borderId="0" xfId="0" applyFill="1"/>
    <xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>
    <xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0" applyFill="1"/>
  </cellXfs>
</styleSheet>"""

    workbook_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="{NS_MAIN}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

    workbook_rels = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{NS_PKG}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"""

    root_rels = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{NS_PKG}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

    ct_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="{NS_CT}">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct_xml)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet_body)
        z.writestr("xl/sharedStrings.xml", sst_xml)
        z.writestr("xl/styles.xml", styles_xml)

    path.write_bytes(buf.getvalue())


def process_one(
    xlsx_path: Path, hash_map: dict[str, str], out_dir: Path
) -> Path | None:
    headers, rows = read_first_sheet_table(xlsx_path)
    if not headers:
        raise ValueError("foglio vuoto o non leggibile")
    new_h, new_r = transform_table(headers, rows)
    out_name = _output_name_from_input(xlsx_path, hash_map)
    out_path = out_dir / out_name
    write_xlsx_table(out_path, new_h, new_r)
    return out_path


def write_confronto_vista_copy(
    source_xlsx: Path,
    pdf_name_1: str,
    pdf_name_2: str,
) -> Path:
    """
    Legge l'XLSX di confronto appena scritto e produce accanto
    ``{label1}_vs_{label2}_confronto_vista.xlsx`` (ordine = primo vs secondo PDF della richiesta).
    """
    source_xlsx = Path(source_xlsx)
    headers, rows = read_first_sheet_table(source_xlsx)
    if not headers:
        raise ValueError("foglio vuoto o non leggibile")
    new_h, new_r = transform_table(headers, rows)
    l1 = display_label_from_pdf_filename(pdf_name_1)
    l2 = display_label_from_pdf_filename(pdf_name_2)
    out_name = f"{l1}_vs_{l2}{OUTPUT_SUFFIX}"
    out_path = source_xlsx.parent / out_name
    write_xlsx_table(out_path, new_h, new_r)
    return out_path


def _default_repo_root() -> Path:
    for anc in Path(__file__).resolve().parents:
        if (anc / "src" / "be" / "src" / "lex_package").is_dir():
            return anc
    return Path(r"C:\Users\quantis\aiac-be")


def run_cli_main() -> None:
    default_root = _default_repo_root()
    parser = argparse.ArgumentParser(
        description="Post-process XLSX confronti (solo stdlib, nessun pip)."
    )
    parser.add_argument(
        "--confronti-dir",
        type=Path,
        default=default_root / "Documenti Confrontati",
    )
    parser.add_argument(
        "--pdf-root",
        type=Path,
        default=default_root / "Documenti Da Analizzare",
    )
    args = parser.parse_args()
    confronti: Path = args.confronti_dir
    if not confronti.is_dir():
        raise SystemExit(f"Cartella non trovata: {confronti}")

    hash_map = _build_hash_to_display(args.pdf_root)
    print(f"Mappa hash->etichetta da PDF: {len(hash_map)} voci")

    done = 0
    skipped = 0
    errors: list[str] = []

    for p in sorted(confronti.iterdir()):
        if not p.is_file() or p.suffix.lower() != ".xlsx":
            continue
        if p.name.endswith(OUTPUT_SUFFIX):
            skipped += 1
            continue
        try:
            out = process_one(p, hash_map, confronti)
            if out:
                print(f"OK: {p.name} -> {out.name}")
                done += 1
        except Exception as e:
            errors.append(f"{p.name}: {e}")
            print(f"ERRORE: {p.name}: {e}")

    print(f"Fine. Creati/aggiornati: {done}, saltati: {skipped}")
    if errors:
        print("Errori:")
        for line in errors:
            print(f"  - {line}")
        raise SystemExit(1)


__all__ = [
    "OUTPUT_SUFFIX",
    "display_label_from_pdf_filename",
    "process_one",
    "read_first_sheet_table",
    "transform_table",
    "write_confronto_vista_copy",
    "write_xlsx_table",
    "run_cli_main",
]
