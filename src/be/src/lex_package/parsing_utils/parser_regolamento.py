import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence, TYPE_CHECKING

try:
    from lex_package.parsing_utils.parser_banca import identify_repeated_headers_footers
except ModuleNotFoundError:  # pragma: no cover - fallback for test environments without PyMuPDF
    def identify_repeated_headers_footers(*args, **kwargs):
        return []

if TYPE_CHECKING:
    import fitz  # pragma: no cover

logger = logging.getLogger(__name__)
from lex_package.utils.pdf_extract import extract_page_text
from lex_package.parsing_utils.article_suffix import ORDINAL, SUFFIX, canonical_suffix

# Ho riscritto il parser incapsulando gli stati in oggetti dedicati per eliminare
# i side effect sparsi e rendere più prevedibile l'aggiunta dei placeholder.


# Ogni grafia delle estensioni latine (nonies/novies, quaterdecies/quattuordecies,
# ...), la più lunga per prima: article_suffix.py, la stessa tabella che V2 usa
# per cercare gli articoli. L'identificativo scritto è sempre quello canonico.
ARTICLE_PATTERN = re.compile(
    r"^(?P<denominazione>ART\.|Articolo)\s*"
    r"(?P<identificativo>\d+)"
    r"(?:[-\s]*(?P<estensione>" + SUFFIX + r")\b)?\.?(?:\s*.+)?\s*$",
    re.IGNORECASE,
)
_ESTENSIONI = SUFFIX[3:-1]
# Una riga apre un articolo solo con la rubrica scritta "Art." / "ART." /
# "Articolo" con l'iniziale maiuscola: un rinvio dentro una nota ("art. 409,
# quinto comma, c.p.p. ...", c.p.p. 2026 p. 6) apriva un falso articolo 409, e
# l'ordine della numerazione scartava poi tutte le rubriche vere da 35 a 408.
_CAPITALISED_HEADING = re.compile(r"^(?:A[Rr][Tt]\.|A(?:RTICOLO|rticolo))\s*\d")
# Dopo il numero (e l'estensione) una rubrica ha un punto, un trattino, una
# parentesi, una maiuscola o niente; un rinvio continua con una virgola o una
# parola minuscola ("art. 5 c.p.p.", "Art. 3, comma 2", "Art. 12 del decreto").
_REFERENCE_TAIL = re.compile(r"^\s*(?:[,;:]|[a-zà-ÿ])")
# ...salvo le parole con cui una rubrica dice che l'articolo non c'è più.
_HEADING_TAIL_WORDS = re.compile(r"^\s*(?:abrogat|soppress|omissis)", re.IGNORECASE)


def article_heading_match(line: str | None) -> re.Match | None:
    """ARTICLE_PATTERN's match when the line is a real article heading: the
    capitalised "Art." / "Articolo", and after the number (and estensione)
    nothing that reads as a reference -- else None."""
    if not line or not _CAPITALISED_HEADING.match(line):
        return None
    match = ARTICLE_PATTERN.search(line)
    if not match:
        return None
    tail = line[match.end("estensione") if match.group("estensione") else match.end("identificativo"):]
    if tail.startswith("."):
        return match
    if _REFERENCE_TAIL.match(tail) and not _HEADING_TAIL_WORDS.match(tail):
        return None
    return match
# Rubrica inline: "Art. 5 Definizioni" -> "Definizioni". L'estensione è una
# delle forme latine note, non una parola qualsiasi (altrimenti "Art. 1
# Approvazione del codice" perdeva "Approvazione").
INLINE_TITLE_PATTERN = re.compile(
    r"^(?:ART\.|Articolo)\s*\d+(?:[-\s]*(?:" + _ESTENSIONI + r")\b)?\.?\s*(.+)$",
    re.IGNORECASE,
)
# Testi consolidati in cui il decreto approva un codice allegato (es. D.Lgs.
# 104/2010, codice del processo amministrativo = Allegato 1): le rubriche sono
# "Allegato 1 - Art. 112 ..." e la numerazione riparte in ogni allegato.
ALLEGATO_ARTICLE_PATTERN = re.compile(
    r"^Allegato\s+(?P<allegato>\d+)\s*[-\u2013\u2014]\s*(?P<articolo>(?:ART\.|Articolo)\s+\d+.*)$",
    re.IGNORECASE,
)
# Intestazione nuda di un allegato, eventualmente con note: "Allegato 2 [371]".
ALLEGATO_HEADING_PATTERN = re.compile(
    r"^Allegato\s+\d+(?:\s*\[\d+\])*\s*$", re.IGNORECASE
)
MIN_ALLEGATO_ARTICLES = 3
MAIN_ALLEGATO = 1

# Una seconda numerazione nello stesso PDF (preleggi e poi codice civile; codice
# e poi disposizioni di attuazione; decreto e poi allegato): un "Art. 1" senza
# estensione, dopo un numero più alto, apre una nuova sequenza se una delle
# righe non vuote che lo precedono è un'intestazione di struttura. Senza questo
# l'ordine della numerazione scartava ogni rubrica della seconda sequenza (c.c.
# 2026: gli artt. 1-31 del libro I finivano dentro l'art. 31 delle preleggi).
# La sequenza con più articoli resta senza prefisso; le altre lo prendono dalla
# loro intestazione (SEQUENCE_PREFIXES), così non collidono con il codice.
SEQUENCE_CONTEXT_LINES = 6
SEQUENCE_BOUNDARY_PATTERN = re.compile(
    r"^\s*(?:CODICE|LIBRO|PARTE|TITOLO|CAPO|SEZIONE|ALLEGATO|DISPOSIZIONI|NORME|REGOLAMENTO|PREAMBOLO)\b"
    r"|disposizioni (?:sulla legge|per l.attuazione|di attuazione|transitorie)|norme di attuazione"
    r"|regolamento per l.esecuzione",
    re.IGNORECASE,
)
SEQUENCE_PREFIXES = (
    (re.compile(r"disposizioni sulla legge in generale|preleggi", re.IGNORECASE), "prel"),
    (re.compile(r"attuazione", re.IGNORECASE), "att"),
    (re.compile(r"regolamento|esecuzione", re.IGNORECASE), "reg"),
    (re.compile(r"allegato", re.IGNORECASE), "all"),
)
# Every prefix an identificativo can carry (allegato mode's "allN-"/"decreto-"
# included); utils/flatten.py keeps it in the Section name.
SEQUENCE_PREFIX_RE = r"^((?:all\d+|decreto|prel\d*|att\d*|reg\d*|seq\d+)-)"

ARTICLE_CODE_PATTERN = re.compile(
    r"\b(?:n\.\s*)?(?P<codice>\d{3,4}/\d{4})\b", flags=re.IGNORECASE
)
DOC_CODE_PATTERN = re.compile(
    r"\b(?:n\.\s*)?(?P<codice>\d{3,4}/\d{4})\b", flags=re.IGNORECASE
)
# Pattern per derivare il codice documento dal nome file quando non è in prima pagina.
# Cerca nel nome del file un pattern tipo NNNNNRNNNN o NNNNNDNNNN (anno+tipo+numero UE) e lo normalizza in NNNNN/NNNN.
DOC_CODE_FROM_STEM_PATTERN = re.compile(
    r"(?P<a>\d{4,5})[RD](?P<b>\d{3,4})(?:_|$)", flags=re.IGNORECASE
)
MAX_HEADING_LENGTH = 100
DEFAULT_MIN_REPEATS = 5

ESTENSIONE_MAP = {None: 1, **ORDINAL}


def _normalize_line(line):
    return line.replace(" ", "").replace("\t", "").replace("\n", "").strip()


def normalize_line(line):
    """Rimuove i numeri di pagina e normalizza spaziature"""
    return re.sub(r"\bpage\s*\d+\b", "", line, flags=re.IGNORECASE).strip().lower()


@dataclass
class SequenceCheck:
    is_valid: bool
    number: int
    extension_value: int


def _extension_cardinality(estensione: str | None) -> int:
    if not estensione:
        return ESTENSIONE_MAP[None]
    return ESTENSIONE_MAP.get(estensione.lower(), ESTENSIONE_MAP[None])


def validate_article_sequence(
    previous_number: int,
    previous_extension: int,
    identificativo: str,
    estensione: str | None,
) -> SequenceCheck:
    try:
        numero = int(identificativo)
    except (TypeError, ValueError):
        logger.debug("Identificativo non numerico: %s", identificativo)
        return SequenceCheck(False, previous_number, previous_extension)

    current_cardinality = _extension_cardinality(estensione)
    is_valid = False

    if previous_number == 0:
        is_valid = True
    elif numero > previous_number:
        is_valid = True  # any forward progression is valid
    elif (
        previous_number == numero
        and previous_extension is not None
        and previous_extension + 1 == current_cardinality
    ):
        is_valid = True
    elif (
        previous_number == numero
        and previous_extension is not None
        and previous_extension + 2 == current_cardinality
    ):
        is_valid = True

    return SequenceCheck(is_valid, numero, current_cardinality)


def clean_text(text, repeated_lines):
    cleaned_lines = []
    for line in text.split("\n"):
        norm_line = normalize_line(line)
        if norm_line not in repeated_lines:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def nojunkchars(text):
    text = re.sub(r"▼\w+\d+", "", text)
    text = text.replace("\n", "").replace("\t", "")
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"[^A-Za-z0-9]", "", text)
    return text.lower()


@dataclass
class ArticleBuilder:
    document_name: str
    document_code: str
    page: int
    identifier: str
    title: str
    article_code: str
    _raw_content: str = ""
    hash_value: str = ""
    sequence: int = 0

    def add_line(self, line: str) -> None:
        self._raw_content += f"{line}\n"

    def _update_hash(self) -> None:
        # sha256, not Python's hash(): that is salted per process, so the same
        # article got a different hash (and node id) on every ingestion.
        if self._raw_content:
            self.hash_value = hashlib.sha256(nojunkchars(self._raw_content).encode("utf-8")).hexdigest()
        else:
            self.hash_value = ""

    def to_dict(self) -> dict:
        self._update_hash()
        return {
            "nomedocumento": self.document_name,
            "codicedocumento": self.document_code,
            "page": self.page,
            "identificativo": self.identifier,
            "titolo": self.title,
            "codicearticolo": self.article_code,
            "contenuto": self._raw_content,
            "hash": self.hash_value,
        }


@dataclass
class ArticleCollector:
    document_name: str
    document_code: str
    articles: list[dict] = field(default_factory=list)
    current: ArticleBuilder | None = None
    previous_number: int = 0
    previous_extension: int = ESTENSIONE_MAP[None]
    include_placeholders: bool = False
    allegato: int | None = None
    sequence: int = 0
    sequence_contexts: dict[int, str] = field(default_factory=dict)
    recent_lines: list[str] = field(default_factory=list)
    pending_line: str | None = None

    def note_line(self, line: str) -> None:
        """The last few non-empty lines, for SEQUENCE_BOUNDARY_PATTERN."""
        if line.strip():
            self.recent_lines = (self.recent_lines + [line.strip()])[-SEQUENCE_CONTEXT_LINES:]

    def restarts_sequence(self, sequence: "SequenceCheck") -> bool:
        return (sequence.number == 1 and sequence.extension_value == ESTENSIONE_MAP[None]
                and self.previous_number > 1
                and any(SEQUENCE_BOUNDARY_PATTERN.search(line) for line in self.recent_lines))

    def enter_sequence(self) -> None:
        self.sequence += 1
        self.previous_number = 0
        self.previous_extension = ESTENSIONE_MAP[None]

    def enter_allegato(self, allegato: int) -> None:
        """Ogni allegato ha la propria numerazione: si riparte da capo."""
        self.allegato = allegato
        self.previous_number = 0
        self.previous_extension = ESTENSIONE_MAP[None]

    def start_article(
        self,
        *,
        sequence: SequenceCheck,
        page: int,
        identifier_line: str,
        title_line: str,
        article_code: str,
    ) -> None:
        self._finalize_current()
        self.sequence_contexts.setdefault(self.sequence, " ".join(self.recent_lines))
        self._maybe_insert_placeholder(sequence.number, page)
        self.previous_number = sequence.number
        self.previous_extension = sequence.extension_value
        self.current = ArticleBuilder(
            document_name=self.document_name,
            document_code=self.document_code,
            page=page,
            identifier=identifier_line,
            title=title_line,
            article_code=article_code,
            sequence=self.sequence,
        )

    def _maybe_insert_placeholder(self, current_number: int, page: int) -> None:
        # Flag per compatibilità: se attivo reinserisce i placeholder (articoli vuoti)
        # per coprire eventuali buchi di numerazione, così si può confrontare
        # facilmente con gli output pre-refactor senza toccare la logica di default.
        if (
            self.include_placeholders
            and self.previous_number
            and (self.previous_number + 1 != current_number)
        ):
            placeholder = {
                "nomedocumento": self.document_name,
                "codicedocumento": "",
                "page": page,
                "identificativo": self.previous_number + 1,
                "titolo": "",
                "codicearticolo": "",
                "contenuto": "",
                "hash": 0,
                "_sequence": self.sequence,
            }
            self.articles.append(placeholder)

    def add_content(self, line: str) -> None:
        if self.current:
            self.current.add_line(line)

    def _finalize_current(self) -> None:
        if self.current:
            self.articles.append({**self.current.to_dict(), "_sequence": self.current.sequence})
            self.current = None

    def finalize(self) -> list[dict]:
        self._finalize_current()
        prefixes = self._sequence_prefixes()
        for article in self.articles:
            prefix = prefixes.get(article.pop("_sequence", 0), "")
            if prefix:
                article["identificativo"] = f"{prefix}-{article['identificativo']}"
        return self.articles

    def _sequence_prefixes(self) -> dict[int, str]:
        """{sequence: prefix} for every sequence but the one with the most
        articles (the code itself, which keeps plain numbers)."""
        sizes: dict[int, int] = {}
        for article in self.articles:
            sizes[article.get("_sequence", 0)] = sizes.get(article.get("_sequence", 0), 0) + 1
        if len(sizes) < 2:
            return {}
        main = max(sizes, key=lambda seq: (sizes[seq], -seq))
        prefixes: dict[int, str] = {}
        for seq in sorted(sizes):
            if seq == main:
                continue
            context = self.sequence_contexts.get(seq, "")
            prefix = next((p for pattern, p in SEQUENCE_PREFIXES if pattern.search(context)), "seq")
            if prefix == "all" or prefix in prefixes.values() or prefix == "seq":
                prefix = f"{prefix}{seq + 1}"
            prefixes[seq] = prefix
        logger.info("Numbering sequences: %s articles each, main %d, prefixes %s", sizes, main, prefixes)
        return prefixes


class RegulationParser:
    def __init__(
        self,
        *,
        min_repeated_lines: int = DEFAULT_MIN_REPEATS,
        max_heading_length: int = MAX_HEADING_LENGTH,
        include_placeholders: bool = False,
    ) -> None:
        self.min_repeated_lines = min_repeated_lines
        self.max_heading_length = max_heading_length
        self.include_placeholders = include_placeholders

    def parse_file(self, pdf_path: str) -> list[dict]:
        import fitz  # Imported lazily to avoid forcing dependency in tests

        doc = fitz.open(pdf_path)
        try:
            repeated_lines = identify_repeated_headers_footers(
                doc, self.min_repeated_lines
            )
            document_stem = Path(pdf_path).stem
            document_code = self._extract_document_code(doc, document_stem=document_stem)
            pages = [
                extract_page_text(doc[page_index]).split("\n")
                for page_index in range(doc.page_count)
            ]
        finally:
            doc.close()

        return self.parse_from_lines(
            pages,
            document_name=Path(pdf_path).name,
            document_code=document_code,
            repeated_lines=repeated_lines,
        )

    def parse_from_lines(
        self,
        pages: Sequence[Sequence[str]],
        *,
        document_name: str,
        document_code: str = "",
        repeated_lines: Sequence[str] | None = None,
    ) -> list[dict]:
        repeated_lookup = set(repeated_lines or [])
        collector = ArticleCollector(
            document_name=document_name,
            document_code=document_code,
            include_placeholders=self.include_placeholders,
        )

        allegato_mode = self._has_allegato_articles(pages)
        for page_num, lines in enumerate(pages, start=1):
            self._process_page_lines(
                collector,
                [line for line in lines if line not in repeated_lookup],
                page_num,
                allegato_mode=allegato_mode,
            )

        return collector.finalize()

    @staticmethod
    def _has_allegato_articles(pages: Sequence[Sequence[str]]) -> bool:
        count = 0
        for lines in pages:
            for line in lines:
                if ALLEGATO_ARTICLE_PATTERN.match(line.strip()):
                    count += 1
                    if count >= MIN_ALLEGATO_ARTICLES:
                        return True
        return False

    @staticmethod
    def _canonical_identifier(match: re.Match) -> str:
        """"Art. 21-nonies" for the heading "Art. 21 novies Annullamento ...":
        the number and canonical estensione only, the title stays in "titolo".
        The raw line, spaces later stripped by consolida_analisi, let flatten
        read "Art. 52 Termini" as 52-ter."""
        identifier = f"Art. {match.group('identificativo')}"
        if match.group("estensione"):
            identifier += f"-{canonical_suffix(match.group('estensione'))}"
        return identifier

    @staticmethod
    def _allegato_identifier(match: re.Match, allegato: int | None) -> str:
        """Identificativo canonico in modalità allegati: gli articoli dell'allegato
        principale (il codice) sono "Art. N"; quelli degli altri allegati e del
        decreto hanno un prefisso, così non collidono con quelli del codice."""
        identifier = f"Art. {match.group('identificativo')}"
        if match.group("estensione"):
            identifier += f"-{canonical_suffix(match.group('estensione'))}"
        if allegato is None:
            return f"decreto-{identifier}"
        if allegato != MAIN_ALLEGATO:
            return f"all{allegato}-{identifier}"
        return identifier

    def _process_page_lines(
        self,
        collector: ArticleCollector,
        lines: list[str],
        page_num: int,
        allegato_mode: bool = False,
    ) -> None:
        title_pending = False
        for idx, line in enumerate(lines):
            # One line late, across pages too: a heading is never its own context.
            if collector.pending_line is not None:
                collector.note_line(collector.pending_line)
            collector.pending_line = line
            header = line
            allegato = collector.allegato
            if allegato_mode:
                stripped = line.strip()
                allegato_match = ALLEGATO_ARTICLE_PATTERN.match(stripped)
                if allegato_match:
                    allegato = int(allegato_match.group("allegato"))
                    header = allegato_match.group("articolo")
                elif ALLEGATO_HEADING_PATTERN.match(stripped):
                    continue
                elif collector.allegato is not None:
                    # Dentro gli allegati solo "Allegato N - Art. X" apre un articolo.
                    header = None
            match = article_heading_match(header)
            if match and allegato != collector.allegato:
                collector.enter_allegato(allegato)
            if collector.current and not match:
                if title_pending:
                    title_pending = False
                else:
                    collector.add_content(line)

            if match and not allegato_mode and collector.current is not None:
                probe = SequenceCheck(True, int(match.group("identificativo")),
                                      _extension_cardinality(match.group("estensione")))
                if collector.restarts_sequence(probe):
                    collector.enter_sequence()
            if match:
                sequence = validate_article_sequence(
                    collector.previous_number,
                    collector.previous_extension,
                    match.group("identificativo"),
                    match.group("estensione"),
                )

                if sequence.is_valid:
                    inline_title_match = INLINE_TITLE_PATTERN.search(header)
                    title_line = (
                        inline_title_match.group(1).strip()
                        if inline_title_match
                        else (lines[idx + 1] if idx + 1 < len(lines) else "")
                    )
                    codicearticolo = self._extract_article_code(
                        lines, idx + 1
                    )
                    collector.start_article(
                        sequence=sequence,
                        page=page_num,
                        identifier_line=(
                            self._allegato_identifier(match, allegato)
                            if allegato_mode
                            else self._canonical_identifier(match)
                        ),
                        title_line=title_line,
                        article_code=codicearticolo,
                    )
                    # Only skip the next line when the title was NOT inline
                    # (i.e. the next line IS the title). When the title was
                    # found inline, the next line is real content and must
                    # not be skipped.
                    title_pending = inline_title_match is None
                else:
                    collector.add_content(line)
            elif len(line) > self.max_heading_length:
                continue

    def _extract_article_code(self, lines: Sequence[str], title_index: int) -> str:
        if 0 <= title_index < len(lines):
            match = ARTICLE_CODE_PATTERN.search(lines[title_index])
            if match:
                return match.group("codice")
        return ""

    def _extract_document_code(
        self, doc: "fitz.Document", *, document_stem: str = ""
    ) -> str:
        if not doc.page_count:
            return self._document_code_from_stem(document_stem) or "CODICE_NON_TROVATO"
        first_page_text = doc[0].get_text()
        match = DOC_CODE_PATTERN.search(first_page_text)
        if match:
            return match.group("codice")
        fallback = self._document_code_from_stem(document_stem)
        if fallback:
            logger.info(
                "Document code derived from filename stem '%s': %s",
                document_stem,
                fallback,
            )
            return fallback
        logger.warning(
            "Document code not found on the first page of '%s'.", doc.name
        )
        return "CODICE_NON_TROVATO"

    def _document_code_from_stem(self, stem: str) -> str:
        """Deriva il codice documento dal nome del file (pattern: cifre + R/D + cifre, es. 32017R0352 -> 32017/0352)."""
        if not stem:
            return ""
        match = DOC_CODE_FROM_STEM_PATTERN.search(stem)
        if match:
            a, b = match.group("a"), match.group("b")
            return f"{a}/{b.zfill(4)}" if len(b) < 4 else f"{a}/{b}"
        return ""


def parser(
    start_doc_name: str = "../data/8_schema.pdf",
    include_placeholders: bool = False,
) -> list[dict]:
    return RegulationParser(include_placeholders=include_placeholders).parse_file(
        start_doc_name
    )


if __name__ == "__main__":
    parser()
