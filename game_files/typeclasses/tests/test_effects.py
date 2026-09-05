"""Tests for the persistent RULES-03 effect and condition model."""

from collections.abc import Mapping
from unittest.mock import MagicMock, patch

from evennia import create_object
from evennia.objects.models import ObjectDB
from evennia.utils.test_resources import EvenniaTest
from systems.dice import RollResult
from systems.effects import (EFFECT_QUARANTINE_ATTRIBUTE, EFFECT_REGISTRY,
                             EFFECTS_ATTRIBUTE, EFFECTS_SCHEMA_VERSION,
                             ApplyOutcome, EffectDefinition, EffectError,
                             EffectHandler, EffectMessage, EffectRegistry,
                             EffectStorageError, RemovalOutcome, RemovalReason,
                             SaveRule, SaveSuccess, SaveTiming, StackingPolicy)


def _register(definition: EffectDefinition) -> None:
    """Register a test definition once across repeated test discovery."""
    if EFFECT_REGISTRY.get(definition.key) is None:
        EFFECT_REGISTRY.register(definition)


_PERMANENT = EffectDefinition(
    key="test.rules03.ward",
    name="Test Ward",
    modifiers={"armor_class": 2, "hp_max": 5},
    conditions=frozenset({"warded"}),
    removal_categories=frozenset({"magic"}),
    messages={
        "apply": EffectMessage(
            target="The {effect} surrounds you.",
            room="The {effect} surrounds {target}.",
        ),
        "remove": EffectMessage(target="The {effect} leaves you."),
    },
)
_TIMED = EffectDefinition(
    key="test.rules03.slow",
    name="Test Slow",
    duration=2,
    modifiers={"speed": -5},
    conditions=frozenset({"slowed"}),
    removal_categories=frozenset({"magic"}),
    messages={
        "apply": EffectMessage(target="You slow down."),
        "expire": EffectMessage(
            target="You speed up again.", room="{target} speeds up again."
        ),
    },
)
_REJECT = EffectDefinition(
    key="test.rules03.reject", name="Reject", stacking=StackingPolicy.REJECT
)
_REFRESH = EffectDefinition(
    key="test.rules03.refresh",
    name="Refresh",
    duration=5,
    stacking=StackingPolicy.REFRESH,
    messages={
        "apply": EffectMessage(target="Refresh applied."),
        "refresh": EffectMessage(target="Refresh refreshed."),
    },
)
_REPLACE = EffectDefinition(
    key="test.rules03.replace",
    name="Replace",
    duration=5,
    stacking=StackingPolicy.REPLACE,
)
_STACK = EffectDefinition(
    key="test.rules03.stack",
    name="Stack",
    duration=4,
    stacking=StackingPolicy.STACK,
    max_stacks=3,
    modifiers={"attack_bonus": 1},
    messages={"stack": EffectMessage(target="Stacks: {stacks}.")},
)
_INDEPENDENT = EffectDefinition(
    key="test.rules03.independent",
    name="Independent",
    duration=3,
    stacking=StackingPolicy.INDEPENDENT,
    removal_categories=frozenset({"poison"}),
)
_SAVE_APPLY = EffectDefinition(
    key="test.rules03.save_apply",
    name="Initial Save",
    save=SaveRule("Wisdom", 12),
    messages={"save": EffectMessage(target="You resist {effect}.")},
)
_SAVE_PULSE = EffectDefinition(
    key="test.rules03.save_pulse",
    name="Pulse Save",
    duration=4,
    save=SaveRule(
        "Constitution",
        13,
        timing=SaveTiming.ON_PULSE,
        success=SaveSuccess.END,
    ),
    messages={"save": EffectMessage(target="You shake off {effect}.")},
)
_CAPACITY = EffectDefinition(
    key="test.rules03.capacity",
    name="Capacity",
    modifiers={"carry_capacity": 25},
)

for _definition in (
    _PERMANENT,
    _TIMED,
    _REJECT,
    _REFRESH,
    _REPLACE,
    _STACK,
    _INDEPENDENT,
    _SAVE_APPLY,
    _SAVE_PULSE,
    _CAPACITY,
):
    _register(_definition)


class TestEffectDefinitions(EvenniaTest):
    """Definitions reject storage and rule combinations the engine cannot honor."""

    def test_registry_rejects_duplicates(self):
        registry = EffectRegistry()
        definition = EffectDefinition(key="test.effect", name="Effect")
        registry.register(definition)

        with self.assertRaises(EffectError):
            registry.register(definition)

    def test_invalid_keys_durations_modifiers_and_messages_are_rejected(self):
        invalid_definitions = (
            {"key": "Bad Key", "name": "Bad"},
            {"key": "bad.duration", "name": "Bad", "duration": 0},
            {
                "key": "bad.modifier",
                "name": "Bad",
                "modifiers": {"made_up": 1},
            },
            {
                "key": "bad.message",
                "name": "Bad",
                "messages": {"apply": EffectMessage(target="{unknown}")},
            },
            {
                "key": "bad.stacks",
                "name": "Bad",
                "max_stacks": 2,
            },
            {
                "key": "bad.conditions",
                "name": "Bad",
                "conditions": "poisoned",
            },
        )

        for values in invalid_definitions:
            with self.subTest(values=values), self.assertRaises(EffectError):
                EffectDefinition(**values)

    def test_save_rules_enforce_supported_timing_and_outcome_pairs(self):
        with self.assertRaises(EffectError):
            SaveRule("Wisdom", 10, SaveTiming.ON_APPLY, SaveSuccess.END)
        with self.assertRaises(EffectError):
            SaveRule("Constitution", 10, SaveTiming.ON_PULSE, SaveSuccess.NEGATE)


class TestEffectHandler(EvenniaTest):
    """Active effects persist and drive shared PC/NPC rules predictably."""

    def setUp(self):
        super().setUp()
        for ability in (
            "strength",
            "dexterity",
            "constitution",
            "intelligence",
            "wisdom",
            "charisma",
        ):
            setattr(self.char1.db, ability, 10)
        self.char1.db.char_class = "Fighter"
        self.char1.db.hp_base = 10
        self.char1.db.hp_current = 10
        self.char1.db.speed = 30
        self.char1.attributes.remove(EFFECTS_ATTRIBUTE)

    def test_persistent_record_reconstructs_without_class_references(self):
        result = self.char1.effects.add(
            _PERMANENT.key,
            source=self.char2,
            source_key="test_spell",
            modifiers={"saving_throw:wisdom": 1},
        )

        stored = self.char1.attributes.get(EFFECTS_ATTRIBUTE)
        self.assertEqual(stored["version"], EFFECTS_SCHEMA_VERSION)
        self.assertFalse(_contains_class_reference(stored))

        reload_registry = EffectRegistry()
        reload_registry.register(
            EffectDefinition(
                key=_PERMANENT.key,
                name="Reloaded Test Ward",
                modifiers={"armor_class": 2, "hp_max": 5},
                conditions=frozenset({"warded"}),
                removal_categories=frozenset({"magic"}),
            )
        )
        reloaded_owner = ObjectDB.objects.get(pk=self.char1.pk)
        reconstructed = EffectHandler(reloaded_owner, reload_registry).get(
            result.effect.instance_id
        )
        self.assertIsNotNone(reconstructed)
        self.assertEqual(reconstructed.key, _PERMANENT.key)
        self.assertEqual(reconstructed.name, "Reloaded Test Ward")
        self.assertEqual(reconstructed.source, self.char2)
        self.assertEqual(reconstructed.source_key, "test_spell")
        self.assertIsNone(reconstructed.remaining_pulses)
        self.assertEqual(reconstructed.modifiers["saving_throw:wisdom"], 1)

    def test_effects_update_conditions_stats_and_hp_immediately(self):
        result = self.char1.effects.add(_PERMANENT.key)

        self.assertTrue(self.char1.effects.has(_PERMANENT.key))
        self.assertTrue(self.char1.effects.has_condition("warded"))
        self.assertEqual(self.char1.stats.armor_class, 12)
        self.assertEqual(self.char1.stats.hp_max, 15)
        self.char1.stats.set_hp(15)

        removed = self.char1.effects.remove(result.effect.instance_id, quiet=True)

        self.assertEqual(removed.outcome, RemovalOutcome.REMOVED)
        self.assertFalse(self.char1.effects.has_condition("warded"))
        self.assertEqual(self.char1.stats.armor_class, 10)
        self.assertEqual(self.char1.stats.hp_max, 10)
        self.assertEqual(self.char1.stats.hp_current, 10)

    def test_carry_capacity_modifier_is_a_valid_canonical_effect_hook(self):
        self.char1.db.strength = 10
        before = self.char1.stats.carry_capacity

        result = self.char1.effects.add(_CAPACITY.key, quiet=True)

        self.assertEqual(self.char1.stats.carry_capacity, before + 25)
        self.char1.effects.remove(result.effect.instance_id, quiet=True)
        self.assertEqual(self.char1.stats.carry_capacity, before)

    def test_reject_refresh_replace_and_independent_policies(self):
        rejected_first = self.char1.effects.add(_REJECT.key, quiet=True)
        rejected_second = self.char1.effects.add(_REJECT.key, quiet=True)
        self.assertEqual(rejected_second.outcome, ApplyOutcome.REJECTED)
        self.assertEqual(
            rejected_second.effect.instance_id, rejected_first.effect.instance_id
        )

        refreshed_first = self.char1.effects.add(
            _REFRESH.key, duration=2, source=self.char1, quiet=True
        )
        self.char1.effects.process_duration(1, quiet=True)
        refreshed_second = self.char1.effects.add(
            _REFRESH.key, source=self.char2, quiet=True
        )
        self.assertEqual(refreshed_second.outcome, ApplyOutcome.REFRESHED)
        self.assertEqual(
            refreshed_second.effect.instance_id, refreshed_first.effect.instance_id
        )
        self.assertEqual(refreshed_second.effect.remaining_pulses, 5)
        self.assertEqual(refreshed_second.effect.source, self.char2)

        replaced_first = self.char1.effects.add(_REPLACE.key, quiet=True)
        replaced_second = self.char1.effects.add(_REPLACE.key, quiet=True)
        self.assertEqual(replaced_second.outcome, ApplyOutcome.REPLACED)
        self.assertNotEqual(
            replaced_second.effect.instance_id, replaced_first.effect.instance_id
        )

        independent_one = self.char1.effects.add(
            _INDEPENDENT.key, source=self.char1, quiet=True
        )
        independent_two = self.char1.effects.add(
            _INDEPENDENT.key, source=self.char2, quiet=True
        )
        self.assertNotEqual(
            independent_one.effect.instance_id, independent_two.effect.instance_id
        )
        self.assertEqual(
            len(
                [
                    effect
                    for effect in self.char1.effects.all()
                    if effect.key == _INDEPENDENT.key
                ]
            ),
            2,
        )

        with self.assertRaises(EffectError):
            self.char1.effects.add(_REJECT.key, stacks=2, quiet=True)

    def test_stacking_is_capped_and_multiplies_modifiers(self):
        first = self.char1.effects.add(_STACK.key, stacks=2, quiet=True)
        stacked = self.char1.effects.add(_STACK.key, stacks=2, quiet=True)

        self.assertEqual(stacked.outcome, ApplyOutcome.STACKED)
        self.assertEqual(stacked.effect.instance_id, first.effect.instance_id)
        self.assertEqual(stacked.effect.stacks, 3)
        self.assertEqual(stacked.effect.modifiers["attack_bonus"], 3)
        self.assertEqual(self.char1.stats.attack_profile().attack_bonus, 5)

    def test_reapplication_emits_only_its_policy_message(self):
        self.char1.msg = MagicMock()

        self.char1.effects.add(_REFRESH.key)
        self.char1.effects.add(_REFRESH.key)
        self.char1.effects.add(_STACK.key, quiet=True)
        self.char1.effects.add(_STACK.key)

        self.assertEqual(
            [call.args[0] for call in self.char1.msg.call_args_list],
            ["Refresh applied.", "Refresh refreshed.", "Stacks: 2."],
        )

    def test_duration_expires_once_with_target_and_room_messages(self):
        self.char1.msg = MagicMock()
        self.room1.msg_contents = MagicMock()
        result = self.char1.effects.add(_TIMED.key)

        self.char1.effects.process_duration(1)
        self.assertEqual(
            self.char1.effects.get(result.effect.instance_id).remaining_pulses, 1
        )
        removed = self.char1.effects.process_duration(1)
        removed_again = self.char1.effects.process_duration(1)

        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0].reason, RemovalReason.EXPIRED)
        self.assertEqual(removed_again, ())
        self.assertEqual(
            [call.args[0] for call in self.char1.msg.call_args_list],
            ["You slow down.", "You speed up again."],
        )
        self.room1.msg_contents.assert_called_once_with(
            f"{self.char1.key} speeds up again.",
            exclude=[self.char1],
            from_obj=self.char1,
        )

    def test_application_and_pulse_saves_use_canonical_roll_results(self):
        success = RollResult(18, 0, 18, 12, True)
        failure = RollResult(2, 0, 2, 12, False)
        self.char1.msg = MagicMock()
        with patch("systems.effects.roll_check", return_value=success) as mocked_roll:
            resisted = self.char1.effects.add(_SAVE_APPLY.key)

        self.assertEqual(resisted.outcome, ApplyOutcome.SAVED)
        self.assertFalse(self.char1.effects.has(_SAVE_APPLY.key))
        mocked_roll.assert_called_once_with(
            self.char1.stats.saving_throw_bonus("Wisdom"), 12
        )
        self.char1.msg.assert_called_once_with("You resist Initial Save.")

        with patch("systems.effects.roll_check", return_value=failure):
            applied = self.char1.effects.add(_SAVE_APPLY.key, quiet=True)
        self.assertEqual(applied.outcome, ApplyOutcome.APPLIED)
        self.assertEqual(applied.save_roll, failure)

        pulse_result = self.char1.effects.add(_SAVE_PULSE.key, quiet=True)
        pulse_success = RollResult(20, 0, 20, 13, True)
        with patch("systems.effects.roll_check", return_value=pulse_success):
            removals = self.char1.effects.process_duration(1)
        self.assertEqual(len(removals), 1)
        self.assertEqual(removals[0].reason, RemovalReason.SAVED)
        self.assertEqual(
            removals[0].effect.instance_id, pulse_result.effect.instance_id
        )
        self.assertFalse(self.char1.effects.has(_SAVE_PULSE.key))

    def test_removal_rules_allow_matching_categories_and_deny_others(self):
        result = self.char1.effects.add(_PERMANENT.key, quiet=True)

        denied = self.char1.effects.remove(
            result.effect.instance_id,
            reason=RemovalReason.CURED,
            category="poison",
            quiet=True,
        )
        self.assertEqual(denied.outcome, RemovalOutcome.DENIED)
        self.assertTrue(self.char1.effects.has(_PERMANENT.key))

        removed = self.char1.effects.remove(
            result.effect.instance_id,
            reason=RemovalReason.DISPELLED,
            category="magic",
            quiet=True,
        )
        self.assertEqual(removed.outcome, RemovalOutcome.REMOVED)

    def test_filtered_removal_and_missing_source_snapshot(self):
        source_dbref = self.char2.dbref
        kept = self.char1.effects.add(_INDEPENDENT.key, source=self.char1, quiet=True)
        removed = self.char1.effects.add(
            _INDEPENDENT.key, source=self.char2, source_key="venom", quiet=True
        )
        source_name = self.char2.key
        self.char2.delete()

        reconstructed = self.char1.effects.get(removed.effect.instance_id)
        self.assertEqual(reconstructed.source_name, source_name)
        self.assertEqual(reconstructed.source_dbref, source_dbref)
        results = self.char1.effects.remove_by_source(source_dbref, quiet=True)

        self.assertEqual(len(results), 1)
        self.assertIsNone(self.char1.effects.get(removed.effect.instance_id))
        self.assertIsNotNone(self.char1.effects.get(kept.effect.instance_id))

    def test_filtered_removal_honors_category_and_source(self):
        kept = self.char1.effects.add(_INDEPENDENT.key, source=self.char1, quiet=True)
        removed = self.char1.effects.add(
            _INDEPENDENT.key, source=self.char2, quiet=True
        )

        results = self.char1.effects.remove_matching(
            "poison", source=self.char2, quiet=True
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].reason, RemovalReason.CURED)
        self.assertIsNotNone(self.char1.effects.get(kept.effect.instance_id))
        self.assertIsNone(self.char1.effects.get(removed.effect.instance_id))

    def test_malformed_storage_is_reported(self):
        self.char1.db.active_effects = {"version": 999, "instances": {}}

        with self.assertRaises(EffectStorageError):
            self.char1.effects.all()

    def test_staff_repair_quarantines_malformed_storage(self):
        self.char1.db.active_effects = {"version": 999, "instances": {}}

        result = self.char1.effects.repair_storage(audited_by=self.char2)

        self.assertTrue(result.repaired)
        self.assertEqual(result.quarantined, 1)
        self.assertIsNone(self.char1.attributes.get(EFFECTS_ATTRIBUTE))
        quarantine = self.char1.attributes.get(EFFECT_QUARANTINE_ATTRIBUTE)
        self.assertEqual(quarantine["version"], 1)
        self.assertEqual(quarantine["entries"][-1]["audited_by"], self.char2.dbref)

    def test_staff_repair_quarantines_orphans_and_retains_valid_effects(self):
        valid = self.char1.effects.add(_PERMANENT.key, quiet=True)
        stored = self.char1.attributes.get(EFFECTS_ATTRIBUTE)
        stored["instances"]["orphaned"] = {
            "key": "test.rules03.deleted",
            "stacks": 1,
            "remaining_pulses": None,
            "source": None,
            "source_dbref": None,
            "source_key": None,
            "source_name": "Unknown",
            "modifiers": {},
            "removal_categories": [],
            "save": None,
        }
        self.char1.db.active_effects = stored

        result = self.char1.effects.repair_storage(audited_by=self.char2)

        self.assertTrue(result.repaired)
        self.assertEqual(result.retained, 1)
        self.assertEqual(result.quarantined, 1)
        self.assertIsNotNone(self.char1.effects.get(valid.effect.instance_id))
        self.assertIsNone(self.char1.effects.get("orphaned"))
        quarantine = self.char1.attributes.get(EFFECT_QUARANTINE_ATTRIBUTE)
        self.assertIn("orphaned", quarantine["entries"][-1]["payload"]["instances"])

    def test_npc_uses_the_same_effect_and_stat_path(self):
        npc = create_object(
            "typeclasses.characters.Character",
            key="Test NPC",
            location=self.room1,
            attributes=(("dexterity", 10),),
        )

        npc.effects.add(_PERMANENT.key, quiet=True)

        self.assertFalse(npc.has_account)
        self.assertTrue(npc.effects.has_condition("warded"))
        self.assertEqual(npc.stats.armor_class, 12)


def _contains_class_reference(value: object) -> bool:
    """Return whether nested persistent data contains an imported class object."""
    if isinstance(value, type):
        return True
    if isinstance(value, Mapping):
        return any(
            _contains_class_reference(key) or _contains_class_reference(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_class_reference(item) for item in value)
    return False
