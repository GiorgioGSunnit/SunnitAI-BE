from typing import List, TypedDict
from enum import Enum


class Scopo(Enum):
    SOSTITUZIONE = "sostituzione"
    AGGIUNTA = "aggiunta"
    SOPPRESSIONE = "soppressione"


class TipoParte(Enum):
    COMMA = "comma"
    LETTERA = "lettera"
    NUMERO = "numero"
    ROMANINO = "romanino"


class Parte(TypedDict):
    """
    TypedDict for a part of an article.
    """

    identificativo: str
    tipo: TipoParte
    contenuto: str


class ParteAI(Parte):
    """
    TypedDict for a part of an article with AI enhancements.
    """

    scopo: Scopo
    description: str


class Articolo(TypedDict):
    """
    TypedDict for an article.
    """

    identificativo: str
    titolo: str
    page: int
    contenuto: str


class Documento(TypedDict):
    """
    TypedDict for a document.
    """

    titolo: str
    considerando: str
    articoli: List[Articolo]


class ArticoloEmendativo(Articolo):
    """
    TypedDict for an amending article.
    """

    refArticoloEmendato: str
    parti: List[Parte]  # in prima battuta solo campo "contenuto", poi rifinire...


class Grapper:
    def __init__(self):
        self.pages: List[str] = []  # the pages of the document
        self.start_index = -1
        self.end_index = -1

    def set_start_index(self, start_index: int):
        self.start_index = start_index

    def set_index(self, index: int):
        if self.start_index == -1:
            self.start_index = index
        else:
            if self.end_index == -1:
                self.end_index = index

    def set_end_index(self, end_index: int):
        self.end_index = end_index

    def set_pages(self, pages):
        self.pages = pages

    def add_page(self, page):
        # if start_index has been set, and the page is new, add it to the list
        if self.start_index > -1 and page not in self.pages:
            print("adding the page :", page)
            self.pages.append(page)
        else:
            if self.start_index == -1:
                print("start_index == -1, not adding the page", page)
            if page in self.pages:
                print("page already in the list:", page)

    def get_page_lines(self, page):
        return page.get_text().split("\n")

    def get_article_content(self):
        if not self.pages or not self.start_index or not self.end_index:
            return (
                "self.start_index =="
                + str(self.start_index)
                + " self.end_index == "
                + str(self.end_index)
                + " self.pages == "
                + str(self.pages)
            )

        content = ""  # content is initialized as empty string

        # if only one page
        if len(self.pages) < 2:
            page_lines = self.get_page_lines(self.pages[0])
            content += " ".join(
                page_lines[self.start_index + 1 : self.end_index]
            )  # correct indexes if necessary

        else:  # take all the lines of the first page starting from the start_line
            n = len(self.pages)
            i = 1
            # add the content in the first page
            page_lines = self.get_page_lines(self.pages[0])
            content += " ".join(
                page_lines[self.start_index + 1 :]
            )  # correct indexes if necessary

            while i < n - 1:
                # untill the last page is reached
                content += " ".join(self.get_page_lines(self.pages[i]))
                i += 1

            content += " ".join(
                self.get_page_lines(self.pages[n - 1])[: self.end_index]
            )
            self.set_start_index(-1)
            self.set_end_index(-1)
            self.set_pages([])

        return content
