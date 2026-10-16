"""Regression test: an empty titolo_articolo must always fall back to the
LLM title generator, even when the existing abstract happens to contain an
early " - " (ordinary prose, not a real heading). A prior version of
_step2b_fix_section_properties used a regex that matched that case and
skipped the LLM call without ever setting s.title, leaving it NULL."""
from lex_package.utils import post_process as pp


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows

    def single(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Answers every query _step2b_fix_section_properties issues; records
    every SET s.title call so the test can assert on it."""

    def __init__(self, row: dict):
        self._row = row
        self.title_writes: list[dict] = []

    def run(self, query, **params):
        q = " ".join(query.split())
        if "RETURN s.name AS name, s.plain_text AS plain_text" in q:
            return _FakeResult([])  # base_article_text_map: none needed
        if "RETURN elementId(s) AS eid, s.plain_text AS plain_text" in q:
            return _FakeResult([self._row])
        if "SET s.title = $title" in q:
            self.title_writes.append(params)
            return _FakeResult([])
        # Every other SET/MATCH in step2b (name fix, abstract fix, plain_text
        # cleanup, embedding clear) — nothing in this test depends on them.
        return _FakeResult([])


def test_empty_titolo_articolo_falls_back_to_llm_despite_dash_in_abstract(monkeypatch):
    monkeypatch.setattr(pp, "_generate_section_abstract", lambda *a, **k: "")
    monkeypatch.setattr(pp, "_generate_section_title", lambda *a, **k: "Omicidio")

    long_text = "x" * 250  # >= 200 chars: skips the short-abstract-prefix branch
    row = {
        "eid": "eid-1",
        "plain_text": long_text,
        "name": "575",
        "abstract": "Il reato - già disciplinato altrove - consiste nell'uccisione di una persona.",
        "titolo_articolo": "",
    }
    session = _FakeSession(row)

    result = pp._step2b_fix_section_properties(session, "doc-hash")

    assert result == "ok"
    assert session.title_writes == [{"eid": "eid-1", "title": "Omicidio"}]


def test_nonempty_titolo_articolo_sets_title_directly_without_llm(monkeypatch):
    def _fail(*a, **k):
        raise AssertionError("LLM title generator must not be called when titolo_articolo is set")

    monkeypatch.setattr(pp, "_generate_section_abstract", lambda *a, **k: "")
    monkeypatch.setattr(pp, "_generate_section_title", _fail)

    long_text = "x" * 250
    row = {
        "eid": "eid-2",
        "plain_text": long_text,
        "name": "3.0.0",
        "abstract": "Qualunque cosa.",
        "titolo_articolo": "Scioglimento del Matrimonio (1).",
    }
    session = _FakeSession(row)

    result = pp._step2b_fix_section_properties(session, "doc-hash")

    assert result == "ok"
    assert session.title_writes == [{"eid": "eid-2", "title": "Scioglimento del Matrimonio"}]
