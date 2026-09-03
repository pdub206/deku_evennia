"""
Characters

Characters are (by default) Objects setup to be puppeted by Accounts.
They are what you "see" in game. The Character class in this module
is setup to be the "default" character type created by the default
creation commands.

"""

import time
from collections.abc import Iterable, Mapping
from typing import Any

from evennia.objects.objects import DefaultCharacter
from systems.character_stats import CharacterStats
from systems.effects import EffectHandler
from systems.equipment import WEAR_LOCATIONS, EquipmentHandler

from .objects import ObjectParent


class Character(ObjectParent, DefaultCharacter):
    """
    The Character just re-implements some of the Object's methods and hooks
    to represent a Character entity in-game.

    See mygame/typeclasses/objects.py for a list of
    properties and methods available on all Object child classes like this.

    """

    def at_object_creation(self) -> None:
        super().at_object_creation()
        self.db.position = "standing"

    @property
    def stats(self) -> CharacterStats:
        """Return the canonical, on-demand interface to character statistics."""
        return CharacterStats(self)

    @property
    def equipment(self) -> EquipmentHandler:
        """Return the canonical, on-demand interface to equipped items."""
        return EquipmentHandler(self)

    @property
    def effects(self) -> EffectHandler:
        """Return the canonical interface to persistent active effects."""
        return EffectHandler(self)

    def get_effect_stat_modifier_sources(self) -> Iterable[Mapping[str, int]]:
        """Yield numeric modifiers supplied by persistent active effects."""
        return self.effects.modifier_sources()

    def get_stat_modifier_sources(self) -> Iterable[Mapping[str, int]]:
        """Yield intrinsic, equipped, and active-effect stat contributions."""
        intrinsic = self.db.stat_modifiers
        if isinstance(intrinsic, Mapping):
            yield intrinsic

        yield from self.equipment.stat_modifier_sources()
        yield from self.get_effect_stat_modifier_sources()

    def at_msg_receive(self, text=None, from_obj=None, **kwargs) -> bool:
        # Sleeping characters cannot perceive messages from other objects.
        if (
            (self.db.position or "standing") == "sleeping"
            and from_obj is not None
            and from_obj is not self
        ):
            return False
        return True

    def at_pre_move(self, destination, **kwargs) -> bool:
        if (self.db.position or "standing") == "sleeping":
            self.msg("You are asleep and cannot move. Type |wwake|n to wake up.")
            return False
        # TODO(WORLD-02): Schedule traversal using stats.movement_delay() so
        # movement cannot bypass pursuit simply by submitting commands rapidly.
        return True

    def get_display_things(self, looker: Any, **kwargs: Any) -> str:
        """Show worn equipment, without exposing the rest of the inventory."""
        equipped = self.filter_visible(
            [item for item in self.contents if item.db.worn_location], looker, **kwargs
        )
        if not equipped:
            return ""

        location_order = {
            location: index for index, location in enumerate(WEAR_LOCATIONS)
        }
        equipped.sort(
            key=lambda item: (
                location_order.get(item.db.worn_location, len(location_order)),
                item.key.lower(),
            )
        )
        lines = [
            f"  |C{item.get_display_name(looker, **kwargs)}|n ({item.db.worn_location})"
            for item in equipped
        ]
        return "|wEquipped:|n\n" + "\n".join(lines)

    def at_post_puppet(self, **kwargs) -> None:
        super().at_post_puppet(**kwargs)
        # Record the moment this session began so we can accumulate IC time.
        self.db.session_login_time = time.time()

    def at_post_unpuppet(self, account, session=None, **kwargs) -> None:
        # Accumulate elapsed IC time before releasing the character.
        login_time = self.db.session_login_time
        if login_time:
            elapsed = time.time() - login_time
            self.db.time_played = (self.db.time_played or 0.0) + elapsed
        self.db.session_login_time = None
        super().at_post_unpuppet(account, session=session, **kwargs)
