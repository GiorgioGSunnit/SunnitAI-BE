import urllib.request
import json
import os
import ssl
import requests
import argparse
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# def allowSelfSignedHttps(allowed):
#     # Bypass del certificato del server client
#     if (
#         allowed
#         and not os.environ.get("PYTHONHTTPSVERIFY", "")
#         and getattr(ssl, "_create_unverified_context", None)
#     ):
#         ssl._create_default_https_context = ssl._create_unverified_context


# allowSelfSignedHttps(True)  .


def comparar_jsons(json_input_1, json_input_2):
    # costruzione del prompt
    ## prompt letto da prompt.txt
    prompt = f"""Confronta i due JSON che ti fornirò e restituisci solo le differenze senza alcun preambolo o descrizione iniziale. Voglio che mi mostri esclusivamente il JSON finale con le differenze, indicando per ciascuna differenza il percorso della chiave e i valori specifici in ogni JSON. Distingui tra modifiche, aggiunte e rimozioni.\n
    Input JSON 1: {json.dumps(json_input_1)}\n 
    Input JSON 2: {json.dumps(json_input_2)}\n
    Restituisci le differenze in un formato strutturato (ad esempio, una lista o un altro oggetto JSON), specificando per ciascuna differenza se si tratta di una modifica, di un'aggiunta o di una rimozione.\n
    """
    

    with open('promptHeader.txt', 'r', encoding='utf-8') as file:
        promptHeader = file.read().strip()
    with open('promptFooter.txt', 'r', encoding='utf-8') as file:
        promptFooter = file.read().strip()
    with open('parametersLLAMA.json', 'r') as file:
        parameters = json.load(file)

    prompt = (promptHeader + f"""
    \nInput JSON 1: {json.dumps(json_input_1)} 
    \nInput JSON 2: {json.dumps(json_input_2)}\n
    """
    + promptFooter)

    print(prompt)
    data = {
        "input_data": {
            "input_string": [
                {
                    "role": "system",
                    "content": """Sei un esperto analista legale specializzato nel confronto analitico di requisiti normativi. Il tuo obiettivo è fornire analisi precise e basate esclusivamente sui testi forniti, senza interpretazioni estensive.
 
                                Linee guida operative:
                                - Cita sempre testualmente i passaggi rilevanti
                                - Non parafrasare né interpretare oltre lo stretto necessario
                                - Mantieni un linguaggio tecnico-legale appropriato
                                - Concentrati solo sugli elementi esplicitamente presenti nei testi
                                - Evidenzia sempre la fonte di ogni citazione
                                - Non aggiungere informazioni esterne ai documenti forniti
                                
                                Se non sei sicuro di un confronto o di una correlazione, devi esplicitamente indicarlo invece di fare supposizioni.
                                
                                L'output è SEMPRE e SOLTANTO in lingua ITALIANA, anche quando in input hai documenti in lingua inglese o in altre lingue che non siano l'italiano.""",
                },
                {"role": "user", "content": prompt},
            ],
            ## parameters file.json
            "parameters": parameters,
        },
    }

    body = json.dumps(data)
    url = "https://aistudio-xzyls.swedencentral.inference.ml.azure.com/score"
    api_key = "FUcYq9WWZbkzWdXgSFMYHjDfvhXtUlDz"  

    headers = {
        "Content-Type": "application/json",
        "Authorization": ("Bearer " + api_key),
    }

    try:
        response = requests.post(url=url, data=body, headers=headers)
        if response.ok:
            response_json = response.json()
            if not response_json:
                raise Exception("La risposta dall'API di Azure è vuota.")
            logger.info(f"Response JSON: {response_json}")
            return response_json
        else:
            logger.error(f"Errore nella risposta da Azure: {response.status_code} - {response.text}")
            raise Exception(f"Errore nella risposta di Azure: {response.status_code} - {response.text}")
    except urllib.error.HTTPError as error:
        logger.error(f"Errore nella richiesta: {str(error)}")
        raise Exception(f"Errore nella richiesta: {str(error)}")

#  `run` solo per prove in console
def run(file_path_1, file_path_2):
    with open(file_path_1, "r") as file:
        json_input_1 = json.load(file)

    with open(file_path_2, "r") as file:
        json_input_2 = json.load(file)

    result = comparar_jsons(json_input_1, json_input_2)
    print(result)
    return result


if __name__ == "__main__":
    # Definisci il parser degli argomenti
    parser = argparse.ArgumentParser(description="Extract requirements comparison from 2 JSON documents.")
    parser.add_argument('--input_file1_path', type=str, required=True, help="Path to the input JSON file 1")
    parser.add_argument('--input_file2_path', type=str, required=True, help="Path to the input JSON file 2")
    parser.add_argument('--output_file', type=str, required=True, help="Path to save the output JSON file")
    
    
    # Parsing degli argomenti
    args = parser.parse_args()
    
    # Configura il percorso del PDF
    file_path_1 = args.input_file1_path
    file_path_2 = args.input_file2_path
    output_file = args.output_file
    result = run(file_path_1, file_path_2)
    ## Salvare result
    
    try:
        with open(output_file, "w") as f:
            logger.info(f"Tentativo di salvataggio con json dump")
            json.dump(result, f, ensure_ascii=False, indent=2)
            logger.info(f"Risultato salvato con successo in {output_file}")
    except Exception as e:
        logger.error(f"Errore nel salvataggio del risultato: {str(e)}")
        raise
