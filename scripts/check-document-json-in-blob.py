"""
Script per verificare se il JSON di analisi di un documento è presente nel blob storage
e per scaricarlo. Eseguito da repo root: python scripts/check-document-json-in-blob.py <nome_documento_senza_pdf>

Uso:
  python scripts/check-document-json-in-blob.py [nome_documento_senza_pdf]
  - senza argomenti: elenca tutti i JSON in out/requirements/
  - con nome: cerca blob e scarica JSON + XLSX (stesso stem)
  - --output-dir DIR: cartella in cui salvare i file (creata se non esiste)
"""
import argparse
import json
import sys
from pathlib import Path

# Permetti import da src
_repo_root = Path(__file__).resolve().parents[1]
_src = _repo_root / "src"
if _src.exists() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

def main():
    parser = argparse.ArgumentParser(description="Verifica e scarica JSON analisi da blob (out/requirements/)")
    parser.add_argument("document_name", nargs="?", help="Nome documento senza .pdf")
    parser.add_argument("-o", "--output", help="File di output per il JSON scaricato")
    parser.add_argument("--output-dir", help="Cartella in cui salvare JSON e XLSX (creata se non esiste)")
    args = parser.parse_args()

    try:
        from utils import blob_storage_client as bsc
    except ImportError:
        print("ERRORE: non trovo utils.blob_storage_client. Esegui da repo root con: python scripts/check-document-json-in-blob.py ...")
        sys.exit(1)

    if not bsc.is_available():
        print("ERRORE: Blob storage non configurato (AZURE_STORAGE_ACCOUNT_NAME / BLOB_CONTAINER_NAME o DefaultAzureCredential)")
        sys.exit(1)

    out_dir = Path(args.output_dir) if args.output_dir else Path.cwd()
    if args.output_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    container = bsc.get_container_client()
    prefix = bsc.path_out_requirements("")  # "out/requirements/"
    blobs = list(container.list_blobs(name_starts_with=prefix))

    if not blobs:
        print(f"Nessun blob trovato sotto {prefix}")
        return

    # Solo file .json
    json_blobs = [b for b in blobs if b.name.endswith(".json") and not b.name.endswith("/")]
    print(f"Trovati {len(json_blobs)} file JSON in {prefix}")

    if not args.document_name:
        for b in sorted(json_blobs, key=lambda x: x.last_modified or "", reverse=True)[:30]:
            name = Path(b.name).name
            size = b.size or 0
            modified = b.last_modified.strftime("%Y-%m-%d %H:%M") if b.last_modified else "-"
            print(f"  {name}  ({size} bytes, {modified})")
        return

    # Cerca per nome documento: il mapping nome->hash è nel pod, qui possiamo solo elencare e scaricare per nome file
    # Se conosci l'hash del PDF puoi passare hash.json; altrimenti scarichiamo l'ultimo modificato o cerchiamo nel contenuto
    doc_lower = args.document_name.replace(".pdf", "").lower()
    found = None
    for b in json_blobs:
        name = Path(b.name).name
        if doc_lower in name.lower():
            found = b
            break
    if not found:
        # Prendi l'ultimo modificato (potrebbe essere il documento appena analizzato)
        found = max(json_blobs, key=lambda x: x.last_modified or x.creation_time)
        print(f"Nessun blob con nome contenente '{args.document_name}'. Uso l'ultimo modificato: {found.name}")

    blob_path = found.name
    stem = Path(blob_path).stem
    print(f"Download JSON: {blob_path}")
    blob_client = container.get_blob_client(blob_path)
    content = blob_client.download_blob().readall().decode("utf-8")
    data = json.loads(content)

    json_out = out_dir / (args.output or f"{stem}.json")
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Salvato: {json_out}")

    # Scarica anche XLSX stesso nome se presente
    xlsx_blob = f"{prefix}{stem}.xlsx"
    xlsx_client = container.get_blob_client(xlsx_blob)
    try:
        xlsx_content = xlsx_client.download_blob().readall()
        xlsx_out = out_dir / f"{stem}.xlsx"
        with open(xlsx_out, "wb") as f:
            f.write(xlsx_content)
        print(f"Salvato: {xlsx_out}")
    except Exception as e:
        print(f"XLSX {xlsx_blob} non presente o errore: {e}")

    if isinstance(data, dict):
        n_art = len(data.get("articoli") or [])
        n_req = len(data.get("requirements") or [])
        print(f"Contenuto JSON: articoli={n_art}, requirements={n_req}")
    elif isinstance(data, list):
        print(f"Contenuto JSON: array di {len(data)} elementi")

if __name__ == "__main__":
    main()
