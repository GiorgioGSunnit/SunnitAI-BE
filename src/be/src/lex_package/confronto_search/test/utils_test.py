#!/usr/bin/env python3
"""
Test file per la funzione index_contents in utils.py
"""

from ..utils import index_contents


def test_example_from_comment():
    """Test con l'esempio fornito nel commento originale"""

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

    values, indexes = index_contents(path, json_data)

    expected_values = [
        "Obbligo conformità reg. 1093/2010.",
        "Obbligo conformità reg. 1093/2010.",
        "Scadenza notifica 27/08/2020.",
        "Ambito: enti.",
        "Destinatari: autorità e enti.",
    ]

    expected_indexes = [[0, 0, 0], [0, 0, 1], [0, 1, 0], [1, 0, 0], [1, 1, 0]]

    assert values == expected_values, (
        f"Values mismatch: expected {expected_values}, got {values}"
    )
    assert indexes == expected_indexes, (
        f"Indexes mismatch: expected {expected_indexes}, got {indexes}"
    )


def test_simple_single_level():
    """Test semplice con un singolo livello"""

    json_data = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]

    path = ["name"]

    values, indexes = index_contents(path, json_data)

    expected_values = ["Alice", "Bob"]
    expected_indexes = [[0], [1]]

    assert values == expected_values
    assert indexes == expected_indexes


def test_nested_dict():
    """Test con dict annidati"""

    json_data = {
        "users": [
            {"profile": {"email": "alice@test.com"}},
            {"profile": {"email": "bob@test.com"}},
        ]
    }

    path = ["users", "profile", "email"]

    values, indexes = index_contents(path, json_data)

    expected_values = ["alice@test.com", "bob@test.com"]
    expected_indexes = [[0, 0, 0], [0, 1, 0]]

    assert values == expected_values
    assert indexes == expected_indexes


def test_empty_result():
    """Test quando il path non esiste"""

    json_data = [{"name": "Alice", "age": 30}]

    path = ["nonexistent", "field"]

    values, indexes = index_contents(path, json_data)

    expected_values = []
    expected_indexes = []

    assert values == expected_values
    assert indexes == expected_indexes


def test_multiple_items_same_level():
    """Test con più elementi allo stesso livello in una lista"""

    json_data = [{"items": [{"value": "A"}, {"value": "B"}, {"value": "C"}]}]

    path = ["items", "value"]

    values, indexes = index_contents(path, json_data)

    expected_values = ["A", "B", "C"]
    expected_indexes = [[0, 0], [0, 1], [0, 2]]

    assert values == expected_values
    assert indexes == expected_indexes


def test_deep_nesting():
    """Test con annidamento molto profondo"""

    json_data = [
        {"level1": [{"level2": [{"level3": [{"value": "deep1"}, {"value": "deep2"}]}]}]}
    ]

    path = ["level1", "level2", "level3", "value"]

    values, indexes = index_contents(path, json_data)

    expected_values = ["deep1", "deep2"]
    expected_indexes = [[0, 0, 0, 0], [0, 0, 0, 1]]

    assert values == expected_values
    assert indexes == expected_indexes


def test_mixed_structure():
    """Test con struttura mista: liste e dict a livelli diversi"""

    json_data = {
        "departments": [
            {"name": "IT", "employees": [{"role": "developer"}, {"role": "tester"}]},
            {"name": "HR", "employees": [{"role": "recruiter"}]},
        ]
    }

    path = ["departments", "employees", "role"]

    values, indexes = index_contents(path, json_data)

    expected_values = ["developer", "tester", "recruiter"]
    expected_indexes = [[0, 0, 0], [0, 0, 1], [0, 1, 0]]

    assert values == expected_values
    assert indexes == expected_indexes


def test_single_dict():
    """Test con un singolo dizionario (non lista)"""

    json_data = {"user": {"name": "John", "age": 25}}

    path = ["user", "name"]

    values, indexes = index_contents(path, json_data)

    expected_values = ["John"]
    expected_indexes = [[0, 0]]

    assert values == expected_values
    assert indexes == expected_indexes


def test_sparse_data():
    """Test con dati sparsi (alcuni elementi non hanno la chiave)"""

    json_data = [
        {"name": "Alice", "age": 30},
        {"age": 25},  # Manca "name"
        {"name": "Bob", "age": 35},
    ]

    path = ["name"]

    values, indexes = index_contents(path, json_data)

    expected_values = ["Alice", "Bob"]
    expected_indexes = [[0], [2]]

    assert values == expected_values
    assert indexes == expected_indexes


def test_multiple_paths_same_structure():
    """Test estraendo valori diversi dalla stessa struttura"""

    json_data = [
        {"name": "Alice", "age": 30, "city": "New York"},
        {"name": "Bob", "age": 25, "city": "London"},
    ]

    # Test per "name"
    path_name = ["name"]
    values_name, indexes_name = index_contents(path_name, json_data)

    # Test per "city"
    path_city = ["city"]
    values_city, indexes_city = index_contents(path_city, json_data)

    expected_values_name = ["Alice", "Bob"]
    expected_indexes_name = [[0], [1]]

    expected_values_city = ["New York", "London"]
    expected_indexes_city = [[0], [1]]

    assert values_name == expected_values_name
    assert indexes_name == expected_indexes_name
    assert values_city == expected_values_city
    assert indexes_city == expected_indexes_city


if __name__ == "__main__":
    try:
        test_simple_single_level()
        test_nested_dict()
        test_empty_result()
        test_multiple_items_same_level()
        test_example_from_comment()
        test_deep_nesting()
        test_mixed_structure()
        test_single_dict()
        test_sparse_data()
        test_multiple_paths_same_structure()
        print("✅ all tests succeded")

    except Exception as e:
        import traceback

        print("test failure: ", e)

        traceback.print_exc()
