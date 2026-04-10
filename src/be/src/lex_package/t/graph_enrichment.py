"""Schema Pydantic per l'estrazione strutturata (grafo Neo4j) da testo legale."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DateItemExtraction(BaseModel):
    """Data estratta con classificazione indicativa."""

    raw_text: str = Field(description="Testo della data come compare nel documento")
    relation_kind: Literal["published", "validity", "other"] = Field(
        default="other",
        description="published=pubblicazione; validity=validità, entrata in vigore, scadenza; other=altro",
    )


class SectionEntitiesExtraction(BaseModel):
    """Entità e date per una sezione (indice allineato all'ordine di costruzione del grafo)."""

    section_index: int = Field(ge=0, description="Indice sezione (0-based) come nel prompt")
    organizations: list[str] = Field(
        default_factory=list,
        description="Organizzazioni citate (max 8, nomi brevi)",
    )
    persons: list[str] = Field(
        default_factory=list,
        description="Persone fisiche o nominative citate (max 8)",
    )
    roles: list[str] = Field(
        default_factory=list,
        description="Ruoli o cariche (es. amministratore delegato, giudice)",
    )
    locations: list[str] = Field(
        default_factory=list,
        description="Luoghi geografici o sedi",
    )
    section_dates: list[DateItemExtraction] = Field(
        default_factory=list,
        description="Date presenti nel testo della sezione",
    )


class GraphEnrichmentPayload(BaseModel):
    """Output unico per arricchimento grafo (documento + sezioni)."""

    legal_concepts: list[str] = Field(
        default_factory=list,
        description="Fino a 5 principali concetti legali italiani (es. Responsabilità civile)",
    )
    legal_actions: list[str] = Field(
        default_factory=list,
        description="Fino a 5 principali azioni legali (es. Ricorso, Notifica, Sanzione)",
    )
    document_dates: list[DateItemExtraction] = Field(
        default_factory=list,
        description="Date rilevanti a livello di documento (pubblicazione, validità, ecc.)",
    )
    sections: list[SectionEntitiesExtraction] = Field(
        default_factory=list,
        description="Per ogni sezione con testo significativo: entità e date",
    )
