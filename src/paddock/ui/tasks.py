"""Small asynchronous boundary between GTK and blocking controller work."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import threading
from typing import Callable, TypeVar


Result = TypeVar("Result")
Dispatcher = Callable[[Callable[[], None]], object]


class AsyncOperationRunner:
    """Run blocking work away from the UI and dispatch only current results.

    Each logical key has a generation. Submitting a newer refresh makes an
    older result stale even if the older subprocess finishes last. Mutating
    operations are serialized separately by the controller's file lock.
    """

    def __init__(self, dispatcher: Dispatcher, *, workers: int = 2):
        self.dispatcher = dispatcher
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="paddock-ui")
        self._lock = threading.Lock()
        self._generations: dict[str, int] = {}
        self._closed = False

    def submit(
        self,
        key: str,
        operation: Callable[[], Result],
        on_success: Callable[[Result], None],
        on_error: Callable[[BaseException], None],
    ) -> Future[Result]:
        with self._lock:
            if self._closed:
                raise RuntimeError("operation runner is closed")
            generation = self._generations.get(key, 0) + 1
            self._generations[key] = generation
        future = self.executor.submit(operation)
        future.add_done_callback(
            lambda completed: self._completed(
                key, generation, completed, on_success, on_error
            )
        )
        return future

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._generations[key] = self._generations.get(key, 0) + 1

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generations.clear()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _completed(
        self,
        key: str,
        generation: int,
        future: Future[Result],
        on_success: Callable[[Result], None],
        on_error: Callable[[BaseException], None],
    ) -> None:
        def deliver() -> None:
            with self._lock:
                current = not self._closed and self._generations.get(key) == generation
            if not current:
                return
            try:
                result = future.result()
            except BaseException as error:
                on_error(error)
            else:
                on_success(result)

        self.dispatcher(deliver)

