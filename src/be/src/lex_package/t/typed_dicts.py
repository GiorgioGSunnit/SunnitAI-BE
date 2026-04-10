from typing import TypedDict, Literal, Optional, List


class Riferimento(TypedDict):
    n_articolo: str
    n_paragrafo: Optional[str]
    nome_documento: Optional[str]
    codice_documento: Optional[str]


class ContenutoParsato(TypedDict):
    requirement: str
    core_text: str
    search_text: str
    pattern_type: str
    riferimenti: List[Riferimento]
    titolo_articolo: str
    titoloParte_articolo: Optional[str]
    titoloTitolo_articolo: Optional[str]
    titoloCapitolo_articolo: Optional[str]
    titoloAllegato_articolo: Optional[str]
    titoloSezione_articolo: Optional[str]
    contenuto: str
    identificativo: str
    Nome_documento: str  # nota: underscore al posto dello spazio


class Articolo_parsed(TypedDict):
    titolo_articolo: str
    contenuto: str
    page: int
    identificativo: str
    contenuto_parsato: List[ContenutoParsato]
    core_text: str
    search_text: str
    requirement: str
    pattern_type: str
    Nome_documento: str  # duplicato del campo sopra, possibile ambiguità
