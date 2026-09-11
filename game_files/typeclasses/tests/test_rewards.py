"""COMBAT-07 attribution and NPC experience regression coverage."""

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.advancement import award_xp, initialize_level_one
from systems.injury import apply_damage
from systems.rewards import record_damage, reward_result


class TestCombatRewards(EvenniaTest):
    """NPC deaths resolve one durable, eligibility-aware XP transaction."""

    def setUp(self):
        super().setUp()
        self.char1.db.hp_current = 20
        self.char1.db.char_class = "Fighter"
        self.char1.db.hit_die = 10
        initialize_level_one(self.char1, class_key="Fighter", hp_base=10)
        self.char1.db.hp_current = self.char1.stats.hp_max
        award_xp(
            self.char1,
            900,
            source_kind="test_setup",
            source_id=self.id(),
        )
        self.char2.db.is_player_character = False
        self.char2.db.hp_current = 10
        self.char2.db.hp_base = 10
        self.char2.db.level = 3
        self.char2.db.xp_reward = 11

    def test_direct_kill_awards_adjusted_xp_once(self):
        """The structured death identity prevents repeated payouts."""
        injury = apply_damage(self.char2, 10, source=self.char1, emit_messages=False)
        result = reward_result(injury.death_id)

        self.assertEqual(result.final_xp, 11)
        self.assertEqual(result.credited_id, self.char1.id)
        self.assertEqual(self.char1.stats.xp, 911)
        # Replaying the same death is served from its durable audit result.
        self.assertEqual(reward_result(injury.death_id).final_xp, 11)
        self.assertEqual(self.char1.stats.xp, 911)

    def test_level_adjustment_uses_the_released_pc_level(self):
        """Reward scaling remains valid while PCs are capped at level three."""
        self.char2.db.level = 1
        self.char2.db.xp_reward = 10
        low = apply_damage(self.char2, 10, source=self.char1, emit_messages=False)
        self.assertEqual(reward_result(low.death_id).final_xp, 8)

        npc = create_object(
            "typeclasses.characters.Character", key="high foe", location=self.room1
        )
        npc.db.is_player_character = False
        npc.db.hp_current = 10
        npc.db.hp_base = 10
        npc.db.level = 20
        npc.db.xp_reward = 1
        high = apply_damage(npc, 10, source=self.char1, emit_messages=False)
        self.assertEqual(reward_result(high.death_id).final_xp, 2)

    def test_environment_uses_most_recent_eligible_contributor(self):
        """Ongoing or environmental damage retains the latest responsible PC."""
        record_damage(self.char2, self.char1)
        injury = apply_damage(
            self.char2, 10, source=None, source_kind="environment", emit_messages=False
        )
        result = reward_result(injury.death_id)
        self.assertEqual(result.credited_id, self.char1.id)
        self.assertEqual(result.final_xp, 11)

    def test_remote_contributor_is_not_deferred_until_return(self):
        """A remote contributor is rejected at the exact death-time check."""
        record_damage(self.char2, self.char1)
        self.char1.move_to(self.room2, quiet=True)
        injury = apply_damage(
            self.char2, 10, source=None, source_kind="environment", emit_messages=False
        )
        self.assertEqual(reward_result(injury.death_id).reason, "no_eligible_recipient")

    def test_missing_xp_reward_is_a_consumed_no_reward(self):
        """Old or malformed NPC prototypes cannot produce a later duplicate award."""
        self.char2.attributes.remove("xp_reward")
        injury = apply_damage(self.char2, 10, source=self.char1, emit_messages=False)
        result = reward_result(injury.death_id)
        self.assertEqual(result.reason, "invalid_xp_reward")
        self.assertEqual(self.char1.stats.xp, 900)
