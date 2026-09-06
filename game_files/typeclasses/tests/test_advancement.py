"""ADV-01 XP threshold, transaction, and replay-safety coverage."""

from evennia.utils.test_resources import EvenniaTest
from systems.advancement import (ADVANCEMENT_ATTRIBUTE, MAX_LEVEL,
                                 XP_THRESHOLDS, AdvancementError, award_xp,
                                 earned_level, initialize_level_one)
from systems.progression import CLASS_PROGRESSION


class TestAdvancement(EvenniaTest):
    """PC advancement has one threshold reader and one award transaction."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.char1.db.constitution = 14
        self.char1.db.char_class = "Fighter"
        self.char1.db.hit_die = 10
        initialize_level_one(self.char1, class_key="Fighter", hp_base=10)
        self.char1.db.hp_current = 5

    def test_every_srd_threshold_and_boundary_derives_one_level(self):
        """Levels change exactly at each cumulative SRD threshold."""
        for level, threshold in enumerate(XP_THRESHOLDS, start=1):
            self.assertEqual(earned_level(threshold), level)
            if threshold:
                self.assertEqual(earned_level(threshold - 1), level - 1)

    def test_crossed_levels_gain_hp_without_healing_damage(self):
        """Each crossed level gains fixed hit-die HP plus current Constitution."""
        result = award_xp(
            self.char1, 6500, source_kind="quest", source_id="starter-quest"
        )

        self.assertEqual(result.old_level, 1)
        self.assertEqual(result.new_level, 5)
        self.assertEqual(result.crossed_thresholds, XP_THRESHOLDS[1:5])
        self.assertEqual(
            result.applied_grants,
            ("hp_level_2", "hp_level_3", "hp_level_4", "hp_level_5"),
        )
        # Fighter fixed gain 6 + CON modifier 2, four times.
        self.assertEqual(self.char1.stats.hp_base, 42)
        self.assertEqual(self.char1.stats.hp_max, 52)
        self.assertEqual(self.char1.stats.hp_current, 45)

    def test_level_one_records_the_registry_identity_without_copying_definitions(self):
        """A later registry edit can be reconciled without rewriting the PC."""
        provenance = self.char1.db.class_progression

        self.assertEqual(provenance["class_key"], "Fighter")
        self.assertEqual(provenance["registry_version"], CLASS_PROGRESSION.version)
        self.assertEqual(provenance["fingerprint"], CLASS_PROGRESSION.fingerprint)
        self.assertEqual(
            provenance["grants"],
            list(
                CLASS_PROGRESSION.class_for("Fighter")
                .grants_at(1)
                .automatic_feature_keys
            ),
        )

    def test_duplicate_source_is_a_durable_noop(self):
        """A retry cannot pay an event a second time after reload-safe storage."""
        first = award_xp(self.char1, 300, source_kind="quest", source_id="q-1")
        second = award_xp(self.char1, 300, source_kind="quest", source_id="q-1")

        self.assertTrue(first.applied)
        self.assertFalse(second.applied)
        self.assertEqual(second.reason, "duplicate_source")
        self.assertEqual(self.char1.stats.xp, 300)
        self.assertIsNotNone(self.char1.attributes.get(ADVANCEMENT_ATTRIBUTE))

    def test_conflicting_retry_cannot_change_an_existing_award(self):
        """A stable source identity has one immutable amount and outcome."""
        award_xp(self.char1, 300, source_kind="quest", source_id="q-1")
        conflict = award_xp(self.char1, 900, source_kind="quest", source_id="q-1")

        self.assertFalse(conflict.applied)
        self.assertEqual(conflict.reason, "conflicting_source")
        self.assertEqual(self.char1.stats.xp, 300)

    def test_xp_above_cap_is_recorded_without_extra_hp_grants(self):
        """XP remains auditable above level 20 but cannot exceed the level cap."""
        result = award_xp(
            self.char1,
            XP_THRESHOLDS[-1] + 1000,
            source_kind="quest",
            source_id="cap",
        )
        hp_base_at_cap = self.char1.stats.hp_base
        later = award_xp(self.char1, 1000, source_kind="quest", source_id="cap-later")

        self.assertEqual(result.new_level, MAX_LEVEL)
        self.assertTrue(result.capped)
        self.assertEqual(later.new_level, MAX_LEVEL)
        self.assertEqual(later.applied_grants, ())
        self.assertEqual(self.char1.stats.hp_base, hp_base_at_cap)

    def test_negative_and_malformed_awards_are_rejected(self):
        """The ordinary API cannot remove XP or coerce player input."""
        for value in (-1, 1.0, True, "1"):
            with self.assertRaises(AdvancementError):
                award_xp(self.char1, value, source_kind="quest", source_id="bad")

    def test_inconsistent_legacy_level_and_xp_is_quarantined(self):
        """Unsafe legacy state cannot compound into a second progression story."""
        self.char1.db.level = 5
        self.char1.db.xp = 7

        with self.assertRaises(AdvancementError):
            award_xp(self.char1, 1, source_kind="quest", source_id="legacy")

        self.assertEqual(self.char1.db.advancement_repair_required, "level_xp_mismatch")

    def test_malformed_persisted_xp_is_quarantined(self):
        """The service does not coerce invalid durable state into an award."""
        self.char1.db.xp = "300"

        with self.assertRaises(AdvancementError):
            award_xp(self.char1, 1, source_kind="quest", source_id="malformed")

        self.assertEqual(
            self.char1.db.advancement_repair_required, "invalid_xp_or_level"
        )
