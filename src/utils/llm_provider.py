#"""
#Provider asincrono per Azure OpenAI.
#
#Supporta:
#- Chat completion standard
#- Chat con tool-calls (function calling)
#- Streaming delle risposte
#- Autenticazione via APIM con JWT token + subscription key
#
#Esempio d'uso:
#    from utils.llm_provider import get_llm_provider
#    
#    llm = get_llm_provider()
#    
#    # Chat standard
#    response = await llm.generate_response([
#        {"role": "system", "content": "Sei un assistente."},
#        {"role": "user", "content": "Ciao!"}
#    ])
#    
#    # Streaming
#    async for chunk in llm.stream_response(messages):
#        print(chunk.choices[0].delta.content)
#"""
#from typing import Any, Optional, Callable
#
#from loguru import logger
#from openai import AsyncAzureOpenAI
#
#from core.settings import get_settings
#
#
#def _create_token_provider(client_id: str, client_secret: str, tenant_id: str) -> Callable[[], str]:
#    """
#    Crea un token provider per autenticazione Azure AD.
#    
#    NOTA: Richiede che l'App Registration abbia accessTokenAcceptedVersion=2
#    per generare token v2.0 compatibili con APIM.
#    """
#    from msal import ConfidentialClientApplication
#    
#    app = ConfidentialClientApplication(
#        client_id,
#        authority=f'https://login.microsoftonline.com/{tenant_id}',
#        client_credential=client_secret
#    )
#    
#    def get_token() -> str:
#        result = app.acquire_token_for_client(
#            scopes=['https://cognitiveservices.azure.com/.default']
#        )
#        if 'access_token' in result:
#            return result['access_token']
#        raise ValueError(f"Token acquisition failed: {result.get('error_description', result)}")
#    
#    return get_token
#
#
#class LLMProvider:
#    """Wrapper asincrono per Azure OpenAI con supporto APIM."""
#
#    def __init__(
#        self,
#        api_key: str,
#        endpoint: str,
#        deployment: str,
#        api_version: str,
#        token_provider: Optional[Callable[[], str]] = None
#    ):
#        """
#        Inizializza il provider.
#        
#        Args:
#            api_key: API key / subscription key per APIM
#            endpoint: Endpoint Azure OpenAI o APIM
#            deployment: Nome del deployment del modello
#            api_version: Versione API da usare
#            token_provider: Funzione che restituisce JWT token (opzionale)
#        """
#        if not all([api_key, endpoint, deployment, api_version]):
#            raise ValueError("Tutti i parametri Azure OpenAI sono obbligatori")
#
#        self.deployment = deployment
#        self._token_provider = token_provider
#        
#        # Configurazione client
#        client_kwargs = {
#            "azure_endpoint": endpoint,
#            "api_version": api_version,
#            "default_headers": {
#                "api-key": api_key,
#                "Ocp-Apim-Subscription-Key": api_key,
#            }
#        }
#        
#        # Se abbiamo token provider, usa JWT; altrimenti usa api_key
#        if token_provider:
#            client_kwargs["azure_ad_token_provider"] = token_provider
#            logger.info("LLMProvider: usando autenticazione JWT + subscription key")
#        else:
#            client_kwargs["api_key"] = api_key
#            logger.info("LLMProvider: usando solo api_key")
#        
#        self.client = AsyncAzureOpenAI(**client_kwargs)
#
#    @classmethod
#    def from_settings(cls, use_jwt: bool = True):
#        """
#        Factory method che crea il provider usando i settings dell'app.
#        
#        Args:
#            use_jwt: Se True, tenta di usare autenticazione JWT per APIM.
#                     Richiede AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID.
#        """
#        import os
#        settings = get_settings()
#        
#        token_provider = None
#        if use_jwt:
#            # Prova a creare token provider se abbiamo le credenziali
#            client_id = os.environ.get('AZURE_READER__CLIENT_ID') or os.environ.get('AZURE_CLIENT_ID')
#            client_secret = os.environ.get('AZURE_READER__CLIENT_SECRET') or os.environ.get('AZURE_CLIENT_SECRET')
#            tenant_id = os.environ.get('AZURE_TENANT_ID')
#            
#            if all([client_id, client_secret, tenant_id]):
#                try:
#                    token_provider = _create_token_provider(client_id, client_secret, tenant_id)
#                    logger.info(f"Token provider creato per client_id: {client_id[:8]}...")
#                except Exception as e:
#                    logger.warning(f"Impossibile creare token provider: {e}")
#            else:
#                logger.info("Credenziali JWT non disponibili, uso solo api_key")
#        
#        return cls(
#            api_key=settings.azure_openai.api_key,
#            endpoint=settings.azure_openai.endpoint,
#            deployment=settings.azure_openai.chat_model_deployment,
#            api_version=settings.azure_openai.api_version,
#            token_provider=token_provider,
#        )
#
#    async def generate_response(
#        self,
#        messages: list[dict[str, str]],
#        **kwargs
#    ):
#        """
#        Chat completion standard.
#        
#        Args:
#            messages: Lista di messaggi [{"role": "user", "content": "..."}]
#            **kwargs: Parametri aggiuntivi per l'API
#        
#        Returns:
#            Risposta del modello
#        """
#        try:
#            return await self.client.chat.completions.create(
#                model=self.deployment,
#                messages=messages,
#                **kwargs
#            )
#        except Exception as e:
#            logger.error(f"OpenAI API error: {e}")
#            raise
#
#    async def generate_response_with_tools(
#        self,
#        messages: list[dict[str, str]],
#        tools: list[dict[str, Any]],
#        tool_choice: str = "auto",
#        **kwargs
#    ):
#        """
#        Chat completion con function calling.
#        
#        Args:
#            messages: Lista di messaggi
#            tools: Definizioni degli strumenti disponibili
#            tool_choice: "auto", "none", o nome specifico
#            **kwargs: Parametri aggiuntivi
#        
#        Returns:
#            Risposta del modello con eventuali tool_calls
#        """
#        try:
#            return await self.client.chat.completions.create(
#                model=self.deployment,
#                messages=messages,
#                tools=tools,
#                tool_choice=tool_choice,
#                **kwargs
#            )
#        except Exception as e:
#            logger.error(f"Tool-call OpenAI error: {e}")
#            raise
#
#    async def stream_response(
#        self,
#        messages: list[dict[str, str]],
#        **kwargs
#    ):
#        """
#        Streaming chat response.
#        
#        Args:
#            messages: Lista di messaggi
#            **kwargs: Parametri aggiuntivi
#        
#        Yields:
#            Chunks della risposta
#        """
#        try:
#            stream = await self.client.chat.completions.create(
#                model=self.deployment,
#                messages=messages,
#                stream=True,
#                **kwargs
#            )
#            async for chunk in stream:
#                yield chunk
#        except Exception as e:
#            logger.error(f"Streaming error: {e}")
#            raise
#
#    async def stream_response_with_tools(
#        self,
#        messages: list[dict[str, str]],
#        tools: list[dict[str, Any]],
#        tool_choice: str = "auto",
#        **kwargs
#    ):
#        """
#        Streaming con tool-calls.
#        
#        Args:
#            messages: Lista di messaggi
#            tools: Definizioni degli strumenti
#            tool_choice: Strategia di selezione tool
#            **kwargs: Parametri aggiuntivi
#        
#        Yields:
#            Chunks della risposta con tool_calls
#        """
#        try:
#            stream = await self.client.chat.completions.create(
#                model=self.deployment,
#                messages=messages,
#                tools=tools,
#                tool_choice=tool_choice,
#                stream=True,
#                **kwargs
#            )
#            async for chunk in stream:
#                yield chunk
#        except Exception as e:
#            logger.error(f"Streaming tool error: {e}")
#            raise
#
#
#def get_llm_provider() -> LLMProvider:
#    """Restituisce l'istanza del provider (lazy loading)."""
#    return LLMProvider.from_settings()









from openai import AsyncAzureOpenAI
from typing import List, Dict, Any, Generator, Optional
from loguru import logger
from azure.identity import get_bearer_token_provider
from core.settings import settings
from utils.azure_credentials import azure_client_credentials
class LLMProvider:
    """
    Async wrapper for the OpenAI chat completion API.
    Fully compatible with iterative tool-calls and async workflows.
    """

    def __init__(self, api_key: str, endpoint: str, deployment: str, api_version: str):
        self.api_key = api_key
        self.endpoint = endpoint
        self.deployment = deployment
        self.api_version = api_version
        if not self.api_key:
            raise ValueError("AZURE_OPENAI_API_KEY is not set")
        if not self.endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT is not set")
        if not self.deployment:
            raise ValueError("AZURE_OPENAI_DEPLOYMENT is not set")
        if not self.api_version:    
            raise ValueError("AZURE_OPENAI_API_VERSION is not set")

        cred = azure_client_credentials.get_credential()
        self.client = AsyncAzureOpenAI(
            api_key=self.api_key,
            azure_endpoint=self.endpoint,
            api_version=self.api_version,
            default_headers={
                "api-key": self.api_key,
                "Ocp-Apim-Subscription-Key": self.api_key
            },
            azure_ad_token_provider=get_bearer_token_provider(cred, settings.azure_openai.scope),
        )

    @classmethod
    def from_settings(cls):
        """
        Convenient factory method to build provider from your Settings() object.
        """
        return cls(
            api_key=settings.azure_openai.api_key,
            endpoint=settings.azure_openai.endpoint,
            deployment=settings.azure_openai.chat_model_deployment,
            api_version=settings.azure_openai.api_version,
        )

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ):
        """
        Standard async chat completion.
        """
        try:
            return await self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                **kwargs
            )
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise

    async def generate_response_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        **kwargs
    ):
        """
        Async chat completion with OpenAI tools.
        """
        try:
            return await self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                **kwargs
            )
        except Exception as e:
            logger.error(f"Tool-call OpenAI error: {e}")
            raise

    async def stream_response(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ):
        """
        Streaming chat response in async mode.

        Yields chunks one by one.
        """
        try:
            stream = await self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                stream=True,
                **kwargs
            )

            async for chunk in stream:
                yield chunk

        except Exception as e:
            logger.error(f"Streaming error: {e}")
            raise

    async def stream_response_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        **kwargs
    ):
        """
        Async tool-enabled streaming response.
        """
        try:
            stream = await self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                stream=True,
                **kwargs
            )

            async for chunk in stream:
                yield chunk

        except Exception as e:
            logger.error(f"Streaming tool error: {e}")
            raise