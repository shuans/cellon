"""
Cello Event Sourcing Module.

Provides Python-friendly wrappers for event sourcing patterns including
events, aggregates, snapshots, and persistent DuckDB or in-memory event stores.
Designed for use with the Cello framework's Rust-powered runtime.

Example:
    from cello import App
    from cello.eventsourcing import (
        Event, Aggregate, EventStore, Snapshot,
        EventSourcingConfig, event_handler,
    )

    # Define an aggregate with event handlers
    class OrderAggregate(Aggregate):

        @event_handler("OrderCreated")
        def on_order_created(self, event):
            self.state["status"] = "created"
            self.state["items"] = event.data.get("items", [])
            self.state["total"] = event.data.get("total", 0)

        @event_handler("OrderShipped")
        def on_order_shipped(self, event):
            self.state["status"] = "shipped"
            self.state["shipped_at"] = event.data.get("shipped_at")

    # Usage in application
    app = App()
    config = EventSourcingConfig.duckdb("./data/events.duckdb")

    @app.on_event("startup")
    async def setup():
        app.state.event_store = await EventStore.connect(config)

    @app.post("/orders")
    async def create_order(request):
        data = request.json()
        order = OrderAggregate()

        event = Event(
            event_type="OrderCreated",
            data={"items": data["items"], "total": data["total"]},
            aggregate_id=order.id,
        )
        order.apply(event)

        await app.state.event_store.append(order.id, order.uncommitted_events)
        order.clear_uncommitted()

        return {"order_id": order.id, "status": order.state["status"]}

    @app.get("/orders/{id}")
    async def get_order(request):
        order_id = request.params["id"]
        events = await app.state.event_store.get_events(order_id)
        order = OrderAggregate(aggregate_id=order_id)
        order.load_from_events(events)
        return {"order_id": order.id, "state": order.state}

    @app.on_event("shutdown")
    async def teardown():
        await app.state.event_store.close()
"""

import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from .database import AsyncFileDatabase


def event_handler(event_type: str) -> Callable:
    """
    Decorator to mark a method as a handler for a specific event type.

    When an event with the matching type is applied to an aggregate,
    the decorated method is automatically called to update state.

    Args:
        event_type: The event type string this handler processes.

    Returns:
        Decorator function for the event handler method.

    Example:
        class OrderAggregate(Aggregate):

            @event_handler("OrderCreated")
            def on_order_created(self, event):
                self.state["status"] = "created"
                self.state["items"] = event.data.get("items", [])

            @event_handler("OrderCancelled")
            def on_order_cancelled(self, event):
                self.state["status"] = "cancelled"
                self.state["cancelled_reason"] = event.data.get("reason")
    """
    def decorator(func: Callable) -> Callable:
        func._cello_event_handler = True
        func._cello_event_type = event_type
        return func
    return decorator


class Event:
    """
    Represents a domain event in the event sourcing system.

    Events are immutable records of something that happened in the domain.
    Each event has a unique ID, a type, associated data, and belongs to
    an aggregate identified by aggregate_id.

    Attributes:
        id: Unique event identifier (auto-generated UUID).
        event_type: String identifying the type of event.
        aggregate_id: ID of the aggregate this event belongs to.
        data: Dictionary of event payload data.
        metadata: Optional dictionary of additional metadata.
        version: Event version number (starts at 0).
        timestamp: Unix timestamp when the event was created.

    Example:
        event = Event(
            event_type="OrderCreated",
            data={"items": ["item1", "item2"], "total": 99.99},
            aggregate_id="order-123",
            metadata={"user_id": "user-456"},
        )
        print(event.json())
    """

    def __init__(
        self,
        event_type: str,
        data: Dict[str, Any],
        aggregate_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize a new Event.

        Args:
            event_type: String identifying the type of event.
            data: Dictionary of event payload data.
            aggregate_id: ID of the aggregate this event belongs to.
            metadata: Optional dictionary of additional metadata.
        """
        self.id: str = str(uuid.uuid4())
        self.event_type: str = event_type
        self.aggregate_id: Optional[str] = aggregate_id
        self.data: Dict[str, Any] = data
        self.metadata: Dict[str, Any] = metadata or {}
        self.version: int = 0
        self.timestamp: float = time.time()

    def json(self) -> Dict[str, Any]:
        """
        Serialize the event to a dictionary.

        Returns:
            Dictionary representation of the event suitable for JSON
            serialization or storage.

        Example:
            event = Event("UserCreated", {"name": "Alice"})
            payload = event.json()
            # {"id": "...", "event_type": "UserCreated", ...}
        """
        return {
            "id": self.id,
            "event_type": self.event_type,
            "aggregate_id": self.aggregate_id,
            "data": self.data,
            "metadata": self.metadata,
            "version": self.version,
            "timestamp": self.timestamp,
        }

    def __repr__(self) -> str:
        return (
            f"Event(id={self.id!r}, event_type={self.event_type!r}, "
            f"aggregate_id={self.aggregate_id!r}, version={self.version})"
        )


class Aggregate:
    """
    Base class for event-sourced aggregates.

    An aggregate is the fundamental building block of event sourcing.
    It maintains state by applying events and tracks uncommitted events
    that need to be persisted.

    Subclasses should define event handler methods decorated with
    @event_handler to process specific event types. Alternatively,
    methods named ``_handle_<event_type>`` are discovered automatically.

    Attributes:
        id: Unique aggregate identifier (auto-generated UUID).
        version: Current version of the aggregate.
        state: Dictionary holding the aggregate's current state.
        uncommitted_events: List of events not yet persisted.

    Example:
        class AccountAggregate(Aggregate):

            @event_handler("AccountOpened")
            def on_account_opened(self, event):
                self.state["balance"] = event.data["initial_balance"]
                self.state["owner"] = event.data["owner"]

            @event_handler("MoneyDeposited")
            def on_money_deposited(self, event):
                self.state["balance"] += event.data["amount"]

            @event_handler("MoneyWithdrawn")
            def on_money_withdrawn(self, event):
                self.state["balance"] -= event.data["amount"]

        account = AccountAggregate()
        event = Event("AccountOpened", {"initial_balance": 1000, "owner": "Alice"})
        account.apply(event)
        print(account.state)  # {"balance": 1000, "owner": "Alice"}
    """

    def __init__(self, aggregate_id: Optional[str] = None):
        """
        Initialize a new Aggregate.

        Args:
            aggregate_id: Optional aggregate ID. If None, a UUID is generated.
        """
        self.id: str = aggregate_id or str(uuid.uuid4())
        self.version: int = 0
        self.state: Dict[str, Any] = {}
        self.uncommitted_events: List[Event] = []

        # Build event handler registry from decorated methods
        self._event_handlers: Dict[str, Callable] = {}
        for attr_name in dir(self):
            try:
                attr = getattr(self, attr_name)
            except AttributeError:
                continue
            if callable(attr) and getattr(attr, "_cello_event_handler", False):
                event_type = getattr(attr, "_cello_event_type", None)
                if event_type:
                    self._event_handlers[event_type] = attr

    def apply(self, event: Event) -> None:
        """
        Apply an event to this aggregate.

        Looks for a handler in the following order:
        1. A method decorated with @event_handler for the event type.
        2. A method named ``_handle_<event_type>`` on the aggregate.

        If a handler is found, it is called with the event. The event
        is then appended to the uncommitted events list, and the
        aggregate version is incremented.

        Args:
            event: The event to apply.

        Example:
            order = OrderAggregate()
            event = Event("OrderCreated", {"total": 42.00})
            order.apply(event)
            assert len(order.uncommitted_events) == 1
        """
        # Set event version and aggregate_id
        event.version = self.version + 1
        if event.aggregate_id is None:
            event.aggregate_id = self.id

        # Look for decorated handler first
        handler = self._event_handlers.get(event.event_type)

        # Fall back to _handle_<event_type> convention
        if handler is None:
            handler_name = f"_handle_{event.event_type}"
            handler = getattr(self, handler_name, None)

        if handler is not None:
            handler(event)

        self.uncommitted_events.append(event)
        self.version = event.version

    def load_from_events(self, events: List[Event]) -> None:
        """
        Rebuild aggregate state by replaying a list of events.

        This method replays each event in order without adding them
        to the uncommitted events list. It is used to reconstitute
        an aggregate from the event store.

        Validates that event versions are sequential (each event's version
        must be exactly one greater than the previous). Raises ValueError
        if the event stream is corrupted or out of order.

        Args:
            events: Ordered list of events to replay.

        Raises:
            ValueError: If event versions are not sequential.

        Example:
            events = await event_store.get_events("order-123")
            order = OrderAggregate(aggregate_id="order-123")
            order.load_from_events(events)
            print(order.state)
        """
        self.state = {}
        self.version = 0

        for event in events:
            expected_version = self.version + 1
            if event.version != expected_version:
                raise ValueError(
                    f"Event version out of order: expected {expected_version}, "
                    f"got {event.version} (event_type={event.event_type!r}, "
                    f"aggregate_id={self.id!r})"
                )

            # Look for decorated handler first
            handler = self._event_handlers.get(event.event_type)

            # Fall back to _handle_<event_type> convention
            if handler is None:
                handler_name = f"_handle_{event.event_type}"
                handler = getattr(self, handler_name, None)

            if handler is not None:
                handler(event)

            self.version = event.version

    def clear_uncommitted(self) -> None:
        """
        Clear the list of uncommitted events.

        Call this after successfully persisting events to the event store.

        Example:
            await event_store.append(aggregate.id, aggregate.uncommitted_events)
            aggregate.clear_uncommitted()
        """
        self.uncommitted_events = []

    def get_version(self) -> int:
        """
        Get the current version of the aggregate.

        Returns:
            The current version number.
        """
        return self.version

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(id={self.id!r}, "
            f"version={self.version}, state_keys={list(self.state.keys())})"
        )


class Snapshot:
    """
    Represents a snapshot of aggregate state at a specific version.

    Snapshots are used to optimize event replay by capturing the full
    state at a point in time. Instead of replaying all events from the
    beginning, the aggregate can be restored from a snapshot and only
    replay events that occurred after the snapshot version.

    Attributes:
        aggregate_id: ID of the aggregate this snapshot belongs to.
        version: Aggregate version at the time of the snapshot.
        state: Dictionary of the aggregate state.
        timestamp: Unix timestamp when the snapshot was created.

    Example:
        snapshot = Snapshot(
            aggregate_id="order-123",
            version=50,
            state={"status": "active", "total": 199.99},
        )
        await event_store.save_snapshot(snapshot)
    """

    def __init__(
        self,
        aggregate_id: str,
        version: int,
        state: Dict[str, Any],
        timestamp: Optional[float] = None,
    ):
        """
        Initialize a new Snapshot.

        Args:
            aggregate_id: ID of the aggregate this snapshot belongs to.
            version: Aggregate version at the time of the snapshot.
            state: Dictionary of the aggregate state.
        """
        self.aggregate_id: str = aggregate_id
        self.version: int = version
        self.state: Dict[str, Any] = state
        self.timestamp: float = time.time() if timestamp is None else timestamp

    def __repr__(self) -> str:
        return (
            f"Snapshot(aggregate_id={self.aggregate_id!r}, "
            f"version={self.version}, state_keys={list(self.state.keys())})"
        )


class EventStore:
    """
    Event store for persisting and retrieving domain events.

    Provides an async interface for appending events, retrieving event
    streams, and managing snapshots. The default implementation uses
    in-memory storage suitable for development and testing.

    DuckDB is the persistent backend implemented by this module. PostgreSQL
    configuration remains reserved for a future native backend and is rejected
    by ``EventStore.connect`` until that backend exists.

    Attributes:
        config: EventSourcingConfig used to create this store.
        connected: Whether the store is currently connected.

    Example:
        config = EventSourcingConfig.duckdb("./data/events.duckdb")
        store = await EventStore.connect(config)

        # Append events
        events = [
            Event("OrderCreated", {"total": 99.99}, aggregate_id="order-1"),
            Event("OrderShipped", {"carrier": "UPS"}, aggregate_id="order-1"),
        ]
        await store.append("order-1", events)

        # Retrieve events
        history = await store.get_events("order-1")
        print(len(history))  # 2

        # Snapshots
        snapshot = Snapshot("order-1", 2, {"status": "shipped"})
        await store.save_snapshot(snapshot)
        loaded = await store.get_snapshot("order-1")

        await store.close()
    """

    def __init__(self, config: Optional["EventSourcingConfig"] = None):
        """
        Initialize the EventStore.

        Prefer using the ``connect`` classmethod for async initialization.

        Args:
            config: Optional EventSourcingConfig. Defaults to in-memory storage.
        """
        self.config: "EventSourcingConfig" = config or EventSourcingConfig()
        self.connected: bool = False
        self._database: Optional[AsyncFileDatabase] = None

        # Internal dict-based storage for testing/development.
        self._events: Dict[str, List[Event]] = {}
        self._snapshots: Dict[str, Snapshot] = {}

    @classmethod
    async def connect(cls, config: Optional["EventSourcingConfig"] = None) -> "EventStore":
        """
        Create and connect an EventStore instance.

        Factory classmethod for async initialization of the event store.

        Args:
            config: Optional EventSourcingConfig. Defaults to in-memory storage.

        Returns:
            A connected EventStore instance ready for use.

        Example:
            config = EventSourcingConfig.memory()
            store = await EventStore.connect(config)
        """
        store = cls(config)
        store_type = getattr(store.config, "store_type", "memory").lower()
        if store_type not in {"memory", "duckdb"}:
            raise ValueError(
                f"EventStore backend {store_type!r} is not implemented; "
                "use EventSourcingConfig.memory() or EventSourcingConfig.duckdb(path)."
            )
        if store_type == "duckdb":
            connection_url = getattr(store.config, "connection_url", None)
            if not connection_url:
                connection_url = getattr(store.config, "_connection_url", None)
            if not connection_url:
                raise ValueError("DuckDB event sourcing requires a database path")
            if "://" not in connection_url:
                connection_url = f"duckdb://{connection_url}"
            store._database = AsyncFileDatabase.from_url(connection_url)
            await store._database.execute(
                """
                CREATE TABLE IF NOT EXISTS cello_events (
                    event_id VARCHAR PRIMARY KEY,
                    aggregate_id VARCHAR NOT NULL,
                    event_type VARCHAR NOT NULL,
                    data_json VARCHAR NOT NULL,
                    metadata_json VARCHAR NOT NULL,
                    version BIGINT NOT NULL,
                    timestamp DOUBLE NOT NULL,
                    UNIQUE (aggregate_id, version)
                )
                """
            )
            await store._database.execute(
                """
                CREATE TABLE IF NOT EXISTS cello_snapshots (
                    aggregate_id VARCHAR PRIMARY KEY,
                    version BIGINT NOT NULL,
                    state_json VARCHAR NOT NULL,
                    timestamp DOUBLE NOT NULL
                )
                """
            )
        store.connected = True
        return store

    async def append(
        self,
        aggregate_id: str,
        events: List[Event],
        expected_version: Optional[int] = None,
        snapshot_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Append events to the event stream for an aggregate.

        Events are added in order and assigned sequential version numbers
        within the aggregate's stream.

        Args:
            aggregate_id: ID of the aggregate owning these events.
            events: List of Event objects to append.
            expected_version: Optional current stream version for optimistic
                concurrency control. ``None`` appends at the observed version.
            snapshot_state: Exact aggregate state to persist when the append
                reaches the configured snapshot interval.

        Raises:
            RuntimeError: If the store is not connected.
            ValueError: If the expected version or event retention limit fails.

        Example:
            event = Event("ItemAdded", {"item": "Widget"}, aggregate_id="cart-1")
            await store.append("cart-1", [event])
        """
        if not self.connected:
            raise RuntimeError("EventStore is not connected. Call connect() first.")
        if not events:
            return

        max_events = getattr(
            self.config,
            "max_events_per_aggregate",
            getattr(self.config, "max_events", 10000),
        )

        if self._database is not None:
            async with self._database.transaction() as tx:
                current_version = await tx.fetchval(
                    "SELECT COALESCE(MAX(version), 0) AS version "
                    "FROM cello_events WHERE aggregate_id = $1",
                    aggregate_id,
                )
                current_version = int(current_version or 0)
                if expected_version is not None and current_version != expected_version:
                    raise ValueError(
                        f"Concurrency conflict for aggregate {aggregate_id!r}: "
                        f"expected version {expected_version}, actual {current_version}"
                    )
                if current_version + len(events) > max_events:
                    raise ValueError(
                        f"Aggregate {aggregate_id!r} would exceed max events limit of {max_events}"
                    )

                next_version = current_version
                for event in events:
                    next_version += 1
                    event.version = next_version
                    event.aggregate_id = aggregate_id
                    await tx.execute(
                        """
                        INSERT INTO cello_events
                            (event_id, aggregate_id, event_type, data_json,
                             metadata_json, version, timestamp)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        event.id,
                        aggregate_id,
                        event.event_type,
                        json.dumps(event.data, separators=(",", ":")),
                        json.dumps(event.metadata, separators=(",", ":")),
                        event.version,
                        event.timestamp,
                    )

                snapshot_interval = getattr(self.config, "snapshot_interval", 0)
                if (
                    getattr(self.config, "enable_snapshots", True)
                    and snapshot_interval > 0
                    and next_version // snapshot_interval > current_version // snapshot_interval
                ):
                    if snapshot_state is None:
                        snapshot_state = _derive_snapshot_state(
                            await tx.fetch(
                                """
                                SELECT data_json FROM cello_events
                                WHERE aggregate_id = $1
                                ORDER BY version ASC
                                """,
                                aggregate_id,
                            )
                        )
                    await _upsert_snapshot(
                        tx,
                        Snapshot(aggregate_id, next_version, snapshot_state),
                    )
            return

        if aggregate_id not in self._events:
            self._events[aggregate_id] = []

        current_version = len(self._events[aggregate_id])
        if expected_version is not None and current_version != expected_version:
            raise ValueError(
                f"Concurrency conflict for aggregate {aggregate_id!r}: "
                f"expected version {expected_version}, actual {current_version}"
            )
        if current_version + len(events) > max_events:
            raise ValueError(
                f"Aggregate {aggregate_id!r} would exceed max events limit of {max_events}"
            )

        for event in events:
            current_version += 1
            event.version = current_version
            event.aggregate_id = aggregate_id
            self._events[aggregate_id].append(event)

        if (
            getattr(self.config, "enable_snapshots", True)
            and getattr(self.config, "snapshot_interval", 0) > 0
            and current_version // self.config.snapshot_interval
            > (current_version - len(events)) // self.config.snapshot_interval
        ):
            if snapshot_state is None:
                snapshot_state = _derive_snapshot_state(
                    [{"data_json": json.dumps(event.data)} for event in self._events[aggregate_id]]
                )
            previous = self._snapshots.get(aggregate_id)
            if previous is None or current_version > previous.version:
                self._snapshots[aggregate_id] = Snapshot(
                    aggregate_id, current_version, dict(snapshot_state)
                )

    async def get_events(
        self, aggregate_id: str, since_version: int = 0
    ) -> List[Event]:
        """
        Retrieve events for an aggregate, optionally from a specific version.

        Args:
            aggregate_id: ID of the aggregate to retrieve events for.
            since_version: Only return events after this version (default: 0).

        Returns:
            Ordered list of Event objects.

        Example:
            # Get all events
            events = await store.get_events("order-1")

            # Get events since version 5
            new_events = await store.get_events("order-1", since_version=5)
        """
        if not self.connected:
            raise RuntimeError("EventStore is not connected. Call connect() first.")

        if self._database is not None:
            rows = await self._database.fetch(
                """
                SELECT event_id, aggregate_id, event_type, data_json,
                       metadata_json, version, timestamp
                FROM cello_events
                WHERE aggregate_id = $1 AND version > $2
                ORDER BY version ASC
                """,
                aggregate_id,
                since_version,
            )
            return [_event_from_row(row) for row in rows]

        all_events = self._events.get(aggregate_id, [])
        return [e for e in all_events if e.version > since_version]

    async def save_snapshot(self, snapshot: Snapshot) -> None:
        """
        Save a snapshot of aggregate state.

        Only the latest snapshot per aggregate is retained.

        Args:
            snapshot: Snapshot instance to save.

        Example:
            snapshot = Snapshot("order-1", 100, {"status": "completed"})
            await store.save_snapshot(snapshot)
        """
        if not self.connected:
            raise RuntimeError("EventStore is not connected. Call connect() first.")

        if self._database is not None:
            async with self._database.transaction() as tx:
                await _upsert_snapshot(tx, snapshot)
            return

        previous = self._snapshots.get(snapshot.aggregate_id)
        if previous is None or snapshot.version > previous.version:
            self._snapshots[snapshot.aggregate_id] = snapshot

    async def get_snapshot(self, aggregate_id: str) -> Optional[Snapshot]:
        """
        Retrieve the latest snapshot for an aggregate.

        Args:
            aggregate_id: ID of the aggregate.

        Returns:
            The latest Snapshot, or None if no snapshot exists.

        Example:
            snapshot = await store.get_snapshot("order-1")
            if snapshot:
                aggregate.state = snapshot.state
                aggregate.version = snapshot.version
        """
        if not self.connected:
            raise RuntimeError("EventStore is not connected. Call connect() first.")

        if self._database is not None:
            row = await self._database.fetchrow(
                """
                SELECT aggregate_id, version, state_json, timestamp
                FROM cello_snapshots
                WHERE aggregate_id = $1
                """,
                aggregate_id,
            )
            if row is None:
                return None
            return Snapshot(
                row["aggregate_id"],
                int(row["version"]),
                json.loads(row["state_json"]),
                float(row["timestamp"]),
            )

        return self._snapshots.get(aggregate_id)

    async def close(self) -> None:
        """
        Close the event store connection.

        After calling close, all subsequent operations will raise
        RuntimeError until connect is called again.

        Example:
            await store.close()
        """
        if self._database is not None:
            await self._database.close()
            self._database = None
        self.connected = False

    def __repr__(self) -> str:
        aggregate_count = len(self._events)
        total_events = sum(len(v) for v in self._events.values())
        backend = "duckdb" if self._database is not None else self.config.store_type
        return (
            f"EventStore(store_type={backend!r}, "
            f"connected={self.connected}, aggregates={aggregate_count}, "
            f"total_events={total_events})"
        )


async def _upsert_snapshot(tx: Any, snapshot: Snapshot) -> None:
    """Insert a snapshot or replace it only when the new version is newer."""
    existing = await tx.fetchval(
        "SELECT version FROM cello_snapshots WHERE aggregate_id = $1",
        snapshot.aggregate_id,
    )
    if existing is not None and int(existing) >= snapshot.version:
        return
    if existing is None:
        await tx.execute(
            """
            INSERT INTO cello_snapshots
                (aggregate_id, version, state_json, timestamp)
            VALUES ($1, $2, $3, $4)
            """,
            snapshot.aggregate_id,
            snapshot.version,
            json.dumps(snapshot.state, separators=(",", ":")),
            snapshot.timestamp,
        )
    else:
        await tx.execute(
            """
            UPDATE cello_snapshots
            SET version = $2, state_json = $3, timestamp = $4
            WHERE aggregate_id = $1 AND version < $2
            """,
            snapshot.aggregate_id,
            snapshot.version,
            json.dumps(snapshot.state, separators=(",", ":")),
            snapshot.timestamp,
        )


def _derive_snapshot_state(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a best-effort state snapshot when no aggregate is supplied.

    Domain aggregates can pass ``snapshot_state`` to ``append`` for exact
    state. The fallback keeps the latest value for each object payload key.
    """
    state: Dict[str, Any] = {}
    for row in rows:
        payload = row.get("data_json")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(payload, dict):
            state.update(payload)
    return state


def _event_from_row(row: Dict[str, Any]) -> Event:
    """Restore an Event from a DuckDB row."""
    event = Event(
        event_type=row["event_type"],
        data=json.loads(row["data_json"]),
        aggregate_id=row["aggregate_id"],
        metadata=json.loads(row["metadata_json"]),
    )
    event.id = row["event_id"]
    event.version = int(row["version"])
    event.timestamp = float(row["timestamp"])
    return event


class EventSourcingConfig:
    """
    Configuration for the event sourcing subsystem.

    Controls the storage backend, snapshot behaviour, and event
    retention settings.

    Attributes:
        store_type: Storage backend type ("memory", "duckdb", or "postgresql").

        snapshot_interval: Number of events between automatic snapshots.
        enable_snapshots: Whether to enable snapshot support.
        max_events: Maximum number of events to retain per aggregate.

    Example:
        # In-memory for development
        config = EventSourcingConfig.memory()

        # Persistent local storage
        config = EventSourcingConfig.duckdb("./data/events.duckdb")
    """

    def __init__(
        self,
        store_type: str = "memory",
        snapshot_interval: int = 100,
        enable_snapshots: bool = True,
        max_events: int = 10000,
        connection_url: Optional[str] = None,
    ):
        """
        Initialize EventSourcingConfig.

        Args:
            store_type: Storage backend ("memory", "duckdb", or "postgresql").
            snapshot_interval: Events between automatic snapshots (default: 100).
            enable_snapshots: Enable snapshot support (default: True).
            max_events: Maximum events per aggregate (default: 10000).
        """
        self.store_type: str = store_type
        self.snapshot_interval: int = snapshot_interval
        self.enable_snapshots: bool = enable_snapshots
        self.max_events: int = max_events
        self.max_events_per_aggregate: int = max_events
        self.connection_url: Optional[str] = connection_url
        self._connection_url: Optional[str] = connection_url

    @classmethod
    def memory(cls) -> "EventSourcingConfig":
        """
        Create an in-memory EventSourcingConfig for development and testing.

        Returns:
            EventSourcingConfig with memory storage backend.

        Example:
            config = EventSourcingConfig.memory()
            store = await EventStore.connect(config)
        """
        return cls(
            store_type="memory",
            snapshot_interval=100,
            enable_snapshots=True,
            max_events=10000,
        )

    @classmethod
    def duckdb(cls, path: str) -> "EventSourcingConfig":
        """Create a persistent Event Sourcing configuration backed by DuckDB."""
        if not path or not path.strip():
            raise ValueError("DuckDB event sourcing requires a non-empty database path")
        return cls(
            store_type="duckdb",
            snapshot_interval=100,
            enable_snapshots=True,
            max_events=10000,
            connection_url=path if "://" in path else f"duckdb://{path}",
        )

    @classmethod
    def postgresql(cls, url: str) -> "EventSourcingConfig":
        """
        Create an EventSourcingConfig backed by PostgreSQL.

        Args:
            url: PostgreSQL connection URL.

        Returns:
            EventSourcingConfig with PostgreSQL storage backend.

        Example:
            config = EventSourcingConfig.postgresql(
                "postgresql://user:pass@localhost/events"
            )
        """
        config = cls(
            store_type="postgresql",
            snapshot_interval=100,
            enable_snapshots=True,
            max_events=10000,
        )
        config.connection_url = url
        config._connection_url = url
        return config

    def __repr__(self) -> str:
        return (
            f"EventSourcingConfig(store_type={self.store_type!r}, "
            f"snapshot_interval={self.snapshot_interval}, "
            f"enable_snapshots={self.enable_snapshots}, "
            f"max_events={self.max_events}, connection_url={self.connection_url!r})"
        )
