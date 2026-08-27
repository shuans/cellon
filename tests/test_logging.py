"""
Tests for structured logging (issue #12): LogFormat, LoggingConfig,
configure_logging, and App.configure_logging.
"""

import pytest

from cello import App
from cello._cello import LogFormat, LoggingConfig
from cello._cello import configure_logging as configure_logging_native


def test_log_format_enum():
    # pyo3 0.20 exposes enum variants verbatim with `.name`; instances are
    # not cached, so compare names rather than identity.
    assert LogFormat.Text.name == "Text"
    assert LogFormat.Json.name == "Json"
    assert LogFormat.Text.name != LogFormat.Json.name
    assert isinstance(LogFormat.Text, LogFormat)
    assert isinstance(LogFormat.Json, LogFormat)


def test_logging_config_defaults():
    config = LoggingConfig()
    assert config.format.name == "Text"
    assert config.level == "info"
    assert config.include_trace_context is True
    assert config.exclude_paths == []


def test_logging_config_kwargs():
    config = LoggingConfig(
        format=LogFormat.Json,
        level="debug",
        include_trace_context=False,
        exclude_paths=["/health", "/metrics"],
        log_body=True,
        log_headers=True,
    )
    assert config.format.name == "Json"
    assert config.level == "debug"
    assert config.include_trace_context is False
    assert config.exclude_paths == ["/health", "/metrics"]
    assert config.log_body is True
    assert config.log_headers is True


def test_logging_config_mutation():
    config = LoggingConfig()
    config.format = LogFormat.Json
    config.level = "warn"
    assert config.format.name == "Json"
    assert config.level == "warn"


def test_configure_logging_accepts_config():
    # Idempotent; a subscriber may already be installed, but the call must not
    # raise.
    config = LoggingConfig(format=LogFormat.Json, level="info", exclude_paths=["/health"])
    configure_logging_native(config)
    configure_logging_native(config)


def test_configure_logging_kwargs():
    configure_logging_native(
        LoggingConfig(
            format=LogFormat.Text,
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
        format=PyLogFormat.Json,
        level="debug",
        include_trace_context=True,
        exclude_paths=["/metrics"],
    )
    app.enable_logging()
    assert app is not None


def test_cello_logging_module():
    from cello.logging import LogFormat as ModuleLogFormat, configure_logging, get_logger

    assert ModuleLogFormat is LogFormat
    assert ModuleLogFormat.Json.name == LogFormat.Json.name
    configure_logging(format=ModuleLogFormat.Json, level="info")
    logger = get_logger("test")
    assert logger.name == "test"
