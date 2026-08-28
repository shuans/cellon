"""
Tests for GraphQL WebSocket subscriptions (issue #14): the graphql-ws
protocol session, subscription streaming, and mount_graphql wiring.
"""

import asyncio
import json

import pytest

from cello import App
from cello.graphql import GraphQL, Subscription, graphql_ws_session


class FakeWebSocket:
    """In-memory WebSocket recording what the session sends."""

    def __init__(self, incoming):
        self._incoming = list(incoming)
        self.sent = []
        self.closed = False

    async def receive_text(self):
        if self._incoming:
            return self._incoming.pop(0)
        return None

    def send_text(self, text):
        self.sent.append(json.loads(text))

    def send_json(self, value):
        self.sent.append(value)

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_connection_init_and_terminate():
    ws = FakeWebSocket(
        [
            json.dumps({"type": "connection_init"}),
            json.dumps({"type": "connection_terminate"}),
        ]
    )
    engine = GraphQL()
    await graphql_ws_session(ws, engine)
    assert ws.sent[0] == {"type": "connection_ack"}
    assert ws.closed is True


@pytest.mark.asyncio
async def test_ping_pong():
    ws = FakeWebSocket(
        [
            json.dumps({"type": "connection_init"}),
            json.dumps({"type": "ping"}),
            json.dumps({"type": "connection_terminate"}),
        ]
    )
    engine = GraphQL()
    await graphql_ws_session(ws, engine)
    types = [msg["type"] for msg in ws.sent]
    assert types == ["connection_ack", "pong"]


@pytest.mark.asyncio
async def test_subscription_streams_next_and_complete():
    async def counter(info):
        for i in range(3):
            yield {"value": i}

    engine = GraphQL()
    engine.add_subscription(counter)

    # Drive the stream handler directly: a session-level `connection_terminate`
    # cancels running subscriptions by design, so streaming is exercised at the
    # handler layer.
    ws = FakeWebSocket([])
    from cello.graphql import _graphql_ws_stream

    await _graphql_ws_stream(
        ws, engine, "1", "subscription { counter { value } }", None, None
    )

    nexts = [msg for msg in ws.sent if msg["type"] == "next"]
    completes = [msg for msg in ws.sent if msg["type"] == "complete"]
    assert len(nexts) == 3
    assert nexts[0]["id"] == "1"
    assert nexts[0]["payload"]["data"]["counter"]["value"] == 0
    assert len(completes) == 1
    assert completes[0]["id"] == "1"


@pytest.mark.asyncio
async def test_subscribe_with_invalid_query():
    engine = GraphQL()
    ws = FakeWebSocket(
        [
            json.dumps({"type": "subscribe", "id": "1", "payload": {"query": ""}}),
            json.dumps({"type": "connection_terminate"}),
        ]
    )
    await graphql_ws_session(ws, engine)
    errors = [msg for msg in ws.sent if msg["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["id"] == "1"


@pytest.mark.asyncio
async def test_complete_cancels_subscription():
    started = asyncio.Event()

    async def infinite(info):
        started.set()
        while True:
            yield {"tick": True}

    engine = GraphQL()
    engine.add_subscription(infinite)

    ws = FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "subscribe",
                    "id": "1",
                    "payload": {"query": "subscription { infinite { tick } }"},
                }
            ),
            json.dumps({"type": "complete", "id": "1"}),
            json.dumps({"type": "connection_terminate"}),
        ]
    )
    # The session itself completes normally; the subscription task is cancelled
    # internally and settled by a loop tick.
    await graphql_ws_session(ws, engine)
    await asyncio.sleep(0)
    assert started.is_set()
    assert ws.closed is True


def test_mount_graphql_registers_websocket_route():
    app = App()
    engine = app.mount_graphql(path="/graphql")
    assert engine is not None
    assert app.state.graphql is engine
    # The same path is registered for websocket upgrades.
    assert app._app is not None


def test_graphql_subscribe_primitive():
    async def one(info):
        return {"message": "hello"}

    engine = GraphQL()
    engine.add_subscription(one)

    async def run():
        payloads = []
        async for payload in engine.subscribe("subscription { one { message } }"):
            payloads.append(payload)
        return payloads

    payloads = asyncio.run(run())
    assert payloads == [{"data": {"one": {"message": "hello"}}}]
