from __future__ import annotations

import unittest

from paddock.application import Change, RedisApplyPlan
from paddock.ui.redis_form import (
    describe_apply_failure,
    describe_lifecycle_failure,
    describe_plan,
    describe_remove_failure,
)


class RedisPlanPresentationTests(unittest.TestCase):
    def plan(self, *, effect: str, changes: tuple[Change, ...]) -> RedisApplyPlan:
        return RedisApplyPlan(True, (), changes, effect, True, ())

    def test_add_preview_names_initial_values_and_safety(self) -> None:
        lines = describe_plan(
            self.plan(
                effect="configure",
                changes=(
                    Change("image", None, "docker.io/library/redis:8"),
                    Change("port", None, 6379),
                ),
            )
        )
        self.assertIn("Container image: Not configured → docker.io/library/redis:8", lines)
        self.assertIn("Host port: Not configured → 6379", lines)
        self.assertTrue(any("enabled, started" in line for line in lines))
        self.assertTrue(any("volume will be preserved" in line for line in lines))

    def test_live_change_explains_restart(self) -> None:
        lines = describe_plan(
            self.plan(effect="restart", changes=(Change("port", 6379, 6380),))
        )
        self.assertIn("Host port: 6379 → 6380", lines)
        self.assertTrue(any("restart" in line for line in lines))

    def test_inactive_change_explains_next_start(self) -> None:
        lines = describe_plan(
            self.plan(effect="next-start", changes=(Change("port", 6379, 6380),))
        )
        self.assertTrue(any("remain stopped" in line for line in lines))

    def test_failure_codes_distinguish_collision_and_rollback_outcomes(self) -> None:
        collision = describe_apply_failure("port_in_use", "Port 6380 is occupied")
        restored = describe_apply_failure("apply_failed_rolled_back", "start failed")
        broken = describe_apply_failure(
            "apply_failed_rollback_failed", "start and rollback failed"
        )
        self.assertIn("another loopback host port", collision)
        self.assertIn("restored the previous configuration", restored)
        self.assertIn("rollback both failed", broken)
        self.assertIn("Paddock Doctor", broken)

    def test_lifecycle_timeout_points_to_logs(self) -> None:
        message = describe_lifecycle_failure("readiness_timeout", "probe timed out")
        self.assertIn("did not become ready", message)
        self.assertIn("Open its logs", message)
        self.assertIn("probe timed out", message)

    def test_volume_failure_names_the_preserved_volume(self) -> None:
        message = describe_remove_failure(
            "volume_delete_failed", "volume busy", "paddock-redis"
        )
        self.assertIn("paddock-redis", message)
        self.assertIn("still exists", message)


if __name__ == "__main__":
    unittest.main()
