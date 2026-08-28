"""
Cello GraphQL Integration.

Provides Python-friendly decorators and classes for building GraphQL APIs
with query resolution, mutations, subscriptions, and DataLoader support
for N+1 query prevention.

Example:
    from cello import App
    from cello.graphql import GraphQL, Schema, Query, Mutation, Subscription, DataLoader

    app = App()

    @Query
    def users(info) -> list:
        return [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

    @Mutation
    def create_user(info, name: str) -> dict:
        return {"id": 3, "name": name}

    @Subscription
    def on_message(info) -> dict:
        return {"message": "New message received"}

    # Build schema
    schema = Schema()
    schema.query(users)
    schema.mutation(create_user)
    schema.subscription(on_message)

    gql = schema.build()

    # Execute a query
    result = await gql.execute('{ users { id name } }')

    # DataLoader for N+1 prevention
    async def batch_load_users(keys):
        return [{"id": k, "name": f"User {k}"} for k in keys]

    user_loader = DataLoader(batch_load_users)
    user = await user_loader.load(1)
    users = await user_loader.load_many([1, 2, 3])
"""

import inspect
import json
import re
from functools import wraps
from typing import Any, Callable, Optional, Dict, List


class Query:
    """
    Decorator class for marking a function as a GraphQL query resolver.

    Stores the decorated function along with its name and metadata
    extracted from type hints and docstring.

    Example:
        @Query
        def users(info) -> list:
            \"\"\"Fetch all users.\"\"\"
            return [{"id": 1, "name": "Alice"}]

        @Query
        def user(info, id: int) -> dict:
            \"\"\"Fetch a single user by ID.\"\"\"
            return {"id": id, "name": "Alice"}
    """

    def __init__(self, func: Callable):
        """
        Initialize the Query decorator.

        Args:
            func: The resolver function to wrap.
        """
        self._func = func
        self._name = func.__name__
        self._doc = func.__doc__ or ""
        self._return_type = _extract_return_type(func)
        self._parameters = _extract_parameters(func)
        wraps(func)(self)

    def __call__(self, *args, **kwargs) -> Any:
        """Execute the underlying resolver function."""
        return self._func(*args, **kwargs)

    @property
    def name(self) -> str:
        """Get the resolver name."""
        return self._name

    @property
    def func(self) -> Callable:
        """Get the underlying function."""
        return self._func

    @property
    def return_type(self) -> Optional[str]:
        """Get the return type annotation as a string."""
        return self._return_type

    @property
    def parameters(self) -> Dict[str, str]:
        """Get parameter names mapped to their type annotations."""
        return self._parameters

    def __repr__(self) -> str:
        return f"<Query '{self._name}'>"


class Mutation:
    """
    Decorator class for marking a function as a GraphQL mutation resolver.

    Stores the decorated function along with its name and metadata
    extracted from type hints and docstring.

    Example:
        @Mutation
        def create_user(info, name: str) -> dict:
            \"\"\"Create a new user.\"\"\"
            return {"id": 3, "name": name}

        @Mutation
        def delete_user(info, id: int) -> dict:
            \"\"\"Delete a user by ID.\"\"\"
            return {"deleted": True}
    """

    def __init__(self, func: Callable):
        """
        Initialize the Mutation decorator.

        Args:
            func: The resolver function to wrap.
        """
        self._func = func
        self._name = func.__name__
        self._doc = func.__doc__ or ""
        self._return_type = _extract_return_type(func)
        self._parameters = _extract_parameters(func)
        wraps(func)(self)

    def __call__(self, *args, **kwargs) -> Any:
        """Execute the underlying resolver function."""
        return self._func(*args, **kwargs)

    @property
    def name(self) -> str:
        """Get the resolver name."""
        return self._name

    @property
    def func(self) -> Callable:
        """Get the underlying function."""
        return self._func

    @property
    def return_type(self) -> Optional[str]:
        """Get the return type annotation as a string."""
        return self._return_type

    @property
    def parameters(self) -> Dict[str, str]:
        """Get parameter names mapped to their type annotations."""
        return self._parameters

    def __repr__(self) -> str:
        return f"<Mutation '{self._name}'>"


class Subscription:
    """
    Decorator class for marking a function as a GraphQL subscription resolver.

    Subscriptions are used for real-time data updates over WebSocket
    connections. The decorated function is expected to yield or return
    data as new events occur.

    Example:
        @Subscription
        def on_message(info) -> dict:
            \"\"\"Subscribe to new messages.\"\"\"
            return {"message": "New message received"}

        @Subscription
        async def on_user_created(info) -> dict:
            \"\"\"Subscribe to user creation events.\"\"\"
            return {"user": {"id": 1, "name": "Alice"}}
    """

    def __init__(self, func: Callable):
        """
        Initialize the Subscription decorator.

        Args:
            func: The resolver function to wrap.
        """
        self._func = func
        self._name = func.__name__
        self._doc = func.__doc__ or ""
        self._return_type = _extract_return_type(func)
        self._parameters = _extract_parameters(func)
        self._is_async = inspect.iscoroutinefunction(func) or inspect.isasyncgenfunction(func)
        wraps(func)(self)

    def __call__(self, *args, **kwargs) -> Any:
        """Execute the underlying resolver function."""
        return self._func(*args, **kwargs)

    @property
    def name(self) -> str:
        """Get the resolver name."""
        return self._name

    @property
    def func(self) -> Callable:
        """Get the underlying function."""
        return self._func

    @property
    def return_type(self) -> Optional[str]:
        """Get the return type annotation as a string."""
        return self._return_type

    @property
    def parameters(self) -> Dict[str, str]:
        """Get parameter names mapped to their type annotations."""
        return self._parameters

    def __repr__(self) -> str:
        return f"<Subscription '{self._name}'>"


class Field:
    """
    Defines a GraphQL field with type information and an optional resolver.

    Fields describe the shape of GraphQL types and can carry custom
    resolver functions for computed or derived values.

    Example:
        name_field = Field("name", "String", description="The user's name")

        full_name_field = Field(
            "full_name",
            "String",
            description="Computed full name",
            resolver=lambda obj, info: f"{obj['first']} {obj['last']}"
        )
    """

    def __init__(
        self,
        name: str,
        type_name: str,
        description: Optional[str] = None,
        resolver: Optional[Callable] = None,
    ):
        """
        Initialize a GraphQL field.

        Args:
            name: The field name as it appears in the schema.
            type_name: The GraphQL type name (e.g., "String", "Int", "[User]").
            description: Optional human-readable description of the field.
            resolver: Optional resolver function for computing the field value.
        """
        self.name = name
        self.type_name = type_name
        self.description = description
        self.resolver = resolver

    def resolve(self, obj: Any, info: Any, **kwargs) -> Any:
        """
        Resolve the field value.

        If a custom resolver is set, it is called with the parent object
        and info context. Otherwise, the field value is looked up by name
        on the parent object (dict key or attribute).

        Args:
            obj: The parent object.
            info: The GraphQL resolve info context.
            **kwargs: Additional arguments passed to the resolver.

        Returns:
            The resolved field value.
        """
        if self.resolver is not None:
            return self.resolver(obj, info, **kwargs)

        # Default resolution: dict key or attribute lookup
        if isinstance(obj, dict):
            return obj.get(self.name)
        return getattr(obj, self.name, None)

    def __repr__(self) -> str:
        return f"<Field '{self.name}: {self.type_name}'>"


class DataLoader:
    """
    DataLoader for batching and caching data fetches to prevent N+1 queries.

    Collects individual load requests and dispatches them in a single batch
    call, then caches results for subsequent requests within the same
    execution context.

    Example:
        async def batch_load_users(keys):
            # Single query for all requested user IDs
            rows = await db.fetch_all(
                "SELECT * FROM users WHERE id = ANY($1)", keys
            )
            # Return results in the same order as keys
            user_map = {r["id"]: r for r in rows}
            return [user_map.get(k) for k in keys]

        user_loader = DataLoader(batch_load_users)

        # These will be batched into a single DB query
        user_a = await user_loader.load(1)
        user_b = await user_loader.load(2)
        users = await user_loader.load_many([3, 4, 5])

        # Clear cache for a specific key or all keys
        user_loader.clear(1)
        user_loader.clear()
    """

    def __init__(self, batch_fn: Callable):
        """
        Initialize the DataLoader.

        Args:
            batch_fn: An async function that accepts a list of keys and returns
                      a list of results in the same order. Must return one result
                      per key.
        """
        self._batch_fn = batch_fn
        self._cache: Dict[Any, Any] = {}
        self._batch: List[Any] = []

    async def load(self, key: Any) -> Any:
        """
        Load a single value by key.

        Returns a cached result if available, otherwise adds the key to the
        current batch, dispatches the batch, and returns the result.

        Args:
            key: The key to load.

        Returns:
            The value associated with the key.
        """
        if key in self._cache:
            return self._cache[key]

        self._batch.append(key)
        results = await self._dispatch()

        return self._cache.get(key)

    async def load_many(self, keys: List[Any]) -> List[Any]:
        """
        Load multiple values by their keys.

        Keys already present in the cache are returned immediately.
        Missing keys are batched together in a single dispatch call.

        Args:
            keys: A list of keys to load.

        Returns:
            A list of values in the same order as the input keys.
        """
        missing_keys = [k for k in keys if k not in self._cache]

        if missing_keys:
            self._batch.extend(missing_keys)
            await self._dispatch()

        return [self._cache.get(k) for k in keys]

    def clear(self, key: Any = None) -> None:
        """
        Clear cached values.

        If a key is provided, only that key is removed from the cache.
        If no key is provided, the entire cache is cleared.

        Args:
            key: Optional specific key to remove from the cache.
        """
        if key is not None:
            self._cache.pop(key, None)
        else:
            self._cache.clear()

    async def _dispatch(self) -> List[Any]:
        """
        Execute the batch function with all accumulated keys.

        Drains the internal batch list, calls the batch function, and
        populates the cache with the returned results. The batch function
        must return exactly one result per key, in the same order.

        Returns:
            The list of results from the batch function.

        Raises:
            ValueError: If the batch function returns a different number
                        of results than keys provided.
        """
        if not self._batch:
            return []

        # Drain the batch list
        keys = list(self._batch)
        self._batch.clear()

        # Deduplicate while preserving order for the batch call
        seen = set()
        unique_keys = []
        for k in keys:
            if k not in seen and k not in self._cache:
                seen.add(k)
                unique_keys.append(k)

        if not unique_keys:
            return []

        # Call the batch function
        results = await self._batch_fn(unique_keys)

        if len(results) != len(unique_keys):
            raise ValueError(
                f"DataLoader batch function returned {len(results)} results "
                f"for {len(unique_keys)} keys. Must return exactly one result per key."
            )

        # Populate cache
        for key, value in zip(unique_keys, results):
            self._cache[key] = value

        return results


class _GraphQLSyntaxError(ValueError):
    """Raised when a GraphQL document cannot be parsed."""


class _GraphQLParser:
    """Small dependency-free parser for the GraphQL subset supported by Cello.

    It supports query/mutation/subscription operations, variables, arguments,
    aliases, nested selection sets, lists, objects, and scalar literals. The
    parser deliberately stays independent of an optional GraphQL package so the
    Python integration remains usable in a minimal installation.
    """

    _token_re = re.compile(
        r'''(?:\s+|#[^\\n]*|,)+|(?:\.\.\.|[$!():=@\\[\\]{|}])|(?:-?\\d+(?:\\.\\d+)?)|(?:"(?:\\\\.|[^"\\\\])*"|[_A-Za-z][_0-9A-Za-z]*)'''
    )

    @staticmethod
    def _scan(source: str, position: int):
        pattern = re.compile(
            r'''(?:\s+|#[^\n]*|,)+|(?:\.\.\.|[$!():=@\[\]{|}])|(?:-?\d+(?:\.\d+)?)|(?:"(?:\\.|[^"\\])*"|[_A-Za-z][_0-9A-Za-z]*)'''
        )
        return pattern.match(source, position)

    def __init__(self, source: str):
        self.tokens = []
        position = 0
        while position < len(source):
            match = self._scan(source, position)
            if match is None:
                raise _GraphQLSyntaxError(f"unexpected token at position {position}")
            token = match.group(0)
            position = match.end()
            if token.isspace() or token.startswith("#") or token.startswith(","):
                continue
            if token in {"...", "$", "!", "(", ")", ":", "=", "@", "[", "]", "{", "|", "}"}:
                self.tokens.append(token)
            elif token.startswith('"'):
                try:
                    self.tokens.append(json.loads(token))
                except json.JSONDecodeError as exc:
                    raise _GraphQLSyntaxError("invalid string literal") from exc
            elif re.fullmatch(r"-?\d+", token):
                self.tokens.append(int(token))
            elif re.fullmatch(r"-?\d+\.\d+", token):
                self.tokens.append(float(token))
            else:
                self.tokens.append(token)
        self.index = 0

    def _peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self, expected=None):
        token = self._peek()
        if token is None or (expected is not None and token != expected):
            wanted = expected or "a token"
            raise _GraphQLSyntaxError(f"expected {wanted}, got {token!r}")
        self.index += 1
        return token

    def document(self):
        operations = []
        while self._peek() is not None:
            operations.append(self.operation())
        if not operations:
            raise _GraphQLSyntaxError("document is empty")
        return operations

    def operation(self):
        if self._peek() == "{":
            operation_type, name = "query", None
        else:
            operation_type = self._take()
            if operation_type not in {"query", "mutation", "subscription"}:
                raise _GraphQLSyntaxError(f"unsupported operation {operation_type!r}")
            name = None
            if isinstance(self._peek(), str) and self._peek() not in {"{", "("}:
                name = self._take()
            if self._peek() == "(":
                self._skip_balanced("(", ")")
        return {"type": operation_type, "name": name, "selection": self.selection_set()}

    def _skip_balanced(self, opening, closing):
        self._take(opening)
        depth = 1
        while depth:
            token = self._take()
            if token == opening:
                depth += 1
            elif token == closing:
                depth -= 1

    def selection_set(self):
        self._take("{")
        fields = []
        while self._peek() != "}":
            if self._peek() is None:
                raise _GraphQLSyntaxError("unterminated selection set")
            fields.append(self.field())
        self._take("}")
        return fields

    def field(self):
        first = self._take()
        if not isinstance(first, str):
            raise _GraphQLSyntaxError("field name must be an identifier")
        alias, name = None, first
        if self._peek() == ":":
            self._take(":")
            alias, name = first, self._take()
        arguments = {}
        if self._peek() == "(":
            self._take("(")
            while self._peek() != ")":
                arg_name = self._take()
                self._take(":")
                arguments[arg_name] = self.value()
            self._take(")")
        selection = self.selection_set() if self._peek() == "{" else []
        return {"name": name, "alias": alias or name, "arguments": arguments, "selection": selection}

    def value(self):
        token = self._peek()
        if token == "$":
            self._take("$")
            return {"__variable__": self._take()}
        if token == "[":
            self._take("[")
            values = []
            while self._peek() != "]":
                values.append(self.value())
            self._take("]")
            return values
        if token == "{":
            self._take("{")
            value = {}
            while self._peek() != "}":
                key = self._take()
                self._take(":")
                value[key] = self.value()
            self._take("}")
            return value
        token = self._take()
        if token == "true":
            return True
        if token == "false":
            return False
        if token == "null":
            return None
        return token


def _resolve_graphql_value(value, variables):
    if isinstance(value, dict) and set(value) == {"__variable__"}:
        return variables.get(value["__variable__"])
    if isinstance(value, list):
        return [_resolve_graphql_value(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_graphql_value(item, variables) for key, item in value.items()}
    return value


def _resolver_candidates(name: str) -> list[str]:
    """Return both snake_case and camelCase spellings for a field name."""
    candidates = [name]
    if "_" in name:
        parts = name.split("_")
        candidates.append(parts[0] + "".join(part.capitalize() for part in parts[1:]))
    else:
        snake = re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()
        if snake != name:
            candidates.append(snake)
    return candidates


def _field_value(value: Any, name: str) -> Any:
    """Read a dict/object field using either GraphQL or Python naming."""
    if isinstance(value, dict):
        for candidate in _resolver_candidates(name):
            if candidate in value:
                return value[candidate]
        return None
    for candidate in _resolver_candidates(name):
        if hasattr(value, candidate):
            return getattr(value, candidate)
    return None


def _normalize_argument_names(arguments: dict) -> dict:
    """Accept conventional camelCase GraphQL arguments for Python resolvers."""
    normalized = {}
    for name, value in arguments.items():
        if "_" not in name:
            chars = []
            for char in name:
                if char.isupper():
                    chars.extend(("_", char.lower()))
                else:
                    chars.append(char)
            name = "".join(chars)
        normalized[name] = value
    return normalized


async def _call_graphql_resolver(resolver, info, arguments):
    """Invoke a resolver without passing arguments it did not declare."""
    arguments = _normalize_argument_names(
        _resolve_graphql_value(arguments, info.get("variables", {}))
    )
    try:
        signature = inspect.signature(resolver)
        parameters = signature.parameters
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
        if accepts_kwargs:
            call_args = arguments
        else:
            call_args = {name: value for name, value in arguments.items() if name in parameters}
    except (TypeError, ValueError):
        call_args = arguments
    result = resolver(info, **call_args)
    if inspect.isawaitable(result):
        result = await result
    return result


async def _project_graphql_value(value, selection, info):
    """Apply a GraphQL selection set to dict/object/list resolver results."""
    if not selection or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [await _project_graphql_value(item, selection, info) for item in value]
    projected = {}
    for field in selection:
        name = field["name"]
        child = _field_value(value, name)
        projected[field["alias"]] = await _project_graphql_value(child, field["selection"], info)
    return projected


class GraphQL:
    """
    Main GraphQL execution engine for mounting on a Cello application.

    Manages query, mutation, and subscription resolvers and executes
    incoming GraphQL operations against them.

    Example:
        gql = GraphQL()

        @Query
        def hello(info) -> str:
            return "Hello, world!"

        gql.add_query(hello)

        result = await gql.execute('{ hello }')
        # {"data": {"hello": "Hello, world!"}}
    """

    def __init__(
        self,
        schema: Optional[Dict[str, Any]] = None,
        introspection: bool = True,
    ):
        """
        Initialize the GraphQL engine.

        Args:
            schema: Optional pre-built schema dictionary. If not provided,
                    resolvers can be registered individually via add_query,
                    add_mutation, and add_subscription.
            introspection: Whether ``__schema`` and ``__type`` are available.
        """
        self._schema = schema or {}
        self._queries: Dict[str, Callable] = {}
        self._mutations: Dict[str, Callable] = {}
        self._subscriptions: Dict[str, Callable] = {}
        self._introspection_enabled = introspection

    def set_introspection(self, enabled: bool) -> "GraphQL":
        """Enable or disable GraphQL introspection for this engine."""
        self._introspection_enabled = bool(enabled)
        return self

    def _introspection_type(self, name: str) -> Optional[Dict[str, Any]]:
        """Build the small introspection type model exposed by this engine."""
        if name == "Query":
            fields = self._queries
        elif name == "Mutation":
            fields = self._mutations
        elif name == "Subscription":
            fields = self._subscriptions
        else:
            fields = {}

        if name not in {"Query", "Mutation", "Subscription"}:
            return None
        return {
            "kind": "OBJECT",
            "name": name,
            "description": None,
            "fields": [
                {
                    "name": field_name,
                    "description": None,
                    "args": [],
                    "type": {"kind": "SCALAR", "name": _extract_return_type(func) or "JSON"},
                }
                for field_name, func in fields.items()
            ],
        }

    def _introspection_schema(self) -> Dict[str, Any]:
        """Return the schema object used by ``__schema`` queries."""
        types = [self._introspection_type("Query")]
        if self._mutations:
            types.append(self._introspection_type("Mutation"))
        if self._subscriptions:
            types.append(self._introspection_type("Subscription"))
        return {
            "queryType": {"name": "Query"},
            "mutationType": {"name": "Mutation"} if self._mutations else None,
            "subscriptionType": {"name": "Subscription"} if self._subscriptions else None,
            "types": [item for item in types if item is not None],
        }

    def add_query(self, func: Callable) -> None:
        """
        Register a query resolver.

        Accepts either a plain function or a Query-decorated function.
        The function name is used as the query field name.

        Args:
            func: The resolver function or Query instance.

        Example:
            @Query
            def users(info) -> list:
                return [{"id": 1}]

            gql.add_query(users)
        """
        if isinstance(func, Query):
            self._queries[func.name] = func.func
        else:
            self._queries[func.__name__] = func

    def add_mutation(self, func: Callable) -> None:
        """
        Register a mutation resolver.

        Accepts either a plain function or a Mutation-decorated function.
        The function name is used as the mutation field name.

        Args:
            func: The resolver function or Mutation instance.

        Example:
            @Mutation
            def create_user(info, name: str) -> dict:
                return {"id": 1, "name": name}

            gql.add_mutation(create_user)
        """
        if isinstance(func, Mutation):
            self._mutations[func.name] = func.func
        else:
            self._mutations[func.__name__] = func

    def add_subscription(self, func: Callable) -> None:
        """
        Register a subscription resolver.

        Accepts either a plain function or a Subscription-decorated function.
        The function name is used as the subscription field name.

        Args:
            func: The resolver function or Subscription instance.

        Example:
            @Subscription
            def on_message(info) -> dict:
                return {"message": "hello"}

            gql.add_subscription(on_message)
        """
        if isinstance(func, Subscription):
            self._subscriptions[func.name] = func.func
        else:
            self._subscriptions[func.__name__] = func

    async def execute(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a GraphQL query string against the registered resolvers.

        Args:
            query: The GraphQL query string.
            variables: Optional dictionary of variable values.
            operation_name: Optional operation name when the query contains
                           multiple operations.

        Returns:
            A dictionary with "data" and optionally "errors" keys following
            the GraphQL response specification.

        Example:
            result = await gql.execute(
                'query GetUser($id: Int!) { user(id: $id) { name } }',
                variables={"id": 1},
                operation_name="GetUser"
            )
        """
        try:
            operations = _GraphQLParser(query).document()
            if operation_name is None and len(operations) > 1:
                raise _GraphQLSyntaxError("operation_name is required for multiple operations")
            operation = next(
                (item for item in operations if operation_name is None or item["name"] == operation_name),
                None,
            )
            if operation is None:
                raise _GraphQLSyntaxError(f"operation {operation_name!r} was not found")
            if operation["type"] == "subscription":
                raise _GraphQLSyntaxError("use GraphQL.subscribe() for subscription operations")
        except (ValueError, TypeError) as exc:
            return {"data": None, "errors": [{"message": str(exc)}]}

        resolver_map = self._mutations if operation["type"] == "mutation" else self._queries
        result: Dict[str, Any] = {"data": {}}
        if any(field["name"] in {"__schema", "__type"} for field in operation["selection"]):
            if not self._introspection_enabled:
                return {
                    "data": None,
                    "errors": [{"message": "Introspection is disabled"}],
                }
        errors: List[Dict[str, Any]] = []
        info = {
            "query": query,
            "variables": variables or {},
            "operation_name": operation_name or operation["name"],
        }

        for field in operation["selection"]:
            name = field["name"]
            if name == "__typename":
                root_type = "Mutation" if operation["type"] == "mutation" else "Query"
                result["data"][field["alias"]] = root_type
                continue
            if name == "__schema":
                result["data"][field["alias"]] = await _project_graphql_value(
                    self._introspection_schema(), field["selection"], info
                )
                continue
            if name == "__type":
                type_name = _resolve_graphql_value(
                    field["arguments"].get("name"), variables or {}
                )
                result["data"][field["alias"]] = await _project_graphql_value(
                    self._introspection_type(type_name) if type_name else None,
                    field["selection"],
                    info,
                )
                continue
            resolver = None
            for candidate in _resolver_candidates(name):
                resolver = resolver_map.get(candidate)
                if resolver is not None:
                    break
            path = [field["alias"]]
            if resolver is None:
                errors.append({"message": f"Cannot query field '{name}'", "path": path})
                continue
            try:
                field_info = dict(info, field_name=name, path=path)
                value = await _call_graphql_resolver(resolver, field_info, field["arguments"])
                result["data"][field["alias"]] = await _project_graphql_value(
                    value, field["selection"], field_info
                )
            except Exception as exc:
                errors.append({"message": str(exc), "path": path})
                result["data"][field["alias"]] = None

        if errors:
            result["errors"] = errors
        return result

    async def subscribe(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        operation_name: Optional[str] = None,
    ):
        """Yield subscription payloads from async-generator resolvers.

        Transport framing is owned by the WebSocket integration; this method
        provides the execution primitive that a WebSocket handler can consume.
        """
        operations = _GraphQLParser(query).document()
        operation = next(
            (item for item in operations if operation_name is None or item["name"] == operation_name),
            None,
        )
        if operation is None or operation["type"] != "subscription":
            raise ValueError("subscribe() requires a subscription operation")
        info = {"query": query, "variables": variables or {}, "operation_name": operation_name}
        for field in operation["selection"]:
            resolver = None
            for candidate in _resolver_candidates(field["name"]):
                resolver = self._subscriptions.get(candidate)
                if resolver is not None:
                    break
            if resolver is None:
                raise ValueError(f"Cannot subscribe to field '{field['name']}'")
            field_info = dict(info, field_name=field["name"], path=[field["alias"]])
            arguments = _normalize_argument_names(
                _resolve_graphql_value(field["arguments"], info["variables"])
            )
            stream = resolver(field_info, **arguments)
            if inspect.isawaitable(stream):
                stream = await stream
            async for value in _subscription_values(stream):
                yield {"data": {field["alias"]: await _project_graphql_value(value, field["selection"], field_info)}}

    def get_schema(self) -> Dict[str, Any]:
        """
        Return schema information describing all registered resolvers.

        Returns:
            A dictionary with "queries", "mutations", and "subscriptions"
            keys, each containing a list of resolver descriptors.

        Example:
            schema_info = gql.get_schema()
            # {
            #     "queries": [{"name": "users", "return_type": "list"}],
            #     "mutations": [{"name": "create_user", ...}],
            #     "subscriptions": [...]
            # }
        """
        return {
            "queries": [
                {
                    "name": name,
                    "return_type": _extract_return_type(func),
                    "parameters": _extract_parameters(func),
                }
                for name, func in self._queries.items()
            ],
            "mutations": [
                {
                    "name": name,
                    "return_type": _extract_return_type(func),
                    "parameters": _extract_parameters(func),
                }
                for name, func in self._mutations.items()
            ],
            "subscriptions": [
                {
                    "name": name,
                    "return_type": _extract_return_type(func),
                    "parameters": _extract_parameters(func),
                }
                for name, func in self._subscriptions.items()
            ],
        }

    def __repr__(self) -> str:
        return (
            f"<GraphQL queries={len(self._queries)} "
            f"mutations={len(self._mutations)} "
            f"subscriptions={len(self._subscriptions)} "
            f"introspection={self._introspection_enabled}>"
        )


async def _subscription_values(stream):
    """Normalize a resolver result into an async iterable of payload values.

    Accepts async generators, sync iterables (awaiting coroutine items), and
    single values such as dicts or primitives.
    """
    if hasattr(stream, "__aiter__"):
        async for value in stream:
            yield value
        return
    if isinstance(stream, dict) or not hasattr(stream, "__iter__"):
        values = (stream,)
    else:
        values = stream
    for value in values:
        if inspect.isawaitable(value):
            value = await value
        yield value


class Schema:
    """
    Builder class for constructing a GraphQL schema and producing a
    GraphQL execution engine instance.

    Provides a fluent API for registering query, mutation, and subscription
    resolver types before building the final GraphQL instance.

    Example:
        schema = Schema()

        @Query
        def users(info) -> list:
            return []

        @Mutation
        def create_user(info, name: str) -> dict:
            return {"name": name}

        @Subscription
        def on_message(info) -> dict:
            return {}

        schema.query(users)
        schema.mutation(create_user)
        schema.subscription(on_message)

        gql = schema.build()
        result = await gql.execute('{ users { id } }')
    """

    def __init__(
        self,
        queries: Optional[List[Any]] = None,
        mutations: Optional[List[Any]] = None,
        subscriptions: Optional[List[Any]] = None,
    ):
        """Initialize a schema builder with optional resolver collections."""
        self._queries: List[Any] = list(queries or [])
        self._mutations: List[Any] = list(mutations or [])
        self._subscriptions: List[Any] = list(subscriptions or [])

    def query(self, type_class: Any) -> "Schema":
        """
        Register a query type or resolver.

        Args:
            type_class: A Query-decorated function, a plain function,
                        or a class whose methods are query resolvers.

        Returns:
            Self for method chaining.

        Example:
            schema.query(users_query)
        """
        self._queries.append(type_class)
        return self

    def mutation(self, type_class: Any) -> "Schema":
        """
        Register a mutation type or resolver.

        Args:
            type_class: A Mutation-decorated function, a plain function,
                        or a class whose methods are mutation resolvers.

        Returns:
            Self for method chaining.

        Example:
            schema.mutation(create_user_mutation)
        """
        self._mutations.append(type_class)
        return self

    def subscription(self, type_class: Any) -> "Schema":
        """
        Register a subscription type or resolver.

        Args:
            type_class: A Subscription-decorated function, a plain function,
                        or a class whose methods are subscription resolvers.

        Returns:
            Self for method chaining.

        Example:
            schema.subscription(on_message_sub)
        """
        self._subscriptions.append(type_class)
        return self

    def build(self) -> GraphQL:
        """
        Build and return a configured GraphQL execution engine.

        Processes all registered query, mutation, and subscription types,
        extracts their resolvers, and registers them on a new GraphQL
        instance.

        Returns:
            A fully configured GraphQL instance ready for execution.

        Example:
            gql = schema.build()
        """
        gql = GraphQL()

        for item in self._queries:
            if isinstance(item, Query):
                gql.add_query(item)
            elif callable(item) and not isinstance(item, type):
                gql.add_query(item)
            elif isinstance(item, type):
                # Class-based: register bound instance methods as queries.
                instance = item()
                for attr_name in dir(instance):
                    if attr_name.startswith("_"):
                        continue
                    attr = getattr(instance, attr_name)
                    if callable(attr):
                        gql.add_query(attr)

        for item in self._mutations:
            if isinstance(item, Mutation):
                gql.add_mutation(item)
            elif callable(item) and not isinstance(item, type):
                gql.add_mutation(item)
            elif isinstance(item, type):
                instance = item()
                for attr_name in dir(instance):
                    if attr_name.startswith("_"):
                        continue
                    attr = getattr(instance, attr_name)
                    if callable(attr):
                        gql.add_mutation(attr)

        for item in self._subscriptions:
            if isinstance(item, Subscription):
                gql.add_subscription(item)
            elif callable(item) and not isinstance(item, type):
                gql.add_subscription(item)
            elif isinstance(item, type):
                instance = item()
                for attr_name in dir(instance):
                    if attr_name.startswith("_"):
                        continue
                    attr = getattr(instance, attr_name)
                    if callable(attr):
                        gql.add_subscription(attr)

        return gql

    def __repr__(self) -> str:
        return (
            f"<Schema queries={len(self._queries)} "
            f"mutations={len(self._mutations)} "
            f"subscriptions={len(self._subscriptions)}>"
        )


# ---------------------------------------------------------------------------
# graphql-ws protocol (WebSocket subscriptions)
# ---------------------------------------------------------------------------

async def graphql_ws_session(ws, engine: GraphQL):
    """Serve the `graphql-ws <https://github.com/enisdenjo/graphql-ws>`_ protocol.

    Implements the server-side messages used for realtime subscriptions:

    - ``connection_init`` → ``connection_ack``
    - ``subscribe`` → ``next`` payloads, then ``complete``
    - ``complete`` cancels a running subscription
    - ``ping`` → ``pong``
    - ``connection_terminate`` closes the session

    ``engine`` must be a :class:`GraphQL` instance with subscription resolvers
    registered (see :meth:`GraphQL.add_subscription`).
    """
    import asyncio

    subscriptions = {}
    while True:
        raw = await ws.receive_text()
        if raw is None:
            break
        try:
            message = json.loads(raw)
        except (TypeError, ValueError) as exc:
            await _graphql_ws_send(ws, {"type": "error", "payload": {"message": f"Invalid JSON: {exc}"}})
            continue
        if not isinstance(message, dict):
            continue
        msg_type = message.get("type")
        op_id = message.get("id")
        if msg_type == "connection_init":
            await _graphql_ws_send(ws, {"type": "connection_ack"})
        elif msg_type == "ping":
            await _graphql_ws_send(ws, {"type": "pong"})
        elif msg_type == "pong":
            pass
        elif msg_type == "connection_terminate":
            break
        elif msg_type == "subscribe":
            payload = message.get("payload") or {}
            query = payload.get("query", "")
            if not isinstance(query, str) or not query.strip():
                await _graphql_ws_send(
                    ws, {"type": "error", "id": op_id, "payload": {"message": "query is required"}}
                )
                continue
            variables = payload.get("variables")
            if variables is not None and not isinstance(variables, dict):
                await _graphql_ws_send(
                    ws,
                    {"type": "error", "id": op_id, "payload": {"message": "variables must be an object"}},
                )
                continue
            subscriptions[op_id] = asyncio.create_task(
                _graphql_ws_stream(
                    ws,
                    engine,
                    op_id,
                    query,
                    variables,
                    payload.get("operationName"),
                )
            )
            # Yield so the new subscription task can start executing before
            # the next incoming frame is processed (otherwise a fast
            # connection_terminate would cancel it before its first tick).
            await asyncio.sleep(0)
        elif msg_type == "complete":
            task = subscriptions.pop(op_id, None)
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        else:
            await _graphql_ws_send(
                ws,
                {"type": "error", "id": op_id, "payload": {"message": f"Unsupported message type: {msg_type}"}},
            )

    pending = list(subscriptions.values())
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    try:
        ws.close()
    except Exception:
        pass


async def _graphql_ws_stream(ws, engine: GraphQL, op_id, query, variables, operation_name):
    """Drive one subscription operation and emit ``next`` / ``complete`` frames."""
    import asyncio

    try:
        async for payload in engine.subscribe(
            query, variables=variables, operation_name=operation_name
        ):
            # Yield control to the event loop every iteration. Async-generator
            # resolvers can yield without any real await, which would otherwise
            # turn this loop into a synchronous busy-loop that never suspends:
            # session frames (complete / connection_terminate) could not be
            # processed and task.cancel() could never be delivered.
            await asyncio.sleep(0)
            await _graphql_ws_send(ws, {"type": "next", "id": op_id, "payload": payload})
        await _graphql_ws_send(ws, {"type": "complete", "id": op_id})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        try:
            await _graphql_ws_send(
                ws, {"type": "error", "id": op_id, "payload": {"message": str(exc)}}
            )
        except Exception:
            pass


async def _graphql_ws_send(ws, message: dict):
    """Send a graphql-ws message, tolerating a closed connection.

    ``send_json`` is synchronous (it queues onto the outbound channel), so no
    await is needed; the writer task flushes it to the socket.
    """
    try:
        ws.send_json(message)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_return_type(func: Callable) -> Optional[str]:
    """
    Extract the return type annotation from a function as a string.

    Args:
        func: The function to inspect.

    Returns:
        String representation of the return type, or None if not annotated.
    """
    try:
        hints = inspect.signature(func).return_annotation
        if hints is inspect.Parameter.empty:
            return None
        if isinstance(hints, type):
            return hints.__name__
        return str(hints)
    except (ValueError, TypeError):
        return None


def _extract_parameters(func: Callable) -> Dict[str, str]:
    """
    Extract parameter names and their type annotations from a function.

    Skips the first parameter (conventionally ``info`` in GraphQL resolvers)
    and any parameters named ``self`` or ``cls``.

    Args:
        func: The function to inspect.

    Returns:
        Dictionary mapping parameter names to their type annotation strings.
    """
    params: Dict[str, str] = {}
    try:
        sig = inspect.signature(func)
        skip_first = True
        for name, param in sig.parameters.items():
            if name in ("self", "cls"):
                continue
            # Skip the first non-self parameter (info)
            if skip_first:
                skip_first = False
                continue
            if param.annotation is not inspect.Parameter.empty:
                if isinstance(param.annotation, type):
                    params[name] = param.annotation.__name__
                else:
                    params[name] = str(param.annotation)
            else:
                params[name] = "Any"
    except (ValueError, TypeError):
        pass
    return params
