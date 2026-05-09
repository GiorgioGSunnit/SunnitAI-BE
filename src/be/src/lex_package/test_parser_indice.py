from lex_package.parsing_utils.parser_indice import _parse_toc_candidate


def test_parse_toc_candidate_numeric_with_filler() -> None:
    parsed = _parse_toc_candidate("1.2 Titolo capitolo .......... 17")
    assert parsed == {
        "identificativo": "1.2",
        "titolo": "Titolo capitolo",
        "pagina_destinazione": 17,
    }


def test_parse_toc_candidate_textual_with_filler() -> None:
    parsed = _parse_toc_candidate("DISPOSIZIONI PRELIMINARI .......... 3")
    assert parsed == {
        "identificativo": "",
        "titolo": "DISPOSIZIONI PRELIMINARI",
        "pagina_destinazione": 3,
    }


def test_parse_toc_candidate_textual_without_filler_accepts_known_keywords() -> None:
    parsed = _parse_toc_candidate("Capitolo 7 Requisiti operativi 42")
    assert parsed == {
        "identificativo": "",
        "titolo": "Capitolo 7 Requisiti operativi",
        "pagina_destinazione": 42,
    }


def test_parse_toc_candidate_textual_without_filler_rejects_generic_lines() -> None:
    assert _parse_toc_candidate("Questa riga non e un indice 42") is None
