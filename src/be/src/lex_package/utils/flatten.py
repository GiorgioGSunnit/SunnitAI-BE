from lex_package.utils.confronto_metadata import looks_like_document_metadata_quality
from lex_package.utils.utils import extract_integer, concat_nested
from lex_package.utils.utils_comparison import _first_non_empty, _section_contenuto_for_leaf
from lex_package.parsing_utils.parser_articolo import nojunkchars, noforbiddenchars
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from lex_package.utils.embeddings import embedding_dim, embedding_to_xlsx_string


def _embedding_flat_fields(embedding: list | None) -> dict[str, Any]:
    """Campi flatten per l'embedding del testo del livello (Articolo / Comma / Sottocomma).

    ``Vettore`` replica ``Embedding Raw`` (lista di float). In export Excel la colonna
    ``Vettore`` usa la stessa serializzazione testuale precedentemente in ``Embedding``.
    """
    raw: list[float] = []
    if embedding:
        for x in embedding:
            try:
                raw.append(float(x))
            except (TypeError, ValueError):
                continue
    return {
        "Embedding Raw": raw,
        "Vettore": list(raw),
        "Embedding": embedding_to_xlsx_string(raw),
        "Embedding Dim": embedding_dim(raw),
    }


def _plain_text_for_embedding(val: Any) -> str:
    """Testo da passare al modello di embedding (stringa o struttura annidata)."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    try:
        return str(concat_nested(val)).strip()
    except Exception:
        return str(val).strip()


def _embedding_flat_fields_or_embed(
    existing: list | None, content_for_embed: Any
) -> dict[str, Any]:
    """Usa l'embedding presente nell'analisi; se assente e ``embeddings_enabled``,
    calcola il vettore dal contenuto del livello (Articolo / Comma / Sottocomma).
    """
    base = _embedding_flat_fields(existing)
    if base.get("Embedding Dim", 0):
        return base
    try:
        from lex_package.utils.embeddings import embeddings_enabled, embed_text
    except ImportError:
        return base
    if not embeddings_enabled():
        return base
    t = _plain_text_for_embedding(content_for_embed)
    if not t:
        return base
    try:
        return _embedding_flat_fields(embed_text(t))
    except Exception:
        return base


def stable_hash(text: str) -> str:
    """Ritorna un digest esadecimale stabile (sha256) del testo canonicalizzato."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _coefficiente_sort_key(v: Any) -> float:
    try:
        if v is None or v == "":
            return float("-inf")
        return float(v)
    except (TypeError, ValueError):
        return float("-inf")


def _apply_ulteriori_per_group(
    rows: list[dict[str, Any]],
    indices_by_key: dict[tuple[Any, ...], list[int]],
) -> None:
    """Per gruppi con più righe, imposta ``Ulteriori`` = ``x`` tranne la riga col coefficiente più alto."""
    for indices in indices_by_key.values():
        if len(indices) <= 1:
            rows[indices[0]]["Ulteriori"] = ""
            continue
        scored = [
            (i, _coefficiente_sort_key(rows[i].get("Coefficiente"))) for i in indices
        ]
        best_val = max(c for _, c in scored)
        primary_i = min(i for i, c in scored if c == best_val)
        for i in indices:
            rows[i]["Ulteriori"] = "" if i == primary_i else "x"


def mark_ulteriori_rif_sottocomma(rows: list[dict[str, Any]]) -> None:
    """Righe ``Tipo`` = ``Rif. Sottocomma`` con lo stesso (Articolo, Comma, Sottocomma)."""
    by_key: dict[tuple[Any, ...], list[int]] = {}
    for i, row in enumerate(rows):
        if row.get("Tipo") != "Rif. Sottocomma":
            continue
        k = (
            row.get("Articolo", ""),
            row.get("Comma", ""),
            row.get("Sottocomma", ""),
        )
        by_key.setdefault(k, []).append(i)
    _apply_ulteriori_per_group(rows, by_key)


def mark_ulteriori_attuativo_seconda_meta(rows: list[dict[str, Any]]) -> None:
    """Più corrispondenze per la stessa unità foglia attuativa (chiave Articolo + identificativo foglia in Comma)."""
    by_key: dict[tuple[Any, ...], list[int]] = {}
    for i, row in enumerate(rows):
        k = (row.get("Articolo", ""), row.get("Comma", ""))
        by_key.setdefault(k, []).append(i)
    _apply_ulteriori_per_group(rows, by_key)


def _trim_words(text: str, max_words: int) -> str:
    words = (text or "").split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])


def _references_from_pagina(pagina: Any) -> str:
    """Riferimento pagina per il grafo (formato richiesto: ``Pg. <n>``)."""
    if pagina is None or pagina == "":
        return ""
    return f"Pg. {pagina}".strip()


def _extract_first_date(text: str) -> str:
    if not text:
        return ""
    patterns = [
        r"\b\d{2}/\d{2}/\d{4}\b",
        r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0)
    return ""


def _infer_legal_source(document_name: str) -> tuple[str, str]:
    n = (document_name or "").lower()
    if "gazzetta" in n or "celex" in n:
        return "Gazzetta ufficiale", "Fonte normativa ufficiale UE/IT."
    if "banca" in n:
        return "Banca d'Italia", "Pubblicazione/atto di vigilanza Banca d'Italia."
    if "boe" in n:
        return "Boletin Oficial del Estado", "Fonte normativa ufficiale spagnola."
    if "eba" in n:
        return "European Banking Authority", "Linea guida/regolamentazione EBA."
    if "uif" in n:
        return "UIF", "Indicazioni/atti Unita di Informazione Finanziaria."
    return "Fonte non classificata", "Fonte non determinata automaticamente."


def _vector_from_flat_record(r: dict) -> list[float]:
    """Estrae il vettore numerico dal flatten (Vettore / Embedding Raw), mai la stringa Embedding."""
    for key in ("Vettore", "Embedding Raw"):
        v = r.get(key)
        if isinstance(v, list) and len(v) > 0:
            out: list[float] = []
            for x in v:
                try:
                    out.append(float(x))
                except (TypeError, ValueError):
                    continue
            return out
    return []


def build_neo4j_graph_payload(
    flattened_records: list[dict],
    document_name: str,
    document_hash: str,
    *,
    file_name: str | None = None,
    pdf_path: str | None = None,
) -> dict:
    """
    Build a Neo4j-ready JSON payload from flattened analysis records.

    I nodi DOCUMENT_SECTION usano gli stessi campi del flatten: testo per livello
    (Contenuto / Contenuto Comma / Contenuto Sottocomma), vettore da ``Vettore`` o
    ``Embedding Raw``, dimensione da ``Embedding Dim``, ``references`` da ``Pagina``
    (formato ``Pg. <n>``). Se il vettore è assente ma gli embedding sono abilitati,
    si tenta ``embed_text`` sul ``plain_text`` della sezione.

    Metadati documento (opzionale): se ``pdf_path`` è fornito, viene effettuata una
    chiamata LLM per estrarre ``document_name`` ufficiale, ``file_name``,
    ``editor_enterprises`` e altri campi. I risultati vengono aggiunti al nodo
    LEGAL_DOC e come nodi EDITOR con relazioni AUTHORED_BY.

    Arricchimento (opzionale, vedi ``lex_package.utils.graph_enrichment``): nodi
    LEGAL_CONCEPT, LEGAL_ACTION, DATE, ORGANIZATION, PERSON, ROLE, LOCATION e relazioni
    collegate.

    Output schema:
    {
      "nodes": [{ "id": "...", "labels": [...], "properties": {...} }],
      "relationships": [{ "type": "...", "source": "...", "target": "...", "properties": {...}? }]
    }

    Args:
        flattened_records: Record appiattiti dall'analisi.
        document_name: Nome normalizzato del documento (usato come fallback per il titolo).
        document_hash: Hash SHA del documento.
        file_name: Nome del file originale sul filesystem (può differire dal titolo ufficiale).
        pdf_path: Percorso al PDF su disco; se fornito abilita l'estrazione LLM dei metadati.
    """
    records = flattened_records or []
    doc_node_id = f"LEGAL_DOC::{document_hash}"
    nodes: list[dict] = []
    relationships: list[dict] = []
    section_by_key: dict[tuple[str, str, str], str] = {}
    seen_node_ids: set[str] = set()
    section_abstracts: list[str] = []
    section_order: list[dict] = []
    embedding_fallback_count = 0

    def add_node(node_id: str, labels: list[str], properties: dict):
        if node_id in seen_node_ids:
            return
        seen_node_ids.add(node_id)
        nodes.append({"id": node_id, "labels": labels, "properties": properties})

    # ── Metadata extraction (LLM, optional) ──────────────────────────────────
    extracted_metadata = None
    if pdf_path:
        try:
            from lex_package.utils.metadata_extraction import extract_document_metadata

            extracted_metadata = extract_document_metadata(
                pdf_path=pdf_path,
                file_name=file_name or document_name,
                document_name=document_name,
            )
        except Exception as _meta_exc:
            logger.warning("Metadata extraction skipped: %s", _meta_exc)

    # Resolve display name: prefer LLM-extracted title, fall back to document_name
    official_document_name = (
        extracted_metadata.document_name
        if extracted_metadata and extracted_metadata.document_name
        else document_name
    )

    doc_date = _extract_first_date(document_name)
    source_name, source_desc = _infer_legal_source(document_name)

    add_node(
        doc_node_id,
        ["LEGAL_DOC"],
        {
            "id": doc_node_id,
            "name": official_document_name,
            "file_name": file_name or document_name,
            "description": "",
            "date_enacted": extracted_metadata.issue_date or doc_date if extracted_metadata else doc_date,
            "document_number": extracted_metadata.document_number if extracted_metadata else None,
            "status": "",
            "hash": document_hash,
        },
    )

    # ── EDITOR nodes + AUTHORED_BY relationships ──────────────────────────────
    if extracted_metadata and extracted_metadata.editor_enterprises:
        for enterprise in extracted_metadata.editor_enterprises:
            editor_id = f"EDITOR::{stable_hash(enterprise.name)[:20]}"
            add_node(
                editor_id,
                ["EDITOR", "ORGANIZATION"],
                {
                    "id": editor_id,
                    "name": enterprise.name,
                    "role": enterprise.role,
                },
            )
            relationships.append(
                {
                    "type": "AUTHORED_BY",
                    "source": doc_node_id,
                    "target": editor_id,
                    "properties": {"role": enterprise.role},
                }
            )

    source_id = f"LEGAL_SOURCE::{stable_hash(source_name)[:16]}"
    add_node(
        source_id,
        ["LEGAL_SOURCE"],
        {
            "id": source_id,
            "name": source_name,
            "date": doc_date,
            "description": source_desc,
        },
    )
    relationships.append({"type": "PUBLISHED", "source": doc_node_id, "target": source_id})

    for r in records:
        tipo = (r.get("Tipo") or "").strip()
        if tipo not in ("Articolo", "Comma", "Sottocomma"):
            continue

        art = str(r.get("Articolo", "")).strip()
        comma = str(r.get("Identificativo Comma", "")).strip()
        sottocomma = str(r.get("Identificativo Sottocomma", "")).strip()

        if tipo == "Articolo":
            section_name = str(r.get("Titolo Articolo", "")).strip() or art
            plain_text = str(
                r.get("Contenuto Articolo") or r.get("Contenuto", "")
            ).strip()
        elif tipo == "Comma":
            section_name = comma or "0"
            plain_text = str(r.get("Contenuto Comma", "")).strip()
        else:
            section_name = sottocomma or "0"
            plain_text = str(r.get("Contenuto Sottocomma", "")).strip()

        vec = _vector_from_flat_record(r)
        if not vec:
            try:
                from lex_package.utils.embeddings import embeddings_enabled, embed_text

                if embeddings_enabled() and plain_text:
                    vec = embed_text(plain_text)
                    if vec:
                        embedding_fallback_count += 1
            except Exception:
                pass
        embedding_dim = r.get("Embedding Dim")
        if embedding_dim is None and vec:
            embedding_dim = len(vec)
        requirement = str(r.get("Requirement", "")).strip()
        core_text = str(r.get("Core Text", "")).strip()
        abstract = requirement if requirement else _trim_words(plain_text, 200)
        if core_text:
            abstract = f"{abstract} Testo significativo: {core_text}".strip()
        abstract = _trim_words(abstract, 200)
        section_abstracts.append(abstract)

        section_hash = str(r.get("Hash", "")).strip() or stable_hash(plain_text)
        section_id = f"DOCUMENT_SECTION::{document_hash}::{section_hash[:20]}"
        section_key = (tipo, art, comma if tipo != "Articolo" else "")
        section_by_key[section_key] = section_id

        references = _references_from_pagina(r.get("Pagina"))
        add_node(
            section_id,
            ["DOCUMENT_SECTION"],
            {
                "id": section_id,
                "name": section_name,
                "type": tipo,
                "abstract": abstract,
                "plain_text": plain_text,
                "references": references,
                # Allineato al flatten JSON: stesso vettore di "Vettore" / "Embedding Raw"
                "vettore": vec,
                "embedding_dim": embedding_dim,
                # Compatibilità: stesso contenuto di ``vettore`` (nome storico per Neo4j)
                "embedding": vec,
            },
        )
        relationships.append({"type": "CONTAINS", "source": doc_node_id, "target": section_id})
        section_order.append(
            {
                "section_id": section_id,
                "plain_text": plain_text,
                "tipo": tipo,
            }
        )

    for (tipo, art, comma), sec_id in section_by_key.items():
        if tipo == "Comma":
            parent = section_by_key.get(("Articolo", art, ""))
            if parent:
                relationships.append({"type": "PART_OF", "source": sec_id, "target": parent})
        elif tipo == "Sottocomma":
            parent = section_by_key.get(("Comma", art, comma))
            if parent:
                relationships.append({"type": "PART_OF", "source": sec_id, "target": parent})

    # ── NEXT relationships — preserve reading order between sections ──────────
    for i in range(len(section_order) - 1):
        curr = section_order[i]["section_id"]
        nxt = section_order[i + 1]["section_id"]
        relationships.append({
            "type": "NEXT",
            "source": curr,
            "target": nxt,
            "properties": {"order": i},
        })

    doc_description = _trim_words(" ".join(section_abstracts), 500)
    for n in nodes:
        if n["id"] == doc_node_id:
            n["properties"]["description"] = doc_description
            break

    payload: dict[str, Any] = {
        "nodes": nodes,
        "relationships": relationships,
        "meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "document_name": official_document_name,
            "file_name": file_name or document_name,
            "document_hash": document_hash,
            "metadata_extraction_llm": extracted_metadata is not None,
            "document_number": extracted_metadata.document_number if extracted_metadata else None,
            "issue_date": extracted_metadata.issue_date if extracted_metadata else None,
            "editor_enterprises": (
                [
                    {"name": e.name, "role": e.role}
                    for e in extracted_metadata.editor_enterprises
                ]
                if extracted_metadata
                else []
            ),
            "flatten_vector_fields": ["Vettore", "Embedding Raw"],
            "document_section_text_fields": {
                "Articolo": ["Contenuto Articolo", "Contenuto"],
                "Comma": ["Contenuto Comma"],
                "Sottocomma": ["Contenuto Sottocomma"],
            },
            "document_section_references_field": "references",
            "embedding_fallback_sections_count": embedding_fallback_count,
            "embedding_vector_note": (
                "Il campo Vettore nel flatten replica Embedding Raw; spesso è vuoto se "
                "gli embedding non sono abilitati (features.json embeddings_enabled), "
                "se la deployment di embedding non è configurata, se embed_text fallisce "
                "in silenzio, oppure se l'analisi LLM non ha restituito embedding sul "
                "nodo articolo/comma. In build_neo4j_graph_payload si tenta un fallback "
                "con embed_text sul plain_text della sezione quando gli embedding sono abilitati."
            ),
        },
    }

    try:
        from lex_package.utils.graph_enrichment import enrich_neo4j_graph_payload

        enrich_neo4j_graph_payload(
            payload,
            document_name=document_name,
            document_hash=document_hash,
            doc_node_id=doc_node_id,
            section_order=section_order,
            llm_enabled=True,
        )
    except Exception as e:
        logger.warning("Graph enrichment skipped: %s", e)

    return payload


def flatten_analisi(out_analisi):
    # prende oggetto json di out analisi e lo piattifica
    res = []
    PaginaArticolo = 0
    PaginaComma = 0
    PaginaSottocomma = 0

    for x in out_analisi:
        if not str(x.get("identificativo", "")).isdigit():
            print("       Flatten_analisi a livello di Articolo")
            if x.get("hash", ""):
                HashCalcolato = x.get("hash", "")
            else:
                HashCalcolato = stable_hash(nojunkchars(x.get("contenuto", "")))
            PaginaArticolo = x.get("page", "")
            res.append(
                {
                    "Tipo": "Articolo",
                    "Pagina": PaginaArticolo,
                    "Codice Documento": x.get("codicedocumento", ""),
                    "Codice Articolo": x.get("codicearticolo", ""),
                    "Titolo Articolo": x.get("titolo", ""),
                    "Articolo": x.get("identificativo", ""),
                    "Contenuto": x.get("contenuto", ""),
                    "Hash": HashCalcolato,
                    **_embedding_flat_fields_or_embed(
                        x.get("embedding", []), x.get("contenuto", "")
                    ),
                }
            )

            for c in x["contenuto_parsato"]:
                print("       Flatten_analisi a livello di Comma")
                if c.get("hash", ""):
                    HashCalcolato = c.get("hash", "")
                else:
                    HashCalcolato = stable_hash(nojunkchars(c.get("contenuto", "")))
                PaginaComma = (
                    c.get("page", "") if (c.get("page", "")) else PaginaArticolo
                )
                _emb_comma = c.get("embedding", []) or (
                    (c.get("contenuto_parsato_2", [{}])[0] or {}).get("embedding", [])
                )
                res.append(
                    {
                        "Tipo": "Comma",
                        "Pagina": PaginaComma,
                        "Codice Documento": x.get("codicedocumento", ""),
                        "Codice Articolo": x.get("codicearticolo", ""),
                        "Titolo Articolo": x.get("titolo", ""),
                        "Articolo": x.get("identificativo", ""),
                        "Identificativo Comma": c.get("identificativo", ""),
                        "Contenuto Articolo": "",
                        "Contenuto Comma": c.get("contenuto", ""),
                        "Hash": HashCalcolato,
                        **_embedding_flat_fields_or_embed(_emb_comma, c.get("contenuto", "")),
                    }
                )
                for sc in c.get("contenuto_parsato_2", []):
                    print("       Flatten_analisi a livello di Sottocomma")
                    if sc.get("hash", ""):
                        HashCalcolato = sc.get("hash", "")
                    else:
                        HashCalcolato = stable_hash(
                            nojunkchars(sc.get("contenuto", ""))
                        )
                    PaginaSottocomma = (
                        sc.get("page", "") if (sc.get("page", "")) else PaginaComma
                    )
                    res.append(
                        {
                            "Tipo": "Sottocomma",
                            "Pagina": PaginaSottocomma,
                            "Codice Documento": x.get("codicedocumento", ""),
                            "Codice Articolo": x.get("codicearticolo", ""),
                            "Titolo Articolo": x.get("titolo", ""),
                            "Articolo": x.get("identificativo", ""),
                            "Identificativo Comma": c.get("identificativo", ""),
                            "Identificativo Sottocomma": sc.get("identificativo", ""),
                            "Contenuto Articolo": "",
                            "Contenuto Comma": "",
                            "Contenuto Sottocomma": sc.get("contenuto", ""),
                            "Requirement": sc.get("requirement", ""),
                            "Core Text": sc.get("core_text", ""),
                            "search_text": sc.get("search_text", ""),
                            "Pattern Type": sc.get("pattern_type", ""),
                            "Flag": sc.get("flag", False),
                            "Hash": HashCalcolato,
                            **_embedding_flat_fields_or_embed(
                                sc.get("embedding", []), sc.get("contenuto", "")
                            ),
                        }
                    )
                    for r in sc.get("riferimenti", []):
                        res.append(
                            {
                                "Tipo": "Riferimenti Sottocomma",
                                "Articolo": x.get("identificativo", ""),
                                "Titolo Articolo": x.get("titolo", ""),
                                "Pagina": x.get("page", ""),
                                "Contenuto Articolo": "",
                                "Identificativo Comma": c.get("identificativo", ""),
                                "Contenuto Comma": "",
                                "Identificativo Sottocomma": sc.get(
                                    "identificativo", ""
                                ),
                                "Contenuto Sottocomma": sc.get("contenuto", ""),
                                "Requirement": sc.get("requirement", ""),
                                "Core Text": sc.get("core_text", ""),
                                "search_text": sc.get("search_text", ""),
                                "Pattern Type": sc.get("pattern_type", ""),
                                "Riferimento Sottocomma - Articolo": r.get(
                                    "n_articolo", ""
                                ),
                                "Riferimento Sottocomma - Comma": r.get(
                                    "n_paragrafo", ""
                                ),
                                "Riferimento Sottocomma - Nome Documento": r.get(
                                    "nome_documento", ""
                                ),
                                "Riferimento Sottocomma - Codice Documento": r.get(
                                    "codice_documento", ""
                                ),
                            }
                        )

    return res


def flatten_analisi_invertito(out_analisi):
    # prende oggetto json di out analisi e lo piattifica
    res = []
    ContenutoSottocomma = ""
    ContenutoSottocommi = ""
    ContenutoComma = ""
    ContenutoCommi = ""
    ContenutoArticolo = ""
    UnaLegge = False
    print("####### flatten_analisi_invertito #######")
    for j,y in enumerate(out_analisi):
        if not (UnaLegge) and ("articolo" in str(y.get("identificativo", "")).lower()):
            # print("####### E' una LEGGE! #######")
            UnaLegge = True

    for x in out_analisi:
        if UnaLegge and (str(x.get("identificativo", "")).isdigit()):
            # print("#########  SALTO!  #########")
            continue
        else:
            ContenutoCommi = ""
            if x.get("identificativo", ""):
                IdentificativoArticolo = x.get("identificativo", "")
            else:
                IdentificativoArticolo = x.get("titolo", "")
            for c in x["contenuto_parsato"]:
                ContenutoSottocommi = ""
                for sc in c.get("contenuto_parsato_2", []):
                    #                    print("       Flatten_analisi_invertito a livello di Sottocomma  -->", sc.get("identificativo", ""), "<--")
                    if sc.get("contenuto", ""):
                        ContenutoSottocomma = noforbiddenchars(
                            concat_nested(sc.get("contenuto", ""))
                        )
                    else:
                        ContenutoSottocomma = ""

                    testo_pulito = nojunkchars(ContenutoSottocomma)

                    if sc.get("hash", ""):
                        sc_hash = sc.get("hash", "")
                    #                        print("################################### HASH (Sottocomma) già CALCOLATO!!!! ########################################à")
                    #                        print("################################### HASH (Sottocomma) già CALCOLATO!!!! ########################################à")
                    else:
                        sc_hash = stable_hash(testo_pulito)
                    #                        print("################################### HASH (Sottocomma) CALCOLATO ", str(len(testo_pulito)), "---> ", str(sc_hash), " Pagina ", x.get("page", ""), " Articolo ", x.get("titolo", ""), " Comma ", c.get("identificativo", ""), " ########################################à")

                    res.append(
                        {
                            "Tipo": "Sottocomma",
                            "Pagina": x.get("page", ""),
                            "Codice Documento": x.get("codicedocumento", ""),
                            "Codice Articolo": x.get("codicearticolo", ""),
                            "Titolo Articolo": x.get("titolo", ""),
                            "Articolo": IdentificativoArticolo,
                            "Identificativo Comma": c.get("identificativo", ""),
                            "Identificativo Sottocomma": sc.get(
                                "identificativo", ""
                            )
                            if sc.get("identificativo", "")
                            else "0",
                            "Contenuto Articolo": "",
                            "Contenuto Comma": "",
                            "Contenuto Sottocomma": ContenutoSottocomma,
                            "Requirement": sc.get("requirement", ""),
                            "Core Text": sc.get("core_text", ""),
                            "search_text": sc.get("search_text", ""),
                            "Pattern Type": sc.get("pattern_type", ""),
                            "Flag": sc.get("flag", False),
                            "Hash": sc_hash,
                            **_embedding_flat_fields_or_embed(
                                sc.get("embedding", []), ContenutoSottocomma
                            ),
                        }
                    )
                    for r in sc.get("riferimenti", []):
                        res.append(
                            {
                                "Tipo": "Riferimenti Sottocomma",
                                "Articolo": IdentificativoArticolo,
                                "Titolo Articolo": x.get("titolo", ""),
                                "Pagina": x.get("page", ""),
                                "Contenuto Articolo": "",
                                "Identificativo Comma": c.get("identificativo", ""),
                                "Contenuto Comma": "",
                                "Identificativo Sottocomma": sc.get(
                                    "identificativo", ""
                                ),
                                "Contenuto Sottocomma": ContenutoSottocomma,
                                "Requirement": sc.get("requirement", ""),
                                "Core Text": sc.get("core_text", ""),
                                "search_text": sc.get("search_text", ""),
                                "Pattern Type": sc.get("pattern_type", ""),
                                "Riferimento Sottocomma - Articolo": r.get(
                                    "n_articolo", ""
                                ),
                                "Riferimento Sottocomma - Comma": r.get(
                                    "n_paragrafo", ""
                                ),
                                "Riferimento Sottocomma - Nome Documento": r.get(
                                    "nome_documento", ""
                                ),
                                "Riferimento Sottocomma - Codice Documento": r.get(
                                    "codice_documento", ""
                                ),
                            }
                        )
                    ContenutoSottocommi += "\n" + ContenutoSottocomma

                #                print("       Flatten_analisi_invertito a livello di Comma  -->", c.get("identificativo", ""), "<--")

                if c.get("contenuto", ""):
                    ContenutoComma = noforbiddenchars(
                        concat_nested(c.get("contenuto", ""))
                    )
                else:
                    ContenutoComma = ContenutoSottocommi

                testo_pulito = nojunkchars(ContenutoComma)

                if c.get("hash", ""):
                    c_hash = c.get("hash", "")
                #                    print("################################### HASH (Comma) già CALCOLATO!!!! ########################################à")
                else:
                    c_hash = stable_hash(testo_pulito)
                #                    print("################################### HASH (Comma) CALCOLATO ", str(len(testo_pulito)), "---> ", str(c_hash), " Pagina ", x.get("page", ""), " Articolo ", x.get("titolo", ""), " Comma ", c.get("identificativo", ""), " ########################################à")

                _emb_c = c.get("embedding", []) or (
                    (c.get("contenuto_parsato_2", [{}])[0] or {}).get("embedding", [])
                )
                if c.get("titoloParte_articolo"):
                    res.append(
                        {
                            "Tipo": "Comma",
                            "Pagina": x.get("page", ""),
                            "Codice Documento": x.get("codicedocumento", ""),
                            "Codice Articolo": x.get("codicearticolo", ""),
                            "Titolo Articolo": x.get("titolo", ""),
                            "Parte": c.get("titoloParte_articolo", ""),
                            "Titolo": c.get("titoloTitolo_articolo", ""),
                            "Capitolo": c.get("titoloCapitolo_articolo", ""),
                            "Allegato": c.get("titoloAllegato_articolo", ""),
                            "Sezione": c.get("titoloSezione_articolo", ""),
                            "Articolo": IdentificativoArticolo,
                            "Identificativo Comma": c.get("identificativo", ""),
                            "Contenuto Articolo": "",
                            "Contenuto Comma": ContenutoComma,
                            #                            "TestoPulito": testo_pulito,
                            "Hash": c_hash,
                            **_embedding_flat_fields_or_embed(_emb_c, ContenutoComma),
                        }
                    )
                else:
                    res.append(
                        {
                            "Tipo": "Comma",
                            "Pagina": x.get("page", ""),
                            "Codice Documento": x.get("codicedocumento", ""),
                            "Codice Articolo": x.get("codicearticolo", ""),
                            "Titolo Articolo": x.get("titolo", ""),
                            "Articolo": IdentificativoArticolo,
                            "Identificativo Comma": c.get("identificativo", ""),
                            "Contenuto Articolo": "",
                            "Contenuto Comma": ContenutoComma,
                            #                            "TestoPulito": testo_pulito,
                            "Hash": c_hash,
                            **_embedding_flat_fields_or_embed(_emb_c, ContenutoComma),
                        }
                    )
                ContenutoCommi += "\n" + ContenutoComma
            #            print("       Flatten_analisi_invertito a livello di Articolo  -->", IdentificativoArticolo, "<--")

            if x.get("contenuto", ""):
                ContenutoArticolo = noforbiddenchars(
                    concat_nested(x.get("contenuto", ""))
                )
            else:
                ContenutoArticolo = ContenutoCommi

            testo_pulito = nojunkchars(ContenutoArticolo)

            if x.get("hash", ""):
                x_hash = x.get("hash", "")
            #                print("################################### HASH (Articolo) già CALCOLATO!!!! ########################################à")
            else:
                x_hash = stable_hash(testo_pulito)
            #                print("################################### HASH (Comma) CALCOLATO ", str(len(testo_pulito)), "---> ", str(x_hash), " Pagina ", x.get("page", ""), " Articolo ", x.get("titolo", ""), " ########################################à")
            ##                x_hash = hash(nojunkchars(concat_nested(ContenutoArticolo)))

            res.append(
                {
                    "Tipo": "Articolo",
                    "Pagina": x.get("page", ""),
                    "Codice Documento": x.get("codicedocumento", ""),
                    "Codice Articolo": x.get("codicearticolo", ""),
                    "Titolo Articolo": x.get("titolo", ""),
                    "Articolo": IdentificativoArticolo,
                    "Contenuto Articolo": ContenutoArticolo,
                    "Contenuto": ContenutoArticolo,
                    #                    "TestoPulito": testo_pulito,
                    "Hash": x_hash,
                    **_embedding_flat_fields_or_embed(
                        x.get("embedding", []), ContenutoArticolo
                    ),
                }
            )

    return res


def flatten_schema_attuativo(confronto, codicedocumento=""):
    res = []

    for x in confronto:
        res.append(
            {
                "Tipo": "Articolo",
                "Pagina": x.get("page", ""),
                "Articolo": x.get("identificativo", ""),
                "Titolo": x.get("titolo", ""),
                "Contenuto": x.get("contenuto", ""),
            }
        )

        for c in x["contenuto_parsato"]:
            res.append(
                {
                    "Tipo": "Comma",
                    "Pagina": x.get("page", ""),
                    "Articolo": x.get("identificativo", ""),
                    "Titolo": x.get("titolo", ""),
                    "Comma": c.get("identificativo", ""),
                    "Contenuto": c.get("contenuto", ""),
                }
            )
            for sc in c.get("contenuto_parsato_2", []):
                if len(sc.get("riferimenti", [])) == 0:
                    res.append(
                        {
                            "Tipo": "Sottocomma",
                            "Pagina": x.get("page", ""),
                            "Articolo": x.get("identificativo", ""),
                            "Titolo": x.get("titolo", ""),
                            "Comma": c.get("identificativo", ""),
                            "Sottocomma": sc.get("identificativo", ""),
                            "Contenuto": sc.get("contenuto", ""),
                            "Requirement": sc.get("requirement", ""),
                            "Core Text": sc.get("core_text", ""),
                            "search_text": sc.get("search_text", ""),
                            "Pattern Type": sc.get("pattern_type", ""),
                        }
                    )
                for r in sc.get("riferimenti", []):
                    match = r.get("matches", [])
                    if len(match) > 0:
                        match = match[
                            0
                        ]  # take the first match corresponding to r in the other document
                    else:
                        match = {}

                    not_found_message = "il match non é stato trovato: il riferimento non corrisponde a nessun articolo o comma del documento attuativo"
                    tipo_match = match.get("Tipo Match", "")
                    match_titolo_articolo = match.get("Match - Titolo Articolo", "")
                    match_identificativo_articolo = match.get(
                        "Match - Identificativo Articolo", ""
                    )

                    match_identificativo_comma = match.get(
                        "Match - Identificativo Comma", ""
                    )
                    match_contenuto = match.get("Match - Contenuto", "")
                    match_relazione_contenuto = match.get("relazione_contenuto", "")
                    match_motivazione = match.get(
                        "motivazione", sc.get("requirement", "")
                    )

                    res.append(
                        {
                            "Tipo": "Rif. Sottocomma",
                            "Pagina": x.get("page", ""),
                            "Articolo": x.get("identificativo", ""),
                            "Titolo": x.get("titolo", ""),
                            "Comma": c.get("identificativo", ""),
                            "Sottocomma": sc.get("identificativo", ""),
                            "Contenuto": sc.get("contenuto", ""),
                            "Requirement": sc.get("requirement", ""),
                            "Core Text": sc.get("core_text", ""),
                            "search_text": sc.get("search_text", ""),
                            "Pattern Type": sc.get("pattern_type", ""),
                            "Riferimento del Sottocomma - Nome Documento": r.get(
                                "nome_documento", ""
                            ),
                            "Riferimento del Sottocomma - Codice Documento": r.get(
                                "codice_documento", ""
                            ),
                            "Rif-Articolo": match_identificativo_articolo,
                            "Rif-Titolo": match_titolo_articolo,
                            "Rif-Comma": match_identificativo_comma,
                            "Rif-Contenuto": match_contenuto,
                            "Coefficiente": 30
                            if (r.get("codice_documento") == codicedocumento)
                            else 1,
                            "Ulteriori": "",
                            "Dettaglio": match_motivazione
                            if (r.get("codice_documento") == codicedocumento)
                            else "",
                        }
                    )
                    # Verifica della corrispondenza del codice_documento estratto dal comma con il codice del documento confrontato
    mark_ulteriori_rif_sottocomma(res)
    return res


def flatten_confronto_search(confronto):
    res = []

    for x in confronto:
        res.append(
            {
                "Tipo": "Articolo",
                "Pagina": x.get("page", ""),
                "Articolo": x.get("identificativo", ""),
                "Titolo": x.get("titolo", ""),
                "Contenuto": x.get("contenuto", ""),
            }
        )

        for c in x["contenuto_parsato"]:
            res.append(
                {
                    "Tipo": "Comma",
                    "Pagina": x.get("page", ""),
                    "Articolo": x.get("identificativo", ""),
                    "Titolo": x.get("titolo", ""),
                    "Comma": c.get("identificativo", ""),
                    "Contenuto": c.get("contenuto", ""),
                }
            )
            for sc in c.get("contenuto_parsato_2", []):
                if len(sc.get("riferimenti", [])) == 0:
                    res.append(
                        {
                            "Tipo": "Sottocomma",
                            "Pagina": x.get("page", ""),
                            "Articolo": x.get("identificativo", ""),
                            "Titolo": x.get("titolo", ""),
                            "Comma": c.get("identificativo", ""),
                            "Sottocomma": sc.get("identificativo", ""),
                            "Contenuto": sc.get("contenuto", ""),
                            "Requirement": sc.get("requirement", ""),
                            "Core Text": sc.get("core_text", ""),
                            "search_text": sc.get("search_text", ""),
                            "Pattern Type": sc.get("pattern_type", ""),
                        }
                    )
                for r in sc.get("confronti", []):
                    res.append(
                        {
                            "Tipo": "Rif. Sottocomma",
                            "Pagina": x.get("page", ""),
                            "Articolo": x.get("identificativo", ""),
                            "Titolo": x.get("titolo", ""),
                            "Comma": c.get("identificativo", ""),
                            "Sottocomma": sc.get("identificativo", ""),
                            "Contenuto": sc.get("contenuto", ""),
                            "Requirement": sc.get("requirement", ""),
                            "Core Text": sc.get("core_text", ""),
                            "search_text": sc.get("search_text", ""),
                            "Pattern Type": sc.get("pattern_type", ""),
                            "Riferimento del Sottocomma - Nome Documento": r.get(
                                "nome_documento", ""
                            ),
                            "Riferimento del Sottocomma - Codice Documento": r.get(
                                "codice_documento", ""
                            ),
                            "Rif-Articolo": "",
                            "Rif-Titolo": "",
                            "Rif-Comma": "",
                            "Rif-Contenuto": r.get("searchai_value", ""),
                            "Coefficiente": r.get("coefficiente", 0),
                            "Ulteriori": "",
                            "Dettaglio": r.get("confronto", ""),
                        }
                    )
    mark_ulteriori_rif_sottocomma(res)
    return res


def flatten_confronto_emendativo(confronto):
    """
    Ritorna una lista di dict “piatti” a partire da una sequenza
    di voci con struttura annidata.

    Parametri
    ----------
    confronto : iterable[dict]
        Le voci da trasformare.

    Ritorno
    -------
    list[dict]
        Elenco di record normalizzati.
    """
    res = []

    for c in confronto:
        tipo = c.get("Tipo", "")
        analisi = c.get("analisi_emendativa", {}) or {}  # sempre dict
        # print(len(c.get("analisi_emendativa", {})))
        if tipo == "Articolo":
            res.append(
                {
                    "Tipo": tipo,
                    "Pagina": c.get("Pagina", ""),
                    "Articolo": c.get("Articolo", ""),
                    "Titolo Articolo": c.get("Titolo Articolo", ""),
                    "Comma": "",
                    "Codice Documento": c.get("Codice Documento", ""),
                    "Contenuto": c.get("Contenuto", ""),
                    "Riferimenti": "",
                    "Codice Articolo": c.get("Codice Articolo", ""),
                    "similarita": c.get("similarita", ""),
                    "Motivazione": analisi.get("motivazione", ""),
                    "Riferimento Contenuto": c.get("riferimento_dettaglio", ""),
                    "Descrizione": "STRINGA VUOTA"
                    if c.get("dettaglio") == ""
                    else c.get("dettaglio"),
                }
            )

        #                    "Descrizione": analisi.get("dettaglio", {}).get("content", ""),

        elif tipo == "Comma":
            riferimenti = analisi.get("riferimenti_emendativa", {}) or {}
            res.append(
                {
                    "Tipo": tipo,
                    "Pagina": c.get("Pagina", ""),
                    "Articolo": c.get("Articolo", ""),
                    "Titolo Articolo": c.get("Titolo Articolo", ""),
                    "Comma": c.get("Identificativo Comma", ""),
                    "Codice Documento": c.get("Codice Documento", ""),
                    "Contenuto": c.get("Contenuto Comma", ""),
                    "Riferimenti": (
                        f"Articolo: {riferimenti.get('n_articolo', '')} "
                        f"Paragrafo: {riferimenti.get('n_paragrafo', '')}"
                    ).strip(),
                    "Codice Articolo": c.get("Codice Articolo", ""),
                    "similarita": c.get("similarita", ""),
                    "Motivazione": analisi.get("motivazione", ""),
                    "Riferimento Contenuto": c.get("riferimento_dettaglio", ""),
                    "Descrizione": analisi.get("dettaglio"),
                }
            )

        elif tipo == "Sottocomma":
            riferimenti = analisi.get("riferimenti_emendativa", {}) or {}
            res.append(
                {
                    "Tipo": tipo,
                    "Pagina": c.get("Pagina", ""),
                    "Articolo": c.get("Articolo", ""),
                    "Titolo Articolo": c.get("Titolo Articolo", ""),
                    "Comma": c.get("Identificativo Comma", ""),
                    "Sottocomma": c.get("Identificativo Sottocomma", ""),
                    "Codice Documento": c.get("Codice Documento", ""),
                    "Contenuto": c.get("Contenuto Sottocomma", ""),
                    "Riferimenti": (
                        f"Articolo: {riferimenti.get('n_articolo', '')} "
                        f"Paragrafo: {riferimenti.get('n_paragrafo', '')}"
                    ).strip(),
                    "Codice Articolo": c.get("Codice Articolo", ""),
                    "similarita": c.get("similarita", ""),
                    "Motivazione": analisi.get("motivazione", ""),
                    "Riferimento Contenuto": c.get("riferimento_dettaglio", ""),
                    "Descrizione": analisi.get("dettaglio"),
                }
            )

    #                    "Descrizione": analisi.get("dettaglio", {}).get("content", ""),

    return res


def flatten_confronto_versioning(confronto):
    res = []

    for c in confronto:
        tipo = c.get("Tipo", "")
        if (tipo == "Articolo") or (tipo == "Capitolo"):
            res.append(
                {
                    "Tipo": tipo,
                    "Pagina": c.get("Pagina", ""),
                    "Nome Capitolo": c.get("Articolo", ""),
                    "Parte": c.get("Parte", ""),  # c.get("titoloParte_articolo", ""),
                    "Titolo": c.get(
                        "Titolo", ""
                    ),  # c.get("titoloTitolo_articolo", ""),
                    "Capitolo": c.get(
                        "Capitolo", ""
                    ),  # c.get("titoloCapitolo_articolo", ""),
                    "Allegato": c.get(
                        "Allegato", ""
                    ),  # c.get("titoloAllegato_articolo", ""),
                    "Sezione": c.get(
                        "Sezione", ""
                    ),  # c.get("titoloSezione_articolo", ""),
                    "Paragrafo": "",
                    "Codice Documento": c.get("Codice Documento", ""),
                    "Contenuto": c.get("Contenuto", ""),
                    "Riferimenti": "",
                    "Codice Articolo": c.get("Codice Articolo", ""),
                    "similarita": c.get("similarita", ""),
                    "Motivazione": c.get("motivo", ""),
                    "Riferimento Contenuto": c.get("relazione_contenuto", ""),
                    "Riferimento Nome Capitolo": c.get("relazione_articolo", ""),
                    "Riferimento Parte": c.get("relazione_Parte", ""),
                    "Riferimento Titolo": c.get("relazione_Titolo", ""),
                    "Riferimento Capitolo": c.get("relazione_Capitolo", ""),
                    "Riferimento Allegato": c.get("relazione_Allegato", ""),
                    "Riferimento Sezione": c.get("relazione_Sezione", ""),
                    "Riferimento Paragrafo": c.get("relazione_comma", ""),
                    "Hash": c.get("Hash", ""),
                    "Riferimento Hash": c.get("relazione_hash", ""),
                    "Descrizione": c.get("Descrizione"),
                }
            )

        #                    "Descrizione": analisi.get("dettaglio", {}).get("content", ""),

        elif (tipo == "Comma") or (tipo == "Paragrafo"):
            res.append(
                {
                    "Tipo": tipo,
                    "Pagina": c.get("Pagina", ""),
                    "Nome Capitolo": c.get("Articolo", ""),
                    "Parte": c.get("Parte", ""),  # c.get("titoloParte_articolo", ""),
                    "Titolo": c.get(
                        "Titolo", ""
                    ),  # c.get("titoloTitolo_articolo", ""),
                    "Capitolo": c.get(
                        "Capitolo", ""
                    ),  # c.get("titoloCapitolo_articolo", ""),
                    "Allegato": c.get(
                        "Allegato", ""
                    ),  # c.get("titoloAllegato_articolo", ""),
                    "Sezione": c.get(
                        "Sezione", ""
                    ),  # c.get("titoloSezione_articolo", ""),
                    "Paragrafo": c.get("Identificativo Comma", ""),
                    "Codice Documento": c.get("Codice Documento", ""),
                    "Contenuto": c.get("Contenuto Comma", ""),
                    "Riferimenti": "",
                    "Codice Articolo": c.get("Codice Articolo", ""),
                    "similarita": c.get("similarita", ""),
                    "Motivazione": c.get("motivo", ""),
                    "Riferimento Contenuto": c.get("relazione_contenuto", ""),
                    "Riferimento Nome Capitolo": c.get("relazione_articolo", ""),
                    "Riferimento Parte": c.get("relazione_Parte", ""),
                    "Riferimento Titolo": c.get("relazione_Titolo", ""),
                    "Riferimento Capitolo": c.get("relazione_Capitolo", ""),
                    "Riferimento Allegato": c.get("relazione_Allegato", ""),
                    "Riferimento Sezione": c.get("relazione_Sezione", ""),
                    "Riferimento Paragrafo": c.get("relazione_comma", ""),
                    "Hash": c.get("Hash", ""),
                    "Riferimento Hash": c.get("relazione_hash", ""),
                    "Descrizione": c.get("Descrizione"),
                }
            )
    #                    "Descrizione": analisi.get("dettaglio", {}).get("content", ""),

    return res


def flat_confronto_attuativo_coefficienti(confronto):
    res = []

    for x in confronto:
        sim = x.get("similarita_attuativa_per_titolo", {})
        for s in sim:
            res.append(
                {
                    "Tipo": "Articolo",
                    "Articolo": x.get("identificativo", ""),
                    "Titolo Articolo": x.get("titolo", ""),
                    "Pagina": x.get("page", ""),
                    "Contenuto": x.get("contenuto", ""),
                    "similarita_attuativa_per_titolo__titolo_articolo_confrontato": s.get(
                        "titolo_articolo_confrontato", ""
                    ),
                    "similarita_attuativa_per_titolo__coefficiente_similarita": (
                        s.get("coefficiente_similarita", {}) or {}
                    ).get("coefficiente_correlazione", ""),
                }
            )

    return res


def flat_confronto_attuativo_seconda_meta(confronto):
    res = []

    for x in confronto:
        for b_match in x["best_matches"]:
            for coppia_commi in b_match["coppie_commi"]:
                rif_titolo = b_match.get("titolo_articolo", "")
                rif_comma = coppia_commi.get("identificativo_comma_attuare", "")
                rif_contenuto = coppia_commi.get("contenuto_comma_attuare", "")
                if looks_like_document_metadata_quality(
                    rif_titolo, rif_comma, rif_contenuto
                ):
                    continue
                res.append(
                    {
                        "Tipo": "Articolo",
                        "Pagina": x.get("page", ""),
                        "Articolo": x.get("identificativo", ""),
                        "Titolo": x.get("titolo", ""),
                        "Comma": coppia_commi.get("identificativo_comma_attuativo", ""),
                        "Rif-Articolo": b_match.get("identificativo_articolo", ""),
                        "Rif-Titolo": rif_titolo,
                        "Rif-Comma": rif_comma,
                        "Contenuto": coppia_commi.get("contenuto_comma_attuativo", ""),
                        "Rif-Contenuto": rif_contenuto,
                        "Coefficiente": coppia_commi["risultato_confronto"][
                            "coefficiente_correlazione"
                        ],
                        "Ulteriori": "",
                        "Embedding Cosine": coppia_commi.get("embedding_cosine", ""),
                        "Score Combinato": coppia_commi.get("score_combinato", ""),
                        "Dettaglio": coppia_commi["risultato_confronto"]["dettaglio"],
                    }
                )

    mark_ulteriori_attuativo_seconda_meta(res)
    return res


def flatterd_totheweb(confronto):
    res = []

    for x in confronto:
        Coefficiente = x.get("Coefficiente", "")
        Motivazione = ""
        if Coefficiente:
            if Coefficiente == 30:
                Motivazione = "Riferimento Letterale"
            elif Coefficiente >= 15:
                Motivazione = "Correlazione Forte"
            elif Coefficiente <= 1:
                Motivazione = "Riferimento Fuorviante"
            else:
                Motivazione = "Correlazione Debole"
        res.append(
            {
                "Tipo": x.get("Tipo", ""),
                "Pagina": x.get("Pagina", ""),
                "Articolo": x.get("Articolo", ""),
                "Titolo Articolo": x.get("Titolo", ""),
                "Comma": x.get("Comma", ""),
                "Sottocomma": "",
                "Contenuto": x.get("Contenuto", ""),
                "Riferimenti": x.get("Rif-Articolo", ""),
                "Codice Articolo": "",
                "similarita11": x.get("Coefficiente", ""),
                "Ulteriori": x.get("Ulteriori", ""),
                "Motivazione": Motivazione,
                "Riferimento Contenuto": x.get("Rif-Contenuto", ""),
                "Dettaglio": x.get("Dettaglio", ""),
            }
        )

    return res


def add_articoli_non_attuati(confronto: list[dict], articoli_attuare):
    X = []  # articoli da attuare che non sono elencati nel confronto tra i riferimenti

    for a_attuare in articoli_attuare:
        id_attuare = a_attuare.get("identificativo", "")
        a_attuare_compare_in_confronto = False
        for cfr in confronto:
            if extract_integer(cfr.get("Rif-Articolo", "")) == extract_integer(
                id_attuare
            ):
                a_attuare_compare_in_confronto = True
        if not a_attuare_compare_in_confronto:  # aggiungi articolo e commi
            rif_cont_art = _first_non_empty(
                a_attuare.get("contenuto"),
                a_attuare.get("Contenuto Articolo"),
            )
            if looks_like_document_metadata_quality(
                a_attuare.get("titolo", ""),
                rif_cont_art,
            ):
                continue

            _X = {
                "Tipo": "Articolo",
                "Rif-Articolo": id_attuare,
                "Rif-Titolo": a_attuare.get("titolo", ""),
                "Rif-Comma": "",
                "Rif-Contenuto": rif_cont_art,
                "Coefficiente": 0,
                "Ulteriori": "",
                "Descrizione": "Non recepito",
            }

            X.append(_X)

            for c in a_attuare.get("contenuto_parsato") or []:
                rif_cont_c = _section_contenuto_for_leaf(a_attuare, c, None)
                if looks_like_document_metadata_quality(
                    a_attuare.get("titolo", ""),
                    str(c.get("identificativo", "")),
                    rif_cont_c,
                ):
                    continue
                _c = {
                    "Tipo": "Comma",
                    "Rif-Articolo": id_attuare,
                    "Rif-Titolo": a_attuare.get("titolo", ""),
                    "Rif-Comma": c.get("identificativo", ""),
                    "Rif-Contenuto": rif_cont_c,
                    "Coefficiente": 0,
                    "Ulteriori": "",
                    "Descrizione": "Non recepito",
                }

                X.append(_c)

    print("Flatten - add_articoli_non_attuati - 🥔🥔🥔", len(X))

    return confronto + X
