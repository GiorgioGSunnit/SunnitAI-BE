"""Schema Pydantic per l'estrazione strutturata dei metadati di un documento legale."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class EditorEnterprise(BaseModel):
    """Organizzazione coinvolta nella produzione o emissione del documento."""

    name: str = Field(
        description=(
            "Nome ufficiale dell'organizzazione o ente (es. 'Banca d'Italia', "
            "'Ministero dell'Economia e delle Finanze', 'Commissione Europea')"
        )
    )
    role: Literal["author", "publisher", "issuing_authority", "other"] = Field(
        default="other",
        description=(
            "author=ha redatto/scritto il documento; "
            "publisher=ha pubblicato o distribuito il documento; "
            "issuing_authority=organo ufficiale che ha emesso il documento; "
            "other=altra relazione"
        ),
    )


class DocumentMetadata(BaseModel):
    """Metadati estratti dal testo del documento (prime pagine)."""

    document_name: str = Field(
        description=(
            "Titolo ufficiale del documento come appare nel testo "
            "(può differire dal nome del file)"
        )
    )
    editor_enterprises: list[EditorEnterprise] = Field(
        default_factory=list,
        description=(
            "Organizzazioni, enti o autorità coinvolti nella redazione, pubblicazione "
            "o emissione del documento (massimo 5). Includere chi ha scritto il documento "
            "e per chi è stato scritto se esplicitato."
        ),
    )
    document_number: Optional[str] = Field(
        default=None,
        description=(
            "Numero o identificativo ufficiale del documento "
            "(es. 'n. 123/2024', 'Delibera n. 15', 'Decreto Legislativo 231/2001')"
        ),
    )
    issue_date: Optional[str] = Field(
        default=None,
        description="Data di emissione o pubblicazione ufficiale del documento",
    )
