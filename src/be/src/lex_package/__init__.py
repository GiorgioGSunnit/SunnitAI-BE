import logging
from .utils.retry_progress import OpenAIRetryProgressHandler

_oa_logger = logging.getLogger("openai._base_client")
_oa_logger.setLevel(logging.INFO)  # o DEBUG se vuoi più dettagli

if not any(isinstance(h, OpenAIRetryProgressHandler) for h in _oa_logger.handlers):
    _oa_logger.addHandler(OpenAIRetryProgressHandler())
    _oa_logger.propagate = False
