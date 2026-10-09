"""Job queue: the API puts job ids in, workers take them out. Redis Streams in production."""

import os
import socket
from dataclasses import dataclass
from typing import Any, Protocol

from claimlens.config.settings import get_settings

JOB_ID_FIELD = "job_id"


@dataclass(frozen=True)
class Message:
    id: str  # receipt used to acknowledge the message
    job_id: str
    redelivered: bool  # True when a previous worker received it but never acknowledged it


class JobQueue(Protocol):
    def enqueue(self, job_id: str) -> None: ...

    def receive(self, block_ms: int) -> Message | None: ...

    def ack(self, message: Message) -> None: ...


class RedisStreamQueue:
    """A Redis Stream read through a consumer group.

    Each message goes to exactly one worker. It stays "pending" until that worker calls
    ack(); if the worker dies first, the message is handed to another worker once it has
    been idle for reclaim_after_ms. This gives at-least-once delivery, so processing must
    be safe to repeat.
    """

    def __init__(
        self,
        client: Any,
        stream: str = "claimlens:jobs",
        group: str = "workers",
        consumer: str | None = None,
        reclaim_after_ms: int = 15 * 60 * 1000,
    ) -> None:
        # The client must be created with decode_responses=True so values are str, not bytes.
        self._client = client
        self._stream = stream
        self._group = group
        self._consumer = consumer or f"{socket.gethostname()}-{os.getpid()}"
        self._reclaim_after_ms = reclaim_after_ms
        self._ensure_group()

    def _ensure_group(self) -> None:
        from redis.exceptions import ResponseError

        try:
            # mkstream creates the stream if it doesn't exist yet; "0" means read from the start.
            self._client.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):  # the group already exists: fine
                raise

    def enqueue(self, job_id: str) -> None:
        self._client.xadd(self._stream, {JOB_ID_FIELD: job_id})

    def receive(self, block_ms: int) -> Message | None:
        """Return the next message, waiting up to block_ms, or None if there is none."""
        stale = self._client.xautoclaim(
            self._stream,
            self._group,
            self._consumer,
            min_idle_time=self._reclaim_after_ms,
            start_id="0-0",
            count=1,
        )
        # Redis 7 returns (next_id, claimed, deleted); older versions omit the last item.
        claimed = stale[1]
        if claimed:
            return self._to_message(claimed[0], redelivered=True)

        # Block 0 would mean "wait forever" in Redis, so never pass less than 1.
        response = self._client.xreadgroup(
            self._group, self._consumer, {self._stream: ">"}, count=1, block=max(block_ms, 1)
        )
        if not response:
            return None
        _stream, entries = response[0]
        return self._to_message(entries[0], redelivered=False)

    def ack(self, message: Message) -> None:
        self._client.xack(self._stream, self._group, message.id)
        # Acknowledged messages are no longer needed; deleting stops the stream growing forever.
        self._client.xdel(self._stream, message.id)

    @staticmethod
    def _to_message(entry: tuple[str, dict[str, str]], redelivered: bool) -> Message:
        message_id, fields = entry
        return Message(id=message_id, job_id=fields[JOB_ID_FIELD], redelivered=redelivered)


def get_queue() -> JobQueue:
    import redis

    settings = get_settings()
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return RedisStreamQueue(client, reclaim_after_ms=settings.queue_reclaim_after_s * 1000)
