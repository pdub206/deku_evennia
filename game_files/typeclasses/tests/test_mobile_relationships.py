"""MOB-07 regression coverage for primitive pet and following relationships."""

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.effects import EFFECT_REGISTRY, EffectDefinition
from systems.lifecycle import UnavailabilityCause, mark_character_unavailable
from systems.mobile_relationships import (
    MOBILE_RELATIONSHIP_ATTRIBUTE,
    acquire_pet,
    advance_follow,
    bind_charm_effect,
    charm,
    dismiss,
    effective_controller,
    order,
    relationship_state,
    set_following,
)
from typeclasses.characters import Character
from typeclasses.exits import Exit
from typeclasses.rooms import Room


class TestMobileRelationships(EvenniaTest):
    """Ownership, charm precedence, order safety, and movement share one API."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.char2.db.is_player_character = True
        self.npc = create_object(Character, key="Hound", location=self.room1)
        self.npc.db.is_player_character = False
        self.npc.locks.add("pet:all();charm:all();transfer:all();order:all()")

    def test_acquire_is_primitive_idempotent_and_capacity_limited(self):
        self.assertTrue(acquire_pet(self.char1, self.npc).accepted)
        self.assertEqual(acquire_pet(self.char1, self.npc).reason, "already_owned")
        raw = self.npc.attributes.get(MOBILE_RELATIONSHIP_ATTRIBUTE)
        self.assertEqual(raw["owner_id"], self.char1.id)
        self.assertEqual(raw["charm"], None)
        other = create_object(Character, key="Other pet", location=self.room1)
        other.db.is_player_character = False
        other.locks.add("pet:all()")
        self.assertEqual(acquire_pet(self.char1, other).reason, "capacity")

    def test_charm_precedes_owner_without_rewriting_durable_ownership(self):
        acquire_pet(self.char1, self.npc)
        self.assertTrue(charm(self.npc, self.char2, source_id=17).accepted)
        self.assertEqual(relationship_state(self.npc)["owner_id"], self.char1.id)
        self.assertIs(effective_controller(self.npc), self.char2)

    def test_effect_removal_releases_only_the_bound_charm(self):
        """Charm expiry/dispel uses the shared effect lifecycle exactly once."""
        if EFFECT_REGISTRY.get("mob07_charm_test") is None:
            EFFECT_REGISTRY.register(
                EffectDefinition(key="mob07_charm_test", name="Test charm")
            )
        effect = self.npc.effects.add("mob07_charm_test", quiet=True).effect

        self.assertTrue(bind_charm_effect(self.npc, self.char2, effect).accepted)
        self.assertIs(effective_controller(self.npc), self.char2)
        self.npc.effects.remove(effect.instance_id, quiet=True)

        self.assertIsNone(relationship_state(self.npc)["charm"])

    def test_controlled_follow_uses_one_normal_navigation_step(self):
        room3 = create_object(Room, key="Third room")
        create_object(Exit, key="north", location=self.room1, destination=self.room2)
        create_object(Exit, key="north", location=self.room2, destination=room3)
        acquire_pet(self.char1, self.npc)
        self.assertTrue(set_following(self.char1, self.npc).accepted)
        self.char1.move_to(room3, quiet=True, move_type="teleport")

        first = advance_follow(self.npc, 1)
        self.assertEqual(first.status, "acted")
        self.assertEqual(self.npc.location, self.room2)
        second = advance_follow(self.npc, 2)
        self.assertEqual(second.status, "acted")
        self.assertEqual(self.npc.location, room3)

    def test_order_uses_only_registered_actions_and_rejects_raw_commands(self):
        acquire_pet(self.char1, self.npc)
        self.assertEqual(
            order(self.char2, self.npc, "follow").reason,
            "not_controlled_here",
        )
        self.assertEqual(
            order(self.char1, self.npc, "get all corpse").reason,
            "invalid_order",
        )
        self.assertTrue(order(self.char1, self.npc, "follow").accepted)

    def test_dismiss_is_idempotent_and_never_extracts_the_pet(self):
        acquire_pet(self.char1, self.npc)
        self.assertTrue(dismiss(self.char1, self.npc).accepted)
        self.assertEqual(self.npc.location, self.room1)
        self.assertEqual(dismiss(self.char1, self.npc).reason, "already_dismissed")

    def test_owner_disconnect_ends_following_but_preserves_ownership(self):
        acquire_pet(self.char1, self.npc)
        set_following(self.char1, self.npc)

        mark_character_unavailable(
            self.char1, UnavailabilityCause.DISCONNECT, has_controlling_sessions=False
        )

        state = relationship_state(self.npc)
        self.assertEqual(state["owner_id"], self.char1.id)
        self.assertIsNone(state["follow"])
