"""Player and staff read-only ADV-05 advancement commands."""

from __future__ import annotations

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.advancement_info import advancement_info, display_name


class CmdLevels(Command):
    """Show your class's complete released level progression.

    Usage:
      levels
    """

    key = "levels"
    aliases = ("level progression",)
    help_category = "Character"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        """Render only validated levels 1 through the alpha cap."""
        if self.args.strip():
            self.caller.msg("Usage: levels")
            return
        info = advancement_info(self.caller)
        if not info.valid or info.state is None or info.practice is None:
            self.caller.msg(info.reason)
            return
        lines = [
            f"|w{info.state.class_key} Levels 1–3|n",
            "The current alpha level cap is 3.",
        ]
        pending: dict[int, list[str]] = {}
        for item in info.practice.pending_choices:
            pending.setdefault(item["level"], []).append(
                display_name(item["choice_key"])
            )
        resolved: dict[int, list[str]] = {}
        for item in info.practice.resolved_choices:
            resolved.setdefault(item["level"], []).append(
                f"{display_name(item['choice_key'])}: {', '.join(item['selected'])}"
            )
        for level in info.levels:
            marker = " |g(current)|n" if level.level == info.state.level else ""
            lines.append(f"|yLevel {level.level}|n — {level.xp_threshold} XP{marker}")
            lines.append(
                "  Automatic: " + (", ".join(level.automatic_features) or "None")
            )
            if level.prerequisites:
                lines.append(
                    "  Prerequisites: "
                    + "; ".join(
                        f"{feature} requires {', '.join(required)}"
                        for feature, required in level.prerequisites
                    )
                )
            lines.append("  Choices: " + (", ".join(level.choices) or "None"))
            if pending.get(level.level):
                lines.append("  Pending training: " + ", ".join(pending[level.level]))
            if resolved.get(level.level):
                lines.append("  Trained: " + "; ".join(resolved[level.level]))
            if level.resources:
                lines.append(
                    "  Resources: "
                    + ", ".join(
                        f"{name} {maximum}" for name, maximum in level.resources
                    )
                )
            for name, cantrips, known, prepared, spellbook, slots in level.spell_access:
                slot_text = ", ".join(
                    f"level {slot_level} slots {count}"
                    for slot_level, count in enumerate(slots, 1)
                    if count
                )
                details = f"cantrips {cantrips}, known {known}, prepared {prepared}"
                if spellbook:
                    details += f", spellbook {spellbook}"
                lines.append(f"  {name}: {details}; {slot_text or 'no spell slots'}")
        self.caller.msg("\n".join(lines))


class CmdAdvancement(Command):
    """Inspect validated advancement identity without repairing it.

    Usage:
      @advancement <character or #dbref>
    """

    key = "@advancement"
    aliases = ("@adv",)
    locks = "cmd:perm(Builder)"
    help_category = "Staff"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        """Show bounded staff-only schema and provenance diagnostics."""
        if not self.args.strip():
            self.caller.msg("Usage: @advancement <character or #dbref>")
            return
        target = self.caller.search(self.args.strip(), global_search=True)
        if target is None:
            return
        info = advancement_info(target)
        if not info.valid or info.state is None or info.practice is None:
            self.caller.msg(
                "Advancement information needs staff review "
                f"({info.diagnostic or 'invalid_advancement_state'})."
            )
            return
        state = info.state
        self.caller.msg(
            "\n".join(
                (
                    f"|wAdvancement: {target.key}|n",
                    f"Class/level/XP: {state.class_key} {state.level}, {state.xp} XP",
                    f"Ledger schema: {state.ledger_version}",
                    f"Progression registry: {state.registry_version} ({state.registry_fingerprint[:12]})",
                    f"Last award identity: {state.last_award_source or 'None'}",
                    f"Automatic grants: {len(state.grants)}",
                    "Grant provenance: " + (", ".join(state.grants) or "None"),
                    f"Pending/resolved choices: {len(info.practice.pending_choices)}/{len(info.practice.resolved_choices)}",
                    "Inspection is read-only; ADV-06 owns future repair.",
                )
            )
        )
