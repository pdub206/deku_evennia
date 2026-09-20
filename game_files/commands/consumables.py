"""ITEM-04A commands for finite food and liquid sources."""

from __future__ import annotations

from typing import Any

from commands.command import Command
from systems.consumables import ConsumableError, drink, eat, pour, taste
from systems.item_resources import ItemResourceError


def _carried(actor: Any, name: str) -> Any | None:
    matches = actor.search(
        name.strip(), candidates=actor.contents, quiet=True, use_locks=False
    )
    return matches[0] if len(matches) == 1 else None


def _source(actor: Any, name: str) -> Any | None:
    candidates = list(actor.contents) + list(getattr(actor.location, "contents", []))
    matches = actor.search(
        name.strip(), candidates=candidates, quiet=True, use_locks=False
    )
    return matches[0] if len(matches) == 1 else None


def _announce(actor: Any, private: str, public: str) -> None:
    """Deliver one actor and room message after the service has committed."""
    actor.msg(private)
    if actor.location is not None:
        actor.location.msg_contents(public, exclude=actor)


class CmdEat(Command):
    """Eat one portion of carried food. Usage: eat <food>"""

    key = "eat"
    help_category = "General"

    def func(self) -> None:
        item = _carried(self.caller, self.args)
        if item is None:
            self.msg("Eat which carried food?")
            return
        try:
            if eat(self.caller, item):
                _announce(
                    self.caller,
                    f"You eat {item.get_display_name(self.caller)}.",
                    f"{self.caller.get_display_name(self.caller.location)} eats {item.get_display_name(self.caller.location)}.",
                )
        except (ConsumableError, ItemResourceError) as err:
            self.msg(str(err))


class _DrinkCommand(Command):
    help_category = "General"

    def func(self) -> None:
        source = _source(self.caller, self.args)
        if source is None:
            self.msg("Name a drink source here or in your inventory.")
            return
        try:
            if drink(self.caller, source):
                _announce(
                    self.caller,
                    f"You drink from {source.get_display_name(self.caller)}.",
                    f"{self.caller.get_display_name(self.caller.location)} drinks from {source.get_display_name(self.caller.location)}.",
                )
        except (ConsumableError, ItemResourceError) as err:
            self.msg(str(err))


class CmdDrink(_DrinkCommand):
    """Drink one serving. Usage: drink <source>"""

    key = "drink"


class CmdSip(_DrinkCommand):
    """Sip one serving. Usage: sip <source>"""

    key = "sip"


class CmdTaste(Command):
    """Taste a liquid without consuming it. Usage: taste <source>"""

    key = "taste"
    help_category = "General"

    def func(self) -> None:
        source = _source(self.caller, self.args)
        if source is None:
            self.msg("Name a drink source here or in your inventory.")
            return
        try:
            self.msg(taste(self.caller, source))
        except (ConsumableError, ItemResourceError) as err:
            self.msg(str(err))


class CmdPour(Command):
    """Pour liquid into a carried container or out. Usage: pour <source> into <container|out>"""

    key = "pour"
    help_category = "General"

    def func(self) -> None:
        source_name, marker, target_name = self.args.partition(" into ")
        if not marker or not source_name.strip() or not target_name.strip():
            self.msg("Usage: pour <source> into <drink container|out>.")
            return
        source = _carried(self.caller, source_name)
        target = (
            None
            if target_name.strip().casefold() == "out"
            else _carried(self.caller, target_name)
        )
        if source is None or (
            target is None and target_name.strip().casefold() != "out"
        ):
            self.msg("You must directly carry the source and target.")
            return
        try:
            units = pour(self.caller, source, target)
            _announce(
                self.caller,
                (
                    "You pour it out."
                    if target is None
                    else f"You pour {units} serving(s)."
                ),
                f"{self.caller.get_display_name(self.caller.location)} pours liquid.",
            )
        except (ConsumableError, ItemResourceError) as err:
            self.msg(str(err))
