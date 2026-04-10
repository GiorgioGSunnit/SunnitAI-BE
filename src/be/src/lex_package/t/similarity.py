from pydantic import BaseModel, Field
from typing import Optional


class Similarity(BaseModel):
    k: int = Field(
        default=0,
        description="un coefficiente da 0 a 100 che misura la similarita tra due articoli, 0 se gli argomenti trattati sono completamente diversi, 100 il significato degli articoli è identico",
    )
    t_1: Optional[str] = Field(default=None, description="titolo del primo articolo")
    t_2: Optional[str] = Field(default=None, description="titolo del secondo articolo")
    motivazione: Optional[str] = Field(
        default=None,
        description="motivazione per cui si é assegnato lo specifico punteggio di similarità",
    )


class Correlazione(BaseModel):
    coefficiente_correlazione: int = Field(
        default=0,
        description="un coefficiente da 0 a 30 che misura la similarita tra due testi legislativi, 0 se gli argomenti trattati sono completamente diversi, 30 il significato dei testi è identico",
    )


class Correlazione_con_Dettaglio(BaseModel):
    coefficiente_correlazione: int = Field(
        default=0,
        description="un coefficiente da 0 a 30 che misura la similarita tra due testi legislativi",
    )
    dettaglio: Optional[str] = Field(
        default=None,
        description="descrizione di circa 100 parole di come il comma attuativo attua il comma da attuare",
    )
