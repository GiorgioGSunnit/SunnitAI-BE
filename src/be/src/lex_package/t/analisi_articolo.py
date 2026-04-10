from typing import Optional
from pydantic import BaseModel, Field
from enum import Enum
from typing import List


s = """
la designazione dell’Agenzia per la cybersicurezza nazionale, con funzioni di coordinatore ai sensi dell’articolo 9, paragrafo 2, della direttiva (UE) 2022/2555, e del Ministero della difesa, ciascuno per gli ambiti di competenza indicati all’articolo 2, comma 1, lettera g), quali Autorità nazionali di gestione delle crisi informatiche su vasta scala, assicurando la coerenza con il quadro nazionale esistente in materia di gestione generale delle crisi informatiche, ferme restando le competenze del Nucleo per la cybersicurezza di cui all’articolo 9 del decreto-legge 14 giugno 2021, n. 82;
Da questo testo dovrebbe dedurre che: 
 - Articolo 9, paragrafo2 è della direttiva (UE) 2022/2555
 - Articolo 9 è del decreto-legge 14 giugno 2021, n. 82
Mentre l'Articolo 2 comma1 (lettera g) è del documento stesso. Il principio è se non c'è scritto "del" o "della" si sotto-intende dello stesso documento...
"""


class Pattern_Type(str, Enum):
    OBBLIGO = "obbligo"
    CONDIZIONE = "condizione"
    TERMINE_TEMPORALE = "termine temporale"
    ALTRO = "altro"
    SANZIONE = "sanzione"


class Riferimento(BaseModel):
    n_articolo: Optional[str] = Field(
        description="""
        Numero o identificatore dell'articolo a cui si fa riferimento (es. '12', '45bis').
        ad esempio se trovi "Articolo 12" o "Articolo 45bis" in un testo, inserisci "12" o "45bis".
        """
    )
    n_paragrafo: Optional[str] = Field(
        default="",
        description="""
        Numero o sigla del paragrafo specifico dell'articolo a cui si fa riferimento (es. '1', '2')"
        ad esempio se trovi "articolo 2 paragrafo 3" metti "3", o se trovi "articolo 3 comma 7ter" metti "7ter". 
        se non esiste, inserisci una stringa vuota
        """,
    )
    nome_documento: str = Field(
        description="Nome completo del documento normativo a cui si fa riferimento (es. 'Codice Civile', 'direttiva (ue)', 'Testo Unico Sicurezza')"
    )
    codice_documento: str = Field(
        description="Codice identificativo del documento normativo a cui si fa riferimento (es. 'cc', 'tusl', '2022/2555'): é una sola parola senza spazi, se trovi una scritta della forma aaaa/aaaa, includi solo quella (es 2022/2055)."
    )


class Riferimento_emendativa(BaseModel):
    n_articolo: Optional[str] = Field(
        description="""
        Numero o identificatore dell'articolo a cui si fa riferimento (es. '12', '45bis').
        ad esempio se trovi "Articolo 12" o "Articolo 45bis" in un testo, inserisci "12" o "45bis".
        """
    )
    n_paragrafo: Optional[str] = Field(
        default="",
        description="""
        Numero o sigla del paragrafo specifico dell'articolo a cui si fa riferimento (es. '1', '2')"
        ad esempio se trovi "articolo 2 paragrafo 3" metti "3", o se trovi "articolo 3 comma 7ter" metti "7ter". 
        se non esiste, inserisci una stringa vuota
        """,
    )


class Analisi_Paragrafo(BaseModel):
    """informazioni sul significato di un paragrafo in un articolo di legge"""

    riferimento_articolo: Optional[str] = Field(
        default=None,
        description="se il titolo articolo contiene una parola simile a 'modifica' e il titolo di un documento, questo campo contiene il titolo del documento che viene modificato: ad esempio 'modifiche del regolamento pinco pallo (ue) 123' diventa 'regolamento pinco pallo (ue) 123'",
    )

    requirement: Optional[str] = Field(
        default=None,
        description="il significato del comma espresso in una breve frase di non più di 100 parole, ottenuto dai punti focali del comma",
    )
    core_text: Optional[str] = Field(
        default=None,
        description="la proposizione più significativa del comma riportata per intero in modo letterale e senza ellissi",
    )
    search_text: Optional[str] = Field(
        default=None,
        description="""la versione ridotta e sintetica della proposizione più significativa del comma, considerandola per intero in modo letterale e senza ellissi, dalla quale sono state eliminate le parole meno significative e meno caratteristiche (quali ad esempio preposizioni o articoli). 
        In questo testo inserisci, se presenti, i riferimenti alla normative, alle leggi e ai documenti citati senza riportare riferimenti troppo puntuali:
        Ad esempio nel caso nel testo sia presente il testo "Ai sensi dell’articolo 16, paragrafo 3, del regolamento (UE) n. 1093/2010" la sintesi richiesta è "regolamento (UE) n. 1093/2010", 
        oppure nel testo "come previsto dal capo 6 della direttiva 2014/17/UE e dall’articolo 8 della direttiva 2008/48/CE." la sintesi richiesta è "direttiva 2014/17/UE direttiva 2008/48/CE.".
        """,
    )
    pattern_type: Optional[Pattern_Type] = Field(
        default=None,
        description="un valore che indica il tipo di pattern del testo",
    )
    riferimenti: Optional[List[Riferimento]] = Field(
        default=None,
        description=(
            "Se il comma ha il campo flag == true, questa é la lista di riferimenti ad altri articoli o paragrafi normativi che sono rilevanti "
            "per il significato del comma. Ogni riferimento puo' specificare: articolo, paragrafo, nome del documento e codice del documento."
            "ad esempio: " + s + ". se il comma ha flag == false, questa lista é vuota."
        ),
    )


class Analisi_emendativa(BaseModel):
    riferimenti_emendativa: Optional[Riferimento_emendativa] = Field(
        default=None,
        description=(
            "questo é il riferimento ad altri articoli o paragrafi normativi che sono rilevanti "
            "per il significato del sottocomma. Ogni riferimento puo' specificare: articolo, paragrafo."
            "ad esempio: "
            + "Esempio1: se si ha una frase del tipo 'l’articolo 14 è così modificato: a) il paragrafo 3 è sostituito dal seguente:'... inserisci 'relazione_articolo' = 'Articolo 14' e 'relazione_comma' = 'Articolo 14 Paragrafo: 3'. "
            + "Esempio2: se si ha una frase del tipo 'L’articolo 3 è sostituito dal seguente:...' inserisci 'relazione_articolo' = 'Articolo 3' e 'relazione_comma' = 'Articolo 3'. "
            + "Esempio3: se si ha una frase del tipo 'l’articolo 4 bis è sostituito dal seguente', inserisci 'relazione_articolo' = 'Articolo 4 bis' e 'relazione_comma' = 'Articolo 4 bis'. "
            + "Esempio4: se si ha una frase del tipo 'all’articolo 6, paragrafo 2, è aggiunta la lettera seguente...' inserisci 'relazione_articolo' = 'Articolo 6' e 'relazione_comma' = 'Articolo 6 Paragrafo 2'."
        ),
    )
    motivazione: Optional[str] = Field(
        default=None,
        description=(
            "Specifica la natura dell'emendamento descritto nel campo 'Estratto'. "
            "Il valore deve essere uno dei seguenti:\n\n"
            "- 'Sopprime': se nel testo sono presenti espressioni come 'soppresso', 'eliminato', 'è soppresso', 'sono soppressi', ecc.\n"
            "- 'Sostituisce': se il testo include parole come 'sostituisce', 'è sostituito', 'modifica', 'aggiorna', ecc.\n"
            "- 'Inserisce': se il testo presenta termini come 'inserisce', 'aggiunge', 'è aggiunto', 'è inserito', ecc.\n\n"
            "La scelta deve essere fatta in base alla presenza di parole chiave nell'estratto normativo, considerando tutte le forme verbali e declinazioni grammaticali delle espressioni indicate."
        ),
    )

    dettaglio: Optional[str] = Field(
        default=None,
        description=(
            "Descrivi come il cambiamento di tipo emendativo tra i requisiti normativi tra i due documenti possa modificare l'impatto legale al quale i soggetti possono essere chiamati a \n"
            "rispondere"
        ),
    )
