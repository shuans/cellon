"""
Tests for WebSocket support (issue #19): message helpers, the channel-backed
stub handle, and App.websocket registration.
"""

import pytest

from cello import App, WebSocket, WebSocketMessage


def test_message_text():
    msg = WebSocketMessage.text("hello")
    assert msg.msg_type == "text"
    assert msg.payload == "hello"
    assert msg.is_text()
    assert not msg.is_binary()
    assert not msg.is_close()


def test_message_binary():
    msg = WebSocketMessage.binary(b"\x01\x02")
    assert msg.msg_type == "binary"
    assert msg.payload == b"\x01\x02"
    assert msg.is_binary()


def test_message_close():
    msg = WebSocketMessage.close()
    assert msg.msg_type == "close"
    assert msg.is_close()


def test_websocket_stub_roundtrip():
    ws = WebSocket()
    assert ws.connected is True
    assert not ws.is_closed()

    ws.send_text("one")
    ws.send_text("two")
    ws.send_binary(b"\xaa")

    first = ws.recv()
    assert first is not None and first.payload == "one"
    second = ws.recv()
    assert second is not None and second.payload == "two"
    third = ws.recv()
    assert third is not None and third.payload == b"\xaa"

    assert ws.recv() is None


def test_websocket_stub_close():
    ws = WebSocket()
    ws.close()
    assert ws.is_closed()
    msg = ws.recv()
    assert msg is not None and msg.is_close()


def test_websocket_send_message_object():
    ws = WebSocket()
    ws.send(WebSocketMessage.text("payload"))
    assert ws.get_queued_messages()[0].payload == "payload"


def test_websocket_registration():
    app = App()

    @app.websocket("/ws")
    async def handler(ws):
        await ws.send_text("pong")

    assert handler is not None
    # Re-registration on a different path is also accepted.
    @app.websocket("/ws2")
    async def handler2(ws):
        pass

    assert app._app is not None


def test_websocket_send_json_and_receive():
    ws = WebSocket()
    ws.send_json({"a": 1, "b": [True, None]})
    msg = ws.recv()
    assert msg is not None and msg.msg_type == "text"
    assert '"a"' in msg.payload
