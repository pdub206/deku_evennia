"""ADV-03 durable class-choice and trainer-service coverage."""

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.advancement import initialize_level_one
from systems.training import (
    CHOICE_STATE_ATTRIBUTE,
    TrainingError,
    default_trainer_profile,
    initialize_choice_entitlements,
    practice_view,
    resolve_training,
    set_trainer_profile,
)


class TestTrainingService(EvenniaTest):
    """Class choices remain pending until an eligible trainer resolves them."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.char1.db.char_class = "Fighter"
        self.char1.db.constitution = 10
        initialize_level_one(self.char1, class_key="Fighter", hp_base=10)
        self.trainer = create_object(
            "typeclasses.characters.Character",
            key="Fighter trainer",
            location=self.room1,
        )
        self.trainer.db.is_player_character = False
        profile = default_trainer_profile()
        profile["classes"] = ["Fighter"]
        profile["choices"] = ["fighter.skills"]
        set_trainer_profile(self.trainer, profile)

    def test_practice_is_read_only_and_level_one_choice_is_pending(self):
        """The view exposes only the player's own unresolved choice data."""
        before = self.char1.attributes.get(CHOICE_STATE_ATTRIBUTE)
        view = practice_view(self.char1)

        self.assertEqual(len(view.pending_choices), 1)
        self.assertEqual(view.pending_choices[0]["choice_key"], "fighter.skills")
        self.assertEqual(before, self.char1.attributes.get(CHOICE_STATE_ATTRIBUTE))

    def test_trainer_resolves_multi_selection_without_duplicate_proficiency(self):
        """Each selection consumes capacity; completion writes one provenance record."""
        first = resolve_training(
            self.char1, "fighter.skills", "Athletics", self.trainer
        )
        second = resolve_training(
            self.char1, "fighter.skills", "Acrobatics", self.trainer
        )
        state = self.char1.attributes.get(CHOICE_STATE_ATTRIBUTE)

        self.assertTrue(first.applied)
        self.assertTrue(second.applied)
        self.assertEqual(self.char1.db.skill_proficiencies, ["Acrobatics", "Athletics"])
        self.assertEqual(state["pending"], [])
        self.assertEqual(state["resolved"][0]["selected"], ["Athletics", "Acrobatics"])
        self.assertEqual(state["resolved"][0]["origin"], "trainer")

    def test_unqualified_trainer_and_duplicate_option_fail_without_consuming(self):
        """Class restrictions and known options leave the entitlement intact."""
        profile = default_trainer_profile()
        profile["classes"] = ["Wizard"]
        profile["choices"] = ["fighter.skills"]
        set_trainer_profile(self.trainer, profile)

        with self.assertRaises(TrainingError):
            resolve_training(self.char1, "fighter.skills", "Athletics", self.trainer)
        self.assertEqual(len(practice_view(self.char1).pending_choices), 1)

        profile["classes"] = ["Fighter"]
        set_trainer_profile(self.trainer, profile)
        self.char1.db.skill_proficiencies = ["Athletics"]
        with self.assertRaises(TrainingError):
            resolve_training(self.char1, "fighter.skills", "Athletics", self.trainer)
        self.assertEqual(len(practice_view(self.char1).pending_choices), 1)

    def test_entitlement_creation_is_idempotent(self):
        """A replayed level grant cannot create another copy of the choice."""
        initialize_choice_entitlements(self.char1, "Fighter", 1)
        initialize_choice_entitlements(self.char1, "Fighter", 1)

        self.assertEqual(len(practice_view(self.char1).pending_choices), 1)
