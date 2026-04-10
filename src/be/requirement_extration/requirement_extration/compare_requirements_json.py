import os
import sys
import json
import argparse
import re
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Dict

import fitz
import numpy as np
import tiktoken
from rich.logging import RichHandler
# from sentence_transformers import SentenceTransformer  # Removed - using lex_package
from openai import AzureOpenAI

from call_fast_api import upload_to_blob

# logging.basicConfig(
#     level=logging.INFO, format="%(message)s", datefmt="[%X]", handlers=[RichHandler()]
# )
logger = logging.getLogger(__name__)

# Ridurre la verbosità dei log HTTP
# logging.getLogger("httpx").setLevel(logging.WARNING)
# logging.getLogger("urllib3").setLevel(logging.WARNING)

AMENDING_MODE = os.getenv("AMENDING_MODE", "false").lower() == "true"
AMEND_HEADER_RE = re.compile(
    # r"REGOLAMENTO\s*\(UE\)\s*N\.\s*\d+/\d+", re.IGNORECASE
    r"REGOLAMENTO\s*\(UE\)\s*N\.\s*",
    re.IGNORECASE,
)

# Carica i prompt (il file "prompt_message.txt" contiene le istruzioni operative)
try:
    with open("prompt_message.txt", "r", encoding="utf-8") as f:
        prompt_message = f.read()
    with open("system_prompt.txt", "r", encoding="utf-8") as f:
        system_message = f.read()
except Exception as e:
    logger.error(f"Errore nel caricamento dei file di prompt: {e}")
    sys.exit(1)

# Parametri globali per il modello LLM
setup_model = "gpt-4"
setup_model_gpt4o = "gpt-4.1"
setup_temperature = 0.1
setup_top_k = 0.5
setup_top_p = 0.5


@dataclass
class AnalysisChunk:
    """
    Dataclass per rappresentare un chunk di confronto
    """

    old_sections: List[str]
    new_sections: List[str]
    similarity_score: float
    importance_score: float


def estimate_tokens(text: str) -> int:
    """
    Funzione per stimare il numero di token di un testo usando tiktoken
    """
    enc = tiktoken.encoding_for_model("gpt-4o")
    return len(enc.encode(text))


def calculate_importance_score(old_text: str, new_text: str) -> float:
    """
    Funzione per calcolare uno score di importanza tra due testi
    """
    critical_keywords = [
        "obbligo",
        "sanzione",
        "requisito",
        "divieto",
        "necessario",
        "deve",
        "vietato",
        "conformità",
        "regolamento",
        "legge",
        "termine",
        "scadenza",
        "multa",
        "ammenda",
        "prescrizione",
    ]
    old_lower = old_text.lower()
    new_lower = new_text.lower()
    keyword_score = sum(
        (
            2
            if (k in new_lower and k not in old_lower)
            else 1 if (k in new_lower or k in old_lower) else 0
        )
        for k in critical_keywords
    ) / len(critical_keywords)
    len_diff = abs(len(new_text) - len(old_text)) / max(len(new_text), len(old_text))
    return keyword_score * 0.7 + len_diff * 0.3


def create_optimal_chunks(
    comparison_pairs: List[Tuple[str, str]],
    embedding_model,  # SentenceTransformer removed
    max_tokens: int = 20000,
) -> List[AnalysisChunk]:
    """
    Funzione per creare chunk ottimali a partire da coppie di requisiti
    """
    chunks = []
    current_chunk = AnalysisChunk([], [], 0.0, 0.0)
    current_tokens = 0

    for old_text, new_text in comparison_pairs:
        # Formatto la coppia per il conteggio dei token
        pair_text = f"REQUISITO DOCUMENTO 1:\n{old_text}\n\nREQUISITO DOCUMENTO 2:\n{new_text}\n\n"
        pair_tokens = estimate_tokens(pair_text)

        if pair_tokens > max_tokens:
            # Se la coppia è troppo lunga, dividiamo il nuovo testo in sottocontenuti
            split_points = [
                i
                for i, char in enumerate(new_text)
                if char == "\n" and i > 0 and new_text[i - 1] == "."
            ]
            if not split_points:
                split_points = [len(new_text) // 2]
            best_split = min(
                split_points,
                key=lambda x: abs(estimate_tokens(new_text[:x]) - max_tokens / 2),
            )
            for text_chunk in [new_text[:best_split], new_text[best_split:]]:
                chunk_tokens = estimate_tokens(text_chunk)
                if current_tokens + chunk_tokens > max_tokens:
                    chunks.append(current_chunk)
                    current_chunk = AnalysisChunk([], [], 0.0, 0.0)
                    current_tokens = 0
                current_chunk.old_sections.append(old_text)
                current_chunk.new_sections.append(text_chunk)
                current_tokens += chunk_tokens
        else:
            if current_tokens + pair_tokens > max_tokens:
                chunks.append(current_chunk)
                current_chunk = AnalysisChunk([], [], 0.0, 0.0)
                current_tokens = 0

            emb_old = embedding_model.encode(old_text)
            emb_new = embedding_model.encode(new_text)
            similarity_score = np.dot(emb_old, emb_new)
            importance_score = calculate_importance_score(old_text, new_text)
            current_chunk.old_sections.append(old_text)
            current_chunk.new_sections.append(new_text)
            current_chunk.similarity_score = max(
                current_chunk.similarity_score, similarity_score
            )
            current_chunk.importance_score = max(
                current_chunk.importance_score, importance_score
            )
            current_tokens += pair_tokens

    if current_chunk.old_sections:
        chunks.append(current_chunk)
    return chunks


def load_requirements(json_file: str) -> List[str]:
    """Carica il file JSON e restituisce la lista dei requisiti (dal campo 'requirement')."""
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    reqs = [req.get("requirement", "") for req in data.get("requirements", [])]
    return reqs


def detect_normative_name(json1_path: str) -> str:
    logger.info("[DETECT NORMATIVE]")
    with open(json1_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("source_file") or ""
    name = Path(raw).name

    candidates = [
        Path(raw),  # esattamente ciò che c'è nel JSON
        Path(json1_path).parent / name,  # accanto al JSON
        Path("/") / raw.lstrip("/"),  # percorso assoluto
        Path("/tmp") / name,  # sempre in /tmp
    ]
    for pdf_file in candidates:
        if pdf_file.exists():
            logger.info(f"Trovato PDF bozza in: {pdf_file}")
            break
        else:
            logger.warning(
                f"PDF bozza non trovato in nessuna delle posizioni {candidates}, salto modalità emendativa"
            )
            raise RuntimeError("SKIP_AMENDING")

    full_text = ""
    with fitz.open(str(pdf_file)) as doc:
        for page in doc:
            full_text += page.get_text("text") + "\n"

    m = AMEND_HEADER_RE.search(full_text)
    if not m:
        raise RuntimeError("Intestazione regolamento non trovata nel PDF bozza")
    # m.group(1) = "648/2012", m.group(0) = "REGOLAMENTO (UE) N. 648/2012"
    return m.group(0)


def load_pdf_to_json_mapping(
    mapping_file_path: str = "pdf_mapping.json",
) -> Dict[str, str]:
    try:
        with open(mapping_file_path, "r", encoding="utf-8") as f:
            mapping_data = json.load(f)
        # The mapping is stored as { "pdf_base_name": "hash.json" }
        # We need to be able to find the pdf_base_name given the hash.json
        # So, we might need to invert it or search through it.
        # For now, let's assume it might also contain a reverse mapping or we adapt.
        # Actually, the main mapping is pdf_name -> hash.json. We need hash.json -> pdf_name

        # Let's create a reverse mapping for easier lookup
        # The loaded mapping_data is expected to be the 'mapping' dictionary part of PDFToJsonMapping
        reverse_mapping = {v: k for k, v in mapping_data.get("mapping", {}).items()}
        logger.info(f"Loaded and reversed PDF mapping: {len(reverse_mapping)} entries.")
        return reverse_mapping
    except FileNotFoundError:
        logger.warning(
            f"PDF mapping file not found at {mapping_file_path}. Cannot map hashes to original names."
        )
        return {}
    except json.JSONDecodeError:
        logger.error(f"Error decoding PDF mapping file at {mapping_file_path}.")
        return {}
    except Exception as e:
        logger.error(f"Error loading PDF mapping: {e}")
        return {}


def get_original_pdf_name(
    hashed_json_filename: str, reverse_mapping: Dict[str, str]
) -> str:
    original_name = reverse_mapping.get(hashed_json_filename)
    if original_name:
        return original_name  # This is already without .pdf
    return hashed_json_filename  # Fallback to the hash name if not found


def compare_requirements(
    json1: str, json2: str, prompt_template: str, output_file: str = None
) -> str:
    """Esegue la comparazione tra due set di requisiti, raggruppandoli in chunk e inviando l'analisi all'LLM."""
    logger.info(f"[COMPARE REQUIREMENTS!]")
    # Load PDF name mapping
    pdf_hash_to_name_map = {}
    try:
        with open("pdf_mapping.json", "r", encoding="utf-8") as f_map:
            full_mapping_data = json.load(f_map)
            pdf_hash_to_name_map = {
                v: k for k, v in full_mapping_data.get("mapping", {}).items()
            }
        logger.info(
            f"Successfully loaded and reversed pdf_mapping.json for display names."
        )
    except Exception as e:
        logger.warning(
            f"Could not load or parse pdf_mapping.json: {e}. Hashed names will be used in report."
        )

    base_json1_name = os.path.basename(json1)
    base_json2_name = os.path.basename(json2)

    original_name1 = pdf_hash_to_name_map.get(
        base_json1_name, base_json1_name.replace(".json", "")
    )
    original_name2 = pdf_hash_to_name_map.get(
        base_json2_name, base_json2_name.replace(".json", "")
    )
    logger.info(
        f"Display names for report: Doc1='{original_name1}', Doc2='{original_name2}'"
    )

    requirements_old = load_requirements(json1)
    # requirements_new = load_requirements(json2)

    if os.getenv("AMENDING_MODE", "false").lower() in ["true", "1"]:
        logger.info("[AMENDING MODE]")
        # norm_name = detect_normative_name(json1)
        try:
            norm_name = detect_normative_name(json1)
            logger.info(f"Nome normativo rilevato: {norm_name}")
        except RuntimeError as e:
            if str(e) == "SKIP_AMENDING":
                logger.info("Non posso trovare il PDF, esco dalla modalità emendativa")
                os.environ["AMENDING_MODE"] = "false"
            else:
                raise

        try:
            with open("amendment_system_prompt.txt", "r", encoding="utf-8") as f:
                amendment_system = f.read().strip()
            with open("amendment_user_prompt.txt", "r", encoding="utf-8") as f:
                amendment_user = f.read().strip()
        except Exception as e:
            logger.error(f"Errore nel caricamento dei prompt emendativi: {e}")
            sys.exit(1)

        # Carica la sezione emendativa estratta da requirement_analyzer
        try:
            with open(json2, "r", encoding="utf-8") as f2:
                data2 = json.load(f2)

            # Verifica che sia presente almeno un requisito
            if not data2.get("requirements") or len(data2["requirements"]) == 0:
                logger.error(f"Nessun requisito trovato nel file {json2}")
                return f"Errore: nessun requisito trovato nel file emendativo {json2}"

            # Il primo (e unico) requisito è l'intero testo emendativo
            amendment_text = data2["requirements"][0].get("requirement", "")

            if not amendment_text.strip():
                logger.error(f"Testo emendativo vuoto nel file {json2}")
                return f"Errore: testo emendativo vuoto nel file {json2}"

            logger.info(f"Testo emendativo estratto ({len(amendment_text)} caratteri)")
        except Exception as e:
            logger.error(f"Errore nell'estrazione del testo emendativo: {e}")
            return f"Errore nell'estrazione del testo emendativo: {e}"

        messages = [
            {"role": "system", "content": amendment_system},
            {
                "role": "user",
                "content": amendment_user.format(
                    normative=norm_name, amendment=amendment_text
                ),
            },
        ]

        AZURE_KEY = os.getenv("AZURE_OPENAI_API_KEY")
        AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
        AZURE_VER = os.getenv("AZURE_API_VERSION", "2024-08-01-preview")
        AZURE_VER_GPT4O = os.getenv("AZURE_API_VERSION_GPT4O", "2024-12-01-preview")
        if not AZURE_KEY or not AZURE_ENDPOINT:
            logger.error(
                "Azure API key o endpoint non definiti nelle variabili d'ambiente."
            )
            sys.exit(1)

        # Chiamata singola all'LLM con il prompt_amending custom
        # client = AzureOpenAI(api_key=os.getenv("AZURE_OPENAI_API_KEY"), azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"), api_version=os.getenv("AZURE_API_VERSION_GPT4o"))

        client = AzureOpenAI(
            api_key=AZURE_KEY,
            azure_endpoint=AZURE_ENDPOINT,
            api_version=AZURE_VER_GPT4O,
        )

        response = client.chat.completions.create(
            messages=messages,
            model=setup_model_gpt4o,
            temperature=setup_temperature,
            max_tokens=4000,
        )

        result = {"output": response.choices[0].message.content}

        if output_file:
            # Define the temporary file path
            temp_output_path = Path("./amending_results") / Path(output_file).name
            # Ensure tmp directory exists
            temp_output_path.parent.mkdir(parents=True, exist_ok=True)

            # Write the result to the temporary file
            with open(temp_output_path, "w", encoding="utf-8") as outf:
                json.dump(result, outf, ensure_ascii=False, indent=2)

            # Upload the temporary file to the 'amendments' blob
            blob_name = Path(output_file).name
            try:
                upload_to_blob(
                    "amendments", file_path=temp_output_path, blob_name=blob_name
                )
                logger.info(
                    f"Analisi emendativa salvata nel blob: amendments/{blob_name}"
                )
                # Optionally remove the temporary file after successful upload
                # temp_output_path.unlink(missing_ok=True)
            except Exception as e:
                logger.error(
                    f"Errore durante l'upload del file emendativo su blob: {e}"
                )
                # Decide how to handle upload failure, maybe raise an exception or log prominently

        print(json.dumps(result, ensure_ascii=False))
        logger.info("Termino l'esecuzione e ritorno il risultato")
        return response.choices[0].message.content

    requirements_new = load_requirements(json2)

    logger.info(f"Caricati {len(requirements_old)} requisiti da '{json1}'")
    # logger.info(f"Caricati {len(requirements_new)} requisiti da '{json2}'")

    # embedding_model = SentenceTransformer("paraphrase-multilingual-mpnet-base-v2")  # Removed
    # Precalcola gli embedding per i requisiti di entrambi i documenti
    # old_embeddings = embedding_model.encode(requirements_old)  # Removed
    # new_embeddings = embedding_model.encode(requirements_new)  # Removed
    # TODO: Use lex_package for embedding logic
    old_embeddings = np.zeros((len(requirements_old), 768))  # Placeholder
    new_embeddings = np.zeros((len(requirements_new), 768))  # Placeholder
    comparison_pairs = []

    for i, new_emb in enumerate(new_embeddings):
        # Calcola la similarità con tutti i requisiti del documento 1
        norms = np.linalg.norm(old_embeddings, axis=1) * np.linalg.norm(new_emb) + 1e-9
        similarities = np.dot(old_embeddings, new_emb) / norms
        most_similar_idx = int(np.argmax(similarities))
        if (
            similarities[most_similar_idx] > 0.5
        ):  # Soglia per considerare rilevante la corrispondenza
            comparison_pairs.append(
                (requirements_old[most_similar_idx], requirements_new[i])
            )
        else:
            # Se non c'è una corrispondenza sufficiente, si accoppia comunque per evidenziare la differenza
            comparison_pairs.append(
                (requirements_old[most_similar_idx], requirements_new[i])
            )
    logger.info(f"Formate {len(comparison_pairs)} coppie di confronto")

    chunks = create_optimal_chunks(comparison_pairs, embedding_model, max_tokens=20000)
    logger.info(f"Divisi in {len(chunks)} chunk per l'analisi")

    # Inizializza il client AzureOpenAI
    # azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
    # azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    # azure_api_version = os.getenv("AZURE_API_VERSION", "2024-08-01-preview")
    azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_endpoint = os.getenv(
        "AZURE_OPENAI_ENDPOINT", "https://cdpaistudioservices.openai.azure.com"
    )
    azure_api_version = os.getenv("AZURE_API_VERSION", "2024-08-01-preview")
    if not azure_api_key or not azure_endpoint:
        logger.error(
            "Azure API key o endpoint non definiti nelle variabili d'ambiente."
        )
        sys.exit(1)
    client = AzureOpenAI(
        api_key=azure_api_key,
        azure_endpoint=azure_endpoint,
        api_version=azure_api_version,
    )

    analyses = []
    for i, chunk in enumerate(chunks, 1):
        logger.info(f"Analizzando chunk {i}/{len(chunks)}...")
        # Passa i nomi originali (o i fallback) ad analyze_chunk
        analysis = analyze_chunk(
            chunk, client, prompt_message, original_name1, original_name2
        )
        logger.info(f"Invio al LLM di chunk {i}/{len(chunks)} completato")
        analyses.append(analysis)

    final_analysis = "\n\n".join(
        [
            "# Analisi Comparativa Documenti Normativi\n",
            f"## Analisi dei documenti: {original_name1} vs {original_name2}\n",
            "### Modifiche Principali\n",
            *analyses,
        ]
    )

    result = {"output": final_analysis}

    if output_file:
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logger.info(f"Report finale salvato in '{output_file}'")
        except Exception as e:
            logger.error(f"Errore nel salvataggio del report finale: {e}")

    # Stampa l'output JSON
    print(json.dumps(result, ensure_ascii=False))
    return result[
        "output"
    ]  # Return the actual content for consistency if needed elsewhere


def pair_requirements(
    doc1_reqs: List[dict],
    doc2_reqs: List[dict],
    embedding_model,  # SentenceTransformer removed
    similarity_threshold: float = 0.0,
) -> List[Tuple[str, str]]:
    """
    Funzione per abbinare i requisiti dei due documenti usando embedding
    """
    doc1_texts = [req.get("requirement", "") for req in doc1_reqs]
    doc2_texts = [req.get("requirement", "") for req in doc2_reqs]

    # logger.info("Calcolo degli embedding per i requisiti del Documento 1...")
    # doc1_embeddings = embedding_model.encode(doc1_texts, convert_to_numpy=True)  # Removed
    # logger.info("Calcolo degli embedding per i requisiti del Documento 2...")
    # doc2_embeddings = embedding_model.encode(doc2_texts, convert_to_numpy=True)  # Removed
    # TODO: Use lex_package for embedding logic
    doc1_embeddings = np.zeros((len(doc1_texts), 768))  # Placeholder
    doc2_embeddings = np.zeros((len(doc2_texts), 768))  # Placeholder

    pairs = []
    for i, emb2 in enumerate(doc2_embeddings):
        norms1 = np.linalg.norm(doc1_embeddings, axis=1)
        norm2 = np.linalg.norm(emb2)
        # Calcolo della similarità coseno
        similarities = np.dot(doc1_embeddings, emb2) / (norms1 * norm2 + 1e-8)
        best_idx = int(np.argmax(similarities))
        if similarities[best_idx] > similarity_threshold:
            pairs.append((doc1_texts[best_idx], doc2_texts[i]))
    return pairs


def analyze_chunk(
    chunk: AnalysisChunk,
    client: AzureOpenAI,
    prompt_template: str,
    doc1_display_name: str,
    doc2_display_name: str,  # Nuovi parametri
) -> str:
    """
    Funzione per analizzare un chunk inviando il contesto all'LLM
    """
    logger.info(f"[ANALYZE CHUNK] Doc1: {doc1_display_name}, Doc2: {doc2_display_name}")

    context_parts = []
    for old_text, new_text in zip(chunk.old_sections, chunk.new_sections):
        context_parts.append(
            f"DOCUMENTO 1 ({doc1_display_name}):\nREQUISITO:\n{old_text}\n\n"
            f"DOCUMENTO 2 ({doc2_display_name}):\nREQUISITO:\n{new_text}"
        )
    context = "\n\n---\n\n".join(context_parts)

    # logger.info(f"[CONTEXT]: {context}") # Can be very verbose

    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": prompt_template.format(context=context)},
    ]
    try:
        response = client.chat.completions.create(
            messages=messages,
            model=setup_model,
            temperature=setup_temperature,
            max_tokens=4000,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Errore durante l'analisi del chunk: {e}")
        return f"Errore durante l'analisi del chunk: {e}"


def main():
    # Configure RichHandler only when running as a script
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )
    # Specific level settings for when run as script can also go here if needed
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    if not os.environ.get("AZURE_OPENAI_API_KEY"):
        raise SystemExit("Imposta la variabile d'ambiente AZURE_OPENAI_API_KEY prima di eseguire questo script.")
    os.environ.setdefault(
        "AZURE_OPENAI_ENDPOINT", "https://cdpaistudioservices.openai.azure.com/"
    )
    os.environ.setdefault("AZURE_API_VERSION", "2024-08-01-preview")
    parser = argparse.ArgumentParser(
        description="Confronta i requisiti estratti (in formato JSON) di due documenti normativi."
    )
    parser.add_argument(
        "--json1",
        type=str,
        required=True,
        help="Percorso del file JSON del primo documento.",
    )
    parser.add_argument(
        "--json2",
        type=str,
        required=True,
        help="Percorso del file JSON del secondo documento.",
    )
    parser.add_argument(
        "--output_file",
        type=str,
        required=False,
        help="Percorso del file di output per salvare il report finale.",
    )
    parser.add_argument(
        "--amending_mode",
        type=bool,
        required=False,
        default="false",
        help="enable emending mode (true/false)",
    )
    args = parser.parse_args()

    # Carica i JSON
    try:
        with open(args.json1, "r", encoding="utf-8") as f:
            doc1 = json.load(f)
            logger.info(f"doc1: {str(doc1)[:50]}...")
        with open(args.json2, "r", encoding="utf-8") as f:
            doc2 = json.load(f)
            logger.info(f"doc2: {str(doc2)[:50]}...")
    except Exception as e:
        logger.error(f"Errore nel caricamento dei file JSON: {e}")
        sys.exit(1)

    doc1_reqs = doc1.get("requirements", [])
    doc2_reqs = doc2.get("requirements", [])
    logger.info(f"Documento 1: {len(doc1_reqs)} requisiti trovati.")
    logger.info(f"Documento 2: {len(doc2_reqs)} requisiti trovati.")

    # Inizializza il modello di embedding
    # logger.info("Inizializzazione del modello di embedding SentenceTransformer...")
    # embedding_model = SentenceTransformer("paraphrase-multilingual-mpnet-base-v2")  # Removed
    embedding_model = None  # Placeholder - use lex_package

    # Abbina i requisiti tra i due documenti
    logger.info("Abbinamento dei requisiti tra i documenti...")
    comparison_pairs = pair_requirements(
        doc1_reqs, doc2_reqs, embedding_model, similarity_threshold=0.0
    )
    logger.info(f"{len(comparison_pairs)} coppie di requisiti formate.")

    # Suddividi le coppie in chunk ottimali
    logger.info("Creazione dei chunk ottimali per l'analisi...")
    chunks = create_optimal_chunks(comparison_pairs, embedding_model, max_tokens=20000)
    logger.info(f"{len(chunks)} chunk creati.")

    # Inizializza il client Azure OpenAI (legge le variabili dall'ambiente)
    azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_api_version = os.getenv("AZURE_API_VERSION", "2024-08-01-preview")
    if not azure_api_key or not azure_endpoint:
        logger.error(
            "Azure API key o endpoint non definiti nelle variabili d'ambiente."
        )
        sys.exit(1)
    client = AzureOpenAI(
        api_key=azure_api_key,
        azure_endpoint=azure_endpoint,
        api_version=azure_api_version,
    )

    logger.info(f"\n\n\t\tAMENDING_MODE = {AMENDING_MODE}\n\n")
    if os.getenv("AMENDING_MODE"):
        result = compare_requirements(
            args.json1,
            args.json2,
            prompt_message,  # il prompt che hai caricato all'inizio del file
            args.output_file,  # può essere None
        )
        if args.output_file:
            logger.info("Il file di output esiste, termino.")
            sys.exit(0)
    else:

        # Analizza ogni chunk tramite l'LLM
        analyses = []
        for i, chunk in enumerate(chunks, 1):
            logger.info(f"Analisi del chunk {i}/{len(chunks)}...")
            analysis = analyze_chunk(
                chunk,
                client,
                prompt_message,
                doc1.get("source_file", "N/D").replace("tmp/", ""),
                doc2.get("source_file", "N/D").replace("tmp/", ""),
            )
            analyses.append(analysis)

        # Combina le analisi in un report finale
        final_analysis = "\n\n".join(
            [
                "# Analisi Comparativa Documenti Normativi\n",
                f"## Analisi dei documenti: {doc1.get('source_file', 'N/D').replace('tmp/', '')} vs {doc2.get('source_file', 'N/D').replace('tmp/', '')}\n",
                "### Modifiche Principali\n",
                *analyses,
            ]
        )
        upload_to_blob(
            "comparisons",
            file_path="./output",
            blob_name=args.output_file.split("/")[1],
        )

        result = {"output": final_analysis}

        if args.output_file:
            try:
                with open(args.output_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                logger.info(f"Report finale salvato in '{args.output_file}'")
            except Exception as e:
                logger.error(f"Errore nel salvataggio del report finale: {e}")

    # Stampa l'output JSON
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
