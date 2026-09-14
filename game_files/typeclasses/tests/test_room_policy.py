"""INTERACT-02A room-policy, admission, combat, and round-trip coverage."""

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.areas import assign_area, build_area_data, load_area_data
from systems.attacks import can_attack
from systems.combat import is_fighting, start_fight
from systems.room_policy import (
    ROOM_POLICY_ATTRIBUTE,
    AdmissionMode,
    RoomPolicyError,
    admission_decision,
    combat_decision,
    remote_view_decision,
    room_policy,
    set_room_policy_value,
    validate_room_policy,
)
from typeclasses.characters import Character
from typeclasses.rooms import Room


class TestRoomPolicy(EvenniaTest):
    """Every consumer reads the same closed, validated policy record."""

    def setUp(self):
        super().setUp()
        self.addCleanup(self.room1.attributes.remove, ROOM_POLICY_ATTRIBUTE)
        self.addCleanup(self.room2.attributes.remove, ROOM_POLICY_ATTRIBUTE)
        self.char1.db.is_player_character = True
        self.char2.db.is_player_character = True
        self.npc = create_object(Character, key="Mobile", location=self.room1)
        self.npc.db.is_player_character = False

    def test_defaults_closed_shape_and_malformed_values(self):
        policy = room_policy(self.room2)
        self.assertFalse(policy.no_combat)
        self.assertIsNone(policy.effective_capacity)
        with self.assertRaises(RoomPolicyError):
            validate_room_policy({"unknown": True})
        self.room2.db.room_policy = {"no_combat": 1}
        self.assertFalse(combat_decision(self.room2).allowed)
        self.assertTrue(admission_decision(self.char1, self.room2).allowed)
        for malformed in ({"no_mobiles": "yes"}, {"occupant_capacity": -1}):
            self.room2.db.room_policy = malformed
            self.assertFalse(admission_decision(self.char1, self.room2).allowed)
            self.assertTrue(combat_decision(self.room2).allowed)
        self.room2.db.room_policy = {"private": None}
        self.assertFalse(admission_decision(self.char1, self.room2).allowed)
        self.assertFalse(remote_view_decision(self.room2).allowed)
        self.assertTrue(combat_decision(self.room2).allowed)

    def test_capacity_counts_pcs_and_npcs_at_exact_boundary(self):
        set_room_policy_value(self.room2, "occupant_capacity", 2)
        self.assertTrue(
            self.char1.move_to(self.room2, quiet=True, move_type="traverse")
        )
        self.assertTrue(self.npc.move_to(self.room2, quiet=True, move_type="traverse"))
        self.assertFalse(
            self.char2.move_to(self.room2, quiet=True, move_type="traverse")
        )
        self.assertEqual(len(self.room2.contents_get(content_type="character")), 2)

    def test_private_defaults_to_two_and_suppresses_remote_view(self):
        set_room_policy_value(self.room2, "private", True)
        self.assertEqual(room_policy(self.room2).effective_capacity, 2)
        self.assertFalse(remote_view_decision(self.room2).allowed)
        set_room_policy_value(self.room2, "occupant_capacity", 1)
        self.assertEqual(room_policy(self.room2).effective_capacity, 1)

    def test_mobile_denial_and_named_bypass_modes(self):
        set_room_policy_value(self.room2, "no_mobiles", True)
        for mode in (AdmissionMode.MOBILE, AdmissionMode.FOLLOW):
            self.assertFalse(
                admission_decision(self.npc, self.room2, mode=mode).allowed
            )
        self.assertTrue(
            admission_decision(self.char1, self.room2, mode="normal").allowed
        )
        self.assertTrue(
            admission_decision(self.char1, self.room2, mode="recall").allowed
        )
        for mode in (
            AdmissionMode.FORCED,
            AdmissionMode.SPAWN,
            AdmissionMode.RESPAWN,
            AdmissionMode.BUILDER,
        ):
            result = admission_decision(self.npc, self.room2, mode=mode)
            self.assertTrue(result.allowed)
            self.assertTrue(result.bypassed)

    def test_no_combat_denies_hostility_and_forced_arrival_ends_fight(self):
        combat_room = create_object(Room, key="Combat policy room")
        safe_room = create_object(Room, key="Safe policy room")
        self.assertTrue(self.char1.move_to(combat_room, quiet=True, move_type="forced"))
        self.assertTrue(self.char2.move_to(combat_room, quiet=True, move_type="forced"))
        set_room_policy_value(combat_room, "no_combat", True)
        self.assertEqual(can_attack(self.char1, self.char2).reason, "no_combat")
        self.assertFalse(start_fight(self.char1, self.char2).accepted)
        set_room_policy_value(combat_room, "no_combat", False)
        self.assertTrue(start_fight(self.char1, self.char2).accepted)
        set_room_policy_value(safe_room, "no_combat", True)
        self.assertTrue(self.char1.move_to(safe_room, quiet=True, move_type="forced"))
        self.assertFalse(is_fighting(self.char1))
        self.assertFalse(is_fighting(self.char2))

    def test_policy_export_load_is_deterministic_and_reload_safe(self):
        assign_area(self.room1, "policy_source")
        set_room_policy_value(self.room1, "private", True)
        set_room_policy_value(self.room1, "occupant_capacity", 1)
        rooms, exits = build_area_data("policy_source")
        loaded = load_area_data("policy_copy", rooms, exits)["room"]
        self.assertEqual(room_policy(loaded), room_policy(self.room1))
        self.assertEqual(build_area_data("policy_source"), (rooms, exits))
        self.assertTrue(self.char1.move_to(loaded, quiet=True, move_type="forced"))
        self.assertFalse(admission_decision(self.char2, loaded).allowed)
