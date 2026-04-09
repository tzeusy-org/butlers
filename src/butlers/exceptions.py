"""Butler-specific exceptions."""


class RuntimeBinaryNotFoundError(RuntimeError):
    """Raised when the runtime adapter's binary is not found on PATH."""
