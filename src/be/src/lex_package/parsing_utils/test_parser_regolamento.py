"""Unit tests for parser_regolamento's article headings, numbering sequences and
Latin suffixes, and for the Section names flatten gives them (21 Oct: c.p.p.
2026 lost arts. 35-408 to a footnote, c.c. 2026 its arts. 1-31 to the preleggi,
c.p.c. 1-20 got a title in front of the number, "21-nonies" was dropped)."""
from lex_package.parsing_utils.article_suffix import canonical_suffix, normalize_number, number_variants
from lex_package.parsing_utils.parser_regolamento import RegulationParser, article_heading_match
from lex_package.utils.flatten import build_neo4j_graph_payload


def _parse(*pages):
    return RegulationParser().parse_from_lines([list(p) for p in pages], document_name="test.pdf")


def _ids(articles):
    return [a["identificativo"] for a in articles]


# --- cause 1: a reference is not a heading -------------------------------------

def test_a_footnote_reference_does_not_open_an_article():
    for line in ("art. 409, quinto comma, c.p.p. a partecipare", "Art. 409, quinto comma", "Art. 12 del decreto",
                 "Art. 5 c.p.p.", "art. 34. Incompatibilità"):
        assert article_heading_match(line) is None, line
    for line in ("Art. 34. Incompatibilità", "Art. 21-nonies Annullamento d'ufficio[156]", "ARTICOLO 3 - Oggetto",
                 "Articolo 2", "Art. 5 abrogato", "Art. 52 Termini", "Art. 7 (Definizioni)"):
        assert article_heading_match(line) is not None, line


def test_the_articles_after_a_footnote_reference_are_kept():
    articles = _parse(["Art. 34. Incompatibilità", "testo 34", "(1) Comma dichiarato illegittimo:",
                       "art. 409, quinto comma, c.p.p. a partecipare all'udienza", "Art. 35. Parentela", "testo 35",
                       "Art. 36. Astensione", "testo 36"])
    assert _ids(articles) == ["Art. 34", "Art. 35", "Art. 36"]
    assert "art. 409, quinto comma" in articles[0]["contenuto"]


# --- cause 2: a second numbering sequence ----------------------------------------

PRELEGGI_THEN_CODE = (
    ["CODICE CIVILE", "DISPOSIZIONI SULLA LEGGE IN GENERALE", "Art. 1. Indicazione delle fonti.", "Sono fonti...",
     "Art. 2. Leggi.", "La formazione delle leggi..."],
    ["LIBRO I - DELLE PERSONE E DELLA FAMIGLIA", "TITOLO I – DELLE PERSONE FISICHE", "Art. 1. Capacità giuridica.",
     "La capacità giuridica si acquista dal momento della nascita.", "Art. 2. Maggiore età.", "La maggiore età...",
     "Art. 3. Residenza.", "testo"],
    ["DISPOSIZIONI PER L'ATTUAZIONE DEL CODICE CIVILE", "CAPO I – DISPOSIZIONI DI ATTUAZIONE", "Art. 1. (1)",
     "attuazione"],
)


def test_a_restart_after_a_boundary_opens_a_new_sequence_and_the_largest_keeps_plain_numbers():
    articles = _parse(*PRELEGGI_THEN_CODE)
    assert _ids(articles) == ["prel-Art. 1", "prel-Art. 2", "Art. 1", "Art. 2", "Art. 3", "att-Art. 1"]
    assert articles[2]["titolo"] == "Capacità giuridica."
    assert "dal momento della nascita" in articles[2]["contenuto"]


def test_an_art_1_without_a_boundary_does_not_restart():
    articles = _parse(["Art. 1. Uno", "testo", "Art. 2. Due", "testo", "Art. 1. Uno di nuovo", "testo"])
    assert _ids(articles) == ["Art. 1", "Art. 2"]
    assert "Art. 1. Uno di nuovo" in articles[1]["contenuto"]


def test_a_single_sequence_has_no_prefix():
    assert _ids(_parse(["LIBRO I", "Art. 1. Uno", "testo", "Art. 2. Due", "testo"])) == ["Art. 1", "Art. 2"]


# --- cause 4: every Latin suffix spelling, written canonically ---------------------

def test_suffix_spellings_normalise_to_one_form():
    assert canonical_suffix("novies") == canonical_suffix("nonies") == "nonies"
    assert canonical_suffix("quattuordecies") == "quaterdecies"
    assert normalize_number("2545 octodecies") == "2545-octiesdecies"
    assert normalize_number("452-terdecies") == "452-terdecies"            # not 452-ter
    assert normalize_number("640ter") == "640-ter"
    assert number_variants("21-nonies") == ["21-nonies", "21-novies"]


def test_every_spelling_follows_in_order_and_is_written_canonically():
    articles = _parse(["Art. 21-septies Nullità", "testo", "Art. 21-octies Annullabilità", "testo",
                       "Art. 21 novies Annullamento d'ufficio", "testo", "Art. 21-decies Riesame", "testo",
                       "Art. 2545-quinquiesdecies Controllo", "testo", "Art. 2545-sexdecies Gestione", "testo"])
    assert _ids(articles) == ["Art. 21-septies", "Art. 21-octies", "Art. 21-nonies", "Art. 21-decies",
                              "Art. 2545-quinquiesdecies", "Art. 2545-sexiesdecies"]


def test_two_word_suffixes_and_a_heading_without_a_space_after_art():
    lines = []
    for suffix in ("duodecies", "ter decies", "quater decies", "quinquies decies", "sexies decies", "septies decies",
                   "octies decies", "novies decies", "vicies", "vicies semel", "vicies bis"):
        lines += [f"Art. 67-{suffix}. Rubrica", "testo"]
    lines += ["Art.2463-bis. Società a responsabilità limitata semplificata.", "testo"]
    assert _ids(_parse(lines))[-4:] == ["Art. 67-vicies", "Art. 67-unvicies", "Art. 67-duovicies", "Art. 2463-bis"]
    assert _ids(_parse(lines))[1] == "Art. 67-terdecies"


# --- cause 3 and the names flatten writes -------------------------------------------

def _record(articolo, titolo="", testo="testo dell'articolo abbastanza lungo"):
    return {"Tipo": "Articolo", "Articolo": articolo, "Titolo Articolo": titolo, "Contenuto Articolo": testo}


def _section_names(records, document_name):
    payload = build_neo4j_graph_payload(records, document_name=document_name, document_hash="h")
    return [n["properties"]["name"] for n in payload["nodes"] if "DOCUMENT_SECTION" in n["labels"]]


def test_an_article_heading_never_gets_a_title_in_front_of_its_number():
    names = _section_names([_record("Articolo1.giurisdizionedeigiudiciordinari.", "Giurisdizione dei giudici ordinari")],
                           "xxx_Codice Civile - Procedura 2026")
    assert names == ["1"]


def test_a_low_chapter_number_of_a_non_legal_document_still_gets_its_prefix():
    names = _section_names([_record("1", "Disposizioni di carattere generale")], "Circolare interna 2024")
    assert names == ["disposizioni_carattere.1"]


def test_sequence_prefixes_and_canonical_suffixes_survive_flatten():
    names = _section_names([_record("prel-Articolo1"), _record("Articolo21-noviesannullamentod'ufficio"),
                            _record("att-Articolo111-bis"), _record("Articolo52termini")], "Codice")
    assert names == ["prel-1", "21-nonies", "att-111-bis", "52"]
