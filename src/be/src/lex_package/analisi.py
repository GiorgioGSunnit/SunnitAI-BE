import logging
import tempfile
import os
from copy import deepcopy
from lex_package.parse import parse
from lex_package.analisi_parallel import analisi_parallel

logger = logging.getLogger("lex_package.analisi")

# Execute the async function with asyncio.run()
async def analisi(pdf_path, pdf_name, template_hint: str | None = None):
    logger.info(f"Starting analisi for {pdf_name} at {pdf_path}")

    # ── OCR pre-check: detect broken font encoding ────────────────────────
    _pdf_path_to_parse = pdf_path
    _ocr_tmp = None
    try:
        import fitz
        _doc = fitz.open(str(pdf_path))
        _full_text = "".join(page.get_text() for page in _doc)
        _num_pages = max(_doc.page_count, 1)
        _doc.close()
        _avg_chars = len(_full_text.strip()) / _num_pages
        if _avg_chars < 50:
            logger.warning(
                "Low text yield (%.1f chars/page) for %s — attempting OCR",
                _avg_chars, pdf_name,
            )
            print(f"[OCR] Low text yield ({_avg_chars:.1f} chars/page) — running OCR at 300 DPI")
            try:
                from pdf2image import convert_from_path
                import pytesseract
                _images = convert_from_path(str(pdf_path), dpi=300)
                _pdf_pages = []
                for i, _img in enumerate(_images):
                    _page_pdf = pytesseract.image_to_pdf_or_hocr(_img, lang="ita", extension="pdf")
                    _pdf_pages.append(_page_pdf)
                    logger.debug("OCR page %d/%d", i + 1, len(_images))
                # Merge pages into a single temp PDF
                import io
                try:
                    import fitz as _fitz2
                    _merged = _fitz2.open()
                    for _page_bytes in _pdf_pages:
                        _src = _fitz2.open("pdf", _page_bytes)
                        _merged.insert_pdf(_src)
                        _src.close()
                    _ocr_tmp = tempfile.NamedTemporaryFile(
                        suffix=".pdf", delete=False, prefix="ocr_"
                    )
                    _merged.save(_ocr_tmp.name)
                    _merged.close()
                    _ocr_tmp.close()
                    _pdf_path_to_parse = _ocr_tmp.name
                    logger.info(
                        "OCR complete — %d pages written to %s",
                        len(_pdf_pages), _ocr_tmp.name,
                    )
                    print(f"[OCR] Success — {len(_pdf_pages)} pages → {_ocr_tmp.name}")
                except Exception as _merge_err:
                    logger.error("OCR merge failed: %s", _merge_err)
                    print(f"[OCR] Merge error: {_merge_err} — using original PDF")
            except Exception as _ocr_err:
                logger.error("OCR failed for %s: %s", pdf_name, _ocr_err)
                print(f"[OCR] Error: {_ocr_err} — using original PDF")
        else:
            logger.debug("Text yield OK (%.1f chars/page) — skipping OCR", _avg_chars)
    except Exception as _probe_err:
        logger.warning("OCR pre-check failed: %s — proceeding normally", _probe_err)

    try:
        articoli: list[dict] = parse(_pdf_path_to_parse, pdf_name, template_hint=template_hint)
    finally:
        if _ocr_tmp is not None and os.path.exists(_ocr_tmp.name):
            os.unlink(_ocr_tmp.name)
            logger.debug("Removed OCR temp file %s", _ocr_tmp.name)
    logger.info(f"Parse returned {len(articoli)} articoli")
    output = await analisi_parallel(articoli, pdf_name)
    logger.info(f"[OUTPUT ANALISI]: {output}")
    return output

async def consolida_analisi(testocontenuto):
    res = []
    TitoloProvvisorio = "" 
    IdentificativoArticolo = ""
    titolo_appoggio = ""
    UnaLegge = False
    print("####### consolida_analisi #######")
    for j,y in enumerate(testocontenuto):
        if not(UnaLegge) and ("articolo" in str(y.get("identificativo", "")).lower()):
            # print("####### E' una LEGGE! #######")
            UnaLegge = True

    for i,x in enumerate(testocontenuto):
        if UnaLegge and (str(x.get("identificativo", "")).isdigit()) and not x.get("contenuto_parsato"):
            # Only skip bare-digit entries that have no contenuto_parsato —
            # those are genuine sub-structure items (commi numbered "1","2")
            # leaking into the top-level list. Real articles always have
            # contenuto_parsato populated, even when their identificativo
            # has been normalized to a bare number upstream.
            continue
        else:
            TitoloProvvisorio = x.get("titolo", "")
            IdentificativoArticolo =  str(x.get("identificativo", "")).lower().replace(" ", "").replace("art.", "Articolo").strip()

            if (TitoloProvvisorio == ""):
                titolo_appoggio = x["contenuto_parsato"][0].get("contenuto", "")
                titolo_appoggio = titolo_appoggio.replace("\n", "").strip()
                if (titolo_appoggio.startswith("(") and titolo_appoggio.endswith(")")):
                    TitoloProvvisorio = titolo_appoggio

            x["titolo"] = TitoloProvvisorio
            x["identificativo"] = IdentificativoArticolo

            res.append(deepcopy(x))

    return res
