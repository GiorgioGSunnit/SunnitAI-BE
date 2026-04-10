"""Arricchimento del payload grafo Neo4j: concetti, azioni, date, entità (LLM + regex)."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from openai import APITimeoutError, RateLimitError

from lex_package.llm.factory import build_chat_model
from lex_package.t.graph_enrichment import GraphEnrichmentPayload

logger = logging.getLogger("lex_package.graph_enrichment")

_MONTHS_IT = (
    "gennaio|febbraio|marzo|aprile|maggio|giugno|"
    "luglio|agosto|settembre|ottobre|novembre|dicembre"
)

# Pattern date comuni (IT / ISO / numeriche)
_DATE_REGEXES = (
    re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(
        rf"\b\d{{1,2}}\s+(?:{_MONTHS_IT})\s+\d{{2,4}}\b",
        re.IGNORECASE,
    ),
)


def _stable_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _trim(s: str, n: int) -> str:
    t = (s or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 3] + "..."


def extract_dates_regex_all(text: str) -> list[str]:
    """Estrae occorrenze di date dal testo (deduplicate, ordine di apparizione)."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for rx in _DATE_REGEXES:
        for m in rx.finditer(text):
            raw = m.group(0).strip()
            if raw and raw not in seen:
                seen.add(raw)
                out.append(raw)
    return out


def _node_id(prefix: str, key: str) -> str:
    return f"{prefix}::{_stable_hash(key)[:24]}"


def _invoke_graph_llm(
    document_name: str,
    document_hash: str,
    section_order: list[dict[str, Any]],
    full_text_sample: str,
) -> GraphEnrichmentPayload | None:
    """Una chiamata LLM strutturata; None se fallisce."""
    lines: list[str] = []
    for i, sec in enumerate(section_order):
        snippet = _trim(sec.get("plain_text") or "", 400)
        tipo = sec.get("tipo") or ""
        lines.append(f"[{i}] ({tipo}) {snippet}")

    system = SystemMessage(
        content=(
            "Sei un esperto di diritto italiano ed europeo. "
            "Analizza il documento e le sezioni numerate. "
            "Rispondi solo con JSON valido secondo lo schema. "
            "Concetti e azioni legali: frasi brevi in italiano, senza ripetizioni. "
            "Per le date: relation_kind published per pubblicazione/gazzetta; "
            "validity per entrata in vigore, decorrenza, scadenza, validità. "
            "Nelle sezioni, usa section_index corrispondente all'indice [n] fornito. "
            "Se una sezione non contiene entità, lascia liste vuote."
        )
    )
    human = HumanMessage(
        content=(
            f"Nome documento: {document_name}\n"
            f"Hash (contesto): {document_hash[:16]}...\n\n"
            f"Estratto testo (campione):\n{_trim(full_text_sample, 12000)}\n\n"
            f"Sezioni (indice | tipo | testo):\n"
            + "\n".join(lines)
        )
    )
    try:
        llm = build_chat_model(target="primary", temperature=0).with_structured_output(
            GraphEnrichmentPayload
        ).with_retry(
            retry_if_exception_type=(RateLimitError, APITimeoutError),
            stop_after_attempt=3,
            wait_exponential_jitter=True,
        )
        return llm.invoke([system, human])
    except Exception as e:
        logger.warning("Graph enrichment LLM failed: %s", e)
        return None


def _merge_enrichment_into_payload(
    payload: dict[str, Any],
    *,
    document_hash: str,
    doc_node_id: str,
    section_order: list[dict[str, Any]],
    enrichment: GraphEnrichmentPayload | None,
    regex_dates_per_section: dict[str, list[str]],
    full_document_text: str,
) -> None:
    """Aggiunge nodi e relazioni a payload (mutazione in-place)."""
    nodes: list[dict] = payload.setdefault("nodes", [])
    rels: list[dict] = payload.setdefault("relationships", [])
    seen: set[str] = {n.get("id") for n in nodes if n.get("id")}

    def add_node(node_id: str, labels: list[str], properties: dict[str, Any]) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        properties = {**properties, "id": node_id}
        nodes.append({"id": node_id, "labels": labels, "properties": properties})

    def add_rel(
        typ: str,
        source: str,
        target: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        r: dict[str, Any] = {"type": typ, "source": source, "target": target}
        if properties:
            r["properties"] = properties
        rels.append(r)

    # --- Concetti e azioni (documento) ---
    for c in (enrichment.legal_concepts if enrichment else [])[:5]:
        c = (c or "").strip()
        if not c:
            continue
        nid = _node_id("LEGAL_CONCEPT", f"{document_hash}:{c}")
        add_node(nid, ["LEGAL_CONCEPT"], {"name": c, "label": "Legal_Concept"})
        add_rel("HAS_LEGAL_CONCEPT", doc_node_id, nid)

    for a in (enrichment.legal_actions if enrichment else [])[:5]:
        a = (a or "").strip()
        if not a:
            continue
        nid = _node_id("LEGAL_ACTION", f"{document_hash}:{a}")
        add_node(nid, ["LEGAL_ACTION"], {"name": a, "label": "Legal_Action"})
        add_rel("HAS_LEGAL_ACTION", doc_node_id, nid)

    # --- Date documento (LLM) ---
    seen_doc_raw: set[str] = set()
    if enrichment:
        for d in enrichment.document_dates:
            raw = (d.raw_text or "").strip()
            if not raw:
                continue
            seen_doc_raw.add(raw)
            rk = d.relation_kind or "other"
            nid = _node_id("DATE", f"{document_hash}:doc:{raw}:{rk}")
            add_node(
                nid,
                ["DATE"],
                {
                    "name": raw,
                    "raw_text": raw,
                    "classification": rk,
                    "label": "Date",
                },
            )
            add_rel("HAS_DATE", doc_node_id, nid, {"relation_kind": rk})

    # Date documento da regex (complemento)
    for raw in extract_dates_regex_all(full_document_text):
        raw = (raw or "").strip()
        if not raw or raw in seen_doc_raw:
            continue
        nid = _node_id("DATE", f"{document_hash}:docregex:{raw}")
        add_node(
            nid,
            ["DATE"],
            {
                "name": raw,
                "raw_text": raw,
                "classification": "mentioned",
                "label": "Date",
            },
        )
        add_rel("HAS_DATE", doc_node_id, nid, {"relation_kind": "mentioned"})

    # --- Sezioni: entità LLM + date regex ---
    by_index: dict[int, dict] = {}
    if enrichment:
        for s in enrichment.sections:
            by_index[int(s.section_index)] = {
                "organizations": list(s.organizations or [])[:8],
                "persons": list(s.persons or [])[:8],
                "roles": list(s.roles or [])[:8],
                "locations": list(s.locations or [])[:8],
                "section_dates": list(s.section_dates or []),
            }

    for i, sec in enumerate(section_order):
        sid = sec.get("section_id") or ""
        if not sid:
            continue
        data = by_index.get(i, {})
        for org in data.get("organizations", []):
            o = (org or "").strip()
            if not o:
                continue
            nid = _node_id("ORGANIZATION", f"{document_hash}:{o}")
            add_node(nid, ["ORGANIZATION"], {"name": o, "label": "Organization"})
            add_rel("MENTIONS_ORGANIZATION", sid, nid)
        for p in data.get("persons", []):
            p = (p or "").strip()
            if not p:
                continue
            nid = _node_id("PERSON", f"{document_hash}:{p}")
            add_node(nid, ["PERSON"], {"name": p, "label": "Person"})
            add_rel("MENTIONS_PERSON", sid, nid)
        for r in data.get("roles", []):
            r = (r or "").strip()
            if not r:
                continue
            nid = _node_id("ROLE", f"{document_hash}:{r}")
            add_node(nid, ["ROLE"], {"name": r, "label": "Role"})
            add_rel("MENTIONS_ROLE", sid, nid)
        for loc in data.get("locations", []):
            loc = (loc or "").strip()
            if not loc:
                continue
            nid = _node_id("LOCATION", f"{document_hash}:{loc}")
            add_node(nid, ["LOCATION"], {"name": loc, "label": "Location"})
            add_rel("MENTIONS_LOCATION", sid, nid)
        llm_seen_raw: set[str] = set()
        for d in data.get("section_dates", []):
            raw = (d.raw_text or "").strip()
            if not raw:
                continue
            llm_seen_raw.add(raw)
            rk = d.relation_kind or "other"
            nid = _node_id("DATE", f"{document_hash}:sec:{sid}:{raw}:{rk}")
            add_node(
                nid,
                ["DATE"],
                {
                    "name": raw,
                    "raw_text": raw,
                    "classification": rk,
                    "label": "Date",
                },
            )
            add_rel("HAS_DATE", sid, nid, {"relation_kind": rk})

        # Regex: date aggiuntive non già estratte dal modello (classificazione mentioned)
        for raw in regex_dates_per_section.get(sid, []):
            raw = (raw or "").strip()
            if not raw or raw in llm_seen_raw:
                continue
            nid = _node_id("DATE", f"{document_hash}:regex:{sid}:{raw}")
            add_node(
                nid,
                ["DATE"],
                {
                    "name": raw,
                    "raw_text": raw,
                    "classification": "mentioned",
                    "label": "Date",
                },
            )
            add_rel("MENTIONS_DATE", sid, nid)


def enrich_neo4j_graph_payload(
    payload: dict[str, Any],
    *,
    document_name: str,
    document_hash: str,
    doc_node_id: str,
    section_order: list[dict[str, Any]],
    llm_enabled: bool = True,
) -> dict[str, Any]:
    """
    Arricchisce nodes/relationships con concetti, azioni, date, entità.

    Se ``llm_enabled`` è True e la chiamata LLM riesce, si usano concetti/azioni/date
    strutturate ed entità per sezione. In ogni caso si aggiungono date da regex per sezione.
    """
    full_text = "\n\n".join((s.get("plain_text") or "") for s in section_order)

    regex_dates_per_section: dict[str, list[str]] = {}
    for sec in section_order:
        sid = sec.get("section_id") or ""
        if not sid:
            continue
        regex_dates_per_section[sid] = extract_dates_regex_all(sec.get("plain_text") or "")

    enrichment: GraphEnrichmentPayload | None = None
    if llm_enabled and section_order:
        enrichment = _invoke_graph_llm(
            document_name, document_hash, section_order, full_text
        )

    _merge_enrichment_into_payload(
        payload,
        document_hash=document_hash,
        doc_node_id=doc_node_id,
        section_order=section_order,
        enrichment=enrichment,
        regex_dates_per_section=regex_dates_per_section,
        full_document_text=full_text,
    )

    meta = payload.setdefault("meta", {})
    meta["graph_enrichment_llm"] = enrichment is not None
    return payload

