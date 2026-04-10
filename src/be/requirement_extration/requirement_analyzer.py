import logging
import json
import os
from pathlib import Path
import sys
from typing import List, Dict, Tuple, Optional, Callable
from datetime import datetime, timedelta
from pdfminer.high_level import extract_pages, extract_text
from pdfminer.layout import LTTextContainer, LAParams
from fuzzywuzzy import fuzz
import re
from dotenv import load_dotenv
import os
import time
import tiktoken
import fitz  # PyMuPDF
import unicodedata
import asyncio
import shutil  # Added for file copying

# from azure.core.exceptions import AzureError
import aiofiles
from collections import deque
import hashlib
from multiprocessing import Pool, cpu_count
from functools import partial
from extration_utils import compute_file_hash, get_python_path

# Configurazione logging
logger = logging.getLogger(__name__)

AMENDING_MODE = os.getenv("AMENDING_MODE", "false").lower() == "true"
AMEND_HEADER_RE = re.compile(
    r"Modifiche\s+del\s+Regolamento\s*\(UE\)\s*N\.?\s*(\d+/\d+)\r?\n", re.IGNORECASE
)
_AMEND_FOOTER_BASE = (
    r"^Articolo\s+\d+\s*\r?\n"  # Articolo <num>\n
    r"\s*Modifiche\s+del\s+Regolamento\s*\(UE\)\s*"
    r"N\.?\s*(\d+/\d+)"  # catturo in gruppo 1 il numero/anno del footer
)
AMEND_FOOTER_RE = None

# Carica variabili d'ambiente
load_dotenv()


def str2bool(v):
    return v.lower() in ("1", "true", "yes")


def extract_article_references(requirement_text: str) -> str:
    """
    Classifica il tipo di requisito usando fuzzy matching e pattern esatti.
    """
    text = requirement_text.lower()

    patterns = {
        "Obbligo": ["deve", "devono", "obbligo", "ispezione", "vigilanza"],
        "Divieto": ["vietato", "divieto", "non può", "precluso"],
        "Condizione": ["qualora", "nel caso", "condizione", "controllo"],
        "Termine temporale": ["termine", "decorrere"],
    }

    threshold = 85

    for pattern_type, keywords in patterns.items():
        words = text.split()
        if any(
            max(fuzz.ratio(word, keyword) for keyword in keywords) >= threshold
            for word in words
        ):
            return pattern_type

    if "entro il" in text:
        return "Termine temporale"

    return "altro"


def process_single_requirement(data: Tuple[Dict, List[str], int]) -> Dict:
    """
    Processa un singolo requisito utilizzando map() per le comparazioni.

    Args:
        data: Tupla contenente (requisito, lista delle pagine, threshold)

    Returns:
        Dict: Requisito processato con pagina e pattern type aggiunti
    """
    req, pages, threshold = data
    req_copy = req.copy()

    # Usa map per calcolare tutti i punteggi di similitudine
    scores = list(
        map(
            lambda p: (
                fuzz.partial_ratio(req["requirement"], p[1]),  # score
                p[0],  # page number
            ),
            enumerate(pages, start=1),
        )
    )

    # Trova la pagina con il punteggio migliore
    best_score, best_page = max(scores, key=lambda x: x[0], default=(0, "N/A"))

    if best_score < threshold:
        best_page = "N/A"

    # Raffinamento del risultato controllando la pagina precedente se necessario
    if best_page != "N/A" and best_page > 1:
        first_25_chars = req["requirement"][:25]
        prev_score = fuzz.partial_ratio(first_25_chars, pages[best_page - 2])
        current_score = fuzz.partial_ratio(first_25_chars, pages[best_page - 1])

        if prev_score > current_score:
            best_page -= 1

    # Aggiorna il requisito con le informazioni elaborate
    req_copy["page"] = best_page
    req_copy["pattern_type"] = extract_article_references(req["requirement"]) or "altro"

    return req_copy


class RequirementAnalyzer:
    def __init__(self, backend: str = "azure_openai", azure_config: Dict = None):
        self.backend = backend
        self.azure_config = azure_config
        if self.backend == "azure_openai" and azure_config:
            self.azure_client = self._init_azure_client()
        logger.info(f"RequirementAnalyzer inizializzato con backend '{self.backend}'")

    def _init_azure_client(self):
        """
        Inizializza il client Azure OpenAI.
        """
        from openai import AsyncAzureOpenAI
        import httpx

        custom_httpx_client = httpx.AsyncClient(proxy=None)
        self.azure_config["http_client"] = custom_httpx_client
        return AsyncAzureOpenAI(**self.azure_config)

    def safe_string_handling(self, input_data):
        if isinstance(input_data, bytes):
            input_data = input_data.decode("utf-8", errors="replace")
        normalized_data = unicodedata.normalize("NFC", input_data)
        clean_data = "".join(ch for ch in normalized_data if ch.isprintable())
        return clean_data

    def extract_text_with_page_mapping(self, pdf_path: str) -> Tuple[str, List[int]]:
        try:
            doc = fitz.open(pdf_path)
            text = b""
            offsets = [0]

            for page_num in range(len(doc)):
                page = doc[page_num]
                page_text = page.get_text("text").encode("utf-8")
                safe_text = self.safe_string_handling(page_text)
                safe_text_bytes = safe_text.encode("utf-8")
                text += safe_text_bytes + b"\n\f"
                offsets.append(len(text))

            text = text.decode("utf-8")
            logger.info(f"Estratto testo con {len(offsets) - 1} pagine.")
            return text, offsets
        except Exception as e:
            logger.error(f"Errore durante l'estrazione del testo dal PDF: {e}")
            raise

    def _split_into_chunks(
        self,
        text: str,
        chunk_size: int = 1500,
        overlap: int = 250,
        model: str = "gpt-4-32k",
    ) -> List[str]:
        tokenizer = tiktoken.encoding_for_model(model)
        tokens = tokenizer.encode(text)
        chunks = []

        start_positions = list(range(0, len(tokens), chunk_size - overlap))

        for start_idx in start_positions:
            end_idx = min(start_idx + chunk_size, len(tokens))
            chunk_tokens = tokens[start_idx:end_idx]
            chunk_text = tokenizer.decode(chunk_tokens)
            chunks.append(chunk_text)

            if end_idx >= len(tokens):
                break

        logger.info(f"Testo diviso in {len(chunks)} chunks")
        return chunks

    async def _process_single_chunk(
        self,
        chunk: str,
        chunk_index: int,
        retry_count: int = 3,
        run_id: Optional[str] = None,
    ) -> Optional[List[Dict]]:
        """
        Processa un singolo chunk con gestione dei retry.
        """
        log_prefix = f"[Run ID: {run_id}] " if run_id else ""
        for attempt in range(retry_count):
            try:
                with open("promptfull.txt", "r", encoding="utf-8") as file:
                    prompt_template = file.read().strip()

                final_prompt = prompt_template.format(document=chunk)

                response = (
                    await self.azure_client.chat.completions.with_raw_response.create(
                        messages=[
                            {
                                "role": "system",
                                "content": "Sei un Senior Compliance Analyst.",
                            },
                            {"role": "user", "content": final_prompt},
                        ],
                        model="gpt-4-32k",
                        temperature=0,
                        max_tokens=8000,
                    )
                )

                # Replace the verbose log with minimal status info to avoid clutter
                status_code = getattr(response, "status_code", "unknown")
                logger.debug(
                    f"{log_prefix}Chunk {chunk_index}: Response received with status {status_code}"
                )

                headers = response.headers if hasattr(response, "headers") else {}
                self.last_rate_limit_headers = {
                    "x-ratelimit-limit-requests": headers.get(
                        "x-ratelimit-limit-requests", ""
                    ),
                    "x-ratelimit-limit-tokens": headers.get(
                        "x-ratelimit-limit-tokens", ""
                    ),
                    "x-ratelimit-remaining-tokens": headers.get(
                        "x-ratelimit-remaining-tokens", ""
                    ),
                    "x-ratelimit-remaining-requests": headers.get(
                        "x-ratelimit-remaining-requests", ""
                    ),
                    "x-ratelimit-reset-requests": headers.get(
                        "x-ratelimit-reset-requests", ""
                    ),
                    "x-ratelimit-reset-tokens": headers.get(
                        "x-ratelimit-reset-tokens", ""
                    ),
                }

                # Log rate limit info in debug level instead of info to reduce noise
                logger.debug(
                    f"{log_prefix}Chunk {chunk_index}: Rate limit headers: {dict(self.last_rate_limit_headers)}"
                )

                response = response.parse()
                content = response.choices[0].message.content

                # Salva il risultato grezzo
                raw_dir = Path("output/raw_chunks")
                raw_dir.mkdir(parents=True, exist_ok=True)
                # Ensure raw_path incorporates run_id if needed for uniqueness, or just log it.
                # For now, keeping raw_path simple, run_id is for logging here.
                raw_path = raw_dir / f"raw_result_{chunk_index}.txt"

                async with aiofiles.open(raw_path, "w", encoding="utf-8") as f:
                    await f.write(content)
                logger.debug(
                    f"{log_prefix}Chunk {chunk_index}: Raw content saved to {raw_path}"
                )

                # Parsing del risultato
                parsed_result = self._parse_gpt_response(content)
                # Log a success message with the number of results found
                num_results = len(parsed_result) if parsed_result else 0
                logger.debug(
                    f"{log_prefix}Chunk {chunk_index}: Parsed {num_results} requirements"
                )
                return parsed_result

            except Exception as e:
                logger.error(
                    f"{log_prefix}Tentativo {attempt + 1} fallito per chunk {chunk_index}: {str(e)}"
                )
                if attempt == retry_count - 1:  # Ultimo tentativo
                    logger.error(
                        f"{log_prefix}Chunk {chunk_index} fallito dopo {retry_count} tentativi"
                    )
                    return None
                await asyncio.sleep(3)  # Breve pausa prima del retry

    async def _process_chunks_with_gpt(
        self,
        chunks: List[str],
        run_id: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> Dict:
        """
        Processa i chunk in parallelo con controllo del rate limit.
        """
        log_prefix = f"[Run ID: {run_id}] " if run_id else ""
        MAX_PARALLEL_REQUESTS = 15
        RATE_LIMIT_REQUESTS = 200
        REQUEST_INTERVAL = 60 / RATE_LIMIT_REQUESTS
        request_times = deque(maxlen=RATE_LIMIT_REQUESTS)
        total_chunks = len(chunks)
        processed_chunks_count = 0
        chunk_status_list = ["⬜"] * total_chunks

        logger.info(f"{log_prefix}Starting OpenAI processing of {total_chunks} chunks.")
        self._update_and_log_progress(
            chunk_status_list,
            run_id,
            progress_callback,
            total_chunks,
            processed_chunks_count,
            current_task_status="llm_analysis_started",
        )

        async def process_with_rate_limit(
            chunk_content: str, index: int
        ) -> Optional[List[Dict]]:
            nonlocal processed_chunks_count
            processed_chunks_count += 1
            now = datetime.now()
            if len(request_times) >= RATE_LIMIT_REQUESTS:
                oldest_request = request_times[0]
                if now - oldest_request < timedelta(minutes=1):
                    await asyncio.sleep(
                        (oldest_request + timedelta(minutes=1) - now).total_seconds()
                    )
            request_times.append(now)
            try:
                chunk_status_list[index] = "🔄"
                self._update_and_log_progress(
                    chunk_status_list,
                    run_id,
                    progress_callback,
                    total_chunks,
                    processed_chunks_count,
                    current_task_status=f"processing_chunk_{index+1}",
                )
                result = await self._process_single_chunk(
                    chunk_content, index, run_id=run_id
                )
                if result:
                    chunk_status_list[index] = "✅"
                else:
                    chunk_status_list[index] = "⚠️"
                self._update_and_log_progress(
                    chunk_status_list,
                    run_id,
                    progress_callback,
                    total_chunks,
                    processed_chunks_count,
                    current_task_status=f"completed_chunk_{index+1}",
                )
                return result
            except Exception as e:
                chunk_status_list[index] = "❌"
                self._update_and_log_progress(
                    chunk_status_list,
                    run_id,
                    progress_callback,
                    total_chunks,
                    processed_chunks_count,
                    current_task_status=f"failed_chunk_{index+1}",
                )
                logger.error(f"{log_prefix}Error processing chunk {index}: {str(e)}")
                return None

        running_tasks = []
        results = []
        aggregated_rate_limit_info = (
            self.last_rate_limit_headers
            if hasattr(self, "last_rate_limit_headers")
            else {}
        )
        for i, chunk_item in enumerate(chunks):
            if len(running_tasks) >= MAX_PARALLEL_REQUESTS:
                done, pending = await asyncio.wait(
                    running_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    try:
                        task_result = await task
                        if task_result:
                            results.extend(task_result)
                    except Exception as e:
                        logger.error(f"{log_prefix}Error in completed task: {e}")
                running_tasks = list(pending)
            task = asyncio.create_task(process_with_rate_limit(chunk_item, i))
            running_tasks.append(task)
            await asyncio.sleep(REQUEST_INTERVAL)
        if running_tasks:
            done, _ = await asyncio.wait(running_tasks)
            for task in done:
                try:
                    task_result = await task
                    if task_result:
                        results.extend(task_result)
                except Exception as e:
                    logger.error(f"{log_prefix}Error in remaining task: {e}")
        unique_results = []
        seen_requirements = set()
        for res_item in results:
            req_key = res_item["requirement"][:100]
            if req_key not in seen_requirements:
                seen_requirements.add(req_key)
                unique_results.append(res_item)

        final_processed_count = max(
            processed_chunks_count,
            (
                total_chunks
                if not any(s == "⬜" for s in chunk_status_list)
                else processed_chunks_count
            ),
        )
        logger.info(
            f"{log_prefix}LLM processing loop finished. Attempted chunks: {final_processed_count}/{total_chunks}."
        )
        logger.info(f"{log_prefix}Unique requirements found: {len(unique_results)}.")
        self._update_and_log_progress(
            chunk_status_list,
            run_id,
            progress_callback,
            total_chunks,
            final_processed_count,
            current_task_status="llm_analysis_completed",
        )
        return {
            "requirements": unique_results,
            "rate_limit_info": aggregated_rate_limit_info,
        }

    def _update_and_log_progress(
        self,
        chunk_status_list: List[str],
        run_id: Optional[str],
        progress_callback: Optional[Callable],
        total_chunks: int,
        processed_chunks_counter: int,
        current_task_status: Optional[str] = "processing",
    ):
        self._display_progress_bar(chunk_status_list, run_id)
        if progress_callback and run_id:
            completed_count = chunk_status_list.count("✅")
            failed_count = chunk_status_list.count("❌")
            in_progress_count = chunk_status_list.count("🔄")
            pending_count = chunk_status_list.count("⬜")
            percent_done = (
                (completed_count / total_chunks) * 100 if total_chunks > 0 else 0
            )

            progress_data = {
                "total_chunks": total_chunks,
                "chunks_completed_successfully": completed_count,
                "chunks_processed_total": processed_chunks_counter,
                "chunks_failed": failed_count,
                "chunks_in_progress": in_progress_count,
                "chunks_pending": pending_count,
                "percent_done": round(percent_done, 2),
                "raw_chunk_status_list": list(chunk_status_list),
            }
            if current_task_status:
                progress_data["current_operation_status"] = current_task_status

            try:
                progress_callback(run_id, progress_data)
            except Exception as e_cb:
                logger.error(
                    f"[Run ID: {run_id}] Error calling progress_callback: {e_cb}"
                )

    def _display_progress_bar(
        self, chunk_status: List[str], run_id: Optional[str] = None
    ):
        """
        Display a visual representation of chunk processing status.
        """
        log_prefix = f"[Run ID: {run_id}] " if run_id else ""
        total = len(chunk_status)
        completed = chunk_status.count("✅")
        failed = chunk_status.count("❌")
        in_progress = chunk_status.count("🔄")
        pending = chunk_status.count("⬜")

        percent_done = (completed / total) * 100 if total > 0 else 0

        progress_line = "".join(chunk_status)
        status_text = f"Progress: {completed}/{total} ({percent_done:.1f}%) | Done: {completed} | Failed: {failed} | In Progress: {in_progress} | Pending: {pending}"

        frame = "=" * 80

        # Construct the multi-line message parts, removing the initial newline from the message itself
        progress_bar_header = f"{frame}\n📊 PROGRESS BAR:"
        progress_details = f"{progress_line}\n{status_text}\n{frame}"

        full_message = f"{progress_bar_header}\n{progress_details}"
        logger.info(f"{log_prefix}{full_message}")

    def _analyze_with_gpt(
        self,
        text: str,
        offsets: List[int],
        run_id: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> Dict:
        """
        Synchronous wrapper that attempts to run _process_chunks_with_gpt.
        This method is problematic in async contexts like FastAPI if not handled carefully.
        Prefer using analyze_text_async directly.
        """
        log_prefix = f"[Run ID: {run_id}] " if run_id else ""
        model = "gpt-4-32k"
        chunks = self._split_into_chunks(
            text, chunk_size=1500, overlap=250, model=model
        )
        logger.info(
            f"{log_prefix}Avvio elaborazione (sync wrapper) GPT su {len(chunks)} chunks."
        )
        if progress_callback and run_id:
            progress_callback(
                run_id,
                {
                    "total_chunks": len(chunks),
                    "status": "starting_llm_analysis_sync_wrapper",
                    "chunks_processed_attempted": 0,
                    "chunks_completed_successfully": 0,
                },
            )
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                logger.warning(
                    f"{log_prefix}_analyze_with_gpt called from a running event loop. This can block. Prefer analyze_text_async."
                )
                # Running a coroutine from a running loop and waiting for its result synchronously
                # is generally an anti-pattern in async programming as it blocks the loop.
                # A better approach for true async execution would be for the caller to be async.
                # If this path MUST be taken from an async caller, consider using
                # asyncio.run_coroutine_threadsafe if this method is called from a different thread,
                # or re-evaluating the design. For now, this will likely block.
                future = asyncio.run_coroutine_threadsafe(
                    self._process_chunks_with_gpt(
                        chunks, run_id=run_id, progress_callback=progress_callback
                    ),
                    loop,
                )
                return future.result()
            else:
                return asyncio.run(
                    self._process_chunks_with_gpt(
                        chunks, run_id=run_id, progress_callback=progress_callback
                    )
                )
        except RuntimeError:
            logger.info(
                f"{log_prefix}RuntimeError during event loop management in _analyze_with_gpt, attempting asyncio.run() as fallback."
            )
            return asyncio.run(
                self._process_chunks_with_gpt(
                    chunks, run_id=run_id, progress_callback=progress_callback
                )
            )
        except Exception as e:
            logger.error(
                f"{log_prefix}Error in _analyze_with_gpt (sync wrapper): {str(e)}"
            )
            if progress_callback and run_id:
                progress_callback(
                    run_id,
                    {"status": "error_in_sync_gpt_wrapper", "error_message": str(e)},
                )
            raise
        return {}

    async def analyze_text_async(
        self,
        text: str,
        offsets: List[int],
        run_id: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        """
        Async version of analyze_text for better integration with FastAPI
        """
        log_prefix = f"[Run ID: {run_id}] " if run_id else ""
        if self.backend == "bert":
            if progress_callback and run_id:
                progress_callback(
                    run_id,
                    {
                        "status": "bert_analysis_started",
                        "total_chunks": 1,
                        "chunks_in_progress": 1,
                        "chunks_processed_attempted": 0,
                        "chunks_completed_successfully": 0,
                    },
                )
            result = self._analyze_with_bert(text, offsets)
            if progress_callback and run_id:
                progress_callback(
                    run_id,
                    {
                        "status": "bert_analysis_completed",
                        "percent_done": 100,
                        "chunks_completed_successfully": 1,
                        "chunks_processed_attempted": 1,
                        "chunks_in_progress": 0,
                    },
                )
            return result
        elif self.backend == "azure_openai":
            model = "gpt-4-32k"
            chunks = self._split_into_chunks(
                text, chunk_size=1500, overlap=250, model=model
            )
            logger.info(
                f"{log_prefix}Avvio elaborazione asincrona OpenAI su {len(chunks)} chunks."
            )
            if progress_callback and run_id:
                progress_callback(
                    run_id,
                    {
                        "total_chunks": len(chunks),
                        "status": "preparing_openai_chunks",
                        "chunks_processed_attempted": 0,
                        "chunks_completed_successfully": 0,
                    },
                )
            return await self._process_chunks_with_gpt(
                chunks, run_id=run_id, progress_callback=progress_callback
            )

    def analyze_text(
        self,
        text: str,
        offsets: List[int],
        run_id: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        """
        Synchronous entry point for analysis that handles async operations properly
        """
        log_prefix = f"[Run ID: {run_id}] " if run_id else ""
        if self.backend == "bert":
            if progress_callback and run_id:
                progress_callback(
                    run_id,
                    {
                        "status": "bert_analysis_started_sync",
                        "total_chunks": 1,
                        "chunks_in_progress": 1,
                        "chunks_processed_attempted": 0,
                        "chunks_completed_successfully": 0,
                    },
                )
            result = self._analyze_with_bert(text, offsets)
            if progress_callback and run_id:
                progress_callback(
                    run_id,
                    {
                        "status": "bert_analysis_completed_sync",
                        "percent_done": 100,
                        "chunks_completed_successfully": 1,
                        "chunks_processed_attempted": 1,
                        "chunks_in_progress": 0,
                    },
                )
            return result
        elif self.backend == "azure_openai":
            logger.warning(
                f"{log_prefix}Synchronous analyze_text called for azure_openai. Progress callback WILL be passed to _analyze_with_gpt wrapper."
            )
            return self._analyze_with_gpt(
                text, offsets, run_id=run_id, progress_callback=progress_callback
            )

    def _parse_gpt_response(self, raw_response: str) -> List[Dict]:
        pattern = r'(?:\[")?(.*?)\s*Etichetta:\s*(.*?)\s*(?:"|\]|$)'
        matches = re.findall(pattern, raw_response, re.DOTALL)
        # Use debug level instead of info for details
        logger.debug(f"Parsing risposta: trovati {len(matches)} match.")

        processed_matches = []
        with open("matches.txt", "w", encoding="utf-8") as f:
            for match in matches:
                requirement = re.sub(r'^[\["]+', "", match[0].strip()).lstrip('"')
                core_text = match[1].strip()
                processed_matches.append(
                    {"requirement": requirement, "core_text": core_text}
                )
                formatted_line = f'["{requirement}", "{core_text}"]\n'
                logger.debug(f"Match: {formatted_line.strip()}")
                f.write(formatted_line)

        # For match details, use debug level, but keep a summary at info level if there are matches
        if processed_matches:
            logger.info(f"Extracted {len(processed_matches)} requirements")
        else:
            logger.debug("No matches found in response")

        return processed_matches

    def _analyze_with_bert(self, text: str, offsets: List[int]) -> List[Dict]:
        pass

    def save_results(
        self, requirements_result, input_file: str, output_file: str, text: str
    ) -> None:
        """
        Salva i risultati dell'analisi usando parallel processing.

        Se 'requirements_result' è un dizionario (come restituito da analyze_text con Azure OpenAI),
        estrae i campi "requirements" e "rate_limit_info". Altrimenti, assume che sia una lista.

        I risultati salvati includeranno inoltre le informazioni sul rate limit (gli header
        relativi a 'x-ratelimit-limit-requests', 'x-ratelimit-remaining-requests' e 'x-ratelimit-reset-requests'),
        che vengono poi passati al frontend (attenzione al limite massimo di byte per il custom status su Azure).
        """
        # Se il risultato è un dizionario, ne estrae i campi; altrimenti, il rate limit sarà vuoto.
        if isinstance(requirements_result, dict):
            rate_limit_info = requirements_result.get("rate_limit_info", {})
            raw_requirements = requirements_result.get("requirements", [])
        else:
            rate_limit_info = {}
            raw_requirements = requirements_result

        # Divide il testo in pagine utilizzando il delimitatore usato (ad es. "\x0c")
        pages = text.split("\x0c")
        logger.info(f"Documento diviso in {len(pages)} pagine.")

        # Imposta la soglia per il processing dei requisiti (puoi regolare questo valore se necessario)
        threshold = 50

        # Prepara i dati per il parallel processing
        # Per ogni requisito estratto, prepara una tupla con (requisito, lista_pagine, soglia)
        processing_data = [(req, pages, threshold) for req in raw_requirements]

        # Calcola il numero ottimale di processi da utilizzare in base al numero di requisiti e ai core disponibili
        n_cores = cpu_count()
        n_processes = min(n_cores, len(raw_requirements)) if raw_requirements else 1
        logger.info(f"Utilizzo {n_processes} processi su {n_cores} core disponibili")

        # Esegue il processing parallelo per eventuali ulteriori analisi/deduplicazione dei requisiti
        try:
            with Pool(processes=n_processes) as pool:
                processed_requirements = pool.map(
                    process_single_requirement, processing_data
                )
        except Exception as e:
            logger.error(f"Errore durante il processing parallelo: {str(e)}")
            raise

        # Prepara il dizionario finale dei risultati includendo anche le informazioni di rate limit
        final_results = {
            "source_file": input_file,
            "analysis_date": datetime.now().isoformat(),
            "text_length": len(text),
            "requirements_found": len(processed_requirements),
            "requirements": processed_requirements,
            "rate_limit_info": rate_limit_info,
        }

        # Salva i risultati in output_file in formato JSON
        try:
            with open(output_file, "w", encoding="utf-8") as file:
                json.dump(final_results, file, indent=2, ensure_ascii=False)
            logger.info(f"Risultati salvati in: {output_file}")
        except Exception as e:
            logger.error(f"Errore durante il salvataggio dei risultati: {str(e)}")
            raise

    def extract_amendment_pdf_section(self, pdf_path: str) -> str:
        """
        Apre il PDF con fitz, unisce il testo di tutte le pagine e
        ne ritorna solo la sezione compresa tra:
        - prima comparsa di REGOLAMENTO (UE) N.xxx/xxxx (catturato in gruppo 1)
        - la successiva riga "Articolo X\nModifiche del Regolamento (UE) n. Y/Z"
        dove Y/Z è diverso da XXX/XXXX dell'header.
        """
        # 1) apro il PDF e concateno tutto il testo
        with fitz.open(pdf_path) as doc:
            full_text = "".join(page.get_text("text") + "\n" for page in doc)

        # 2) trovo l'header e ne estraggo il numero/anno
        hdr = AMEND_HEADER_RE.search(full_text)
        if not hdr:
            raise RuntimeError(
                "Intestazione regolamento non trovata nell'atto emendativo"
            )
        header_num = hdr.group(1)  # es. "648/2012"

        # 3) ricompilo la regex del footer escludendo quel numero/anno
        footer_re = re.compile(
            rf"^Articolo\s+\d+\s*\r?\n"
            rf"\s*Modifiche\s+del\s+Regolamento\s*\(UE\)\s*"
            rf"N\.?\s*(?!{re.escape(header_num)})\d+/\d+",
            re.IGNORECASE | re.MULTILINE,
        )

        # 4) cerco il footer *a partire* dalla fine dell'header
        footer = footer_re.search(full_text, pos=hdr.end())
        end_pos = footer.start() if footer else len(full_text)

        # 5) estraggo la sezione compresa tra header ed eventuale footer
        section = full_text[hdr.end() : end_pos].strip()

        # 6) loggo la sezione, tronca a 200 caratteri per evitare output eccessivi
        preview = section[:200] + ("..." if len(section) > 200 else "")
        logger.info(f"[EMEND] estratte {len(section)} caratteri: {preview!r}")

        return section


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract requirements from a PDF document."
    )
    parser.add_argument(
        "--input_data", type=str, required=True, help="Path to the input PDF file"
    )
    parser.add_argument(
        "--model_output",
        type=str,
        required=True,
        help="Path to save the output JSON file",
    )
    parser.add_argument(
        "--amending_mode",
        type=str2bool,
        default=False,
        help="Se True, usa il prompt per legge emendativa",
    )
    parser.add_argument(
        "--backend",
        type=str,
        choices=["bert", "azure_openai"],
        default="azure_openai",
        help="Backend to use for analysis ('bert' or 'azure_openai')",
    )
    parser.add_argument(
        "--azure_api_key",
        type=str,
        help="Azure OpenAI API key (optional, otherwise read from .env)",
    )
    parser.add_argument(
        "--azure_endpoint",
        type=str,
        help="Azure OpenAI endpoint (optional, otherwise read from .env)",
    )

    args = parser.parse_args()

    # --- START New Caching Logic ---
    CACHE_DIR = Path("output/analyzer_cache/final_results")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)  # Ensure cache directory exists

    file_hash = compute_file_hash(args.input_data)
    cached_json_path = CACHE_DIR / f"{file_hash}.json"
    user_specified_output_file = Path(args.model_output)

    logger.info(f"Input file: {args.input_data}")
    logger.info(f"User-specified output file: {user_specified_output_file}")
    logger.info(f"Cache file path: {cached_json_path}")

    # Check if a valid cached result exists
    if cached_json_path.exists() and cached_json_path.stat().st_size > 0:
        logger.info(
            f"Cache hit: Found existing results for hash {file_hash} at {cached_json_path}"
        )
        # If the user wants the output in a specific different file, copy it there.
        if user_specified_output_file != cached_json_path:
            try:
                user_specified_output_file.parent.mkdir(
                    parents=True, exist_ok=True
                )  # Ensure target directory exists
                shutil.copyfile(cached_json_path, user_specified_output_file)
                logger.info(f"Copied cached results to {user_specified_output_file}")
            except Exception as e:
                logger.error(
                    f"Error copying cached file to {user_specified_output_file}: {e}"
                )
                # Decide if to proceed or exit; for now, we'll exit as cache is primary
                exit(1)
        else:
            logger.info(
                f"Results already available at specified output path: {user_specified_output_file}"
            )
        exit(0)  # Exit after handling cache hit
    else:
        logger.info(f"Cache miss for hash {file_hash}. Proceeding with analysis.")
    # --- END New Caching Logic ---

    # The original output_file variable is now user_specified_output_file for clarity
    # output_file = args.model_output # This line is effectively replaced by user_specified_output_file

    # The old caching check is now replaced by the new logic above.
    # if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
    #     logger.info(
    #         f"Il file di output per l'hash {file_hash} esiste già. Recupero i risultati salvati."
    #     )
    #     logger.info(
    #         f"Il file di output {output_file} esiste già. Recupero i risultati salvati."
    #     )
    #     with open(output_file, "r", encoding="utf-8") as f:
    #         data = json.load(f)
    #     # print(json.dumps(dsta, indent=2, ensure_ascii=False))\
    #     exit(0)

    azure_config = None
    if args.backend == "azure_openai":
        azure_api_key = os.environ.get("AZURE_OPENAI_API_KEY")
        azure_endpoint = os.environ.get(
            "AZURE_OPENAI_ENDPOINT", "https://cdpaistudioservices.openai.azure.com"
        )
        azure_api_version = os.environ.get("AZURE_API_VERSION", "2024-08-01-preview")
        if not azure_api_key or not azure_endpoint:
            raise ValueError(
                "Azure API key and endpoint are required to use Azure OpenAI."
            )
        azure_config = {
            "api_key": azure_api_key,
            "azure_endpoint": azure_endpoint,
            "api_version": azure_api_version,
        }

    analyzer = RequirementAnalyzer(backend=args.backend, azure_config=azure_config)

    try:
        logger.info(f"Processing PDF file: {args.input_data}")
        if args.amending_mode:
            # ritaglio e salvo il JSON con un'unica entry
            amendment = analyzer.extract_amendment_pdf_section(args.input_data)
            out = {
                "source_file": args.input_data,
                "requirements": [{"requirement": amendment}],
            }
            with open(user_specified_output_file, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            logger.info(
                f"Emendamento salvato in {user_specified_output_file}, lunghezza: {len(amendment)} caratteri\nContenuto: {json.dumps(out, ensure_ascii=False)[:600]}"
            )
            sys.exit(0)
        else:
            text, offsets = analyzer.extract_text_with_page_mapping(args.input_data)
            logger.info(f"Text extracted: {len(text)} characters")

        logger.info("Starting requirement analysis...")
        requirements_result = analyzer.analyze_text(
            text, offsets, run_id=None
        )  # Questo metodo ora gestisce internamente l'async

        analyzer.save_results(
            requirements_result, args.input_data, str(user_specified_output_file), text
        )
        logger.info("Analysis completed successfully!")

        # --- START Save to Cache After Analysis ---
        if (
            user_specified_output_file.exists()
            and user_specified_output_file.stat().st_size > 0
        ):
            if user_specified_output_file != cached_json_path:
                try:
                    shutil.copyfile(user_specified_output_file, cached_json_path)
                    logger.info(f"Saved results to cache: {cached_json_path}")
                except Exception as e:
                    logger.error(
                        f"Error saving results to cache file {cached_json_path}: {e}"
                    )
            else:
                # If user_specified_output_file is already the cache path, it's already saved there by save_results
                logger.info(f"Results already saved to cache path: {cached_json_path}")
        else:
            logger.warning(
                f"Output file {user_specified_output_file} was not created or is empty. Cannot update cache."
            )
        # --- END Save to Cache After Analysis ---

        # Pulisce la cartella dei risultati raw
        raw_dir = Path("output/raw_chunks")
        if raw_dir.exists() and raw_dir.is_dir():
            for file in raw_dir.glob("raw_result_*.txt"):
                try:
                    file.unlink()
                except Exception as e:
                    logger.warning(f"Non riesco a cancellare {file}: {e}")
    except Exception as e:
        logger.error(f"Error during processing: {str(e)}")
        raise
