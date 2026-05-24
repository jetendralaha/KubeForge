"""In-process async event bus using asyncio.

Supports publish/subscribe with typed events for decoupled communication
between parsers, analyzers, packager, and deployer.

For production, swap to NATS JetStream by implementing the same interface.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

logger = logging.getLogger("kubeforge.events")

# Type alias for async event handlers
Handler = Callable[["Event"], Coroutine[Any, Any, None]]


# ── Subjects ────────────────────────────────────────────────────────

ARTIFACT_UPLOADED = "artifact.uploaded"
ARTIFACT_PARSED = "artifact.parsed"
IMAGES_RESOLVED = "images.resolved"
ANALYSIS_COMPLETED = "analysis.completed"
MANIFESTS_GENERATED = "manifests.generated"
PACKAGE_STARTED = "package.started"
PACKAGE_COMPLETED = "package.completed"
DEPLOY_STARTED = "deploy.started"
DEPLOY_COMPLETED = "deploy.completed"
CHART_DEPLOYED = "chart.deployed"
DEPLOY_FAILED = "deploy.failed"


# ── Event envelope ──────────────────────────────────────────────────


@dataclass
class Event:
    subject: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Bus implementation ──────────────────────────────────────────────


class EventBus:
    """Async in-process event bus with fire-and-forget dispatch."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, subject: str, handler: Handler) -> None:
        """Register an async handler for a subject."""
        self._handlers[subject].append(handler)
        logger.debug(f"Subscribed handler to {subject}")

    async def publish(self, event: Event) -> None:
        """Dispatch an event to all subscribed handlers (async, non-blocking)."""
        handlers = self._handlers.get(event.subject, [])
        if not handlers:
            return

        logger.debug(f"Publishing {event.subject} to {len(handlers)} handler(s)")
        for handler in handlers:
            asyncio.create_task(_safe_call(handler, event))


async def _safe_call(handler: Handler, event: Event) -> None:
    """Call a handler with error isolation."""
    try:
        await handler(event)
    except Exception:
        logger.exception(f"Error in event handler for {event.subject}")


# ── Singleton ───────────────────────────────────────────────────────

bus = EventBus()
