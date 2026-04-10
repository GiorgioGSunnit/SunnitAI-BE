# Neo Confronto - Chunk Management

Sistema per la gestione e ricerca semantica di chunks di documenti tramite Azure Search AI.

## Script Principali

### 1. chunk_generator.py
**Genera chunks dal file di analisi del documento R**

```python
from chunk_generator import convert_r_analysis_to_chunks, save_chunks_to_file

# Genera chunks dal file R.json
chunks = convert_r_analysis_to_chunks()

# Salva in file JSON
save_chunks_to_file(chunks, "r_chunks.json")
```

**Utilizzo da riga di comando:**
```bash
python chunk_generator.py
```

### 2. chunk_indexer.py
**Indicizza i chunks in Azure Search AI**

```bash
python chunk_indexer.py
```

**Funzionalità:**
- Legge chunks da `r_chunks.json`
- Li converte nel formato Azure Search
- Li indicizza in parallelo (max 10 concurrent)
- Crea chiavi sicure nel formato `CDP-R-{chunk_id}`

### 3. chunk_retriever.py
**Recupera e cerca chunks da Azure Search AI**

```python
from chunk_retriever import search_chunks, search_cdp_chunks, get_chunk_by_id

# Ricerca semantica generica
results = search_chunks("monitoraggio", use_semantic=True, top=5)

# Ricerca specifica nei chunks CDP-R
cdp_results = search_cdp_chunks("rischio creditizio", top=10)

# Recupero chunk specifico
chunk = get_chunk_by_id("CDP-R-doc-R-chunk-00")
```

**Utilizzo da riga di comando:**
```bash
python chunk_retriever.py
```

## Parametri di Ricerca

### search_chunks()
- `search_text`: Testo da cercare (obbligatorio)
- `document_name`: Filtro per documento specifico (es. "Regolamento_del_credito_v7.0.pdf")
- `chapter`: Filtro per capitolo specifico
- `author`: Filtro per autore (es. "CDP System") 
- `use_semantic`: True per ricerca semantica, False per tradizionale
- `top`: Numero massimo di risultati (default: 10)

### search_cdp_chunks()
- `search_text`: Testo da cercare (obbligatorio)
- `top`: Numero massimo di risultati (default: 10)

## Workflow Completo

1. **Generazione**: `python chunk_generator.py`
2. **Indicizzazione**: `python chunk_indexer.py`
3. **Ricerca**: `python chunk_retriever.py` o use le funzioni programmaticamente

## Test

```bash
# Test completo
python test/test_chunks.py

# Test dettagliati
python test/test_azure_searchai.py
```

## Configurazione Azure Search

- **Endpoint**: `https://cdpaisearch.search.windows.net`
- **Index**: `azureblob-data-index`
- **Configurazione semantica**: `default`

## File di Output

- `r_chunks.json` - Chunks generati
- `search_example_results.json` - Risultati di esempio
- Vari file di test nella directory `test/`

## Struttura Chunk

```json
{
  "id": "doc-R-chunk-XX",
  "fileName": "Regolamento_del_credito_v7.0.pdf",
  "chapter": "Nome capitolo",
  "section": "Numero sezione", 
  "content": "Contenuto del chunk",
  "page": 123
}
```

## Note

- I chunks vengono indicizzati con chiavi nel formato `CDP-R-{chunk_id}`
- La ricerca semantica richiede Azure Search con configurazione semantica abilitata
- I chunks del documento R sono identificati dall'autore "CDP System" 