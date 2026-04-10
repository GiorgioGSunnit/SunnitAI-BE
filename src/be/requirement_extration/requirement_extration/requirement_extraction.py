import sys
print(sys.executable)

import os
print(os.getcwd())

# Imports corretti
import os
from datetime import datetime
import json
# import torch
# from transformers import AutoTokenizer, AutoModel  # Commented - not used
from spacy.lang.it import Italian
from spacy.pipeline import Sentencizer
import re
from typing import List, Dict, IO
import logging
from pathlib import Path
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer
import argparse


# Setup logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


#class RequirementExtractor:
#
#    def __init__(self, 
#                 model_name: str = "dlicari/lsg16k-Italian-Legal-BERT", 
#                 max_length: int = 16384,
#                 pooling_strategy: str = "attention_weighted",
#                 use_cls_pooling: bool = True,
#                 layers_to_combine: List[int] = [-1, -2, -3, -4],
#                 normalize_embeddings: bool = True):
#        """
#        Inizializza l'estrattore di requisiti con parametri avanzati.
#        
#        Args:
#            model_name: Nome del modello da utilizzare
#            max_length: Lunghezza massima della sequenza
#            pooling_strategy: Strategia di pooling ('attention_weighted', 'mean', 'max')
#            use_cls_pooling: Se utilizzare il token CLS per il pooling
#            layers_to_combine: Lista degli indici dei layer da combinare
#            normalize_embeddings: Se normalizzare gli embedding
#        """
#        logger.info("Inizializzazione dell'estrattore di requisiti con parametri avanzati...")
#        
#        self.model_name = model_name
#        self.max_length = max_length
#        self.pooling_strategy = pooling_strategy
#        self.use_cls_pooling = use_cls_pooling
#        self.layers_to_combine = layers_to_combine
#        self.normalize_embeddings = normalize_embeddings
#        
#        logger.info(f"Caricamento del modello {self.model_name}...")
#        self.tokenizer = AutoTokenizer.from_pretrained(
#            self.model_name,
#            model_max_length=self.max_length
#        )
#        self.model = AutoModel.from_pretrained(self.model_name)
#        
#        logger.info("Caricamento del modello spaCy per l'analisi linguistica italiana...")
#        """
#        self.nlp = spacy.load("it_core_news_sm", disable=["parser", "lemmatizer", "ner"])  # Disabilita il parser
#        self.nlp.add_pipe('sentencizer')
#        """
#        logger.info("Initializing sentencizer for text processing...")
#        self.nlp = Italian()
#        sentencizer = Sentencizer()
#        self.nlp.add_pipe("sentencizer")
#        
#        logger.info("Initialization completed successfully")
#
#        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
#        self.model.to(self.device)
#        logger.info(f"Modello caricato su {self.device}")
#        
#        logger.info("Compilazione dei pattern per il riconoscimento dei requisiti...")
#        # Pattern linguistici per requisiti con spiegazioni
#        self.requirement_patterns = [
#            # Pattern per obblighi diretti
#            r"(?:deve|devono|è tenuto a|sono tenuti a|ha l'obbligo di|hanno l'obbligo di)",
#            # Pattern per necessità e obbligatorietà
#            r"(?:è obbligatorio|è necessario)",
#            # Pattern per divieti e limitazioni
#            r"(?:non può|non possono|è vietato|è fatto divieto|non è consentito)",
#            # Pattern per condizioni
#            r"(?:qualora|nel caso in cui|a condizione che|purché)",
#            # Pattern per termini temporali
#            r"(?:entro|non oltre|a decorrere da|nel termine di)"
#        ]
#        self.compiled_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in self.requirement_patterns]
#        
#        logger.info("Inizializzazione completata con successo")
#
#    def _combine_layers(self, hidden_states):
#        """Combina i layer specificati secondo la strategia scelta."""
#        logger.info("Combina i layer specificati secondo la strategia scelta.")
#        if hidden_states is None:
#            raise ValueError("Hidden states non disponibili per la combinazione")
#            
#        try:
#            selected_layers = [hidden_states[i] for i in self.layers_to_combine]
#        except IndexError as e:
#            available_layers = len(hidden_states)
#            logger.error(f"Indice layer non valido. Layers disponibili: {available_layers}")
#            raise ValueError(f"Layer richiesto non disponibile. Massimo layer: {available_layers-1}")
#        
#        if len(selected_layers) == 1:
#            return selected_layers[0]
#        return torch.stack(selected_layers).mean(dim=0)
#
#    def _pool_embeddings(self, hidden_states, attention_mask=None):
#        """Applica la strategia di pooling scelta."""
#        logger.info("Applica la strategia di pooling scelta.")
#        if self.pooling_strategy == "attention_weighted":
#            # Implementazione del pooling con attenzione pesata
#            weights = torch.softmax(torch.matmul(hidden_states, hidden_states.transpose(-2, -1)), dim=-1)
#            if attention_mask is not None:
#                weights = weights * attention_mask.unsqueeze(1)
#            pooled = torch.matmul(weights, hidden_states)
#            if self.use_cls_pooling:
#                return pooled[:, 0]
#            return pooled.mean(dim=1)
#        elif self.pooling_strategy == "mean":
#            if attention_mask is not None:
#                hidden_states = hidden_states * attention_mask.unsqueeze(-1)
#                return hidden_states.sum(dim=1) / attention_mask.sum(dim=1, keepdim=True)
#            return hidden_states.mean(dim=1)
#        elif self.pooling_strategy == "max":
#            if attention_mask is not None:
#                hidden_states = hidden_states * attention_mask.unsqueeze(-1)
#            return hidden_states.max(dim=1)[0]
#        else:
#            raise ValueError(f"Strategia di pooling non supportata: {self.pooling_strategy}")
#
#    def extract_context(self, text: str, start_idx: int, end_idx: int, context_chars: int = 350) -> str:
#        """
#        Estrae il contesto intorno al requisito identificato, assicurandosi di mantenere
#        frasi complete per una migliore comprensibilità.
#        
#        Args:
#            text: Il testo completo del documento
#            start_idx: Indice di inizio del requisito
#            end_idx: Indice di fine del requisito
#            context_chars: Numero di caratteri di contesto da includere prima e dopo
#        
#        Returns:
#            str: Il testo del requisito con il suo contesto
#        """
#        logger.debug(f"Estrazione contesto per requisito che inizia alla posizione {start_idx}")
#        logger.info("Estrazione contesto per requisito")
#        doc = self.nlp(text)
#        
#        #logger.info(f'doc: {doc}')
#        # Calcola i confini iniziali del contesto
#        start_context = max(0, start_idx - context_chars)
#        end_context = min(len(text), end_idx + context_chars)
#        
#        logger.info(f"Confini iniziali del contesto: [{start_context}, {end_context}]")
#        
#        # Estendi ai confini delle frasi per non tagliare a metà
#        for sent in doc.sents:
#            logger.info(f'sent: {sent}')
#            if sent.start_char <= start_context <= sent.end_char:
#                logger.info(f"Estensione inizio contesto da {start_context} a {sent.start_char}")
#                start_context = sent.start_char
#            if sent.start_char <= end_context <= sent.end_char:
#                logger.info(f"Estensione fine contesto da {end_context} a {sent.end_char}")
#                end_context = sent.end_char
#        
#        extracted_context = text[start_context:end_context]
#        logger.info(f"Contesto estratto di lunghezza {len(extracted_context)} caratteri")
#        
#        return extracted_context
#    
#    def _find_requirement_candidates(self, text: str, page_offsets: List[int], include_context: bool = True) -> List[Dict]:
#        """Find requirement candidates with optional context."""
#        candidates = []
#        
#        for pattern in self.compiled_patterns:
#            matches = list(pattern.finditer(text))
#            
#            for match in matches:
#                # Calcola il numero di pagina
#                page_num = 1
#                for i, offset in enumerate(page_offsets[1:], 2):
#                    if match.start() < offset:
#                        break
#                    page_num = i
#
#                candidate = {
#                    'start': match.start(),
#                    'end': match.end(),
#                    'pattern': match.group(),
#                    'full_text': text[match.start():match.end()],
#                    'page': page_num  # Aggiunto il numero di pagina
#                }
#                
#                if include_context:
#                    candidate['context'] = self.extract_context_simple(
#                        text, 
#                        match.start(), 
#                        match.end()
#                    )
#                else:
#                    candidate['context'] = candidate['full_text']
#                    
#                candidates.append(candidate)
#                
#        return candidates
#
#    def analyze_with_bert(self, texts: List[str]) -> List[float]:
#        """Analizza i testi usando il modello BERT con i parametri avanzati."""
#        logger.info(f"Analisi BERT per {len(texts)} testi")
#        
#        try:
#            inputs = self.tokenizer(texts, 
#                                return_tensors="pt",
#                                truncation=True,
#                                max_length=self.max_length,
#                                padding=True)
#            
#            inputs = {k: v.to(self.device) for k, v in inputs.items()}
#            
#            with torch.no_grad():
#                # Forziamo output_hidden_states=True
#                outputs = self.model(
#                    **inputs,
#                    output_hidden_states=True
#                )
#                
#                logger.debug(f"Numero di hidden states: {len(outputs.hidden_states)}")
#                
#                # Prendiamo gli ultimi N layer direttamente
#                last_hidden_states = outputs.last_hidden_state  # Questo è garantito essere presente
#                
#                # Applichiamo il pooling direttamente sull'ultimo hidden state
#                if self.pooling_strategy == "attention_weighted":
#                    # Calcolo attention scores
#                    attention_weights = torch.matmul(last_hidden_states, last_hidden_states.transpose(-2, -1))
#                    attention_weights = torch.softmax(attention_weights, dim=-1)
#                    
#                    if 'attention_mask' in inputs:
#                        attention_mask = inputs['attention_mask'].unsqueeze(1).expand(attention_weights.size())
#                        attention_weights = attention_weights * attention_mask
#                        attention_weights = attention_weights / (attention_weights.sum(dim=-1, keepdim=True) + 1e-9)
#                    
#                    # Pooling con attention
#                    pooled = torch.matmul(attention_weights, last_hidden_states)
#                    if self.use_cls_pooling:
#                        pooled = pooled[:, 0]
#                    else:
#                        pooled = pooled.mean(dim=1)
#                else:
#                    # Mean pooling come fallback
#                    pooled = last_hidden_states.mean(dim=1)
#                
#                # Normalizzazione se richiesta
#                if self.normalize_embeddings:
#                    pooled = torch.nn.functional.normalize(pooled, p=2, dim=-1)
#                
#                # Calcolo scores
#                scores = []
#                for emb in pooled:
#                    score = torch.mean(emb).item()
#                    normalized_score = self._sigmoid(score)
#                    scores.append(normalized_score)
#                    logger.debug(f"Score raw = {score:.4f}, normalizzato = {normalized_score:.4f}")
#                
#                logger.info(f"Analisi completata. Score medi: {sum(scores)/len(scores):.4f}")
#                return scores
#            
#        except Exception as e:
#            logger.error(f"Errore nell'analisi BERT: {str(e)}")
#            raise
#
#    def _sigmoid(self, x: float) -> float:
#        """Applica la funzione sigmoid per normalizzare gli score."""
#        return 1 / (1 + torch.exp(-torch.tensor(x, device=self.device))).item()
#
#    def _deduplicate_requirements(self, requirements: List[Dict]) -> List[Dict]:
#        """
#        Rimuove i requisiti duplicati con contesto esattamente identico,
#        mantenendo quello con il confidence score più alto.
#        
#        Args:
#            requirements: Lista dei requisiti estratti
#            
#        Returns:
#            List[Dict]: Lista dei requisiti deduplicati
#        """
#        logger.info(f"Avvio deduplicazione su {len(requirements)} requisiti")
#        unique_requirements = {}
#        
#        for req in requirements:
#            requirement_text = req['requirement']
#            current_confidence = req['confidence']
#            
#            # Se il contesto non è stato ancora visto, o se questo requisito
#            # ha un confidence score più alto, lo memorizziamo
#            if (requirement_text not in unique_requirements or
#                current_confidence > unique_requirements[requirement_text]['confidence']):
#                unique_requirements[requirement_text] = req
#                
#        deduplicated = list(unique_requirements.values())
#        logger.info(f"Deduplicazione completata: {len(requirements) - len(deduplicated)} duplicati rimossi.")
#        
#        return deduplicated
#
#    def extract_context_simple(self, text: str, start_idx: int, end_idx: int, context_chars: int = 350) -> str:
#        """
#        Efficient context extraction that returns complete sentences within the context window.
#        
#        Args:
#            text: The full text
#            start_idx: Start index of the requirement
#            end_idx: End index of the requirement
#            context_chars: Number of characters to include before and after
#        
#        Returns:
#            str: The requirement with its sentence-based context
#        """
#        # First get the rough context window
#        text_length = len(text)
#        context_start = max(0, start_idx - context_chars)
#        context_end = min(text_length, end_idx + context_chars)
#        
#        # Get the text chunk we're interested in
#        context_chunk = text[context_start:context_end]
#        
#        # Create a Doc object with just the context chunk
#        doc = self.nlp(context_chunk)
#        
#        # Find sentence boundaries
#        sentences = list(doc.sents)
#        
#        # If no sentences found (rare case), return the original chunk
#        if not sentences:
#            return context_chunk
#            
#        # Get complete sentences
#        # Adjust relative position of the requirement within the chunk
#        relative_start = start_idx - context_start
#        relative_end = end_idx - context_start
#        
#        # Find the sentences containing our requirement
#        requirement_sentences = []
#        for sent in sentences:
#            # If there's any overlap between the sentence and our requirement
#            if not (sent.end_char <= relative_start or sent.start_char >= relative_end):
#                requirement_sentences.append(sent.text)
#        
#        # Join the sentences
#        return " ".join(requirement_sentences)
#    
#    def extract_requirements(self, 
#                           text: str, 
#                           page_offsets: List[int],
#                           threshold: float = 0.19, 
#                           batch_size: int = 8,
#                           include_context: bool = True) -> List[Dict]:
#        """
#        Extract requirements with optional context.
#        
#        Args:
#            text: Text to analyze
#            threshold: Confidence threshold
#            batch_size: Size of batches for BERT analysis
#            include_context: Whether to include context around requirements
#        """
#        logger.info(f"Inizio estrazione requisiti da testo di {len(text)} caratteri")
#        logger.info(f"Parameters: threshold={threshold}, batch_size={batch_size}, include_context={include_context}")
#        
#        candidates = self._find_requirement_candidates(text, page_offsets, include_context)
#        requirements = []
#        
#        # Process candidates in batches
#        for i in range(0, len(candidates), batch_size):
#            batch = candidates[i:i+batch_size]
#            logger.debug(f"Processing batch {i//batch_size + 1}/{(len(candidates)-1)//batch_size + 1}")
#            
#            # Use context if available, otherwise use full_text
#            texts_to_analyze = [c['context'] for c in batch]
#            scores = self.analyze_with_bert(texts_to_analyze)
#            
#            for candidate, score in zip(batch, scores):
#                if score > threshold:
#                    requirement = {
#                        'requirement': candidate['context'] if include_context else candidate['full_text'],
#                        'core_text': candidate['full_text'],
#                        'confidence': score,
#                        'pattern_type': self._get_pattern_type(candidate['pattern']),
#                        'page': candidate['page']
#                    }
#                    requirements.append(requirement)
#                else:
#                    logger.debug(f"Candidate scartato: '{candidate['pattern']}' con confidence {score:.4f}")
#        
#        requirements = self._deduplicate_requirements(requirements)
#        
#        logger.info(f"Estratti {len(requirements)} requisiti validi")
#        return requirements
#
#    def _get_pattern_type(self, pattern: str) -> str:
#        """
#        Classifica il tipo di pattern identificato nel requisito.
#        
#        Args:
#            pattern: Il pattern trovato nel testo
#            
#        Returns:
#            str: La tipologia del pattern
#        """
#        pattern = pattern.lower()
#        logger.debug(f"Classificazione pattern: '{pattern}'")
#        
#        if any(word in pattern for word in ['deve', 'devono', 'obbligo']):
#            return 'obbligo_diretto'
#        elif any(word in pattern for word in ['vietato', 'divieto', 'non può']):
#            return 'divieto'
#        elif any(word in pattern for word in ['qualora', 'nel caso', 'condizione']):
#            return 'condizione'
#        elif any(word in pattern for word in ['entro', 'termine', 'decorrere']):
#            return 'termine_temporale'
#        return 'altro'
#
#    def _initialize_prototype_vectors(self):
#        """
#        Inizializza i prototype vectors per i diversi contesti di 'entro'
#        """
#        temporal_examples = [
#            "il termine per la presentazione delle domande è fissato entro 30 giorni dalla pubblicazione",
#            "la risposta deve essere fornita entro il termine di dieci giorni lavorativi",
#            "il procedimento si conclude entro sei mesi dalla data di avvio",
#            "devono comunicare entro la scadenza del mese successivo",
#            "sono tenuti a rispondere entro la data stabilita"
#        ]
#
#        dimensional_examples = [
#            "il punteggio economico deve rimanere entro il limite del 10 per cento",
#            "la variazione dei costi deve mantenersi entro la soglia massima prevista",
#            "il valore deve essere contenuto entro i parametri stabiliti",
#            "la percentuale di scostamento entro il margine del 5 percento",
#            "il tetto massimo è fissato entro il budget stanziato"
#        ]
#
#        try:
#            temporal_embeddings = []
#            dimensional_embeddings = []
#
#            def get_context_embedding(text: str) -> torch.Tensor:
#                inputs = self.tokenizer(
#                    text,
#                    return_tensors="pt",
#                    padding=True,
#                    truncation=True,
#                    max_length=self.max_length
#                ).to(self.device)
#                
#                with torch.no_grad():
#                    outputs = self.model(**inputs, output_hidden_states=True)
#                    last_hidden_state = outputs.last_hidden_state[0]
#                    
#                    entro_position = text.find("entro")
#                    if entro_position == -1:
#                        raise ValueError("Esempio non contiene 'entro'")
#                    
#                    entro_token_idx = None
#                    curr_pos = 0
#                    for i, token in enumerate(self.tokenizer.convert_ids_to_tokens(inputs['input_ids'][0])):
#                        if curr_pos <= entro_position < curr_pos + len(token):
#                            entro_token_idx = i
#                            break
#                        curr_pos += len(token)
#                    
#                    if entro_token_idx is None:
#                        raise ValueError("Non è possibile trovare il token 'entro'")
#                    
#                    start_idx = max(0, entro_token_idx - self.window_size)
#                    end_idx = min(len(last_hidden_state), entro_token_idx + self.window_size + 1)
#                    
#                    context_embeddings = last_hidden_state[start_idx:end_idx]
#                    return context_embeddings.mean(dim=0)
#
#            for example in temporal_examples:
#                temporal_embeddings.append(get_context_embedding(example))
#            
#            for example in dimensional_examples:
#                dimensional_embeddings.append(get_context_embedding(example))
#
#            self.temporal_prototype = torch.stack(temporal_embeddings).mean(dim=0)
#            self.dimensional_prototype = torch.stack(dimensional_embeddings).mean(dim=0)
#
#        except Exception as e:
#            logger.error(f"Errore nell'inizializzazione dei prototype vectors: {str(e)}")
#            raise
#
#    def _analyze_entro_context(self, context: str, start_pos: int) -> bool:
#        """
#        Analizza il contesto di 'entro' per determinare se mantenerlo come requisito.
#        Returns True se il requisito va mantenuto, False altrimenti.
#        
#        try:
#            inputs = self.tokenizer(
#                context,
#                return_tensors="pt", 
#                padding=True,
#                truncation=True,
#                max_length=self.max_length
#            ).to(self.device)
#            
#            with torch.no_grad():
#                outputs = self.model(**inputs, output_hidden_states=True)
#                last_hidden_state = outputs.last_hidden_state[0]
#                
#                context_emb = last_hidden_state.mean(dim=0)
#                sim_temp = torch.cosine_similarity(context_emb.unsqueeze(0), 
#                                                self.temporal_prototype.unsqueeze(0))
#                sim_dim = torch.cosine_similarity(context_emb.unsqueeze(0), 
#                                            self.dimensional_prototype.unsqueeze(0))
#                
#                is_temporal = sim_temp > sim_dim
#                print(f"'entro' in contesto: '{context}'\nClassificato come: {'temporale' if is_temporal else 'dimensionale'}\n")
#                
#                return is_temporal
#                
#        except Exception as e:
#            print(f"Errore analisi 'entro', mantengo il requisito. Errore: {str(e)}")
#            return True
#        """
#        return True
#
#    def extract_text_from_pdf(pdf_file: IO):
#        """
#        Extracts text from a PDF file using pdfminer.
#        Args:
#            pdf_file (IO): A file-like object representing the PDF.
#        Returns:
#            str: The extracted text from the PDF.
#        """
#        try:
#            temp_file_path = "/tmp/temp_pdf_file.pdf"
#            with open(temp_file_path, "wb") as temp_file:
#                temp_file.write(pdf_file.read())
#            text = extract_text(temp_file_path)
#            os.remove(temp_file_path)
#            return text
#        except Exception as e:
#            print(f"Error extracting text from PDF: {e}")
#            return ""

def process_pdf(pdf_path: str, output_dir: str = "./output", model_params: Dict = None) -> str:
    logger.info("starting processpdf")
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Il file PDF {pdf_path} non esiste")
        
    if model_params is None:
        model_params = {
            'model_name': "dlicari/lsg16k-Italian-Legal-BERT",
            'max_length': 16384,
            'pooling_strategy': "attention_weighted",
            'use_cls_pooling': True,
            'layers_to_combine': [-1, -2, -3, -4],
            'normalize_embeddings': True
        }
    
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        file_name = Path(pdf_path).stem
        # timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_file = f"{file_name}.json"
        output_path = os.path.join(output_dir, output_file)
        
        logger.info(f"Elaborazione del file: {pdf_path}")
        logger.info(f"file input e output: {pdf_path}, {output_file}, {output_path}")
        text = extract_text(pdf_path)
        
        if not text.strip():
            raise ValueError("Il PDF non contiene testo estraibile")
            
        extractor = RequirementExtractor(**model_params)
        requirements = extractor.extract_requirements(text)
        logger.info(f"Requisiti estratti: {len(requirements[:600])}")
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump({
                'source_file': pdf_path,
                'extraction_date': datetime.now().isoformat(),
                'model_parameters': model_params,
                'requirements': requirements
            }, f, ensure_ascii=False, indent=2)
            
        logger.info(f"Risultati salvati in: {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"Errore durante l'elaborazione del file {pdf_path}: {str(e)}")
        raise

#def __init__(self, 
#             model_name: str = "dlicari/lsg16k-Italian-Legal-BERT", 
#             max_length: int = 16384,
#             window_size: int = 10,
#             pooling_strategy: str = "attention_weighted",
#             use_cls_pooling: bool = True,
#             layers_to_combine: List[int] = [-1, -2, -3, -4],
#             normalize_embeddings: bool = True):
#    
#    logger.info("Inizializzazione dell'estrattore di requisiti con parametri avanzati...")
#    
#    # 1. Prima tutti i parametri
#    self.model_name = model_name
#    self.max_length = max_length
#    self.pooling_strategy = pooling_strategy
#    self.use_cls_pooling = use_cls_pooling
#    self.layers_to_combine = layers_to_combine
#    self.normalize_embeddings = normalize_embeddings
#    self.window_size = window_size
#    
#    # 2. Poi il modello e il tokenizer
#    logger.info(f"Caricamento del modello {self.model_name}...")
#    self.tokenizer = AutoTokenizer.from_pretrained(
#        self.model_name,
#        model_max_length=self.max_length
#    )
#    self.model = AutoModel.from_pretrained(
#        self.model_name,
#        output_hidden_states=True,
#        return_dict=True
#    )
#    
#    # 3. Setup del device e spostamento del modello
#    self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
#    self.model.to(self.device)
#    
#    # 4. Inizializzazione prototype vectors (usa modello, tokenizer e device)
#    logger.info("Inizializzazione prototype vectors...")
#    self._initialize_prototype_vectors()
#    
#    # 5. Infine spaCy che è indipendente dai passaggi precedenti
#    """
#    logger.info("Caricamento del modello spaCy per l'analisi linguistica italiana...")
#    self.nlp = spacy.load("it_core_news_sm", disable=["parser"])
#    
#    logger.info("Inizializzazione completata con successo")
#    """
#    logger.info("Initializing sentencizer for text processing...")
#    self.nlp = Italian()
#    self.nlp.add_pipe("sentencizer")
#    
#    logger.info("Initialization completed successfully")


if __name__ == "__main__":
    # Configurazione iniziale
    logger.info("Configurazione iniziale")
    import os
    os.environ["TOKENIZERS_PARALLELISM"] = "false"  # Gestione warning tokenizer
    
    
    # Definisci il parser degli argomenti
    parser = argparse.ArgumentParser(description="Extract requirements from a PDF document.")
    parser.add_argument('--input_data', type=str, required=True, help="Path to the input PDF file")
    parser.add_argument('--model_output', type=str, required=True, help="Path to save the output JSON file")
    
    
    # Parsing degli argomenti
    args = parser.parse_args()
    
    # Configura il percorso del PDF
    pdf_path = args.input_data
    output_file = args.model_output
    print(f"Input PDF path: {pdf_path}")
    print(f"Output JSON path: {output_file}")
    
    
    # Parametri del modello con valori ottimizzati
    model_params = {
        'model_name': "dlicari/lsg16k-Italian-Legal-BERT",
        'max_length': 16384,
        'pooling_strategy': "attention_weighted",
        'use_cls_pooling': True,
        'layers_to_combine': [-1, -2, -3, -4],
        'normalize_embeddings': True
    }
    
    try:
        # Verifica esistenza file
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"File non trovato: {pdf_path}")
            
        # Crea directory output se non esiste
        output_dir = "./output"
        os.makedirs(output_dir, exist_ok=True)
        
        # Estrai il nome del file e crea il timestamp
        file_name = os.path.splitext(os.path.basename(pdf_path))[0]
        # timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        
        # Definisci il percorso di output
        output_file = os.path.join(output_dir, f"{file_name}.json")
        
        # Estrai il testo dal PDF mantenendo l'informazione delle pagine
        logger.info(f"Estrazione testo da: {pdf_path}")
        # text = extract_text(pdf_path)

        text_by_pages = []
        full_text = ""
        page_offsets = [0]  # Il primo offset (delimitatore di pagina) è sempre 0
        
        for page_layout in extract_pages(args.input_data): # extract_pages al posto di extract_text
            page_text = ""
            for element in page_layout:
                if isinstance(element, LTTextContainer):
                    page_text += element.get_text()
            text_by_pages.append(page_text)
            full_text += page_text
            page_offsets.append(len(full_text))
        
        if not full_text.strip():
            raise ValueError("Il PDF non contiene testo estraibile")
        
        # Inizializza l'estrattore con i parametri
        extractor = RequirementExtractor(**model_params)
        
        # Estrai i requisiti
        logger.info("Inizio estrazione requisiti...")
        # Il metodo di estrazione dei requirements tiene traccia dei delimitatori di pagina
        requirements = extractor.extract_requirements(full_text, page_offsets) 
        
        # Salva i risultati
        logger.info(f"Salvataggio risultati in: {output_file}")
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({
                'source_file': pdf_path,
                'extraction_date': datetime.now().isoformat(),
                'model_parameters': model_params,
                'text_length': len(full_text),
                'requirements_found': len(requirements),
                'requirements': requirements
            }, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Elaborazione completata. Trovati {len(requirements)} requisiti")
        logger.info(f"Risultati salvati in: {output_file}")
        
    except FileNotFoundError as e:
        logger.error(f"Errore: {str(e)}")
        raise
    except ValueError as e:
        logger.error(f"Errore nel processamento: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Errore inatteso: {str(e)}")
        raise


