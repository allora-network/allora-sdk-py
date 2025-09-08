import asyncio
from typing import Set


class Context:
    """Go-like context for coordinating shutdown across the worker."""

    def __init__(self):
        self._cancelled = False
        self._cancel_event = asyncio.Event()
        self._cleanup_tasks: Set[asyncio.Task] = set()

    def is_cancelled(self) -> bool:
        """Check if the context has been cancelled."""
        return self._cancelled

    async def wait_for_cancellation(self):
        """Wait until the context is cancelled."""
        await self._cancel_event.wait()

    def cancel(self):
        """Cancel the context, triggering shutdown."""
        if not self._cancelled:
            self._cancelled = True
            self._cancel_event.set()

    def add_cleanup_task(self, task: asyncio.Task):
        """Register a task for cleanup on cancellation."""
        self._cleanup_tasks.add(task)

    async def cleanup(self):
        """Cancel all registered cleanup tasks."""
        for task in self._cleanup_tasks:
            if not task.done():
                task.cancel()

        if self._cleanup_tasks:
            await asyncio.gather(*self._cleanup_tasks, return_exceptions=True)
