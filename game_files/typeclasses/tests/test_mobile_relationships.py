"""MOB-07 regression coverage for primitive pet and following relationships."""

from unittest.mock import patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.lifecycle import UnavailabilityCause, mark_character_unavailable
from systems.mobile_relationships import (MOBILE_RELATIONSHIP_ATTRIBUTE,
                                          acquire_pet, advance_follow, charm,
                                          dismiss, effective_controller, order,
                                          relationship_state, set_following)
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

    def test_acquire_is_primitive_idempotent_and_capacity_limited(self):
        self.assertTrue(acquire_pet(self.char1, self.npc).accepted)
        self.assertEqual(acquire_pet(self.char1, self.npc).reason, "already_owned")
        raw = self.npc.attributes.get(MOBILE_RELATIONSHIP_ATTRIBUTE)
        self.assertEqual(raw["owner_id"], self.char1.id)
        self.assertEqual(raw["charm"], None)
        other = create_object(Character, key="Other pet", location=self.room1)
        other.db.is_player_character = False
        self.assertEqual(acquire_pet(self.char1, other).reason, "capacity")

    def test_charm_precedes_owner_without_rewriting_durable_ownership(self):
        acquire_pet(self.char1, self.npc)
        self.assertTrue(charm(self.npc, self.char2, source_id=17).accepted)
        self.assertEqual(relationship_state(self.npc)["owner_id"], self.char1.id)
        self.assertIs(effective_controller(self.npc), self.char2)

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

    def test_order_forwards_normal_commands_and_rejects_nested_orders(self):
        acquire_pet(self.char1, self.npc)
        self.assertEqual(
            order(self.char2, self.npc, "get all corpse").reason,
            "not_controlled_here",
        )
        with patch.object(self.npc, "execute_cmd") as execute_cmd:
            result = order(self.char1, self.npc, "get all corpse")
        self.assertEqual(result.status, "acted")
        execute_cmd.assert_called_once_with("get all corpse")
        self.assertEqual(
            order(self.char1, self.npc, "order hound look").reason, "nested_order"
        )

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
