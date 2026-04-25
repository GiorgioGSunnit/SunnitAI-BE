import os
import logging
import argparse
import json
import re
from typing import Dict, List, Optional
class EnhancedSearchService:
    """Azure Search has been removed. This class is a no-op stub."""

    def __init__(self, endpoint: str = "", index_name: str = "", api_key: str = ""):
        self.search_client = None
        self.logger = logging.getLogger(__name__)
        self.logger.warning("Azure Search removed — full-text search features are disabled.")

    def remove_stopwords(self, text: str) -> str:
        # Lista delle stopwords italiane
        stopwords = [
            "il", "lo", "la", "i", "gli", "le", "l'",
            "un", "uno", "una", "un'",
            "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
            "del", "dello", "della", "dei", "degli", "delle",
            "al", "allo", "alla", "ai", "agli", "alle",
            "dal", "dallo", "dalla", "dai", "dagli", "dalle",
            "nel", "nello", "nella", "nei", "negli", "nelle",
            "col", "coi", "sul", "sullo", "sulla", "sui", "sugli", "sulle",
            "mi", "ti", "ci", "vi", "si", "loro", "egli", "ella", "essi", "esse",
            "io", "tu", "lui", "lei", "noi", "voi",
            "e", "o", "ma", "perché", "anche", "come", "dunque", "quindi", "oppure",
            "né", "pure", "se", "anzi", "bensì", "sia", "perciò", "cioè",
            "qui", "lì", "là", "qua", "dove", "come", "quando", "così", "sempre",
            "mai", "già", "ancora", "appena", "ora", "oggi", "ieri", "domani",
            "questo", "quello", "questa", "quella", "questi", "quelle", "ciò",
            "alcuno", "alcuna", "alcuni", "alcune", "nessuno", "nessuna", "tutto",
            "tutta", "tutti", "tutte", "qualcosa", "qualcuno", "ognuno", "ciascuno",
            "che", "cui", "quale", "quali",
            "anche", "oltre", "tuttavia", "però", "infatti", "anzi", "magari",
            "ci", "ne", "me", "te", "se", "gli", "le", "ne", "vi", "ce",
            "essere", "avere", "è", "sono", "sei", "era", "erano", "fui", "foste",
            "ho", "hai", "ha", "abbiamo", "hanno", "può", "posso", "puoi", "possiamo",
            "possono", "deve", "devo", "dobbiamo", "devono", "voglio", "vuoi", "vuole",
            "vogliamo", "vogliono", "fare", "faccio", "fai", "fa", "facciamo", "fanno",
            "ah", "oh", "eh", "uffa", "via", "su", "ecco",
            "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"
        ]

        # Creazione della regex per rimuovere le stopwords
        stopwords_pattern = r'\b(' + '|'.join(stopwords) + r')\b'

        # Usa regex per sostituire tutte le stopwords con uno spazio
        cleaned_text = re.sub(stopwords_pattern, '', text, flags=re.IGNORECASE)
        # Rimuovi eventuali spazi multipli
        cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
        return cleaned_text

    def search_documents(
        self,
        search_text: str,
        *,
        select_fields: Optional[List[str]] = None,
        filter_expression: Optional[str] = None,
        top: int = 10,
        skip: int = 0,
        enable_semantic_search: bool = False,
        semantic_configuration_name: Optional[str] = None,
        query_caption: Optional[QueryCaptionType] = None,
        query_answer: Optional[QueryAnswerType] = None,
        highlight_fields: Optional[List[str]] = None,
        highlight_pre_tag: str = "<hit>",
        highlight_post_tag: str = "</hit>",
        order_by: Optional[List[str]] = None,
        minimum_coverage: Optional[float] = None,
        scoring_profile: Optional[str] = None,
        from_analysis: bool = False
    ) -> Dict:
        try:
            # Rimuovi le stopwords se il parametro from_analysis è True
            if from_analysis:
                search_text = self.remove_stopwords(search_text)

            # Imposta il campo di ricerca hardcoded su "content"
            search_fields = ["content"]

            # Costruzione dei parametri di ricerca
            search_parameters = {
                "search_text": search_text,
                "search_fields": search_fields,
                "select": select_fields,
                "filter": filter_expression,
                "top": top,
                "skip": skip,
                "order_by": order_by,
                "highlight_fields": highlight_fields or "content",
                "highlight_pre_tag": highlight_pre_tag,
                "highlight_post_tag": highlight_post_tag,
                "minimum_coverage": minimum_coverage,
                "scoring_profile": scoring_profile,
                "include_total_count": True
            }

            # Configurazione per la ricerca semantica
            if enable_semantic_search:
                search_parameters.update({
                    "query_type": QueryType.SEMANTIC,
                    "semantic_configuration_name": semantic_configuration_name,
                    "query_caption": query_caption,
                    "query_answer": query_answer,
                })
            
            # Chiamata al client di Azure Search
            results = self.search_client.search(**{k: v for k, v in search_parameters.items() if v is not None})
            
            documents = []
            for doc in results:
                highlights = doc.get('@search.highlights', {})
                if highlights == None:
                    highlights = doc.get('@search.answers', {})
                content_highlights = highlights.get('content', [])
                if content_highlights == None:
                    content_highlights = doc.get('content', {})
                    
                # Filtro per rimuovere campi indesiderati
                content = {
                    k: v for k, v in doc.items()
                    if not k.startswith('@') and k not in ['content', 'keyphrases', 'people', 'organizations', 'locations']
                }
                
                doc_result = {
                    "filename": doc.get('metadata_storage_name', ''),
                    "relevant_excerpts": content_highlights,
                    "score": doc.get('@search.score', 0),
                    "content": content
                }
                documents.append(doc_result)

            return {
                "total_count": results.get_count(),
                "documents": documents
            }
            
        except Exception as e:
            self.logger.error(f"Error in search_documents: {str(e)}")
            raise

def main():
    parser = argparse.ArgumentParser(description="Enhanced Search Service CLI")
    parser.add_argument('--endpoint', type=str, required=True, help='Azure Search Service endpoint')
    parser.add_argument('--index-name', type=str, required=True, help='Azure Search Index name')
    parser.add_argument('--api-key', type=str, required=True, help='Azure Search API key')
    parser.add_argument('--search-text', type=str, required=True, help='Text to search for')
    parser.add_argument('--top', type=int, default=10, help='Number of results to return (default: 10)')
    parser.add_argument('--enable-semantic-search', action='store_true', help='Enable semantic search')
    parser.add_argument('--from-analysis', action='store_true', help='Remove stopwords from the search text before querying')
    parser.add_argument('--output-file', type=str, help='File to save the output JSON')
    
    args = parser.parse_args()

    search_service = EnhancedSearchService(
        endpoint=args.endpoint,
        index_name=args.index_name,
        api_key=args.api_key
    )

    results = search_service.search_documents(
        search_text=args.search_text,
        top=args.top,
        enable_semantic_search=args.enable_semantic_search,
        from_analysis=args.from_analysis
    )

    if args.output_file:
        with open(args.output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=4)
        print(f"Risultati salvati in {args.output_file}")
    else:
        print(f"Trovati {results['total_count']} documenti")
        for doc in results['documents']:
            print(f"\nDocumento: {doc['filename']}")
            print(f"Score: {doc['score']}")
            if doc['relevant_excerpts']:
                print("Estratti rilevanti:")
                for excerpt in doc['relevant_excerpts']:
                    print(f"- {excerpt}")

if __name__ == "__main__":
    main()
