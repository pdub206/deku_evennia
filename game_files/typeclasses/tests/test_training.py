"""ADV-03 durable class-choice and trainer-service coverage."""

from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType
from unittest.mock import patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.advancement import initialize_level_one
from systems.magic import (
    AccessMode,
    ClassAccess,
    MagicDefinition,
    MagicKind,
    PlayerHelp,
    RangeCategory,
    Targeting,
    TargetingMode,
    build_magic_registry,
)
from systems.magic_actions import (
    available_actions,
    has_action_entitlement,
    has_spellbook_entry,
    mark_preparation_window,
)
from systems.progression import CLASS_PROGRESSION, ChoiceSet
from systems.training import (
    CHOICE_STATE_ATTRIBUTE,
    TrainingError,
    default_trainer_profile,
    find_trainer,
    initialize_choice_entitlements,
    practice_view,
    resolve_training,
    set_trainer_profile,
)
from typeclasses.characters import Character


def _magic_definition(
    key: str,
    name: str,
    modes: tuple[str, ...],
    *,
    spell_level: int = 0,
    kind: str = MagicKind.SPELL,
) -> MagicDefinition:
    """Build a test-only Wizard option with no executable game consequence."""
    return MagicDefinition(
        key=key,
        display_name=name,
        aliases=(name.casefold(),),
        kind=kind,
        school="abjuration",
        tags=("test",),
        class_access=(ClassAccess("Wizard", 1),),
        access_modes=modes,
        action_category="manipulate",
        handler_key="utility",
        targeting=Targeting(TargetingMode.SELF, include_caster=True),
        range=RangeCategory.SELF,
        spell_level=spell_level,
        player_help=PlayerHelp(name.casefold(), "A test training option."),
    )


def _magic_registry():
    """Return every ownership shape exposed through the training adapter."""
    definitions = (
        _magic_definition(
            "wizard.training_cantrip",
            "Training Cantrip",
            (AccessMode.LEARNED,),
        ),
        _magic_definition(
            "wizard.training_innate",
            "Training Innate",
            (AccessMode.INNATE,),
            kind=MagicKind.ABILITY,
        ),
        _magic_definition(
            "wizard.training_book_spell",
            "Training Book Spell",
            (AccessMode.PREPARED,),
            spell_level=1,
        ),
        _magic_definition(
            "wizard.training_unlearnable_spell",
            "Training Unlearnable Spell",
            (AccessMode.LEARNED,),
            spell_level=1,
        ),
    )
    return build_magic_registry(
        definitions,
        class_keys=("Wizard",),
        help_keys=tuple(definition.player_help.key for definition in definitions),
    )


def _registry_with_magic_choices():
    """Attach test-only magic choices to Wizard level one declaratively."""
    choices = (
        ChoiceSet(
            "wizard.test_learned",
            1,
            ("wizard.training_cantrip",),
            "none",
            (),
            "resolution",
            "magic_learned",
        ),
        ChoiceSet(
            "wizard.test_innate",
            1,
            ("wizard.training_innate",),
            "none",
            (),
            "resolution",
            "magic_innate",
        ),
        ChoiceSet(
            "wizard.test_spellbook",
            1,
            ("wizard.training_book_spell",),
            "none",
            (),
            "resolution",
            "magic_spellbook",
        ),
        ChoiceSet(
            "wizard.test_prepared",
            1,
            ("wizard.training_book_spell",),
            "none",
            (),
            "resolution",
            "magic_prepared",
        ),
        ChoiceSet(
            "wizard.test_unlearnable",
            1,
            ("wizard.training_unlearnable_spell",),
            "none",
            (),
            "resolution",
            "magic_learned",
        ),
    )
    wizard = CLASS_PROGRESSION.class_for("Wizard")
    first = replace(
        wizard.grants_at(1),
        choice_keys=wizard.grants_at(1).choice_keys
        + tuple(choice.key for choice in choices),
    )
    definitions = dict(CLASS_PROGRESSION.definitions)
    definitions["Wizard"] = replace(wizard, levels=(first,) + wizard.levels[1:])
    return replace(
        CLASS_PROGRESSION,
        definitions=MappingProxyType(definitions),
        choices=MappingProxyType(
            {**CLASS_PROGRESSION.choices, **{item.key: item for item in choices}}
        ),
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

        self.assertEqual(
            [item["choice_key"] for item in view.pending_choices],
            ["fighter.skills", "fighter.fighting_style"],
        )
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
        self.assertEqual(
            [item["choice_key"] for item in state["pending"]],
            ["fighter.fighting_style"],
        )
        self.assertEqual(state["resolved"][0]["selected"], ["Athletics", "Acrobatics"])
        self.assertEqual(state["resolved"][0]["origin"], "trainer")

    def test_unqualified_trainer_and_duplicate_option_fail_without_consuming(self):
        """Class restrictions and known options leave the entitlement intact."""
        profile = default_trainer_profile()
        profile["classes"] = ["Wizard"]
        profile["choices"] = ["wizard.skills"]
        set_trainer_profile(self.trainer, profile)

        with self.assertRaises(TrainingError):
            resolve_training(self.char1, "fighter.skills", "Athletics", self.trainer)
        self.assertEqual(len(practice_view(self.char1).pending_choices), 2)

        profile["classes"] = ["Fighter"]
        profile["choices"] = ["fighter.skills"]
        set_trainer_profile(self.trainer, profile)
        self.char1.db.skill_proficiencies = ["Athletics"]
        with self.assertRaises(TrainingError):
            resolve_training(self.char1, "fighter.skills", "Athletics", self.trainer)
        self.assertEqual(len(practice_view(self.char1).pending_choices), 2)

    def test_entitlement_creation_is_idempotent(self):
        """A replayed level grant cannot create another copy of the choice."""
        initialize_choice_entitlements(self.char1, "Fighter", 1)
        initialize_choice_entitlements(self.char1, "Fighter", 1)

        self.assertEqual(len(practice_view(self.char1).pending_choices), 2)

    def test_primitive_choice_state_reconstructs_from_a_fresh_orm_instance(self):
        """A reconnect-style object reload cannot replay or lose entitlements."""
        before = deepcopy(self.char1.attributes.get(CHOICE_STATE_ATTRIBUTE))
        fresh = Character.objects.get(id=self.char1.id)
        view = practice_view(fresh)

        self.assertEqual(
            tuple(item["choice_key"] for item in view.pending_choices),
            ("fighter.skills", "fighter.fighting_style"),
        )
        self.assertEqual(fresh.attributes.get(CHOICE_STATE_ATTRIBUTE), before)

    def test_malformed_or_stale_entitlement_fails_closed(self):
        """Choice provenance cannot drift from its exact registry grant."""
        original = self.char1.attributes.get(CHOICE_STATE_ATTRIBUTE)
        for field, value in (
            ("registry_version", 999),
            ("registry_fingerprint", "stale"),
            ("class_key", "Wizard"),
            ("level", 3),
            ("count", 99),
            ("choice_key", "wizard.skills"),
        ):
            malformed = deepcopy(original)
            malformed["pending"][0][field] = value
            self.char1.attributes.add(CHOICE_STATE_ATTRIBUTE, malformed)
            with self.assertRaisesRegex(TrainingError, "needs staff repair"):
                practice_view(self.char1)
        self.char1.attributes.add(CHOICE_STATE_ATTRIBUTE, original)

    def test_failed_commit_rolls_back_option_and_entitlement(self):
        """An exception after the owning grant cannot leave half a choice."""
        profile = default_trainer_profile()
        profile["classes"] = ["Fighter"]
        profile["choices"] = ["fighter.fighting_style"]
        set_trainer_profile(self.trainer, profile)

        with patch(
            "systems.training._write_choice_state", side_effect=RuntimeError("write")
        ):
            with self.assertRaisesRegex(RuntimeError, "write"):
                resolve_training(
                    self.char1,
                    "fighter.fighting_style",
                    "Defense",
                    self.trainer,
                )

        self.assertIsNone(self.char1.db.class_feature_choices)
        self.assertIn(
            "fighter.fighting_style",
            [item["choice_key"] for item in practice_view(self.char1).pending_choices],
        )

    def test_trainer_level_band_is_limited_to_alpha(self):
        """Builder profiles cannot advertise unreleased character levels."""
        profile = default_trainer_profile()
        self.assertEqual(profile["maximum_level"], 3)
        profile["maximum_level"] = 4
        with self.assertRaisesRegex(TrainingError, "invalid service access"):
            set_trainer_profile(self.trainer, profile)

    def test_trainer_choices_must_belong_to_a_supported_profile_class(self):
        """A profile cannot pair a Fighter service with a Wizard choice."""
        profile = default_trainer_profile()
        profile["classes"] = ["Fighter"]
        profile["choices"] = ["wizard.skills"]
        with self.assertRaisesRegex(TrainingError, "invalid choices"):
            set_trainer_profile(self.trainer, profile)

    def test_exact_trainer_name_wins_and_partial_ambiguity_is_rejected(self):
        """Explicit lookup prefers one exact name but never guesses a partial."""
        second = create_object(
            "typeclasses.characters.Character",
            key="Fighter trainer veteran",
            location=self.room1,
        )
        second.db.is_player_character = False
        profile = default_trainer_profile()
        profile["classes"] = ["Fighter"]
        profile["choices"] = ["fighter.skills"]
        set_trainer_profile(second, profile)

        self.assertIs(find_trainer(self.char1, "Fighter trainer"), self.trainer)
        with self.assertRaisesRegex(TrainingError, "Please name"):
            find_trainer(self.char1, "trainer")

    def test_location_lock_and_level_are_revalidated_without_consuming(self):
        """Every mutable trainer condition is checked inside the transaction."""
        original = deepcopy(self.char1.attributes.get(CHOICE_STATE_ATTRIBUTE))

        self.trainer.move_to(self.room2, quiet=True)
        with self.assertRaisesRegex(TrainingError, "not here"):
            resolve_training(self.char1, "fighter.skills", "Athletics", self.trainer)
        self.trainer.move_to(self.room1, quiet=True)

        profile = default_trainer_profile()
        profile["classes"] = ["Fighter"]
        profile["choices"] = ["fighter.skills"]
        profile["minimum_level"] = 2
        set_trainer_profile(self.trainer, profile)
        with self.assertRaisesRegex(TrainingError, "cannot help"):
            resolve_training(self.char1, "fighter.skills", "Athletics", self.trainer)

        profile["minimum_level"] = 1
        profile["service_lock"] = "false()"
        set_trainer_profile(self.trainer, profile)
        with self.assertRaisesRegex(TrainingError, "cannot help"):
            resolve_training(self.char1, "fighter.skills", "Athletics", self.trainer)

        self.assertEqual(
            self.char1.attributes.get(CHOICE_STATE_ATTRIBUTE),
            original,
        )

    def test_service_revalidates_character_action_state_inside_transaction(self):
        """Calling the service directly cannot bypass WORLD-02 restrictions."""
        original = deepcopy(self.char1.attributes.get(CHOICE_STATE_ATTRIBUTE))
        self.char1.db.position = "sleeping"

        with self.assertRaisesRegex(TrainingError, "cannot train"):
            resolve_training(self.char1, "fighter.skills", "Athletics", self.trainer)

        self.assertEqual(self.char1.attributes.get(CHOICE_STATE_ATTRIBUTE), original)

    def test_unknown_and_duplicate_submission_do_not_consume_capacity(self):
        """Invalid retries leave both selected options and capacity unchanged."""
        with self.assertRaisesRegex(TrainingError, "not available"):
            resolve_training(self.char1, "fighter.skills", "Arcana", self.trainer)
        resolve_training(self.char1, "fighter.skills", "Athletics", self.trainer)
        with self.assertRaisesRegex(TrainingError, "already know"):
            resolve_training(self.char1, "fighter.skills", "Athletics", self.trainer)
        resolve_training(self.char1, "fighter.skills", "Acrobatics", self.trainer)
        completed = deepcopy(self.char1.attributes.get(CHOICE_STATE_ATTRIBUTE))
        with self.assertRaisesRegex(TrainingError, "not pending"):
            resolve_training(self.char1, "fighter.skills", "History", self.trainer)
        self.assertEqual(self.char1.attributes.get(CHOICE_STATE_ATTRIBUTE), completed)

    def test_expertise_requires_proficiency_and_doubles_its_bonus(self):
        """Rogue and Scholar choices use one proficiency-aware adapter."""
        self.char2.db.is_player_character = True
        self.char2.db.constitution = 10
        self.char2.db.dexterity = 14
        initialize_level_one(self.char2, class_key="Rogue", hp_base=8)
        self.char2.db.skill_proficiencies = ["Acrobatics", "Stealth"]
        profile = default_trainer_profile()
        profile["classes"] = ["Rogue"]
        profile["choices"] = ["rogue.expertise"]
        set_trainer_profile(self.trainer, profile)

        with self.assertRaisesRegex(TrainingError, "requires proficiency"):
            resolve_training(self.char2, "rogue.expertise", "Athletics", self.trainer)
        resolve_training(self.char2, "rogue.expertise", "Acrobatics", self.trainer)
        resolve_training(self.char2, "rogue.expertise", "Stealth", self.trainer)

        self.assertEqual(self.char2.db.skill_expertise, ["Acrobatics", "Stealth"])
        self.assertEqual(self.char2.stats.skill_bonus("Acrobatics"), 6)
        self.assertEqual(
            practice_view(self.char2).expertise_proficiencies,
            ("Acrobatics", "Stealth"),
        )

    def test_magic_choice_adapters_use_magic_ownership_transactionally(self):
        """Magic choices grant only through their declared ownership adapters."""
        progression = _registry_with_magic_choices()
        magic = _magic_registry()
        self.char2.db.is_player_character = True
        self.char2.db.constitution = 10
        profile = default_trainer_profile()
        profile["classes"] = ["Wizard"]
        profile["choices"] = [
            "wizard.test_learned",
            "wizard.test_innate",
            "wizard.test_spellbook",
            "wizard.test_prepared",
            "wizard.test_unlearnable",
        ]

        with (
            patch("systems.advancement.CLASS_PROGRESSION", progression),
            patch("systems.training.CLASS_PROGRESSION", progression),
            patch("systems.magic.MAGIC_REGISTRY", magic),
        ):
            initialize_level_one(self.char2, class_key="Wizard", hp_base=6)
            set_trainer_profile(self.trainer, profile)
            mark_preparation_window(self.char2, 1)

            with self.assertRaisesRegex(TrainingError, "does not learn"):
                resolve_training(
                    self.char2,
                    "wizard.test_unlearnable",
                    "wizard.training_unlearnable_spell",
                    self.trainer,
                )
            pending = practice_view(self.char2).pending_choices
            unlearnable = next(
                item
                for item in pending
                if item["choice_key"] == "wizard.test_unlearnable"
            )
            self.assertEqual(unlearnable["selected"], [])

            resolve_training(
                self.char2,
                "wizard.test_learned",
                "wizard.training_cantrip",
                self.trainer,
            )
            resolve_training(
                self.char2,
                "wizard.test_innate",
                "wizard.training_innate",
                self.trainer,
            )
            resolve_training(
                self.char2,
                "wizard.test_spellbook",
                "wizard.training_book_spell",
                self.trainer,
            )
            resolve_training(
                self.char2,
                "wizard.test_prepared",
                "wizard.training_book_spell",
                self.trainer,
            )

            self.assertTrue(
                has_action_entitlement(
                    self.char2, "wizard.training_cantrip", AccessMode.LEARNED
                )
            )
            self.assertTrue(
                has_action_entitlement(
                    self.char2, "wizard.training_innate", AccessMode.INNATE
                )
            )
            self.assertTrue(
                has_spellbook_entry(self.char2, "wizard.training_book_spell")
            )
            view = practice_view(self.char2)
            self.assertEqual(view.known_spells, ("Training Cantrip",))
            self.assertEqual(view.prepared_spells, ("Training Book Spell",))
            self.assertEqual(view.known_abilities, ("Training Innate",))
            self.assertEqual(view.spellbook_spells, ("Training Book Spell",))
            self.assertTrue(
                has_action_entitlement(
                    self.char2, "wizard.training_book_spell", AccessMode.PREPARED
                )
            )
            self.assertEqual(
                {
                    action.key
                    for action in available_actions(self.char2, MagicKind.SPELL)
                },
                {
                    "wizard.training_book_spell",
                    "wizard.training_cantrip",
                },
            )
            self.assertEqual(
                {
                    action.key
                    for action in available_actions(self.char2, MagicKind.ABILITY)
                },
                {"wizard.training_innate"},
            )
