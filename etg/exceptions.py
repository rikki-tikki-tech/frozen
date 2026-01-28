"""ETG API client exceptions."""


class ETGClientError(Exception):
    """Base exception for ETG client errors."""


class ETGAuthError(ETGClientError):
    """Authentication failed."""


class ETGAuthInvalidCredentialsError(ETGAuthError):
    """Authentication failed due to invalid credentials."""

    def __init__(self) -> None:
        """Initialize with default message."""
        super().__init__("Authentication failed: Invalid credentials")


class ETGAuthForbiddenError(ETGAuthError):
    """Authentication failed due to access being forbidden."""

    def __init__(self) -> None:
        """Initialize with default message."""
        super().__init__("Authentication failed: Access forbidden")


class ETGAPIError(ETGClientError):
    """API returned an error response."""


class ETGAPIHttpError(ETGAPIError):
    """API returned an HTTP error status."""

    def __init__(self, status_code: int, response_text: str) -> None:
        """Initialize with HTTP status code and response text."""
        super().__init__(f"API error (HTTP {status_code}): {response_text[:500]}")
        self.status_code = status_code
        self.response_text = response_text


class ETGAPIInvalidJsonError(ETGAPIError):
    """API returned invalid JSON response."""

    def __init__(self, error: Exception) -> None:
        """Initialize with the JSON parsing error."""
        super().__init__(f"Invalid JSON response: {error}")
        self.original_error = error


class ETGAPIResponseError(ETGAPIError):
    """API returned an error in the response body."""

    def __init__(self, error_info: object) -> None:
        """Initialize with error info from API response."""
        super().__init__(f"API error: {error_info}")
        self.error_info = error_info


class ETGNetworkError(ETGClientError):
    """Network-related error occurred."""


class ETGTimeoutError(ETGNetworkError):
    """Request timed out."""

    def __init__(self) -> None:
        """Initialize with default message."""
        super().__init__("Request timed out")


class ETGConnectionError(ETGNetworkError):
    """Connection error occurred."""

    def __init__(self, error: Exception) -> None:
        """Initialize with the connection error."""
        super().__init__(f"Connection error: {error}")
        self.original_error = error


class ETGRequestError(ETGNetworkError):
    """Request failed."""

    def __init__(self, error: Exception) -> None:
        """Initialize with the request error."""
        super().__init__(f"Request failed: {error}")
        self.original_error = error
