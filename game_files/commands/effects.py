"""Staff inspection command for persistent character effects."""

from commands.command import Command
from systems.effects import ActiveEffect, EffectStorageError


def _effect_lines(effect: ActiveEffect) -> list[str]:
    """Render one active effect as compact, auditable staff output."""
    duration = (
        "permanent"
        if effect.remaining_pulses is None
        else f"{effect.remaining_pulses} pulse(s) remaining"
    )
    source = effect.source_name
    if effect.source_dbref:
        source += f" ({effect.source_dbref})"
    if effect.source_key:
        source += f" via {effect.source_key}"
    conditions = ", ".join(sorted(effect.conditions)) or "none"
    modifiers = (
        ", ".join(
            f"{name} {value:+d}" for name, value in sorted(effect.modifiers.items())
        )
        or "none"
    )
    removal = ", ".join(sorted(effect.removal_categories)) or "none"
    if effect.save:
        save = (
            f"{effect.save.ability} DC {effect.save.dc}, "
            f"{effect.save.timing.value} -> {effect.save.success.value}"
        )
    else:
        save = "none"
    orphaned = " |r[definition missing]|n" if effect.definition is None else ""
    return [
        f"  |w{effect.name}|n [{effect.key}] {effect.instance_id}{orphaned}",
        f"    stacks: {effect.stacks}; duration: {duration}",
        f"    source: {source}; conditions: {conditions}",
        f"    modifiers: {modifiers}",
        f"    save: {save}; removable by: {removal}",
    ]


class CmdEffects(Command):
    """
    Inspect persistent effects on a character.

    Usage:
      @effects
      @effects <character or #dbref>

    Shows each effect's stable key and instance ID, stacks, remaining duration,
    source, condition flags, numeric modifiers, saving throw, and ordinary
    removal categories. This command is restricted to staff with Builder
    permission or higher and does not modify effects.
    """

    key = "@effects"
    aliases = ["@conditions"]
    locks = "cmd:perm(Builder)"
    help_category = "Staff"

    def func(self) -> None:
        caller = self.caller
        argument = self.args.strip()
        target = caller.search(argument, global_search=True) if argument else caller
        if target is None:
            return
        if not hasattr(target, "effects"):
            caller.msg(f"{target.key} cannot have character effects.")
            return
        try:
            effects = target.effects.all()
        except EffectStorageError:
            caller.msg(
                f"{target.key}'s effect data is invalid; inspect its "
                "active_effects Attribute directly."
            )
            return
        if not effects:
            caller.msg(f"{target.key} has no active effects.")
            return

        lines = [f"|wActive effects on {target.key} ({target.dbref}):|n"]
        for effect in effects:
            lines.extend(_effect_lines(effect))
        caller.msg("\n".join(lines))
