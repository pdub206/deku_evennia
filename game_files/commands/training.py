"""Player commands for ADV-03's class-choice training loop."""

from __future__ import annotations

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.training import (
    TrainingError,
    find_trainer,
    practice_view,
    replace_training_option,
    resolve_training,
)


class CmdPractice(Command):
    """Review learned class options and choices awaiting training.

    Usage:
      practice

    This is read-only and works anywhere. It shows automatic class gains,
    known skill proficiencies, class resources, spell-access limits, and any
    selections you still need to make through an eligible trainer.
    """

    key = "practice"
    aliases = ["practise"]
    help_category = "Character"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        if self.args.strip():
            self.caller.msg("Usage: practice")
            return
        try:
            view = practice_view(self.caller)
        except TrainingError as err:
            self.caller.msg(str(err))
            return
        lines = ["|wPractice|n"]
        lines.append(
            "Known proficiencies: "
            + (
                ", ".join(view.known_proficiencies)
                if view.known_proficiencies
                else "None"
            )
        )
        lines.append(
            "Automatic class features: "
            + (
                ", ".join(view.automatic_features)
                if view.automatic_features
                else "None"
            )
        )
        lines.append(
            "Class resources: "
            + (
                ", ".join(f"{key} ({maximum})" for key, maximum in view.resources)
                if view.resources
                else "None"
            )
        )
        lines.append(
            "Spell access: "
            + (
                ", ".join(
                    f"{key} (cantrips {cantrips}, known {known}, max spell level {maximum})"
                    for key, cantrips, known, maximum in view.spell_access
                )
                if view.spell_access
                else "None"
            )
        )
        if view.pending_choices:
            lines.append("Pending choices:")
            for item in view.pending_choices:
                lines.append(
                    f"  {item['choice_key']}: choose {item['count'] - len(item['selected'])} from "
                    f"{', '.join(_options(item['choice_key']))}"
                )
        else:
            lines.append("Pending choices: None")
        self.caller.msg("\n".join(lines))


class CmdTrain(Command):
    """Resolve one pending class choice through a nearby trainer.

    Usage:
      train
      train <choice> <option>
      train <choice> <option> at <trainer>
      train <choice> replace <old option> with <new option> [at <trainer>]

    With no arguments, this is a read-only shortcut for ``practice``. Training
    a choice requires one qualified nearby NPC; XP levels and automatic class
    gains never require a trainer.
    """

    key = "train"
    help_category = "Character"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        raw = self.args.strip()
        if not raw:
            try:
                view = practice_view(self.caller)
            except TrainingError as err:
                self.caller.msg(str(err))
                return
            self.caller.msg(_practice_summary(view))
            return
        parsed = _parse_training(raw)
        if parsed is None:
            self.caller.msg("Usage: train <choice> <option> [at <trainer>]")
            return
        decision = self.caller.actions.check(ActionCategory.MANIPULATE)
        if not decision.allowed:
            self.caller.msg(decision.message)
            return
        choice, old_option, option, trainer_name = parsed
        try:
            trainer = find_trainer(self.caller, trainer_name)
            if old_option is None:
                result = resolve_training(self.caller, choice, option, trainer)
            else:
                result = replace_training_option(
                    self.caller, choice, old_option, option, trainer
                )
        except TrainingError as err:
            self.caller.msg(str(err))
            return
        if result.applied:
            verb = "replace with" if result.reason == "replaced" else "train"
            self.caller.msg(f"You {verb} |w{result.option}|n through {trainer.key}.")


def _parse_training(raw: str) -> tuple[str, str | None, str, str | None] | None:
    """Parse positional training without treating ``=`` as command syntax."""
    if "=" in raw:
        return None
    body, marker, trainer = raw.rpartition(" at ")
    if not marker:
        body, trainer = raw, None
    choice, separator, remainder = body.partition(" ")
    if not separator:
        return None
    old_option = None
    if remainder.startswith("replace "):
        old_option, replace_marker, option = remainder.removeprefix(
            "replace "
        ).partition(" with ")
        if not replace_marker:
            return None
    else:
        option = remainder
    if (
        not choice
        or not option.strip()
        or (old_option is not None and not old_option.strip())
        or (marker and not trainer.strip())
    ):
        return None
    return (
        choice,
        old_option.strip() if old_option else None,
        option.strip(),
        trainer.strip() if trainer else None,
    )


def _options(choice_key: str) -> tuple[str, ...]:
    """Read legal options from ADV-02 rather than duplicating command data."""
    from systems.progression import CLASS_PROGRESSION

    return CLASS_PROGRESSION.choices[choice_key].legal_options


def _practice_summary(view) -> str:
    """Keep ``train`` without arguments a concise read-only listing."""
    if not view.pending_choices:
        return "No training choices are pending. Use |wpractice|n for your full record."
    entries = ", ".join(item["choice_key"] for item in view.pending_choices)
    return f"Pending training choices: {entries}. Use |wpractice|n for details."
