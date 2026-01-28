"""ETG API client exceptions."""


class ETGClientError(Exception):
    """Base exception for ETG client errors."""



class ETGAuthError(ETGClientError):
    """Authentication failed."""



class ETGAPIError(ETGClientError):
    """API returned an error response."""



class ETGNetworkError(ETGClientError):
    """Network-related error occurred."""

