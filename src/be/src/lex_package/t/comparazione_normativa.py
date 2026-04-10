from pydantic import BaseModel, Field
from typing import Literal, Optional


class M(BaseModel):
    relazione_contenuto: Optional[str] = Field(
        ...,
        description="comparazione tra il contenuto di due testi normativi correlati, il primo proveninete da un regolamento attuativo, il secondo proveniente dal regolamento normativo da attuare. descrizione tra le 70 e le 120 parole.",
    )
    motivazione: Optional[str] = Field(
        ...,
        description="motivazione --- il modo in cui un testo si rapporta all'altro (inserisce, sopprime, sostituisce, applica",
    )
