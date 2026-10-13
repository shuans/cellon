"""
A raised built-in error answers with the status its class declares.

`cello.exceptions.*` sets `status_code` on each class and documented the mapping
as 1:1 onto the Rust `AppError` variants — but nothing read the attribute, so
every exception raised in a handler became an internal error (500). The status
is now carried: `raise NotFoundError(...)` answers 404 with the same RFC 7807
body a route miss produces, `raise ConflictError(...)` answers 409, and so on.

An exception that declares no status (any ordinary Python error such as
`ValueError`) is still an internal error, both in status and in the body shape.
"""

import threading
import time

import pytest

requests = pytest.importorskip("requests")

from cello import (
    App,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)

_PORT = [18800]


def _next_port():
    _PORT[0] += 1
    return _PORT[0]


def _serve(app):
    """Start ``app`` on a daemon thread and wait until it answers."""
    port = _next_port()
    threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port), daemon=True
    ).start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    last = None
    while time.time() < deadline:
        try:
            requests.get(base + "/__ping__", timeout=0.5)
            return base
        except Exception as e:  # not up yet
            last = e
            time.sleep(0.1)
    raise RuntimeError(f"server on :{port} never came up: {last}")


@pytest.fixture(scope="module")
def errors_server():
    app = App()

    @app.get("/__ping__")
    def ping(request):
        return {"ok": True}

    @app.get("/missing")
    def missing(request):
        raise NotFoundError("user 42 missing")

    @app.get("/conflict")
    def conflict(request):
        raise ConflictError("email already taken")

    @app.get("/invalid")
    def invalid(request):
        raise ValidationError("bad payload")

    @app.get("/busy")
    def busy(request):
        raise RateLimitError("slow down")

    @app.get("/boom")
    def boom(request):
        raise ValueError("no status here")

    return _serve(app)


def test_not_found_error_answers_404(errors_server):
    r = requests.get(f"{errors_server}/missing", timeout=5)
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"

    body = r.json()
    # Same body a route miss produces, with the exception's own message.
    assert body["type"] == "/errors/not-found"
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["detail"] == "user 42 missing"


def test_other_declared_statuses(errors_server):
    assert requests.get(f"{errors_server}/conflict", timeout=5).status_code == 409
    assert requests.get(f"{errors_server}/busy", timeout=5).status_code == 429

    r = requests.get(f"{errors_server}/invalid", timeout=5)
    assert r.status_code == 422
    assert r.json()["type"] == "/errors/validation"


def test_undeclared_status_is_still_internal(errors_server):
    r = requests.get(f"{errors_server}/boom", timeout=5)
    assert r.status_code == 500
    # An internal error keeps the Python exception type, which is diagnostic.
    body = r.json()
    assert body["type"] == "/errors/python-exception"
    assert "ValueError" in body["detail"]


@pytest.fixture(scope="module")
def handled_server():
    """An exception handler still receives the original Python exception."""
    app = App()
    seen = {}

    @app.get("/__ping__")
    def ping(request):
        return {"ok": True}

    @app.exception_handler(NotFoundError)
    def on_not_found(request, exc):
        seen["exc"] = exc
        # Returning a Response keeps full control of the status code.
        return {"caught": str(exc)}

    @app.get("/missing")
    def missing(request):
        raise NotFoundError("user 42 missing")

    return _serve(app), seen


def test_exception_handler_gets_the_original_exception(handled_server):
    base, seen = handled_server
    r = requests.get(f"{base}/missing", timeout=5)

    # The handler ran, and it was handed the exception the route raised.
    assert isinstance(seen["exc"], NotFoundError)
    assert str(seen["exc"]) == "user 42 missing"
    assert seen["exc"].status_code == 404
    # A handler's dict is serialized at the error's status.
    assert r.status_code == 404
    assert r.json() == {"caught": "user 42 missing"}
