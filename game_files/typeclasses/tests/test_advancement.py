"""ADV-01 XP threshold, transaction, and replay-safety coverage."""

from unittest.mock import patch

from evennia.utils.test_resources import EvenniaTest
from systems.advancement import (ADVANCEMENT_ATTRIBUTE, MAX_LEVEL,
                                 XP_THRESHOLDS, AdvancementError, award_xp,
                                 earned_level, expected_progression_records,
                                 initialize_level_one, progression_state)
from systems.advancement_repair import (apply_progression_repair,
                                        diagnose_progression,
                                        plan_progression_repair, repair_audit)
from systems.magic_resources import (MagicResourceError, resource_current,
                                     spend_resource)
from systems.progression import CLASS_PROGRESSION


class TestAdvancement(EvenniaTest):
    """PC advancement has one threshold reader and one award transaction."""

    def setUp(self):
        super().setUp()
        self._release_gate = patch(
            "systems.advancement.is_level_published", return_value=True
        )
        self._release_gate.start()
        self.addCleanup(self._release_gate.stop)
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

    def test_spell_slot_capacity_is_created_by_grant_and_not_refilled_on_upgrade(self):
        """Spell-access grants seed new slots without restoring spent ones."""
        self.char2.db.is_player_character = True
        self.char2.db.constitution = 10
        initialize_level_one(self.char2, class_key="Wizard", hp_base=6)

        self.assertEqual(resource_current(self.char2, "wizard.spell_slot.1"), 2)
        spend_resource(self.char2, "wizard.spell_slot.1", 2)
        award_xp(self.char2, 900, source_kind="quest", source_id="wizard-level-3")

        self.assertEqual(self.char2.db.level, 3)
        self.assertEqual(resource_current(self.char2, "wizard.spell_slot.1"), 0)
        self.assertEqual(resource_current(self.char2, "wizard.spell_slot.2"), 2)

    def test_missing_resource_record_is_repair_state_not_a_free_refill(self):
        """Reads never materialize a full slot after its durable state is lost."""
        self.char2.db.is_player_character = True
        self.char2.db.constitution = 10
        initialize_level_one(self.char2, class_key="Wizard", hp_base=6)
        resources = self.char2.db.magic_resources
        del resources["current"]["wizard.spell_slot.1"]
        self.char2.db.magic_resources = resources

        with self.assertRaisesRegex(MagicResourceError, "needs staff repair"):
            resource_current(self.char2, "wizard.spell_slot.1")

    def test_every_level_grant_phase_rolls_back_as_one_transaction(self):
        """A failed adapter leaves no HP, XP, level, or provenance mutation."""
        phases = (
            "systems.training.initialize_choice_entitlements",
            "systems.advancement._grant_automatic_actions",
            "systems.advancement._initialize_granted_resources",
        )
        for phase in phases:
            with self.subTest(phase=phase):
                before = progression_state(self.char1)
                with patch(phase, side_effect=AdvancementError("adapter failed")):
                    with self.assertRaisesRegex(AdvancementError, "adapter failed"):
                        award_xp(
                            self.char1,
                            300,
                            source_kind="rollback",
                            source_id=phase,
                        )
                reloaded = self.char1.__class__.objects.get(pk=self.char1.pk)
                self.assertEqual((reloaded.db.xp, reloaded.db.level), (0, 1))
                self.assertEqual(reloaded.db.hp_base, 10)
                self.assertEqual(progression_state(reloaded), before)
                self.char1 = reloaded

    def test_level_one_adapter_failure_does_not_leave_a_partial_character(self):
        """Chargen's baseline has the same all-or-nothing grant boundary."""
        self.char2.db.is_player_character = True
        self.char2.attributes.remove("char_class")
        with patch(
            "systems.training.initialize_choice_entitlements",
            side_effect=AdvancementError("level-one adapter failed"),
        ):
            with self.assertRaisesRegex(AdvancementError, "level-one adapter failed"):
                initialize_level_one(self.char2, class_key="Fighter", hp_base=10)

        reloaded = self.char2.__class__.objects.get(pk=self.char2.pk)
        self.assertIsNone(reloaded.db.char_class)
        self.assertIsNone(reloaded.db.class_progression)
        self.assertIsNone(reloaded.db.progression_choices)

    def test_spell_access_grant_phase_rolls_back_as_one_transaction(self):
        """A failed slot initializer also rolls back its level's provenance."""
        self.char2.db.is_player_character = True
        self.char2.db.constitution = 10
        initialize_level_one(self.char2, class_key="Wizard", hp_base=6)
        before = progression_state(self.char2)
        with patch(
            "systems.advancement._initialize_spell_access",
            side_effect=AdvancementError("slot adapter failed"),
        ):
            with self.assertRaisesRegex(AdvancementError, "slot adapter failed"):
                award_xp(
                    self.char2, 300, source_kind="rollback", source_id="spell-access"
                )

        reloaded = self.char2.__class__.objects.get(pk=self.char2.pk)
        self.assertEqual((reloaded.db.xp, reloaded.db.level), (0, 1))
        self.assertEqual(progression_state(reloaded), before)

    def test_level_twenty_provenance_survives_reload_and_source_retry(self):
        """All crossed levels are primitive, ordered, and never replayed twice."""
        result = award_xp(
            self.char1,
            XP_THRESHOLDS[-1],
            source_kind="campaign",
            source_id="level-twenty",
        )
        reloaded = self.char1.__class__.objects.get(pk=self.char1.pk)
        provenance = progression_state(reloaded)
        flattened = tuple(
            record for entry in provenance["levels"] for record in entry["records"]
        )
        retry = award_xp(
            reloaded,
            XP_THRESHOLDS[-1],
            source_kind="campaign",
            source_id="level-twenty",
        )

        self.assertEqual(result.new_level, MAX_LEVEL)
        self.assertEqual(len(provenance["levels"]), MAX_LEVEL)
        self.assertEqual(
            flattened,
            expected_progression_records("Fighter", MAX_LEVEL),
        )
        self.assertEqual(retry.reason, "duplicate_source")
        self.assertFalse(retry.applied)
        self.assertEqual(reloaded.db.level, MAX_LEVEL)

    def test_advancement_never_sends_a_message_before_commit(self):
        """Notification remains the caller's post-commit responsibility."""
        with patch.object(self.char1, "msg", side_effect=AssertionError("messaged")):
            result = award_xp(
                self.char1, 300, source_kind="quest", source_id="no-message"
            )

        self.assertTrue(result.applied)
        self.assertEqual(self.char1.db.level, 2)

    def test_unpublished_level_rejects_xp_before_any_advancement_mutation(self):
        """A valid PC is not quarantined merely because a release is pending."""
        with patch("systems.advancement.is_level_published", return_value=False):
            with self.assertRaisesRegex(AdvancementError, "not released"):
                award_xp(self.char1, 300, source_kind="quest", source_id="pending")

        self.assertEqual((self.char1.db.xp, self.char1.db.level), (0, 1))
        self.assertIsNone(self.char1.db.advancement_repair_required)

    def test_level_one_records_versioned_occurrence_provenance(self):
        """A later registry edit can be reconciled without rewriting the PC."""
        provenance = self.char1.db.class_progression

        self.assertEqual(provenance["class_key"], "Fighter")
        self.assertEqual(provenance["registry_version"], CLASS_PROGRESSION.version)
        self.assertEqual(provenance["fingerprint"], CLASS_PROGRESSION.fingerprint)
        self.assertEqual(
            [record["key"] for record in provenance["levels"][0]["records"]],
            ["fighter.second_wind", "fighter.skills"],
        )

    def test_registry_drift_quarantines_without_rewriting_provenance(self):
        """A changed registry fingerprint blocks awards until an audit repair."""
        provenance = dict(self.char1.db.class_progression)
        provenance["fingerprint"] = "0" * 64
        self.char1.db.class_progression = provenance

        with self.assertRaises(AdvancementError):
            award_xp(self.char1, 300, source_kind="quest", source_id="drift")

        self.assertEqual(
            self.char1.db.advancement_repair_required, "progression_version_drift"
        )

    def test_supported_legacy_record_has_a_dry_run_and_audited_migration(self):
        """ADV-06's Phase-1 repair reconstructs provenance without XP replay."""
        self.char1.attributes.remove("class_progression")
        diagnosis = diagnose_progression(self.char1)
        self.assertEqual(diagnosis.issues, ("missing_progression_provenance",))
        plan = plan_progression_repair(self.char1)
        self.assertEqual(plan.operations, ("migrate_progression_baseline",))

        repaired = apply_progression_repair(self.char1, plan, reason="legacy import")

        self.assertEqual(repaired.issues, ())
        self.assertEqual(progression_state(self.char1)["class_key"], "Fighter")
        self.assertEqual(repair_audit(self.char1)[-1]["outcome"], "applied")

    def test_duplicate_source_is_a_durable_noop(self):
        """A retry cannot pay an event a second time after reload-safe storage."""
        first = award_xp(self.char1, 300, source_kind="quest", source_id="q-1")
        second = award_xp(self.char1, 300, source_kind="quest", source_id="q-1")

        self.assertTrue(first.applied)
        self.assertFalse(second.applied)
        self.assertEqual(second.reason, "duplicate_source")
        self.assertEqual(self.char1.stats.xp, 300)
        self.assertIsNotNone(self.char1.attributes.get(ADVANCEMENT_ATTRIBUTE))

    def test_interleaved_same_source_award_resolves_once_after_the_lock(self):
        """A winner arriving at the lock boundary cannot create a double award."""
        from systems.advancement import _lock_character

        nested_results = []
        entered = False

        def interleaved_lock(character):
            nonlocal entered
            if not entered:
                entered = True
                with patch("systems.advancement._lock_character", _lock_character):
                    nested_results.append(
                        award_xp(
                            character,
                            300,
                            source_kind="concurrent",
                            source_id="same-event",
                        )
                    )
            _lock_character(character)

        with patch("systems.advancement._lock_character", side_effect=interleaved_lock):
            outer = award_xp(
                self.char1,
                300,
                source_kind="concurrent",
                source_id="same-event",
            )

        self.assertTrue(nested_results[0].applied)
        self.assertFalse(outer.applied)
        self.assertEqual(outer.reason, "duplicate_source")
        self.assertEqual(self.char1.db.xp, 300)

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
