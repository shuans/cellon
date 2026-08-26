---
title: GraphQL API
description: Add Cello's built-in GraphQL HTTP endpoint alongside REST routes
tags:
  - GraphQL
  - API
  - Schema
  - Queries
  - REST API
  - Examples
---

# GraphQL API

This example uses Cello's built-in Python GraphQL engine. It mounts query and mutation resolvers at `/graphql` while keeping regular REST routes in the same application. The engine supports variables, nested field projection, limited introspection, and the explicit `DataLoader` batching primitive. Subscription execution is available through `GraphQL.subscribe()`; WebSocket subscription transport is not included.

## Complete Example

```python
from cello import App, Response
from cello.graphql import DataLoader, Mutation, Query, Schema

AUTHORS = {
    1: {"id": 1, "name": "Ada Lovelace", "email": "ada@example.com"},
    2: {"id": 2, "name": "Grace Hopper", "email": "grace@example.com"},
}
POSTS = [
    {"id": 1, "title": "Notes on the Analytical Engine", "author_id": 1},
    {"id": 2, "title": "Compiling the Future", "author_id": 2},
]


async def load_authors(ids):
    """Batch-load authors; replace with one database query in production."""
    return [AUTHORS.get(author_id) for author_id in ids]


author_loader = DataLoader(load_authors)


@Query
def posts(info) -> list:
    return POSTS


@Query
def author(info, id: int) -> dict:
    return AUTHORS.get(id)


@Mutation
def create_post(info, title: str, author_id: int) -> dict:
    post = {"id": len(POSTS) + 1, "title": title, "author_id": author_id}
    POSTS.append(post)
    return post


app = App()
app.mount_graphql(
    Schema()
    .query(posts)
    .query(author)
    .mutation(create_post)
    .build()
)


@app.get("/health")
def health(request):
    return Response.json({"status": "ok", "graphql": "/graphql"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

## Example Requests

```bash
# Query with nested field projection
curl -X POST http://127.0.0.1:8000/graphql \
  -H 'content-type: application/json' \
  -d '{"query":"{ posts { id title authorId } }"}'

# Mutation with variables
curl -X POST http://127.0.0.1:8000/graphql \
  -H 'content-type: application/json' \
  -d '{"query":"mutation Create($title: String!, $authorId: Int!) { createPost(title: $title, authorId: $authorId) { id title } }","variables":{"title":"New post","authorId":1}}'

# GET Playground or query endpoint
curl 'http://127.0.0.1:8000/graphql?query=%7B%20posts%20%7B%20id%20title%20%7D%20%7D'
```

## Key Concepts

- `@Query` and `@Mutation` mark resolver functions. `Schema` composes them into a `GraphQL` execution engine.
- `app.mount_graphql(schema)` registers the HTTP GET and POST handlers. `app.enable_graphql(config)` is the empty-engine convenience form; add resolvers through `app.graphql` before serving.
- GraphQL names are accepted in camelCase while Python resolvers may use snake_case, so `author_id` is available as `authorId`.
- `DataLoader` batches application-level lookups. Create one per request when its cache must not be shared across requests.
- `GraphQL.subscribe()` provides subscription execution. A WebSocket protocol adapter, federation, and full GraphQL validation are outside this built-in engine.

## Running This Example

```bash
python examples/advanced/graphql.py
```
