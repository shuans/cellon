"""Integration tests for the SQLite and DuckDB database backends."""

import asyncio

import pytest

from cello import App, DatabaseConfig, EventSourcingConfig
from cello.eventsourcing import Event, EventStore
from cello.orm import AutoField, CharField, IntegerField, Model, setup


def _run(coro):
    return asyncio.run(coro)


class FileDbUser(Model):
    id = AutoField()
    name = CharField(max_length=100)
    age = IntegerField(null=True)

    class Meta:
        table_name = "test_file_db_user"


@pytest.mark.parametrize("url", ["sqlite:///:memory:"])
def test_sqlite_raw_queries_and_transactions(url):
    async def main():
        app = App()
        app.enable_database(DatabaseConfig(url=url))
        db = app.database

        assert db.backend == "sqlite"
        await db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        await db.execute("INSERT INTO users (name) VALUES ($1)", "Alice")
        assert await db.fetchval("SELECT name FROM users WHERE id = $1", 1) == "Alice"

        async with db.transaction() as tx:
            await tx.execute("INSERT INTO users (name) VALUES ($1)", "Bob")
        assert await db.fetchval("SELECT count(*) FROM users") == 2

        with pytest.raises(ValueError):
            async with db.transaction() as tx:
                await tx.execute("INSERT INTO users (name) VALUES ($1)", "Rolled back")
                raise ValueError("rollback")
        assert await db.fetchval("SELECT count(*) FROM users") == 2
        await db.close()

    _run(main())


def test_sqlite_orm_crud_and_filters():
    async def main():
        app = App()
        app.enable_database(DatabaseConfig(url="sqlite:///:memory:"))
        setup(app.database)
        await FileDbUser.create_table()

        await FileDbUser.objects.create(name="Alice", age=30)
        await FileDbUser.objects.create(name="Bob", age=20)
        assert await FileDbUser.objects.count() == 2
        assert (await FileDbUser.objects.get(name="Alice")).age == 30
        adults = await FileDbUser.objects.filter(age__gte=25)
        assert [user.name for user in adults] == ["Alice"]

        await FileDbUser.objects.filter(name="Bob").update(age=21)
        assert (await FileDbUser.objects.get(name="Bob")).age == 21
        await FileDbUser.objects.filter(name="Alice").delete()
        assert await FileDbUser.objects.count() == 1
        await app.database.close()

    _run(main())


def test_duckdb_event_store_persists_events_and_snapshots(tmp_path):
    pytest.importorskip("duckdb")

    async def main():
        path = str(tmp_path / "events.duckdb")
        config = EventSourcingConfig.duckdb(path)
        first = await EventStore.connect(config)
        first.config.snapshot_interval = 2
        await first.append(
            "order-1",
            [
                Event("OrderCreated", {"status": "created"}),
                Event("OrderShipped", {"status": "shipped"}),
            ],
            expected_version=0,
            snapshot_state={"status": "shipped"},
        )
        await first.close()

        second = await EventStore.connect(config)
        events = await second.get_events("order-1")
        snapshot = await second.get_snapshot("order-1")
        assert [event.version for event in events] == [1, 2]
        assert snapshot is not None
        assert snapshot.version == 2
        assert snapshot.state == {"status": "shipped"}
        with pytest.raises(ValueError, match="Concurrency conflict"):
            await second.append(
                "order-1",
                [Event("Stale", {})],
                expected_version=0,
            )
        assert len(await second.get_events("order-1")) == 2
        await second.close()

    _run(main())


def test_duckdb_backend_when_installed():
    pytest.importorskip("duckdb")

    async def main():
        app = App()
        app.enable_database(DatabaseConfig(url="duckdb:///:memory:"))
        setup(app.database)
        assert app.database.backend == "duckdb"
        await FileDbUser.create_table()
        user = await FileDbUser.objects.create(name="Duck", age=7)
        assert user.id == 1
        assert (await FileDbUser.objects.get(name="Duck")).age == 7
        await app.database.close()

    _run(main())
