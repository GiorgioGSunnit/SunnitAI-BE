"""
Azure credentials stub — replaced for server-only deployment.
No Azure Identity required; authentication is via plain API keys in .env.
"""
import logging

logger = logging.getLogger(__name__)


class _NoOpCredential:
    """Stand-in for DefaultAzureCredential when Azure Identity is not used."""

    def get_token(self, *scopes, **kwargs):
        raise RuntimeError(
            "Azure Identity credentials are not configured. "
            "Use LLM_API_KEY and LLM_BASE_URL in .env instead."
        )


class AzureClientCredentials:
    """No-op credentials manager for non-Azure (server-only) deployments."""

    def get_token(self, scope: str) -> str:
        raise RuntimeError(
            "Azure AD tokens are not supported in server-only mode. "
            "Use LLM_API_KEY for authentication."
        )

    def get_token_struct_for_odbc(self):
        raise RuntimeError("Azure AD ODBC tokens are not supported in server-only mode.")

    def get_credential(self):
        return _NoOpCredential()


# Global singleton — matches the original API surface
azure_client_credentials = AzureClientCredentials()
