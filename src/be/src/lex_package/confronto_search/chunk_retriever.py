import os
import requests
import json
from typing import Dict, Any, Optional

# Azure Search AI configuration
ENDPOINT = "https://cdpaisearch.search.windows.net"
INDEX_NAME = "azureblob-data-index"
API_KEY = os.getenv("AZURE_SEARCH_ADMIN_KEY", "")

# Headers for the request
HEADERS = {"Content-Type": "application/json", "api-key": API_KEY}
PARAMS = {"api-version": "2023-11-01"}


def search_chunks(
    search_text: str,
    document_name: Optional[str] = None,
    chapter: Optional[str] = None,
    author: Optional[str] = None,
    use_semantic: bool = False,  # Cambiato a False per evitare quota semantica
    top: int = 10,
) -> Optional[Dict[str, Any]]:
    """
    Cerca tra i chunks indicizzati in Azure Search AI

    Args:
        search_text: Testo da cercare nel contenuto
        document_name: Nome del documento (es. "Regolamento_del_credito_v7.0.pdf")
        chapter: Capitolo specifico da cercare
        author: Autore del documento
        use_semantic: Usa la ricerca semantica (True) o tradizionale (False)
        top: Numero massimo di risultati da restituire

    Returns:
        Dict con i risultati della ricerca o None se errore
    """
    # Costruisci i filtri
    filters = []
    if document_name:
        filters.append(f"metadata_storage_name eq '{document_name}'")
    if author:
        filters.append(f"metadata_author eq '{author}'")

    filter_string = " and ".join(filters) if filters else None

    # Costruisci i dati della richiesta
    if use_semantic:
        search_data = {
            "search": search_text,
            "queryType": "semantic",
            "semanticConfiguration": "default",
            "captions": "extractive|highlight-true",
            "answers": "extractive|count-3",
            "top": top,
            "select": "metadata_storage_path,metadata_storage_name,content,metadata_author",
        }
    else:
        search_data = {
            "search": search_text,
            "top": top,
            "select": "metadata_storage_path,metadata_storage_name,content,metadata_author",
        }

    # Aggiungi il filtro se presente
    if filter_string:
        search_data["filter"] = filter_string

    # Aggiungi ricerca nel capitolo se specificato
    if chapter:
        search_data["search"] = f"{search_text} AND Chapter: {chapter}"

    try:
        response = requests.post(
            f"{ENDPOINT}/indexes/{INDEX_NAME}/docs/search",
            headers=HEADERS,
            params=PARAMS,
            json=search_data,
        )

        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ Ricerca fallita (status {response.status_code})")
            return None

    except requests.exceptions.RequestException as e:
        print(f"❌ Errore nella richiesta: {e}")
        return None


def search_cdp_chunks(search_text: str, top: int = 100) -> Optional[Dict[str, Any]]:
    """
    Cerca specificamente tra i chunks CDP-R

    Args:
        search_text: Testo da cercare
        top: Numero massimo di risultati da restituire

    Returns:
        Dict con i risultati della ricerca filtrati per chunks CDP-R
    """
    # Usa ricerca con filtro autore per trovare chunks CDP-R
    result = search_chunks(
        search_text=search_text,
        document_name="Regolamento_del_credito_v7.0.pdf",
        use_semantic=False,  # Ricerca tradizionale per evitare quota semantica
        top=top * 2,  # Prendi più risultati per filtrare
    )

    if result and result.get("value"):
        # Filtra solo i chunks CDP-R
        cdp_chunks = [
            chunk
            for chunk in result["value"]
            if chunk.get("metadata_storage_path", "").startswith("CDP-R-")
        ]

        # Limita ai primi N risultati
        result["value"] = cdp_chunks[:top]

    return result


def get_chunk_by_id(chunk_id: str) -> Optional[Dict[str, Any]]:
    """
    Recupera un chunk specifico tramite il suo ID

    Args:
        chunk_id: ID del chunk (es. "CDP-R-doc-R-chunk-00")

    Returns:
        Dict con i dati del chunk o None se non trovato
    """
    try:
        response = requests.get(
            f"{ENDPOINT}/indexes/{INDEX_NAME}/docs/{chunk_id}",
            headers=HEADERS,
            params=PARAMS,
        )

        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ Chunk non trovato: {chunk_id}")
            return None

    except requests.exceptions.RequestException as e:
        print(f"❌ Errore nel recupero chunk: {e}")
        return None


def save_results_to_file(results: Dict[str, Any], filename: str) -> bool:
    """
    Salva i risultati della ricerca in un file JSON

    Args:
        results: Risultati della ricerca
        filename: Nome del file di output

    Returns:
        True se salvato con successo, False altrimenti
    """
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"❌ Errore nel salvataggio: {e}")
        return False


def main():
    # Esempio di utilizzo
    print("🔍 Esempio di ricerca chunks...")

    # Test ricerca nei chunks CDP-R
    result = search_chunks(
        search_text="Tali rischi “equity”,  assunti da società sottoposte a direzione e coordinamento di CDP ovvero da Fondi di cui CDP  detenga quote",  # Cerca nel content
        document_name="Regolamento_del_credito_v7.0.pdf",  # Filtra per fileName
        top=3,
    )

    if result and result.get("value"):
        print(f"✅ Trovati {len(result['value'])} chunks CDP-R")

        # Mostra primi risultati
        for i, chunk in enumerate(result["value"][:3]):
            chunk_id = chunk.get("metadata_storage_path", "N/A")
            content_preview = chunk.get("content", "")[:100] + "..."
            print(f"  {i + 1}. {chunk_id}")
            print(f"     Content: {content_preview}")

        # Salva risultati
        save_results_to_file(result, "search_example_results.json")
        print("💾 Risultati salvati in: search_example_results.json")
    else:
        print("❌ Nessun risultato trovato")


if __name__ == "__main__":
    main()
