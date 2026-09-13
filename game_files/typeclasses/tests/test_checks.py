"""ADV-04 coverage for the project-owned action-check service."""

from unittest.mock import patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.checks import (
    CheckError,
    CheckRequest,
    RollMode,
    active_detection,
    passive_check,
    resolve_check,
    resolve_opposed_check,
    resolve_saving_throw,
    stealth_against_passive,
    validate_dc,
)
from systems.effects import EFFECT_REGISTRY, EffectDefinition
from world.chargen_data import ABILITY_NAMES, SKILLS

_CHECK_EFFECT = EffectDefinition(
    key="test.adv04.check_bonus",
    name="Check Bonus",
    modifiers={"check_bonus": 2},
)
if EFFECT_REGISTRY.get(_CHECK_EFFECT.key) is None:
    EFFECT_REGISTRY.register(_CHECK_EFFECT)


class TestChecks(EvenniaTest):
    """Checks calculate transparent, side-effect-free shared mechanics."""

    def setUp(self):
        super().setUp()
        self.char1.db.strength = 14
        self.char1.db.dexterity = 14
        self.char1.db.wisdom = 14

    def test_skill_proficiency_and_expertise_are_not_duplicate_entries(self):
        self.char1.db.skill_proficiencies = ["Athletics", "Athletics"]
        result = resolve_check(
            CheckRequest(self.char1, "Strength", 10, skill="Athletics"),
            roller=lambda _: 7,
        )
        expertise = resolve_check(
            CheckRequest(
                self.char1, "Strength", 10, skill="Athletics", expertise_multiplier=2
            ),
            roller=lambda _: 4,
        )
        self.assertEqual(
            (result.ability_modifier, result.proficiency_contribution), (2, 2)
        )
        self.assertEqual(expertise.proficiency_contribution, 4)
        self.assertTrue(result.success)

        self.char1.db.skill_expertise = ["Athletics"]
        derived = resolve_check(
            CheckRequest(self.char1, "Strength", 10, skill="Athletics"),
            roller=lambda _: 4,
        )
        self.assertEqual(derived.proficiency_contribution, 4)

    def test_alternate_ability_sources_and_normal_check_outcomes(self):
        with self.assertRaises(CheckError):
            resolve_check(CheckRequest(self.char1, "Strength", 10, skill="Stealth"))
        self.char1.db.stat_modifiers = {"check_bonus": 1, "skill:stealth": 2}
        result = resolve_check(
            CheckRequest(
                self.char1,
                "Wisdom",
                15,
                skill="Stealth",
                alternate_ability=True,
                advantage_sources=("help", "help"),
                disadvantage_sources=("armor",),
            ),
            roller=lambda _: 12,
        )
        self.assertEqual(result.roll_mode, RollMode.CANCELLED)
        self.assertEqual(result.other_modifiers, 3)
        self.assertEqual(result.total, 17)
        self.assertTrue(result.success)

    def test_passive_and_opposed_ties_preserve_status_quo(self):
        self.char1.db.skill_proficiencies = ["Perception"]
        passive = passive_check(
            self.char1,
            ability="Wisdom",
            skill="Perception",
            disadvantage_sources=("dim",),
        )
        self.assertEqual(passive.roll_mode, RollMode.PASSIVE)
        self.assertEqual(passive.total, 9)
        self.char2.db.strength = 14
        tied = resolve_opposed_check(
            CheckRequest(self.char1, "Strength", 5, action_key="push"),
            CheckRequest(self.char2, "Strength", 5, action_key="resist"),
            roller=lambda _: 10,
        )
        self.assertTrue(tied.tie)
        self.assertFalse(tied.actor_wins)
        detected = active_detection(self.char1, self.char2, roller=lambda _: 10)
        self.assertIsInstance(detected.actor_wins, bool)

    def test_dc_is_bounded_and_checks_do_not_mutate_actor(self):
        with self.assertRaises(CheckError):
            validate_dc(31)
        before = tuple(
            (attribute.key, attribute.value)
            for attribute in self.char1.attributes.all()
        )
        resolve_check(CheckRequest(self.char1, "Strength", 10), roller=lambda _: 20)
        after = tuple(
            (attribute.key, attribute.value)
            for attribute in self.char1.attributes.all()
        )
        self.assertEqual(after, before)

    def test_saving_throw_uses_class_proficiency_and_allows_damage_dc(self):
        """Saving throws retain their own proficiency and extended SRD DC range."""
        self.char1.db.constitution = 14
        result = resolve_saving_throw(
            self.char1,
            "Constitution",
            50,
            action_key="concentration",
            roller=lambda _: 20,
        )
        self.assertEqual(result.ability, "Constitution")
        self.assertEqual(result.target_dc, 50)
        self.assertEqual(result.proficiency_contribution, 2)
        self.assertFalse(result.success)

    def test_every_ability_and_skill_mapping_is_accepted(self):
        """Canonical chargen names all resolve through the shared check map."""
        for ability in ABILITY_NAMES:
            result = resolve_check(
                CheckRequest(self.char1, ability, 10), roller=lambda _: 10
            )
            self.assertEqual(result.ability, ability)
        for skill, ability in SKILLS.items():
            result = resolve_check(
                CheckRequest(self.char1, ability, 10, skill=skill),
                roller=lambda _: 10,
            )
            self.assertEqual((result.skill, result.ability), (skill, ability))

    def test_tool_and_skill_proficiency_contribute_only_once(self):
        """Two applicable proficiency sources cannot double the same bonus."""
        self.char1.db.skill_proficiencies = ["Athletics"]
        self.char1.db.tool_proficiencies = ["Climber's Kit", "Climber's Kit"]
        result = resolve_check(
            CheckRequest(
                self.char1,
                "Strength",
                10,
                skill="Athletics",
                tool="Climber's Kit",
            ),
            roller=lambda _: 10,
        )
        self.assertEqual(result.proficiency_contribution, 2)

    def test_roll_modes_natural_results_and_dc_equality(self):
        """All d20 modes preserve check equality and lack attack auto-outcomes."""
        advantage_rolls = iter((1, 20))
        advantage = resolve_check(
            CheckRequest(self.char1, "Strength", 22, advantage_sources=("help",)),
            roller=lambda _: next(advantage_rolls),
        )
        self.char1.db.strength = 30
        disadvantage_rolls = iter((20, 1))
        disadvantage = resolve_check(
            CheckRequest(
                self.char1, "Strength", 5, disadvantage_sources=("hindrance",)
            ),
            roller=lambda _: next(disadvantage_rolls),
        )
        equality = resolve_check(
            CheckRequest(self.char1, "Strength", 20), roller=lambda _: 10
        )

        self.assertEqual(advantage.roll_mode, RollMode.ADVANTAGE)
        self.assertTrue(advantage.success)
        self.assertEqual(disadvantage.roll_mode, RollMode.DISADVANTAGE)
        self.assertTrue(disadvantage.success)
        self.assertTrue(equality.success)
        self.assertEqual(equality.total, equality.target_dc)

    def test_malformed_proficiencies_are_ignored_and_logged(self):
        """Malformed legacy data cannot grant a bonus or expose its contents."""
        self.char1.db.skill_proficiencies = "Athletics"
        with patch("systems.checks.logger.log_warn") as warning:
            result = resolve_check(
                CheckRequest(self.char1, "Strength", 10, skill="Athletics"),
                roller=lambda _: 10,
            )
        warning.assert_called_once()
        self.assertEqual(result.proficiency_contribution, 0)

    def test_pc_and_npc_use_identical_check_calculation(self):
        """The durable PC marker never changes check mathematics."""
        self.char1.db.is_player_character = True
        self.char2.db.is_player_character = False
        for actor in (self.char1, self.char2):
            actor.db.strength = 14
            actor.db.skill_proficiencies = ["Athletics"]
        pc = resolve_check(
            CheckRequest(self.char1, "Strength", 10, skill="Athletics"),
            roller=lambda _: 8,
        )
        npc = resolve_check(
            CheckRequest(self.char2, "Strength", 10, skill="Athletics"),
            roller=lambda _: 8,
        )
        self.assertEqual(pc, npc)

    def test_untrained_armor_adds_disadvantage_once(self):
        """RULES-02 armor state enters the request as one named source."""
        self.char1.db.char_class = "Wizard"
        create_object(
            "typeclasses.objects.Item",
            key="heavy test armor",
            location=self.char1,
            attributes=(
                ("type", "armor"),
                ("subtype", "heavy"),
                ("wear_locations", ["body"]),
                ("worn_location", "body"),
            ),
        )
        rolls = iter((18, 7))
        result = resolve_check(
            CheckRequest(
                self.char1,
                "Dexterity",
                10,
                disadvantage_sources=("untrained_armor",),
            ),
            roller=lambda _: next(rolls),
        )
        self.assertEqual(result.roll_mode, RollMode.DISADVANTAGE)
        self.assertEqual(result.disadvantage_sources, ("untrained_armor",))

    def test_rules03_numeric_effect_enters_check_once(self):
        """An active effect contributes through the named RULES-01 seam once."""
        self.char1.effects.add(_CHECK_EFFECT.key, source=self.char2, quiet=True)
        result = resolve_check(
            CheckRequest(self.char1, "Strength", 14), roller=lambda _: 10
        )
        self.assertEqual(result.other_modifiers, 2)
        self.assertEqual(result.total, 14)
        self.assertTrue(result.success)

    def test_stealth_uses_one_roll_against_passive_perception(self):
        """Detection uses the canonical active/passive totals and defender tie."""
        self.char1.db.dexterity = 14
        self.char1.db.skill_proficiencies = ["Stealth"]
        self.char2.db.wisdom = 14
        self.char2.db.skill_proficiencies = ["Perception"]

        hidden = stealth_against_passive(self.char1, self.char2, roller=lambda _: 15)
        tied = stealth_against_passive(self.char1, self.char2, roller=lambda _: 10)

        self.assertTrue(hidden.actor_wins)
        self.assertEqual(hidden.actor.die_result, 15)
        self.assertEqual(hidden.opponent.roll_mode, RollMode.PASSIVE)
        self.assertTrue(tied.tie)
        self.assertFalse(tied.actor_wins)
