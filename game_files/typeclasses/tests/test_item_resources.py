"""ITEM-05B resource bounds, conservation, light lifecycle, and replay tests."""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

from commands.building import CmdBuild, CmdBuildSet, _apply_field, _set_item_type
from commands.default_cmdsets import CharacterCmdSet
from commands.item_resources import CmdExtinguish, CmdLight, CmdRefill
from evennia import create_object
from evennia.objects.models import ObjectDB
from evennia.prototypes.prototypes import (
    delete_prototype,
    search_prototype,
)
from evennia.prototypes.spawner import spawn
from evennia.utils.test_resources import EvenniaCommandTest, EvenniaTest
from systems.item_resources import (
    MAX_RESOURCE_UNITS,
    RESOURCE_ATTRIBUTE,
    RESOURCE_STATE_ATTRIBUTE,
    ItemResourceError,
    active_light_level,
    commit_resource_use,
    process_dawn_recharge,
    process_object_pulse,
    refill_resource,
    resource_state,
    set_light,
    set_resource_profile,
    validate_resource_profile,
)
from systems.pulses import (
    PULSE_LANES,
    PulseEvent,
    PulseLane,
    advance_pulse_state,
    initial_pulse_state,
)
from systems.room_environment import LightLevel, set_room_environment_value
from systems.visibility import room_visibility
from systems.world_clock import ClockBoundary, clock_state
from typeclasses.objects import Item
from typeclasses.scripts import GamePulseScript
from world.build_schema import TYPE_FIELDS, as_item_resource


def profile(
    kind: str = "fuel",
    current: int = 3,
    maximum: int = 5,
    recharge: str = "refill",
    key: str = "lamp_oil",
    amount: int = 0,
) -> dict:
    """Build a primitive authored record shared by live and prototype tests."""
    return {
        "version": 1,
        "kind": kind,
        "resource_key": key,
        "current": current,
        "maximum": maximum,
        "recharge": recharge,
        "recharge_amount": amount,
    }


def lamp(owner, current: int = 3, maximum: int = 5, name: str = "lamp"):
    """Create a real directly carried lamp with finite fuel."""
    item = create_object(Item, key=name, location=owner)
    item.db.type = "light"
    set_resource_profile(item, profile(current=current, maximum=maximum))
    return item


def pulse(sequence: int) -> PulseEvent:
    """Supply stable objects tokens without relying on a running timer."""
    return PulseEvent(sequence * 60, PulseLane.OBJECTS, sequence)


class TestItemResources(EvenniaTest):
    """Real ORM state validates boundaries and retains durable replay receipts."""

    def test_profile_bounds_and_unknown_data(self):
        for current, maximum in (
            (0, 0),
            (1, 1),
            (MAX_RESOURCE_UNITS, MAX_RESOURCE_UNITS),
        ):
            self.assertEqual(
                validate_resource_profile(profile(current=current, maximum=maximum))[
                    "current"
                ],
                current,
            )
        invalid = [
            {"current": True},
            {"maximum": -1},
            {"current": 6},
            {"maximum": MAX_RESOURCE_UNITS + 1},
            {"current": 1.5},
            {"kind": "unknown"},
            {"resource_key": "bad key"},
            {"version": True},
            {"recharge": "daily"},
            {"recharge_amount": 1},
            {"recharge": "dawn", "recharge_amount": 2},
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ItemResourceError):
                validate_resource_profile({**profile(), **values})
        with self.assertRaises(ItemResourceError):
            validate_resource_profile({**profile(), "executable": "danger"})
        with self.assertRaises(ItemResourceError):
            validate_resource_profile(profile(), "wand")

    def test_maximum_change_clamps_without_refilling(self):
        item = lamp(self.char1)
        self.assertTrue(commit_resource_use(item, 2, "spend", lambda: True))
        set_resource_profile(item, profile(current=5, maximum=10))
        self.assertEqual(resource_state(item)["current"], 1)
        set_resource_profile(item, profile(current=0, maximum=0))
        self.assertEqual(resource_state(item)["current"], 0)
        self.assertIn("spend", resource_state(item)["receipts"])

    def test_partial_refill_conservation_and_replay_after_spend(self):
        target, source = lamp(self.char1, current=4), lamp(
            self.char1, current=3, name="oil"
        )
        self.assertEqual(
            refill_resource(self.char1, target, source, identity="transfer"), 1
        )
        self.assertEqual(
            (resource_state(target)["current"], resource_state(source)["current"]),
            (5, 2),
        )
        commit_resource_use(target, 2, "spend", lambda: True)
        self.assertEqual(
            refill_resource(self.char1, target, source, identity="transfer"), 1
        )
        self.assertEqual(
            (resource_state(target)["current"], resource_state(source)["current"]),
            (3, 2),
        )
        self.assertEqual(
            refill_resource(self.char1, target, source, identity="second"), 2
        )
        self.assertEqual(
            (resource_state(target)["current"], resource_state(source)["current"]),
            (5, 0),
        )

    def test_refill_messages_wait_for_commit_and_retry_is_silent(self):
        target, source = lamp(self.char1, current=0), lamp(self.char1, name="oil")
        with patch.object(self.char1, "msg") as private, patch.object(
            self.room1, "msg_contents"
        ) as public:
            with self.captureOnCommitCallbacks(execute=True):
                refill_resource(self.char1, target, source, identity="message")
                private.assert_not_called()
            with self.captureOnCommitCallbacks(execute=True):
                refill_resource(self.char1, target, source, identity="message")
            self.assertEqual(private.call_count, 1)
            self.assertEqual(public.call_count, 1)

    def test_denied_adapter_discards_output_and_can_retry(self):
        from django.db import transaction

        item = lamp(self.char1)
        delivered = Mock()

        def adapter() -> bool:
            transaction.on_commit(delivered)
            return False

        with self.captureOnCommitCallbacks(execute=True):
            self.assertFalse(commit_resource_use(item, 1, "failed", adapter))
        delivered.assert_not_called()
        self.assertEqual(resource_state(item)["current"], 3)
        self.assertTrue(commit_resource_use(item, 1, "failed", lambda: True))

    def test_refill_denials(self):
        target, source = lamp(self.char1, current=0), lamp(self.char1)
        with self.assertRaises(ItemResourceError):
            refill_resource(self.char1, target, target)
        for kind, key, policy in (
            ("fuel", "different", "none"),
            ("liquid", "lamp_oil", "refill"),
        ):
            vessel = create_object(Item, key="vessel", location=self.char1)
            vessel.db.type = "other"
            set_resource_profile(vessel, profile(kind=kind, key=key, recharge=policy))
            with self.assertRaises(ItemResourceError):
                refill_resource(self.char1, target, vessel)
        set_resource_profile(target, profile(current=0, recharge="none"))
        with self.assertRaises(ItemResourceError):
            refill_resource(self.char1, target, source)
        self.assertEqual(resource_state(source)["current"], 3)

    def test_liquid_transfer_and_empty_full_bounds(self):
        target, source = [
            create_object(Item, key=name, location=self.char1)
            for name in ("bottle", "jug")
        ]
        for item, current in ((target, 0), (source, 5)):
            item.db.type = "drinkcon"
            set_resource_profile(
                item, profile(kind="liquid", current=current, key="water")
            )
        self.assertEqual(refill_resource(self.char1, target, source), 5)
        for recipient, donor in ((target, source), (source, source)):
            with self.assertRaises(ItemResourceError):
                refill_resource(self.char1, recipient, donor)
        self.assertEqual(resource_state(target)["current"], 5)

    def test_refill_ownership_access_and_action_denied(self):
        target, source = lamp(self.char1, current=0), lamp(self.char2)
        with self.assertRaises(ItemResourceError):
            refill_resource(self.char1, target, source)
        source.move_to(self.char1, quiet=True)
        source.locks.add("interact:false()")
        with self.assertRaises(ItemResourceError):
            refill_resource(self.char1, target, source)
        source.locks.add("interact:all()")
        self.char1.db.position = "sleeping"
        with self.assertRaises(ItemResourceError):
            refill_resource(self.char1, target, source)
        self.assertEqual(resource_state(target)["current"], 0)

    def test_charge_success_failure_exception_and_retry(self):
        item = create_object(Item, key="wand", location=self.char1)
        item.db.type = "wand"
        set_resource_profile(
            item, profile(kind="charges", current=2, recharge="none", key="magic")
        )
        adapter = Mock(return_value=True)
        self.assertTrue(commit_resource_use(item, 1, "first", adapter))
        self.assertTrue(commit_resource_use(item, 1, "first", adapter))
        adapter.assert_called_once()

        def failed() -> bool:
            self.char1.db.resource_adapter_test = "effect"
            return False

        self.assertFalse(
            commit_resource_use(item, 1, "failed", failed, participants=(self.char1,))
        )
        self.assertIsNone(self.char1.db.resource_adapter_test)
        self.assertEqual(resource_state(item)["current"], 1)

        def raises() -> bool:
            self.char1.db.resource_adapter_test = "effect"
            raise RuntimeError("adapter failure")

        with self.assertRaises(RuntimeError):
            commit_resource_use(item, 1, "failed", raises, participants=(self.char1,))
        self.assertIsNone(self.char1.db.resource_adapter_test)
        self.assertTrue(commit_resource_use(item, 1, "failed", adapter))
        self.assertEqual(resource_state(item)["current"], 0)
        self.assertIsNotNone(ObjectDB.objects.filter(pk=item.pk).first())
        with self.assertRaises(ItemResourceError):
            commit_resource_use(item, 1, "empty", adapter)
        self.assertEqual(adapter.call_count, 2)

    def test_reentrant_use_and_refill_are_reserved(self):
        target, source = lamp(self.char1), lamp(self.char1, name="source")

        def adapter() -> bool:
            with self.assertRaises(ItemResourceError):
                commit_resource_use(target, 1, "inner", lambda: True)
            with self.assertRaises(ItemResourceError):
                refill_resource(self.char1, target, source)
            return True

        self.assertTrue(commit_resource_use(target, 1, "outer", adapter))
        self.assertEqual(resource_state(target)["current"], 2)
        self.assertEqual(resource_state(source)["current"], 3)

    def test_refill_rollback_restores_both_items(self):
        target, source = lamp(self.char1, current=0), lamp(self.char1)
        original_add = source.attributes.add

        def fail(key, *args, **kwargs):
            if key == RESOURCE_STATE_ATTRIBUTE:
                raise RuntimeError("write failed")
            return original_add(key, *args, **kwargs)

        with patch.object(
            source.attributes, "add", side_effect=fail
        ), self.assertRaises(RuntimeError):
            refill_resource(self.char1, target, source, identity="retry")
        self.assertEqual(
            (resource_state(target)["current"], resource_state(source)["current"]),
            (0, 3),
        )
        self.assertEqual(
            refill_resource(self.char1, target, source, identity="retry"), 3
        )

    def test_all_charge_recharge_policies_and_dawn_replay(self):
        for item_type in ("wand", "staff"):
            item = create_object(Item, key=item_type, location=self.char1)
            item.db.type = item_type
            set_resource_profile(
                item,
                profile(
                    kind="charges", current=0, recharge="dawn", key="magic", amount=2
                ),
            )
            process_dawn_recharge(ClockBoundary(360, "dawn"))
            self.assertEqual(resource_state(item)["current"], 2)
            commit_resource_use(item, 1, f"{item_type}.spend", lambda: True)
            process_dawn_recharge(ClockBoundary(360, "dawn"))
            self.assertEqual(resource_state(item)["current"], 1)
            process_dawn_recharge(ClockBoundary(1440 * 10 + 360, "dawn"))
            self.assertEqual(resource_state(item)["current"], 3)
            process_dawn_recharge(ClockBoundary(1800, "dawn"))
            self.assertEqual(resource_state(item)["current"], 3)
            process_dawn_recharge(ClockBoundary(1440 * 11 + 360, "dawn"))
            self.assertEqual(resource_state(item)["current"], 5)
            process_dawn_recharge(ClockBoundary(1440 * 12 + 360, "dawn"))
            self.assertEqual(resource_state(item)["current"], 5)
            with self.assertRaises(ItemResourceError):
                validate_resource_profile(
                    profile(kind="charges", recharge="refill", key="magic"), item_type
                )
            with self.assertRaises(ItemResourceError):
                refill_resource(self.char1, item, lamp(self.char1))

    def test_none_policy_does_not_recharge(self):
        item = create_object(Item, key="device", location=self.char1)
        item.db.type = "wand"
        set_resource_profile(
            item, profile(kind="charges", current=0, recharge="none", key="magic")
        )
        process_dawn_recharge(ClockBoundary(360, "dawn"))
        self.assertEqual(resource_state(item)["current"], 0)

    def test_malformed_resource_isolation(self):
        good, bad = lamp(self.char1, current=1), lamp(self.char1, name="bad")
        set_light(self.char1, good, True)
        bad.db.item_resource_state = {"version": 99}
        with patch("systems.item_resources.logger.log_trace"):
            process_object_pulse(pulse(1))
            process_dawn_recharge(ClockBoundary(360, "dawn"))
        self.assertEqual(resource_state(good)["current"], 0)
        self.assertIsNone(active_light_level(self.char1, self.room1))
        with self.assertRaises(ItemResourceError):
            resource_state(bad)


class TestLights(EvenniaTest):
    """Fuel and room illumination follow carried ownership across moves/reloads."""

    def test_light_visibility_and_exact_fuel_exhaustion_messages(self):
        item = lamp(self.char1, current=1)
        set_room_environment_value(self.room1, "indoors", True)
        set_room_environment_value(self.room1, "light", "dark")
        self.assertFalse(room_visibility(self.char2, self.room1).visible)
        with patch.object(self.char1, "msg") as private, patch.object(
            self.room1, "msg_contents"
        ) as public:
            with self.captureOnCommitCallbacks(execute=True):
                set_light(self.char1, item, True, identity="light")
            self.assertTrue(room_visibility(self.char2, self.room1).visible)
            with self.captureOnCommitCallbacks(execute=True):
                process_object_pulse(pulse(1))
                process_object_pulse(pulse(1))
                process_object_pulse(pulse(0 + 1))
            self.assertEqual(private.call_count, 2)
            self.assertEqual(public.call_count, 2)
        self.assertEqual(resource_state(item)["current"], 0)
        self.assertFalse(resource_state(item)["lit"])
        self.assertFalse(room_visibility(self.char2, self.room1).visible)
        with self.assertRaises(ItemResourceError):
            set_light(self.char1, item, True)

    def test_extinguish_stops_consumption_and_old_light_retry(self):
        item = lamp(self.char1)
        set_light(self.char1, item, True, identity="light")
        process_object_pulse(pulse(2))
        process_object_pulse(pulse(1))
        set_light(self.char1, item, False, identity="off")
        self.assertFalse(set_light(self.char1, item, True, identity="light"))
        process_object_pulse(pulse(3))
        self.assertEqual(resource_state(item)["current"], 2)
        self.assertFalse(resource_state(item)["lit"])

    def test_equipped_lamp_and_carrier_room_movement(self):
        item = lamp(self.char1)
        item.db.wear_locations = ["hold"]
        self.char1.equipment.equip(item, "hold")
        set_light(self.char1, item, True)
        self.assertEqual(active_light_level(self.char2, self.room1), LightLevel.BRIGHT)
        self.assertIsNone(active_light_level(self.char2, self.room2))
        self.char1.move_to(self.room2, quiet=True, move_type="forced")
        self.assertIsNone(active_light_level(self.char2, self.room1))
        self.assertEqual(active_light_level(self.char2, self.room2), LightLevel.BRIGHT)

    def test_drop_give_put_extinguish_and_delete_does_not_poison_pulses(self):
        container = create_object(Item, key="bag", location=self.char1)
        container.db.type = "container"
        container.db.capacity = 100
        for destination in (self.room1, self.char2, container):
            item = lamp(self.char1)
            set_light(self.char1, item, True)
            item.move_to(destination, quiet=True)
            self.assertFalse(resource_state(item)["lit"])
            process_object_pulse(pulse(1))
            self.assertEqual(resource_state(item)["current"], 3)
            with self.assertRaises(ItemResourceError):
                set_light(self.char1, item, True)
            item.delete()
        process_object_pulse(pulse(2))
        self.assertIsNone(active_light_level(self.char1, self.room1))

    def test_hook_bypass_cannot_supply_light(self):
        item = lamp(self.char1)
        set_light(self.char1, item, True)
        item.location = self.char2
        self.assertIsNone(active_light_level(self.char2, self.room1))
        process_object_pulse(pulse(1))
        self.assertFalse(resource_state(item)["lit"])
        self.assertEqual(resource_state(item)["current"], 3)

    def test_reload_and_cold_restart_do_not_catch_up(self):
        item = lamp(self.char1)
        set_light(self.char1, item, True)
        process_object_pulse(pulse(5))
        reloaded = ObjectDB.objects.get(pk=item.pk)
        self.assertTrue(resource_state(reloaded)["lit"])
        process_object_pulse(pulse(5))
        process_object_pulse(pulse(100))
        self.assertEqual(resource_state(reloaded)["current"], 1)

    def test_light_lock_wrong_type_zero_and_sleep_denials(self):
        item = lamp(self.char1)
        item.locks.add("interact:false()")
        with self.assertRaises(ItemResourceError):
            set_light(self.char1, item, True)
        item.locks.add("interact:all()")
        self.char1.db.position = "sleeping"
        with self.assertRaises(ItemResourceError):
            set_light(self.char1, item, True)
        self.char1.db.position = "standing"
        item.db.type = "other"
        with self.assertRaises(ItemResourceError):
            set_light(self.char1, item, True)
        self.assertEqual(resource_state(item)["current"], 3)


class TestResourceBuilder(EvenniaTest):
    """The existing primitive editor/prototype path preserves schema and state."""

    def test_profile_json_live_builder_and_type_cleanup(self):
        item = lamp(self.char1)
        field = TYPE_FIELDS["light"]["resource"]
        self.assertEqual(as_item_resource(json.dumps(profile())), profile())
        _apply_field(
            item,
            "resource",
            field,
            as_item_resource(json.dumps(profile(current=0, maximum=2))),
        )
        self.assertEqual(resource_state(item)["current"], 2)
        _set_item_type(item, "wand")
        self.assertFalse(item.attributes.has(RESOURCE_ATTRIBUTE))
        self.assertFalse(item.attributes.has(RESOURCE_STATE_ATTRIBUTE))
        with self.assertRaises(ValueError):
            as_item_resource("not JSON")
        with self.assertRaises(ValueError):
            as_item_resource(" " * 2001)

    def test_prototype_round_trip_and_independent_spawns(self):
        key = "item05b_test_lamp"
        self.addCleanup(delete_prototype, key)
        prototype = {
            "prototype_key": key,
            "key": "template lamp",
            "typeclass": "typeclasses.objects.Item",
            "type": "light",
            "location": self.char1,
        }
        _apply_field(prototype, "resource", TYPE_FIELDS["light"]["resource"], profile())
        first, second = spawn(key)[0], spawn(key)[0]
        set_light(self.char1, first, True)
        process_object_pulse(pulse(1))
        self.assertEqual(resource_state(first)["current"], 2)
        self.assertEqual(resource_state(second)["current"], 3)
        stored = search_prototype(key)[0]
        self.assertNotIn(RESOURCE_STATE_ATTRIBUTE, stored)
        with self.assertRaises(ItemResourceError):
            _apply_field(
                {**prototype, "type": "wand"},
                "resource",
                TYPE_FIELDS["wand"]["resource"],
                profile(),
            )

    def test_objects_lane_upgrade_and_script_dispatch(self):
        state = initial_pulse_state()
        state["heartbeat"] = 59
        state["lane_sequences"].pop("objects")
        upgraded, events = advance_pulse_state(
            state, {lane: 60 for lane in PULSE_LANES}
        )
        self.assertEqual(upgraded["heartbeat"], 60)
        self.assertEqual(upgraded["lane_sequences"]["objects"], 1)
        self.assertIn(PulseLane.OBJECTS, [event.lane for event in events])
        with patch("systems.item_resources.process_object_pulse") as process:
            script = self.script
            GamePulseScript.at_objects_pulse(script, pulse(1))
        process.assert_called_once_with(pulse(1))

    def test_clock_dispatch_recharges_devices(self):
        # Use the real existing scheduler typeclass instead of an object timer.
        from evennia import create_script

        script = create_script(GamePulseScript, key="test item clock", autostart=False)
        self.addCleanup(script.delete)
        item = create_object(Item, key="wand", location=self.char1)
        item.db.type = "wand"
        set_resource_profile(
            item,
            profile(kind="charges", current=0, recharge="dawn", key="magic", amount=2),
        )
        script.db.world_clock = {**clock_state(script), "minute": 359}
        script.at_world_time_pulse(PulseEvent(60, PulseLane.WORLD_TIME, 1))
        self.assertEqual(resource_state(item)["current"], 2)


class TestResourceCommands(EvenniaCommandTest):
    """Run grammar and denials through the project's command runner."""

    def test_commands_and_registration(self):
        item = lamp(self.char1)
        source = lamp(self.char1, name="oil")
        with patch(
            "systems.item_resources.transaction.on_commit",
            side_effect=lambda callback: callback(),
        ):
            self.call(CmdLight(), "lamp", "You light lamp.")
            self.call(CmdExtinguish(), "lamp", "You extinguish lamp.")
            self.call(CmdRefill(), "lamp FROM oil", "You refill lamp with 2 units.")
        self.assertEqual(resource_state(source)["current"], 1)
        self.assertEqual(resource_state(item)["current"], 5)
        for key in ("light", "extinguish", "refill"):
            self.assertIsNotNone(CharacterCmdSet().get(key))

    def test_builder_command_profile_and_bounds(self):
        key = "item05b_editor_test"
        self.addCleanup(delete_prototype, key)
        self.call(CmdBuild(), f"new item {key}")
        self.call(CmdBuildSet(), "type light")
        self.call(CmdBuildSet(), "resource " + json.dumps(profile()))
        self.assertEqual(self.char1.ndb._build_target[RESOURCE_ATTRIBUTE], profile())
        self.call(
            CmdBuildSet(),
            "resource " + json.dumps(profile(current=6)),
            "Invalid value for 'resource'",
        )
        self.assertEqual(self.char1.ndb._build_target[RESOURCE_ATTRIBUTE], profile())

    def test_invalid_grammar_missing_ambiguous_and_unseen(self):
        self.call(CmdRefill(), "lamp", "Usage: refill <target> from <source>.")
        self.call(CmdLight(), "", "Name one directly carried item.")
        lamp(self.char1)
        lamp(self.char1)
        self.call(CmdLight(), "lamp", "Name one directly carried item.")
        hidden = lamp(self.char1, name="hidden")
        hidden.locks.add("view:false()")
        self.call(CmdLight(), "hidden", "Name one directly carried item.")

    def test_empty_already_extinguished_and_noncombat_policy(self):
        item = lamp(self.char1, current=0)
        self.call(CmdLight(), "lamp", "That light has no fuel.")
        self.call(CmdExtinguish(), "lamp", "It is already extinguished.")
        self.char1.db.position = "sleeping"
        self.call(
            CmdLight(),
            "lamp",
            "You are asleep and cannot do that. Type wake to wake up.",
        )
        self.assertEqual(resource_state(item)["current"], 0)
