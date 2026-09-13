"""ADV-05 read-only advancement projection coverage."""

from copy import deepcopy

from evennia.utils.test_resources import EvenniaTest
from systems.advancement import award_xp, initialize_level_one
from systems.advancement_info import advancement_info
from systems.progression import CLASS_PROGRESSION
from systems.training import CHOICE_STATE_ATTRIBUTE
from world.help_entries import HELP_ENTRY_DICTS


class TestAdvancementInfo(EvenniaTest):
    """Released progression presentation derives from canonical registries."""

    def _initialize(self, class_key):
        definition = CLASS_PROGRESSION.class_for(class_key)
        self.char1.attributes.remove(CHOICE_STATE_ATTRIBUTE)
        self.char1.db.is_player_character = True
        self.char1.db.constitution = 10
        initialize_level_one(
            self.char1, class_key=class_key, hp_base=definition.hit_die
        )

    def test_all_classes_expose_exact_released_levels_and_thresholds(self):
        """Every selectable class projects levels 1–3 and no future level."""
        for class_key in CLASS_PROGRESSION.definitions:
            self._initialize(class_key)
            info = advancement_info(self.char1)

            self.assertTrue(info.valid)
            self.assertEqual(
                tuple((level.level, level.xp_threshold) for level in info.levels),
                ((1, 0), (2, 300), (3, 900)),
            )
            expected = CLASS_PROGRESSION.class_for(class_key)
            self.assertEqual(
                tuple(
                    feature
                    for level in info.levels
                    for feature in level.automatic_features
                ),
                tuple(
                    key.rsplit(".", 1)[-1].replace("_", " ").title()
                    for grants in expected.levels
                    for key in grants.automatic_feature_keys
                ),
            )
            for view, grants in zip(info.levels, expected.levels, strict=True):
                self.assertEqual(
                    view.choices,
                    tuple(
                        key.rsplit(".", 1)[-1].replace("_", " ").title()
                        for key in grants.choice_keys
                    ),
                )
                self.assertEqual(
                    tuple(maximum for _, maximum in view.resources),
                    tuple(
                        CLASS_PROGRESSION.resources[key].maxima[view.level - 1]
                        for key in grants.resource_keys
                    ),
                )
                self.assertEqual(len(view.spell_access), len(grants.spell_access_keys))

    def test_next_xp_multi_level_visibility_and_cap_are_exact(self):
        """Views retain every crossed level while calculating the cap correctly."""
        self._initialize("Wizard")
        initial = advancement_info(self.char1)
        self.assertEqual((initial.next_threshold, initial.xp_remaining), (300, 300))

        award_xp(self.char1, 450, source_kind="test", source_id="level-two")
        second = advancement_info(self.char1)
        self.assertEqual((second.next_threshold, second.xp_remaining), (900, 450))
        self.assertTrue(second.levels[0].spell_access)
        self.assertTrue(second.levels[1].spell_access)

        award_xp(self.char1, 1000, source_kind="test", source_id="level-three")
        capped = advancement_info(self.char1)
        self.assertTrue(capped.capped)
        self.assertIsNone(capped.next_threshold)
        self.assertIsNone(capped.xp_remaining)

    def test_views_are_mutation_free_and_malformed_state_fails_closed(self):
        """Inspection neither reconciles valid state nor marks invalid state."""
        self._initialize("Cleric")
        before = deepcopy(
            {
                attribute.key: attribute.value
                for attribute in self.char1.attributes.all()
            }
        )
        self.assertTrue(advancement_info(self.char1).valid)
        self.assertEqual(
            {
                attribute.key: attribute.value
                for attribute in self.char1.attributes.all()
            },
            before,
        )

        malformed = deepcopy(self.char1.db.class_progression)
        malformed["fingerprint"] = "stale"
        self.char1.db.class_progression = malformed
        invalid_before = deepcopy(malformed)
        info = advancement_info(self.char1)
        self.assertFalse(info.valid)
        self.assertEqual(info.diagnostic, "invalid_class_progression")
        self.assertEqual(self.char1.db.class_progression, invalid_before)
        self.assertIsNone(self.char1.db.advancement_repair_required)

    def test_feature_help_metadata_targets_published_help(self):
        """Every released feature's validated help key remains player-visible."""
        player_help = {
            entry["key"] for entry in HELP_ENTRY_DICTS if "locks" not in entry
        }
        self.assertIn("levels", player_help)
        for feature in CLASS_PROGRESSION.features.values():
            self.assertIn(feature.help_key, player_help)
