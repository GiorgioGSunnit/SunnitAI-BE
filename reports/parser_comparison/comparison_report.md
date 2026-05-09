# Parser Comparison

## Scope
- confronto tra `main` e `feat/parser-quality-performance` sul parser indice
- stesso interprete Python e stesso ambiente virtuale
- stessi PDF di input

## Input
- `NEW_1to5.pdf`
- `OLD_1to5.pdf`
- `generated_index_case.pdf`

## Summary Table

| PDF | main ms | branch ms | delta ms | main entries | branch entries |
| --- | ---: | ---: | ---: | ---: | ---: |
| `NEW_1to5.pdf` | 201.915 | 148.043 | -53.872 | 0 | 0 |
| `OLD_1to5.pdf` | 141.91 | 144.503 | 2.593 | 0 | 0 |
| `generated_index_case.pdf` | 128.367 | 133.601 | 5.234 | 1 | 2 |

## Detailed Comparison

### NEW_1to5.pdf
- main entries: 0
- branch entries: 0
- main time: 201.915 ms
- branch time: 148.043 ms
- delta: -53.872 ms
- main titles: []
- branch titles: []

### OLD_1to5.pdf
- main entries: 0
- branch entries: 0
- main time: 141.91 ms
- branch time: 144.503 ms
- delta: 2.593 ms
- main titles: []
- branch titles: []

### generated_index_case.pdf
- main entries: 1
- branch entries: 2
- main time: 128.367 ms
- branch time: 133.601 ms
- delta: 5.234 ms
- main titles: ['Ambito di applicazione']
- branch titles: ['Ambito di applicazione', 'Capitolo 7 Requisiti operativi']
