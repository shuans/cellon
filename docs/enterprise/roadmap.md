---
title: Enterprise Roadmap
description: Upcoming enterprise features for Cello Framework
---

# Enterprise Roadmap

This roadmap outlines planned enterprise features for Cello Framework, based on research of industry best practices for building production-grade web services.

## Timeline Overview

```mermaid
gantt
    title Cello Enterprise Roadmap
    dateFormat  YYYY-Q
    section Observability
    OpenTelemetry & Health Checks    :2026-Q1, 90d
    section Data Layer
    Database & Redis Integration     :2026-Q2, 90d
    section Protocols
    GraphQL & gRPC Support           :2026-Q1, 90d
    section Patterns
    Event Sourcing & CQRS            :2026-Q4, 90d
    section Production
    v1.0 Production Ready            :2026-Q1, 30d
```

---

## v0.7.0 - Observability & Health (Q1 2026) :material-check-circle:{ .green }

### OpenTelemetry Integration

Full observability with the three pillars: traces, metrics, and logs.

```python
from cello.telemetry import OpenTelemetryConfig

app.configure_telemetry(OpenTelemetryConfig(
    service_name="my-service",
    otlp_endpoint="http://collector:4317",
    sampling_rate=0.1,
    export_metrics=True,
    export_traces=True,
    export_logs=True
))
```

**Features:**
- Distributed tracing with context propagation
- Automatic instrumentation for HTTP, database, external calls
- Metrics export via OTLP
- Log correlation with trace IDs
- Baggage propagation

### Health Check Endpoints

Kubernetes-compatible probes.

```python
from cello.health import HealthCheck

@app.health_check("database")
async def check_database():
    await db.ping()
    return HealthStatus.UP

# Auto-exposed:
# GET /health/live
# GET /health/ready
# GET /health
```

**Features:**
- Liveness, readiness, startup probes
- Dependency health checks
- Custom health indicators
- Health aggregation

---

## v0.8.0 - Data Layer (Q1 2026) :material-check-circle:{ .green }

### Database Connection Pooling

High-performance async database connections.

```python
from cello.database import DatabaseConfig

db = await Database.connect(DatabaseConfig(
    url="postgresql://localhost/mydb",
    pool_size=20,
    max_lifetime=1800
))
```

**Supported Databases:**
- PostgreSQL
- MySQL
- SQLite
- MongoDB (planned)

### Redis Integration

Async Redis client with clustering support.

```python
from cello.cache import Redis

redis = await Redis.connect("redis://localhost:6379")
await redis.set("key", "value", ttl=300)
```

**Features:**
- Connection pooling
- Pub/Sub support
- Cluster mode
- Sentinel support

---

## v0.9.0 - API Protocols (Q1 2026) :material-check-circle:{ .green }

### GraphQL Support (partial)

The Python engine supports decorator-based resolvers, schema building, HTTP GET/POST query and mutation execution, variables, field projection, limited introspection, and DataLoader. Subscription execution is available as an engine primitive; WebSocket transport, federation, and full GraphQL validation remain pending.

```python
from cello import App
from cello.graphql import Query, Schema

@Query
def users(info) -> list:
    return db.get_users()

app = App()
app.mount_graphql(Schema().query(users).build())
```

### gRPC Support (partial)

The Python convenience API uses a real `grpc.aio` HTTP/2 transport with JSON serializers, unary calls, and server-streaming calls. It is not wire-compatible with protobuf-generated stubs; reflection, gRPC-Web, bidirectional streaming, and interceptors remain pending.

```python
from cello import App, GrpcConfig
from cello.grpc import GrpcService, grpc_method, GrpcResponse

class UserService(GrpcService):
    @grpc_method
    async def GetUser(self, request):
        return GrpcResponse.ok({"id": request.get("id"), "name": "Alice"})

app = App()
app.enable_grpc(GrpcConfig(address="[::]:50051"))
app.add_grpc_service(UserService())
```

### Message Queue Adapters (partial)

Redis Streams and RabbitMQ AMQP have real asynchronous producer/consumer clients with acknowledgements. Kafka and SQS remain compatibility configuration/decorator APIs without external broker clients.

```python
from cello import RedisConfig
from cello.messaging import Producer

producer = await Producer.connect(RedisConfig(url="redis://localhost:6379"))
await producer.send("orders", {"type": "OrderCreated", "order_id": 1})
await producer.close()
```

---

## v0.10.0 - Advanced Patterns (Q1 2026) :material-progress-clock:{ .orange }

The Python Event Sourcing runtime supports DuckDB and in-memory stores. CQRS and Saga integration remain configuration-only and remain in progress.

### Event Sourcing

Event-driven persistence with `Event`, `Aggregate`, ordered replay, and persisted snapshots. The current persistent backends are DuckDB and in-memory storage.

```python
from cello import App, EventSourcingConfig
from cello.eventsourcing import Aggregate, Event, event_handler

app = App()
app.enable_event_sourcing(EventSourcingConfig.duckdb("./data/events.duckdb"))

class Order(Aggregate):
    @event_handler("OrderCreated")
    def on_created(self, event):
        self.state["status"] = "created"
        self.state.update(event.data)

order = Order(aggregate_id="order-1")
order.apply(Event("OrderCreated", {"customer_id": "customer-1"}))
# Persist order.uncommitted_events through app.state.event_store at startup.
```

**Features:**
- Aggregate orchestration remains application-level
- Event replay to rebuild state
- Snapshot support for performance
- DuckDB and in-memory backends
- Optimistic aggregate version checks
- Event versioning and ordered replay
- PostgreSQL/MySQL persistence, federation, and upcasting remain planned

### CQRS (planned)

`App.enable_cqrs()` currently records configuration only. A complete command/query bus, handler registration, and event synchronization runtime is not part of the implemented public API yet.

### Saga Pattern (planned)

`App.enable_saga()` currently records configuration only. Persistent orchestration, compensation, retries, and crash recovery remain planned.

---

## v1.0.1 - Production Ready (Q1 2026) :material-check-circle:{ .green }

### Stable Release

First production-ready release with semantic versioning guarantees and major performance optimizations.

- API stability commitment (no breaking changes until v2.0)
- Handler metadata caching (async detection, DI params)
- Lazy body parsing, zero-copy responses, TCP_NODELAY
- Optimized release build configuration
- All 394 tests passing

---

## v1.1.0+ - Future Enhancements (Planned)

### OAuth2/OIDC Provider

Full OAuth2 server implementation.

```python
from cello.oauth2 import OAuth2Provider

oauth = OAuth2Provider(config)
app.mount("/oauth", oauth)
```

### Service Mesh Integration

Istio/Envoy support.

```python
from cello.mesh import ServiceMesh

mesh = ServiceMesh(DiscoveryConfig(
    registry="consul://localhost:8500"
))
```

### Admin Dashboard

Real-time monitoring UI.

```python
app.enable_admin(
    path="/admin",
    features=["metrics", "routes", "health"]
)
```

### Multi-tenancy

Tenant isolation and data partitioning.

```python
from cello.multitenancy import tenant_context

@app.middleware
async def tenant_middleware(request, call_next):
    with tenant_context(request.headers["X-Tenant-ID"]):
        return await call_next(request)
```

---

## Feature Requests

Have a feature request? Submit it on [GitHub Issues](https://github.com/jagadeesh32/cello/issues/new?template=feature_request.md).

We prioritize features based on:
- Community demand
- Enterprise use cases
- Technical feasibility
- Alignment with project goals

---

## Contributing

Help us build these features! See our [Contributing Guide](../community/contributing.md).

Priority areas:
- OAuth2/OIDC Provider
- Service Mesh Integration
- Admin Dashboard
- Multi-tenancy

---

## Sources & Research

This roadmap is informed by widely adopted industry best practices for
observability, security, and distributed-systems design.
