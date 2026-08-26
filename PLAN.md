# Cellon Feature Alignment Plan

## Baseline Review

The initial review found that the documentation overstated several enterprise features compared with the implementation:

- HTTP/3 has configuration objects but `App.run()` does not start a QUIC/UDP listener.
- gRPC was initially an in-process registry/mock path, without protobuf encoding, a network listener, remote channels, streaming, reflection, or gRPC-Web.
- Kafka, RabbitMQ, and SQS configuration methods initially only recorded configuration; the Python messaging layer used an in-memory broker rather than external brokers.
- GraphQL initially had separate simplified Python and Rust paths that were not connected to the application's `/graphql` endpoint. Standard schema validation, federation, full introspection, and subscription transport were incomplete.
- Event Sourcing's Python `EventStore` was initially dictionary-backed. PostgreSQL configuration stored a URL but did not create a PostgreSQL store; automatic snapshots were not persisted.
- CQRS and Saga configuration is recorded by the Rust app but does not configure the Python buses/orchestrator.
- Some documentation examples use APIs or signatures that do not exist in the current source.
- Python 3.12 is the supported runtime (`requires-python >=3.12`, `abi3-py312`); a system default Python 3.8 must not be used to create or run the project environment.

## Implementation Order

1. Implement real Redis Streams and RabbitMQ AMQP asynchronous messaging adapters while preserving the existing producer/consumer API and making optional dependencies explicit. (Implemented in `cello.messaging`; Kafka/SQS remain compatibility adapters.)
2. Implement a usable network gRPC server/client path with request dispatch, JSON payload handling, metadata, server streaming, and lifecycle management. (Implemented in `cello.grpc`; protobuf-generated wire compatibility, reflection, and gRPC-Web remain pending.)
3. Connect GraphQL schema execution to an HTTP endpoint exposed by `App`, including query variables, field selection, errors, introspection, and the documented registration API. (Implemented in the Python HTTP route path; WebSocket subscription transport and full GraphQL validation remain pending.)
4. Implement DuckDB-backed Event Sourcing: (Implemented in the Python runtime.)
   - [x] append-only event persistence;
   - [x] aggregate/version optimistic concurrency checks;
   - [x] event replay and incremental reads;
   - [x] persisted latest snapshots;
   - [x] configurable event retention limits;
   - [x] `EventSourcingConfig.duckdb(path)` and application configuration access.
5. Reconcile documentation and public API examples with the implemented behavior. (Redis/RabbitMQ, gRPC, GraphQL, and DuckDB Event Sourcing docs updated; remaining protocol limitations are called out explicitly.)

## DuckDB Event Sourcing Acceptance Criteria

- [x] `EventSourcingConfig.duckdb(path)` produces a `duckdb` configuration with a usable connection URL.
- [x] `await EventStore.connect(config)` opens or creates the DuckDB database and its event/snapshot tables.
- [x] Events survive closing one store and opening another against the same path.
- [x] Appends are atomic and reject stale `expected_version` values without partially writing a batch.
- [x] Event versions are sequential per aggregate and `get_events(..., since_version=...)` is ordered.
- [x] Snapshots survive reconnects and only the newest snapshot is returned.
- [x] In-memory storage remains available for tests and development.

## Current Status

The requested implementation pass is complete for Redis Streams, RabbitMQ AMQP,
GraphQL HTTP query/mutation mounting, and the JSON generic `grpc.aio` transport.
The remaining limitations are intentional and documented: Kafka/SQS external
clients, protobuf-generated gRPC wire compatibility, gRPC reflection/gRPC-Web/
bidirectional streaming, GraphQL WebSocket subscriptions/federation/full
validation, and HTTP/3 QUIC serving.

## Verification Policy

This plan is being implemented without building, testing, installing dependencies, or changing the system's default Python interpreter. Static diff and source inspection are the only verification performed in this pass. Runtime verification still requires Python 3.12 plus the relevant optional dependencies and external services: `grpcio` for gRPC, `redis`/`aio-pika` for Redis and RabbitMQ, and `duckdb` for persistent Event Sourcing. The `full` extra includes these dependencies.
