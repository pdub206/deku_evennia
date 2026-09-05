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
from systems.action_policy import ActionCategory, ActionPolicy, Position
from systems.character_stats import CharacterStats
from systems.effects import EffectHandler, EffectStorageError
from systems.encumbrance import character_load
from systems.equipment import WEAR_LOCATIONS, EquipmentHandler
from systems.lifecycle import (deliver_character_notices,
                               mark_character_available,
                               mark_character_unavailable,
                               resolve_unavailability_cause)

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

    @property
    def actions(self) -> ActionPolicy:
        """Return the canonical interface to position and action legality."""
        return ActionPolicy(self)

    @property
    def action_position(self) -> Position:
        """Return the most restrictive current position from every system."""
        return self.actions.position

    def get_imposed_action_positions(self) -> Iterable[Position]:
        """Yield non-posture positions imposed by effects and future systems.

        COMBAT-01 and COMBAT-04 extend this hook with fighting and vitality
        states. Keeping them here prevents either system from overwriting the
        character's persistent voluntary posture.
        """
        try:
            if self.effects.has_condition("stunned"):
                yield Position.STUNNED
        except EffectStorageError:
            # Invalid effect data must fail closed while leaving staff recovery
            # commands available through the state-independent action category.
            yield Position.INCAPACITATED

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
        """Suppress external messages when the action policy denies perception."""
        if (
            from_obj is not None
            and from_obj is not self
            and not self.actions.check(ActionCategory.OBSERVE).allowed
        ):
            return False
        return True

    def at_pre_move(self, destination, **kwargs) -> bool:
        """Apply the shared movement policy to voluntary traversal only."""
        if kwargs.get("move_type") == "traverse":
            decision = self.actions.check(ActionCategory.MOVE)
            if not decision.allowed:
                self.msg(decision.message)
                return False
            if character_load(self).overloaded:
                self.msg(
                    "You are too encumbered to move. Drop or give away some items."
                )
                return False
        # TODO(INTERACT-03): Apply terrain cost and stats.movement_delay() when
        # travel scheduling is introduced.
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

    def at_post_puppet(self, **kwargs: Any) -> None:
        super().at_post_puppet(**kwargs)
        if self.sessions.count() == 1:
            mark_character_available(self)
            deliver_character_notices(self)
        # Multiple sessions share one continuous IC interval.
        if self.db.session_login_time is None:
            self.db.session_login_time = time.time()

    def at_post_unpuppet(
        self,
        account: Any,
        session: Any | None = None,
        **kwargs: Any,
    ) -> None:
        """Run final-session cleanup before Evennia stows the character."""
        has_sessions = bool(self.sessions.count())
        cause = resolve_unavailability_cause(
            session,
            reason=kwargs.get("reason"),
            cold_shutdown=bool(self.ndb._world_cold_shutdown),
        )
        mark_character_unavailable(
            self,
            cause,
            has_controlling_sessions=has_sessions,
        )
        if not has_sessions:
            login_time = self.db.session_login_time
            if login_time:
                elapsed = time.time() - login_time
                self.db.time_played = (self.db.time_played or 0.0) + elapsed
            self.db.session_login_time = None
        super().at_post_unpuppet(account, session=session, **kwargs)

    def at_server_shutdown(self) -> None:
        """Distinguish server shutdown unpuppets from player departures."""
        self.ndb._world_cold_shutdown = True
        super().at_server_shutdown()
