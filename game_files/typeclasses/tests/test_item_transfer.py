"""ITEM-05A transfer policy, bound-item death retention, and item decay tests."""

from __future__ import annotations

from unittest.mock import patch

from commands.building import _apply_field
from commands.default_cmdsets import CharacterCmdSet
from commands.generic import CmdDrop, CmdGet, CmdGive, CmdJunk, CmdPut
from commands.item_policy import CmdItemPolicy
from evennia import create_object
from evennia.prototypes.prototypes import delete_prototype, search_prototype
from evennia.prototypes.spawner import spawn
from evennia.utils.test_resources import EvenniaCommandTest, EvenniaTest
from systems.corpses import create_corpse
from systems.door_actions import DoorActionError, has_matching_key, item_key_kind
from systems.item_decay import (
    DECAY_ATTRIBUTE,
    QUARANTINE_CATEGORY,
    QUARANTINE_TAG,
    ItemDecayError,
    decay_record,
    process_decay_pulse,
    repair_decay,
)
from systems.item_transfer import (
    AUDIT_ATTRIBUTE,
    BINDING_ATTRIBUTE,
    TransferPolicyError,
    bound_owner_id,
    effective_owner_id,
    move_denial,
    staff_move,
    staff_unbind,
    transfer_denial,
    validate_flag_change,
)
from systems.pulses import PulseEvent, PulseLane
from systems.shops import ShopError, trade_eligible
from typeclasses.objects import Item
from typeclasses.scripts import GamePulseScript
from world.build_schema import ITEM_FIELDS, TYPE_FIELDS, as_decay_minutes, as_key_kind

CHARACTER = "typeclasses.characters.Character"


def pulse(sequence: int) -> PulseEvent:
    """Supply stable objects tokens without a running timer."""
    return PulseEvent(sequence * 60, PulseLane.OBJECTS, sequence)


def make_item(location, key: str = "trinket", **attrs) -> Item:
    """Create a real Item and author primitive attributes on it."""
    item = create_object(Item, key=key, location=location)
    for name, value in attrs.items():
        item.attributes.add(name, value)
    return item


def container(location, key: str = "bag") -> Item:
    """Create an open ordinary container with ample capacity."""
    return make_item(location, key, type="container", capacity=100.0)


class TransferFixture:
    """Share a non-staff PC and an NPC beside EvenniaTest's characters."""

    def setUp(self):
        super().setUp()
        self.other = create_object(CHARACTER, key="Other", location=self.room1)
        self.npc = create_object(CHARACTER, key="Grocer", location=self.room1)
        self.npc.db.is_player_character = False


class TestTransferPolicy(TransferFixture, EvenniaTest):
    """Direct move hooks enforce the same possession rules as commands."""

    def test_no_drop_stays_with_holder_but_moves_freely_when_uncarried(self):
        item = make_item(self.char1, no_drop=True)
        self.assertFalse(item.move_to(self.room1, quiet=True))
        self.assertFalse(item.move_to(self.other, quiet=True))
        bag = container(self.char1)
        self.assertFalse(item.move_to(bag, quiet=True))
        self.assertIs(item.location, self.char1)
        loose = make_item(self.room1, no_drop=True)
        self.assertTrue(loose.move_to(self.char1, quiet=True))

    def test_nested_flags_block_moving_their_container(self):
        bag = container(self.char1)
        make_item(bag, "locket", no_drop=True)
        self.assertFalse(bag.move_to(self.room1, quiet=True))
        self.assertTrue(bag.move_to(container(self.char1, "chest"), quiet=True))

    def test_first_grant_binds_once_and_same_owner_nesting_is_allowed(self):
        item = make_item(self.room1, "ring", account_bound=True)
        self.assertIsNone(bound_owner_id(item))
        self.assertTrue(item.move_to(self.char1, quiet=True))
        self.assertEqual(bound_owner_id(item), self.char1.id)
        bag = container(self.char1)
        inner = container(bag, "pouch")
        self.assertTrue(item.move_to(inner, quiet=True))
        self.assertTrue(item.move_to(self.char1, quiet=True))
        self.assertEqual(bound_owner_id(item), self.char1.id)

    def test_bound_denies_other_characters_npcs_rooms_and_corpses(self):
        item = make_item(self.char1, "ring", account_bound=True)
        for destination in (self.room1, self.other, self.npc, container(self.room1)):
            self.assertFalse(item.move_to(destination, quiet=True))
        self.assertIs(item.location, self.char1)
        self.assertEqual(effective_owner_id(item), self.char1.id)
        corpse = create_object("typeclasses.objects.Corpse", key="corpse")
        corpse.location = self.room1
        self.assertTrue(move_denial(item, corpse, corpse_transfer=True))

    def test_unbound_account_item_cannot_enter_npc_or_reset_onto_npc(self):
        item = make_item(self.room1, "ring", account_bound=True)
        self.assertFalse(item.move_to(self.npc, quiet=True))
        self.assertTrue(transfer_denial(item, None, "reset", self.npc))
        self.assertIsNone(transfer_denial(item, None, "reset", self.room2))

    def test_rollback_and_staff_bypass_are_explicit(self):
        item = make_item(self.char1, no_drop=True)
        self.assertIsNone(move_denial(item, self.room1, move_type="rollback"))
        self.assertTrue(move_denial(item, self.room1, transfer_bypass=" "))
        staff_move(item, self.room1, self.char2, "stuck quest item")
        self.assertIs(item.location, self.room1)
        audit = item.attributes.get(AUDIT_ATTRIBUTE)
        self.assertEqual(audit[-1]["operation"], "move")
        self.assertEqual(audit[-1]["actor"], self.char2.id)
        with self.assertRaises(TransferPolicyError):
            staff_move(item, self.char1, self.char2, "  ")

    def test_staff_unbind_allows_a_new_owner(self):
        item = make_item(self.char1, "ring", account_bound=True)
        item.move_to(container(self.char1), quiet=True)
        staff_unbind(item, self.char2, "gifted by staff")
        staff_move(item, self.other, self.char2, "transfer to new owner")
        self.assertEqual(bound_owner_id(item), self.other.id)
        self.assertEqual(len(item.attributes.get(AUDIT_ATTRIBUTE)), 2)
        with self.assertRaises(TransferPolicyError):
            staff_unbind(make_item(self.room1), self.char2, "nothing bound")

    def test_malformed_flags_and_bindings_fail_closed(self):
        flagged = make_item(self.room1, no_drop="yes")
        self.assertEqual(
            transfer_denial(flagged, self.char1, "get", self.char1),
            "That item needs staff repair.",
        )
        bound = make_item(self.room1, account_bound=True)
        bound.attributes.add(BINDING_ATTRIBUTE, {"version": 1, "owner_id": True})
        self.assertFalse(bound.move_to(self.char1, quiet=True))

    def test_operation_denials(self):
        no_drop = make_item(self.char1, "stone", no_drop=True)
        bound = make_item(self.char1, "ring", account_bound=True)
        bag = container(self.char1)
        for operation in ("drop", "give", "put", "junk", "sell"):
            self.assertTrue(transfer_denial(no_drop, self.char1, operation))
        self.assertIsNone(transfer_denial(no_drop, self.char1, "get"))
        for operation in ("drop", "give", "junk", "sell"):
            self.assertTrue(transfer_denial(bound, self.char1, operation))
        self.assertIsNone(transfer_denial(bound, self.char1, "put", bag))
        with self.assertRaises(TransferPolicyError):
            transfer_denial(bound, self.char1, "teleport")

    def test_shop_uses_canonical_policy(self):
        profile = {"accepted_kinds": ["item"], "buy_markup": 100, "sell_markdown": 50}
        for attrs in ({"no_drop": True}, {"account_bound": True}):
            item = make_item(self.char1, value=10, **attrs)
            with self.assertRaises(ShopError):
                trade_eligible(item, self.char1, profile, buying=False)

    def test_builder_flag_guards(self):
        held = make_item(self.npc, "idol")
        with self.assertRaises(ValueError):
            validate_flag_change(held, "account_bound", True)
        coins = make_item(self.room1, type="money")
        with self.assertRaises(ValueError):
            validate_flag_change(coins, "account_bound", True)
        bound = make_item(self.room1, "ring", account_bound=True)
        bound.move_to(self.char1, quiet=True)
        with self.assertRaises(ValueError):
            _apply_field(bound, "account_bound", ITEM_FIELDS["account_bound"], False)
        loose = make_item(self.room1)
        _apply_field(loose, "no_drop", ITEM_FIELDS["no_drop"], True)
        self.assertIs(loose.attributes.get("no_drop"), True)


class TestDeathRetention(TransferFixture, EvenniaTest):
    """COMBAT-05 death keeps bound items and still takes no-drop items."""

    def test_bound_items_stay_with_owner_including_nested_ones(self):
        self.other.db.is_player_character = True
        ring = make_item(
            self.other, "ring", account_bound=True, wear_locations=["neck"]
        )
        self.other.equipment.equip(ring, "neck")
        bag = container(self.other)
        charm = make_item(bag, "charm", account_bound=True)
        stone = make_item(self.other, "stone", no_drop=True)

        corpse = create_corpse(self.other, "item05a-death")
        again = create_corpse(self.other, "item05a-death")

        self.assertEqual(corpse.id, again.id)
        self.assertIs(ring.location, self.other)
        self.assertIsNone(ring.db.worn_location)
        self.assertIs(charm.location, self.other)
        self.assertIs(bag.location, corpse)
        self.assertIs(stone.location, corpse)
        self.assertEqual(bound_owner_id(charm), self.other.id)
        self.assertTrue(self.other.move_to(self.room2, quiet=True))
        self.assertIs(ring.location, self.other)


class TestItemDecay(TransferFixture, EvenniaTest):
    """Objects-lane tokens decay live items once and spill contents safely."""

    def test_timer_boundaries_duplicate_tokens_and_reload(self):
        item = make_item(self.room1, "bread", decay_minutes=2)
        process_decay_pulse(pulse(1))
        self.assertEqual(decay_record(item)["remaining_pulses"], 2)
        process_decay_pulse(pulse(2))
        process_decay_pulse(pulse(2))
        self.assertEqual(decay_record(item)["remaining_pulses"], 1)
        item.attributes.reset_cache()
        self.assertEqual(decay_record(item)["last_pulse"], 2)
        with patch.object(self.room1, "msg_contents") as announce:
            result = process_decay_pulse(pulse(3))
        self.assertEqual(result.decayed, 1)
        self.assertFalse(Item.objects.filter(id=item.id).exists())
        announce.assert_called_once_with("Bread decays away.")

    def test_downtime_gap_does_not_catch_up(self):
        item = make_item(self.room1, "bread", decay_minutes=3)
        process_decay_pulse(pulse(1))
        process_decay_pulse(pulse(50))
        self.assertEqual(decay_record(item)["remaining_pulses"], 2)

    def test_carried_items_decay_and_stowed_items_pause(self):
        carried = make_item(self.char1, "flower", decay_minutes=1)
        process_decay_pulse(pulse(1))
        self.char1.location = None
        process_decay_pulse(pulse(2))
        record = decay_record(carried)
        self.assertEqual((record["status"], record["remaining_pulses"]), ("paused", 1))
        self.char1.location = self.room1
        with patch.object(self.char1, "msg") as owner_msg:
            process_decay_pulse(pulse(3))
        self.assertFalse(Item.objects.filter(id=carried.id).exists())
        owner_msg.assert_called_once_with("Your flower decays away.")

    def test_equipped_item_is_unequipped_before_deletion(self):
        hat = make_item(self.char1, "hat", decay_minutes=1, wear_locations=["head"])
        self.char1.equipment.equip(hat, "head")
        process_decay_pulse(pulse(1))
        handler = type(self.char1.equipment)
        with patch.object(handler, "unequip", autospec=True) as unequip:
            process_decay_pulse(pulse(2))
        self.assertIs(unequip.call_args.args[1], hat)
        unequip.assert_called_once()

    def test_nested_contents_spill_in_order_preserving_subtrees_and_timers(self):
        outer = container(self.room1, "crate")
        crate = container(outer, "basket")
        crate.attributes.add("decay_minutes", 1)
        pouch = container(crate, "pouch")
        gem = make_item(pouch, "gem")
        apple = make_item(crate, "apple", decay_minutes=5)
        coins = make_item(crate, "coins", type="money", amount=9)
        process_decay_pulse(pulse(1))
        apple_before = decay_record(apple)["remaining_pulses"]
        with patch.object(self.room1, "msg_contents") as announce:
            process_decay_pulse(pulse(2))
        self.assertFalse(Item.objects.filter(id=crate.id).exists())
        for obj in (pouch, apple, coins):
            self.assertIs(obj.location, outer)
        self.assertIs(gem.location, pouch)
        self.assertEqual(coins.attributes.get("amount"), 9)
        self.assertEqual(decay_record(apple)["remaining_pulses"], apple_before - 1)
        announce.assert_called_once()

    def test_empty_item_and_exemptions(self):
        key = make_item(self.room1, "key", type="key", key_kind="gate", decay_minutes=1)
        bound = make_item(self.room1, "ring", account_bound=True, decay_minutes=1)
        pile = make_item(self.room1, "coins", type="money", decay_minutes=1)
        for token in (1, 2, 3):
            process_decay_pulse(pulse(token))
        for obj in (key, bound, pile):
            self.assertTrue(Item.objects.filter(id=obj.id).exists())
            self.assertIsNone(decay_record(obj))

    def test_partial_failure_retains_recoverable_expiring_item(self):
        crate = container(self.room1, "crate")
        crate.attributes.add("decay_minutes", 1)
        first = make_item(crate, "a-first")
        second = make_item(crate, "b-second")
        process_decay_pulse(pulse(1))
        original = Item.move_to

        def fail_second(obj, *args, **kwargs):
            if obj.id == second.id:
                return False
            return original(obj, *args, **kwargs)

        with patch.object(Item, "move_to", fail_second):
            process_decay_pulse(pulse(2))
        self.assertTrue(Item.objects.filter(id=crate.id).exists())
        self.assertEqual(decay_record(crate)["status"], "expiring")
        self.assertIs(first.location, self.room1)
        self.assertIs(second.location, crate)
        process_decay_pulse(pulse(3))
        self.assertFalse(Item.objects.filter(id=crate.id).exists())
        self.assertIs(second.location, self.room1)

    def test_malformed_record_quarantines_only_that_item(self):
        broken = make_item(self.room1, "broken", decay_minutes=1)
        broken.attributes.add(DECAY_ATTRIBUTE, {"version": 9})
        healthy = make_item(self.room1, "healthy", decay_minutes=1)
        result = process_decay_pulse(pulse(1))
        process_decay_pulse(pulse(2))
        self.assertEqual(result.failures, 1)
        self.assertTrue(broken.tags.has(QUARANTINE_TAG, category=QUARANTINE_CATEGORY))
        self.assertFalse(Item.objects.filter(id=healthy.id).exists())
        repair_decay(broken, self.char2, "reset corrupted timer")
        self.assertFalse(broken.tags.has(QUARANTINE_TAG, category=QUARANTINE_CATEGORY))
        process_decay_pulse(pulse(3))
        self.assertEqual(decay_record(broken)["status"], "active")
        with self.assertRaises(ItemDecayError):
            repair_decay(broken, self.char2, "")
        with self.assertRaises(ItemDecayError):
            process_decay_pulse(PulseEvent(60, PulseLane.CORPSES, 1))

    def test_builder_decay_field_restarts_timer_and_prototype_round_trip(self):
        item = make_item(self.room1, "bread")
        field = ITEM_FIELDS["decay_minutes"]
        _apply_field(item, "decay_minutes", field, as_decay_minutes("2"))
        process_decay_pulse(pulse(1))
        process_decay_pulse(pulse(2))
        _apply_field(item, "decay_minutes", field, as_decay_minutes("2"))
        self.assertIsNone(decay_record(item))
        _apply_field(item, "decay_minutes", field, as_decay_minutes("none"))
        self.assertFalse(item.attributes.has("decay_minutes"))
        for raw in ("0", "10081", "1.5", "soon"):
            with self.assertRaises(ValueError):
                as_decay_minutes(raw)

        key = "item05a_test_bread"
        self.addCleanup(delete_prototype, key)
        prototype = {
            "prototype_key": key,
            "key": "loaf",
            "typeclass": "typeclasses.objects.Item",
            "location": self.room1,
        }
        _apply_field(prototype, "decay_minutes", field, 3)
        _apply_field(prototype, "no_drop", ITEM_FIELDS["no_drop"], True)
        _apply_field(prototype, "decay_minutes", field, None)
        _apply_field(prototype, "decay_minutes", field, 4)
        self.assertTrue(search_prototype(key))
        first, second = spawn(key)[0], spawn(key)[0]
        self.assertEqual(first.attributes.get("decay_minutes"), 4)
        self.assertIs(first.attributes.get("no_drop"), True)
        process_decay_pulse(pulse(1))
        process_decay_pulse(pulse(2))
        self.assertEqual(decay_record(second)["remaining_pulses"], 3)
        self.assertEqual(decay_record(first)["remaining_pulses"], 3)

    def test_objects_lane_runs_both_consumers(self):
        with (
            patch("systems.item_resources.process_object_pulse") as fuel,
            patch("systems.item_decay.process_decay_pulse") as decay,
        ):
            GamePulseScript.at_objects_pulse(self.script, pulse(1))
        fuel.assert_called_once_with(pulse(1))
        decay.assert_called_once_with(pulse(1))


class TestKeyKind(EvenniaTest):
    """Key profiles use the same stable identity that locks consume."""

    def test_key_kind_validation_and_matching(self):
        self.assertIs(TYPE_FIELDS["key"]["key_kind"].validate, as_key_kind)
        self.assertEqual(as_key_kind("Gate Key"), "gate_key")
        with self.assertRaises(ValueError):
            as_key_kind("!!!")
        with self.assertRaises(ValueError):
            as_key_kind("x" * 200)
        key = make_item(self.char1, "key", type="key", key_kind="gate_key")
        self.assertEqual(item_key_kind(key), "gate_key")
        self.assertTrue(has_matching_key(self.char1, "gate_key"))
        key.move_to(container(self.char1), quiet=True)
        self.assertFalse(has_matching_key(self.char1, "gate_key"))
        key.attributes.add("key_kind", "Bad Kind")
        with self.assertRaises(DoorActionError):
            item_key_kind(key)


class TestTransferCommands(TransferFixture, EvenniaCommandTest):
    """Every player transfer path reports exactly one canonical denial."""

    def test_drop_give_put_junk_denials_leave_items_in_place(self):
        make_item(self.char1, "stone", no_drop=True)
        container(self.char1)
        self.call(CmdDrop(), "stone", "You cannot drop stone.")
        self.call(CmdGive(), "stone Other", "You cannot give stone away.")
        self.call(CmdPut(), "stone in bag", "You cannot put stone into anything.")
        self.call(CmdJunk(), "stone", "You cannot junk stone.")
        self.assertEqual(
            [obj.key for obj in self.char1.contents if obj.key == "stone"], ["stone"]
        )

    def test_bound_item_commands(self):
        ring = make_item(self.room1, "ring", account_bound=True)
        self.call(CmdGet(), "ring", "You pick up a ring.")
        self.assertEqual(bound_owner_id(ring), self.char1.id)
        container(self.char1)
        self.call(CmdPut(), "ring in bag", "You put ring in bag.")
        self.call(CmdGet(), "ring from bag", "You take ring from bag.")
        self.call(CmdDrop(), "ring", "You cannot drop ring.")
        self.call(CmdGive(), "ring Other", "You cannot give ring away.")
        ring.move_to(self.room1, quiet=True, transfer_bypass="test setup")
        self.call(CmdGet(), "ring", "ring is bound to someone else.", caller=self.other)
        self.assertIs(ring.location, self.room1)

    def test_bag_with_bound_item_cannot_be_dropped(self):
        bag = container(self.char1)
        make_item(bag, "ring", account_bound=True)
        self.call(CmdDrop(), "bag", "You cannot drop bag.")
        self.assertIs(bag.location, self.char1)

    def test_junk_refuses_filled_container_and_deletes_empty_one(self):
        bag = container(self.char1)
        gem = make_item(bag, "gem")
        self.call(CmdJunk(), "bag", "Empty bag first.")
        gem.move_to(self.char1, quiet=True)
        self.call(CmdJunk(), "bag", "You junk bag.")
        self.assertFalse(Item.objects.filter(id=bag.id).exists())

    def test_staff_command_inspect_and_repairs(self):
        ring = make_item(self.char1, "ring", account_bound=True)
        ring.move_to(container(self.char1), quiet=True)
        self.call(CmdItemPolicy(), "ring", "ring (#")
        self.call(CmdItemPolicy(), "/unbind ring", "Usage: itempolicy")
        self.call(CmdItemPolicy(), "/unbind ring = lost owner", "Unbound ring")
        self.call(CmdItemPolicy(), "/move ring = Other, reassign", "Moved ring")
        self.assertEqual(bound_owner_id(ring), self.other.id)
        self.call(CmdItemPolicy(), "/decay ring = clear", "Reset the decay timer")
        self.assertIsNotNone(CharacterCmdSet().get("itempolicy"))
        self.assertEqual(CmdItemPolicy.locks, "cmd:perm(Builder)")
