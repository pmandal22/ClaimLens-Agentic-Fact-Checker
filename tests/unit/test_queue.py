import time

import fakeredis
import pytest

from claimlens.services.queue import RedisStreamQueue


@pytest.fixture
def client():
    return fakeredis.FakeRedis(decode_responses=True)


def make_queue(client, consumer: str = "worker-1", reclaim_after_ms: int = 60_000):
    return RedisStreamQueue(client, consumer=consumer, reclaim_after_ms=reclaim_after_ms)


def test_empty_queue_returns_none(client):
    assert make_queue(client).receive(block_ms=1) is None


def test_messages_arrive_in_order_with_job_id(client):
    queue = make_queue(client)
    queue.enqueue("job-a")
    queue.enqueue("job-b")

    first = queue.receive(block_ms=1)
    second = queue.receive(block_ms=1)

    assert first and first.job_id == "job-a" and first.redelivered is False
    assert second and second.job_id == "job-b"
    assert queue.receive(block_ms=1) is None


def test_each_message_goes_to_only_one_worker(client):
    worker_1 = make_queue(client, consumer="worker-1")
    worker_2 = make_queue(client, consumer="worker-2")
    worker_1.enqueue("job-a")

    got_1 = worker_1.receive(block_ms=1)
    got_2 = worker_2.receive(block_ms=1)

    assert got_1 is not None
    assert got_2 is None


def test_acknowledged_message_is_not_redelivered(client):
    queue = make_queue(client, reclaim_after_ms=0)
    queue.enqueue("job-a")
    message = queue.receive(block_ms=1)
    assert message is not None

    queue.ack(message)

    assert queue.receive(block_ms=1) is None
    assert client.xlen("claimlens:jobs") == 0


def test_unacknowledged_message_is_redelivered_to_another_worker(client):
    crashed = make_queue(client, consumer="worker-1", reclaim_after_ms=0)
    crashed.enqueue("job-a")
    assert crashed.receive(block_ms=1) is not None  # received, never acknowledged

    healthy = make_queue(client, consumer="worker-2", reclaim_after_ms=0)
    retry = healthy.receive(block_ms=1)

    assert retry and retry.job_id == "job-a" and retry.redelivered is True


def test_message_is_not_reclaimed_before_the_idle_timeout(client):
    first_worker = make_queue(client, consumer="worker-1", reclaim_after_ms=60_000)
    first_worker.enqueue("job-a")
    assert first_worker.receive(block_ms=1) is not None

    other_worker = make_queue(client, consumer="worker-2", reclaim_after_ms=60_000)

    assert other_worker.receive(block_ms=1) is None


def test_creating_a_queue_twice_does_not_fail(client):
    make_queue(client)
    make_queue(client, consumer="worker-2")


def test_touch_stops_a_slow_worker_losing_its_message(client):
    slow = make_queue(client, consumer="worker-1", reclaim_after_ms=50)
    slow.enqueue("job-a")
    message = slow.receive(block_ms=1)
    assert message is not None
    time.sleep(0.1)  # now idle long enough to be reclaimed

    assert slow.touch(message) is True

    other = make_queue(client, consumer="worker-2", reclaim_after_ms=50)
    assert other.receive(block_ms=1) is None


def test_touch_reports_a_message_taken_by_another_worker(client):
    first = make_queue(client, consumer="worker-1", reclaim_after_ms=0)
    first.enqueue("job-a")
    message = first.receive(block_ms=1)
    assert message is not None
    assert make_queue(client, consumer="worker-2", reclaim_after_ms=0).receive(block_ms=1)

    assert first.touch(message) is False
    pending = client.xpending_range("claimlens:jobs", "workers", min=message.id, max=message.id, count=1)
    assert pending[0]["consumer"] == "worker-2"  # touch must not steal it back


def test_touch_after_ack_returns_false(client):
    queue = make_queue(client)
    queue.enqueue("job-a")
    message = queue.receive(block_ms=1)
    assert message is not None
    queue.ack(message)

    assert queue.touch(message) is False


def test_prune_removes_only_idle_consumers_with_nothing_pending(client):
    gone = make_queue(client, consumer="gone")
    busy = make_queue(client, consumer="busy")
    gone.receive(block_ms=1)  # registers the consumer, receives nothing
    busy.enqueue("job-a")
    assert busy.receive(block_ms=1) is not None  # still pending: must survive
    me = make_queue(client, consumer="me")
    me.receive(block_ms=1)

    removed = me.prune_consumers(idle_ms=0)

    assert removed == ["gone"]
    names = {c["name"] for c in client.xinfo_consumers("claimlens:jobs", "workers")}
    assert names == {"busy", "me"}
