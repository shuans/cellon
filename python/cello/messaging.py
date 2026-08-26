"""Async message queue adapters for Redis Streams and RabbitMQ AMQP.

Redis and RabbitMQ use their real network clients. Kafka and SQS keep the
existing deterministic in-process adapter until dedicated clients are added.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from collections import defaultdict, deque
from functools import wraps
from typing import Any, Callable


def _is_async(func: Callable) -> bool:
    return inspect.iscoroutinefunction(func)


def kafka_consumer(topic: str, group: str = None, auto_commit: bool = True) -> Callable:
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result

        wrapper._cello_consumer = True
        wrapper._cello_consumer_topic = topic
        wrapper._cello_consumer_group = group
        wrapper._cello_consumer_auto_commit = auto_commit
        return wrapper

    return decorator


def kafka_producer(topic: str) -> Callable:
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            result = await result if inspect.isawaitable(result) else result
            if result is not None:
                wrapper._cello_last_publish = {"topic": topic, "value": result}
            return result

        wrapper._cello_producer = True
        wrapper._cello_producer_topic = topic
        return wrapper

    return decorator


class KafkaConfig:
    def __init__(
        self,
        brokers: list[str] | str = None,
        group_id: str = None,
        client_id: str = None,
        auto_commit: bool = True,
        session_timeout_ms: int = 30000,
        max_poll_records: int = 500,
    ):
        if brokers is None:
            brokers = ["localhost:9092"]
        if isinstance(brokers, str):
            brokers = [item.strip() for item in brokers.split(",") if item.strip()]
        if not brokers:
            raise ValueError("brokers must contain at least one address")
        self.brokers = list(brokers)
        self.group_id = group_id
        self.client_id = client_id
        self.auto_commit = auto_commit
        self.session_timeout_ms = session_timeout_ms
        self.max_poll_records = max_poll_records

    @classmethod
    def local(cls) -> "KafkaConfig":
        return cls(["localhost:9092"], group_id="cello-local", client_id="cello-dev")


class RabbitMQConfig:
    def __init__(
        self,
        url: str = "amqp://guest:guest@localhost:5672/",
        vhost: str = "/",
        prefetch_count: int = 10,
        heartbeat: int = 60,
    ):
        self.url = url
        self.vhost = vhost
        self.prefetch_count = prefetch_count
        self.heartbeat = heartbeat

    @classmethod
    def local(cls) -> "RabbitMQConfig":
        return cls()


class SqsConfig:
    def __init__(
        self,
        region: str = "us-east-1",
        queue_url: str = "",
        endpoint_url: str = None,
        max_messages: int = 10,
        wait_time_secs: int = 20,
    ):
        self.region = region
        self.queue_url = queue_url
        self.endpoint_url = endpoint_url
        self.max_messages = max_messages
        self.wait_time_secs = wait_time_secs

    @classmethod
    def local(cls, queue_url: str) -> "SqsConfig":
        return cls(region="us-east-1", queue_url=queue_url, endpoint_url="http://localhost:4566", wait_time_secs=5)


class Message:
    MAX_JSON_SIZE = 10 * 1024 * 1024

    def __init__(
        self,
        id: str = None,
        topic: str = "",
        key: str = None,
        value: Any = None,
        headers: dict = None,
        timestamp: float = None,
    ):
        self.id = id or str(uuid.uuid4())
        self.topic = topic
        self.key = key
        self.value = value
        self.headers = headers or {}
        self.timestamp = time.time() if timestamp is None else timestamp
        self._acked = False
        self._nacked = False
        self._ack_callback = None
        self._nack_callback = None

    @property
    def text(self) -> str:
        if self.value is None:
            return ""
        if isinstance(self.value, bytes):
            return self.value.decode("utf-8")
        return str(self.value)

    def json(self) -> Any:
        raw = self.value
        if isinstance(raw, (dict, list)):
            return raw
        if isinstance(raw, bytes):
            if len(raw) > self.MAX_JSON_SIZE:
                raise ValueError("Message payload exceeds maximum allowed size")
            raw = raw.decode("utf-8")
        elif not isinstance(raw, str):
            raw = str(raw)
        if len(raw.encode("utf-8")) > self.MAX_JSON_SIZE:
            raise ValueError("Message payload exceeds maximum allowed size")
        parsed = json.loads(raw)
        if not isinstance(parsed, (dict, list)):
            raise ValueError(f"Expected JSON object or array, got {type(parsed).__name__}")
        return parsed

    def ack(self) -> None:
        self._acked = True

    def nack(self) -> None:
        self._nacked = True


class MessageResult:
    ACK = "ack"
    NACK = "nack"
    REJECT = "reject"
    REQUEUE = "requeue"
    DEAD_LETTER = "dead_letter"


class _InMemoryBroker:
    def __init__(self):
        self._queues = defaultdict(deque)
        self._condition = asyncio.Condition()

    async def publish(self, message: Message) -> None:
        async with self._condition:
            self._queues[message.topic].append(message)
            self._condition.notify_all()

    async def poll(self, topics: list[str], timeout: float) -> list[Message]:
        if not topics:
            return []
        deadline = asyncio.get_running_loop().time() + max(timeout, 0)
        async with self._condition:
            while not any(self._queues[topic] for topic in topics):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return []
                try:
                    await asyncio.wait_for(self._condition.wait(), remaining)
                except asyncio.TimeoutError:
                    return []
            messages = []
            for topic in topics:
                queue = self._queues[topic]
                while queue:
                    messages.append(queue.popleft())
            return messages


_BROKERS = defaultdict(_InMemoryBroker)


def _config_kind(config) -> str:
    name = type(config).__name__.lower()
    url = str(getattr(config, "url", "")).lower()
    if name in {"redisconfig", "redismessagingconfig"} or url.startswith(("redis://", "rediss://")):
        return "redis"
    if isinstance(config, RabbitMQConfig) or name in {"rabbitmqconfig", "rabbitconfig"} or url.startswith(("amqp://", "amqps://")):
        return "rabbitmq"
    if isinstance(config, KafkaConfig) or hasattr(config, "brokers"):
        return "kafka"
    if isinstance(config, SqsConfig) or hasattr(config, "queue_url"):
        return "sqs"
    raise TypeError("config must be KafkaConfig, RabbitMQConfig, SqsConfig, or a Redis config")


def _broker_key(config) -> str:
    kind = _config_kind(config)
    if kind == "kafka":
        brokers = getattr(config, "brokers", [])
        if isinstance(brokers, str):
            brokers = [item.strip() for item in brokers.split(",") if item.strip()]
        return "kafka:" + ",".join(brokers)
    if kind == "rabbitmq":
        return "rabbitmq:" + str(getattr(config, "url", ""))
    if kind == "redis":
        return "redis:" + str(getattr(config, "url", ""))
    return "sqs:" + str(getattr(config, "endpoint_url", None) or "aws") + ":" + str(getattr(config, "queue_url", ""))


def _encode_value(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _decode_headers(value: Any) -> dict:
    if value in (None, b"", ""):
        return {}
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _field(fields: dict, name: str, default=None):
    return fields.get(name, fields.get(name.encode("utf-8"), default))


class Producer:
    def __init__(self, config):
        self._config = config
        self._kind = _config_kind(config)
        self._connected = False
        self._broker = _BROKERS[_broker_key(config)] if self._kind in {"kafka", "sqs"} else None
        self._client = None
        self._channel = None

    @classmethod
    async def connect(cls, config) -> "Producer":
        instance = cls(config)
        if instance._kind == "redis":
            try:
                import redis.asyncio as redis
            except ImportError as exc:
                raise RuntimeError("Redis messaging requires the 'redis' package") from exc
            instance._client = redis.from_url(str(config.url), decode_responses=False)
            await instance._client.ping()
        elif instance._kind == "rabbitmq":
            try:
                import aio_pika
            except ImportError as exc:
                raise RuntimeError("RabbitMQ messaging requires the 'aio-pika' package") from exc
            instance._client = await aio_pika.connect_robust(
                str(config.url), heartbeat=int(getattr(config, "heartbeat", 60))
            )
            instance._channel = await instance._client.channel()
        instance._connected = True
        return instance

    async def send(self, topic: str, value: Any, key: str = None, headers: dict = None) -> bool:
        if not self._connected:
            raise RuntimeError("Producer is closed")
        if not topic:
            raise ValueError("topic must not be empty")
        if self._kind == "redis":
            await self._client.xadd(topic, {
                "value": _encode_value(value),
                "key": (key or "").encode("utf-8"),
                "headers": json.dumps(headers or {}, separators=(",", ":")).encode("utf-8"),
            })
        elif self._kind == "rabbitmq":
            import aio_pika
            await self._channel.declare_queue(topic, durable=True)
            await self._channel.default_exchange.publish(
                aio_pika.Message(
                    body=_encode_value(value),
                    message_id=str(uuid.uuid4()),
                    headers=headers or {},
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=topic,
            )
        else:
            await self._broker.publish(Message(topic=topic, key=key, value=value, headers=headers))
        return True

    async def send_batch(self, messages: list[dict]) -> int:
        if not self._connected:
            raise RuntimeError("Producer is closed")
        for message in messages:
            if not isinstance(message, dict) or not message.get("topic"):
                raise ValueError("each message requires a non-empty topic")
            await self.send(message["topic"], message.get("value"), message.get("key"), message.get("headers"))
        return len(messages)

    async def close(self) -> None:
        self._connected = False
        if self._kind == "redis" and self._client is not None:
            await self._client.aclose()
        elif self._kind == "rabbitmq" and self._client is not None:
            await self._client.close()
        self._client = None
        self._channel = None


class Consumer:
    def __init__(self, config):
        self._config = config
        self._kind = _config_kind(config)
        self._connected = False
        self._broker = _BROKERS[_broker_key(config)] if self._kind in {"kafka", "sqs"} else None
        self._client = None
        self._channel = None
        self._subscriptions: list[str] = []
        self._queues: dict[str, Any] = {}
        self._consumer_name = str(getattr(config, "consumer_name", None) or f"cello-{uuid.uuid4().hex[:8]}")
        self._group_id = str(getattr(config, "group_id", None) or "cello-consumers")

    @classmethod
    async def connect(cls, config) -> "Consumer":
        instance = cls(config)
        if instance._kind == "redis":
            try:
                import redis.asyncio as redis
            except ImportError as exc:
                raise RuntimeError("Redis messaging requires the 'redis' package") from exc
            instance._client = redis.from_url(str(config.url), decode_responses=False)
            await instance._client.ping()
        elif instance._kind == "rabbitmq":
            try:
                import aio_pika
            except ImportError as exc:
                raise RuntimeError("RabbitMQ messaging requires the 'aio-pika' package") from exc
            instance._client = await aio_pika.connect_robust(
                str(config.url), heartbeat=int(getattr(config, "heartbeat", 60))
            )
            instance._channel = await instance._client.channel()
        instance._connected = True
        return instance

    async def subscribe(self, topics: list[str]) -> None:
        if not self._connected:
            raise RuntimeError("Consumer is closed")
        if not topics or any(not isinstance(topic, str) or not topic for topic in topics):
            raise ValueError("topics must contain at least one non-empty topic")
        self._subscriptions = list(dict.fromkeys(topics))
        if self._kind == "redis":
            for topic in self._subscriptions:
                try:
                    await self._client.xgroup_create(topic, self._group_id, id="0-0", mkstream=True)
                except Exception as exc:
                    if "BUSYGROUP" not in str(exc):
                        raise
        elif self._kind == "rabbitmq":
            await self._channel.set_qos(prefetch_count=int(getattr(self._config, "prefetch_count", 10)))
            for topic in self._subscriptions:
                self._queues[topic] = await self._channel.declare_queue(topic, durable=True)

    async def poll(self, timeout_ms: int = 1000) -> list[Message]:
        if not self._connected:
            raise RuntimeError("Consumer is closed")
        if timeout_ms < 0:
            raise ValueError("timeout_ms must be non-negative")
        if not self._subscriptions:
            return []
        if self._kind == "redis":
            result = await self._client.xreadgroup(
                self._group_id,
                self._consumer_name,
                {topic: ">" for topic in self._subscriptions},
                count=int(getattr(self._config, "max_messages", 10)),
                block=timeout_ms,
            )
            messages = []
            for topic, entries in result or []:
                topic = topic.decode() if isinstance(topic, bytes) else topic
                for message_id, fields in entries:
                    message_id = message_id.decode() if isinstance(message_id, bytes) else message_id
                    key = _field(fields, "key", b"")
                    value = _field(fields, "value", b"")
                    headers = _field(fields, "headers")
                    message = Message(
                        id=message_id,
                        topic=topic,
                        key=key.decode() if isinstance(key, bytes) and key else key or None,
                        value=value,
                        headers=_decode_headers(headers),
                    )
                    message._ack_callback = lambda msg=message: self._client.xack(
                        msg.topic, self._group_id, msg.id
                    )

                    async def redis_nack(requeue=False, msg=message):
                        # A requeued Redis Stream entry remains pending for
                        # redelivery; a permanent rejection acknowledges it
                        # so it leaves the consumer group's PEL.
                        if not requeue:
                            await self._client.xack(msg.topic, self._group_id, msg.id)

                    message._nack_callback = redis_nack
                    messages.append(message)
            return messages
        if self._kind == "rabbitmq":
            messages = []
            timeout = timeout_ms / 1000
            for topic in self._subscriptions:
                try:
                    incoming = await self._queues[topic].get(fail=False, timeout=timeout)
                except asyncio.TimeoutError:
                    continue
                if incoming is None:
                    continue
                message = Message(
                    id=incoming.message_id or str(uuid.uuid4()),
                    topic=topic,
                    value=incoming.body,
                    headers=dict(incoming.headers or {}),
                )
                message._ack_callback = incoming.ack
                message._nack_callback = incoming.nack
                messages.append(message)
            return messages
        return await self._broker.poll(self._subscriptions, timeout_ms / 1000)

    async def commit(self, message: Message = None) -> None:
        if not self._connected:
            raise RuntimeError("Consumer is closed")
        if message is None:
            return
        if not isinstance(message, Message):
            raise TypeError("message must be a Message")
        callback = message._ack_callback
        if callback is not None:
            result = callback()
            if inspect.isawaitable(result):
                await result
        message.ack()

    async def reject(self, message: Message, requeue: bool = False) -> None:
        if not self._connected:
            raise RuntimeError("Consumer is closed")
        if not isinstance(message, Message):
            raise TypeError("message must be a Message")
        callback = message._nack_callback
        if callback is not None:
            result = callback(requeue=requeue)
            if inspect.isawaitable(result):
                await result
        message.nack()

    async def close(self) -> None:
        self._connected = False
        self._subscriptions = []
        self._queues = {}
        if self._kind == "redis" and self._client is not None:
            await self._client.aclose()
        elif self._kind == "rabbitmq" and self._client is not None:
            await self._client.close()
        self._client = None
        self._channel = None
