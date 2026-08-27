"""Async gRPC integration backed by grpc.aio.

The service API intentionally uses JSON serializers so a service can be exposed
without generated protobuf code. The transport is real gRPC over HTTP/2, but the
public decorator API is a JSON generic API and is not wire-compatible with
protobuf-generated stubs.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from functools import wraps
from typing import Any, Callable, Optional


class GrpcError(Exception):
    """Exception for gRPC errors with standard status codes."""

    OK = 0
    CANCELLED = 1
    UNKNOWN = 2
    INVALID_ARGUMENT = 3
    DEADLINE_EXCEEDED = 4
    NOT_FOUND = 5
    ALREADY_EXISTS = 6
    PERMISSION_DENIED = 7
    RESOURCE_EXHAUSTED = 8
    FAILED_PRECONDITION = 9
    ABORTED = 10
    OUT_OF_RANGE = 11
    UNIMPLEMENTED = 12
    INTERNAL = 13
    UNAVAILABLE = 14
    DATA_LOSS = 15
    UNAUTHENTICATED = 16

    def __init__(self, code: int, message: str, details: str = None):
        self.code = code
        self.message = message
        self.details = details
        super().__init__(f"GrpcError(code={code}, message={message})")

    def __repr__(self) -> str:
        return f"GrpcError(code={self.code}, message={self.message!r}, details={self.details!r})"


def grpc_method(
    func: Callable = None,
    *,
    stream: bool = False,
    client_stream: bool = False,
    bidi: bool = False,
) -> Callable:
    """Mark a service method as an RPC of a given cardinality.

    Args:
        stream: Server streaming — single request, many responses.
        client_stream: Client streaming — many requests, single response.
        bidi: Bidirectional streaming — many requests, many responses.
            Implies both streaming directions.

    Streaming methods receive an async iterator of :class:`GrpcRequest` and
    return (or yield) one or more :class:`GrpcResponse` / dict values.
    """

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        wrapper._grpc_method = True
        wrapper._grpc_method_name = fn.__name__
        wrapper._grpc_stream = bool(stream) or bool(bidi)
        wrapper._grpc_client_stream = bool(client_stream) or bool(bidi)
        wrapper._grpc_bidi = bool(bidi)
        return wrapper

    return decorator(func) if func is not None else decorator


class GrpcRequest:
    """Incoming request metadata and decoded JSON payload."""

    def __init__(
        self,
        service: str,
        method: str,
        data: dict = None,
        metadata: dict = None,
    ):
        self._service = service
        self._method = method
        self._data = data or {}
        self._metadata = metadata or {}

    @property
    def service(self) -> str:
        return self._service

    @property
    def method(self) -> str:
        return self._method

    @property
    def data(self) -> dict:
        return self._data

    @property
    def metadata(self) -> dict:
        return self._metadata

    def get(self, key: str, default: Any = None) -> Any:
        """Read a payload field using dictionary-style convenience syntax."""
        return self._data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __repr__(self) -> str:
        return f"GrpcRequest(service={self._service!r}, method={self._method!r})"


class GrpcResponse:
    """Outgoing gRPC response with a JSON-compatible payload."""

    def __init__(
        self,
        data: dict = None,
        status_code: int = 0,
        message: str = "OK",
        metadata: dict = None,
    ):
        self._data = data or {}
        self._status_code = status_code
        self._message = message
        self._metadata = metadata or {}

    @property
    def data(self) -> dict:
        return self._data

    @property
    def status_code(self) -> int:
        return self._status_code

    @property
    def message(self) -> str:
        return self._message

    @property
    def metadata(self) -> dict:
        return self._metadata

    @classmethod
    def ok(cls, data: dict) -> "GrpcResponse":
        return cls(data=data, status_code=GrpcError.OK, message="OK")

    @classmethod
    def error(cls, code: int, message: str) -> "GrpcResponse":
        return cls(data=None, status_code=code, message=message)

    def __repr__(self) -> str:
        return f"GrpcResponse(status_code={self._status_code}, message={self._message!r})"


class GrpcService:
    """Base class for class-based gRPC services."""

    def __init__(self, name: str = None):
        self._name = name or getattr(self.__class__, "service_name", None) or self.__class__.__name__
        self._methods: dict[str, dict[str, Any]] = {}
        self._discover_methods()

    def _discover_methods(self) -> None:
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            attr = getattr(self, attr_name, None)
            if callable(attr) and getattr(attr, "_grpc_method", False):
                self._methods[attr._grpc_method_name] = {
                    "name": attr._grpc_method_name,
                    "handler": attr,
                    "stream": bool(attr._grpc_stream),
                    "client_stream": bool(attr._grpc_client_stream),
                    "bidi": bool(attr._grpc_bidi),
                }

    def get_methods(self) -> list[dict]:
        return [
            {
                "name": info["name"],
                "stream": info["stream"],
                "client_stream": info["client_stream"],
                "bidi": info["bidi"],
            }
            for info in self._methods.values()
        ]

    def get_name(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"GrpcService(name={self._name!r}, methods={len(self._methods)})"


_STATUS_NAMES = {
    GrpcError.CANCELLED: "CANCELLED",
    GrpcError.UNKNOWN: "UNKNOWN",
    GrpcError.INVALID_ARGUMENT: "INVALID_ARGUMENT",
    GrpcError.DEADLINE_EXCEEDED: "DEADLINE_EXCEEDED",
    GrpcError.NOT_FOUND: "NOT_FOUND",
    GrpcError.ALREADY_EXISTS: "ALREADY_EXISTS",
    GrpcError.PERMISSION_DENIED: "PERMISSION_DENIED",
    GrpcError.RESOURCE_EXHAUSTED: "RESOURCE_EXHAUSTED",
    GrpcError.FAILED_PRECONDITION: "FAILED_PRECONDITION",
    GrpcError.ABORTED: "ABORTED",
    GrpcError.OUT_OF_RANGE: "OUT_OF_RANGE",
    GrpcError.UNIMPLEMENTED: "UNIMPLEMENTED",
    GrpcError.INTERNAL: "INTERNAL",
    GrpcError.UNAVAILABLE: "UNAVAILABLE",
    GrpcError.DATA_LOSS: "DATA_LOSS",
    GrpcError.UNAUTHENTICATED: "UNAUTHENTICATED",
}


def _json_loads(payload: bytes) -> dict:
    if not payload:
        return {}
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("gRPC JSON request must be an object")
    return value


def _json_dumps(value: Any) -> bytes:
    if isinstance(value, GrpcResponse):
        value = value.data
    return json.dumps(value if value is not None else {}, separators=(",", ":")).encode("utf-8")


def _metadata_dict(context) -> dict:
    return {key: value for key, value in context.invocation_metadata()}


class GrpcServer:
    """Real asyncio gRPC server using generic JSON-serialized handlers.

    This is a standard gRPC HTTP/2 transport with JSON payload serialization;
    protobuf code generation is intentionally outside this convenience API.
    """

    def __init__(self, config: Any = None, host: str = None, port: int = None):
        self._config = config
        self._services: dict[str, GrpcService] = {}
        self._running = False
        self._address: Optional[str] = None
        self._server = None
        self._grpc = None
        self._active_calls = 0
        if host is not None or port is not None:
            resolved_host = host or "127.0.0.1"
            resolved_port = 50051 if port is None else port
            self._address = f"{resolved_host}:{resolved_port}"
        elif config is not None and getattr(config, "address", None):
            self._address = config.address

    def register_service(self, service: GrpcService) -> None:
        if not isinstance(service, GrpcService):
            raise TypeError(f"Expected GrpcService instance, got {type(service).__name__}")
        name = service.get_name()
        if name in self._services:
            raise ValueError(f"Service '{name}' is already registered")
        if self._running:
            raise RuntimeError("Services must be registered before the gRPC server starts")
        self._services[name] = service

    def get_services(self) -> list[str]:
        return list(self._services.keys())

    async def _invoke(self, service_name: str, method_name: str, payload: dict, context):
        service = self._services.get(service_name)
        if service is None:
            await context.abort(
                self._grpc.StatusCode.NOT_FOUND,
                f"Service '{service_name}' was not found",
            )
            return None
        method_info = service._methods.get(method_name)
        if method_info is None:
            await context.abort(
                self._grpc.StatusCode.UNIMPLEMENTED,
                f"Method '{method_name}' was not found",
            )
            return None

        request = GrpcRequest(service_name, method_name, payload, _metadata_dict(context))
        self._active_calls += 1
        call_error = None
        try:
            value = method_info["handler"](request)
            if inspect.isawaitable(value):
                value = await value
        except GrpcError as exc:
            call_error = (
                getattr(self._grpc.StatusCode, _STATUS_NAMES.get(exc.code, "UNKNOWN")),
                exc.message,
            )
            value = None
        except Exception as exc:
            call_error = (self._grpc.StatusCode.INTERNAL, str(exc))
            value = None
        finally:
            self._active_calls -= 1

        # Keep context.abort outside the invocation exception handlers.  The
        # aio runtime raises from abort to terminate the RPC, and that control
        # flow must never be wrapped as a second INTERNAL error.
        if call_error is not None:
            await context.abort(*call_error)
            return None

        if isinstance(value, GrpcResponse):
            if value.metadata:
                await context.send_initial_metadata(tuple(value.metadata.items()))
            if value.status_code != GrpcError.OK:
                status = getattr(self._grpc.StatusCode, _STATUS_NAMES.get(value.status_code, "UNKNOWN"))
                await context.abort(status, value.message)
            return value.data
        return value

    async def _invoke_stream(
        self,
        service_name: str,
        method_name: str,
        request_iter,
        context,
    ) -> Any:
        """Invoke a client-streaming or bidi method with a request iterator.

        The handler receives an async iterator of :class:`GrpcRequest` objects
        (one per incoming message) and may return a single value (client
        streaming) or yield responses (bidirectional streaming).
        """
        service = self._services.get(service_name)
        if service is None:
            await context.abort(self._grpc.StatusCode.NOT_FOUND, f"Service '{service_name}' was not found")
            return None
        method_info = service._methods.get(method_name)
        if method_info is None:
            await context.abort(
                self._grpc.StatusCode.UNIMPLEMENTED, f"Method '{method_name}' was not found"
            )
            return None

        async def request_stream():
            async for request in request_iter:
                try:
                    yield GrpcRequest(
                        service_name,
                        method_name,
                        _json_loads(request),
                        _metadata_dict(context),
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    await context.abort(self._grpc.StatusCode.INVALID_ARGUMENT, str(exc))
                    return

        value = method_info["handler"](request_stream())
        if inspect.isawaitable(value):
            value = await value
        return value

    def _build_handlers(self):
        handlers = {}
        for service_name, service in self._services.items():
            method_handlers = {}
            for method_name, method_info in service._methods.items():
                path_service = service_name
                if method_info.get("bidi"):
                    async def bidi_handler(request_iterator, context, _service=path_service, _method=method_name):
                        value = await self._invoke_stream(_service, _method, request_iterator, context)
                        if hasattr(value, "__aiter__"):
                            async for item in value:
                                yield item.data if isinstance(item, GrpcResponse) else item
                        elif value is not None:
                            yield value.data if isinstance(value, GrpcResponse) else value

                    method_handlers[method_name] = self._grpc.stream_stream_rpc_method_handler(
                        bidi_handler,
                        request_deserializer=lambda data: data,
                        response_serializer=_json_dumps,
                    )
                elif method_info.get("client_stream"):
                    async def client_stream_handler(request_iterator, context, _service=path_service, _method=method_name):
                        value = await self._invoke_stream(_service, _method, request_iterator, context)
                        return value.data if isinstance(value, GrpcResponse) else value

                    method_handlers[method_name] = self._grpc.stream_unary_rpc_method_handler(
                        client_stream_handler,
                        request_deserializer=lambda data: data,
                        response_serializer=_json_dumps,
                    )
                elif method_info.get("stream"):
                    async def stream_handler(request, context, _service=path_service, _method=method_name):
                        try:
                            payload = _json_loads(request)
                        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                            await context.abort(self._grpc.StatusCode.INVALID_ARGUMENT, str(exc))
                            return
                        value = await self._invoke(_service, _method, payload, context)
                        if hasattr(value, "__aiter__"):
                            async for item in value:
                                yield item.data if isinstance(item, GrpcResponse) else item
                        elif hasattr(value, "__iter__") and not isinstance(value, (str, bytes, dict)):
                            for item in value:
                                yield item.data if isinstance(item, GrpcResponse) else item
                        elif value is not None:
                            yield value

                    method_handlers[method_name] = self._grpc.unary_stream_rpc_method_handler(
                        stream_handler,
                        request_deserializer=lambda data: data,
                        response_serializer=_json_dumps,
                    )
                else:
                    async def unary_handler(request, context, _service=path_service, _method=method_name):
                        try:
                            payload = _json_loads(request)
                        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                            await context.abort(self._grpc.StatusCode.INVALID_ARGUMENT, str(exc))
                            return None
                        return await self._invoke(_service, _method, payload, context)

                    method_handlers[method_name] = self._grpc.unary_unary_rpc_method_handler(
                        unary_handler,
                        request_deserializer=lambda data: data,
                        response_serializer=_json_dumps,
                    )
            handlers[service_name] = self._grpc.method_handlers_generic_handler(service_name, method_handlers)
        return handlers

    async def start(self, address: str = None) -> None:
        if self._running:
            raise RuntimeError("gRPC server is already running")
        if address is not None:
            self._address = address
        if not self._address:
            self._address = "[::]:50051"
        try:
            import grpc
        except ImportError as exc:
            raise RuntimeError("gRPC support requires the 'grpcio' package") from exc

        self._grpc = grpc
        options = []
        max_message_size = getattr(self._config, "max_message_size", None)
        if max_message_size:
            options.extend([
                ("grpc.max_receive_message_length", int(max_message_size)),
                ("grpc.max_send_message_length", int(max_message_size)),
            ])
        keepalive_secs = getattr(self._config, "keepalive_secs", None)
        if keepalive_secs:
            options.append(("grpc.keepalive_time_ms", int(keepalive_secs) * 1000))
        concurrency_limit = getattr(self._config, "concurrency_limit", None)
        if concurrency_limit:
            options.append(("grpc.max_concurrent_streams", int(concurrency_limit)))
        self._server = grpc.aio.server(options=options)
        for handler in self._build_handlers().values():
            self._server.add_generic_rpc_handlers((handler,))

        # gRPC server reflection (service discovery, grpcurl --plaintext).
        if getattr(self._config, "reflection", False):
            try:
                from grpc_reflection.v1alpha import reflection

                reflection.enable_server_reflection(list(self._services.keys()), self._server)
            except ImportError:
                print(
                    "Warning: gRPC reflection requires the 'grpcio-reflection' package; "
                    "install it (pip install grpcio-reflection) to enable it."
                )

        requested_address = self._address
        bound_port = self._server.add_insecure_port(requested_address)
        if bound_port == 0:
            self._server = None
            raise OSError(f"Could not bind gRPC address '{self._address}'")
        await self._server.start()
        if requested_address.rsplit(":", 1)[-1] == "0":
            host = requested_address.rsplit(":", 1)[0]
            self._address = f"{host}:{bound_port}"
        self._running = True

    @property
    def address(self) -> Optional[str]:
        """The bound address, including the resolved port after startup."""
        return self._address

    async def stop(self, grace: float = 5.0) -> None:
        if self._server is not None:
            await self._server.stop(grace)
        self._server = None
        self._running = False

    async def wait_for_termination(self) -> None:
        if self._server is None:
            raise GrpcError(GrpcError.UNAVAILABLE, "gRPC server is not running")
        await self._server.wait_for_termination()

    async def dispatch(self, service: str, method: str, request: GrpcRequest) -> Any:
        """Dispatch directly for application code and unit tests."""
        if not self._running:
            raise GrpcError(GrpcError.UNAVAILABLE, "gRPC server is not running")
        target = self._services.get(service)
        if target is None:
            raise GrpcError(GrpcError.NOT_FOUND, f"Service '{service}' was not found")
        method_info = target._methods.get(method)
        if method_info is None:
            raise GrpcError(GrpcError.UNIMPLEMENTED, f"Method '{method}' was not found")
        value = method_info["handler"](request)
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, GrpcResponse) and value.status_code != GrpcError.OK:
            raise GrpcError(value.status_code, value.message)
        return value.data if isinstance(value, GrpcResponse) else value

    def __repr__(self) -> str:
        return f"GrpcServer(services={len(self._services)}, running={self._running})"


class GrpcChannel:
    """Real asyncio gRPC client channel for JSON generic RPC calls."""

    def __init__(self, target: str):
        self._target = target
        self._connected = False
        self._channel = None
        self._grpc = None

    @classmethod
    async def connect(cls, target: str) -> "GrpcChannel":
        if not isinstance(target, str) or not target.strip():
            raise ValueError("gRPC target must be a non-empty host:port string")
        try:
            import grpc
        except ImportError as exc:
            raise RuntimeError("gRPC support requires the 'grpcio' package") from exc
        instance = cls(target)
        instance._grpc = grpc
        instance._channel = grpc.aio.insecure_channel(target)
        try:
            await asyncio_wait_for_channel_ready(instance._channel)
        except Exception:
            await instance._channel.close()
            raise
        instance._connected = True
        return instance

    async def call(
        self,
        service: str,
        method: str,
        request: dict,
        metadata: dict = None,
        timeout: float = None,
    ) -> dict:
        if not self._connected or self._channel is None:
            raise GrpcError(GrpcError.UNAVAILABLE, "Channel is not connected")
        if not isinstance(request, dict):
            raise TypeError("gRPC request must be a dictionary")
        rpc = self._channel.unary_unary(
            f"/{service}/{method}",
            request_serializer=_json_dumps,
            response_deserializer=_json_loads,
        )
        try:
            return await rpc(request, metadata=tuple((metadata or {}).items()), timeout=timeout)
        except self._grpc.aio.AioRpcError as exc:
            raise GrpcError(_status_code(exc.code()), exc.details() or str(exc)) from exc

    async def stream(
        self,
        service: str,
        method: str,
        request: dict,
        metadata: dict = None,
        timeout: float = None,
    ):
        if not self._connected or self._channel is None:
            raise GrpcError(GrpcError.UNAVAILABLE, "Channel is not connected")
        if not isinstance(request, dict):
            raise TypeError("gRPC request must be a dictionary")
        rpc = self._channel.unary_stream(
            f"/{service}/{method}",
            request_serializer=_json_dumps,
            response_deserializer=_json_loads,
        )
        try:
            call = rpc(request, metadata=tuple((metadata or {}).items()), timeout=timeout)
            async for item in call:
                yield item
        except self._grpc.aio.AioRpcError as exc:
            raise GrpcError(_status_code(exc.code()), exc.details() or str(exc)) from exc

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
        self._channel = None
        self._connected = False

    def __repr__(self) -> str:
        return f"GrpcChannel(target={self._target!r}, connected={self._connected})"


async def asyncio_wait_for_channel_ready(channel, timeout: float = 5.0) -> None:
    """Wait for a channel to connect without relying on deprecated loop APIs."""
    await asyncio.wait_for(channel.channel_ready(), timeout=timeout)


# ============================================================================
# gRPC-Web bridge (browser clients over HTTP/1.1)
# ============================================================================

#: gRPC-Web trailing frame flag for trailers (0x80).
_GRPC_WEB_TRAILERS_FLAG = 0x80


def _frame_message(payload: bytes) -> bytes:
    """Wrap a payload in a gRPC-Web data frame (1 flag byte + 4-byte length)."""
    return b"\x00" + len(payload).to_bytes(4, "big") + payload


def _unframe_messages(data: bytes) -> list[bytes]:
    """Split a gRPC-Web frame stream into its payloads.

    Each frame is 1 flag byte + 4-byte big-endian length + payload. Trailer
    frames (flag 0x80) are returned as-is and are typically the last frame.
    """
    messages: list[bytes] = []
    offset = 0
    while offset < len(data):
        if offset + 5 > len(data):
            break
        flag = data[offset]
        length = int.from_bytes(data[offset + 1 : offset + 5], "big")
        offset += 5
        if offset + length > len(data):
            break
        messages.append(data[offset : offset + length])
        offset += length
        if flag & _GRPC_WEB_TRAILERS_FLAG:
            break
    return messages


def _grpc_web_status(payload: bytes) -> int:
    """Parse a gRPC-Web trailer frame and return the ``grpc-status`` value."""
    try:
        trailers = payload.decode("utf-8")
    except UnicodeDecodeError:
        return GrpcError.INTERNAL
    status = GrpcError.OK
    for line in trailers.split("\r\n"):
        if line.startswith("grpc-status:") and line[12:].strip().isdigit():
            status = int(line[12:].strip())
    return status


def grpc_web_request_handler(server: "GrpcServer"):
    """Return an async HTTP handler that bridges gRPC-Web frames to a service.

    The handler is bound to one ``/service/method`` path and speaks the
    gRPC-Web framing (5-byte prefix, JSON payloads for this generic API) over
    plain HTTP/1.1 so browser clients (``@grpc/grpc-js``, ``grpc-web``) can
    call the server without HTTP/2.
    """

    async def handler(request):
        content_type = request.get_header("content-type") or ""
        data = request.body() or b""
        if "text" in content_type:
            import base64

            try:
                data = base64.b64decode(data)
            except (ValueError, TypeError) as exc:
                return _grpc_web_error(GrpcError.INVALID_ARGUMENT, str(exc))
        payloads = _unframe_messages(data)
        if not payloads:
            return _grpc_web_error(GrpcError.INVALID_ARGUMENT, "empty gRPC-Web request")

        service, method = _grpc_web_service_method(request.path)
        try:
            payload = json.loads(payloads[-1].decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("gRPC-Web JSON request must be an object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return _grpc_web_error(GrpcError.INVALID_ARGUMENT, str(exc))

        try:
            value = await server.dispatch(service, method, GrpcRequest(service, method, payload))
        except GrpcError as exc:
            return _grpc_web_error(exc.code, exc.message)

        body = _json_dumps(value.data if isinstance(value, GrpcResponse) else value)
        return _grpc_web_ok(body)

    return handler


def _grpc_web_service_method(path: str) -> tuple[str, str]:
    """Split ``/prefix/package.Service/Method`` into (service, method).

    Uses the last two path segments so any configured gRPC-Web prefix works.
    """
    parts = path.strip("/").split("/")
    if len(parts) < 2:
        return path.strip("/"), ""
    return parts[-2], parts[-1]


def _grpc_web_ok(body: bytes) -> "Any":
    """Build a successful gRPC-Web response with a data frame."""
    from cello import Response

    framed = _frame_message(body)
    response = Response.binary(framed, "application/grpc-web+json", 200)
    response.set_header("grpc-status", "0")
    response.set_header("grpc-message", "OK")
    return response


def _grpc_web_error(code: int, message: str) -> "Any":
    """Build a gRPC-Web error response carrying a trailer frame."""
    from cello import Response

    trailers = f"grpc-status: {code}\r\ngrpc-message: {message}\r\n".encode("utf-8")
    framed = bytes([_GRPC_WEB_TRAILERS_FLAG]) + len(trailers).to_bytes(4, "big") + trailers
    response = Response.binary(framed, "application/grpc-web+json", 200)
    response.set_header("grpc-status", str(code))
    response.set_header("grpc-message", message)
    return response


# ============================================================================
# Protobuf codec (wire-compatible messages)
# ============================================================================


class ProtobufCodec:
    """Encode/decode protobuf messages for the generic gRPC transport.

    Pass a ``google.protobuf`` message class (generated or from
    ``descriptor_pb2``) to serialize dict payloads to the protobuf wire format
    and back, so the JSON generic API can talk to protobuf-generated stubs.

    Example:
        from echo_pb2 import EchoRequest, EchoResponse

        codec = ProtobufCodec(EchoRequest, EchoResponse)
        wire = codec.encode({"message": "hello"})
        decoded = codec.decode(wire)
    """

    def __init__(self, request_type=None, response_type=None):
        self._request_type = request_type
        self._response_type = response_type

    def encode(self, value: Any, message_type=None) -> bytes:
        """Serialize a dict (or GrpcResponse payload) to protobuf bytes."""
        if isinstance(value, GrpcResponse):
            value = value.data
        message_type = message_type or self._request_type
        if message_type is None:
            return _json_dumps(value)
        try:
            from google.protobuf.json_format import ParseDict
        except ImportError as exc:
            raise RuntimeError("ProtobufCodec requires the 'protobuf' package") from exc
        message = message_type()
        ParseDict(value if value is not None else {}, message)
        return message.SerializeToString()

    def decode(self, data: bytes, message_type=None) -> Any:
        """Parse protobuf bytes into a dict."""
        message_type = message_type or self._response_type
        if message_type is None:
            return _json_loads(data)
        try:
            from google.protobuf.json_format import MessageToDict
        except ImportError as exc:
            raise RuntimeError("ProtobufCodec requires the 'protobuf' package") from exc
        message = message_type()
        message.ParseFromString(data)
        return MessageToDict(message)


__all__ = [
    "GrpcError",
    "GrpcRequest",
    "GrpcResponse",
    "GrpcService",
    "GrpcServer",
    "GrpcChannel",
    "grpc_method",
    "ProtobufCodec",
    "grpc_web_request_handler",
    "_frame_message",
    "_unframe_messages",
]


def _status_code(status) -> int:
    name = getattr(status, "name", "UNKNOWN")
    return {
        "CANCELLED": GrpcError.CANCELLED,
        "UNKNOWN": GrpcError.UNKNOWN,
        "INVALID_ARGUMENT": GrpcError.INVALID_ARGUMENT,
        "DEADLINE_EXCEEDED": GrpcError.DEADLINE_EXCEEDED,
        "NOT_FOUND": GrpcError.NOT_FOUND,
        "ALREADY_EXISTS": GrpcError.ALREADY_EXISTS,
        "PERMISSION_DENIED": GrpcError.PERMISSION_DENIED,
        "RESOURCE_EXHAUSTED": GrpcError.RESOURCE_EXHAUSTED,
        "FAILED_PRECONDITION": GrpcError.FAILED_PRECONDITION,
        "ABORTED": GrpcError.ABORTED,
        "OUT_OF_RANGE": GrpcError.OUT_OF_RANGE,
        "UNIMPLEMENTED": GrpcError.UNIMPLEMENTED,
        "INTERNAL": GrpcError.INTERNAL,
        "UNAVAILABLE": GrpcError.UNAVAILABLE,
        "DATA_LOSS": GrpcError.DATA_LOSS,
        "UNAUTHENTICATED": GrpcError.UNAUTHENTICATED,
    }.get(name, GrpcError.UNKNOWN)
