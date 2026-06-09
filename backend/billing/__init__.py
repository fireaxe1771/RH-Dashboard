"""Azure billing analytics integration package.

Houses the billing API clients, sync orchestration, AI vectorization, and
the APScheduler job definitions. Custom exceptions are defined here so they
can be imported from ``billing`` by any submodule without circular imports.
"""


class BillingAPIError(Exception):
    """Raised when an Azure billing API call fails after all retries are exhausted."""

    def __init__(self, message: str, status_code: int | None = None, error_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class BillingConfigError(Exception):
    """Raised when billing configuration is incomplete or invalid."""
    pass


class VectorizerError(Exception):
    """Raised when embedding generation or vector search fails."""
    pass
