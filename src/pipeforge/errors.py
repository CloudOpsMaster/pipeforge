"""User-facing errors must not include raw configuration or secret values."""


class ConfigError(Exception):
    """Configuration cannot be validated or resolved."""


class ExecutionError(Exception):
    """The runner cannot start or supervise a command."""
