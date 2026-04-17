import sys
import os
from pathlib import Path
from dotenv import load_dotenv
from lex_package.parsing_utils.parser_articolo import nojunkchars, noforbiddenchars
import argparse
import json
import asyncio
import traceback
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv()

# Add the 'src' directory to sys.path
# This allows for absolute imports from 'lex_package' when running cli.py directly
_SRCDIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SRCDIR))

# Now import the modules after the path has been set up
from lex_package.analisi import analisi, consolida_analisi
from lex_package.parse import parse
from lex_package.arricchimento_parti import (
    arricchisci_parti,
    load_parts_from_parse_output,
    merge_enriched_into_parse_output,
)
from lex_package.utils.flatten import (
    flatten_analisi,
    flatten_analisi_invertito,
    flatten_schema_attuativo,
    flatten_confronto_emendativo,
    flatten_confronto_versioning,
    flat_confronto_attuativo_coefficienti,
    flat_confronto_attuativo_seconda_meta,
    add_articoli_non_attuati,
    flatterd_totheweb,
    flatten_confronto_search,
)
from lex_package.utils.to_xlsx import (
    write_records_to_xlsx,
    beautify_xlsx_confronto_attuativo,
)
from lex_package.schema_attuativo import confronto_attuativo
from lex_package.emendativa_confronto import confronto_emendativo
from lex_package.versioning_confronto import confronto_versioning
from lex_package.utils.integrazione_confronto_attuativo import (
    integrazione_confronto_attuativo_confronto_titoli,
    integrazione_confronto_attuativo_confronto_commi,
    select_best_matches,
)
from lex_package.confronto_search.search_confronto import confronto_searchai


def _check_azure_credentials():
    """Verifica che le credenziali Azure siano configurate (lazy check)."""
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION")
    if not api_key or not api_version:
        print(
            "Error: The AZURE_OPENAI_API_KEY or AZURE_OPENAI_API_VERSION environment variables not set."
        )
        print("Please set it before running the script.")
        sys.exit(1)


async def cli():
    parser = argparse.ArgumentParser(description="CLI a prova di protozoo 🧬")
    parser.add_argument(
        "--parse",
        type=str,
        help="modalita per solo parsing, inserisci il numero del file da analizzare: esempio per parsare documento 1, si scrive: python3 cli.py --parse 1",
    )
    parser.add_argument(
        "--template",
        type=str,
        default=None,
        help="forza il template di parsing (es. boe, banca, gazzetta_ue, regolamento). "
             "Se non specificato, il profiler seleziona automaticamente il template.",
    )
    parser.add_argument(
        "--enrich",
        type=str,
        default=None,
        help="Part B: arricchisce le parti di un documento già parsato con abstract, "
             "main_phrase, meaning e vector. Passare il nome del documento (es. '1'). "
             "Legge da out_parser/<nome>.json e aggiorna il file in-place.",
    )
    parser.add_argument(
        "-a",
        type=str,
        help="modalita a per analisi, A il numero  del file da analizzare, esempio per analizzare documento 1: python3 cli.py -a 1",
    )
    parser.add_argument(
        "-fa",
        type=str,
        help="piattifica output analisi gia' fatto creando json piatto e .xslx",
    )
    parser.add_argument(
        "-e",
        nargs=2,
        help="modalita b per confronto con regolamento emendativo, aggiungere i nomi dei due files, primo arg testo da emendare secondo arg testo emendativo",
    )
    parser.add_argument(
        "-fe",
        type=str,
        help="piattifica file json confronto emendativo gia' esistente e genera .xlsx",
    )

    parser.add_argument(
        "-sa",
        nargs=2,
        help="modalita c per confronto con schema attuativo, aggiungere i nomi dei due files, primo arg il nome del documento attuativo, secondo arg il nome del documento da attuare",
    )
    parser.add_argument(
        "-isa",
        nargs=2,
        help="prendi output confronto con schema attuativo non piattificato e output analisi documento da attuare, e produci confronto attuativo titoli. uso: -isa 8 7 , prima documento attuativo poi documento attuare",
    )

    parser.add_argument(
        "-fisac",
        nargs=1,
        help="prendi output confronto con schema attuativo con coefficienti non piattificato, e piattificalo e genera il xlsx ",
    )

    parser.add_argument(
        "-fsa",
        type=str,
        help="piattifica output confronto attuativo e scrivi json e xlsx",
    )
    parser.add_argument(
        "-v",
        nargs=2,
        help="Modalita per confronto di tipo Versioning, aggiungere i nomi dei due files",
    )
    parser.add_argument("-t", type=str, help="temp for testing a feature")
    parser.add_argument("-fl", type=str, help="temp for testing a feature")
    parser.add_argument(
        "-sas",
        nargs=2,
        help="fai confronto tra documento esterno da attuare (eg documento R) su documento interno (eg F)"
        + "usage: -sas [iniziali_doc_esterno] [nome_completo_doc_interno]"
        + "viene cercato il doc esterno tra gli out_analisi con regex, mentre serve il nome completo del doc interno per passarlo"
        + "alla ricerca di searchai"
        + "esempio: -sas F  Regolamento_del_credito_v7.0.pdf",
    )
    args = parser.parse_args()

    modes = {
        "parse": args.parse,
        "enrich": args.enrich,
        "a": args.a,
        "fa": args.fa,
        "e": args.e,
        "fe": args.fe,
        "sa": args.sa,
        "isa": args.isa,
        "fisac": args.fisac,
        "fsa": args.fsa,
        "t": args.t,
        "fl": args.fl,
        "v": args.v,
        "sas": args.sas,
    }
    template_hint: str | None = args.template

    # vedi quale é stato selezionato
    selected_mode_key = [k for k, v in modes.items() if v is not None]

    # Comandi che richiedono credenziali Azure (usano LLM)
    llm_commands = {"a", "e", "sa", "isa", "v", "sas", "t", "enrich"}
    if selected_mode_key and selected_mode_key[0] in llm_commands:
        _check_azure_credentials()

    match selected_mode_key[0]:
        case "parse":
            pdf_name = modes[selected_mode_key[0]]
            print(f"Parse mode selected for {pdf_name}")
            run_parse(pdf_name, template_hint=template_hint)

        case "enrich":
            doc_name = modes[selected_mode_key[0]]
            print(f"Part B enrichment for {doc_name}")
            await run_enrich(doc_name)

        case "a":
            pdf_name = modes[selected_mode_key[0]]
            print(f"Analisi mode selected for {pdf_name}")
            await run_anal(pdf_name)
            run_flatten_analisi(pdf_name)

        case "fa":
            json_name = modes[selected_mode_key[0]]
            print(f"Piattificazione analisi per {json_name}")
            run_flatten_analisi(json_name)

        case "e":
            ## deve essere fatto prima il testo emendativo e poi il testo da emendare
            json_names = modes[selected_mode_key[0]]
            print(f"Confronto emendativo tra {json_names[0]} e {json_names[1]}")
            await run_confronto_emendativo(json_names[0], json_names[1])
            run_flat_confronto_emendativo()

        case "fe":
            print(f"piattificazione confronto emendativo ")
            run_flat_confronto_emendativo()

        case "sa":
            json_names = modes[selected_mode_key[0]]
            await run_confronto_attuativo_prima_meta(json_names[0], json_names[1])

            print("🔎🔎🔎", "flatten confronto attuativo prima meta")
            flatten_confronto_attuativo_prima_meta()

            print("🔎🔎🔎", "flatten confronto attuativo seconda meta --- titoli")
            await run_integrazione_confronto_attuativo_titoli(
                json_names[0], json_names[1]
            )

            print("🔎🔎🔎", "flatten confronto attuativo seconda meta --- commi")
            await run_integrazione_confronto_attuativo_commi(json_names[1])
            run_flat_schema_attuativo_final(json_names[1])

        case "isa":
            json_names = modes[selected_mode_key[0]]
            await run_integrazione_confronto_attuativo_titoli(
                json_names[0], json_names[1]
            )

        case "t":
            await run_integrazione_confronto_attuativo_commi("7")
            run_flat_schema_attuativo_final("7")

        case "fl":
            flatten_confronto_attuativo_prima_meta()
            run_flat_schema_attuativo_final("7")

        case "v":
            json_names = modes[selected_mode_key[0]]
            print(f"Confronto Versioning tra {json_names[0]} e {json_names[1]}")
            await run_confronto_versioning(json_names[0], json_names[1])

        case "sas":
            names = modes[selected_mode_key[0]]
            await run_confronto_search(names[0], names[1])
            run_flatten_confronto_search()

        case _:
            print("Carattere non riconosciuto.")
            return

    # ritorna il dizionario con la chiave selezionata e i percorsi dei file
    return {
        "selected_mode_key": selected_mode_key[0],
        "paths": modes[selected_mode_key[0]],
    }


def run_parse(pdf_name, template_hint: str | None = None):
    data_dir = Path(_SRCDIR / "data")
    # regex per matchare "pdf_name."
    pattern = re.compile(rf"^{re.escape(pdf_name)}\.")

    # trova tutti i file che matchano il pattern
    matches = [p for p in data_dir.iterdir() if p.is_file() and pattern.match(p.name)]
    if not matches:
        raise FileNotFoundError(
            f"Nessun file in '{data_dir}' che inizi con '{pdf_name}.'"
        )

    # usa il primo match
    file_path = matches[0]
    pdf_path = str(file_path)

    output_file_dir = Path(_SRCDIR / "out_parser")
    output_file_dir.mkdir(parents=True, exist_ok=True)
    output_file_path = output_file_dir / f"{pdf_name}.json"

    res = parse(
        pdf_path,
        pdf_name,
        output_file_path_str=str(output_file_path),
        template_hint=template_hint,
    )

    return res


async def run_enrich(doc_name: str):
    """Part B: enrich parts of an already-parsed document.

    Reads out_parser/<doc_name>.json, runs the three-phase enrichment pipeline,
    and writes the enriched parts back into the same file in-place.
    """
    data_dir = Path(_SRCDIR / "out_parser")
    pattern = re.compile(rf"^{re.escape(doc_name)}\.")
    matches = [p for p in data_dir.iterdir() if p.is_file() and pattern.match(p.name)]
    if not matches:
        raise FileNotFoundError(
            f"Nessun file parsato in '{data_dir}' che inizi con '{doc_name}.'"
            " Esegui prima --parse."
        )

    json_path = str(matches[0])
    parts = load_parts_from_parse_output(json_path)
    print(f"[INFO] Loaded {len(parts)} parts from {json_path}")

    all_parts = await arricchisci_parti(parts, doc_name)
    merge_enriched_into_parse_output(json_path, all_parts)

    leaf_count = sum(1 for p in all_parts if p.get("level", "leaf") == "leaf")
    section_count = sum(1 for p in all_parts if p.get("level") == "section")
    print(f"[INFO] Enrichment done: {leaf_count} leaf, {section_count} section, 1 document node")
    print(f"[INFO] Results written to {json_path}")
    return all_parts


async def run_anal(pdf_name: str):
    data_dir = Path(_SRCDIR / "data")
    # regex per matchare "pdf_name."
    pattern = re.compile(rf"^{re.escape(pdf_name)}\.")

    # trova tutti i file che matchano il pattern
    matches = [p for p in data_dir.iterdir() if p.is_file() and pattern.match(p.name)]
    if not matches:
        raise FileNotFoundError(
            f"Nessun file in '{data_dir}' che inizi con '{pdf_name}.'"
        )

    # usa il primo match
    file_path = matches[0]
    pdf_path = str(file_path)

    # chiama analisi passando il nome reale del file
    res = await analisi(pdf_path, file_path.name)
    res = await consolida_analisi(res)
    # salva l'output
    out_dir = Path(_SRCDIR / "out_analisi")
    out_dir.mkdir(exist_ok=True)
    with open(out_dir / f"{pdf_name}.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    return res


def run_flatten_analisi(json_name):
    data_dir = Path(_SRCDIR / "out_analisi")
    # regex per matchare "pdf_name."
    pattern = re.compile(rf"^{re.escape(json_name)}\.")

    # trova tutti i file che matchano il pattern
    matches = [p for p in data_dir.iterdir() if p.is_file() and pattern.match(p.name)]
    if not matches:
        raise FileNotFoundError(
            f"Nessun file in '{data_dir}' che inizi con '{json_name}.'"
        )

    # usa il primo match
    file_path = matches[0]
    json_path = str(file_path)

    # flatten analysis
    with open(json_path, "r", encoding="utf-8") as file:
        a = json.load(file)
    flattened_data = flatten_analisi_invertito(a)
    # E' stato invertito l'ordine di preparazione della piattificazione in modo da eseguire la concatenazione dei contenuti e eseguire l'hash
    #    flattened_data = flatten_analisi(a)
    #########################################

    # Ensure the output directory exists
    out_flat_analisi_dir = Path(_SRCDIR / "out_flat/out_analisi")
    out_flat_analisi_dir.mkdir(parents=True, exist_ok=True)

    # Excel: niente liste lunghe; colonna "Vettore" = stesso testo serializzato di "Embedding".
    xlsx_records = []
    for rec in flattened_data:
        row = {k: v for k, v in rec.items() if k not in ("Embedding Raw", "Vettore", "Embedding")}
        row["Vettore"] = rec.get("Embedding") or ""
        xlsx_records.append(row)
    write_records_to_xlsx(xlsx_records, out_flat_analisi_dir / f"{json_name}.xlsx")

    with open(
        out_flat_analisi_dir / f"{json_name}.json", "w", encoding="utf-8"
    ) as file:
        json.dump(flattened_data, file, ensure_ascii=False, indent=2)

    return flattened_data


def run_flat_schema_attuativo_final(json_name_attuare):
    json_path = (
        f"{_SRCDIR}/out_schema_attuativo/confronti/coefficienti_correlazione_commi.json"
    )
    with open(json_path, "r", encoding="utf-8") as file:
        confronto_attuativo_commi = json.load(file)

    # clean output of commi comparison
    confronto_cleaned = select_best_matches(confronto_attuativo_commi)
    with open(
        _SRCDIR
        / "out_schema_attuativo/confronti/confronto_attuativo_seconda_meta.json",
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(confronto_cleaned, f, ensure_ascii=False, indent=2)

    ## flatten
    flat_2 = flat_confronto_attuativo_seconda_meta(confronto_cleaned)
    with open(
        _SRCDIR
        / "out_flat/out_schema_attuativo/confronto_attuativo_seconda_meta_flattened.json",
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(flat_2, f, ensure_ascii=False, indent=2)

    write_records_to_xlsx(
        flat_2,
        f"{_SRCDIR}/out_flat/out_schema_attuativo/confronto_attuativo_seconda_meta_flattened.xlsx",
    )

    # read the flat json of the first part of confronto  attuativo and concatenate the second part
    # to get the final output

    with open(
        f"{_SRCDIR}/out_flat/out_schema_attuativo/confronto_attuativo_prima_meta.json",
        "r",
        encoding="utf-8",
    ) as f:
        flat_1 = json.load(f)

    flat = flat_1 + flat_2

    # add articoli del documento da attuare che non sono stati considerati nel
    # documento attuativo

    data_dir = Path(_SRCDIR / "out_analisi")
    pattern = re.compile(rf"^{re.escape(json_name_attuare)}\.")

    # trova tutti i file che matchano il pattern
    matches = [p for p in data_dir.iterdir() if p.is_file() and pattern.match(p.name)]
    if not matches or not matches:
        raise FileNotFoundError(
            f"Nessun file in '{data_dir}' che inizi con  '{json_name_attuare}.'"
        )

    file_path = matches[0]
    with open(file_path, "r", encoding="utf-8") as f:
        analisi_articoli_attuare = json.load(f)

    # write the final result json
    with open(
        f"{_SRCDIR}/out_flat/out_schema_attuativo/confronto_attuativo_final_result.json",
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(flat, f, ensure_ascii=False, indent=2)

    # write the xlsx
    write_records_to_xlsx(
        flat,
        f"{_SRCDIR}/out_flat/out_schema_attuativo/confronto_attuativo_final_result.xlsx",
    )

    flat_ultimate = add_articoli_non_attuati(flat, analisi_articoli_attuare)
    with open(
        f"{_SRCDIR}/out_flat/out_schema_attuativo/confronto_attuativo_ultimate.json",
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(flat_ultimate, f, ensure_ascii=False, indent=2)

    # write the xlsx
    write_records_to_xlsx(
        flat_ultimate,
        f"{_SRCDIR}/out_flat/out_schema_attuativo/confronto_attuativo_ultimate.xlsx",
    )

    # Per adeguare l'output a quello desiderato dal Front-End
    flat_adjustes = flatterd_totheweb(flat_ultimate)
    with open(
        f"{_SRCDIR}/out_flat/out_schema_attuativo/confronto_attuativo_adjusted.json",
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(flat_adjustes, f, ensure_ascii=False, indent=2)

    # write the xlsx
    write_records_to_xlsx(
        flat_adjustes,
        f"{_SRCDIR}/out_flat/out_schema_attuativo/confronto_attuativo_adjusted.xlsx",
    )


async def run_integrazione_confronto_attuativo_titoli(
    json_name_attuativo, json_name_attuare
):
    # recupera out analisi di doc 7
    data_dir = Path(_SRCDIR / "out_analisi")
    pattern = re.compile(rf"^{re.escape(json_name_attuare)}\.")

    # trova tutti i file che matchano il pattern
    matches = [p for p in data_dir.iterdir() if p.is_file() and pattern.match(p.name)]
    if not matches or not matches:
        raise FileNotFoundError(
            f"Nessun file in '{data_dir}' che inizi con '{json_name_attuativo}.' o '{json_name_attuare}.'"
        )

    file_path = matches[0]
    with open(file_path, "r", encoding="utf-8") as f:
        analisi_articoli_attuare = json.load(f)

    # recupera out ultimo confronto attuativo
    src_dir = (
        _SRCDIR / "out_schema_attuativo/confronti/confronto_attuativo_prima_meta.json"
    )
    with open(src_dir, "r", encoding="utf-8") as f:
        confronti = json.load(f)
    confronti_con_correlazione_titoli = (
        await integrazione_confronto_attuativo_confronto_titoli(
            confronti, analisi_articoli_attuare
        )
    )
    with open(
        _SRCDIR
        / "out_schema_attuativo/confronti/coefficienti_correlazione_titoli.json",
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(confronti_con_correlazione_titoli, f, ensure_ascii=False, indent=2)


async def run_integrazione_confronto_attuativo_commi(json_name_attuare):
    data_dir = Path(_SRCDIR / "out_analisi")
    pattern = re.compile(rf"^{re.escape(json_name_attuare)}\.")

    # trova tutti i file che matchano il pattern
    matches = [p for p in data_dir.iterdir() if p.is_file() and pattern.match(p.name)]
    if not matches or not matches:
        raise FileNotFoundError(
            f"Nessun file in '{data_dir}' che inizi con  '{json_name_attuare}.'"
        )

    file_path = matches[0]
    with open(file_path, "r", encoding="utf-8") as f:
        analisi_articoli_attuare = json.load(f)

    with open(
        _SRCDIR
        / "out_schema_attuativo/confronti/coefficienti_correlazione_titoli.json",
        "r",
        encoding="utf-8",
    ) as f:
        confronti_con_correlazione_titoli = json.load(f)

    confronto_completo = await integrazione_confronto_attuativo_confronto_commi(
        confronti_con_correlazione_titoli, analisi_articoli_attuare
    )

    with open(
        _SRCDIR / "out_schema_attuativo/confronti/coefficienti_correlazione_commi.json",
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(confronto_completo, f, ensure_ascii=False, indent=2)


async def run_confronto_attuativo_prima_meta(json_name_attuativo, json_name_attuare):
    data_dir = Path(_SRCDIR / "out_analisi")
    # regex per matchare "pdf_name."
    pattern = re.compile(rf"^{re.escape(json_name_attuativo)}\.")
    pattern2 = re.compile(rf"^{re.escape(json_name_attuare)}\.")

    # trova tutti i file che matchano il pattern
    matches = [p for p in data_dir.iterdir() if p.is_file() and pattern.match(p.name)]
    matches2 = [p for p in data_dir.iterdir() if p.is_file() and pattern2.match(p.name)]
    if not matches or not matches2:
        raise FileNotFoundError(
            f"Nessun file in '{data_dir}' che inizi con '{json_name_attuativo}.' o '{json_name_attuare}.'"
        )

    # usa il primo match
    file_path = matches[0]
    file_path2 = matches2[0]

    with open(file_path, "r", encoding="utf-8") as f:
        articoli_attuativo = json.load(f)
        articoli_attuativo = noforbiddenchars(articoli_attuativo)
    with open(file_path2, "r", encoding="utf-8") as f:
        articoli_attuare = json.load(f)
        articoli_attuare = noforbiddenchars(articoli_attuare)

    # Define output directory for confronto_attuativo results --- src/out_schema_attuativo/confronti/confronto_attuativo.json
    confronto_output_dir = Path(_SRCDIR / "out_schema_attuativo/confronti")
    confronto_output_dir.mkdir(parents=True, exist_ok=True)
    confronto_result_path = confronto_output_dir / "confronto_attuativo_prima_meta.json"

    confronti = await confronto_attuativo(articoli_attuativo, articoli_attuare)

    with open(confronto_result_path, "w", encoding="utf-8") as f:
        json.dump(confronti, f, ensure_ascii=False, indent=2)

    return confronti


def flatten_confronto_attuativo_prima_meta():
    """
    scrivi:
    - piattificato della prima meta del confronto attuativo
    - xlsx prima meta confronto attuativo
      in src/out_flat/out_schema_attuativo/confronto_attuativo_prima_meta.*
    """

    # Define output directory for confronto_attuativo results
    confronto_output_dir = Path(_SRCDIR / "out_schema_attuativo/confronti")
    confronto_output_dir.mkdir(parents=True, exist_ok=True)
    confronto_result_path = confronto_output_dir / "confronto_attuativo_prima_meta.json"

    with open(confronto_result_path, "r", encoding="utf-8") as f:
        confronti = json.load(f)

    # Define the output directories for flattened data and xlsx
    confronto_flat_output_dir = Path(_SRCDIR / "out_flat/out_schema_attuativo")
    confronto_flat_output_dir.mkdir(parents=True, exist_ok=True)
    confronto_flat_result_path = (
        confronto_flat_output_dir / "confronto_attuativo_prima_meta.json"
    )

    ### flatten ###
    flattened_data = flatten_schema_attuativo(confronti, "2022/2555")
    with open(confronto_flat_result_path, "w", encoding="utf-8") as f:
        json.dump(flattened_data, f, ensure_ascii=False, indent=2)

    write_records_to_xlsx(
        flattened_data,
        confronto_flat_output_dir / "confronto_attuativo_prima_meta.xlsx",
    )

    return flattened_data


async def run_confronto_emendativo(json_name_emendare, json_name_emendativa):
    data_dir = Path(_SRCDIR / "out_flat/out_analisi")
    # regex per matchare "pdf_name."
    pattern = re.compile(rf"^{re.escape(json_name_emendare)}\.json$")
    pattern2 = re.compile(rf"^{re.escape(json_name_emendativa)}\.json$")
    # trova tutti i file che matchano il pattern
    matches = [p for p in data_dir.iterdir() if p.is_file() and pattern.match(p.name)]
    matches2 = [p for p in data_dir.iterdir() if p.is_file() and pattern2.match(p.name)]
    if not matches or not matches2:
        raise FileNotFoundError(
            f"Nessun file in '{data_dir}' che inizi con '{json_name_emendare}.' o '{json_name_emendativa}.'"
        )

    # usa il primo match
    file_path = matches[0]
    file_path2 = matches2[0]

    with open(_SRCDIR / file_path, "r", encoding="utf-8") as f:
        articoli_emendare = json.load(f)
    with open(_SRCDIR / file_path2, "r", encoding="utf-8") as f:
        articoli_emendativa = json.load(f)
    res = await confronto_emendativo(articoli_emendare, articoli_emendativa)

    with open(
        _SRCDIR / "out_confronto_emendativo/confronto_emendativo.json",
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    run_flat_confronto_emendativo()


async def run_confronto_versioning(json_name_1, json_name_2):
    data_dir = Path(_SRCDIR / "out_flat/out_analisi")
    # regex per matchare "pdf_name."
    pattern = re.compile(rf"^{re.escape(json_name_1)}\.json$")
    pattern2 = re.compile(rf"^{re.escape(json_name_2)}\.json$")
    # trova tutti i file che matchano il pattern
    matches = [p for p in data_dir.iterdir() if p.is_file() and pattern.match(p.name)]
    matches2 = [p for p in data_dir.iterdir() if p.is_file() and pattern2.match(p.name)]
    if not matches or not matches2:
        raise FileNotFoundError(
            f"Nessun file in '{data_dir}' che inizi con '{json_name_1}.' o '{json_name_2}.'"
        )

    # usa il primo match
    file_path = matches[0]
    file_path2 = matches2[0]

    with open(_SRCDIR / file_path, "r", encoding="utf-8") as f:
        articoli_1 = json.load(f)
    with open(_SRCDIR / file_path2, "r", encoding="utf-8") as f:
        articoli_2 = json.load(f)
    res = await confronto_versioning(articoli_1, articoli_2)

    out_dir = Path(_SRCDIR / "out_confronto_versioning")
    out_dir.mkdir(parents=True, exist_ok=True)
    dest_path = out_dir / "confronto_versioning.json"

    with open(
        dest_path,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    run_flat_confronto_versioning()  # DOVREBBERO ESSERE UGUALI


def run_flat_confronto_emendativo():
    with open(
        _SRCDIR / "out_confronto_emendativo/confronto_emendativo.json",
        "r",
        encoding="utf-8",
    ) as f:
        emendativa_confronto = json.load(f)

    res = flatten_confronto_emendativo(emendativa_confronto)

    out_dir = Path(_SRCDIR / "out_flat/out_confronto_emendativo")

    with open(
        out_dir / "confronto_emendativo_flattened.json", "w", encoding="utf-8"
    ) as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    write_records_to_xlsx(res, out_dir / "confronto_emendativo_flattened.xlsx")


def run_flat_confronto_versioning():  # DOVREBBERO ESSERE UGUALI
    try:
        out_dir = Path(_SRCDIR / "out_confronto_versioning")
        out_dir.mkdir(parents=True, exist_ok=True)
        dest_path = out_dir / "confronto_versioning.json"

        with open(
            dest_path,
            "r",
            encoding="utf-8",
        ) as f:
            versioning_confronto = json.load(f)

        res = flatten_confronto_versioning(versioning_confronto)

        out_dir = Path(_SRCDIR / "out_flat" / "out_confronto_versioning")
        out_dir.mkdir(parents=True, exist_ok=True)

        with open(
            out_dir / "confronto_versioning_flattened.json", "w", encoding="utf-8"
        ) as f:
            json.dump(res, f, ensure_ascii=False, indent=2)

        write_records_to_xlsx(res, out_dir / "confronto_versioning_flattened.xlsx")

    except json.JSONDecodeError as e:
        print(f"Invalid JSON format: {e}")
        print(
            f"Content that caused the error: {content[:500]}"
        )  # Stampa i primi 500 caratteri del contenuto problematico
    except FileNotFoundError as e:
        print(f"File not found: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        print(traceback.format_exc())


async def run_confronto_search(nome_doc_attuare, nome_doc_attuativo):
    """
    confronto con search ai
    - name_doc_attuare é il nome del documento da attuare (esterno)
    - name_doc_attuativo é il nome del doc attuativo (interno e indicizzato da searchai)
    """
    # recuperiamo il json dell'analisi del documento da attuare dall'out_analisi

    data_dir = Path(_SRCDIR / "out_analisi")
    pattern = re.compile(rf"^{re.escape(nome_doc_attuare)}\.")
    matches = [p for p in data_dir.iterdir() if p.is_file() and pattern.match(p.name)]
    if not matches:
        raise FileNotFoundError(
            f"Nessun file in '{data_dir}' che inizi con o '{nome_doc_attuare}.'"
        )
    file_path = matches[0]
    with open(file_path, "r", encoding="utf-8") as f:
        articoli_attuare = json.load(f)
        articoli_attuare = noforbiddenchars(articoli_attuare)

    # passiamo il json dell'out analisi e il nome del documento attuativo
    res = await confronto_searchai(articoli_attuare, nome_doc_attuativo)
    out_dir = Path(_SRCDIR / "out_confronto_search")
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "confronto.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)


def run_flatten_confronto_search():
    confronto_path = Path(_SRCDIR / "out_confronto_search/confronto.json")
    flattened_out_path = Path(_SRCDIR / "out_flat/out_confronto_search")
    flattened_out_path.mkdir(parents=True, exist_ok=True)

    with open(f"{confronto_path}", "r") as f:
        confronto = json.load(f)
    confronto_flattened = flatten_confronto_search(confronto)

    with open(f"{flattened_out_path}/confronto.json", "w", encoding="utf-8") as f:
        json.dump(confronto_flattened, f, ensure_ascii=False, indent=2)

    write_records_to_xlsx(confronto_flattened, flattened_out_path / "confronto.xlsx")
    print("✅ xlsx written in out_flat/out_confronto_search/confronto.xslx")


def main():
    asyncio.run(cli())


if __name__ == "__main__":
    main()
