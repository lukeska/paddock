"""Pure text presentation for the Redis configuration workflow."""

from __future__ import annotations

from paddock.application import RedisApplyPlan


FIELD_NAMES = {"image": "Container image", "port": "Host port"}


def describe_plan(plan: RedisApplyPlan) -> tuple[str, ...]:
    """Return concise, user-facing plan lines without GTK dependencies."""
    lines = []
    for change in plan.changes:
        name = FIELD_NAMES.get(change.field, change.field)
        before = "Not configured" if change.before is None else str(change.before)
        lines.append(f"{name}: {before} → {change.after}")

    effects = {
        "configure": "Redis will be configured, enabled, started, and checked for readiness.",
        "restart": "Redis will restart and be checked for readiness.",
        "next-start": "Redis will remain stopped; changes take effect on its next start.",
        "none": "No system or configuration changes are needed.",
    }
    lines.append(effects.get(plan.runtime_effect, plan.runtime_effect))
    if plan.preserves_volume:
        lines.append("The existing Redis data volume will be preserved.")
    return tuple(lines)


def describe_apply_failure(code: str, detail: str | None) -> str:
    """Explain stable controller failure codes in actionable UI language."""
    explanations = {
        "port_in_use": "Choose another loopback host port and try again.",
        "apply_failed_rolled_back": (
            "The change failed, and Paddock restored the previous configuration."
        ),
        "apply_failed_rollback_failed": (
            "The change and automatic rollback both failed. Run Paddock Doctor "
            "before retrying."
        ),
    }
    explanation = explanations.get(code, detail or code)
    if detail and detail not in explanation:
        return f"{explanation}\n\n{detail}"
    return explanation


def describe_lifecycle_failure(code: str, detail: str | None) -> str:
    if code == "readiness_timeout":
        explanation = (
            "Redis did not become ready before the startup timeout. "
            "Open its logs for the underlying error."
        )
        return f"{explanation}\n\n{detail}" if detail else explanation
    return detail or code


def describe_remove_failure(code: str, detail: str | None, volume: str) -> str:
    if code == "volume_delete_failed":
        explanation = f"Redis was removed, but data volume {volume} still exists."
        return f"{explanation}\n\n{detail}" if detail else explanation
    return detail or code
