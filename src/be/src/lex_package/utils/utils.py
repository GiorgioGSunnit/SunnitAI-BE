import os
import pathlib
import json
import sys
from langchain.output_parsers import PydanticOutputParser
from lex_package.llm.factory import build_chat_model
# Ho centralizzato l'accesso all'LLM anche per le utility per non dover più
# settare AZURE_* manualmente in ogni script.
from langchain.prompts import PromptTemplate
from collections.abc import Iterable


# Handle imports for both direct script execution and package import
try:
    # When imported as part of the package
    from lex_package.t.similarity_minimal import Similarity
except ModuleNotFoundError:
    # When run directly, add parent directory to path
    sys.path.insert(
        0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    )
    from lex_package.t.similarity_minimal import Similarity

from langchain_core.runnables import RunnableConfig
from numbers import Number
from collections.abc import Hashable
from openai import RateLimitError, APITimeoutError
import re


MAX_CONCURRENCY = 5


def load_prompt(file_name):
    prompt_path = pathlib.Path(__file__).parent / "prompts" / file_name
    encodings = ["utf-8", "latin-1", "ISO-8859-1", "windows-1252"]

    for encoding in encodings:
        try:
            with open(prompt_path, "r", encoding=encoding) as file:
                return file.read()
        except UnicodeDecodeError:
            # Try next encoding
            continue
        except Exception as e:
            print(f"Errore nel caricamento del file {file_name}: {e}")
            return ""

    # If all encodings fail
    print(
        f"Errore nel caricamento del file {file_name}: Impossibile decodificare con nessuna codifica supportata"
    )
    return ""


def get_messages(type: str):
    system_prompt = load_prompt("system.txt")
    user_prompt = load_prompt(f"{type}.txt")
    return [system_prompt, user_prompt]


def clean_json_response(response_content):
    """
    Clean JSON response from AI models to extract valid JSON content, with improved error handling
    and fallback options for malformed JSON.

    Args:
        response_content (str): The raw response from the AI model

    Returns:
        dict: Parsed JSON content or a default fallback structure
    """
    import json
    import re
    import ast

    if not response_content or not isinstance(response_content, str):
        print("Warning: Empty or non-string response received")
        return {}

    # Initial cleaning
    cleaned_content = response_content.strip()

    try:
        # First attempt: Try direct JSON parsing of the entire content
        try:
            return json.loads(cleaned_content)
        except json.JSONDecodeError:
            pass

        # Second attempt: Extract JSON from code blocks
        json_pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)```")
        json_matches = json_pattern.findall(cleaned_content)

        if json_matches:
            # Try each extracted JSON block
            for json_str in json_matches:
                try:
                    return json.loads(json_str.strip())
                except json.JSONDecodeError:
                    continue

        # Third attempt: Try to find JSON-like content with curly braces
        brace_pattern = re.compile(r"(\{[\s\S]*\})")
        brace_matches = brace_pattern.findall(cleaned_content)

        if brace_matches:
            # Try each potential JSON object
            for potential_json in brace_matches:
                try:
                    return json.loads(potential_json.strip())
                except json.JSONDecodeError:
                    continue

        # Fourth attempt: Clean more aggressively and try again
        # Remove common problematic characters and try to parse again
        aggressive_clean = re.sub(
            r"[^\x20-\x7E]", "", cleaned_content
        )  # Remove non-printable chars
        aggressive_clean = re.sub(
            r"^[^{]*(\{.*\})[^}]*$", r"\1", aggressive_clean
        )  # Extract first {...}

        try:
            return json.loads(aggressive_clean)
        except json.JSONDecodeError:
            pass

        # Fifth attempt: Try using ast.literal_eval for Python literal structures
        # This can handle Python lists/dicts with single quotes which aren't valid JSON
        try:
            # Make sure we're only trying to parse list or dict literals
            stripped = cleaned_content.strip()
            if (stripped.startswith("[") and stripped.endswith("]")) or (
                stripped.startswith("{") and stripped.endswith("}")
            ):
                result = ast.literal_eval(stripped)
                # Convert result to a proper dict if it's a list
                if isinstance(result, list):
                    return {"items": result}
                return result
        except (SyntaxError, ValueError) as e:
            print(f"Warning: standard ast.literal_eval failed: {str(e)}")

        # Sixth attempt: Custom parser for Python-style lists with problematic quotes
        # This handles the case of lists with single-quoted strings containing unescaped single quotes
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                # Custom parsing for Python-style lists with single quotes
                items = []
                # Pattern to match items in a list, handling both single and double quoted strings
                list_pattern = re.compile(
                    r"""
                    '([^'\\]*(?:\\.[^'\\]*)*)'  # Match single-quoted strings with proper escaping
                    |
                    "([^"\\]*(?:\\.[^"\\]*)*)"  # Match double-quoted strings with proper escaping
                    |
                    ,                           # Match commas between items
                    |
                    \s+                         # Match whitespace
                """,
                    re.VERBOSE,
                )

                # Extract strings from the list, skipping the opening and closing brackets
                content_between_brackets = stripped[1:-1].strip()

                # A more direct approach for this specific case: matching each full list item
                item_pattern = re.compile(r"'([^']*(?:'[^']*)*?)'(?:,|$)")
                matches = item_pattern.findall(content_between_brackets)

                if matches:
                    return {"items": matches}

                # If that didn't work, try an even more manual approach
                if not matches and "\n" in content_between_brackets:
                    # Handle multiline lists with problematic quotes
                    lines = content_between_brackets.split("\n")
                    for line in lines:
                        line = line.strip()
                        if line and not line.startswith(
                            "#"
                        ):  # Skip comments and empty lines
                            # Extract text between single quotes, handling possible trailing commas
                            match = re.match(r"'(.*?)'(?:,|\s*$)", line)
                            if match:
                                items.append(match.group(1))

                    if items:
                        return {"items": items}
            except Exception as e:
                print(f"Warning: Custom list parser failed: {str(e)}")

        # Seventh attempt: Direct line-by-line extraction for specific format we observed
        if "\n" in stripped and stripped.startswith("[") and stripped.endswith("]"):
            try:
                # This is specifically for the format we observed in the debug output
                lines = stripped.split("\n")
                items = []

                for line in lines:
                    line = line.strip()
                    if not line or line == "[" or line == "]":
                        continue

                    # Remove trailing commas and handle quotes
                    if line.endswith(","):
                        line = line[:-1]

                    # Extract content between quotes (either single or double)
                    quote_match = re.match(r"['\"](.+)['\"]", line)
                    if quote_match:
                        items.append(quote_match.group(1))

                if items:
                    return {"items": items}
            except Exception as e:
                print(f"Warning: Line-by-line extraction failed: {str(e)}")

        # If all attempts fail, provide detailed error information and return fallback
        print(
            f"Warning: Failed to parse JSON or Python literals after multiple cleaning attempts"
        )
        print(f"First 100 chars of cleaned content: {cleaned_content[:100]}...")

        # Final fallback: Return a structured error response
        return {
            "error": "Failed to parse JSON response",
            "raw_content_preview": cleaned_content[:200]
            + ("..." if len(cleaned_content) > 200 else ""),
        }

    except Exception as e:
        print(f"Error during JSON cleaning: {str(e)}")
        return {
            "error": f"Unexpected error during JSON cleaning: {str(e)}",
            "raw_content_preview": cleaned_content[:100]
            + ("..." if len(cleaned_content) > 100 else ""),
        }


# utils for manipulating the articoli json


def get_title_and_comma(a):
    # Extract the title and the first comma from the article
    _a = {
        "titolo_articolo": a["titolo_articolo"],
        "comma": a["contenuto_parsato"][0]["contenuto"],
    }
    return _a


def get_title_and_requirements(a):
    # Extract the title and the requirements from the article
    if isinstance(a, str):
        # Handle case where a is a string
        return {"titolo_articolo": "Unknown", "requirement": a}

    # Handle case where a is a dictionary
    if not isinstance(a, dict) or "contenuto_parsato" not in a:
        # Handle case where a doesn't have contenuto_parsato
        title = (
            a.get("titolo_articolo", "Unknown") if isinstance(a, dict) else "Unknown"
        )
        return {"titolo_articolo": title, "requirement": str(a)}

    # Regular case - a is a dictionary with contenuto_parsato
    try:
        reqs: list[str] = [x["requirement"] for x in a["contenuto_parsato"]]
        req = "\n".join(reqs)  # uno per riga

        _a = {"titolo_articolo": a["titolo_articolo"], "requirement": req}
        return _a
    except (KeyError, TypeError) as e:
        # Handle case where contenuto_parsato items don't have requirement key
        title = a.get("titolo_articolo", "Unknown")
        return {
            "titolo_articolo": title,
            "requirement": str(a.get("contenuto_parsato", "")),
        }


def is_definitions_article(a):
    # Check if the article is a definitions article
    s = "definizioni"
    return s in a["titolo"].lower()


def max_correlation(data, t1):
    k_max = None
    matches = []

    for entry in data:
        try:
            if entry["source_data"]["articolo_1"]["titolo_articolo"] != t1:
                continue
            k_val = entry["similarity"]["k"]
        except (KeyError, IndexError, TypeError):
            # Struttura malformata: ignora senza pietà
            continue
        # Gestione del massimo
        if k_max is None or k_val > k_max:
            k_max = k_val
            matches = [
                {
                    "motivazione": entry["similarity"]["motivazione"],
                    "articolo_2": entry["source_data"]["articolo_2"],
                }
            ]  # reset se troviamo un nuovo massimo
        if k_val == k_max:
            matches.append(
                {
                    "motivazione": entry["similarity"]["motivazione"],
                    "articolo_2": entry["source_data"]["articolo_2"],
                }
            )
    return k_max, matches


async def compute_similarity_list(data_1, data_2, descrizione_dati):
    # takes two lists of dicts, returns the list of all the possible pairs together with their
    # similarity score

    prompt = PromptTemplate.from_template(
        """
    ti vengono dati  due estratti da due regolamenti legislativi, in particolare si tratta di {descrizione_dati},
    il tuo compito é assegnare un punteggio di correlazione tra i due testi da 0 a 30.
    Il punteggio deve essere tanto piu alto quanto gli argomenti trattati dai due estratti sono simili.
    Formato richiesto: {format_instructions}
    Input JSON: {input_json}
    """
    )

    llm = build_chat_model(target="primary", temperature=0)
    parser = PydanticOutputParser(pydantic_object=Similarity)  # {k, motivazione}
    similarity_chain = prompt | llm | parser
    similarity_chain = similarity_chain.with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError),
        stop_after_attempt=5,  # massimo 5 tentativi
        wait_exponential_jitter=True,  # jitter casuale per evitare collisioni
    )

    input_data = [
        {
            "input_json": json.dumps(
                {
                    "articolo_1": get_title_and_requirements(_a),
                    "articolo_2": get_title_and_requirements(_b),
                }
            ),
            "format_instructions": parser.get_format_instructions(),
            "descrizione_dati": descrizione_dati,
        }
        for _a in data_1
        for _b in data_2
    ]

    data = [
        {
            "articolo_1": a,
            "articolo_2": b,
        }
        for a in data_1
        for b in data_2
    ]

    similarities = await similarity_chain.abatch(
        input_data, config=RunnableConfig(max_concurrency=MAX_CONCURRENCY)
    )

    similarities_with_sources = [
        {"similarity": s.dict(), "source_data": d}
        for s, d in list(zip(similarities, data))
    ]

    return similarities_with_sources


async def compute_similarity_list_commi(data_1, data_2, descrizione_dati):
    # takes two lists of dicts, returns the list of all the possible pairs together with their
    # similarity score

    prompt = PromptTemplate.from_template(
        """
    ti vengono dati  due estratti da due regolamenti legislativi, in particolare si tratta di {descrizione_dati},
    il tuo compito é assegnare un punteggio di correlazione tra i due testi da 0 a 30.
    Il punteggio deve essere tanto piu alto quanto gli argomenti trattati dai due estratti sono simili.
    Formato richiesto: {format_instructions}
    Input JSON: {input_json}
    """
    )
    llm = build_chat_model(target="primary", temperature=0.0)
    parser = PydanticOutputParser(pydantic_object=Similarity)  # {k, motivazione}
    similarity_chain = prompt | llm | parser

    input_data = [
        {
            "input_json": json.dumps(
                {
                    "comma_1": _a["contenuto"],
                    "comma_2": _b["comma_2"]["contenuto"],
                }
            ),
            "format_instructions": parser.get_format_instructions(),
            "descrizione_dati": descrizione_dati,
        }
        for _a in data_1
        for _b in data_2
    ]

    data = [
        {
            "articolo_1": a,
            "comma_2": b,
        }
        for a in data_1
        for b in data_2
    ]

    similarities = await similarity_chain.abatch(
        input_data, config=RunnableConfig(max_concurrency=MAX_CONCURRENCY)
    )

    similarities_with_sources = [
        {"similarity": s.dict(), "source_data": d}
        for s, d in list(zip(similarities, data))
    ]

    return similarities_with_sources


def max_correlation_commi_articolo1(data, t1):
    k_max = None
    matches = []

    for entry in data:
        try:
            if entry["source_data"]["articolo_1"]["titolo_articolo"] != t1:
                continue
            k_val = entry["similarity"]["k"]
        except (KeyError, IndexError, TypeError):
            # Struttura malformata: ignora senza pietà
            continue
        # Gestione del massimo
        if k_max is None or k_val > k_max:
            k_max = k_val
            matches = [
                {
                    "motivazione": entry["similarity"]["motivazione"],
                    "articolo_2": entry["source_data"]["articolo_2"],
                }
            ]  # reset se troviamo un nuovo massimo
        if k_val == k_max:
            matches.append(
                {
                    "motivazione": entry["similarity"]["motivazione"],
                    "articolo_2": entry["source_data"]["articolo_2"],
                }
            )
    return k_max, matches


def argmax(data, keys):
    """
    Restituisce i sotto-dizionari di `data` il cui valore annidato indicato da `keys`
    (lista di chiavi) è massimo, ignorando le voci con valore mancante
    o non scalare/comparabile.
    """
    if not data or not keys:
        return []

    def get_nested_value(item):
        for k in keys:
            try:
                item = item[k]
            except (KeyError, TypeError):
                return None
        return item

    def is_scalar(v):
        return isinstance(v, (Number, str, bytes, bool, Hashable))

    # 1. costruisci (elemento, valore) una sola volta
    pairs = [
        (x, v) for x in data if (v := get_nested_value(x)) is not None and is_scalar(v)
    ]
    if not pairs:
        return []

    # 2. massimo; se vuoi gestire anche stringhe/numeri mischiati, ordina per str(v)
    try:
        max_val = max(v for _, v in pairs)
    except TypeError:  # tipi eterogenei -> fallback
        max_val = max(pairs, key=lambda p: str(p[1]))[1]

    # 3. tutti gli elementi che eguagliano il massimo
    return [x for x, v in pairs if v == max_val]


def get_article_by_identificativo(id: str, articles: list[dict]) -> dict:
    if id is None:
        return {}
    sid = str(id).strip()
    for a in articles:
        if not isinstance(a, dict):
            continue
        aid = a.get("identificativo")
        if aid is not None and str(aid).strip() == sid:
            return a
    return {}


def extract_integer(s: str) -> int:
    """
    Estrae il primo numero intero da una stringa.

    Args:
        s (str): Stringa di input contenente un numero.

    Returns:
        int: Il numero estratto.

    Raises:
        ValueError: Se non viene trovato alcun numero.
    """
    match = re.search(r"\d+", s)
    if match:
        return int(match.group())
    else:
        return -1


def normalize_string(s: str):
    return s.replace(" ", "").lower()


def concat_nested(data, sep: str = "") -> str:
    parts: list[str] = []

    def _walk(item):
        if isinstance(item, str):
            if item.strip():
                parts.append(item)
        elif isinstance(item, Iterable):
            for sub in item:
                _walk(sub)
        else:
            pass

    _walk(data)
    return sep.join(parts)
