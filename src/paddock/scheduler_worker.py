"""Site-scoped Laravel scheduler managed by the user's systemd instance."""

from __future__ import annotations

from .queue_worker import QueueWorkerError, QueueWorkerManager


class SchedulerWorkerError(QueueWorkerError):
    pass


class SchedulerWorkerManager(QueueWorkerManager):
    key = "scheduler"
    display_name = "Scheduler"
    command = ("schedule:work", "--no-interaction")
    error_type = SchedulerWorkerError
