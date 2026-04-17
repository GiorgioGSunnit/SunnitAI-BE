"""
Pydantic output schemas for Part B (DocumentPart AI enrichment).

PartEnrichment   — structured output for a single leaf part.
SectionSynthesis — same fields, used for section and document synthesis nodes.
Both reuse the Pattern_Type enum already defined in analisi_articolo.py.
"""

from pydantic import BaseModel, Field
from .analisi_articolo import Pattern_Type


class PartEnrichment(BaseModel):
    """Structured AI output for a single leaf DocumentPart (~10 000 chars of text)."""

    abstract: str = Field(
        description=(
            "Descrizione del contenuto della parte normativa in massimo 200 parole. "
            "Deve catturare: le disposizioni principali, i soggetti destinatari, "
            "gli obblighi o condizioni stabiliti e il contesto normativo."
        )
    )
    main_phrase: str = Field(
        description=(
            "La frase o proposizione più significativa del testo, riportata "
            "letteralmente e per intero, senza ellissi."
        )
    )
    meaning: Pattern_Type = Field(
        description="Il tipo di disposizione normativa prevalente nel testo della parte."
    )


class SectionSynthesis(BaseModel):
    """
    Structured AI output for a section or document synthesis node.

    At section level  : the AI reads the abstracts of all leaf parts in the section.
    At document level : the AI reads the abstracts of all section synthesis nodes.
    The input is always compressed (abstracts), never raw text, so token cost is
    proportional to the number of nodes, not to the size of the raw document.
    """

    abstract: str = Field(
        description=(
            "Sintesi in massimo 200 parole dell'intera sezione o documento, "
            "ottenuta integrando i riepiloghi delle parti che la compongono. "
            "Deve evidenziare i temi centrali e le disposizioni più rilevanti."
        )
    )
    main_phrase: str = Field(
        description=(
            "La frase o proposizione più rappresentativa dell'intera sezione, "
            "selezionata o sintetizzata a partire dai riepiloghi delle parti."
        )
    )
    meaning: Pattern_Type = Field(
        description="Il tipo di disposizione normativa prevalente nell'intera sezione o documento."
    )
