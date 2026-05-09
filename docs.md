# Parser Worklog

## Contesto
Richiesta: migliorare tempo e qualita` degli output dei parser del backend prendendo spunto dalla cartella esterna `../parsing_utils`, lavorando sempre su un branch dedicato e mantenendo traccia delle novita`.

Repository di lavoro:
- `SunnitAI-BE`

Cartella parser di confronto:
- `../parsing_utils`

## Preparazione branch
- Verificato branch corrente: `main`
- Eseguito `git pull --ff-only origin main`
- Creato branch di lavoro: `feat/parser-quality-performance`

## Analisi iniziale
- Confrontata la cartella `src/be/src/lex_package/parsing_utils` con `../parsing_utils`
- Verificato che il `document_profiler` interno al backend e` gia` piu` evoluto della versione esterna
- Identificati come candidati migliori da cui prendere spunto:
  - `parser_indice_avanzato.py`
  - `parser_gazzetta_ufficiale.py`
  - `parser_banca_d_italia.py`
  - `parser_spinoff_chapters.py`

## Intervento 1
Target:
- `src/be/src/lex_package/parsing_utils/parser_indice.py`

Obiettivo:
- migliorare robustezza del parsing degli indici senza introdurre subito un merge invasivo del parser avanzato esterno

Modifiche introdotte:
- aumento della finestra di ricerca dell'indice
- riconoscimento esplicito della pagina di start dell'indice
- parsing piu` robusto di voci TOC:
  - numeriche con filler
  - testuali con filler
  - testuali senza filler ma con keyword strutturali
- supporto a righe spezzate / multilinea
- stop anticipato dopo piu` pagine che non sembrano piu` indice
- deduplica finale delle voci

File aggiunti/modificati:
- `src/be/src/lex_package/parsing_utils/parser_indice.py`
- `src/be/src/lex_package/test_parser_indice.py`

Verifica locale eseguita:
- smoke test Python sulle nuove euristiche di `_parse_toc_candidate`

## Benchmark main vs branch
Obiettivo:
- eseguire lo stesso test su `main` e sul branch corrente
- salvare risultati grezzi e confronto leggibile

Output previsti:
- `reports/parser_comparison/`
  - `results_main.json`
  - `results_branch.json`
  - `comparison_report.md`

Metodo:
- confronto su PDF reali presenti nel repo
- confronto su PDF sintetico costruito ad hoc per esercitare i casi migliorati del parser indice
- snapshot di `main` estratto in `/tmp` via `git archive` per evitare side effect sul branch di lavoro

Risultati prodotti:
- `reports/parser_comparison/results_main.json`
- `reports/parser_comparison/results_branch.json`
- `reports/parser_comparison/comparison_report.md`
- `reports/parser_comparison/assets/generated_index_case.pdf`

Esito benchmark:
- `NEW_1to5.pdf`
  - main: `0` voci indice, `201.915 ms`
  - branch: `0` voci indice, `148.043 ms`
  - nota: PDF reale non esercita il parser indice
- `OLD_1to5.pdf`
  - main: `0` voci indice, `141.91 ms`
  - branch: `0` voci indice, `144.503 ms`
  - nota: PDF reale non esercita il parser indice
- `generated_index_case.pdf`
  - main: `1` voce indice, `128.367 ms`
  - branch: `2` voci indice, `133.601 ms`
  - miglioramento qualitativo osservato:
    - `main` estrae solo `Ambito di applicazione`
    - `branch` estrae `Ambito di applicazione` e `Capitolo 7 Requisiti operativi`
  - tradeoff osservato:
    - branch leggermente piu` lento sul caso sintetico (`+5.234 ms`)
    - guadagno di copertura strutturale superiore al piccolo aumento di tempo

Lettura del risultato:
- sui PDF reali attualmente disponibili nel repo non c'e` differenza funzionale, perche' non contengono un indice utilizzabile da questo parser
- sul caso sintetico costruito per replicare i pattern presi dal parser avanzato esterno, il branch mostra un miglioramento reale della qualita` di estrazione

## Note
- I file locali `pdf_mapping.json` e `src/be/requirement_extration/pdf_mapping.json` sono artefatti generati localmente e non fanno parte dell'intervento parser.

## Next Step
Obiettivo per il prossimo step:
- estendere il confronto verso un parser con valore distintivo reale rispetto al backend attuale

Priorita` proposta:
1. valutare integrazione controllata di `../parsing_utils/parser_gazzetta_ufficiale.py`
2. in alternativa valutare `../parsing_utils/parser_banca_d_italia.py`

Piano operativo:
- scegliere un parser specializzato da portare nel backend
- costruire almeno un fixture sintetico e, se possibile, un PDF reale rappresentativo
- misurare `main` vs branch come fatto per il parser indice
- salvare risultati in `reports/parser_comparison/` con report dedicato
