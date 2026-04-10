from pydantic import BaseModel, Field


class Similarity(BaseModel):
    coefficiente_correlazione: int = Field(
        default=0,
        description="un coefficiente da 0 a 30 che misura la similarita tra due testi legislativi, 0 se gli argomenti trattati sono completamente diversi, 30 il significato degli articoli è identico",
    )

class DescriptionSet(BaseModel):
    DescrizioneCambiamento: str = Field(
        default=None,
        description="un testo breve che descriva l'impatto dell'inserimento o dell'eliminazione di una porzione di testo (Articolo o Comma) all'interno di un documento.",
    )