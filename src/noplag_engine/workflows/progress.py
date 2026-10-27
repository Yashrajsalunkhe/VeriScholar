"""In-memory pub-sub for check-workflow progress events.

The check pipeline (`run_check`) calls a `progress_callback` at stage
boundaries; the API layer's SSE endpoint subscribes per-check and
streams events to clients. Storage is a `dict[check_id, list[Queue]]`
in module scope.

This is single-process only — fine while the FastAPI app and the
background worker run in the same process. A multi-process deployment
would need to replace this with an external pub-sub (e.g. Redis) so
events fan out across processes.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

_QUEUE_MAXSIZE = 256

_subscribers: dict[UUID, list[asyncio.Queue]] = defaultdict(list)


@dataclass(frozen=True)
class ProgressEvent:
    # In-flight stages emitted from inside run_check: chunking,
    # fingerprinting, retrieving, aligning, assembling, complete.
    # Additionally, the SSE endpoint synthesizes a single 'failed' event
    # for late subscribers that connect after the pipeline already
    # raised — clients shouldn't have to poll /v1/checks/{id} just to
    # learn the stream's outcome.
    stage: str
    chunks_done: int
    chunks_total: int


def open_subscription(check_id: UUID) -> asyncio.Queue:
    """Subscribe to events for `check_id`. Returns a fresh queue that
    receives every event published from this point forward. Callers must
    call `close_subscription` when done so the publisher doesn't keep
    feeding a dead consumer."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    _subscribers[check_id].append(queue)
    return queue


def close_subscription(check_id: UUID, queue: asyncio.Queue) -> None:
    subs = _subscribers.get(check_id)
    if subs is None:
        return
    if queue in subs:
        subs.remove(queue)
    if not subs:
        _subscribers.pop(check_id, None)


def publish(check_id: UUID, event: ProgressEvent) -> None:
    """Fan an event out to every subscription for `check_id`. Sync —
    safe to call from a sync `progress_callback` inside the pipeline.
    Drops the event for slow consumers rather than blocking the
    pipeline."""
    for queue in _subscribers.get(check_id, ()):
        # Slow consumer; drop. Progress is advisory, not critical data —
        # the final report persisted in check_results is the source of
        # truth.
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(event)


def close_publisher(check_id: UUID) -> None:
    """Signal end-of-stream to every subscriber by pushing `None`. The
    SSE generator interprets `None` as "no more events" and closes the
    response."""
    for queue in _subscribers.get(check_id, ()):
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(None)


def reset_for_tests() -> None:
    """Clear all subscriptions. Tests call this to isolate between cases."""
    _subscribers.clear()
