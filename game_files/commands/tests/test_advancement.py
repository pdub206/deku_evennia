"""Staff command coverage for ADV-06 progression repair."""

from unittest.mock import patch

from commands.advancement import CmdAdvancementRepair
from commands.default_cmdsets import CharacterCmdSet
from evennia.utils.test_resources import EvenniaCommandTest
from systems.advancement import initialize_level_one, progression_state
from systems.advancement_repair import plan_progression_repair, repair_audit


class TestAdvancementRepairCommand(EvenniaCommandTest):
    """The public repair workflow is locked and plan-identity bound."""

    def setUp(self):
        super().setUp()
        self._release_gate = patch(
            "systems.advancement.is_level_published", return_value=True
        )
        self._release_gate.start()
        self.addCleanup(self._release_gate.stop)
        self.char2.db.is_player_character = True
        self.char2.db.constitution = 14
        initialize_level_one(self.char2, class_key="Fighter", hp_base=10)
        self.char2.attributes.remove("class_progression")

    def test_command_is_registered_and_builder_locked(self):
        cmdset = CharacterCmdSet()
        cmdset.at_cmdset_creation()

        self.assertTrue(
            any(command.key == "@advancement" for command in cmdset.commands)
        )
        self.assertTrue(CmdAdvancementRepair().access(self.char1, "cmd"))
        self.char2.permissions.clear()
        self.assertFalse(CmdAdvancementRepair().access(self.char2, "cmd"))

    def test_builder_can_plan_without_mutating_the_target(self):
        plan = plan_progression_repair(self.char2)

        output = self.call(CmdAdvancementRepair(), f"/plan #{self.char2.id}")

        self.assertIn(plan.plan_id, output)
        self.assertIn("migrate_progression_baseline", output)
        self.assertIsNone(self.char2.db.class_progression)

    def test_apply_requires_admin_even_for_a_supported_low_risk_plan(self):
        plan = plan_progression_repair(self.char2)

        with patch.object(self.char1, "check_permstring", return_value=False):
            output = self.call(
                CmdAdvancementRepair(),
                f"/apply #{self.char2.id}={plan.plan_id};legacy import",
            )

        self.assertIn("Only Admin", output)
        self.assertIsNone(self.char2.db.class_progression)

    def test_admin_apply_rechecks_plan_identity_and_audits(self):
        plan = plan_progression_repair(self.char2)

        output = self.call(
            CmdAdvancementRepair(),
            f"/apply #{self.char2.id}={plan.plan_id};legacy import;DEKU-102",
        )

        self.assertIn("Applied advancement plan", output)
        self.assertEqual(progression_state(self.char2)["class_key"], "Fighter")
        audit = repair_audit(self.char2)[-1]
        self.assertEqual(audit["source_ticket"], "DEKU-102")
        self.assertEqual(audit["actor_id"], self.char1.id)
