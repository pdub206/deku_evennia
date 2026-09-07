"""MAGIC-02 command and instant-action integration coverage."""

from unittest.mock import patch

from commands.default_cmdsets import CharacterCmdSet
from commands.magic import CmdAbilities, CmdCast, CmdSpells
from evennia.utils.test_resources import EvenniaCommandTest
from systems.advancement import initialize_level_one
from systems.effects import EFFECT_REGISTRY, EffectDefinition, StackingPolicy
from systems.dice import RollResult
from systems.magic import (
    AccessMode,
    ClassAccess,
    Damage,
    DiceExpression,
    MagicDefinition,
    MagicKind,
    PlayerHelp,
    RangeCategory,
    ResourceCost,
    Save,
    Targeting,
    TargetingMode,
    build_magic_registry,
)
from systems.magic_actions import cast_action, grant_action
from systems.magic_resources import resource_current, restore_resource

_WARD_EFFECT = EffectDefinition(
    key="test.magic.ward",
    name="Test Magic Ward",
    duration=9,
    stacking=StackingPolicy.REJECT,
    modifiers={"armor_class": 1},
    removal_categories=frozenset({"magic"}),
)
if EFFECT_REGISTRY.get(_WARD_EFFECT.key) is None:
    EFFECT_REGISTRY.register(_WARD_EFFECT)

_SAVE_EFFECT = EffectDefinition(
    key="test.magic.resisted",
    name="Test Resisted Effect",
    duration=9,
    stacking=StackingPolicy.INDEPENDENT,
)
if EFFECT_REGISTRY.get(_SAVE_EFFECT.key) is None:
    EFFECT_REGISTRY.register(_SAVE_EFFECT)


def _registry():
    """Build one released-shaped, safe utility action for command tests."""
    action = MagicDefinition(
        key="wizard.spark",
        display_name="Spark",
        aliases=("spark",),
        kind=MagicKind.SPELL,
        school="evocation",
        tags=("arcane",),
        class_access=(ClassAccess("Wizard", 1),),
        access_modes=(AccessMode.LEARNED,),
        action_category="manipulate",
        handler_key="utility",
        targeting=Targeting(TargetingMode.SELF, include_caster=True),
        range=RangeCategory.SELF,
        cost=ResourceCost("wizard.arcane_recovery", 1),
        player_help=PlayerHelp("spark", "A harmless spark."),
    )
    return build_magic_registry(
        (action,),
        class_keys=("Wizard",),
        resource_keys=("wizard.arcane_recovery",),
        help_keys=("spark",),
    )


def _effect_registry():
    """Build one persistent-effect action with the same test-safe resource."""
    action = MagicDefinition(
        key="wizard.test_ward",
        display_name="Test Ward",
        aliases=("ward",),
        kind=MagicKind.SPELL,
        school="abjuration",
        tags=("arcane",),
        class_access=(ClassAccess("Wizard", 1),),
        access_modes=(AccessMode.LEARNED,),
        action_category="manipulate",
        handler_key="effect",
        targeting=Targeting(TargetingMode.SELF, include_caster=True),
        range=RangeCategory.SELF,
        cost=ResourceCost("wizard.arcane_recovery", 1),
        duration=3,
        effect_keys=(_WARD_EFFECT.key,),
        player_help=PlayerHelp("test ward", "A test ward."),
    )
    return build_magic_registry(
        (action,),
        class_keys=("Wizard",),
        resource_keys=("wizard.arcane_recovery",),
        effect_keys=(_WARD_EFFECT.key,),
        help_keys=("test ward",),
    )


def _saving_throw_registry():
    """Build a single-target, effect-backed saving-throw action for tests."""
    action = MagicDefinition(
        key="wizard.test_resisted",
        display_name="Test Resisted",
        aliases=("resisted",),
        kind=MagicKind.SPELL,
        school="enchantment",
        tags=("arcane",),
        class_access=(ClassAccess("Wizard", 1),),
        access_modes=(AccessMode.LEARNED,),
        action_category="manipulate",
        handler_key="saving_throw",
        targeting=Targeting(TargetingMode.CREATURE),
        range=RangeCategory.ROOM,
        cost=ResourceCost("wizard.arcane_recovery", 1),
        save=Save("Wisdom"),
        duration=3,
        effect_keys=(_SAVE_EFFECT.key,),
        player_help=PlayerHelp("test resisted", "A resisted test effect."),
    )
    return build_magic_registry(
        (action,),
        class_keys=("Wizard",),
        resource_keys=("wizard.arcane_recovery",),
        effect_keys=(_SAVE_EFFECT.key,),
        help_keys=("test resisted",),
    )


def _damage_save_registry():
    """Build a hostile, direct-damage saving-throw action for tests."""
    action = MagicDefinition(
        key="wizard.test_burst",
        display_name="Test Burst",
        aliases=("burst",),
        kind=MagicKind.SPELL,
        school="evocation",
        tags=("arcane",),
        class_access=(ClassAccess("Wizard", 1),),
        access_modes=(AccessMode.LEARNED,),
        action_category="combat",
        handler_key="saving_throw",
        targeting=Targeting(TargetingMode.HOSTILE),
        range=RangeCategory.ROOM,
        cost=ResourceCost("wizard.arcane_recovery", 1),
        damage=Damage(DiceExpression(1, 8), "force"),
        save=Save("Dexterity", on_success="half"),
        player_help=PlayerHelp("test burst", "A resisted burst of force."),
    )
    return build_magic_registry(
        (action,),
        class_keys=("Wizard",),
        resource_keys=("wizard.arcane_recovery",),
        damage_types=("force",),
        help_keys=("test burst",),
    )


class TestMagicCommands(EvenniaCommandTest):
    """Commands use one entitlement and resource service rather than strings."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.char1.db.constitution = 10
        initialize_level_one(self.char1, class_key="Wizard", hp_base=6)
        self.registry = _registry()

    def test_cmdset_registers_all_magic_commands(self):
        """The player command set exposes the three MAGIC-02 entry points."""
        cmdset = CharacterCmdSet()
        cmdset.at_cmdset_creation()
        keys = {command.key for command in cmdset.commands}
        self.assertTrue({"cast", "spells", "abilities"} <= keys)

    def test_listing_hides_unlearned_actions_and_cast_spends_once(self):
        """A known action lists and resolves through its declared cost once."""
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            self.assertIn("None.", self.call(CmdSpells(), ""))
            grant_action(self.char1, "wizard.spark", AccessMode.LEARNED)
            self.assertIn("Spark", self.call(CmdSpells(), ""))
            self.assertEqual(resource_current(self.char1, "wizard.arcane_recovery"), 1)
            self.assertIn("You cast", self.call(CmdCast(), "spark"))
            self.assertEqual(resource_current(self.char1, "wizard.arcane_recovery"), 0)
            self.assertIn("enough magical resources", self.call(CmdCast(), "spark"))

    def test_target_grammar_and_kind_specific_lists_fail_safely(self):
        """Target text is explicit, and spells never leak into ability listings."""
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            grant_action(self.char1, "wizard.spark", AccessMode.LEARNED)
            self.assertIn("only target you", self.call(CmdCast(), "spark at Char2"))
            self.assertIn("None.", self.call(CmdAbilities(), ""))

    def test_effect_actions_use_rules03_storage_and_reject_without_spending(self):
        """Effect actions retain source, duration, and RULES-03 stacking policy."""
        with patch("systems.magic.MAGIC_REGISTRY", _effect_registry()):
            grant_action(self.char1, "wizard.test_ward", AccessMode.LEARNED)
            self.assertIn("You cast", self.call(CmdCast(), "ward"))
            active = next(
                effect
                for effect in self.char1.effects.all()
                if effect.key == _WARD_EFFECT.key
            )
            self.assertEqual(active.source, self.char1)
            self.assertEqual(active.source_key, "wizard.test_ward")
            self.assertEqual(active.remaining_pulses, 3)
            self.assertEqual(resource_current(self.char1, "wizard.arcane_recovery"), 0)

            restore_resource(self.char1, "wizard.arcane_recovery", 1)
            self.assertIn("already active", self.call(CmdCast(), "ward"))
            self.assertEqual(resource_current(self.char1, "wizard.arcane_recovery"), 1)

    def test_saving_throw_effects_snapshot_the_dc_and_apply_only_on_failure(self):
        """A saving-throw action delegates its save and storage to RULES-03."""
        with patch("systems.magic.MAGIC_REGISTRY", _saving_throw_registry()):
            grant_action(self.char1, "wizard.test_resisted", AccessMode.LEARNED)
            saved = RollResult(20, 0, 20, 10, True)
            with patch("systems.effects.roll_check", return_value=saved):
                result = cast_action(
                    self.char1,
                    "resisted",
                    target_name=self.char2.key,
                )
            self.assertEqual(result.reason, "saved")
            self.assertFalse(self.char2.effects.has(_SAVE_EFFECT.key))
            self.assertEqual(resource_current(self.char1, "wizard.arcane_recovery"), 0)

            restore_resource(self.char1, "wizard.arcane_recovery", 1)
            failed = RollResult(1, 0, 1, 10, False)
            with patch("systems.effects.roll_check", return_value=failed):
                result = cast_action(
                    self.char1,
                    "resisted",
                    target_name=self.char2.key,
                )
            effect = next(
                active
                for active in self.char2.effects.all()
                if active.key == _SAVE_EFFECT.key
            )
            self.assertEqual(result.reason, "effect_applied")
            self.assertEqual(effect.save.dc, result.snapshot.save_dc)

    def test_saving_throw_damage_applies_its_declared_success_outcome(self):
        """A hostile save halves direct damage before canonical injury handling."""
        self.char2.db.hp_max_override = 20
        self.char2.db.hp_current = 20
        with patch("systems.magic.MAGIC_REGISTRY", _damage_save_registry()):
            grant_action(self.char1, "wizard.test_burst", AccessMode.LEARNED)
            saved = RollResult(20, 0, 20, 10, True)
            with (
                patch("systems.dice.roll_check", return_value=saved),
                patch("systems.magic_actions._roll_dice", return_value=7),
            ):
                result = cast_action(
                    self.char1,
                    "burst",
                    target_name=self.char2.key,
                )
        self.assertEqual(result.reason, "saved")
        self.assertEqual(result.amount, 3)
        self.assertEqual(self.char2.stats.hp_current, 17)
