"""Builder/Admin command surface for ADV-06 progression repair."""

from __future__ import annotations

from typing import Any

from commands.command import MuxCommand
from systems.action_policy import ActionCategory
from systems.advancement_repair import (AdvancementRepairError,
                                        RepairDiagnosis, RepairPlan,
                                        apply_progression_repair,
                                        diagnose_progression,
                                        plan_progression_repair)


class CmdAdvancementRepair(MuxCommand):
    """Inspect, plan, or apply one scoped ADV-06 progression correction.

    Usage:
      @advancement [<character or #dbref>]
      @advancement/plan <character or #dbref>
      @advancement/apply <character or #dbref>=<plan id>;<reason>[;<ticket>]

    Builders may inspect and create a fresh plan. Applying any repair requires
    Admin permission, the full current plan identity, and a bounded reason.
    The command never accepts Attribute names, executable text, or free-form
    mutation values.
    """

    key = "@advancement"
    aliases = ("@advance", "@progression")
    locks = "cmd:perm(Builder)"
    help_category = "Staff"
    action_category = ActionCategory.STATE_INDEPENDENT
    switch_options = ("plan", "apply")

    def func(self) -> None:
        """Route one permissioned inspect, plan, or apply request."""
        if any(switch not in self.switch_options for switch in self.switches):
            self.caller.msg(self._usage())
            return
        if "apply" in self.switches:
            self._apply()
            return
        if "plan" in self.switches:
            target = self._target(self.args.strip(), required=True)
            if target is not None:
                self.caller.msg(_render_plan(target, plan_progression_repair(target)))
            return
        target = self._target(self.args.strip(), required=False)
        if target is not None:
            self.caller.msg(_render_diagnosis(target, diagnose_progression(target)))

    def _apply(self) -> None:
        """Revalidate the supplied plan identity before one Admin-only apply."""
        if not _is_admin(self.caller):
            self.caller.msg("Only Admin staff may apply advancement repairs.")
            return
        target = self._target(self.lhs.strip(), required=True)
        if target is None:
            return
        parts = [part.strip() for part in self.rhs.split(";", 2)]
        if len(parts) < 2 or not _plan_id(parts[0]) or not parts[1]:
            self.caller.msg(self._usage())
            return
        plan_id, reason = parts[0], parts[1]
        source_ticket = parts[2] if len(parts) == 3 else ""
        plan = plan_progression_repair(target)
        if plan.plan_id != plan_id:
            self.caller.msg(
                "That plan is no longer current; run @advancement/plan again."
            )
            return
        try:
            result = apply_progression_repair(
                target,
                plan,
                actor=self.caller,
                reason=reason,
                source_ticket=source_ticket,
            )
        except AdvancementRepairError as err:
            self.caller.msg(f"Advancement repair was not applied: {err}")
            return
        self.caller.msg(
            f"Applied advancement plan {plan.plan_id} to {target.key} ({target.dbref}).\n"
            + _render_diagnosis(target, result)
        )

    def _target(self, argument: str, *, required: bool) -> Any | None:
        """Resolve an explicit saved character without exposing storage details."""
        if not argument:
            if required:
                self.caller.msg(self._usage())
                return None
            return self.caller
        target = self.caller.search(argument, global_search=True)
        if target is None:
            return None
        if not hasattr(target, "stats"):
            self.caller.msg("That object has no character advancement state.")
            return None
        return target

    @staticmethod
    def _usage() -> str:
        """Return one bounded, staff-facing syntax reminder."""
        return (
            "Usage: @advancement [<character or #dbref>] | "
            "@advancement/plan <character or #dbref> | "
            "@advancement/apply <character or #dbref>=<plan id>;<reason>[;<ticket>]"
        )


def _render_diagnosis(target: Any, diagnosis: RepairDiagnosis) -> str:
    """Render the bounded primitive diagnosis without raw Attribute contents."""
    issues = ", ".join(diagnosis.issues) if diagnosis.issues else "none"
    return (
        f"|wAdvancement repair for {target.key} ({target.dbref}):|n\n"
        f"class: {diagnosis.class_key or 'invalid'}; xp: {diagnosis.xp!r}; "
        f"level: {diagnosis.level!r}\n"
        f"issues: {issues}"
    )


def _render_plan(target: Any, plan: RepairPlan) -> str:
    """Render a deterministic plan summary suitable for an Admin confirmation."""
    lines = [
        f"|wAdvancement plan for {target.key} ({target.dbref}):|n",
        f"id: {plan.plan_id}; version: {plan.version}; risk: {plan.risk}",
        f"issues: {', '.join(plan.issues) if plan.issues else 'none'}",
    ]
    if not plan.operations:
        lines.append("operations: none (staff review required)")
    for operation in plan.operations:
        lines.append(f"operation: {operation.key} ({operation.risk})")
        if operation.before_values:
            before = ", ".join(
                f"{key}={value}" for key, value in operation.before_values
            )
            after = ", ".join(f"{key}={value}" for key, value in operation.after_values)
            lines.append(f"values: {before} -> {after}")
    if plan.operations:
        lines.append(
            "Apply with: @advancement/apply "
            f"{target.dbref}={plan.plan_id};<reason>[;<ticket>]"
        )
    return "\n".join(lines)


def _is_admin(actor: Any) -> bool:
    """Match the project's Admin-or-superuser boundary for mutation commands."""
    return bool(
        getattr(actor, "is_superuser", False) or actor.check_permstring("Admin")
    )


def _plan_id(value: str) -> bool:
    """Accept only the fixed SHA-256 identity emitted by the planning service."""
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )
