---
title: gRPC Integration
description: gRPC support in Cellon - JSON generic services, methods, and server streaming
---

# gRPC Integration

Cello provides a real asyncio gRPC HTTP/2 transport with class-based service definitions and automatic method discovery. The convenience API serializes request and response payloads as JSON, supports unary, server-streaming, client-streaming, and bidirectional streaming calls, and exposes gRPC server reflection plus a gRPC-Web bridge for browser clients. A `ProtobufCodec` makes the transport wire-compatible with protobuf messages when you provide generated message classes.

## Quick Start

```python
from cello import App, GrpcConfig
from cello.grpc import GrpcService, grpc_method, GrpcServer, GrpcRequest, GrpcResponse

app = App()
app.enable_grpc(GrpcConfig(address="[::]:50051"))

class UserService(GrpcService):
    @grpc_method
    async def GetUser(self, request):
        return GrpcResponse.ok({"id": 1, "name": "Alice"})

    @grpc_method(stream=True)
    async def ListUsers(self, request):
        for user in [{"id": 1}, {"id": 2}]:
            yield user

app.add_grpc_service(UserService())
app.run()
```

## GrpcService

Base class for defining gRPC services. Methods decorated with `@grpc_method` are automatically discovered.

```python
from cello.grpc import GrpcService, grpc_method

class OrderService(GrpcService):
    name = "OrderService"  # Optional, defaults to class name

    @grpc_method
    async def CreateOrder(self, request):
        return GrpcResponse.ok({"order_id": "123"})

    @grpc_method
    async def GetOrder(self, request):
        order_id = request.data.get("id")
        return GrpcResponse.ok({"id": order_id, "status": "pending"})

# Discover methods
service = OrderService()
print(service.get_methods())  # [{"name": "CreateOrder", ...}, {"name": "GetOrder", ...}]
```

## @grpc_method Decorator

Marks a method as a gRPC endpoint. Supports both unary and streaming modes.

```python
# Unary RPC
@grpc_method
async def GetUser(self, request):
    return GrpcResponse.ok({"id": 1})

# Streaming RPC
@grpc_method(stream=True)
async def ListUsers(self, request):
    for user in users:
        yield user
```

## GrpcRequest and GrpcResponse

### GrpcRequest

```python
request = GrpcRequest(
    service="UserService",
    method="GetUser",
    data={"id": 1},
    metadata={"auth": "token123"}
)

print(request.service)   # "UserService"
print(request.method)    # "GetUser"
print(request.data)      # {"id": 1}
print(request.metadata)  # {"auth": "token123"}
```

### GrpcResponse

```python
# Success response
response = GrpcResponse.ok({"id": 1, "name": "Alice"})

# Error response
response = GrpcResponse.error(code=5, message="User not found")

# Custom response
response = GrpcResponse(
    data={"id": 1},
    status_code=0,
    message="OK",
    metadata={"request-id": "abc123"}
)
```

## GrpcServer

Hosts gRPC services and manages their lifecycle.

```python
from cello.grpc import GrpcServer

server = GrpcServer(host="localhost", port=50051)
server.register_service(UserService())
server.register_service(OrderService())

# Start/stop for standalone use
await server.start()
print(server.get_services())  # ["UserService", "OrderService"]
await server.stop()
```

## GrpcChannel (Client)

Connect to gRPC services as a client.

```python
from cello.grpc import GrpcChannel, GrpcRequest

channel = await GrpcChannel.connect("localhost:50051")

response = await channel.call(
    service="UserService",
    method="GetUser",
    request={"id": 1},
    metadata={"authorization": "Bearer token123"},
)

print(response)  # {"id": 1, "name": "Alice"}
await channel.close()
```

## GrpcError

Standard gRPC status codes for error handling.

```python
from cello.grpc import GrpcError

# Status code constants
GrpcError.OK              # 0
GrpcError.CANCELLED       # 1
GrpcError.UNKNOWN         # 2
GrpcError.INVALID_ARGUMENT # 3
GrpcError.NOT_FOUND       # 5
GrpcError.PERMISSION_DENIED # 7
GrpcError.INTERNAL        # 13
GrpcError.UNAVAILABLE     # 14
GrpcError.UNAUTHENTICATED # 16

# Raise errors
raise GrpcError(code=GrpcError.NOT_FOUND, message="User not found")
```

## Configuration

```python
from cello import App, GrpcConfig

app = App()
app.enable_grpc(GrpcConfig(
    address="[::]:50051",
    max_message_size=4_194_304,   # 4MB
    keepalive_secs=60,
    concurrency_limit=100,
))
```

| Option | Default | Description |
|--------|---------|-------------|
| `address` | `[::]:50051` | gRPC server bind address |
| `max_message_size` | `4MB` | Max message size in bytes |
| `reflection` | `False` | Enables the standard gRPC server reflection service (`grpcio-reflection` package required) |
| `enable_web` | `False` | Config flag; use `app.enable_grpc_web()` to expose the gRPC-Web bridge |
| `keepalive_secs` | `60` | Keepalive interval |
| `concurrency_limit` | `100` | Max concurrent streams |

---

## Streaming Calls

`grpc_method` accepts four cardinalities:

```python
@grpc_method                      # unary
@grpc_method(stream=True)         # server streaming
@grpc_method(client_stream=True)  # client streaming
@grpc_method(bidi=True)           # bidirectional streaming
```

Streaming methods receive an async iterator of `GrpcRequest` objects:

```python
class ChatService(GrpcService):
    @grpc_method(bidi=True)
    async def RouteChat(self, requests):
        async for request in requests:
            yield GrpcResponse.ok({"echo": request.data.get("text", "")})
```

---

## Reflection

Enable the standard gRPC reflection service (used by `grpcurl`, `grpc-ui`, etc.):

```python
app.enable_grpc(GrpcConfig(address="[::]:50051", reflection=True))
```

Requires the `grpcio-reflection` package (`pip install grpcio-reflection`); it is
included in the `grpc` extra.

---

## gRPC-Web (browser clients)

Browsers cannot speak raw HTTP/2 gRPC. `enable_grpc_web()` exposes the same
services over HTTP/1.1 with gRPC-Web framing (5-byte prefix, JSON payloads for
the generic API):

```python
app.enable_grpc(GrpcConfig(address="[::]:50051", reflection=True))
app.add_grpc_service(UserService())
app.enable_grpc_web("/grpc.web")
```

Clients POST `application/grpc-web+json` frames to
`/grpc.web/{service}/{method}` and receive a framed JSON response with a
`grpc-status` trailer.

---

## Protobuf Codec

To talk to protobuf-generated stubs on the wire, provide message classes to
`ProtobufCodec` (requires the `protobuf` package):

```python
from echo_pb2 import EchoRequest, EchoResponse
from cello.grpc import ProtobufCodec

codec = ProtobufCodec(EchoRequest, EchoResponse)
wire = codec.encode({"message": "hello"})
back = codec.decode(wire)
```

## API Reference

| Class | Description |
|-------|-------------|
| `GrpcService` | Base class for gRPC service definitions |
| `grpc_method` | Decorator to mark methods as gRPC endpoints (unary/stream/client_stream/bidi) |
| `GrpcRequest` | Request wrapper with service, method, data, metadata |
| `GrpcResponse` | Response wrapper with data, status_code, message |
| `GrpcServer` | Server for hosting gRPC services |
| `GrpcChannel` | Client for calling gRPC services |
| `GrpcError` | Exception class with standard gRPC status codes |
| `GrpcConfig` | Rust-backed bind and transport configuration class |
| `ProtobufCodec` | Encode/decode protobuf messages for wire compatibility |
