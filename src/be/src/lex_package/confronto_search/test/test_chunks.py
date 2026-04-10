import sys
import os
import json

# Aggiungi il path per gli import
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from chunk_generator import convert_r_analysis_to_chunks, save_chunks_to_file
from chunk_retriever import search_chunks, search_cdp_chunks, get_chunk_by_id


def test_chunk_generator():
    """Test generazione chunks dal file R.json"""
    print("🧪 TEST 1: GENERAZIONE CHUNKS")
    print("-" * 40)
    
    try:
        # Genera chunks
        chunks = convert_r_analysis_to_chunks()
        
        if chunks and len(chunks) > 0:
            print(f"✅ Generati {len(chunks)} chunks")
            
            # Verifica struttura primo chunk
            if chunks[0]:
                required_fields = ['id', 'fileName', 'chapter', 'section', 'content', 'page']
                has_all_fields = all(field in chunks[0] for field in required_fields)
                
                if has_all_fields:
                    print("✅ Struttura chunks corretta")
                    
                    # Salva chunks di test
                    save_chunks_to_file(chunks, "test_chunks.json")
                    print("💾 Chunks salvati in test_chunks.json")
                    
                    return True
                else:
                    print("❌ Struttura chunks incompleta")
                    return False
            else:
                print("❌ Primo chunk vuoto")
                return False
        else:
            print("❌ Nessun chunk generato")
            return False
            
    except Exception as e:
        print(f"❌ Errore nella generazione: {e}")
        return False


def test_chunk_indexer():
    """Test indicizzazione chunks (verifica se script può essere importato)"""
    print("\n🧪 TEST 2: INDICIZZAZIONE CHUNKS")
    print("-" * 40)
    
    try:
        # Importa e verifica che le funzioni esistano
        from chunk_indexer import prepare_document_for_indexing, load_chunks
        
        # Test con chunk di esempio
        test_chunk = {
            "id": "test-chunk-001",
            "fileName": "test.pdf",
            "chapter": "Test Chapter",
            "section": "Test Section",
            "content": "Test content",
            "page": 1
        }
        
        # Verifica preparazione documento
        doc = prepare_document_for_indexing(test_chunk)
        
        required_fields = ['@search.action', 'metadata_storage_path', 'metadata_storage_name', 'content', 'metadata_author']
        has_all_fields = all(field in doc for field in required_fields)
        
        if has_all_fields:
            print("✅ Preparazione documento corretta")
            print(f"   Chiave: {doc['metadata_storage_path']}")
            print(f"   Autore: {doc['metadata_author']}")
            return True
        else:
            print("❌ Preparazione documento fallita")
            return False
            
    except Exception as e:
        print(f"❌ Errore nell'importazione indexer: {e}")
        return False


def test_chunk_retriever():
    """Test recupero chunks da Azure Search"""
    print("\n🧪 TEST 3: RECUPERO CHUNKS")
    print("-" * 40)
    
    try:
        # Test ricerca generica
        result = search_chunks("regolamento", author="CDP System", top=3)
        
        if result and result.get('value'):
            chunks_found = len(result['value'])
            print(f"✅ Ricerca generica: {chunks_found} chunks trovati")
            
            # Test ricerca chunks CDP-R specifici
            cdp_result = search_cdp_chunks("credito", top=3)
            
            if cdp_result and cdp_result.get('value'):
                cdp_chunks = len(cdp_result['value'])
                print(f"✅ Ricerca CDP-R: {cdp_chunks} chunks trovati")
                
                # Test recupero chunk specifico
                if cdp_result['value']:
                    chunk_id = cdp_result['value'][0].get('metadata_storage_path')
                    specific_chunk = get_chunk_by_id(chunk_id)
                    
                    if specific_chunk:
                        print(f"✅ Recupero specifico: chunk {chunk_id}")
                        return True
                    else:
                        print("❌ Recupero specifico fallito")
                        return False
                else:
                    print("⚠️  Nessun chunk CDP-R per test specifico")
                    return True
            else:
                print("⚠️  Nessun chunk CDP-R trovato")
                return True
        else:
            print("❌ Nessun risultato dalla ricerca")
            return False
            
    except Exception as e:
        print(f"❌ Errore nel recupero: {e}")
        return False


def run_all_tests():
    """Esegue tutti i test"""
    print("🚀 SUITE TEST CHUNKS")
    print("=" * 50)
    
    tests = [
        ("Generazione Chunks", test_chunk_generator),
        ("Indicizzazione Chunks", test_chunk_indexer),
        ("Recupero Chunks", test_chunk_retriever),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            success = test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"❌ Errore nel test {test_name}: {e}")
            results.append((test_name, False))
    
    # Riepilogo
    print("\n" + "=" * 50)
    print("📊 RIEPILOGO RISULTATI")
    print("=" * 50)
    
    passed = 0
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} - {test_name}")
        if success:
            passed += 1
    
    print(f"\n🎯 Risultato: {passed}/{len(results)} test superati")
    
    if passed == len(results):
        print("🎉 TUTTI I TEST SUPERATI!")
    else:
        print("⚠️  Alcuni test falliti. Verifica la configurazione.")
    
    return passed == len(results)


if __name__ == "__main__":
    run_all_tests() 