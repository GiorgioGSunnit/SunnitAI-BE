"""Normalizza la struttura Articolo → Comma → Sottocomma per l'analisi."""

from __future__ import annotations

from typing import Any


def normalizza_gerarchia_articoli(articoli: list[dict[str, Any]]) -> None:
    """Garantisce almeno un Comma per ogni Articolo e almeno un Sottocomma per ogni Comma.

    Se l'articolo non è suddivisibile in commi, viene creato un Comma sintetico con
    ``identificativo`` ``\"0\"`` e il testo dell'articolo in ``contenuto`` (in flatten:
    ``Contenuto Comma``).

    Se il comma non è suddivisibile in sottocommi, viene creato un Sottocomma sintetico con
    ``identificativo`` ``\"0\"`` e il testo del comma in ``contenuto`` (in flatten:
    ``Contenuto Sottocomma``).
    """
    for a in articoli:
        if not a.get("contenuto_parsato"):
            body = (a.get("contenuto") or "").strip()
            a["contenuto_parsato"] = [
                {
                    "identificativo": "0",
                    "contenuto": body,
                    "contenuto_parsato_2": [],
                    "page": a.get("page", ""),
                    "flag": a.get("flag", False),
                }
            ]
        for c in a["contenuto_parsato"]:
            if not c.get("contenuto_parsato_2"):
                comm_body = (c.get("contenuto") or "").strip()
                c["contenuto_parsato_2"] = [
                    {
                        "identificativo": "0",
                        "contenuto": comm_body,
                        "page": c.get("page", ""),
                        "flag": c.get("flag", False),
                    }
                ]


def is_synthetic_zero_id(ident: Any) -> bool:
    """True se l'identificativo è il nodo sintetico \"0\" (non suddivisibile)."""
    return str(ident).strip() == "0"


def content_ok_for_llm(
    contenuto: str, identificativo: Any, min_len: int
) -> bool:
    """I nodi sintetici \"0\" partecipano sempre (anche con testo corto/vuoto)."""
    if is_synthetic_zero_id(identificativo):
        return True
    return len((contenuto or "").strip()) >= min_len


def _str_clean(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def identificativo_composto(
    titolo_articolo: str, id_comma: str, id_sottocomma: str
) -> str:
    """Concatenazione con spazi (modello UI): Titolo Articolo + Comma + Sottocomma."""
    return " ".join(
        x for x in (_str_clean(titolo_articolo), _str_clean(id_comma), _str_clean(id_sottocomma)) if x
    )


def ensure_identificativo_fields_for_confronto(articoli: list[dict[str, Any]]) -> None:
    """Valorizza ``identificativo`` / ``titolo`` se mancano, usando alias da export flatten.

    In export si usano spesso ``Titolo Articolo``, ``Identificativo Comma``,
    ``Identificativo Sottocomma``; la pipeline di confronto si aspetta invece
    ``titolo`` e ``identificativo`` su ogni livello. Su ogni sottocomma imposta
    anche ``identificativo_composto`` (testo unico con spazi).
    """
    for a in articoli:
        if not isinstance(a, dict):
            continue
        titolo = _str_clean(a.get("titolo") or a.get("Titolo Articolo"))
        if titolo and not _str_clean(a.get("titolo")):
            a["titolo"] = titolo
        id_art = _str_clean(a.get("identificativo"))
        if not id_art:
            id_art = titolo or "0"
        a["identificativo"] = id_art

        commi = a.get("contenuto_parsato")
        if not isinstance(commi, list):
            continue
        for c in commi:
            if not isinstance(c, dict):
                continue
            id_c = _str_clean(c.get("identificativo"))
            if not id_c:
                id_c = _str_clean(c.get("Identificativo Comma"))
            if not id_c:
                id_c = "0"
            c["identificativo"] = id_c

            subs = c.get("contenuto_parsato_2")
            if not isinstance(subs, list):
                continue
            for sc in subs:
                if not isinstance(sc, dict):
                    continue
                id_sc = _str_clean(sc.get("identificativo"))
                if not id_sc:
                    id_sc = _str_clean(sc.get("Identificativo Sottocomma"))
                if not id_sc:
                    id_sc = "0"
                sc["identificativo"] = id_sc
                comp = identificativo_composto(titolo, id_c, id_sc)
                if comp:
                    sc["identificativo_composto"] = comp
