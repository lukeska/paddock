from __future__ import annotations

import queue
import threading
import unittest

from paddock.ui.tasks import AsyncOperationRunner


class Dispatcher:
    def __init__(self):
        self.callbacks: queue.Queue = queue.Queue()

    def __call__(self, callback):
        self.callbacks.put(callback)

    def deliver(self, timeout: float = 1) -> None:
        self.callbacks.get(timeout=timeout)()


class AsyncOperationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher = Dispatcher()
        self.runner = AsyncOperationRunner(self.dispatcher, workers=2)

    def tearDown(self) -> None:
        self.runner.close()

    def test_work_runs_off_the_calling_thread_and_dispatches_success(self) -> None:
        calling_thread = threading.get_ident()
        results = []
        self.runner.submit(
            "refresh",
            threading.get_ident,
            results.append,
            self.fail,
        )
        self.dispatcher.deliver()
        self.assertEqual(1, len(results))
        self.assertNotEqual(calling_thread, results[0])

    def test_errors_are_dispatched_instead_of_escaping_a_worker(self) -> None:
        errors = []

        def fail():
            raise RuntimeError("boom")

        self.runner.submit("refresh", fail, self.fail, errors.append)
        self.dispatcher.deliver()
        self.assertEqual("boom", str(errors[0]))

    def test_a_new_generation_discards_an_older_result(self) -> None:
        release = threading.Event()
        results = []

        self.runner.submit(
            "refresh",
            lambda: (release.wait(1), "old")[1],
            results.append,
            self.fail,
        )
        self.runner.submit("refresh", lambda: "new", results.append, self.fail)
        self.dispatcher.deliver()
        release.set()
        self.dispatcher.deliver()
        self.assertEqual(["new"], results)

    def test_closed_runner_rejects_new_work(self) -> None:
        self.runner.close()
        with self.assertRaises(RuntimeError):
            self.runner.submit("refresh", lambda: None, lambda _: None, self.fail)


if __name__ == "__main__":
    unittest.main()

