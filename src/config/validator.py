"""Configuration validation module."""


class ConfigValidationError(Exception):
    """Raised when configuration validation fails (strict mode)."""

    def __init__(self, messages: list):
        self.messages = messages
        super().__init__(self._format_messages())

    def _format_messages(self) -> str:
        if not self.messages:
            return "Config validation failed."
        return "Config validation failed:\n  " + "\n  ".join(self.messages)
