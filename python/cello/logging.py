"""
Structured logging for Cello.

Installs the Rust-side global `tracing_subscriber` in text or JSON format and
drives the access-log format of ``app.enable_logging()``.

Example:
    from cello.logging import configure_logging, LogFormat

    configure_logging(
        format=LogFormat.Json,
        level="INFO",
        include_trace_context=True,
        exclude_paths=["/health", "/metrics"],
    )
"""

from __future__ import annotations

from typing import List, Optional

from cello._cello import LogFormat, LoggingConfig
from cello._cello import configure_logging as _configure_logging_native

__all__ = ["LogFormat", "LoggingConfig", "configure_logging", "get_logger"]


def configure_logging(
    config: Optional[LoggingConfig] = None,
    *,
    format: Optional[LogFormat] = None,
    level: str = "info",
    include_trace_context: bool = True,
    exclude_paths: Optional[List[str]] = None,
    log_body: bool = False,
    log_headers: bool = False,
) -> None:
    """Configure global structured logging.

    Args:
        config: A ``LoggingConfig`` instance. When given, all keyword
            arguments are ignored.
        format: ``LogFormat.Json`` or ``LogFormat.Text``.
        level: Minimum level to emit: "trace", "debug", "info", "warn",
            or "error".
        include_trace_context: Attach ``trace_id``/``span_id`` fields when
            OpenTelemetry trace context is present.
        exclude_paths: Paths to exclude from the request access log.
        log_body: Include request bodies in the access log.
        log_headers: Include request headers in the access log.
    """
    if config is not None:
        _configure_logging_native(config)
        return

    resolved_format = format if format is not None else LogFormat.Text
    _configure_logging_native(
        LoggingConfig(
            format=resolved_format,
            level=level,
            include_trace_context=include_trace_context,
            exclude_paths=exclude_paths or [],
            log_body=log_body,
            log_headers=log_headers,
        )
    )


def get_logger(name: str = "cello"):
    """Return the standard-library logger for a name (for app code)."""
    import logging

    return logging.getLogger(name)
