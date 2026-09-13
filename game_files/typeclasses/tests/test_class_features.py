"""Released class-choice adapter coverage for ADV-03."""

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.advancement import initialize_level_one
from systems.progression import CLASS_PROGRESSION
from systems.training import (
    TrainingError,
    default_trainer_profile,
    practice_view,
    resolve_training,
    set_trainer_profile,
)


class TestClassFeatureChoices(EvenniaTest):
    """A trained class option affects only its narrowly owned rules."""

    def _initialize(self, class_key: str, hit_die: int):
        character = self.char1
        character.db.is_player_character = True
        character.db.constitution = 10
        initialize_level_one(character, class_key=class_key, hp_base=hit_die)
        trainer = create_object(
            "typeclasses.characters.Character",
            key=f"{class_key} trainer",
            location=self.room1,
        )
        trainer.db.is_player_character = False
        return character, trainer

    @staticmethod
    def _profile(trainer, class_key: str, choice_key: str) -> None:
        profile = default_trainer_profile()
        profile["classes"] = [class_key]
        profile["choices"] = [choice_key]
        set_trainer_profile(trainer, profile)

    def test_protector_grants_heavy_armor_and_martial_weapon_training(self):
        cleric, trainer = self._initialize("Cleric", 8)
        self._profile(trainer, "Cleric", "cleric.divine_order")
        armor = create_object(
            "typeclasses.objects.Item",
            key="plate armor",
            location=cleric,
            attributes=(("type", "armor"), ("subtype", "heavy")),
        )
        weapon = create_object(
            "typeclasses.objects.Item",
            key="longsword",
            location=cleric,
            attributes=(
                ("type", "weapon"),
                ("weapon_category", "martial"),
                ("weapon_kind", "long_sword"),
            ),
        )
        self.assertFalse(cleric.equipment.is_armor_proficient(armor))
        self.assertFalse(cleric.equipment.is_weapon_proficient(weapon))

        resolve_training(cleric, "cleric.divine_order", "Protector", trainer)

        self.assertTrue(cleric.equipment.is_armor_proficient(armor))
        self.assertTrue(cleric.equipment.is_weapon_proficient(weapon))
        self.assertEqual(
            CLASS_PROGRESSION.choices["cleric.divine_order"].legal_options,
            ("Protector",),
        )

    def test_defense_adds_ac_only_while_wearing_armor(self):
        fighter, trainer = self._initialize("Fighter", 10)
        self._profile(trainer, "Fighter", "fighter.fighting_style")
        armor = create_object(
            "typeclasses.objects.Item",
            key="leather armor",
            location=fighter,
            attributes=(
                ("type", "armor"),
                ("subtype", "light"),
                ("base_ac", 11),
                ("wear_locations", ["body"]),
                ("worn_location", "body"),
            ),
        )
        before = fighter.stats.armor_class
        resolve_training(fighter, "fighter.fighting_style", "Defense", trainer)

        self.assertEqual(fighter.stats.armor_class, before + 1)
        armor.db.worn_location = None
        self.assertEqual(
            fighter.stats.armor_class,
            10 + fighter.stats.ability_modifier("Dexterity"),
        )

    def test_unimplemented_options_are_absent_and_remain_pending(self):
        fighter, trainer = self._initialize("Fighter", 10)
        self._profile(trainer, "Fighter", "fighter.fighting_style")

        with self.assertRaisesRegex(TrainingError, "not available"):
            resolve_training(fighter, "fighter.fighting_style", "Archery", trainer)

        self.assertIn(
            "fighter.fighting_style",
            [item["choice_key"] for item in practice_view(fighter).pending_choices],
        )
        self.assertEqual(
            CLASS_PROGRESSION.choices["fighter.fighting_style"].legal_options,
            ("Defense",),
        )
