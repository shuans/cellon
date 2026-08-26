---
title: gRPC Integration
description: gRPC support in Cello Framework - JSON generic services, methods, and server streaming
---

# gRPC Integration

Cello provides a real asyncio gRPC HTTP/2 transport with class-based service definitions and automatic method discovery. The convenience API serializes request and response payloads as JSON; it is not wire-compatible with protobuf-generated stubs and does not currently expose reflection or gRPC-Web.

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
| `reflection` | `True` | Configuration is retained for compatibility; reflection service is not exposed |
| `enable_web` | `False` | Configuration is retained for compatibility; gRPC-Web is not exposed |
| `keepalive_secs` | `60` | Keepalive interval |
| `concurrency_limit` | `100` | Max concurrent streams |

## API Reference

| Class | Description |
|-------|-------------|
| `GrpcService` | Base class for gRPC service definitions |
| `grpc_method` | Decorator to mark methods as gRPC endpoints |
| `GrpcRequest` | Request wrapper with service, method, data, metadata |
| `GrpcResponse` | Response wrapper with data, status_code, message |
| `GrpcServer` | Server for hosting gRPC services |
| `GrpcChannel` | Client for calling gRPC services |
| `GrpcError` | Exception class with standard gRPC status codes |
| `GrpcConfig` | Rust-backed bind and transport configuration class |
