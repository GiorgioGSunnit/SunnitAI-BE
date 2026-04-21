"""Estrazione strutturata dei metadati di documento via LLM (prime pagine del PDF)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from openai import APITimeoutError, RateLimitError

from lex_package.llm.factory import build_chat_model
from lex_package.t.document_metadata import DocumentMetadata, EditorEnterprise

logger = logging.getLogger("lex_package.metadata_extraction")

# Numero di pagine iniziali da leggere per l'estrazione dei metadati
_PAGES_TO_READ = 4
# Lunghezza massima del testo campione passato all'LLM
_MAX_SAMPLE_CHARS = 8_000


def _read_first_pages(pdf_path: str | Path, n_pages: int = _PAGES_TO_READ) -> str:
    """Estrae il testo delle prime *n_pages* pagine del PDF con PyMuPDF.

    Restituisce stringa vuota in caso di errore (il PDF potrebbe non essere ancora
    su disco o non essere leggibile).
    """
    try:
        import fitz  # PyMuPDF

        with fitz.open(str(pdf_path)) as doc:
            pages = min(n_pages, len(doc))
            return "\n".join(doc[i].get_text() for i in range(pages)).strip()
    except Exception as exc:
        logger.warning("metadata_extraction: impossibile leggere il PDF '%s': %s", pdf_path, exc)
        return ""


def extract_document_metadata(
    pdf_path: str | Path,
    file_name: str,
    document_name: str | None = None,
) -> DocumentMetadata | None:
    """Estrae i metadati strutturati dal documento PDF.

    Legge le prime pagine del PDF, effettua una chiamata LLM con output strutturato
    e restituisce un oggetto :class:`DocumentMetadata`.  In caso di errore LLM o PDF
    non leggibile restituisce ``None`` (il chiamante gestirà il fallback).

    Args:
        pdf_path: Percorso al file PDF su disco.
        file_name: Nome del file (inclusa estensione) come appare nel filesystem.
        document_name: Eventuale nome normalizzato già disponibile (usato come hint).

    Returns:
        :class:`DocumentMetadata` oppure ``None`` se l'estrazione fallisce.
    """
    raw_text = _read_first_pages(pdf_path)
    if not raw_text:
        logger.warning(
            "metadata_extraction: testo vuoto per '%s', salto estrazione LLM.", file_name
        )
        return None

    sample = raw_text[:_MAX_SAMPLE_CHARS]
    hint = (
        f"Nome file: {file_name}\n"
        + (f"Nome documento normalizzato (hint): {document_name}\n" if document_name else "")
    )

    system = SystemMessage(
        content=(
            "Sei un esperto di documenti legali italiani ed europei. "
            "Analizza il testo delle prime pagine di un documento e "
            "estrai i metadati richiesti nello schema JSON fornito. "
            "Il titolo ufficiale del documento (document_name) deve essere estratto dal testo, "
            "NON dal nome del file: potrebbe differire significativamente. "
            "Per editor_enterprises includi tutte le organizzazioni che hanno redatto, "
            "emesso o per cui è stato scritto il documento. "
            "Se un'informazione non è presente nel testo, lascia il campo null o lista vuota. "
            "Rispondi SOLO con JSON valido secondo lo schema."
        )
    )
    human = HumanMessage(
        content=(
            f"{hint}\n"
            f"--- TESTO PRIME PAGINE ---\n{sample}\n--- FINE TESTO ---"
        )
    )

    try:
        llm = (
            build_chat_model(target="primary", temperature=0)
            .with_structured_output(DocumentMetadata)
            .with_retry(
                retry_if_exception_type=(RateLimitError, APITimeoutError),
                stop_after_attempt=3,
                wait_exponential_jitter=True,
            )
        )
        result: DocumentMetadata = llm.invoke([system, human])
        logger.info(
            "metadata_extraction: estratti metadati per '%s' — "
            "document_name=%r, editors=%d",
            file_name,
            result.document_name,
            len(result.editor_enterprises),
        )
        return result
    except Exception as exc:
        logger.warning("metadata_extraction: LLM fallito per '%s': %s", file_name, exc)
        return None


def metadata_fallback(file_name: str, document_name: str) -> DocumentMetadata:
    """Costruisce metadati minimi quando l'estrazione LLM non è disponibile.

    Usa *document_name* come titolo e non popola gli editor (lista vuota).
    """
    return DocumentMetadata(
        document_name=document_name,
        editor_enterprises=[],
        document_number=None,
        issue_date=None,
    )
