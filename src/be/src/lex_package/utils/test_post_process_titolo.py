"""Unit tests for _clean_titolo_articolo (post_process.py, §7.1)."""
from lex_package.utils.post_process import _clean_titolo_articolo


def test_empty_and_none():
    assert _clean_titolo_articolo("") == ""
    assert _clean_titolo_articolo(None) == ""
    assert _clean_titolo_articolo("   ") == ""


def test_plain_title_passthrough():
    assert _clean_titolo_articolo("Definizioni") == "Definizioni"


def test_strips_single_footnote_marker():
    assert _clean_titolo_articolo("Appalti pubblici (1)") == "Appalti pubblici"


def test_strips_multiple_footnote_markers():
    assert _clean_titolo_articolo("Appalti pubblici (1) (6)") == "Appalti pubblici"


def test_strips_interspersed_footnote_markers():
    assert _clean_titolo_articolo("Reati (3) tributari (5)") == "Reati tributari"


def test_strips_trailing_period():
    assert _clean_titolo_articolo("Oggetto.") == "Oggetto"


def test_strips_trailing_period_after_footnote():
    assert _clean_titolo_articolo("Appalti pubblici (1).") == "Appalti pubblici"


def test_strips_analisi_prefix_with_single_quotes():
    assert (
        _clean_titolo_articolo("Analisi del comma dell'articolo 'Definizioni'")
        == "Definizioni"
    )


def test_strips_analisi_prefix_with_curly_quotes():
    assert (
        _clean_titolo_articolo("Analisi del comma dell’articolo ‘Oggetto e finalità’")
        == "Oggetto e finalità"
    )


def test_strips_analisi_prefix_without_quotes():
    assert (
        _clean_titolo_articolo("Analisi del comma dell'articolo Definizioni")
        == "Definizioni"
    )


def test_strips_analisi_prefix_case_insensitive():
    assert (
        _clean_titolo_articolo("ANALISI DEL COMMA DELL'ARTICOLO 'Definizioni'")
        == "Definizioni"
    )


def test_combines_prefix_and_footnote_and_period():
    assert (
        _clean_titolo_articolo("Analisi del comma dell'articolo 'Appalti pubblici (1) (6).'")
        == "Appalti pubblici"
    )


def test_collapses_double_spaces_left_by_stripped_markers():
    assert _clean_titolo_articolo("Reati  tributari") == "Reati tributari"
