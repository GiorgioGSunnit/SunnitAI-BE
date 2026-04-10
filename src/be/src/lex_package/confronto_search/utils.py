def index_contents(path, json_data):
    """
    Traversa il json seguendo il path ed estrae una lista piatta di values e una lista di indexes.

    Args:
        path: Lista di stringhe che rappresenta il percorso da seguire nel json
        json_data: Dict o lista di dicts da traversare

    Returns:
        tuple: (values, indexes) dove values è una lista piatta e indexes una lista di liste
        Ogni elemento di indexes ha lunghezza uguale alla lunghezza di path
    """
    values = []
    indexes = []
    path_length = len(path)

    def traverse_recursive(current_data, remaining_path, coords_so_far):
        if not remaining_path:
            # Fine del percorso, salva il valore
            values.append(current_data)
            # Assicurati che le coordinate abbiano la lunghezza corretta
            final_coords = coords_so_far[:]
            while len(final_coords) < path_length:
                final_coords.append(0)
            indexes.append(final_coords[:path_length])
            return

        key = remaining_path[0]
        next_path = remaining_path[1:]

        if isinstance(current_data, list):
            # Se siamo in una lista, itera su ogni elemento
            for i, item in enumerate(current_data):
                if isinstance(item, dict) and key in item:
                    new_coords = coords_so_far + [i]
                    next_data = item[key]

                    if isinstance(next_data, list):
                        # Il prossimo livello è anche una lista
                        for j, sub_item in enumerate(next_data):
                            traverse_recursive(sub_item, next_path, new_coords + [j])
                    else:
                        # Il prossimo livello non è una lista
                        traverse_recursive(next_data, next_path, new_coords + [0])

        elif isinstance(current_data, dict) and key in current_data:
            # Se siamo in un dict, accedi alla chiave
            next_data = current_data[key]

            if isinstance(next_data, list):
                # Il valore è una lista, itera su ogni elemento
                for j, sub_item in enumerate(next_data):
                    # Per il dict root, usa sempre 0 come primo elemento
                    if len(coords_so_far) == 0:
                        new_coords = [0, j]
                    else:
                        new_coords = coords_so_far + [j]
                    traverse_recursive(sub_item, next_path, new_coords)
            else:
                # Il valore non è una lista
                if len(coords_so_far) == 0:
                    new_coords = [0, 0]
                else:
                    new_coords = coords_so_far + [0]
                traverse_recursive(next_data, next_path, new_coords)

    # Inizia la traversata
    if isinstance(json_data, list):
        # Se l'input è una lista, itera su ogni elemento
        for i, item in enumerate(json_data):
            traverse_recursive(item, path, [i])
    else:
        # Se l'input è un dict, inizia con coordinate vuote
        traverse_recursive(json_data, path, [])

    return values, indexes


# scrivi funzione insert_deep
#
#
# questa funzione prende una coppia  values , indexes, json_data (della forma uscita dalla funzione index_contents)
# e reinserisce values dentro json_data seguendo le coordinate di indexes


def insert_deep(values, indexes, json_data):
    """
    Reinserisce values dentro json_data seguendo le coordinate di indexes.

    Args:
        values: Lista di valori da inserire
        indexes: Lista di liste di coordinate dove inserire i valori
        json_data: Dict o lista di dicts dove reinserire i valori

    Returns:
        json_data modificato con i nuovi valori inseriti
    """
    import copy

    # Crea una copia profonda per non modificare l'originale
    result = copy.deepcopy(json_data)

    for value, coords in zip(values, indexes):
        # Naviga nella struttura usando le coordinate
        current = result

        # Naviga attraverso tutte le coordinate tranne l'ultima
        for coord in coords[:-1]:
            if isinstance(current, list):
                # Se siamo in una lista, accedi all'elemento
                current = current[coord]
            elif isinstance(current, dict):
                # Se siamo in un dict, accedi alla chiave corrispondente all'indice
                keys = list(current.keys())
                if coord < len(keys):
                    key = keys[coord]
                    current = current[key]

        # Inserisci il valore nell'ultima posizione
        if len(coords) > 0:
            last_coord = coords[-1]
            if isinstance(current, list):
                # Se è una lista, sostituisci l'elemento
                if last_coord < len(current):
                    current[last_coord] = value
            elif isinstance(current, dict):
                # Se è un dict, sostituisci il valore nella chiave corrispondente
                keys = list(current.keys())
                if last_coord < len(keys):
                    key = keys[last_coord]
                    current[key] = value
        else:
            # Se non ci sono coordinate, sostituisci il valore root
            result = value

    return result


def insert_deep_with_path(values, indexes, json_data, path):
    """
    Reinserisce values dentro json_data seguendo le coordinate di indexes e il path originale.

    Args:
        values: Lista di valori da inserire
        indexes: Lista di liste di coordinate dove inserire i valori
        json_data: Dict o lista di dicts dove reinserire i valori
        path: Lista di stringhe che rappresenta il percorso originale usato in index_contents

    Returns:
        json_data modificato con i nuovi valori inseriti
    """
    import copy

    # Crea una copia profonda per non modificare l'originale
    result = copy.deepcopy(json_data)

    for value, coords in zip(values, indexes):
        # Naviga nella struttura seguendo le coordinate e il path
        current = result

        # Mappa ogni livello del path a una coordinata specifica
        # Se partiamo da un dict root, la prima coordinata (coords[0]) è sempre 0
        # La coordinata importante per gli array viene dopo
        coord_for_arrays = 1  # Per strutture dict → array, usa coords[1]

        # Caso speciale: se partiamo da un array (coords[0] è l'indice dell'array)
        if isinstance(current, list):
            coord = coords[0]
            if coord < len(current):
                current = current[coord]
            coord_for_arrays = 1  # Il prossimo array userebbe coords[1]

        # Naviga attraverso il path
        for path_level, path_key in enumerate(path[:-1]):  # Tutti tranne l'ultimo
            if isinstance(current, dict) and path_key in current:
                # Accedi alla chiave nel dict
                current = current[path_key]

                # Se la chiave porta a un array, usa la coordinata mappata per questo livello
                if isinstance(current, list):
                    # Per il path ["users", "profile", "name"]:
                    # - Level 0 ("users"): usa coords[1] per l'array users
                    # - Level 1+ ("profile", etc.): userebbe coords[2], coords[3], etc.
                    coord_index = coord_for_arrays + path_level
                    if coord_index < len(coords):
                        coord = coords[coord_index]
                        if coord < len(current):
                            current = current[coord]

        # Inserisci il valore nell'ultimo livello
        final_key = path[-1]
        if isinstance(current, dict):
            current[final_key] = value

    return result


def extract_and_reinsert(path, json_data):
    """
    Funzione di convenienza che combina index_contents e insert_deep_with_path
    per verificare che l'operazione sia un'identità.

    Args:
        path: Lista di stringhe che rappresenta il percorso
        json_data: Dict o lista di dicts da processare

    Returns:
        json_data ricostruito (dovrebbe essere uguale all'originale)
    """
    values, indexes = index_contents(path, json_data)
    return insert_deep_with_path(values, indexes, json_data, path)
