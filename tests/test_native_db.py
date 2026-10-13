"""
Integration tests for the native data layer (Postgres pool + Redis) and the ORM.

These talk to **real** servers. They are skipped automatically when Postgres or
Redis is unreachable, so they never break a machine without them.

Configure via env (defaults shown):
    CELLO_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/cello_test
    CELLO_TEST_REDIS_URL=redis://127.0.0.1:6379
"""

import asyncio
import os

import pytest

from cello import App, DatabaseConfig, OperationalError, RedisConfig
from cello.database import transactional  # noqa: F401 (imported for API surface)
from cello.orm import (
    Model,
    AutoField,
    CharField,
    BooleanField,
    IntegerField,
    JSONField,
    ForeignKey,
    setup,
)

DSN = os.environ.get(
    "CELLO_TEST_DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:5432/cello_test",
)
REDIS_URL = os.environ.get("CELLO_TEST_REDIS_URL", "redis://127.0.0.1:6379")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _pg_available():
    async def check():
        app = App()
        app.enable_database(DatabaseConfig(url=DSN, pool_size=1))
        await app.database.ping()
        await app.database.close()
    try:
        _run(check())
        return True
    except Exception:
        return False


def _redis_available():
    async def check():
        app = App()
        app.enable_redis(RedisConfig(url=REDIS_URL))
        await app.redis.ping()
        await app.redis.close()
    try:
        _run(check())
        return True
    except Exception:
        return False


pg = pytest.mark.skipif(not _pg_available(), reason="PostgreSQL not reachable")
rd = pytest.mark.skipif(not _redis_available(), reason="Redis not reachable")


# ── Raw pool ──────────────────────────────────────────────────────────────────

@pg
def test_raw_queries_and_types():
    async def main():
        app = App()
        app.enable_database(DatabaseConfig(url=DSN, pool_size=3))
        db = app.database
        await db.execute("DROP TABLE IF EXISTS t_raw")
        await db.execute(
            "CREATE TABLE t_raw (id SERIAL PRIMARY KEY, name TEXT, n INT, "
            "flag BOOLEAN, doc JSONB)"
        )
        new_id = await db.fetchval(
            "INSERT INTO t_raw (name, n, flag, doc) VALUES ($1, $2, $3, $4) RETURNING id",
            "alice", 42, True, {"a": [1, 2, 3]},
        )
        assert new_id == 1

        row = await db.fetchrow("SELECT * FROM t_raw WHERE id = $1", 1)
        assert row["name"] == "alice"
        assert row["n"] == 42
        assert row["flag"] is True
        assert row["doc"] == {"a": [1, 2, 3]}  # JSONB decoded to nested Python

        rows = await db.fetch("SELECT * FROM t_raw")
        assert len(rows) == 1

        affected = await db.execute("UPDATE t_raw SET n = n + 1 WHERE id = $1", 1)
        assert affected == 1
        assert await db.fetchval("SELECT n FROM t_raw WHERE id = $1", 1) == 43

        assert await db.fetchrow("SELECT * FROM t_raw WHERE id = $1", 999) is None
        await db.execute("DROP TABLE t_raw")
        await db.close()

    _run(main())


@pg
def test_transaction_commit_and_rollback():
    async def main():
        app = App()
        app.enable_database(DatabaseConfig(url=DSN, pool_size=3))
        db = app.database
        await db.execute("DROP TABLE IF EXISTS t_acct")
        await db.execute("CREATE TABLE t_acct (id INT PRIMARY KEY, bal INT)")
        await db.execute("INSERT INTO t_acct VALUES (1, 100), (2, 100)")

        # commit
        async with db.transaction() as tx:
            await tx.execute("UPDATE t_acct SET bal = bal - 30 WHERE id = 1")
            await tx.execute("UPDATE t_acct SET bal = bal + 30 WHERE id = 2")
        assert await db.fetchval("SELECT bal FROM t_acct WHERE id = 1") == 70

        # rollback
        with pytest.raises(ValueError):
            async with db.transaction() as tx:
                await tx.execute("UPDATE t_acct SET bal = 0 WHERE id = 1")
                raise ValueError("boom")
        assert await db.fetchval("SELECT bal FROM t_acct WHERE id = 1") == 70  # unchanged

        await db.execute("DROP TABLE t_acct")
        await db.close()

    _run(main())


# ── Redis ─────────────────────────────────────────────────────────────────────

@rd
def test_redis_commands():
    async def main():
        app = App()
        app.enable_redis(RedisConfig(url=REDIS_URL))
        r = app.redis
        await r.set("cello:test:k", "v", ex=30)
        assert await r.get("cello:test:k") == "v"

        await r.set("cello:test:c", 0)
        await r.incr("cello:test:c")
        await r.incrby("cello:test:c", 4)
        assert await r.get("cello:test:c") == "5"

        await r.hset("cello:test:h", "f", "hv")
        assert await r.hget("cello:test:h", "f") == "hv"

        await r.delete("cello:test:list")
        await r.rpush("cello:test:list", "a", "b", "c")
        assert await r.lrange("cello:test:list", 0, -1) == ["a", "b", "c"]

        sha = await r.script_load("return tonumber(ARGV[1]) + 1")
        assert await r.evalsha(sha, 0, 41) == 42

        await r.delete("cello:test:k", "cello:test:c", "cello:test:h", "cello:test:list")
        await r.close()

    _run(main())


# ── ORM ───────────────────────────────────────────────────────────────────────

class OrmUser(Model):
    id = AutoField()
    name = CharField(max_length=100)
    email = CharField(max_length=255, null=True)
    active = BooleanField(default=True)
    age = IntegerField(null=True)
    doc = JSONField(null=True)

    class Meta:
        table_name = "t_orm_user"


class OrmPost(Model):
    id = AutoField()
    title = CharField(max_length=200)
    author = ForeignKey(OrmUser)

    class Meta:
        table_name = "t_orm_post"


@pg
def test_orm_crud_filter_and_fk():
    async def main():
        app = App()
        app.enable_database(DatabaseConfig(url=DSN, pool_size=3))
        setup(app.database)

        await OrmPost.drop_table()
        await OrmUser.drop_table()
        await OrmUser.create_table()
        await OrmPost.create_table()

        alice = await OrmUser.objects.create(name="Alice", age=30, doc={"role": "admin"})
        await OrmUser.objects.create(name="Bob", age=25, active=False)
        await OrmUser.objects.create(name="Carol", age=40)

        assert await OrmUser.objects.count() == 3

        top = await OrmUser.objects.order_by("-age").limit(1)
        assert top[0].name == "Carol"

        adults = await OrmUser.objects.filter(age__gte=30).order_by("age")
        assert [u.name for u in adults] == ["Alice", "Carol"]

        got = await OrmUser.objects.get(name="Alice")
        assert got.doc == {"role": "admin"}
        with pytest.raises(OrmUser.DoesNotExist):
            await OrmUser.objects.get(name="Nobody")

        n = await OrmUser.objects.filter(name="Bob").update(active=True, age=26)
        assert n == 1
        assert (await OrmUser.objects.get(name="Bob")).age == 26

        inlist = await OrmUser.objects.filter(name__in=["Alice", "Carol"]).order_by("name")
        assert [u.name for u in inlist] == ["Alice", "Carol"]

        # ForeignKey
        post = await OrmPost.objects.create(title="Hello", author_id=alice.id)
        assert post.author_id == alice.id
        by_alice = await OrmPost.objects.filter(author_id=alice.id)
        assert [p.title for p in by_alice] == ["Hello"]

        # values() + delete
        vals = await OrmUser.objects.filter(active=True).values("name")
        assert {v["name"] for v in vals} == {"Alice", "Bob", "Carol"}
        assert await OrmUser.objects.filter(name="Carol").delete() == 1
        assert await OrmUser.objects.count() == 2

        await OrmPost.drop_table()
        await OrmUser.drop_table()
        await app.database.close()

    _run(main())


# ── Typed database errors ─────────────────────────────────────────────────────


def test_database_error_hierarchy():
    """The hierarchy exists without a server, and nests the documented way."""
    from cello import (
        CheckViolation,
        DataError,
        DatabaseError,
        ForeignKeyViolation,
        IntegrityError,
        NotNullViolation,
        OperationalError,
        ParameterRangeError,
        ParameterTypeError,
        ParameterValueError,
        ProgrammingError,
        UniqueViolation,
    )

    # Code written against 1.3.0 (`except RuntimeError`) keeps catching these.
    assert issubclass(DatabaseError, RuntimeError)
    assert issubclass(IntegrityError, DatabaseError)
    for cls in (
        UniqueViolation,
        NotNullViolation,
        ForeignKeyViolation,
        CheckViolation,
    ):
        assert issubclass(cls, IntegrityError)
    for cls in (DataError, OperationalError, ProgrammingError):
        assert issubclass(cls, DatabaseError)

    # The parameter errors also keep the builtin they used to be raised as.
    assert issubclass(ParameterTypeError, (DataError, TypeError))
    assert issubclass(ParameterRangeError, (DataError, OverflowError))
    assert issubclass(ParameterValueError, (DataError, ValueError))

    # Attributes exist on every instance, even when the driver supplied none.
    exc = DatabaseError("boom")
    for attr in ("sqlstate", "detail", "constraint", "table", "column"):
        assert getattr(exc, attr) is None


@pg
def test_sqlstate_maps_to_a_typed_error():
    async def main():
        app = App()
        app.enable_database(DatabaseConfig(url=DSN, pool_size=2))
        db = app.database
        try:
            await db.execute("DROP TABLE IF EXISTS t_errors")
            await db.execute(
                "CREATE TABLE t_errors ("
                "id serial PRIMARY KEY, "
                "email text UNIQUE NOT NULL, "
                "parent_id int REFERENCES t_errors(id), "
                "score int CHECK (score > 0))"
            )
            await db.execute("INSERT INTO t_errors (email, score) VALUES ($1, $2)", "a@x.com", 1)

            # 23505 unique violation, with the constraint name read off the error.
            with pytest.raises(UniqueViolation) as unique:
                await db.execute("INSERT INTO t_errors (email, score) VALUES ($1, $2)", "a@x.com", 1)
            assert unique.value.sqlstate == "23505"
            assert unique.value.constraint == "t_errors_email_key"
            assert unique.value.table == "t_errors"
            # `except RuntimeError` and `except DatabaseError` still catch it.
            assert isinstance(unique.value, DatabaseError)
            assert isinstance(unique.value, RuntimeError)

            # 23502 NOT NULL.
            with pytest.raises(NotNullViolation) as not_null:
                await db.execute("INSERT INTO t_errors (score) VALUES ($1)", 1)
            assert not_null.value.sqlstate == "23502"
            assert not_null.value.column == "email"

            # 23503 foreign key.
            with pytest.raises(ForeignKeyViolation) as fk:
                await db.execute(
                    "INSERT INTO t_errors (email, parent_id, score) VALUES ($1, $2, $3)",
                    "b@x.com",
                    999,
                    1,
                )
            assert fk.value.sqlstate == "23503"
            assert fk.value.constraint == "t_errors_parent_id_fkey"

            # 23514 CHECK.
            with pytest.raises(CheckViolation) as check:
                await db.execute("INSERT INTO t_errors (email, score) VALUES ($1, $2)", "c@x.com", 0)
            assert check.value.sqlstate == "23514"

            # 42P01 undefined table → ProgrammingError.
            with pytest.raises(ProgrammingError) as missing:
                await db.fetch("SELECT * FROM t_errors_does_not_exist")
            assert missing.value.sqlstate == "42P01"

            # A successful query still works after all of those.
            assert await db.fetchval("SELECT count(*) FROM t_errors") == 1
        finally:
            await db.execute("DROP TABLE IF EXISTS t_errors")
            await db.close()

    _run(main())


@pg
def test_pool_failure_is_operational_error():
    async def main():
        # A port with nothing listening: the pool check-out fails.
        app = App()
        app.enable_database(
            DatabaseConfig(url="postgresql://postgres:postgres@127.0.0.1:59999/nope", pool_size=1)
        )
        with pytest.raises(OperationalError):
            await app.database.fetch("SELECT 1")

    _run(main())
