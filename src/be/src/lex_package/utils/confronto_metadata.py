"""
Rilevamento di sezioni "di servizio" (scheda documento, informazioni sul documento, ecc.)
per il confronto attuativo: non devono ricevere punteggio di correlazione significativo.
"""

from __future__ import annotations

# Frasi tipiche dell'area qualità / metadati del documento (titoli e identificativi testuali)
_METADATA_PHRASES: tuple[str, ...] = (
    "informazioni sul documento",
    "informazioni del documento",
    "scheda del documento",
    "storia del documento",
    "qualità del documento",
    "qualita del documento",
)


def looks_like_document_metadata_quality(*text_parts: str | None) -> bool:
    """
    True se uno dei frammenti (titolo articolo, identificativo comma/sottocomma, composto)
    contiene testo assimilabile a metadati di documento.
    """
    blob = " ".join(
        str(p).strip() for p in text_parts if p is not None and str(p).strip()
    )
    if not blob:
        return False
    low = blob.lower()
    return any(p in low for p in _METADATA_PHRASES)


def unit_has_metadata_labels(unit: dict) -> bool:
    """Unità foglia da ``_leaf_units`` con chiavi articolo/comma/sottocomma."""
    return looks_like_document_metadata_quality(
        unit.get("articolo_titolo"),
        unit.get("comma_identificativo"),
        unit.get("sottocomma_identificativo"),
        unit.get("identificativo_composto"),
        unit.get("contenuto"),
    )
