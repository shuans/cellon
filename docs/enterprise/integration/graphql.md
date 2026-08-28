---
title: GraphQL Integration
description: GraphQL support in Cellon - queries, mutations, subscriptions, and DataLoader
---

# GraphQL Integration

Cello provides decorator-based GraphQL schema definition, a Python execution engine, DataLoader support, and HTTP query/mutation mounting on `App`. Subscription execution is available as an engine primitive; WebSocket subscription transport and full schema validation are not included yet.

## Quick Start

```python
from cello import App
from cello.graphql import Query, Mutation, Schema, DataLoader, GraphQL

app = App()

@Query
def users(info) -> list:
    return [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

@Query
def user(info, id: int) -> dict:
    return {"id": id, "name": "Alice"}

@Mutation
def create_user(info, name: str, email: str) -> dict:
    return {"id": 3, "name": name, "email": email}

schema = Schema().query(users).query(user).mutation(create_user).build()
app.mount_graphql(schema)

# The same engine can also be executed directly.
result = await schema.execute("{ users { id name } }")
```

## Decorators

### @Query

Marks a function as a GraphQL query resolver.

```python
from cello.graphql import Query

@Query
def books(info) -> list:
    return db.get_all_books()

@Query
def book(info, id: int) -> dict:
    return db.get_book(id)
```

### @Mutation

Marks a function as a GraphQL mutation resolver.

```python
from cello.graphql import Mutation

@Mutation
def create_book(info, title: str, author: str) -> dict:
    return db.create_book(title, author)
```

### @Subscription

Marks an async generator as a GraphQL subscription.

```python
from cello.graphql import Subscription

@Subscription
async def book_added(info):
    async for event in event_stream("book_added"):
        yield event
```

### WebSocket Subscriptions (graphql-ws)

Mounting GraphQL automatically registers a WebSocket endpoint on the same path
speaking the [graphql-ws](https://github.com/enisdenjo/graphql-ws) protocol, so
clients can subscribe in realtime:

```python
app.mount_graphql(Schema().subscription(book_added).build(), path="/graphql")
```

Connect with any graphql-ws client:

```javascript
import { createClient } from "graphql-ws";

const client = createClient({ url: "ws://localhost:8000/graphql" });
client.subscribe(
  { query: "subscription { bookAdded { title } }" },
  { next: (payload) => console.log(payload) }
);
```

The server handles `connection_init` → `connection_ack`, `subscribe` →
`next`/`complete`, `complete` (cancellation), `ping`/`pong`, and
`connection_terminate`.

## DataLoader

Prevents N+1 query problems by batching and caching database calls.

```python
from cello.graphql import DataLoader

async def batch_load_authors(ids):
    return [db.get_author(id) for id in ids]

author_loader = DataLoader(batch_fn=batch_load_authors)

# Single load (cached after first call)
author = await author_loader.load(1)

# Batch load
authors = await author_loader.load_many([1, 2, 3])

# Clear cache
author_loader.clear(1)       # Clear single key
author_loader.clear()         # Clear all
```

## Schema Builder

Compose queries, mutations, and subscriptions into a schema using the fluent builder API.

```python
from cello.graphql import Schema

schema = (
    Schema()
    .query(users)
    .query(user)
    .mutation(create_user)
    .mutation(update_user)
    .subscription(user_created)
    .build()
)
```

### Class-Based Types

You can also register entire classes where each method becomes a resolver:

```python
class QueryType:
    def users(self, info) -> list:
        return db.get_all_users()

    def books(self, info) -> list:
        return db.get_all_books()

schema = Schema().query(QueryType).build()
```

## GraphQL Engine

Execute queries directly using the GraphQL engine.

```python
from cello.graphql import GraphQL

engine = GraphQL()
engine.add_query(users)
engine.add_mutation(create_user)

result = await engine.execute("{ users }")
# {"data": {"users": [...]}, "errors": None}

# With variables
result = await engine.execute(
    "mutation($name: String!) { createUser(name: $name) }",
    variables={"name": "Alice"}
)
```

## Configuration

Enable GraphQL on your Cello app:

```python
from cello import App, GraphQLConfig
from cello.graphql import Schema

app = App()
app.mount_graphql(
    Schema().query(users).mutation(create_user).build(),
    path=GraphQLConfig().path,
    playground=True,
    introspection=True,
)

# Alternatively, enable an empty engine and register resolvers before serving.
# app.enable_graphql(GraphQLConfig(path="/graphql", playground=True, introspection=True))
# app.graphql.add_query(users)
# app.graphql.add_mutation(create_user)
```

| Option | Default | Description |
|--------|---------|-------------|
| `path` | `/graphql` | GraphQL endpoint path |
| `playground` | `True` | Enable the lightweight browser query form |
| `introspection` | `True` | Enable schema introspection |

## API Reference

| Class | Description |
|-------|-------------|
| `Query` | Decorator for query resolvers |
| `Mutation` | Decorator for mutation resolvers |
| `Subscription` | Decorator for subscription resolvers |
| `Field` | GraphQL field definition with optional resolver |
| `DataLoader` | Batching and caching loader for N+1 prevention |
| `GraphQL` | Execution engine for running queries |
| `Schema` | Fluent builder for composing a schema |
