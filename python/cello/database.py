"""
Cellon database helpers and local database backends.

PostgreSQL remains backed by the native Rust pool. SQLite and DuckDB use a
small async adapter that runs their synchronous drivers in worker threads while
exposing the same fetch/fetchrow/fetchval/execute/transaction API.
"""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
from functools import wraps
from pathlib import Path
from typing import Any, Callable

__all__ = ["transactional", "AsyncFileDatabase"]


class AsyncFileDatabase:
    """Async adapter for SQLite and DuckDB.

    The connection is serialized with an asyncio lock. Individual synchronous
    driver calls run in a worker thread so handlers do not block Cello's event
    loop. DuckDB is imported lazily and is an optional dependency.
    """

    def __init__(self, connection: Any, dialect: str, path: str) -> None:
        self._connection = connection
        self.dialect = dialect
        self.path = path
        self._lock = asyncio.Lock()
        self._closed = False

    @classmethod
    def from_url(cls, url: str, pool_size: int = 1) -> "AsyncFileDatabase":
        del pool_size  # File databases use one serialized connection.
        scheme, _, raw_path = url.partition("://")
        scheme = scheme.lower()
        if scheme not in {"sqlite", "duckdb"}:
            raise ValueError(f"Unsupported file database URL: {url}")

        path = raw_path
        if raw_path.startswith("//") and not raw_path.startswith("///"):
            path = "/" + raw_path.lstrip("/")
        if path in {"", ":memory:", "/:memory:"}:
            path = ":memory:"
        else:
            path = str(Path(path).expanduser())
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        if scheme == "sqlite":
            connection = sqlite3.connect(path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
        else:
            try:
                import duckdb
            except ImportError as exc:
                raise RuntimeError(
                    "DuckDB support requires the optional 'duckdb' package. "
                    "Install it with: pip install duckdb"
                ) from exc
            connection = duckdb.connect(path)

        return cls(connection, scheme, path)

    @property
    def backend(self) -> str:
        return self.dialect

    def _prepare(self, sql: str, params: tuple[Any, ...]) -> tuple[str, tuple[Any, ...]]:
        if self.dialect in {"sqlite", "duckdb"}:
            sql = _postgres_placeholders_to_qmark(sql)
            if self.dialect == "sqlite":
                sql = sql.replace(" ILIKE ", " LIKE ")
        params = tuple(_normalize_param(value) for value in params)
        return sql, params

    def _rows_to_dicts(self, cursor: Any) -> list[dict[str, Any]]:
        description = cursor.description or []
        names = [column[0] for column in description]
        rows = cursor.fetchall()
        if self.dialect == "sqlite":
            return [dict(row) for row in rows]
        return [dict(zip(names, row)) for row in rows]

    async def _call(self, operation: Callable[[], Any]) -> Any:
        if self._closed:
            raise RuntimeError("Database connection is closed")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, operation)

    async def execute(self, sql: str, *params: Any) -> int:
        async with self._lock:
            sql, params = self._prepare(sql, params)

            def operation() -> int:
                cursor = self._connection.execute(sql, params)
                self._connection.commit()
                return max(cursor.rowcount if cursor.rowcount >= 0 else 0, 0)

            return await self._call(operation)

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        async with self._lock:
            sql, params = self._prepare(sql, params)
            return await self._call(
                lambda: self._rows_to_dicts(self._connection.execute(sql, params))
            )

    async def fetchrow(self, sql: str, *params: Any) -> dict[str, Any] | None:
        rows = await self.fetch(sql, *params)
        return rows[0] if rows else None

    async def fetchval(self, sql: str, *params: Any) -> Any:
        row = await self.fetchrow(sql, *params)
        return next(iter(row.values())) if row else None

    async def ping(self) -> bool:
        await self.fetchval("SELECT 1")
        return True

    async def close(self) -> None:
        async with self._lock:
            if not self._closed:
                await self._call(self._connection.close)
                self._closed = True

    def transaction(self) -> "AsyncFileTransaction":
        return AsyncFileTransaction(self)


class AsyncFileTransaction:
    """Async context manager for SQLite/DuckDB transactions."""

    def __init__(self, database: AsyncFileDatabase) -> None:
        self.database = database
        self._active = False

    async def __aenter__(self) -> "AsyncFileTransaction":
        if self.database._closed:
            raise RuntimeError("Database connection is closed")
        await self.database._lock.acquire()
        try:
            await self.database._call(lambda: self.database._connection.execute("BEGIN"))
        except Exception:
            self.database._lock.release()
            raise
        self._active = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        try:
            statement = "ROLLBACK" if exc_type is not None else "COMMIT"
            await self.database._call(
                lambda: self.database._connection.execute(statement)
            )
        finally:
            self._active = False
            self.database._lock.release()
        return False

    def _prepare(self, sql: str, params: tuple[Any, ...]) -> tuple[str, tuple[Any, ...]]:
        return self.database._prepare(sql, params)

    async def execute(self, sql: str, *params: Any) -> int:
        self._ensure_active()
        sql, params = self._prepare(sql, params)

        def operation() -> int:
            cursor = self.database._connection.execute(sql, params)
            return max(cursor.rowcount if cursor.rowcount >= 0 else 0, 0)

        return await self.database._call(operation)

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self._ensure_active()
        sql, params = self._prepare(sql, params)
        return await self.database._call(
            lambda: self.database._rows_to_dicts(
                self.database._connection.execute(sql, params)
            )
        )

    async def fetchrow(self, sql: str, *params: Any) -> dict[str, Any] | None:
        rows = await self.fetch(sql, *params)
        return rows[0] if rows else None

    async def fetchval(self, sql: str, *params: Any) -> Any:
        row = await self.fetchrow(sql, *params)
        return next(iter(row.values())) if row else None

    def _ensure_active(self) -> None:
        if not self._active:
            raise RuntimeError("transaction is not active (use `async with`)")


def _normalize_param(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        import json
        return json.dumps(value)
    return value


def _postgres_placeholders_to_qmark(sql: str) -> str:
    """Convert PostgreSQL positional placeholders to DB-API qmark syntax."""
    result: list[str] = []
    index = 0
    in_single_quote = False
    i = 0
    while i < len(sql):
        char = sql[i]
        if char == "'":
            in_single_quote = not in_single_quote
            result.append(char)
            i += 1
            continue
        if not in_single_quote and char == "$" and i + 1 < len(sql) and sql[i + 1].isdigit():
            i += 1
            while i < len(sql) and sql[i].isdigit():
                i += 1
            result.append("?")
            continue
        result.append(char)
        i += 1
    return "".join(result)


def transactional(func: Callable) -> Callable:
    """Run an async handler inside a database transaction."""
    if not inspect.iscoroutinefunction(func):
        raise TypeError(
            "@transactional requires an 'async def' handler; native transactions "
            "are asynchronous."
        )

    @wraps(func)
    async def wrapper(request, *args, **kwargs):
        try:
            database = request.database
        except AttributeError as exc:
            raise RuntimeError(
                "@transactional needs a database. Call app.enable_database(...) first."
            ) from exc
        async with database.transaction() as tx:
            return await func(request, *args, tx=tx, **kwargs)

    return wrapper
