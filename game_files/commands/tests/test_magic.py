"""MAGIC-02 command and instant-action integration coverage."""

from unittest.mock import patch

from commands.default_cmdsets import CharacterCmdSet
from commands.magic import CmdAbilities, CmdCast, CmdSpells, _parse_cast
from evennia.utils.test_resources import EvenniaCommandTest
from systems.advancement import initialize_level_one
from systems.dice import RollResult
from systems.effects import EFFECT_REGISTRY, EffectDefinition, StackingPolicy
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
    Scaling,
    Targeting,
    TargetingMode,
    build_magic_registry,
)
from systems.magic_actions import cast_action, grant_action
from systems.magic_resources import recover_profile, resource_current, restore_resource
from systems.magic_rest import (
    MAGIC_REST_ATTRIBUTE,
    SAFE_REST_TAG,
    SAFE_REST_TAG_CATEGORY,
    advance_magic_rest,
)
from systems.pulses import PulseEvent, PulseLane

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

_CURABLE_EFFECT = EffectDefinition(
    key="test.magic.curable",
    name="Test Curable Effect",
    duration=9,
    removal_categories=frozenset({"condition.poisoned"}),
)
_UNCURABLE_EFFECT = EffectDefinition(
    key="test.magic.uncurable",
    name="Test Uncurable Effect",
    duration=9,
    removal_categories=frozenset({"condition.frightened"}),
)
for _effect in (_CURABLE_EFFECT, _UNCURABLE_EFFECT):
    if EFFECT_REGISTRY.get(_effect.key) is None:
        EFFECT_REGISTRY.register(_effect)


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
        cost=ResourceCost("wizard.spell_slot.1", 1),
        player_help=PlayerHelp("spark", "A harmless spark."),
    )
    return build_magic_registry(
        (action,),
        class_keys=("Wizard",),
        resource_keys=("wizard.spell_slot.1",),
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
        cost=ResourceCost("wizard.spell_slot.1", 1),
        duration=3,
        effect_keys=(_WARD_EFFECT.key,),
        player_help=PlayerHelp("test ward", "A test ward."),
    )
    return build_magic_registry(
        (action,),
        class_keys=("Wizard",),
        resource_keys=("wizard.spell_slot.1",),
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
        cost=ResourceCost("wizard.spell_slot.1", 1),
        save=Save("Wisdom"),
        duration=3,
        effect_keys=(_SAVE_EFFECT.key,),
        player_help=PlayerHelp("test resisted", "A resisted test effect."),
    )
    return build_magic_registry(
        (action,),
        class_keys=("Wizard",),
        resource_keys=("wizard.spell_slot.1",),
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
        cost=ResourceCost("wizard.spell_slot.1", 1),
        damage=Damage(DiceExpression(1, 8), "force"),
        save=Save("Dexterity", on_success="half"),
        player_help=PlayerHelp("test burst", "A resisted burst of force."),
    )
    return build_magic_registry(
        (action,),
        class_keys=("Wizard",),
        resource_keys=("wizard.spell_slot.1",),
        damage_types=("force",),
        help_keys=("test burst",),
    )


def _removal_registry():
    """Build one targeted cure bridge without registering SRD content yet."""
    action = MagicDefinition(
        key="wizard.test_cleanse",
        display_name="Test Cleanse",
        aliases=("cleanse",),
        kind=MagicKind.SPELL,
        school="abjuration",
        tags=("test",),
        class_access=(ClassAccess("Wizard", 1),),
        access_modes=(AccessMode.LEARNED,),
        action_category="manipulate",
        handler_key="removal",
        targeting=Targeting(TargetingMode.CREATURE),
        range=RangeCategory.ROOM,
        cost=ResourceCost("wizard.spell_slot.1", 1),
        removal_categories=("condition.poisoned",),
        removal_reason="cured",
        player_help=PlayerHelp("test cleanse", "A targeted effect-removal test."),
    )
    return build_magic_registry(
        (action,),
        class_keys=("Wizard",),
        resource_keys=("wizard.spell_slot.1",),
        help_keys=("test cleanse",),
    )


def _slot_registry(class_key: str = "Wizard"):
    """Build a level-one slot spell for casting-resource integration tests."""
    action = MagicDefinition(
        key=f"{class_key.casefold()}.test_slot_spell",
        display_name="Test Slot Spell",
        aliases=("slot spell",),
        kind=MagicKind.SPELL,
        school="evocation",
        tags=("arcane",),
        class_access=(ClassAccess(class_key, 1),),
        access_modes=(AccessMode.INNATE,),
        action_category="manipulate",
        handler_key="utility",
        targeting=Targeting(TargetingMode.SELF, include_caster=True),
        range=RangeCategory.SELF,
        spell_level=1,
        uses_spell_slot=True,
        player_help=PlayerHelp("test slot spell", "A slot-cost test spell."),
    )
    return build_magic_registry(
        (action,), class_keys=(class_key,), help_keys=("test slot spell",)
    )


def _scaled_slot_healing_registry():
    """Build a declared upcast healing spell for cast-level scaling coverage."""
    action = MagicDefinition(
        key="wizard.test_scaled_healing",
        display_name="Test Scaled Healing",
        aliases=("scaled healing",),
        kind=MagicKind.SPELL,
        school="evocation",
        tags=("arcane",),
        class_access=(ClassAccess("Wizard", 1),),
        access_modes=(AccessMode.INNATE,),
        action_category="manipulate",
        handler_key="healing",
        targeting=Targeting(TargetingMode.SELF, include_caster=True),
        range=RangeCategory.SELF,
        spell_level=1,
        uses_spell_slot=True,
        healing=DiceExpression(1, 4),
        scaling=Scaling(levels=(2, 3), healing_per_step=1),
        player_help=PlayerHelp("test scaled healing", "A scaling test spell."),
    )
    return build_magic_registry(
        (action,), class_keys=("Wizard",), help_keys=("test scaled healing",)
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
            self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 2)
            self.assertIn("You cast", self.call(CmdCast(), "spark"))
            self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 1)
            self.assertIn("You cast", self.call(CmdCast(), "spark"))
            self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 0)
            self.assertIn("enough magical resources", self.call(CmdCast(), "spark"))

    def test_completed_spellcasting_interrupts_safe_rest_progress(self):
        """A committed spell cannot leave pre-cast rest credit intact."""
        self.room1.tags.add(SAFE_REST_TAG, category=SAFE_REST_TAG_CATEGORY)
        self.char1.db.position = "resting"
        advance_magic_rest(self.char1, PulseEvent(60, PulseLane.RECOVERY, 1))
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            grant_action(self.char1, "wizard.spark", AccessMode.LEARNED)
            result = cast_action(self.char1, "spark")

        self.assertTrue(result.accepted)
        self.assertEqual(
            self.char1.attributes.get(MAGIC_REST_ATTRIBUTE)["continuous_pulses"], 0
        )

    def test_target_grammar_and_kind_specific_lists_fail_safely(self):
        """Positional targets stay safe, and spells never leak into abilities."""
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            grant_action(self.char1, "wizard.spark", AccessMode.LEARNED)
            self.assertIn("only target you", self.call(CmdCast(), "'spark' Char2"))
            self.assertIn("None.", self.call(CmdAbilities(), ""))

    def test_slot_casts_snapshot_and_spend_the_selected_spell_slot(self):
        """Leveled spells reserve one ordinary slot instead of a generic resource."""
        with patch("systems.magic.MAGIC_REGISTRY", _slot_registry()):
            grant_action(self.char1, "wizard.test_slot_spell", AccessMode.INNATE)
            result = cast_action(self.char1, "slot spell", slot_level=1)

        self.assertEqual(result.snapshot.cast_level, 1)
        self.assertEqual(
            dict(result.snapshot.resource_reservation), {"wizard.spell_slot.1": 1}
        )
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 1)

    def test_slot_cast_grammar_is_positional_and_bounded(self):
        """The command accepts positional level and target without marker words."""
        self.assertEqual(
            _parse_cast("'slot spell' 2 Char2"), ("slot spell", "Char2", 2)
        )
        self.assertEqual(
            _parse_cast("'slot spell' Char2"), ("slot spell", "Char2", None)
        )
        self.assertIsNone(_parse_cast("'slot spell' 10"))
        self.assertIsNone(_parse_cast("slot spell using 2"))

    def test_slot_cast_uses_warlock_pact_magic_at_its_table_level(self):
        """Pact Magic casts cannot spend an ordinary slot or choose its level."""
        self.char1.db.char_class = "Warlock"
        self.char1.db.level = 5
        with patch("systems.magic.MAGIC_REGISTRY", _slot_registry("Warlock")):
            grant_action(self.char1, "warlock.test_slot_spell", AccessMode.INNATE)
            result = cast_action(self.char1, "slot spell", slot_level=3)

        self.assertEqual(result.snapshot.cast_level, 3)
        self.assertEqual(
            dict(result.snapshot.resource_reservation), {"warlock.pact_slot": 1}
        )

    def test_slot_level_applies_only_its_declared_healing_thresholds(self):
        """An upcast uses its snapshot level, never an undeclared bonus formula."""
        self.char1.db.level = 5
        self.char1.db.hp_current = 1
        with patch("systems.magic.MAGIC_REGISTRY", _scaled_slot_healing_registry()):
            grant_action(self.char1, "wizard.test_scaled_healing", AccessMode.INNATE)
            with patch("systems.magic_actions._roll_dice", return_value=7) as roller:
                result = cast_action(self.char1, "scaled healing", slot_level=3)

        self.assertEqual(result.snapshot.cast_level, 3)
        self.assertEqual(result.amount, 7)
        roller.assert_called_once_with(DiceExpression(3, 4))

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
            self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 1)

            restore_resource(self.char1, "wizard.spell_slot.1", 1)
            self.assertIn("already active", self.call(CmdCast(), "ward"))
            self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 2)

    def test_removal_actions_use_effect_categories_without_touching_other_effects(self):
        """The P-03 cure bridge delegates authorization and cleanup to RULES-03."""
        self.char2.effects.add(_CURABLE_EFFECT.key, source=self.char1, quiet=True)
        self.char2.effects.add(_UNCURABLE_EFFECT.key, source=self.char1, quiet=True)
        with patch("systems.magic.MAGIC_REGISTRY", _removal_registry()):
            grant_action(self.char1, "wizard.test_cleanse", AccessMode.LEARNED)
            result = cast_action(self.char1, "cleanse", target_name=self.char2.key)
            self.assertTrue(result.accepted)
            self.assertEqual((result.reason, result.amount), ("effects_removed", 1))
            self.assertFalse(self.char2.effects.has(_CURABLE_EFFECT.key))
            self.assertTrue(self.char2.effects.has(_UNCURABLE_EFFECT.key))
            self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 1)

            result = cast_action(self.char1, "cleanse", target_name=self.char2.key)
            self.assertTrue(result.accepted)
            self.assertEqual((result.reason, result.amount), ("no_matching_effect", 0))
            self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 0)

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
            self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 1)

            restore_resource(self.char1, "wizard.spell_slot.1", 1)
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


class TestSecondWindRelease(EvenniaCommandTest):
    """The first P-04 release follows progression through execution and recovery."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.char1.db.constitution = 10
        initialize_level_one(self.char1, class_key="Fighter", hp_base=10)
        self.char1.db.hp_current = 1

    def test_automatic_fighter_feature_heals_and_uses_its_own_resource(self):
        """Second Wind is granted at level one, not selected or manually injected."""
        self.assertIn("Second Wind", self.call(CmdAbilities(), ""))
        self.assertEqual(resource_current(self.char1, "fighter.second_wind"), 2)
        with patch("systems.magic_actions._roll_dice", return_value=7):
            result = cast_action(self.char1, "second wind")
        self.assertTrue(result.accepted)
        self.assertEqual((result.reason, result.amount), ("healed", 8))
        self.assertEqual(self.char1.stats.hp_current, 9)
        self.assertEqual(resource_current(self.char1, "fighter.second_wind"), 1)

        with patch("systems.magic_actions._roll_dice", return_value=7):
            self.assertTrue(cast_action(self.char1, "second wind").accepted)
        self.assertEqual(resource_current(self.char1, "fighter.second_wind"), 0)
        self.assertEqual(
            recover_profile(self.char1, "short_rest"),
            (("fighter.second_wind", 1),),
        )
        self.assertEqual(
            recover_profile(self.char1, "long_rest"),
            (("fighter.second_wind", 2),),
        )
