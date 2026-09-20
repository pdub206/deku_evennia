"""ITEM-04B magic-item registry, reservation, consumption, and replay tests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import Mock, patch

from commands.building import CmdBuild, CmdBuildSet, _set_item_type
from commands.default_cmdsets import CharacterCmdSet
from commands.magic_items import (
    CmdQuaff,
    CmdRecite,
    CmdUseDevice,
    _parse_activation,
)
from evennia import create_object
from evennia.objects.models import ObjectDB
from evennia.prototypes.prototypes import delete_prototype
from evennia.utils.test_resources import EvenniaCommandTest, EvenniaTest
from systems.advancement import initialize_level_one
from systems.item_resources import (
    RESOURCE_ATTRIBUTE,
    ItemResourceError,
    resource_state,
    set_resource_profile,
)
from systems.magic import MAGIC_REGISTRY, MagicRegistryError, TargetingMode
from systems.magic_actions import (
    MagicActionError,
    grant_spellbook_entry,
    mark_preparation_window,
    prepare_action,
)
from systems.magic_items import (
    ACCESS_ANYONE,
    ACCESS_KNOWN_CLASS_ACTION,
    CONSUMPTION_CHARGE,
    CONSUMPTION_ITEM,
    CONSUMPTION_PORTION,
    MAGIC_ITEM_ATTRIBUTE,
    MAGIC_ITEM_REGISTRY,
    MAGIC_ITEM_STATE_ATTRIBUTE,
    MAGIC_ITEM_TYPES,
    MagicItemDefinition,
    MagicItemError,
    activate_magic_item,
    build_magic_item_registry,
    magic_item_state,
    remaining_uses,
    set_magic_item_profile,
    validate_magic_item_profile,
)
from typeclasses.objects import Item

_ALPHA_TARGETING = frozenset(
    {TargetingMode.SELF, TargetingMode.CREATURE, TargetingMode.HOSTILE}
)


def profile(definition_key: str, uses: int = 1) -> dict:
    """Build the primitive authored record shared by live and prototype tests."""
    return {"version": 1, "definition": definition_key, "uses": uses}


def charges(current: int = 2, maximum: int = 3) -> dict:
    """Build the ITEM-05B charge resource a wand or staff spends."""
    return {
        "version": 1,
        "kind": "charges",
        "resource_key": "arcane_charge",
        "current": current,
        "maximum": maximum,
        "recharge": "none",
        "recharge_amount": 0,
    }


def magic_item(
    owner,
    definition_key: str,
    *,
    uses: int = 1,
    name: str | None = None,
    units: int | None = None,
):
    """Create a real carried magic item of the definition's own item type."""
    definition = MAGIC_ITEM_REGISTRY.definition_for(definition_key)
    item = create_object(Item, key=name or definition.item_type, location=owner)
    item.db.type = definition.item_type
    if units is not None:
        set_resource_profile(item, charges(current=units))
    set_magic_item_profile(item, profile(definition_key, uses))
    return item


def sample_definition(**overrides) -> MagicItemDefinition:
    """Build one valid released-shaped definition tests can invalidate."""
    values = {
        "key": "potion.sample",
        "display_name": "Sample Potion",
        "item_type": "potion",
        "action_key": "cleric.cure_wounds",
        "targeting_mode": TargetingMode.CREATURE,
        "command": "quaff",
        "access_rule": ACCESS_ANYONE,
        "consumption": CONSUMPTION_PORTION,
        "help_summary": "A sample potion.",
        "srd_reference": "SRD 5.2.1 Magic Items: Potion of Healing",
    }
    values.update(overrides)
    return MagicItemDefinition(**values)


class TestMagicItemRegistry(EvenniaTest):
    """The released catalogue and its validation fail closed on unsafe data."""

    def test_released_catalogue_covers_every_type_rule_and_targeting_mode(self):
        """Every released definition agrees with MAGIC-01 and carries its help."""
        definitions = tuple(MAGIC_ITEM_REGISTRY.definitions.values())
        self.assertEqual(
            {definition.item_type for definition in definitions}, set(MAGIC_ITEM_TYPES)
        )
        self.assertEqual(
            {definition.access_rule for definition in definitions},
            {ACCESS_ANYONE, ACCESS_KNOWN_CLASS_ACTION},
        )
        self.assertEqual(
            {definition.targeting_mode for definition in definitions}, _ALPHA_TARGETING
        )
        for definition in definitions:
            action = MAGIC_REGISTRY.definition_for(
                definition.action_key, include_disabled=False
            )
            self.assertEqual(action.targeting.mode, definition.targeting_mode)
            self.assertEqual(action.cast_time, 1)
            self.assertTrue(definition.srd_reference.startswith("SRD 5.2.1 "))
            self.assertTrue(definition.help_summary)
        self.assertEqual(
            {definition.consumption for definition in definitions},
            {CONSUMPTION_PORTION, CONSUMPTION_ITEM, CONSUMPTION_CHARGE},
        )

    def test_definitions_fail_closed_on_unsafe_or_unreleased_data(self):
        """Unknown, mismatched, and unbounded definitions never become usable."""
        invalid = (
            {"key": "Potion.Sample"},
            {"key": "sample"},
            {"display_name": ""},
            {"item_type": "wand"},
            {"command": "recite"},
            {"consumption": CONSUMPTION_CHARGE},
            {"access_rule": "skill-check"},
            {"action_key": "wizard.not_a_spell"},
            {"targeting_mode": TargetingMode.HOSTILE},
            {"help_summary": ""},
            {"srd_reference": "Homebrew"},
            {"enabled": "yes"},
        )
        for overrides in invalid:
            with self.assertRaises(MagicItemError, msg=overrides):
                build_magic_item_registry((sample_definition(**overrides),))

    def test_duplicate_and_unavailable_action_references_are_rejected(self):
        """One key owns one definition; unknown, disabled, and slow magic is out."""
        with self.assertRaises(MagicItemError):
            build_magic_item_registry((sample_definition(), sample_definition()))

        unavailable = Mock()
        unavailable.definition_for.side_effect = MagicRegistryError("disabled")
        with self.assertRaises(MagicItemError):
            build_magic_item_registry((sample_definition(),), magic=unavailable)

        action = MAGIC_REGISTRY.definition_for("cleric.cure_wounds")
        slow = Mock()
        slow.definition_for.return_value = replace(action, cast_time=2)
        with self.assertRaises(MagicItemError):
            build_magic_item_registry((sample_definition(),), magic=slow)

        with self.assertRaises(MagicItemError):
            build_magic_item_registry((sample_definition(),), version=0)
        with self.assertRaises(MagicItemError):
            build_magic_item_registry(("not a definition",))


class TestMagicItemProfiles(EvenniaTest):
    """Authored profiles and durable state stay primitive and bounded."""

    def test_profile_bounds_and_type_agreement(self):
        """Shape, version, consumption, and item type must all agree."""
        self.assertEqual(
            validate_magic_item_profile(profile("potion.healing", 2), "potion"),
            profile("potion.healing", 2),
        )
        invalid = (
            ({"version": 1, "definition": "potion.healing"}, None),
            (profile("potion.healing") | {"extra": 1}, None),
            ({"version": 2, "definition": "potion.healing", "uses": 1}, None),
            (profile("potion.unknown"), None),
            (profile("potion.healing", 0), None),
            (profile("potion.healing", 21), None),
            (profile("potion.healing", True), None),
            (profile("scroll.magic_missile", 2), None),
            (profile("wand.magic_missiles", 1), None),
            (profile("potion.healing"), "scroll"),
        )
        for raw, item_type in invalid:
            with self.assertRaises(MagicItemError, msg=raw):
                validate_magic_item_profile(raw, item_type)
        self.assertEqual(
            validate_magic_item_profile(profile("wand.magic_missiles", 0), "wand")[
                "uses"
            ],
            0,
        )

    def test_state_projection_clamps_and_quarantines(self):
        """Malformed state is repairable data, never a silent extra portion."""
        item = magic_item(self.char1, "potion.healing", uses=2)
        self.assertEqual(magic_item_state(item)["uses"], 2)
        item.attributes.add(
            MAGIC_ITEM_STATE_ATTRIBUTE,
            {"version": 1, "uses": 9, "receipts": {}},
        )
        self.assertEqual(magic_item_state(item)["uses"], 2)
        for broken in (
            {"version": 1, "uses": 1},
            {"version": 2, "uses": 1, "receipts": {}},
            {"version": 1, "uses": 1, "receipts": {"a b": {}}},
            {
                "version": 1,
                "uses": 1,
                "receipts": {"one": {"operation": "spend", "uses": 1}},
            },
            {
                "version": 1,
                "uses": 1,
                "receipts": {"one": {"operation": "activate", "uses": 0}},
            },
        ):
            item.attributes.add(MAGIC_ITEM_STATE_ATTRIBUTE, broken)
            with self.assertRaises(MagicItemError, msg=broken):
                magic_item_state(item)

    def test_editing_a_profile_preserves_spent_portions(self):
        """Re-authoring clamps remaining portions and manufactures no new ones."""
        item = magic_item(self.char1, "potion.healing", uses=3)
        state = magic_item_state(item)
        state["uses"] = 1
        item.attributes.add(MAGIC_ITEM_STATE_ATTRIBUTE, state)
        set_magic_item_profile(item, profile("potion.healing", 5))
        self.assertEqual(magic_item_state(item)["uses"], 1)
        set_magic_item_profile(item, profile("potion.healing", 1))
        self.assertEqual(magic_item_state(item)["uses"], 1)
        with self.assertRaises(MagicItemError):
            set_magic_item_profile(item, profile("scroll.magic_missile"))

    def test_remaining_uses_reads_the_owning_resource_for_devices(self):
        """A device reports ITEM-05B charges; a broken resource reads as empty."""
        wand = magic_item(self.char1, "wand.magic_missiles", uses=0, units=2)
        self.assertEqual(remaining_uses(wand), 2)
        wand.attributes.add(RESOURCE_ATTRIBUTE, {"version": 1})
        self.assertEqual(remaining_uses(wand), 0)


class TestMagicItemActivation(EvenniaCommandTest):
    """Reservation, consumption, deletion, and replay stay exactly-once."""

    def setUp(self):
        super().setUp()
        for character in (self.char1, self.char2):
            character.db.is_player_character = True
            character.db.constitution = 10
        initialize_level_one(self.char1, class_key="Wizard", hp_base=6)
        initialize_level_one(self.char2, class_key="Cleric", hp_base=8)
        for character in (self.char1, self.char2):
            character.db.hp_max_override = 200
            character.db.hp_current = 200

    def _immediate_output(self):
        """Run committed announcements inline so one attempt is observable."""
        return patch(
            "systems.magic_items.transaction.on_commit",
            side_effect=lambda callback: callback(),
        )

    @contextmanager
    def _hostile(self):
        """Allow hostile targeting without dragging a real encounter in."""
        with patch("systems.attacks.can_attack") as can_attack, patch(
            "systems.combat.start_fight"
        ):
            can_attack.return_value.allowed = True
            yield

    def test_potion_spends_one_portion_and_deletes_on_its_last_use(self):
        """A potion heals its drinker, then disappears with its final portion."""
        item = magic_item(self.char1, "potion.healing", uses=2, name="vial")
        item_id = item.pk
        self.char1.db.hp_current = 1
        with self._immediate_output(), patch.object(self.char1, "msg") as private:
            result = activate_magic_item(self.char1, item, command="quaff")
        self.assertTrue(result.accepted)
        self.assertEqual(result.remaining, 1)
        self.assertFalse(result.consumed)
        self.assertTrue(self.char1.stats.hp_current > 1)
        self.assertIn("You quaff vial.", [call.args[0] for call in private.mock_calls])

        with self._immediate_output():
            second = activate_magic_item(self.char1, item, command="quaff")
        self.assertTrue(second.consumed)
        self.assertEqual(second.remaining, 0)
        self.assertFalse(ObjectDB.objects.filter(pk=item_id).exists())

    def test_potion_effect_and_concentration_reach_magic_04(self):
        """A bottled concentration spell installs the real effect and link."""
        item = magic_item(self.char1, "potion.blurring", name="blue vial")
        with self._immediate_output():
            result = activate_magic_item(self.char1, item, command="quaff")
        self.assertTrue(result.accepted)
        self.assertTrue(self.char1.effects.has("magic.blur"))
        self.assertIsNotNone(self.char1.attributes.get("magic_concentration"))

    def test_scroll_requires_its_known_class_action_and_is_consumed(self):
        """Only a caster who knows the spell may read it, and once only."""
        item = magic_item(self.char1, "scroll.magic_missile", name="scroll")
        item_id = item.pk
        with self.assertRaises(MagicItemError) as denied:
            activate_magic_item(
                self.char1, item, command="recite", target_name=self.char2.key
            )
        self.assertIn("do not know", str(denied.exception))
        self.assertTrue(ObjectDB.objects.filter(pk=item_id).exists())

        mark_preparation_window(self.char1, 1)
        grant_spellbook_entry(self.char1, "wizard.magic_missile")
        prepare_action(self.char1, "wizard.magic_missile")
        with self._hostile(), self._immediate_output():
            result = activate_magic_item(
                self.char1, item, command="recite", target_name=self.char2.key
            )
        self.assertTrue(result.consumed)
        self.assertFalse(ObjectDB.objects.filter(pk=item_id).exists())

    def test_wand_spends_one_charge_and_stops_at_zero(self):
        """Charges come from ITEM-05B, and an empty device never activates."""
        wand = magic_item(self.char1, "wand.magic_missiles", uses=0, units=1)
        with self._hostile(), self._immediate_output():
            result = activate_magic_item(
                self.char1, wand, command="use", target_name=self.char2.key
            )
            self.assertTrue(result.accepted)
            self.assertEqual(resource_state(wand)["current"], 0)
            with self.assertRaises(MagicItemError) as empty:
                activate_magic_item(
                    self.char1, wand, command="use", target_name=self.char2.key
                )
        self.assertIn("no charges", str(empty.exception))
        self.assertTrue(ObjectDB.objects.filter(pk=wand.pk).exists())

    def test_staff_applies_its_access_rule_and_named_creature_target(self):
        """A known-class device heals a named creature and spends one charge."""
        staff = magic_item(self.char2, "staff.healing", uses=0, units=2, name="rod")
        with self.assertRaises(MagicItemError):
            activate_magic_item(self.char2, staff, command="use")
        mark_preparation_window(self.char2, 1)
        prepare_action(self.char2, "cleric.healing_word")
        self.char1.db.hp_current = 1
        with self._immediate_output(), patch.object(self.room1, "msg_contents") as room:
            result = activate_magic_item(
                self.char2, staff, command="use", target_name=self.char1.key
            )
        self.assertTrue(result.accepted)
        self.assertEqual(resource_state(staff)["current"], 1)
        self.assertTrue(self.char1.stats.hp_current > 1)
        self.assertEqual(room.call_count, 1)

    def test_wrong_command_and_unusable_items_are_refused(self):
        """A potion cannot be recited and a dropped item cannot be activated."""
        item = magic_item(self.char1, "potion.healing", name="vial")
        with self.assertRaises(MagicItemError) as wrong:
            activate_magic_item(self.char1, item, command="recite")
        self.assertIn("cannot recite", str(wrong.exception))
        item.location = self.room1
        with self.assertRaises(MagicItemError) as away:
            activate_magic_item(self.char1, item, command="quaff")
        self.assertIn("directly carry", str(away.exception))
        self.assertEqual(magic_item_state(item)["uses"], 1)

    def test_failed_magic_releases_the_reservation_without_spending(self):
        """A denied cast spends no portion or charge and announces nothing."""
        item = magic_item(self.char1, "potion.healing", uses=2, name="vial")
        wand = magic_item(self.char1, "wand.magic_missiles", uses=0, units=2)
        failure = MagicActionError("Your magic fails to take hold.")
        with patch(
            "systems.magic_items.commit_item_action", side_effect=failure
        ), self._immediate_output(), patch.object(self.char1, "msg") as private:
            with self.assertRaises(MagicActionError):
                activate_magic_item(self.char1, item, command="quaff")
            with self._hostile():
                with self.assertRaises(MagicActionError):
                    activate_magic_item(
                        self.char1, wand, command="use", target_name=self.char2.key
                    )
        self.assertEqual(magic_item_state(item)["uses"], 2)
        self.assertEqual(resource_state(wand)["current"], 2)
        self.assertNotIn(
            "You quaff vial.", [call.args[0] for call in private.mock_calls]
        )

    def test_a_repeated_identity_spends_once_and_stays_silent(self):
        """A retry after reload repeats no effect, spend, deletion, or output."""
        item = magic_item(self.char1, "potion.healing", uses=2, name="vial")
        self.char1.db.hp_current = 1
        with self._immediate_output(), patch.object(self.char1, "msg") as private:
            first = activate_magic_item(
                self.char1, item, command="quaff", identity="retry"
            )
            healed = self.char1.stats.hp_current
            second = activate_magic_item(
                self.char1, item, command="quaff", identity="retry"
            )
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(magic_item_state(item)["uses"], 1)
        self.assertEqual(self.char1.stats.hp_current, healed)
        self.assertEqual(
            [call.args[0] for call in private.mock_calls].count("You quaff vial."), 1
        )

    def test_a_repeated_device_identity_spends_one_charge_once(self):
        """The ITEM-05B receipt suppresses a duplicate charge spend as well."""
        wand = magic_item(self.char1, "wand.magic_missiles", uses=0, units=2)
        with self._hostile(), self._immediate_output():
            with patch("systems.combat.get_encounter_id", return_value=None):
                activate_magic_item(
                    self.char1,
                    wand,
                    command="use",
                    target_name=self.char2.key,
                    identity="retry",
                )
                replay = activate_magic_item(
                    self.char1,
                    wand,
                    command="use",
                    target_name=self.char2.key,
                    identity="retry",
                )
        self.assertTrue(replay.replayed)
        self.assertEqual(resource_state(wand)["current"], 1)

    def test_a_reserved_item_rejects_a_concurrent_activation(self):
        """Two simultaneous activations cannot share one reservation lane."""
        from systems import item_resources

        item = magic_item(self.char1, "potion.healing", uses=2, name="vial")
        item_resources._ACTIVE.add(item.pk)
        self.addCleanup(item_resources._ACTIVE.discard, item.pk)
        with self.assertRaises(MagicItemError):
            activate_magic_item(self.char1, item, command="quaff")
        self.assertEqual(magic_item_state(item)["uses"], 2)

    def test_action_policy_visibility_and_combat_denials(self):
        """Sleeping, unseen targets, and combat all refuse before any spend."""
        item = magic_item(self.char1, "potion.healing", uses=2, name="vial")
        self.char1.db.position = "sleeping"
        with self.assertRaises(MagicItemError):
            activate_magic_item(self.char1, item, command="quaff")
        self.char1.db.position = "standing"

        with patch("systems.combat.get_encounter_id", return_value=7):
            with self.assertRaises(MagicItemError) as fighting:
                activate_magic_item(self.char1, item, command="quaff")
        self.assertIn("while fighting", str(fighting.exception))

        with patch("systems.visibility.target_visibility") as visibility:
            visibility.return_value.visible = False
            with self.assertRaises(MagicActionError):
                activate_magic_item(
                    self.char1, item, command="quaff", target_name=self.char2.key
                )
        self.assertEqual(magic_item_state(item)["uses"], 2)

    def test_malformed_and_unreleased_profiles_quarantine_one_item(self):
        """A broken authored item denies safely without touching its neighbours."""
        good = magic_item(self.char1, "potion.healing", name="vial")
        broken = create_object(Item, key="tainted", location=self.char1)
        broken.db.type = "potion"
        broken.attributes.add(MAGIC_ITEM_ATTRIBUTE, {"version": 1, "uses": 1})
        self.char1.db.hp_current = 1
        with self.assertRaises(MagicItemError):
            activate_magic_item(self.char1, broken, command="quaff")
        with self._immediate_output():
            self.assertTrue(
                activate_magic_item(self.char1, good, command="quaff").accepted
            )

    def test_invalid_identity_is_rejected_before_any_reservation(self):
        """Activation identities are bounded opaque strings, never content."""
        item = magic_item(self.char1, "potion.healing", uses=2)
        for identity in ("", "a b", "x" * 129, 7):
            with self.assertRaises(MagicItemError, msg=identity):
                activate_magic_item(
                    self.char1, item, command="quaff", identity=identity
                )
        with self.assertRaises(MagicItemError):
            activate_magic_item(self.char1, item, command="sip")


class TestMagicItemCommands(EvenniaCommandTest):
    """Grammar, registration, and builder round-trip through real commands."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.char1.db.constitution = 10
        initialize_level_one(self.char1, class_key="Wizard", hp_base=6)
        self.char1.db.hp_max_override = 30
        self.char1.db.hp_current = 1

    def test_commands_and_registration(self):
        """The player command set exposes all three activation entry points."""
        magic_item(self.char1, "potion.healing", name="vial")
        with patch(
            "systems.magic_items.transaction.on_commit",
            side_effect=lambda callback: callback(),
        ):
            output = self.call(CmdQuaff(), "vial")
        self.assertIn("You quaff vial.", output)
        for key in ("quaff", "recite", "use"):
            self.assertIsNotNone(CharacterCmdSet().get(key))

    def test_invalid_grammar_missing_and_ambiguous_items(self):
        """Bad grammar and unresolved names never reach the activation service."""
        self.call(CmdQuaff(), "", "Usage: quaff <potion> [on <target>]")
        self.call(CmdUseDevice(), "x" * 400, "Usage: use <item> [on <target>]")
        self.call(CmdRecite(), "nothing", "Name one magic item you are carrying.")
        for malformed in ("", "   ", "x" * 400):
            self.assertIsNone(_parse_activation(malformed), msg=malformed)
        self.assertEqual(
            _parse_activation(" blue vial on troll "), ("blue vial", "troll")
        )
        self.assertEqual(_parse_activation("blue vial"), ("blue vial", None))
        # Only the final marker splits, so a name ending in "on" stays a name
        # and a multiword item name survives ahead of the target.
        self.assertEqual(_parse_activation("vial on"), ("vial on", None))
        self.assertEqual(
            _parse_activation("vial on giant on fire"), ("vial on giant", "fire")
        )
        magic_item(self.char1, "potion.healing", name="vial")
        magic_item(self.char1, "potion.healing", name="vial")
        self.call(CmdQuaff(), "vial", "Name one magic item you are carrying.")

    def test_builder_and_prototype_round_trip(self):
        """A prototype stores only validated primitives and rejects mismatches."""
        key = "item04b_editor_test"
        self.addCleanup(delete_prototype, key)
        self.call(CmdBuild(), f"new item {key}")
        self.call(CmdBuildSet(), "type potion")
        self.call(CmdBuildSet(), "magic " + json.dumps(profile("potion.healing", 2)))
        self.assertEqual(
            self.char1.ndb._build_target[MAGIC_ITEM_ATTRIBUTE],
            profile("potion.healing", 2),
        )
        self.call(
            CmdBuildSet(),
            "magic " + json.dumps(profile("wand.magic_missiles", 0)),
            "Invalid value for 'magic'",
        )
        self.call(
            CmdBuildSet(),
            "magic " + json.dumps(profile("potion.healing", 99)),
            "Invalid value for 'magic'",
        )
        self.assertEqual(
            self.char1.ndb._build_target[MAGIC_ITEM_ATTRIBUTE],
            profile("potion.healing", 2),
        )

    def test_changing_an_item_type_clears_its_magic_state(self):
        """Retyping an item drops both the authored profile and its receipts."""
        item = magic_item(self.char1, "potion.healing", uses=2, name="vial")
        with patch(
            "systems.magic_items.transaction.on_commit",
            side_effect=lambda callback: callback(),
        ):
            activate_magic_item(self.char1, item, command="quaff", identity="typed")
        self.assertTrue(item.attributes.has(MAGIC_ITEM_STATE_ATTRIBUTE))
        _set_item_type(item, "trash")
        self.assertFalse(item.attributes.has(MAGIC_ITEM_ATTRIBUTE))
        self.assertFalse(item.attributes.has(MAGIC_ITEM_STATE_ATTRIBUTE))

    def test_devices_still_reject_a_player_refill(self):
        """ITEM-05B's charge policy is unchanged by magic-item activation."""
        from systems.item_resources import refill_resource

        wand = magic_item(self.char1, "wand.magic_missiles", uses=0, units=1)
        source = magic_item(
            self.char1, "wand.magic_missiles", uses=0, units=3, name="spare"
        )
        with self.assertRaises(ItemResourceError):
            refill_resource(self.char1, wand, source)
