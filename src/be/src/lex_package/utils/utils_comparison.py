from collections.abc import Mapping
from itertools import product

from lex_package.utils.confronto_metadata import unit_has_metadata_labels


def _first_non_empty(*vals) -> str:
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _section_contenuto_for_leaf(article: dict, comma: dict, sc: dict | None) -> str:
    """
    Testo della porzione coerente con l’export analisi (Tipo + Contenuto Articolo/Comma/Sottocomma).
    Usato per Rif-Contenuto / contenuto_comma_attuare quando ``contenuto`` è vuoto ma altri campi sono valorizzati.
    """
    if sc is not None:
        tipo = (sc.get("Tipo") or "").strip().lower()
        id_sc = str(sc.get("identificativo", "")).strip()
        if not tipo:
            tipo = "comma" if id_sc == "0" else "sottocomma"
        if tipo == "articolo":
            return _first_non_empty(
                article.get("contenuto"),
                article.get("Contenuto Articolo"),
            )
        if tipo == "comma":
            return _first_non_empty(
                comma.get("contenuto"),
                comma.get("Contenuto Comma"),
                sc.get("contenuto"),
                sc.get("Contenuto Comma"),
            )
        return _first_non_empty(
            sc.get("contenuto"),
            sc.get("Contenuto Sottocomma"),
            sc.get("requirement"),
        )

    subs = comma.get("contenuto_parsato_2", []) or []
    leaf0 = subs[0] if subs else {}
    tipo = (leaf0.get("Tipo") or "").strip().lower() if isinstance(leaf0, dict) else ""
    if not tipo:
        tipo = "comma"
    if tipo == "articolo":
        return _first_non_empty(
            article.get("contenuto"),
            article.get("Contenuto Articolo"),
        )
    if tipo == "sottocomma" and isinstance(leaf0, dict):
        return _first_non_empty(
            leaf0.get("contenuto"),
            leaf0.get("Contenuto Sottocomma"),
            leaf0.get("requirement"),
        )
    return _first_non_empty(
        comma.get("contenuto"),
        comma.get("Contenuto Comma"),
        leaf0.get("contenuto") if isinstance(leaf0, dict) else "",
        leaf0.get("Contenuto Comma") if isinstance(leaf0, dict) else "",
    )


def flatten_dict(d, parent_key="", sep="_"):
    """
    Restituisce un nuovo dict con tutti i campi annidati 'piattificati'.

    Parameters
    ----------
    d : Mapping
        Il dizionario di partenza (può contenere dizionari annidati a profondità arbitraria).
    parent_key : str, optional
        Prefisso da anteporre alle chiavi (usato internamente dalla ricorsione).
    sep : str, optional
        Separatore da usare tra i livelli (default: '_').

    Returns
    -------
    dict
        Dizionario piattificato.
    """
    items = {}
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, Mapping):
            items.update(flatten_dict(v, new_key, sep=sep))
        else:
            items[new_key] = v
    return items


def get_coordinates_of_ij(l: list[list[int]], i, j) -> list[list[int]]:
    res = []
    for x in l:
        if x[0] == i and x[1] == j:
            res.append(x)
    return res


def all_sottocommi_have_refs(c) -> bool:
    sc = c.get("contenuto_parsato_2", [])
    for _sc in sc:
        riferimenti_non_ereditati = [
            r for r in _sc.get("riferimenti", []) if not r.get("ereditato")
        ]
        if len(riferimenti_non_ereditati) == 0:
            print(
                "\n\n\n",
                "🥭🥭🥭 sottocomma doesn't have own refs, its refs are: ",
                _sc.get("riferimenti", []),
                "\n\n\n",
            )
            return False
    return True


def a_sottocomma_has_a_ref(c) -> bool:
    sc = c.get("contenuto_parsato_2", [])
    print(
        "searcing riferimenti in comma: ",
        c["identificativo"],
        "of articolo: ",
        c["titolo_articolo"],
        "🍌🍌🍌🍌",
    )
    for _sc in sc:
        print(
            "\n\n\n",
            "🥭🥭🥭 sottocomm refs, its refs are: ",
            _sc.get("riferimenti", []),
            "\n\n\n",
        )
        if len(_sc.get("riferimenti", [])) > 0:
            print("❤️it has riferimenti")
            return True
    return False


def get_best_matching_articles_attuativo(records: list[dict]) -> list[dict]:
    """
    da una lista di matches di un articolo
    (campo "similarita_attuativa_per_titolo") estrae i migliori matches
    """
    a_records = []
    b_records = []
    c_records = []

    for r in records:
        if r["coefficiente_correlazione"] > 15:
            a_records.append(r)
        if r["coefficiente_correlazione"] == 15:
            b_records.append(r)
        if r["coefficiente_correlazione"] < 15:
            c_records.append(r)
    if len(a_records) > 0:
        records = sorted(
            a_records, key=lambda d: d["coefficiente_correlazione"], reverse=True
        )
        return records[0:3]
    if len(b_records) > 0:
        records = sorted(
            b_records, key=lambda d: d["coefficiente_correlazione"], reverse=True
        )
        return b_records[0:3]
    if len(c_records) > 0:
        return c_records[0:1]

    return [{}]


def get_couples_commas_comparison(art_attuativo: dict, art_attuare: dict):
    """
    Get a list of dicts representing the couples of smallest comparable units
    (sottocomma if present, otherwise comma) for two articles.

    """

    def _leaf_units(article: dict) -> list[dict]:
        """
        Returns a list of units to compare for an article.

        Each returned unit is a dict with:
        - identificativo: a stable identifier (comma id, or comma.sottocomma id)
        - contenuto: the text content for the unit
        - articolo_titolo, comma_identificativo, sottocomma_identificativo, identificativo_composto:
          usati per escludere sezioni metadati (scheda documento, ecc.) dal punteggio.
        """
        articolo_titolo = article.get("titolo", "") or ""
        units: list[dict] = []
        for comma in article.get("contenuto_parsato", []) or []:
            sottocommi = comma.get("contenuto_parsato_2", []) or []
            comma_id = comma.get("identificativo", "")
            if sottocommi:
                for sc in sottocommi:
                    sc_id = sc.get("identificativo", "")
                    units.append(
                        {
                            "identificativo": f"{comma_id}.{sc_id}"
                            if comma_id and sc_id
                            else (sc_id or comma_id),
                            "contenuto": _section_contenuto_for_leaf(article, comma, sc),
                            # Optional semantic vector computed at analysis-time.
                            "embedding": sc.get("embedding", []) or [],
                            "articolo_titolo": articolo_titolo,
                            "comma_identificativo": comma_id,
                            "sottocomma_identificativo": sc_id,
                            "identificativo_composto": sc.get("identificativo_composto", "")
                            or "",
                        }
                    )
            else:
                # Some pipelines store the leaf-level enrichment (including embedding)
                # inside contenuto_parsato_2[0] when no explicit sottocommi exist.
                leaf0 = (comma.get("contenuto_parsato_2", [{}]) or [{}])[0] or {}
                units.append(
                    {
                        "identificativo": comma.get("identificativo", ""),
                        "contenuto": _section_contenuto_for_leaf(article, comma, None),
                        "embedding": comma.get("embedding", [])
                        or leaf0.get("embedding", [])
                        or [],
                        "articolo_titolo": articolo_titolo,
                        "comma_identificativo": comma_id,
                        "sottocomma_identificativo": "",
                        "identificativo_composto": leaf0.get("identificativo_composto", "")
                        or "",
                    }
                )
        return units

    # Esclude porzioni del documento interno (da attuare) che sono metadati/scheda documento.
    m = [
        t
        for t in product(_leaf_units(art_attuativo), _leaf_units(art_attuare))
        if not unit_has_metadata_labels(t[1])
    ]
    return [
        {
            "identificativo_comma_attuativo": t[0].get("identificativo", ""),
            "identificativo_comma_attuare": t[1].get("identificativo", ""),
            "contenuto_comma_attuativo": t[0].get("contenuto", ""),
            "contenuto_comma_attuare": t[1].get("contenuto", ""),
            "embedding_attuativo": t[0].get("embedding", []) or [],
            "embedding_attuare": t[1].get("embedding", []) or [],
            "metadata_quality_pair": unit_has_metadata_labels(t[0])
            or unit_has_metadata_labels(t[1]),
        }
        for t in m
    ]
