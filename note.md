
fe -> be ok

## Test pod be da pod fe
crea ssh connection al pod fe --- 
kubectl exec -it aiac-fe-service-68f7d96b84-2bnj4 -n aiac -- /bin/sh


## Endpoint API

| Metodo | Endpoint                                      | Cosa fa                                    | File/Linea              ✅ |
|--------|-----------------------------------------------|-------------------------------------------|-------------------------|
| GET    | /health                                       | Health check del servizio                 | call_fast_api.py:141    |
| GET    | /analysis-progress/                           | Stato avanzamento analisi in corso        | call_fast_api.py:325    |
| POST   | /compare-requirements/                        | **CORE** - Confronta 2 PDF (3 modalità)   | call_fast_api.py:369    |
| GET    | /api/v0/documents/{name}/                     | Ottiene requirements estratti             | call_fast_api.py:1493   |
| POST   | /api/v0/documents/{name}/compare/{compareTo}  | Confronta 2 documenti già estratti        | call_fast_api.py:1550   |
| POST   | /test                                         | Endpoint di test                          | call_fast_api.py:1756   |
| POST   | /translate                                    | Traduzione testo via Azure Translator     | call_fast_api.py:1804   |
| POST   | /search                                       | Ricerca documenti (Azure AI Search)       | call_fast_api.py:1870   |
| GET    | /download-excel                               | Download Excel ricerca                    | call_fast_api.py:1920   |
| GET    | /download-requirements-result                 | Download JSON requirements                | call_fast_api.py:1963   |
| GET    | /download-comparison-result                   | Download JSON confronto                   | call_fast_api.py:2041   |
| GET    | /api/v0/results/{name}                        | Ottiene risultati per nome                | call_fast_api.py:2157   |
| GET    | /api/v0/hashed-names                          | Lista mapping nomi -> hash                | call_fast_api.py:2237   |
| GET    | /api/v0/documents/{name}/excel                | Excel per singolo documento               | call_fast_api.py:2614   |
| GET    | /download-requirements-excel                  | Download Excel requirements               | call_fast_api.py:2683   |
| DELETE | /analysis-progress/{run_id}                   | Cancella progress di analisi              | call_fast_api.py:2700   |
| POST   | /extract-requirements/                        | **CORE** - Estrae requirements da PDF     | call_fast_api.py:2719   |
| GET    | /download-comparison-excel                    | Download Excel confronto                  | call_fast_api.py:3144   |
| DELETE | /delete-files                                 | Cancella files temporanei                 | call_fast_api.py:3214   |

---

## test analisi 

da pod del be, dopo aver copiato i pdf nel pod...

function analyze(){
# analizza il pdf di cui passo il nome
echo "run anal"

curl -X POST http://localhost:2025/extract-requirements/ \
  -F "file=@/tmp/$1" \
  -H "accept: application/json"
}


## Integrazione lex_package

`lex_package` è il **core business logic** per l'analisi e confronto di normative. Viene usato esclusivamente dall'endpoint principale:

### POST /compare-requirements/

Accetta 2 PDF + parametro `comparisonMode` ("emendativa" | "attuativa" | "versioning").

**Flusso:**

```
1. Upload file1.pdf + file2.pdf
   ↓
2. lex_package.analisi(pdf) → estrae articoli/commi strutturati
   ↓
3. Switch su comparisonMode:
   ├─ "emendativa"  → lex_package.confronto_emendativo(art1, art2)
   ├─ "attuativa"   → lex_package.confronto_attuativo(art1, art2)
   └─ "versioning"  → lex_package.confronto_versioning(art1, art2)
   ↓
4. flatten_*() → converte risultato in formato flat per Excel/JSON
   ↓
5. Ritorna JSON con risultati confronto
```

### Funzioni lex_package usate

| Funzione                              | Linea | Descrizione                                      |
|---------------------------------------|-------|--------------------------------------------------|
| `analisi()`                           | -     | Parsing PDF → struttura articoli/commi           |
| `confronto_emendativo()`              | 768   | Confronto modifiche emendative tra versioni      |
| `confronto_attuativo()`               | 977   | Confronto normativa vs attuazione                |
| `confronto_versioning()`              | 1139  | Confronto versioni successive stesso documento   |
| `flatten_confronto_emendativo()`      | 771   | Flatten output emendativo                        |
| `flat_confronto_attuativo_seconda_meta()` | 1006 | Flatten output attuativo                     |
| `flatten_confronto_versioning()`      | 1272  | Flatten output versioning                        |
| `integrazione_confronto_attuativo_*()` | 993  | Post-processing match titoli/commi               |
| `write_records_to_xlsx()`             | -     | Export risultati in Excel                        |

### Modalità confronto

- **emendativa**: Identifica modifiche puntuali (aggiunte/rimozioni/sostituzioni) tra 2 versioni
- **attuativa**: Verifica se una normativa è stata attuata da un regolamento
- **versioning**: Traccia evoluzione articoli tra versioni successive

