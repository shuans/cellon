"""
Tests for HTTP/3 (QUIC) support (issue #9): the Http3Config builder and
App.enable_http3 wiring (the QUIC/UDP listener itself is exercised in CI).
"""

import pytest

from cello import App, Http3Config, TlsConfig


def test_http3_config_defaults():
    config = Http3Config()
    assert config.max_idle_timeout == 30
    assert config.max_udp_payload_size == 1350
    assert config.initial_max_streams_bidi == 100
    assert config.enable_0rtt is False


def test_http3_config_custom():
    config = Http3Config(
        max_idle_timeout=60,
        max_udp_payload_size=1400,
        initial_max_streams_bidi=200,
        enable_0rtt=True,
    )
    assert config.max_idle_timeout == 60
    assert config.max_udp_payload_size == 1400
    assert config.initial_max_streams_bidi == 200
    assert config.enable_0rtt is True


def test_http3_config_mutation():
    config = Http3Config()
    config.enable_0rtt = True
    config.initial_max_streams_bidi = 150
    assert config.enable_0rtt is True
    assert config.initial_max_streams_bidi == 150


def test_enable_http3_records_config():
    app = App()
    app.enable_http3(Http3Config(max_idle_timeout=45))
    assert app._app.http3_config is not None


def test_enable_http3_with_tls_config(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----\n")
    key.write_text("-----BEGIN PRIVATE KEY-----\n-----END PRIVATE KEY-----\n")

    app = App()
    app.enable_tls(TlsConfig(cert_path=str(cert), key_path=str(key)))
    app.enable_http3()
    assert app._app.tls_config is not None
    assert app._app.http3_config is not None


def test_enable_http3_default():
    app = App()
    app.enable_http3()
    config = app._app.http3_config
    assert config is not None
    assert config.initial_max_streams_bidi == 100
