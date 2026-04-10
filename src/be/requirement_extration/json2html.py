#!/usr/bin/env python3
"""
Script per convertire un file JSON di confronto in un file HTML formattato.
Include la gestione del mapping tra file PDF e file JSON.

Utilizzo:
    python json_to_html.py [--file PERCORSO_JSON] [--dir DIRECTORY]

Esempio:
    python json_to_html.py --file output/mio_confronto.json
    python json_to_html.py --dir output
"""

import json
import os
import argparse
import logging
import re
from pathlib import Path
import markdown2
from datetime import datetime
from threading import Lock

# Configurazione del logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class PDFToJsonMapping:
    _instance = None
    _lock = Lock()

    def __init__(self):
        self.mapping = {}
        self.comparisonMapping = {}
        self.load_mapping()
        if not self.mapping:
            self.build_mapping()
            self.save_mapping()

    def load_mapping(self, filename: str = "pdf_mapping.json"):
        """Carica il mapping da file"""
        try:
            if Path(filename).exists():
                with open(filename, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.mapping = data.get("mapping", {})
                    self.comparisonMapping = data.get("comparisonMapping", {})
                logger.info(f"Mapping caricato da {filename}")
            else:
                logger.warning(f"File di mapping {filename} non trovato")
        except Exception as e:
            logger.warning(f"Mapping non caricato: {str(e)}")

    def save_mapping(self, filename: str = "pdf_mapping.json"):
        """Salva il mapping su file"""
        try:
            data = {
                "mapping": self.mapping,
                "comparisonMapping": self.comparisonMapping
            }
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Mapping salvato su {filename}")
        except Exception as e:
            logger.error(f"Errore nel salvataggio del mapping: {str(e)}")

    def build_mapping(self):
        """
        Costruisce una mappa che associa il nome normalizzato del PDF (senza estensione)
        al nome del file JSON (basato sull'hash del PDF) presente nella cartella output.
        La mappa viene ricostruita esaminando tutti i file PDF in ./tmp/.
        """
        tmp_dir = Path("./tmp")
        output_dir = Path("./output")
        self.mapping.clear()
        logger.info("Costruzione della mappa PDF -> JSON")
        
        # Assicurati che la directory tmp esista
        if not tmp_dir.exists():
            logger.warning("La directory ./tmp/ non esiste")
            return
            
        for pdf_file in tmp_dir.glob("*.pdf"):
            normalized_name = pdf_file.name
            base_name = normalized_name.replace(".pdf", "")
            
            # Utilizziamo una funzione semplificata per il calcolo dell'hash
            # Normalmente dovremmo usare la stessa funzione di hash utilizzata nella tua applicazione
            file_hash = compute_file_hash(str(pdf_file))
            
            json_filename = f"{file_hash}.json"
            json_path = output_dir / json_filename
            if json_path.exists():
                logger.info(f"Per {normalized_name} è presente il file JSON {json_filename}")
                self.mapping[file_hash] = normalized_name
            else:
                logger.warning(f"Attenzione: per {normalized_name} il file JSON {json_filename} non esiste ancora.")
        
        # Mappa anche i file di confronto
        for comp_file in output_dir.glob("*_vs_*_comparison.json"):
            match = re.match(r"([a-f0-9]+)_vs_([a-f0-9]+)_comparison\.json", comp_file.name)
            if match:
                hash1, hash2 = match.groups()
                self.comparisonMapping[comp_file.name] = (hash1, hash2)

    def get_pdf_name_from_hash(self, file_hash):
        """Ottiene il nome originale del PDF dall'hash"""
        return self.mapping.get(file_hash, f"Unknown PDF ({file_hash})")

    def get_pdf_names_from_comparison(self, comp_filename):
        """Ottiene i nomi originali dei PDF dal nome del file di confronto"""
        filename = os.path.basename(comp_filename)
        
        # Estrai gli hash dal nome del file di confronto
        match = re.match(r"([a-f0-9]+)_vs_([a-f0-9]+)_comparison\.json", filename)
        if match:
            hash1, hash2 = match.groups()
            
            # Verifica che gli hash siano nel mapping
            if hash1 not in self.mapping:
                raise ValueError(f"PDF corrispondente all'hash {hash1} non trovato nel mapping")
            if hash2 not in self.mapping:
                raise ValueError(f"PDF corrispondente all'hash {hash2} non trovato nel mapping")
                
            return (self.get_pdf_name_from_hash(hash1), self.get_pdf_name_from_hash(hash2))
        
        return ("PDF sconosciuto", "PDF sconosciuto")

    @classmethod
    def get_instance(cls):
        logger.info("Richiesta di istanza di PDFToJsonMapping")
        with cls._lock:
            if cls._instance is None:
                cls._instance = PDFToJsonMapping()
        return cls._instance


def compute_file_hash(file_path):
    """
    Implementazione semplificata del calcolo dell'hash di un file.
    Nella tua applicazione reale, dovresti utilizzare la stessa funzione 
    che hai usato per generare i nomi dei file JSON.
    """
    import hashlib
    
    try:
        with open(file_path, "rb") as f:
            file_hash = hashlib.md5()
            # Leggiamo il file a blocchi per evitare di caricare file grandi in memoria
            for chunk in iter(lambda: f.read(4096), b""):
                file_hash.update(chunk)
        return file_hash.hexdigest()
    except Exception as e:
        logger.error(f"Errore nel calcolo dell'hash per {file_path}: {str(e)}")
        return None


def find_latest_json(directory):
    """Trova il file JSON più recente che termina con '_comparison.json' nella directory specificata."""
    directory_path = Path(directory)
    if not directory_path.exists():
        raise FileNotFoundError(f"La directory '{directory}' non esiste.")
    
    try:
        latest_file = max(directory_path.glob("*_comparison.json"), key=os.path.getmtime)
        logger.info(f"File di confronto trovato: {latest_file}")
        return latest_file
    except ValueError:
        raise FileNotFoundError(f"Nessun file '_comparison.json' trovato nella directory '{directory}'.")


def load_json_data(file_path):
    """Carica i dati dal file JSON specificato."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
        
        # Verifica che il JSON contenga la chiave 'output'
        if "output" not in data or not data["output"]:
            raise KeyError("Il file JSON non contiene la chiave 'output' o è vuota.")
            
        return data
    except json.JSONDecodeError:
        raise ValueError(f"Errore nella lettura del file JSON '{file_path}'. Formato non valido.")


def convert_to_html(markdown_text):
    """Converte il testo Markdown in HTML con supporto per tabelle."""
    try:
        html_content = markdown2.markdown(markdown_text, extras=["tables"])
        return html_content
    except Exception as e:
        raise RuntimeError(f"Errore nella conversione del Markdown: {str(e)}")


def create_html_file(html_content, original_file_path, output_path=None):
    """Crea un file HTML con stile CSS inline."""
    # Ottieni i nomi originali dei PDF
    mapping = PDFToJsonMapping.get_instance()
    try:
        pdf1_name, pdf2_name = mapping.get_pdf_names_from_comparison(original_file_path)
        pdf_info = f"""
        <div class="pdf-info">
            <p><strong>PDF confrontati:</strong></p>
            <ul>
                <li>Documento 1: {pdf1_name}</li>
                <li>Documento 2: {pdf2_name}</li>
            </ul>
        </div>
        """
    except ValueError as e:
        logger.warning(f"Impossibile recuperare informazioni sui PDF originali: {str(e)}")
        pdf_info = "<div class='pdf-info warning'>Informazioni sui PDF originali non disponibili</div>"
    
    # Aggiungi stile CSS inline per migliorare l'aspetto
    styled_html = f"""
    <!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Risultati Confronto</title>
        <style>
            body {{
                font-family: Arial, Helvetica, sans-serif;
                line-height: 1.6;
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
                color: #333;
            }}
            h1, h2, h3, h4 {{
                color: #2c3e50;
                margin-top: 1.5em;
                margin-bottom: 0.5em;
            }}
            h1 {{
                border-bottom: 2px solid #eaecef;
                padding-bottom: 0.3em;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                margin: 1em 0;
                overflow-x: auto;
                display: block;
            }}
            table, th, td {{
                border: 1px solid #ddd;
            }}
            th {{
                background-color: #f8f9fa;
                font-weight: 600;
            }}
            th, td {{
                padding: 12px 15px;
                text-align: left;
            }}
            tr:nth-child(even) {{
                background-color: #f8f9fa;
            }}
            pre {{
                background-color: #f6f8fa;
                border-radius: 3px;
                padding: 16px;
                overflow: auto;
            }}
            code {{
                font-family: Consolas, Monaco, 'Andale Mono', monospace;
                background-color: rgba(27, 31, 35, 0.05);
                padding: 0.2em 0.4em;
                border-radius: 3px;
            }}
            .header-info {{
                color: #6c757d;
                font-size: 0.9em;
                margin-bottom: 2em;
            }}
            .pdf-info {{
                background-color: #f8f9fa;
                border-left: 4px solid #2c3e50;
                padding: 10px 15px;
                margin: 1em 0;
            }}
            .warning {{
                border-left-color: #e74c3c;
            }}
            .footer {{
                margin-top: 3em;
                padding-top: 1em;
                border-top: 1px solid #eaecef;
                color: #6c757d;
                font-size: 0.9em;
            }}
        </style>
    </head>
    <body>
        <div class="header-info">
            <p>Generato il: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</p>
        </div>
        
        {pdf_info}
        
        {html_content}
        
        <div class="footer">
            <p>Report generato automaticamente dal sistema di confronto normativo</p>
        </div>
    </body>
    </html>
    """
    
    # Se non è specificato un percorso di output, usa un nome predefinito
    if output_path is None:
        output_path = "risultati_confronto.html"
    
    try:
        with open(output_path, 'w', encoding='utf-8') as html_file:
            html_file.write(styled_html)
        logger.info(f"File HTML creato con successo: {output_path}")
        return output_path
    except Exception as e:
        raise IOError(f"Errore durante la creazione del file HTML: {str(e)}")


def main():
    parser = argparse.ArgumentParser(description='Converti un file JSON di confronto in HTML.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--file', type=str, help='Percorso del file JSON di confronto')
    group.add_argument('--dir', type=str, help='Directory contenente i file JSON di confronto')
    parser.add_argument('--output', type=str, help='Percorso del file HTML di output (opzionale)')
    parser.add_argument('--refresh-mapping', action='store_true', help='Forza il ricalcolo del mapping PDF->JSON')
    
    args = parser.parse_args()
    
    try:
        # Inizializza il singleton di mapping
        mapping = PDFToJsonMapping.get_instance()
        
        # Se richiesto, forza il refresh del mapping
        if args.refresh_mapping:
            logger.info("Ricostruzione forzata del mapping PDF->JSON")
            mapping.build_mapping()
            mapping.save_mapping()
        
        # Determina il file JSON da usare
        if args.file:
            json_file = Path(args.file)
            if not json_file.exists():
                raise FileNotFoundError(f"Il file '{args.file}' non esiste.")
        else:
            json_file = find_latest_json(args.dir)
        
        # Carica i dati JSON
        data = load_json_data(json_file)
        
        # Estrai il testo Markdown
        markdown_text = data["output"]
        
        # Converti Markdown in HTML
        html_content = convert_to_html(markdown_text)
        
        # Crea il file HTML
        output_path = args.output if args.output else None
        html_path = create_html_file(html_content, json_file, output_path)
        
        logger.info(f"Conversione completata. File HTML salvato in: {html_path}")
        print(f"\nOperazione completata con successo!\nFile HTML salvato in: {html_path}")
        
    except Exception as e:
        logger.error(str(e))
        print(f"Errore: {str(e)}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
