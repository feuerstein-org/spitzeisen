"""Exceptions raised by Spitzeisen's code-generation pipeline."""


class CodegenError(Exception):
    """An expected fatal failure that should be presented without a traceback."""

    def __init__(self, *, header: str = "Unable to generate the client", detail: str | None = None) -> None:
        """Store both a concise heading and optional user-facing detail."""
        self.header = header
        self.detail = detail
        super().__init__(detail or header)
