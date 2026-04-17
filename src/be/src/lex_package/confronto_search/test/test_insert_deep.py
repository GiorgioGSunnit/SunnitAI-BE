"""
Test per la funzione insert_deep.

Questo modulo contiene test per verificare che la funzione insert_deep
funzioni correttamente come operazione inversa di index_contents.
"""

import json
from typing import Dict, List, Any

from ..utils import (
    index_contents,
    insert_deep,
    insert_deep_with_path,
    extract_and_reinsert,
)


def test_simple_list_structure():
    """Test con una struttura molto semplice per verificare il funzionamento base."""
    print("\n=== Test Simple List Structure ===")

    # Struttura molto semplice
    test_data = [{"name": "Alice"}, {"name": "Bob"}]
    path = ["name"]

    print(f"Original data: {json.dumps(test_data, indent=2)}")

    # Test con la funzione extract_and_reinsert
    reconstructed = extract_and_reinsert(path, test_data)
    print(f"Reconstructed: {json.dumps(reconstructed, indent=2)}")

    # Questo dovrebbe funzionare
    assert reconstructed == test_data, (
        f"Simple list test fallito: {reconstructed} != {test_data}"
    )
    print("✅ Simple list test passato!")


def test_single_level_dict():
    """Test con un singolo livello di dict."""
    print("\n=== Test Single Level Dict ===")

    test_data = {"title": "My Title", "content": "My Content"}
    path = ["title"]

    print(f"Original data: {json.dumps(test_data, indent=2)}")

    reconstructed = extract_and_reinsert(path, test_data)
    print(f"Reconstructed: {json.dumps(reconstructed, indent=2)}")

    assert reconstructed == test_data, (
        f"Single dict test fallito: {reconstructed} != {test_data}"
    )
    print("✅ Single dict test passato!")


def test_empty_and_edge_cases():
    """Test per casi limite ed edge cases."""
    print("\n=== Test Edge Cases ===")

    # Test con lista vuota
    test_data_empty = []
    path_empty = ["name"]

    print("\nTest lista vuota:")
    reconstructed_empty = extract_and_reinsert(path_empty, test_data_empty)

    # Se la lista è vuota, dovrebbe rimanere vuota
    assert reconstructed_empty == test_data_empty, f"Lista vuota test fallito"
    print("✅ Lista vuota OK")

    # Test con dict vuoto
    test_data_empty_dict = {}
    reconstructed_empty_dict = extract_and_reinsert(path_empty, test_data_empty_dict)

    assert reconstructed_empty_dict == test_data_empty_dict, f"Dict vuoto test fallito"
    print("✅ Dict vuoto OK")


def test_with_modified_values():
    """Test inserendo valori modificati per verificare che la funzione funzioni."""
    print("\n=== Test With Modified Values ===")

    test_data = [{"name": "Alice", "age": 25}, {"name": "Bob", "age": 30}]
    path = ["name"]

    print(f"Original data: {json.dumps(test_data, indent=2)}")

    values, indexes = index_contents(path, test_data)
    print(f"Original values: {values}")

    # Modifica i valori
    modified_values = ["Charlie", "David"]
    print(f"Modified values: {modified_values}")

    result = insert_deep_with_path(modified_values, indexes, test_data, path)
    print(f"Result with modified values: {json.dumps(result, indent=2)}")

    # Verifica che i nomi siano stati cambiati
    expected_result = [{"name": "Charlie", "age": 25}, {"name": "David", "age": 30}]

    assert result == expected_result, (
        f"Test con valori modificati fallito: {result} != {expected_result}"
    )
    print("✅ Test con valori modificati passato!")


def test_nested_structure():
    """Test con struttura più complessa e annidata."""
    print("\n=== Test Nested Structure ===")

    test_data = {
        "users": [
            {
                "profile": {"name": "Alice", "role": "admin"},
                "settings": {"theme": "dark"},
            },
            {
                "profile": {"name": "Bob", "role": "user"},
                "settings": {"theme": "light"},
            },
        ]
    }
    path = ["users", "profile", "name"]

    print(f"Original data: {json.dumps(test_data, indent=2)}")

    reconstructed = extract_and_reinsert(path, test_data)
    print(f"Reconstructed: {json.dumps(reconstructed, indent=2)}")

    assert reconstructed == test_data, (
        f"Nested structure test fallito: {reconstructed} != {test_data}"
    )
    print("✅ Nested structure test passato!")


def test_complex_real_world_structure():
    """Test con la struttura JSON complessa del dominio reale."""
    print("\n=== Test Complex Real World Structure ===")

    json_data = [
        {
            "codicedocumento": "",
            "page": 1,
            "identificativo": "",
            "titolo": "1. Conformità",
            "codicearticolo": "",
            "contenuto": "",
            "contenuto_parsato": [
                {
                    "titolo_articolo": "Status giuridico",
                    "contenuto_parsato_2": [
                        {
                            "identificativo": "1.",
                            "contenuto": "Documento conforme art. 16 reg. UE 1093/2010.",
                            "flag": False,
                            "requirement": "Enti e autorità devono conformarsi.",
                            "core_text": "Enti e autorità conformi a reg. UE 1093/2010.",
                            "search_text": "Obbligo conformità reg. 1093/2010.",
                            "pattern_type": "obbligo",
                            "riferimenti": [],
                        },
                        {
                            "identificativo": "1.",
                            "contenuto": "Documento conforme art. 16 reg. UE 1093/2010.",
                            "flag": False,
                            "requirement": "Enti e autorità devono conformarsi.",
                            "core_text": "Enti e autorità conformi a reg. UE 1093/2010.",
                            "search_text": "Obbligo conformità reg. 1093/2010.",
                            "pattern_type": "obbligo",
                            "riferimenti": [],
                        },
                    ],
                },
                {
                    "titolo_articolo": "Obblighi notifica",
                    "contenuto_parsato_2": [
                        {
                            "identificativo": "2.",
                            "contenuto": "Autorità notifichino all'ABE entro 27/08/20.",
                            "flag": False,
                            "requirement": "Notifica conformità o motivazione.",
                            "core_text": "Notifica entro 27/08/20.",
                            "search_text": "Scadenza notifica 27/08/2020.",
                            "pattern_type": "obbligo",
                            "riferimenti": [],
                        }
                    ],
                },
            ],
        },
        {
            "page": 2,
            "titolo": "2. Ambito e definizioni",
            "contenuto_parsato": [
                {
                    "titolo_articolo": "Ambito",
                    "contenuto_parsato_2": [
                        {
                            "identificativo": "3.",
                            "contenuto": "Orientamenti applicabili a enti e creditori.",
                            "flag": False,
                            "requirement": "Si applica a enti come da reg. 575/2013.",
                            "core_text": "Applicazione ad enti (reg. 575/2013).",
                            "search_text": "Ambito: enti.",
                            "pattern_type": "condizione",
                            "riferimenti": [],
                        }
                    ],
                },
                {
                    "titolo_articolo": "Destinatari",
                    "contenuto_parsato_2": [
                        {
                            "identificativo": "4.",
                            "contenuto": "Orientamenti rivolti a autorità e enti.",
                            "flag": False,
                            "requirement": "Destinatari: enti finanziari e autorità.",
                            "core_text": "Rivolto a enti e autorità (reg. 1093/2010).",
                            "search_text": "Destinatari: autorità e enti.",
                            "pattern_type": "altro",
                            "riferimenti": [],
                        }
                    ],
                },
            ],
        },
    ]

    path = ["contenuto_parsato", "contenuto_parsato_2", "search_text"]

    print(f"Testing path: {path}")

    # Prima verifichiamo che index_contents funzioni correttamente
    values, indexes = index_contents(path, json_data)
    print(f"Extracted values: {values}")
    print(f"Number of values: {len(values)}")
    expected_values = [
        "Obbligo conformità reg. 1093/2010.",
        "Obbligo conformità reg. 1093/2010.",
        "Scadenza notifica 27/08/2020.",
        "Ambito: enti.",
        "Destinatari: autorità e enti.",
    ]
    assert values == expected_values, (
        f"Values extracted incorrectly: {values} != {expected_values}"
    )
    print("✅ Values extraction correct!")

    # Ora test dell'identity property
    reconstructed = extract_and_reinsert(path, json_data)

    # Verifica che la struttura sia identica
    assert reconstructed == json_data, f"Complex structure identity test fallito"
    print("✅ Complex real world structure identity test passato!")


# test identity property


def test_identity_property_variations():
    """Test aggiuntivi per l'identity property con varie strutture."""
    print("\n=== Test Identity Property Variations ===")

    # Test 1: Array di stringhe semplici con wrapper object
    test_case_1 = {"messages": [{"text": "Hello"}, {"text": "World"}, {"text": "Test"}]}
    path_1 = ["messages", "text"]

    print("\nVariation 1: Simple array of objects with single field")
    reconstructed_1 = extract_and_reinsert(path_1, test_case_1)
    assert reconstructed_1 == test_case_1, f"Variation 1 fallita"
    print("✅ Variation 1 passata!")

    # Test 2: Struttura molto annidata
    test_case_2 = {
        "company": {
            "departments": [
                {
                    "name": "Engineering",
                    "teams": [
                        {
                            "lead": {"name": "Alice", "level": "Senior"},
                            "members": [
                                {"name": "Bob", "role": "Developer"},
                                {"name": "Charlie", "role": "Tester"},
                            ],
                        }
                    ],
                },
                {
                    "name": "Marketing",
                    "teams": [
                        {
                            "lead": {"name": "Diana", "level": "Manager"},
                            "members": [{"name": "Eve", "role": "Analyst"}],
                        }
                    ],
                },
            ]
        }
    }
    path_2 = ["company", "departments", "teams", "members", "name"]

    print("\nVariation 2: Deeply nested structure")
    reconstructed_2 = extract_and_reinsert(path_2, test_case_2)
    assert reconstructed_2 == test_case_2, f"Variation 2 fallita"
    print("✅ Variation 2 passata!")

    # Test 3: Array root con path semplice
    test_case_3 = [
        {"id": 1, "status": "active"},
        {"id": 2, "status": "inactive"},
        {"id": 3, "status": "pending"},
    ]
    path_3 = ["status"]

    print("\nVariation 3: Root array with simple path")
    reconstructed_3 = extract_and_reinsert(path_3, test_case_3)
    assert reconstructed_3 == test_case_3, f"Variation 3 fallita"
    print("✅ Variation 3 passata!")

    # Test 4: Mixed types and structures
    test_case_4 = {
        "config": {
            "environments": [
                {
                    "name": "production",
                    "services": [
                        {"type": "web", "count": 3},
                        {"type": "api", "count": 2},
                    ],
                },
                {"name": "staging", "services": [{"type": "web", "count": 1}]},
            ]
        }
    }
    path_4 = ["config", "environments", "services", "type"]

    print("\nVariation 4: Mixed structure with different array sizes")
    reconstructed_4 = extract_and_reinsert(path_4, test_case_4)
    assert reconstructed_4 == test_case_4, f"Variation 4 fallita"
    print("✅ Variation 4 passata!")

    print("✅ All identity property variations passed!")


def test_identity_with_complex_values():
    """Test identity property con valori complessi (non solo stringhe)."""
    print("\n=== Test Identity with Complex Values ===")

    test_data = {
        "transactions": [
            {
                "details": {
                    "amount": 100.50,
                    "currency": "EUR",
                    "metadata": {"source": "online", "fee": 2.5},
                }
            },
            {
                "details": {
                    "amount": 250.00,
                    "currency": "USD",
                    "metadata": {"source": "branch", "fee": 5.0},
                }
            },
        ]
    }

    # Test con valori numerici
    path_amount = ["transactions", "details", "amount"]
    reconstructed_amount = extract_and_reinsert(path_amount, test_data)
    assert reconstructed_amount == test_data, f"Amount extraction identity failed"
    print("✅ Numeric values identity test passed!")

    # Test con oggetti complessi (metadata)
    path_metadata = ["transactions", "details", "metadata"]
    reconstructed_metadata = extract_and_reinsert(path_metadata, test_data)
    assert reconstructed_metadata == test_data, (
        f"Complex object extraction identity failed"
    )
    print("✅ Complex object values identity test passed!")


def test_identity_property():
    """
    Test principale: verifica che insert_deep_with_path(index_contents(path, json_data), json_data, path)
    restituisca un json con gli stessi contenuti di json_data.
    """
    print("\n=== Test Identity Property ===")

    # Test case 1: Struttura semplice con lista
    test_data_1 = {
        "items": [
            {"title": "Item 1", "content": "Content 1"},
            {"title": "Item 2", "content": "Content 2"},
        ]
    }
    path_1 = ["items", "title"]

    print("\nTest 1: Struttura semplice con lista")
    print(f"Original data: {json.dumps(test_data_1, indent=2)}")

    # Test con extract_and_reinsert
    reconstructed_1 = extract_and_reinsert(path_1, test_data_1)
    print(f"Reconstructed: {json.dumps(reconstructed_1, indent=2)}")

    # Verifica che siano uguali
    assert reconstructed_1 == test_data_1, (
        f"Test 1 fallito: {reconstructed_1} != {test_data_1}"
    )
    print("✅ Test 1 passato!")

    # Test case 2: Struttura annidata
    test_data_2 = {
        "sections": [
            {
                "name": "Section A",
                "chapters": [
                    {"title": "Chapter 1", "pages": 10},
                    {"title": "Chapter 2", "pages": 15},
                ],
            },
            {"name": "Section B", "chapters": [{"title": "Chapter 3", "pages": 20}]},
        ]
    }
    path_2 = ["sections", "chapters", "title"]

    print("\nTest 2: Struttura annidata")
    print(f"Original data: {json.dumps(test_data_2, indent=2)}")

    reconstructed_2 = extract_and_reinsert(path_2, test_data_2)
    print(f"Reconstructed: {json.dumps(reconstructed_2, indent=2)}")

    assert reconstructed_2 == test_data_2, (
        f"Test 2 fallito: {reconstructed_2} != {test_data_2}"
    )
    print("✅ Test 2 passato!")

    # Test case 3: Struttura semplice con dict e lista di un livello
    test_data_3 = [{"name": "John", "age": 30}, {"name": "Jane", "age": 25}]
    path_3 = ["name"]  # Estrae i nomi dalla lista

    print("\nTest 3: Lista di dict con path semplice")
    print(f"Original data: {json.dumps(test_data_3, indent=2)}")

    reconstructed_3 = extract_and_reinsert(path_3, test_data_3)
    print(f"Reconstructed: {json.dumps(reconstructed_3, indent=2)}")

    assert reconstructed_3 == test_data_3, (
        f"Test 3 fallito: {reconstructed_3} != {test_data_3}"
    )
    print("✅ Test 3 passato!")


def run_all_tests():
    """Esegue tutti i test."""
    print("Avvio test per insert_deep")
    print("=" * 50)

    try:
        test_simple_list_structure()
        test_single_level_dict()
        test_empty_and_edge_cases()
        test_with_modified_values()
        test_nested_structure()
        test_complex_real_world_structure()
        test_identity_property_variations()
        test_identity_with_complex_values()
        test_identity_property()

        print("\n" + "=" * 50)
        print("✅ Tutti i test completati con successo!")

    except Exception as e:
        print(f"\n❌ Errore durante i test: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    run_all_tests()

