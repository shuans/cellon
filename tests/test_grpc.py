"""
Tests for gRPC completion (issue #13): client/bidi streaming, gRPC-Web
framing, the gRPC-Web HTTP bridge, and the protobuf codec.
"""

import asyncio
import json

import pytest

from cello import App
from cello.grpc import (
    GrpcError,
    GrpcRequest,
    GrpcResponse,
    GrpcServer,
    GrpcService,
    ProtobufCodec,
    _frame_message,
    _unframe_messages,
    grpc_method,
    grpc_web_request_handler,
)


# ── Framing ──────────────────────────────────────────────────────────────────


def test_frame_roundtrip():
    payload = b'{"x": 1}'
    framed = _frame_message(payload)
    assert framed[0] == 0
    assert int.from_bytes(framed[1:5], "big") == len(payload)
    assert _unframe_messages(framed) == [payload]


def test_unframe_multiple_messages():
    a = _frame_message(b"first")
    b = _frame_message(b"second")
    messages = _unframe_messages(a + b)
    assert messages == [b"first", b"second"]


def test_unframe_empty():
    assert _unframe_messages(b"") == []
    assert _unframe_messages(b"\x00\x00\x00") == []  # truncated header


# ── Method cardinality ───────────────────────────────────────────────────────


def test_grpc_method_flags():
    @grpc_method
    def unary(request):
        return GrpcResponse.ok({"ok": True})

    @grpc_method(stream=True)
    def server_stream(request):
        yield GrpcResponse.ok({"i": 1})

    @grpc_method(client_stream=True)
    async def client_stream(requests):
        return GrpcResponse.ok({"count": 1})

    @grpc_method(bidi=True)
    async def bidi(requests):
        yield GrpcResponse.ok({"echo": True})

    assert not unary._grpc_stream and not unary._grpc_client_stream
    assert server_stream._grpc_stream and not server_stream._grpc_client_stream
    assert client_stream._grpc_client_stream and not client_stream._grpc_stream
    assert bidi._grpc_stream and bidi._grpc_client_stream and bidi._grpc_bidi


class StreamingService(GrpcService):
    @grpc_method(bidi=True)
    async def RouteChat(self, requests):
        async for request in requests:
            yield GrpcResponse.ok({"echo": request.data.get("text", "")})

    @grpc_method(client_stream=True)
    async def RecordRoute(self, requests):
        count = 0
        async for _request in requests:
            count += 1
        return GrpcResponse.ok({"count": count})


def test_service_discovers_streaming_methods():
    service = StreamingService()
    methods = {m["name"]: m for m in service.get_methods()}
    assert methods["RouteChat"]["bidi"] is True
    assert methods["RouteChat"]["stream"] is True
    assert methods["RouteChat"]["client_stream"] is True
    assert methods["RecordRoute"]["client_stream"] is True
    assert methods["RecordRoute"]["stream"] is False


def test_server_registers_services():
    server = GrpcServer(config=None)
    server.register_service(StreamingService())
    assert server.get_services() == ["StreamingService"]


# ── Protobuf codec ───────────────────────────────────────────────────────────


def test_protobuf_codec_json_fallback():
    codec = ProtobufCodec()
    assert codec.encode({"a": 1}) == b'{"a":1}'
    assert codec.decode(b'{"a":1}') == {"a": 1}


def test_protobuf_codec_with_message_type():
    try:
        from google.protobuf import descriptor_pb2
    except ImportError:
        pytest.skip("protobuf not installed")

    codec = ProtobufCodec(
        request_type=descriptor_pb2.DescriptorProto,
        response_type=descriptor_pb2.DescriptorProto,
    )
    wire = codec.encode({"name": "Echo"})
    decoded = codec.decode(wire)
    assert decoded.get("name") == "Echo"


# ── gRPC-Web bridge ──────────────────────────────────────────────────────────


class FakeRequest:
    def __init__(self, path, body, content_type="application/grpc-web+json"):
        self.path = path
        self._body = body
        self.content_type = content_type

    def get_header(self, name):
        return self.content_type if name == "content-type" else None

    def body(self):
        return self._body


@pytest.mark.asyncio
async def test_grpc_web_dispatch():
    class EchoService(GrpcService):
        @grpc_method
        async def Echo(self, request):
            return GrpcResponse.ok({"echo": request.data.get("message", "")})

    server = GrpcServer(config=None)
    server.register_service(EchoService())
    server._running = True  # allow direct dispatch in tests

    handler = grpc_web_request_handler(server)
    body = _frame_message(b'{"message": "hi"}')
    request = FakeRequest("/grpc.web/EchoService/Echo", body)

    response = await handler(request)
    assert response.status == 200
    assert response.headers.get("grpc-status") == "0"

    payloads = _unframe_messages(bytes(response.body()))
    assert json.loads(payloads[0]) == {"echo": "hi"}


@pytest.mark.asyncio
async def test_grpc_web_unknown_service():
    server = GrpcServer(config=None)
    server._running = True
    handler = grpc_web_request_handler(server)
    body = _frame_message(b"{}")
    request = FakeRequest("/grpc.web/Missing.Service/Call", body)
    response = await handler(request)
    assert response.headers.get("grpc-status") == str(GrpcError.NOT_FOUND)


def test_app_enable_grpc_web_registers_route():
    app = App()
    app.enable_grpc(config=None)
    app.enable_grpc_web("/grpc.web")
    assert app is not None


def test_grpc_web_service_method_splits():
    from cello.grpc import _grpc_web_service_method

    assert _grpc_web_service_method("/grpc.web/pkg.Service/Method") == (
        "pkg.Service",
        "Method",
    )
    assert _grpc_web_service_method("/Service") == ("Service", "")
