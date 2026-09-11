"""ADV-01 XP threshold, transaction, and replay-safety coverage."""

from unittest.mock import patch

from evennia.utils.test_resources import EvenniaTest
from systems.advancement import (
    ADVANCEMENT_ATTRIBUTE,
    RELEASE_LEVEL_CAP,
    XP_THRESHOLDS,
    AdvancementError,
    award_xp,
    earned_level,
    initialize_level_one,
)
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
        """One large award applies only the released levels in order."""
        result = award_xp(
            self.char1, 6500, source_kind="quest", source_id="starter-quest"
        )

        self.assertEqual(result.old_level, 1)
        self.assertEqual(result.new_level, RELEASE_LEVEL_CAP)
        self.assertEqual(result.crossed_thresholds, XP_THRESHOLDS[1:3])
        self.assertEqual(
            result.applied_grants,
            (
                "hp_level_2",
                "fighter.action_surge",
                "fighter.tactical_mind",
                "hp_level_3",
                "fighter.champion",
                "fighter.improved_critical",
                "fighter.remarkable_athlete",
            ),
        )
        # hp_base stores the fixed six; CharacterStats applies CON once/level.
        self.assertEqual(self.char1.stats.hp_base, 22)
        self.assertEqual(self.char1.stats.hp_max, 28)
        self.assertEqual(self.char1.stats.hp_current, 21)
        self.assertEqual(
            self.char1.db.class_progression["grants"],
            [
                "fighter.second_wind",
                "fighter.weapon_mastery",
                "fighter.action_surge",
                "fighter.tactical_mind",
                "fighter.champion",
                "fighter.improved_critical",
                "fighter.remarkable_athlete",
            ],
        )

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
        """XP remains auditable above level 3 without level-four grants."""
        result = award_xp(
            self.char1,
            XP_THRESHOLDS[-1] + 1000,
            source_kind="quest",
            source_id="cap",
        )
        hp_base_at_cap = self.char1.stats.hp_base
        later = award_xp(self.char1, 1000, source_kind="quest", source_id="cap-later")

        self.assertEqual(result.new_level, RELEASE_LEVEL_CAP)
        self.assertTrue(result.capped)
        self.assertEqual(later.new_level, RELEASE_LEVEL_CAP)
        self.assertEqual(later.applied_grants, ())
        self.assertEqual(self.char1.stats.hp_base, hp_base_at_cap)

    def test_negative_and_malformed_awards_are_rejected(self):
        """The ordinary API cannot remove XP or coerce player input."""
        for value in (-1, 1.0, True, "1"):
            with self.assertRaises(AdvancementError):
                award_xp(self.char1, value, source_kind="quest", source_id="bad")

    def test_negative_constitution_still_grants_one_hp_per_level(self):
        """The fixed gain plus Constitution modifier has a one-HP floor."""
        self.char1.db.constitution = 1

        award_xp(self.char1, 300, source_kind="quest", source_id="frail")

        self.assertEqual(self.char1.db.hp_base, 16)
        self.assertEqual(self.char1.db.hp_current, 6)

    def test_inconsistent_legacy_level_and_xp_is_quarantined(self):
        """Unsafe legacy state cannot compound into a second progression story."""
        self.char1.db.level = 2
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

    def test_malformed_ledger_is_quarantined_outside_the_rolled_back_award(self):
        """A failed validation leaves a durable staff-visible repair reason."""
        self.char1.db.advancement_ledger = {"version": 999}

        with self.assertRaises(AdvancementError):
            award_xp(self.char1, 1, source_kind="quest", source_id="bad-ledger")

        self.char1.attributes.reset_cache()
        self.assertEqual(self.char1.db.advancement_repair_required, "invalid_ledger")
        self.assertEqual(self.char1.db.xp, 0)

    def test_incomplete_grant_provenance_is_quarantined(self):
        """A level record cannot silently omit an already-earned fixed grant."""
        self.char1.db.class_progression["grants"] = []

        with self.assertRaises(AdvancementError):
            award_xp(self.char1, 1, source_kind="quest", source_id="bad-grants")

        self.assertEqual(
            self.char1.db.advancement_repair_required,
            "invalid_class_progression",
        )

    def test_failed_grant_rolls_back_xp_level_hp_and_choices(self):
        """An adapter failure cannot leave a partial level transaction cached."""
        before_choices = self.char1.db.progression_choices

        with patch(
            "systems.training.initialize_choice_entitlements",
            side_effect=RuntimeError("grant failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "grant failed"):
                award_xp(
                    self.char1,
                    300,
                    source_kind="quest",
                    source_id="rollback",
                )

        self.assertEqual(self.char1.db.xp, 0)
        self.assertEqual(self.char1.db.level, 1)
        self.assertEqual(self.char1.db.hp_base, 10)
        self.assertEqual(self.char1.db.progression_choices, before_choices)
        self.assertEqual(self.char1.db.hp_current, 5)
        self.assertEqual(self.char1.db.advancement_ledger["entries"], [])

    def test_player_stat_mutators_cannot_bypass_advancement(self):
        """Public stat helpers cannot directly rewrite PC XP or level."""
        with self.assertRaisesRegex(ValueError, "advancement service"):
            self.char1.stats.set_xp(300)
        with self.assertRaisesRegex(ValueError, "advancement service"):
            self.char1.stats.set_level(2)
