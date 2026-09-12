"""Command-level coverage for COMBAT-02 target enrollment."""

from commands.combat import CmdAim, CmdAttack, CmdHide
from evennia.utils.test_resources import EvenniaCommandTest
from systems.combat import get_target, is_fighting


class TestCmdAttack(EvenniaCommandTest):
    """Attack aliases alter encounter intent but never deal immediate damage."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = False
        self.char2.db.is_player_character = False

    def test_attack_starts_fight_without_an_immediate_hit(self):
        before = self.char2.stats.hp_current

        self.call(CmdAttack(), "Char2", "You begin fighting Char2.")

        self.assertTrue(is_fighting(self.char1))
        self.assertIs(get_target(self.char1), self.char2)
        self.assertEqual(self.char2.stats.hp_current, before)

    def test_kill_and_hit_aliases_share_targeting_and_repeat_is_idempotent(self):
        self.call(CmdAttack(), "Char2", "You begin fighting Char2.", cmdstring="kill")
        self.call(
            CmdAttack(),
            "Char2",
            "You are already fighting Char2.",
            cmdstring="hit",
        )

    def test_invalid_target_has_no_combat_side_effect(self):
        self.call(CmdAttack(), "", "Attack whom?")
        self.call(CmdAttack(), self.char1.key, "You cannot attack yourself.")
        self.assertFalse(is_fighting(self.char1))

    def test_aim_current_target_shorthand_queues_without_an_immediate_hit(self):
        self.call(CmdAttack(), "Char2", "You begin fighting Char2.")
        before = self.char2.stats.hp_current

        self.call(CmdAim(), "body", "You prepare to aim Char2 on your next action.")

        self.assertEqual(self.char2.stats.hp_current, before)

    def test_hide_requires_combat_and_queues_without_rolling(self):
        """Hide consumes the next combat action rather than resolving immediately."""
        self.assertIn("must be fighting", self.call(CmdHide(), ""))
        self.call(CmdAttack(), "Char2", "You begin fighting Char2.")

        output = self.call(CmdHide(), "")

        self.assertIn("prepare to hide", output)
