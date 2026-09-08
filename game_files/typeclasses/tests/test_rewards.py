"""COMBAT-07 attribution and NPC experience regression coverage."""

from unittest.mock import patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.advancement import initialize_level_one
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
        self.char1.db.xp = 6500
        self.char1.db.level = 5
        self.char2.db.is_player_character = False
        self.char2.db.hp_current = 10
        self.char2.db.hp_base = 10
        self.char2.db.level = 5
        self.char2.db.xp_reward = 11

    def test_direct_kill_awards_adjusted_xp_once(self):
        """The structured death identity prevents repeated payouts."""
        injury = apply_damage(self.char2, 10, source=self.char1, emit_messages=False)
        result = reward_result(injury.death_id)

        self.assertEqual(result.final_xp, 11)
        self.assertEqual(result.credited_id, self.char1.id)
        self.assertEqual(self.char1.stats.xp, 6511)
        # Replaying the same death is served from its durable audit result.
        self.assertEqual(reward_result(injury.death_id).final_xp, 11)
        self.assertEqual(self.char1.stats.xp, 6511)

    def test_death_reward_crosses_a_level_through_progression_transaction(self):
        """COMBAT-07 rewards use ADV-01's provenance and replay boundary."""
        initialize_level_one(self.char1, class_key="Fighter", hp_base=10)
        self.char1.db.xp = 0
        self.char1.db.level = 1
        self.char2.db.xp_reward = 300
        with patch("systems.advancement.is_level_published", return_value=True):
            injury = apply_damage(
                self.char2, 10, source=self.char1, emit_messages=False
            )

        result = reward_result(injury.death_id)
        provenance = self.char1.db.class_progression
        self.assertEqual((result.final_xp, self.char1.db.level), (420, 2))
        self.assertEqual([entry["level"] for entry in provenance["levels"]], [1, 2])
        self.assertEqual(reward_result(injury.death_id).resulting_xp, 420)

    def test_level_adjustment_clamps_floors_and_keeps_minimum_one(self):
        """The authored base is adjusted only after a recipient is selected."""
        self.char2.db.level = 1
        self.char2.db.xp_reward = 9
        self.char1.db.level = 11
        self.char1.db.xp = 85000
        low = apply_damage(self.char2, 10, source=self.char1, emit_messages=False)
        self.assertEqual(reward_result(low.death_id).final_xp, 0)

        self.char1.db.level = 10
        self.char1.db.xp = 64000
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
        self.assertEqual(self.char1.stats.xp, 6500)
