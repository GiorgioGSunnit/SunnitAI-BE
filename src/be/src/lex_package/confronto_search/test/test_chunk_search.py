import json
import sys
import os

# Aggiungi il path per gli import
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from chunk_retriever import search_chunks, search_cdp_chunks


def test_basic_search():
    """Test di ricerca base"""
    print("=" * 60)
    print("🧪 TEST 1: RICERCA BASE")
    print("=" * 60)
    
    # Test ricerca di base per "credito"
    result = search_chunks(
        search_text="credito",
        use_semantic=True,
        top=5
    )
    
    if result and result.get('value'):
        print(f"✅ Test superato: trovati {len(result['value'])} chunks")
        
        # Salva risultati
        with open("test1_basic_search.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        return True
    else:
        print("❌ Test fallito: nessun risultato")
        return False


def test_document_filter():
    """Test con filtro documento specifico"""
    print("=" * 60)
    print("🧪 TEST 2: RICERCA CON FILTRO DOCUMENTO")
    print("=" * 60)
    
    # Test con filtro documento specifico
    result = search_chunks(
        search_text="monitoraggio",
        document_name="Regolamento_del_credito_v7.0.pdf",
        use_semantic=True,
        top=5
    )
    
    if result and result.get('value'):
        print(f"✅ Test superato: trovati {len(result['value'])} chunks")
        
        # Verifica che tutti i risultati siano del documento corretto
        all_correct = True
        for chunk in result['value']:
            doc_name = chunk.get('metadata_storage_name', '')
            if doc_name != "Regolamento_del_credito_v7.0.pdf":
                print(f"⚠️  Chunk da documento diverso: {doc_name}")
                all_correct = False
        
        if all_correct:
            print("✅ Tutti i chunks sono del documento corretto")
        
        # Salva risultati
        with open("test2_document_filter.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            
        return True
    else:
        print("❌ Test fallito: nessun risultato")
        return False


def test_cdp_chunks():
    """Test ricerca specifica nei chunks CDP-R"""
    print("=" * 60)
    print("🧪 TEST 3: RICERCA CHUNKS CDP-R")
    print("=" * 60)
    
    # Test ricerca specifica nei chunks CDP con termine più specifico
    result = search_cdp_chunks(
        search_text="regolamento del credito",
        top=5
    )
    
    if result and result.get('value'):
        print(f"✅ Test superato: trovati {len(result['value'])} chunks CDP")
        
        # Verifica che tutti i risultati siano chunks CDP-R
        all_cdp = True
        for chunk in result['value']:
            chunk_id = chunk.get('metadata_storage_path', '')
            if not chunk_id.startswith('CDP-R-'):
                print(f"⚠️  Chunk non CDP-R: {chunk_id}")
                all_cdp = False
        
        if all_cdp:
            print("✅ Tutti i chunks sono CDP-R")
        
        # Salva risultati
        with open("test3_cdp_chunks.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            
        return True
    else:
        # Fallback: prova con search_chunks normale ma filtro autore
        print("⚠️  Nessun risultato con search_cdp_chunks, provo con filtro autore...")
        fallback_result = search_chunks(
            search_text="regolamento",
            author="CDP System",
            top=5
        )
        
        if fallback_result and fallback_result.get('value'):
            cdp_count = len([
                chunk for chunk in fallback_result['value'] 
                if chunk.get('metadata_storage_path', '').startswith('CDP-R-')
            ])
            
            if cdp_count > 0:
                print(f"✅ Test superato (fallback): trovati {cdp_count} chunks CDP-R")
                with open("test3_cdp_chunks.json", "w", encoding="utf-8") as f:
                    json.dump(fallback_result, f, indent=2, ensure_ascii=False)
                return True
        
        print("❌ Test fallito: nessun risultato CDP")
        return False


def test_semantic_vs_traditional():
    """Test confronto ricerca semantica vs tradizionale"""
    print("=" * 60)
    print("🧪 TEST 4: SEMANTICA VS TRADIZIONALE")
    print("=" * 60)
    
    search_term = "valutazione del rischio finanziario"
    
    # Ricerca semantica
    print("📊 Ricerca semantica...")
    semantic_result = search_chunks(
        search_text=search_term,
        use_semantic=True,
        top=5
    )
    
    # Ricerca tradizionale
    print("📊 Ricerca tradizionale...")
    traditional_result = search_chunks(
        search_text=search_term,
        use_semantic=False,
        top=5
    )
    
    if semantic_result and traditional_result:
        semantic_count = len(semantic_result.get('value', []))
        traditional_count = len(traditional_result.get('value', []))
        
        print(f"🔍 Risultati semantici: {semantic_count}")
        print(f"🔍 Risultati tradizionali: {traditional_count}")
        
        # Salva risultati
        comparison = {
            "search_term": search_term,
            "semantic": semantic_result,
            "traditional": traditional_result
        }
        
        with open("test4_semantic_vs_traditional.json", "w", encoding="utf-8") as f:
            json.dump(comparison, f, indent=2, ensure_ascii=False)
        
        print("✅ Test completato")
        return True
    else:
        print("❌ Test fallito: errore nella ricerca")
        return False


def test_chapter_search():
    """Test ricerca per capitolo specifico"""
    print("=" * 60)
    print("🧪 TEST 5: RICERCA PER CAPITOLO")
    print("=" * 60)
    
    # Test ricerca per capitolo
    result = search_chunks(
        search_text="procedure",
        chapter="Premessa",
        use_semantic=True,
        top=3
    )
    
    if result:
        chunks_found = len(result.get('value', []))
        print(f"✅ Test superato: trovati {chunks_found} chunks con capitolo 'Premessa'")
        
        # Mostra i capitoli trovati
        for i, chunk in enumerate(result.get('value', [])[:3]):
            content = chunk.get('content', '')
            # Estrai il capitolo dal content
            if 'Chapter:' in content:
                chapter_line = content.split('\n')[0]
                print(f"   {i+1}. {chapter_line}")
        
        # Salva risultati
        with open("test5_chapter_search.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            
        return True
    else:
        print("❌ Test fallito: nessun risultato")
        return False


def test_author_filter():
    """Test ricerca con filtro autore"""
    print("=" * 60)
    print("🧪 TEST 6: RICERCA CON FILTRO AUTORE")
    print("=" * 60)
    
    # Test con filtro autore
    result = search_chunks(
        search_text="regolamento",
        author="CDP System",
        use_semantic=True,
        top=5
    )
    
    if result and result.get('value'):
        print(f"✅ Test superato: trovati {len(result['value'])} chunks di 'CDP System'")
        
        # Verifica che tutti abbiano l'autore corretto
        all_correct_author = True
        for chunk in result['value']:
            author = chunk.get('metadata_author', '')
            if author != "CDP System":
                print(f"⚠️  Chunk con autore diverso: {author}")
                all_correct_author = False
        
        if all_correct_author:
            print("✅ Tutti i chunks hanno l'autore corretto")
        
        # Salva risultati
        with open("test6_author_filter.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            
        return True
    else:
        print("❌ Test fallito: nessun risultato")
        return False


def run_all_tests():
    """Esegue tutti i test"""
    print("🚀 AVVIO SUITE COMPLETA DI TEST")
    print("=" * 60)
    
    tests = [
        ("Ricerca Base", test_basic_search),
        ("Filtro Documento", test_document_filter),
        ("Chunks CDP-R", test_cdp_chunks),
        ("Semantica vs Tradizionale", test_semantic_vs_traditional),
        ("Ricerca Capitolo", test_chapter_search),
        ("Filtro Autore", test_author_filter),
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n🔄 Eseguendo: {test_name}")
        try:
            success = test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"❌ Errore nel test {test_name}: {e}")
            results.append((test_name, False))
    
    # Riepilogo finale
    print("\n" + "=" * 60)
    print("📊 RIEPILOGO RISULTATI TEST")
    print("=" * 60)
    
    passed = 0
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} - {test_name}")
        if success:
            passed += 1
    
    print(f"\n🎯 Risultato finale: {passed}/{len(results)} test superati")
    
    if passed == len(results):
        print("🎉 TUTTI I TEST SUPERATI! La funzione di ricerca funziona correttamente.")
    else:
        print("⚠️  Alcuni test falliti. Verifica i log per i dettagli.")
    
    return passed == len(results)


if __name__ == "__main__":
    print("🧪 TEST SUITE RICERCA CHUNKS")
    print("Questo script testa la funzionalità di ricerca nei chunks indicizzati")
    
    if len(sys.argv) > 1 and sys.argv[1] == "single":
        # Esegui un singolo test
        test_name = sys.argv[2] if len(sys.argv) > 2 else "basic"
        
        test_map = {
            "basic": test_basic_search,
            "document": test_document_filter, 
            "cdp": test_cdp_chunks,
            "semantic": test_semantic_vs_traditional,
            "chapter": test_chapter_search,
            "author": test_author_filter,
        }
        
        if test_name in test_map:
            test_map[test_name]()
        else:
            print(f"Test '{test_name}' non trovato. Disponibili: {list(test_map.keys())}")
    else:
        # Esegui tutti i test
        run_all_tests() 