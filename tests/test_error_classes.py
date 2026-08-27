"""
Tests for the built-in error exception classes (NotFoundError, etc.).

These classes were previously documented but not importable; they now map 1:1
onto the Rust AppError variants and work with ``@app.exception_handler``.
"""

import pytest

from cello import (
    App,
    AuthenticationError,
    AuthorizationError,
    BadRequestError,
    CelloError,
    ConflictError,
    InternalServerError,
    NotFoundError,
    RateLimitError,
    TimeoutError,
    ValidationError,
)


def test_all_error_classes_importable():
    for cls in (
        CelloError,
        ValidationError,
        NotFoundError,
        AuthenticationError,
        AuthorizationError,
        BadRequestError,
        ConflictError,
        RateLimitError,
        TimeoutError,
        InternalServerError,
    ):
        assert isinstance(cls, type)
        assert issubclass(cls, Exception)


def test_status_codes():
    assert CelloError.status_code == 500
    assert ValidationError.status_code == 422
    assert NotFoundError.status_code == 404
    assert AuthenticationError.status_code == 401
    assert AuthorizationError.status_code == 403
    assert BadRequestError.status_code == 400
    assert ConflictError.status_code == 409
    assert RateLimitError.status_code == 429
    assert TimeoutError.status_code == 504
    assert InternalServerError.status_code == 500


def test_message_defaults_and_detail():
    err = NotFoundError("User 42 missing")
    assert err.message == "User 42 missing"
    assert str(err) == "User 42 missing"

    err = ValidationError(detail={"age": "invalid"})
    assert err.message == "ValidationError"
    assert err.detail == {"age": "invalid"}

    err = ConflictError()
    assert str(err) == "ConflictError"


def test_exception_handler_accepts_error_class():
    """`@app.exception_handler(NotFoundError)` registers by class name."""
    app = App()
    calls = []

    @app.exception_handler(NotFoundError)
    def handle_not_found(request, exc):
        calls.append((request, exc))
        return {"handled": True}

    # The decorator stores the handler under the class name without raising.
    assert handle_not_found is not None
    assert calls == []


def test_exception_handler_by_name_string():
    app = App()

    @app.exception_handler("ValidationError")
    def handle_validation(request, exc):
        return {"handled": "validation"}

    assert handle_validation is not None


def test_raise_and_catch():
    with pytest.raises(NotFoundError) as exc_info:
        raise NotFoundError("gone")
    assert exc_info.value.status_code == 404
    assert "gone" in str(exc_info.value)

    with pytest.raises(RateLimitError):
        raise RateLimitError("slow down")
