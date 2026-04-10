import requests
import json
from typing import Dict, Any, Optional, List
import sys
import os

# Aggiungi il path per gli import
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from chunk_retriever import search_chunks, search_cdp_chunks

# Configurazione per compatibilità con test esistenti
endpoint = "https://cdpaisearch.search.windows.net"
index_name = "azureblob-data-index"
api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY", "")
headers = {"Content-Type": "application/json", "api-key": api_key}
params = {"api-version": "2023-11-01"}


def interactive_search():
    """
    Interfaccia interattiva per la ricerca
    """
    print("=" * 60)
    print("🔍 RICERCA INTERATTIVA NEI CHUNKS")
    print("=" * 60)
    
    # Input utente
    search_text = input("📝 Inserisci il testo da cercare: ").strip()
    if not search_text:
        print("❌ Testo di ricerca obbligatorio!")
        return None
    
    document_name = input("📄 Nome documento (opzionale, es. 'Regolamento_del_credito_v7.0.pdf'): ").strip()
    chapter = input("📂 Capitolo (opzionale): ").strip()
    author = input("👤 Autore (opzionale, es. 'CDP System'): ").strip()
    
    use_semantic = input("🧠 Usare ricerca semantica? (y/N): ").strip().lower() == 'y'  # Default a False per quota
    
    try:
        top = int(input("🔢 Numero max risultati (default 10): ") or "10")
    except ValueError:
        top = 10
    
    # Esegui ricerca
    result = search_chunks(
        search_text=search_text,
        document_name=document_name if document_name else None,
        chapter=chapter if chapter else None,
        author=author if author else None,
        use_semantic=use_semantic,
        top=top
    )
    
    # Mostra risultati
    if result and result.get('value'):
        print(f"\n✅ Trovati {len(result['value'])} chunks")
        for i, chunk in enumerate(result['value'][:5]):
            chunk_id = chunk.get('metadata_storage_path', 'N/A')
            print(f"  {i+1}. {chunk_id}")
    
    # Salva risultati
    if result:
        filename = f"search_results_{search_text.replace(' ', '_')[:20]}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"💾 Risultati salvati in: {filename}")
    
    return result


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 TEST RICERCA CHUNKS AZURE SEARCH AI")
    print("=" * 60)

    # Offri opzioni
    print("Scegli modalità:")
    print("1. Ricerca interattiva")
    print("2. Test automatico")
    
    choice = input("Scegli (1/2): ").strip()
    
    if choice == "1":
        interactive_search()
    else:
        # Test automatico
        print("🤖 Esecuzione test automatico...")
        
        # Test 1: Ricerca generica
        print("\n--- Test 1: Ricerca 'credito' ---")
        result1 = search_chunks("credito", top=5)
        print(f"Risultati: {len(result1.get('value', [])) if result1 else 0}")
        
        # Test 2: Ricerca con filtro documento
        print("\n--- Test 2: Ricerca 'monitoraggio' nel regolamento ---")
        result2 = search_chunks(
            "monitoraggio", 
            document_name="Regolamento_del_credito_v7.0.pdf",
            top=3
        )
        print(f"Risultati: {len(result2.get('value', [])) if result2 else 0}")
        
        # Test 3: Ricerca semantica specifica
        print("\n--- Test 3: Ricerca chunks CDP-R ---")
        result3 = search_cdp_chunks("regolamento", top=5)
        print(f"Risultati: {len(result3.get('value', [])) if result3 else 0}")
        
        print("\n✅ Test automatici completati!")
    
    print("=" * 60)
