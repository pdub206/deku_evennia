"""COMBAT-07 attribution and NPC experience regression coverage."""

from django.test import override_settings
from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems import groups
from systems.advancement import award_xp, initialize_level_one
from systems.corpses import can_withdraw, corpse_record, process_corpse_pulse
from systems.injury import apply_damage
from systems.pulses import PulseEvent, PulseLane
from systems.rewards import record_damage, reward_result
from typeclasses.objects import Corpse


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

    def _group_with_char3(self):
        """Create a same-room, equal-level party for shared-reward tests."""
        self.char3 = create_object(
            "typeclasses.characters.Character", key="Char3", location=self.room1
        )
        self.char3.db.hp_current = 20
        self.char3.db.char_class = "Fighter"
        self.char3.db.hit_die = 10
        initialize_level_one(self.char3, class_key="Fighter", hp_base=10)
        self.char3.db.hp_current = self.char3.stats.hp_max
        award_xp(
            self.char3, 900, source_kind="test_setup", source_id=f"{self.id()}-three"
        )
        self.assertTrue(groups.invite(self.char1, self.char3).accepted)
        self.assertTrue(groups.accept(self.char3, self.char1).accepted)

    def test_group_splits_frozen_roster_with_remainder_in_join_order(self):
        """A responsible party member snapshots and splits its authored base XP."""
        self._group_with_char3()
        injury = apply_damage(self.char2, 10, source=self.char1, emit_messages=False)
        result = reward_result(injury.death_id)

        self.assertEqual(result.frozen_roster, (self.char1.id, self.char3.id))
        self.assertEqual(
            [(share.recipient_id, share.raw_xp) for share in result.shares],
            [(self.char1.id, 6), (self.char3.id, 5)],
        )
        self.assertEqual(self.char1.stats.xp, 906)
        self.assertEqual(self.char3.stats.xp, 905)

    def test_late_joiner_is_not_in_the_contribution_roster(self):
        """Joining after damage cannot turn a solo contribution into a group split."""
        record_damage(self.char2, self.char1)
        self._group_with_char3()
        injury = apply_damage(
            self.char2, 10, source=None, source_kind="environment", emit_messages=False
        )
        result = reward_result(injury.death_id)

        self.assertEqual(result.frozen_roster, ())
        self.assertEqual(
            [share.recipient_id for share in result.shares], [self.char1.id]
        )
        self.assertEqual(self.char3.stats.xp, 900)

    @override_settings(NPC_CORPSE_LOOT_RESERVATION_MINUTES=1)
    def test_group_death_roster_reserves_then_releases_npc_corpse_loot(self):
        """Loot uses the resolved XP roster, not current party membership."""
        self._group_with_char3()
        outsider = create_object(
            "typeclasses.characters.Character", key="outsider", location=self.room1
        )
        outsider.db.hp_current = 20
        item = create_object(
            "typeclasses.objects.Item", key="relic", location=self.char2
        )

        injury = apply_damage(self.char2, 10, source=self.char1, emit_messages=False)
        corpse = next(
            corpse
            for corpse in Corpse.objects.filter_family()
            if corpse_record(corpse).death_id == injury.death_id
        )

        self.assertEqual(
            corpse_record(corpse).loot_recipient_ids,
            (self.char1.id, self.char3.id),
        )
        self.assertTrue(can_withdraw(corpse, self.char3))
        self.assertFalse(can_withdraw(corpse, outsider))
        self.assertTrue(groups.leave(self.char3).accepted)
        self.assertTrue(can_withdraw(corpse, self.char3))
        self.assertIs(item.location, corpse)

        process_corpse_pulse(PulseEvent(60, PulseLane.CORPSES, 1))

        self.assertEqual(corpse_record(corpse).reservation_remaining_pulses, 0)
        self.assertTrue(can_withdraw(corpse, outsider))
