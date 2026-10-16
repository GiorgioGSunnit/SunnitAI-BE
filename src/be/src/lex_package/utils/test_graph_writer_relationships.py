"""Unit tests for the labelled-match relationship writer (§7.2).

Uses a fake Neo4j session (no driver, no network) to check which Cypher
each relationship group produces, since the behaviour that matters here —
label vs. elementId matching — is about the query shape, not data.
"""
from lex_package.utils import graph_writer as gw


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows

    def single(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Records every query; answers elementId resolution and MERGE counts."""

    def __init__(self, doc_exists: bool, doc_elementid: str = "doc-eid"):
        self.queries: list[tuple[str, dict]] = []
        self.doc_exists = doc_exists
        self.doc_elementid = doc_elementid

    def run(self, query, **params):
        self.queries.append((" ".join(query.split()), params))
        q = self.queries[-1][0]
        if "RETURN n.id AS id, elementId(n) AS eid" in q:
            if self.doc_exists:
                return _FakeResult([{"id": params["ids"][0], "eid": self.doc_elementid}])
            return _FakeResult([])
        if "MERGE (a)-[r:" in q:
            return _FakeResult([{"written": len(params["batch"])}])
        raise AssertionError(f"unexpected query: {q}")


DOC_ID = "LEGAL_DOC::abc123"
SECTION_A = "DOCUMENT_SECTION::abc123::sec1"
SECTION_B = "DOCUMENT_SECTION::abc123::sec2"
SOURCE_ID = "LEGAL_SOURCE::xyz"
CONCEPT_ID = "LEGAL_CONCEPT::c1"
UNKNOWN_EDITOR_ID = "EDITOR::not-in-payload"

ID_TO_LABELS = {
    DOC_ID: ["LEGAL_DOC"],
    SECTION_A: ["DOCUMENT_SECTION"],
    SECTION_B: ["DOCUMENT_SECTION"],
    SOURCE_ID: ["LEGAL_SOURCE"],
    CONCEPT_ID: ["LEGAL_CONCEPT"],
}

RELATIONSHIPS = [
    {"type": "CONTAINS", "source": DOC_ID, "target": SECTION_A},
    {"type": "CONTAINS", "source": DOC_ID, "target": SECTION_B},
    {"type": "PART_OF", "source": SECTION_B, "target": SECTION_A},
    {"type": "PUBLISHED", "source": DOC_ID, "target": SOURCE_ID},
    {"type": "HAS_LEGAL_CONCEPT", "source": DOC_ID, "target": CONCEPT_ID},
    # Target not among this payload's own nodes — must fall back.
    {"type": "AUTHORED_BY", "source": DOC_ID, "target": UNKNOWN_EDITOR_ID},
]


def test_fresh_document_uses_labelled_match_for_everything_resolvable():
    """A brand-new LEGAL_DOC node (no prior relabel) has no elementId to
    resolve, so relationships touching it fall back to a labelled MATCH on
    LEGAL_DOC itself — which still has a real id constraint."""
    session = _FakeSession(doc_exists=False)
    written = gw._write_relationships_batched(session, RELATIONSHIPS, ID_TO_LABELS)

    assert written == len(RELATIONSHIPS)

    merge_queries = [q for q, _ in session.queries if "MERGE (a)-[r:" in q]
    assert any(":LEGAL_DOC {id: row.source_id}" in q for q in merge_queries)
    assert any(":DOCUMENT_SECTION {id: row.target_id}" in q for q in merge_queries)
    assert not any("elementId(a) = $a_eid" in q for q in merge_queries)

    # The AUTHORED_BY row (unknown target) must use the old unlabelled match.
    fallback = [q for q, _ in session.queries if "MATCH (a {id: row.source_id})" in q]
    assert len(fallback) == 1


def test_reingested_document_matches_by_elementid_not_label():
    """Once post_process has relabeled LEGAL_DOC -> Document, the payload
    still says LEGAL_DOC, but the node no longer carries that label. The
    writer must resolve its elementId and match by that, not by label."""
    session = _FakeSession(doc_exists=True, doc_elementid="doc-eid-999")
    written = gw._write_relationships_batched(session, RELATIONSHIPS, ID_TO_LABELS)

    assert written == len(RELATIONSHIPS)

    doc_sourced = [
        (q, p) for q, p in session.queries
        if "MERGE (a)-[r:" in q and "elementId(a) = $a_eid" in q
    ]
    assert doc_sourced, "relationships sourced from the Document node must match by elementId"
    for _, params in doc_sourced:
        assert params["a_eid"] == "doc-eid-999"
    assert not any(":LEGAL_DOC" in q for q, _ in doc_sourced)


def test_section_to_section_relationship_matches_by_label_on_both_sides():
    session = _FakeSession(doc_exists=False)
    gw._write_relationships_batched(session, RELATIONSHIPS, ID_TO_LABELS)

    part_of = [q for q, _ in session.queries if "MERGE (a)-[r:PART_OF]" in q]
    assert len(part_of) == 1
    assert ":DOCUMENT_SECTION {id: row.source_id}" in part_of[0]
    assert ":DOCUMENT_SECTION {id: row.target_id}" in part_of[0]


def test_unresolvable_endpoint_skips_eid_resolution_query_when_no_doc_ids():
    """No LEGAL_DOC id in id_to_labels at all -> no elementId lookup query."""
    session = _FakeSession(doc_exists=False)
    only_section_rel = [{"type": "PART_OF", "source": SECTION_B, "target": SECTION_A}]
    labels = {SECTION_A: ["DOCUMENT_SECTION"], SECTION_B: ["DOCUMENT_SECTION"]}

    written = gw._write_relationships_batched(session, only_section_rel, labels)

    assert written == 1
    assert not any("elementId(n) AS eid" in q for q, _ in session.queries)
