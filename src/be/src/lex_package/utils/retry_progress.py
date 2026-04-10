# lex_package/utils/retry_progress.py
import logging, re

try:
    from rich.console import Console
    from rich.progress import (
        Progress,
        SpinnerColumn,
        BarColumn,
        TimeElapsedColumn,
        TextColumn,
    )
    _RICH_AVAILABLE = True
except ModuleNotFoundError:
    # Rich is an optional UX dependency; the package should still work without it.
    _RICH_AVAILABLE = False

_RETRY_RE = re.compile(r"Retrying request to /chat/completions in ([\d\.]+)")

class OpenAIRetryProgressHandler(logging.Handler):
    """
    Converte i log di retry dell'SDK OpenAI in una barra di avanzamento Rich.
    Non modifica il flusso di controllo della chiamata a openai.
    """

    MAX_RETRIES = 10  # metti 15 se hai un back-off più lungo

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if _RICH_AVAILABLE:
            self.console = Console()
            self.progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]Richiesta LLM"),
                BarColumn(bar_width=None),
                TimeElapsedColumn(),
                console=self.console,
                transient=True,  # scompare al termine
            )
        else:
            self.console = None
            self.progress = None
        self._task_id = None
        self._attempt = 0
        global _llm_progress_handler
        _llm_progress_handler = self

    # ------------------------------------------------------------
    def emit(self, record: logging.LogRecord) -> None:
        if not _RICH_AVAILABLE:
            return
        msg = record.getMessage()
        m = _RETRY_RE.search(msg)

        # if this is a retry → advance the bar (or start it if first retry)
        if m:
            if self._task_id is None:
                self.progress.start()
                self._task_id = self.progress.add_task(
                    "Richiesta LLM", total=self.MAX_RETRIES
                )
            self._attempt += 1
            self.progress.update(self._task_id, advance=1)

            # if we've now reached MAX_RETRIES → stop it
            if self._attempt >= self.MAX_RETRIES:
                self.progress.stop()
                self._reset()
            return

        # **non-retry message** → if bar is running, stop immediately
        if self._task_id is not None:
            self.progress.stop()
            self._reset()

    def _reset(self):
        self._task_id = None
        self._attempt = 0

_llm_progress_handler: OpenAIRetryProgressHandler | None = None


def stop_llm_progress():
    """
    Ferma (se presente) la barra Rich visualizzata dall'handler.
    Va chiamato dagli strati dell'applicazione quando la richiesta
    all'LLM è terminata.
    """
    global _llm_progress_handler
    if (
        _llm_progress_handler
        and _RICH_AVAILABLE
        and _llm_progress_handler._task_id
        and _llm_progress_handler.progress
    ):
        _llm_progress_handler.progress.stop()
        _llm_progress_handler._reset()
