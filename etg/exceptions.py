"""ETG API client exceptions."""


class ETGClientError(Exception):
    """Base exception for ETG client errors."""

    pass


class ETGAuthError(ETGClientError):
    """Authentication failed."""

    pass


class ETGAPIError(ETGClientError):
    """API returned an error response."""

    pass


class ETGNetworkError(ETGClientError):
    """Network-related error occurred."""

    pass
