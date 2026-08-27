"""
Built-in error exception classes for Cello.

These map 1:1 onto the Rust ``AppError`` variants and can be raised from any
handler. Pair them with ``@app.exception_handler(...)`` to control the response
shape, or leave them unhandled to get the default RFC 7807 Problem Details
response with the matching status code.

Example:
    from cello import App, NotFoundError

    app = App()

    @app.get("/users/{id}")
    def get_user(request):
        user = find_user(request.params["id"])
        if user is None:
            raise NotFoundError(f"User {request.params['id']} not found")
        return user
"""

from __future__ import annotations

from typing import Any, Optional


class CelloError(Exception):
    """Base class for all built-in Cello errors."""

    #: Default HTTP status code for this error class.
    status_code: int = 500

    def __init__(self, message: Optional[str] = None, *, detail: Any = None):
        self.message = message if message is not None else self.__class__.__name__
        self.detail = detail
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.message!r})"


class ValidationError(CelloError):
    """Raised when a request fails validation (HTTP 422)."""

    status_code = 422


class NotFoundError(CelloError):
    """Raised when a requested resource does not exist (HTTP 404)."""

    status_code = 404


class AuthenticationError(CelloError):
    """Raised when the caller cannot be authenticated (HTTP 401)."""

    status_code = 401


class AuthorizationError(CelloError):
    """Raised when the caller lacks permission (HTTP 403)."""

    status_code = 403


class BadRequestError(CelloError):
    """Raised when a request is malformed (HTTP 400)."""

    status_code = 400


class ConflictError(CelloError):
    """Raised when a request conflicts with current state (HTTP 409)."""

    status_code = 409


class RateLimitError(CelloError):
    """Raised when a request exceeds the rate limit (HTTP 429)."""

    status_code = 429


class TimeoutError(CelloError):
    """Raised when an operation exceeds its deadline (HTTP 504)."""

    status_code = 504


class InternalServerError(CelloError):
    """Raised when an unexpected server-side failure occurs (HTTP 500)."""

    status_code = 500


__all__ = [
    "CelloError",
    "ValidationError",
    "NotFoundError",
    "AuthenticationError",
    "AuthorizationError",
    "BadRequestError",
    "ConflictError",
    "RateLimitError",
    "TimeoutError",
    "InternalServerError",
]
