"""Executable MAGIC-03 active class-feature coverage."""

from unittest.mock import patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.advancement import award_xp, initialize_level_one
from systems.attacks import AttackOutcome, resolve_basic_attack
from systems.checks import CheckRequest, RollMode, resolve_check
from systems.combat import start_fight
from systems.dice import RollResult
from systems.magic import AccessMode
from systems.magic_actions import (available_actions, cast_action,
                                   grant_action, has_spellbook_entry,
                                   mark_preparation_window, prepare_action)
from systems.magic_resources import resource_current, spend_resource
from systems.training import (default_trainer_profile, resolve_training,
                              set_trainer_profile)


class TestReleasedClassActions(EvenniaTest):
    """Automatic ADV-01 grants expose bounded MAGIC-02 class actions."""

    def _initialize(self, class_key: str, hit_die: int) -> None:
        self.char1.db.is_player_character = True
        self.char1.db.constitution = 10
        initialize_level_one(self.char1, class_key=class_key, hp_base=hit_die)

    def _set_level(self, level: int) -> None:
        """Apply cumulative automatic grants for a direct class-action fixture."""
        from systems.progression import CLASS_PROGRESSION

        definition = CLASS_PROGRESSION.class_for(self.char1.db.char_class)
        state = dict(self.char1.db.class_progression)
        state["grants"] = [
            key
            for grants in definition.levels[:level]
            for key in grants.automatic_feature_keys
        ]
        self.char1.db.level = level
        self.char1.db.class_progression = state

    def test_second_wind_is_innate_and_spends_one_short_rest_use(self):
        """A damaged Fighter can use its automatic level-one recovery action."""
        self._initialize("Fighter", 10)
        self.char1.db.hp_current = 1

        with patch("systems.dice.roll", return_value=4):
            result = cast_action(self.char1, "second wind")

        self.assertEqual(result.amount, 5)
        self.assertEqual(self.char1.stats.hp_current, 6)
        self.assertEqual(resource_current(self.char1, "fighter.second_wind"), 1)
        self.assertIn(
            "fighter.second_wind",
            {action.key for action in available_actions(self.char1, "ability")},
        )

    def test_action_surge_requires_combat_and_spends_once(self):
        """The level-two cadence adapter cannot create an out-of-combat action."""
        self._initialize("Fighter", 10)
        award_xp(self.char1, 300, source_kind="test", source_id="fighter-two")
        with self.assertRaisesRegex(ValueError, "while fighting"):
            cast_action(self.char1, "action surge")
        self.assertEqual(resource_current(self.char1, "fighter.action_surge"), 1)

        self.char1.db.is_player_character = False
        self.char2.db.is_player_character = False
        start_fight(self.char1, self.char2)
        result = cast_action(self.char1, "action surge")
        self.assertEqual(result.reason, "surged")
        self.assertEqual(resource_current(self.char1, "fighter.action_surge"), 0)

    def test_preserve_life_obeys_pool_and_half_health_ceiling(self):
        """Life Domain healing consumes Channel Divinity without exceeding half HP."""
        self._initialize("Cleric", 8)
        award_xp(self.char1, 900, source_kind="test", source_id="cleric-three")
        self.char2.db.hp_max_override = 20
        self.char2.db.hp_current = 1

        result = cast_action(self.char1, "preserve life", target_name=self.char2.key)

        self.assertEqual(result.amount, 9)
        self.assertEqual(self.char2.stats.hp_current, 10)
        self.assertEqual(resource_current(self.char1, "cleric.channel_divinity"), 1)

    def test_arcane_recovery_restores_highest_eligible_expended_slot(self):
        """A resting level-three Wizard exchanges its daily use for one slot."""
        self._initialize("Wizard", 6)
        self._set_level(3)
        spend_resource(self.char1, "wizard.spell_slot.2", 1)
        self.char1.db.position = "resting"

        result = cast_action(self.char1, "arcane recovery")

        self.assertEqual(result.reason, "recovered")
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.2"), 2)
        self.assertEqual(resource_current(self.char1, "wizard.arcane_recovery"), 0)

    def test_improved_critical_turns_a_natural_nineteen_into_a_critical(self):
        """Champion's passive is consumed by the canonical weapon resolver."""
        self._initialize("Fighter", 10)
        self._set_level(3)
        self.char1.db.is_player_character = False
        self.char2.db.is_player_character = False
        rolls = iter((19, 1))

        result = resolve_basic_attack(
            self.char1,
            self.char2,
            die_roller=lambda _sides: next(rolls),
            location_selector=lambda *_: "body",
            emit_messages=False,
        )

        self.assertEqual(result.outcome, AttackOutcome.CRITICAL)

    def test_disciple_of_life_adds_its_slot_level_healing_bonus(self):
        """Life Domain healing extends the ordinary MAGIC-02 healing handler."""
        self._initialize("Cleric", 8)
        self.char1.db.wisdom = 10
        self._set_level(3)
        self.char2.db.hp_max_override = 20
        self.char2.db.hp_current = 1
        mark_preparation_window(self.char1, 1)
        prepare_action(self.char1, "cleric.cure_wounds")

        with patch("systems.dice.roll", return_value=1):
            result = cast_action(self.char1, "cure wounds", target_name=self.char2.key)

        self.assertEqual(result.amount, 5)

    def test_potent_cantrip_deals_half_damage_after_a_successful_save(self):
        """Evoker's passive uses the released cantrip's single save and damage roll."""
        self._initialize("Wizard", 6)
        self._set_level(3)
        self.char2.db.is_player_character = False
        grant_action(self.char1, "wizard.acid_splash", AccessMode.LEARNED)
        saved = RollResult(20, 0, 20, 10, True)

        with (
            patch("systems.dice.roll_check", return_value=saved),
            patch("systems.magic_actions._roll_dice", return_value=7),
        ):
            result = cast_action(self.char1, "acid splash", target_name=self.char2.key)

        self.assertEqual(result.reason, "saved")
        self.assertEqual(result.amount, 3)

    def test_potent_cantrip_deals_half_damage_after_a_missed_attack(self):
        """Evoker's attack cantrip still uses one miss and one damage roll."""
        self._initialize("Wizard", 6)
        self._set_level(3)
        self.char2.db.is_player_character = False
        grant_action(self.char1, "wizard.fire_bolt", AccessMode.LEARNED)

        with (
            patch("systems.magic_actions._spell_attack_hits", return_value=False),
            patch("systems.magic_actions._roll_dice", return_value=9),
        ):
            result = cast_action(self.char1, "fire bolt", target_name=self.char2.key)

        self.assertEqual(result.reason, "miss")
        self.assertEqual(result.amount, 4)

    def test_evocation_savant_adds_two_eligible_spellbook_entries(self):
        """The level-three feature resolves as two bonus Evocation choices."""
        self._initialize("Wizard", 6)
        result = award_xp(self.char1, 900, source_kind="test", source_id="wizard-three")
        trainer = create_object(
            "typeclasses.characters.Character",
            key="Wizard trainer",
            location=self.room1,
        )
        trainer.db.is_player_character = False
        profile = default_trainer_profile()
        profile["classes"] = ["Wizard"]
        profile["choices"] = ["wizard.evocation_savant_spells"]
        set_trainer_profile(trainer, profile)

        resolve_training(
            self.char1,
            "wizard.evocation_savant_spells",
            "wizard.magic_missile",
            trainer,
        )
        resolve_training(
            self.char1,
            "wizard.evocation_savant_spells",
            "wizard.scorching_ray",
            trainer,
        )

        self.assertIn("wizard.evocation_savant_spells", result.pending_choices)
        self.assertTrue(has_spellbook_entry(self.char1, "wizard.magic_missile"))
        self.assertTrue(has_spellbook_entry(self.char1, "wizard.scorching_ray"))

    def test_remarkable_athlete_advantages_strength_athletics(self):
        """Champion's check benefit enters ADV-04 as a named advantage source."""
        self._initialize("Fighter", 10)
        self._set_level(3)

        result = resolve_check(
            CheckRequest(
                self.char1,
                "Strength",
                10,
                skill="Athletics",
                action_key="climb",
            ),
            roller=lambda _sides: 10,
        )

        self.assertEqual(result.roll_mode, RollMode.ADVANTAGE)
        self.assertIn("remarkable_athlete", result.advantage_sources)
