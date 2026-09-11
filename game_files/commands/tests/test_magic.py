"""MAGIC-02 command and instant-action integration coverage."""

from unittest.mock import patch

from commands.default_cmdsets import CharacterCmdSet
from commands.magic import CmdAbilities, CmdCast, CmdSpells
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest
from systems.advancement import initialize_level_one
from systems.combat import is_fighting
from systems.dice import RollResult
from systems.effects import EFFECT_REGISTRY, EffectDefinition, StackingPolicy
from systems.injury import InjuryState, apply_damage, injury_record
from systems.magic import (
    MAGIC_REGISTRY,
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
from systems.magic_actions import (
    cast_action,
    end_concentration,
    grant_action,
    grant_spellbook_entry,
    mark_preparation_window,
    prepare_action,
)
from systems.magic_resources import resource_current, restore_resource
from systems.magic_rest import (
    MAGIC_REST_ATTRIBUTE,
    SAFE_REST_TAG,
    SAFE_REST_TAG_CATEGORY,
    advance_magic_rest,
)
from systems.pulses import PulseEvent, PulseLane
from systems.tactical_combat import consume_prone_action

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

    def test_released_cleric_cantrip_handlers_change_the_world(self):
        """Spare the Dying and Thaumaturgy execute their declared adapters."""
        self.char2.db.is_player_character = True
        self.char2.db.constitution = 10
        initialize_level_one(self.char2, class_key="Cleric", hp_base=8)
        self.char1.db.hp_current = 10
        with patch("systems.injury._is_staff_immune", return_value=False):
            injury = apply_damage(self.char1, 10, emit_messages=False)
        self.assertEqual(injury.state, InjuryState.DYING)

        grant_action(self.char2, "cleric.spare_the_dying", AccessMode.LEARNED)
        result = cast_action(
            self.char2,
            "spare the dying",
            target_name=self.char1.key,
            registry=MAGIC_REGISTRY,
        )
        self.assertEqual(result.reason, "stabilized")
        self.assertEqual(injury_record(self.char1).state, InjuryState.INCAPACITATED)

        grant_action(self.char2, "cleric.thaumaturgy", AccessMode.LEARNED)
        with patch.object(self.room1, "msg_contents") as room_message:
            result = cast_action(self.char2, "thaumaturgy", registry=MAGIC_REGISTRY)
        self.assertEqual(result.reason, "manifested")
        self.assertIn("phantom sound", room_message.call_args.args[0])

    def test_released_cleric_healing_snapshots_wisdom_and_spends_one_slot(self):
        """Cure Wounds uses committed potency and the Cleric slot resource."""
        self.char2.db.is_player_character = True
        self.char2.db.constitution = 10
        self.char2.db.wisdom = 16
        initialize_level_one(self.char2, class_key="Cleric", hp_base=8)
        mark_preparation_window(self.char2, 1)
        prepare_action(self.char2, "cleric.cure_wounds")
        self.char1.db.hp_max_override = 20
        self.char1.db.hp_current = 1

        with patch("systems.dice.roll", side_effect=(1, 1)):
            result = cast_action(
                self.char2,
                "cure wounds",
                target_name=self.char1.key,
                registry=MAGIC_REGISTRY,
            )

        self.assertEqual(result.amount, 5)
        self.assertEqual(result.snapshot.spellcasting_modifier, 3)
        self.assertEqual(self.char1.stats.hp_current, 6)
        self.assertEqual(resource_current(self.char2, "cleric.spell_slot.1"), 1)

    def test_released_guiding_bolt_marks_and_advances_the_next_spell_attack(self):
        """Guiding Bolt grants one attack Advantage and then consumes its mark."""
        self.char2.db.is_player_character = True
        self.char2.db.constitution = 10
        self.char2.db.wisdom = 16
        initialize_level_one(self.char2, class_key="Cleric", hp_base=8)
        mark_preparation_window(self.char2, 1)
        prepare_action(self.char2, "cleric.guiding_bolt")
        prepare_action(self.char2, "cleric.inflict_wounds")
        target = create_object(
            "typeclasses.characters.Character", key="Target", location=self.room1
        )
        target.db.is_player_character = False
        target.db.hp_max_override = 40
        target.db.hp_current = 40

        with (
            patch("systems.attacks.can_attack") as can_attack,
            patch("systems.dice.roll", side_effect=(20, 2, 3, 4, 5)),
        ):
            can_attack.return_value.allowed = True
            guiding = cast_action(
                self.char2,
                "guiding bolt",
                target_name=target.key,
                registry=MAGIC_REGISTRY,
            )

        self.assertEqual(guiding.amount, 14)
        self.assertTrue(target.effects.has("magic.guiding_bolt"))

        with (
            patch("systems.attacks.can_attack") as can_attack,
            patch("systems.dice.roll", side_effect=(2, 20, 1, 1, 1)),
        ):
            can_attack.return_value.allowed = True
            inflict = cast_action(
                self.char2,
                "inflict wounds",
                target_name=target.key,
                registry=MAGIC_REGISTRY,
            )

        self.assertEqual(inflict.reason, "hit")
        self.assertEqual(inflict.amount, 3)
        self.assertFalse(target.effects.has("magic.guiding_bolt"))
        self.assertEqual(resource_current(self.char2, "cleric.spell_slot.1"), 0)

    def test_released_aid_raises_current_and_maximum_hit_points(self):
        """Aid applies its bounded HP modifier and spends one level-two slot."""
        self.char2.db.is_player_character = True
        self.char2.db.constitution = 10
        self.char2.db.wisdom = 16
        initialize_level_one(self.char2, class_key="Cleric", hp_base=8)
        self.char2.db.level = 3
        self.char2.db.hp_max_override = 20
        self.char2.db.hp_current = 10
        mark_preparation_window(self.char2, 1)
        prepare_action(self.char2, "cleric.aid")

        result = cast_action(
            self.char2,
            "aid",
            target_name=self.char2.key,
            registry=MAGIC_REGISTRY,
        )

        self.assertEqual(result.reason, "effect_applied")
        self.assertEqual(result.amount, 5)
        self.assertEqual(self.char2.stats.hp_max, 25)
        self.assertEqual(self.char2.stats.hp_current, 15)
        self.assertEqual(resource_current(self.char2, "cleric.spell_slot.2"), 1)

    def test_released_magic_missile_hits_automatically_and_spends_one_slot(self):
        """Magic Missile bypasses attack and save rolls but still starts combat."""
        mark_preparation_window(self.char1, 1)
        grant_spellbook_entry(self.char1, "wizard.magic_missile")
        prepare_action(self.char1, "wizard.magic_missile")
        self.char2.db.hp_max_override = 20
        self.char2.db.hp_current = 20

        with patch("systems.magic_actions._roll_dice", return_value=9):
            result = cast_action(
                self.char1,
                "magic missile",
                target_name=self.char2.key,
                registry=MAGIC_REGISTRY,
            )

        self.assertEqual(result.reason, "hit")
        self.assertEqual(result.amount, 9)
        self.assertEqual(self.char2.stats.hp_current, 11)
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 1)

    def test_released_shield_of_faith_applies_concentrated_armor_class(self):
        """Shield of Faith is a linked RULES-03 effect with its declared bonus."""
        self.char2.db.is_player_character = True
        self.char2.db.constitution = 10
        self.char2.db.wisdom = 16
        initialize_level_one(self.char2, class_key="Cleric", hp_base=8)
        mark_preparation_window(self.char2, 1)
        prepare_action(self.char2, "cleric.shield_of_faith")
        armor_class = self.char2.stats.armor_class

        result = cast_action(
            self.char2,
            "shield of faith",
            target_name=self.char2.key,
            registry=MAGIC_REGISTRY,
        )

        self.assertEqual(result.reason, "effect_applied")
        self.assertTrue(self.char2.effects.has("magic.shield_of_faith"))
        self.assertEqual(self.char2.stats.armor_class, armor_class + 2)
        self.assertEqual(resource_current(self.char2, "cleric.spell_slot.1"), 1)

    def test_ritual_adept_detects_only_visible_magic_without_spending_a_slot(self):
        """An unprepared spellbook ritual is useful, bounded, and slot-free."""
        grant_spellbook_entry(self.char1, "wizard.detect_magic")
        visible = create_object(
            "typeclasses.objects.Object", key="glowing stone", location=self.room1
        )
        hidden = create_object(
            "typeclasses.objects.Object", key="hidden sigil", location=self.room1
        )
        visible.tags.add("magic")
        hidden.tags.add("magic")
        slots = resource_current(self.char1, "wizard.spell_slot.1")

        with patch.object(
            self.room1, "filter_visible", return_value=(self.char1, visible)
        ):
            result = cast_action(
                self.char1,
                "detect magic",
                registry=MAGIC_REGISTRY,
            )

        self.assertEqual(result.reason, "detected")
        self.assertEqual(result.amount, 1)
        self.assertEqual(dict(result.snapshot.resource_reservation), {})
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), slots)
        self.assertTrue(self.char1.effects.has("magic.detect_magic"))

        end_concentration(self.char1)
        mark_preparation_window(self.char1, 1)
        prepare_action(self.char1, "wizard.detect_magic")
        with patch.object(self.room1, "filter_visible", return_value=()):
            prepared = cast_action(
                self.char1,
                "detect magic",
                registry=MAGIC_REGISTRY,
            )
        self.assertEqual(
            dict(prepared.snapshot.resource_reservation),
            {"wizard.spell_slot.1": 1},
        )
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), slots - 1)

    def test_released_longstrider_applies_speed_without_concentration(self):
        """Longstrider persists through RULES-03 and spends exactly one slot."""
        mark_preparation_window(self.char1, 1)
        grant_spellbook_entry(self.char1, "wizard.longstrider")
        prepare_action(self.char1, "wizard.longstrider")
        speed = self.char1.stats.speed

        result = cast_action(
            self.char1,
            "longstrider",
            target_name=self.char1.key,
            registry=MAGIC_REGISTRY,
        )

        self.assertEqual(result.reason, "effect_applied")
        self.assertTrue(self.char1.effects.has("magic.longstrider"))
        self.assertEqual(self.char1.stats.speed, speed + 10)
        self.assertIsNone(self.char1.attributes.get("magic_concentration"))
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 1)

    def test_released_grease_applies_and_consumes_prone_in_combat(self):
        """Failed Grease saves enter combat and cost the target one ready action."""
        mark_preparation_window(self.char1, 1)
        grant_spellbook_entry(self.char1, "wizard.grease")
        prepare_action(self.char1, "wizard.grease")
        failed = RollResult(1, 0, 1, 10, False)

        with patch("systems.effects.roll_check", return_value=failed):
            result = cast_action(
                self.char1,
                "grease",
                target_name=self.char2.key,
                registry=MAGIC_REGISTRY,
            )

        self.assertEqual(result.reason, "effect_applied")
        self.assertTrue(self.char2.effects.has("combat.prone"))
        self.assertTrue(is_fighting(self.char1))
        self.assertTrue(consume_prone_action(self.char2))
        self.assertFalse(self.char2.effects.has("combat.prone"))
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 1)

    def test_released_acid_arrow_deals_miss_damage_and_spends_level_two_slot(self):
        """Acid Arrow deals its reduced miss damage through canonical injury."""
        self.char1.db.level = 3
        mark_preparation_window(self.char1, 1)
        grant_spellbook_entry(self.char1, "wizard.acid_arrow")
        prepare_action(self.char1, "wizard.acid_arrow")
        self.char2.db.hp_max_override = 20
        self.char2.db.hp_current = 20

        with patch("systems.dice.roll", side_effect=(1, 2, 3)):
            result = cast_action(
                self.char1,
                "acid arrow",
                target_name=self.char2.key,
                registry=MAGIC_REGISTRY,
            )

        self.assertEqual(result.reason, "miss")
        self.assertEqual(result.amount, 5)
        self.assertEqual(self.char2.stats.hp_current, 15)
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.2"), 1)

    def test_released_scorching_ray_resolves_three_independent_attacks(self):
        """Scorching Ray totals damage only for rays that hit one alpha target."""
        self.char1.db.level = 3
        mark_preparation_window(self.char1, 1)
        grant_spellbook_entry(self.char1, "wizard.scorching_ray")
        prepare_action(self.char1, "wizard.scorching_ray")
        self.char2.db.hp_max_override = 30
        self.char2.db.hp_current = 30

        with patch("systems.dice.roll", side_effect=(20, 1, 15, 3, 4, 5, 6)):
            result = cast_action(
                self.char1,
                "scorching ray",
                target_name=self.char2.key,
                registry=MAGIC_REGISTRY,
            )

        self.assertEqual(result.amount, 18)
        self.assertEqual(self.char2.stats.hp_current, 12)
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.2"), 1)

    def test_released_shatter_halves_damage_on_a_successful_save(self):
        """Shatter applies its declared Constitution save to level-two damage."""
        self.char1.db.level = 3
        mark_preparation_window(self.char1, 1)
        grant_spellbook_entry(self.char1, "wizard.shatter")
        prepare_action(self.char1, "wizard.shatter")
        self.char2.db.hp_max_override = 30
        self.char2.db.hp_current = 30
        saved = RollResult(20, 0, 20, 10, True)

        with (
            patch("systems.dice.roll_check", return_value=saved),
            patch("systems.magic_actions._roll_dice", return_value=15),
        ):
            result = cast_action(
                self.char1,
                "shatter",
                target_name=self.char2.key,
                registry=MAGIC_REGISTRY,
            )

        self.assertEqual(result.reason, "saved")
        self.assertEqual(result.amount, 7)
        self.assertEqual(self.char2.stats.hp_current, 23)
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.2"), 1)

    def test_released_blur_imposes_disadvantage_on_spell_attacks(self):
        """Blur links concentration and forces spell attacks to roll twice."""
        self.char1.db.level = 3
        mark_preparation_window(self.char1, 1)
        grant_spellbook_entry(self.char1, "wizard.blur")
        prepare_action(self.char1, "wizard.blur")
        result = cast_action(self.char1, "blur", registry=MAGIC_REGISTRY)

        self.assertEqual(result.reason, "effect_applied")
        self.assertTrue(self.char1.effects.has("magic.blur"))
        self.assertIsNotNone(self.char1.attributes.get("magic_concentration"))
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.2"), 1)

        self.char2.db.is_player_character = True
        self.char2.db.constitution = 10
        initialize_level_one(self.char2, class_key="Wizard", hp_base=6)
        grant_action(self.char2, "wizard.fire_bolt", AccessMode.LEARNED)
        self.char2.db.is_player_character = False
        self.char1.db.is_player_character = False
        hit_points = self.char1.stats.hp_current
        with (
            patch("systems.attacks.can_attack") as can_attack,
            patch("systems.dice.roll", side_effect=(20, 1)),
        ):
            can_attack.return_value.allowed = True
            attack = cast_action(
                self.char2,
                "fire bolt",
                target_name=self.char1.key,
                registry=MAGIC_REGISTRY,
            )

        self.assertEqual(attack.reason, "miss")
        self.assertEqual(self.char1.stats.hp_current, hit_points)
