import json
import os
from typing import List, Dict, Any


def convert_r_analysis_to_chunks() -> List[Dict[str, Any]]:
    """
    Converte il file R.json in chunks per Azure Cognitive Search.

    Prende il json in out_analisi/R.json e lo converte nel formato richiesto
    per l'indicizzazione in Azure Cognitive Search.
    """
    # Carica il file R.json
    json_path = os.path.join(os.path.dirname(__file__), "../../out_analisi/R.json")

    with open(json_path, "r", encoding="utf-8") as f:
        analisi = json.load(f)

    chunks = []

    # Itera su ogni elemento A di analisi
    for m, A in enumerate(analisi):
        # Itera su ogni elemento pA di contenuto_parsato
        for n, pA in enumerate(A.get("contenuto_parsato", [])):
            # Crea il JSON seguendo il template specificato
            chunk = {
                "id": f"doc-R-chunk-{n}{m}",
                "fileName": "Regolamento_del_credito_v7.0.pdf",
                "chapter": pA.get("titolo_articolo", ""),
                "section": A.get("identificativo", ""),
                "content": pA.get("contenuto", ""),
                "page": A.get("page", 0),
            }

            chunks.append(chunk)

    return chunks


def save_chunks_to_file(
    chunks: List[Dict[str, Any]], output_file: str = "r_chunks.json"
):
    """
    Salva i chunks in un file JSON.
    """
    output_path = os.path.join(os.path.dirname(__file__), output_file)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)

    print(f"Salvati {len(chunks)} chunks in {output_path}")


if __name__ == "__main__":
    # Converte e salva i chunks
    chunks = convert_r_analysis_to_chunks()
    save_chunks_to_file(chunks)

    # Stampa statistiche
    print(f"Totale chunks creati: {len(chunks)}")
    if chunks:
        print(f"Esempio del primo chunk:")
        print(json.dumps(chunks[0], ensure_ascii=False, indent=2))
