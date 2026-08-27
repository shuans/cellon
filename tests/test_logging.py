"""
Tests for structured logging (issue #12): LogFormat, LoggingConfig,
configure_logging, and App.configure_logging.
"""

import pytest

from cello import App
from cello._cello import LogFormat, LoggingConfig
from cello._cello import configure_logging as configure_logging_native


def test_log_format_enum():
    assert LogFormat.TEXT != LogFormat.JSON
    assert isinstance(LogFormat.TEXT, LogFormat)
    assert isinstance(LogFormat.JSON, LogFormat)


def test_logging_config_defaults():
    config = LoggingConfig()
    assert config.format == LogFormat.TEXT
    assert config.level == "info"
    assert config.include_trace_context is True
    assert config.exclude_paths == []


def test_logging_config_kwargs():
    config = LoggingConfig(
        format=LogFormat.JSON,
        level="debug",
        include_trace_context=False,
        exclude_paths=["/health", "/metrics"],
        log_body=True,
        log_headers=True,
    )
    assert config.format == LogFormat.JSON
    assert config.level == "debug"
    assert config.include_trace_context is False
    assert config.exclude_paths == ["/health", "/metrics"]
    assert config.log_body is True
    assert config.log_headers is True


def test_logging_config_mutation():
    config = LoggingConfig()
    config.format = LogFormat.JSON
    config.level = "warn"
    assert config.format == LogFormat.JSON
    assert config.level == "warn"


def test_configure_logging_accepts_config():
    # Idempotent; a subscriber may already be installed, but the call must not
    # raise.
    config = LoggingConfig(format=LogFormat.JSON, level="info", exclude_paths=["/health"])
    configure_logging_native(config)
    configure_logging_native(config)


def test_configure_logging_kwargs():
    configure_logging_native(
        LoggingConfig(
            format=LogFormat.TEXT,
            level="error",
            include_trace_context=True,
            exclude_paths=None,
            log_body=False,
            log_headers=False,
        )
    )


def test_app_configure_logging_json_string():
    app = App()
    app.configure_logging(format="json", level="INFO", exclude_paths=["/health"])
    assert app is not None


def test_app_configure_logging_text_string():
    app = App()
    app.configure_logging(format="text", level="warn")


def test_app_configure_logging_bad_format():
    app = App()
    with pytest.raises(ValueError):
        app.configure_logging(format="yaml")


def test_app_configure_logging_with_log_format_enum():
    from cello.logging import LogFormat as PyLogFormat

    app = App()
    app.configure_logging(
        format=PyLogFormat.JSON,
        level="debug",
        include_trace_context=True,
        exclude_paths=["/metrics"],
    )
    app.enable_logging()
    assert app is not None


def test_cello_logging_module():
    from cello.logging import LogFormat as ModuleLogFormat, configure_logging, get_logger

    assert ModuleLogFormat is LogFormat
    assert ModuleLogFormat.JSON == LogFormat.JSON
    configure_logging(format=ModuleLogFormat.JSON, level="info")
    logger = get_logger("test")
    assert logger.name == "test"
