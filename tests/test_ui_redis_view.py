from __future__ import annotations

import unittest

from paddock.application import RedisSnapshot
from paddock.ui.redis_view import present_redis


def snapshot(*, configured: bool = True, state: str = "active") -> RedisSnapshot:
    return RedisSnapshot(
        configured=configured,
        image="docker.io/library/redis:8" if configured else None,
        host="127.0.0.1",
        port=6379 if configured else None,
        container_port=6379,
        volume="paddock-redis" if configured else None,
        unit="paddock-service-redis.service",
        active_state=state,
        enabled_state="enabled" if configured else "not-configured",
        lingering=True,
        connection=("REDIS_HOST=127.0.0.1", "REDIS_PORT=6379") if configured else (),
    )


class RedisPresentationTests(unittest.TestCase):
    def test_unconfigured_and_unavailable_are_distinct(self) -> None:
        absent = present_redis(snapshot(configured=False, state="not-configured"))
        unknown = present_redis(snapshot(configured=False, state="unknown"))
        self.assertEqual("unconfigured", absent.view)
        self.assertEqual("unknown", unknown.view)
        self.assertEqual("warning", unknown.tone)

    def test_running_state_has_text_icon_and_semantic_tone(self) -> None:
        result = present_redis(snapshot(state="active"))
        self.assertEqual("Running", result.title)
        self.assertIn("127.0.0.1:6379", result.description)
        self.assertEqual("emblem-ok-symbolic", result.icon_name)
        self.assertEqual("success", result.tone)
        self.assertTrue(result.can_stop)
        self.assertTrue(result.can_restart)

    def test_stopped_state_can_start(self) -> None:
        result = present_redis(snapshot(state="inactive"))
        self.assertEqual("Stopped", result.title)
        self.assertTrue(result.can_start)
        self.assertFalse(result.can_stop)

    def test_transitional_states_disable_conflicting_actions(self) -> None:
        for state in ("activating", "reloading", "deactivating"):
            with self.subTest(state=state):
                result = present_redis(snapshot(state=state))
                self.assertTrue(result.transitioning)
                self.assertFalse(result.can_start)
                self.assertFalse(result.can_stop)
                self.assertFalse(result.can_restart)

    def test_failed_state_exposes_retry_paths(self) -> None:
        result = present_redis(snapshot(state="failed"))
        self.assertEqual("error", result.tone)
        self.assertTrue(result.can_start)
        self.assertTrue(result.can_restart)

    def test_unknown_systemd_state_does_not_offer_mutations(self) -> None:
        result = present_redis(snapshot(state="unknown"))
        self.assertEqual("warning", result.tone)
        self.assertFalse(result.can_start)
        self.assertFalse(result.can_stop)


if __name__ == "__main__":
    unittest.main()

