"""End-to-end Milestone 1 terminal-combat regression coverage."""

from django.test import override_settings
from evennia import create_object
from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaTest
from systems.areas import AREA_TAG_CATEGORY, ROOM_KEY_CATEGORY
from systems.attacks import resolve_basic_attack
from systems.combat import (
    COMBAT_CONFIG_KEY,
    is_fighting,
    join_fight,
    process_combat_pulse,
    set_combat_action_hook,
    start_fight,
)
from systems.corpses import corpse_record
from systems.injury import InjuryState, injury_record
from systems.pulses import PulseEvent, PulseLane
from systems.respawn import prepare_entry
from systems.rewards import reward_result
from typeclasses.characters import Character
from typeclasses.objects import Corpse


class TestMilestoneOneExitGate(EvenniaTest):
    """Live pulse combat feeds every terminal Milestone 1 system exactly once."""

    def setUp(self):
        super().setUp()
        ServerConfig.objects.conf(COMBAT_CONFIG_KEY, delete=True)
        self.addCleanup(set_combat_action_hook, None)

    def _player(self, key):
        """Create an ordinary player-owned character in the test room."""
        character = create_object(Character, key=key, location=self.room1)
        character.db.is_player_character = True
        return character

    def _npc(self, key):
        """Create an ordinary mortal NPC in the test room."""
        character = create_object(Character, key=key, location=self.room1)
        character.db.is_player_character = False
        return character

    def test_two_players_defeat_one_mobile_for_one_corpse_loot_and_xp_award(self):
        """The victory exit path preserves equipment, loot, and reward identity."""
        player_one = self._player("Player One")
        player_two = self._player("Player Two")
        mobile = self._npc("Mobile")
        mobile.db.hp_current = 10
        mobile.db.xp_reward = 25
        player_one.db.xp = 0
        sword = create_object(
            "typeclasses.objects.Item",
            key="victory sword",
            location=player_one,
            attributes=(
                ("type", "weapon"),
                ("subtype", "slashing"),
                ("damage", "1d6"),
                ("weapon_category", "simple"),
                ("wear_locations", ["wield"]),
                ("worn_location", "wield"),
            ),
        )
        loot = create_object(
            "typeclasses.objects.Item", key="mobile token", location=mobile
        )
        self.assertIs(player_one.equipment.wielded_weapon, sword)

        start_fight(player_one, mobile)
        join_fight(player_two, mobile)
        set_combat_action_hook(
            lambda actor, target, event: resolve_basic_attack(
                actor,
                target,
                event,
                die_roller=lambda sides: sides,
                location_selector=lambda *_: "body",
                emit_messages=False,
            )
        )

        event = PulseEvent(2, PulseLane.COMBAT, 1)
        first = process_combat_pulse(event)
        corpses = tuple(Corpse.objects.filter_family())

        self.assertGreaterEqual(first.actions, 1)
        self.assertFalse(is_fighting(player_one))
        self.assertFalse(is_fighting(player_two))
        self.assertEqual(player_one.stats.xp, 25)
        self.assertEqual(len(corpses), 1)
        self.assertIs(loot.location, corpses[0])
        death_id = corpse_record(corpses[0]).death_id
        self.assertEqual(reward_result(death_id).final_xp, 25)

        replay = process_combat_pulse(event)
        self.assertFalse(replay.processed)
        self.assertEqual(player_one.stats.xp, 25)
        self.assertEqual(
            len(
                tuple(
                    corpse
                    for corpse in Corpse.objects.filter_family()
                    if corpse_record(corpse).death_id == death_id
                )
            ),
            1,
        )

    @override_settings(COMBAT_RESPAWN_SANCTUARY="sanctuary:return")
    def test_mobile_defeat_creates_recoverable_corpse_then_respawns_player(self):
        """The defeat exit path retains possessions until the respawn transaction."""
        player = self._player("Defeated Player")
        mobile = self._npc("Deadly Mobile")
        self.room2.tags.add("sanctuary", category=AREA_TAG_CATEGORY)
        self.room2.tags.add("return", category=ROOM_KEY_CATEGORY)
        player.db.hp_max_override = 20
        player.stats.set_hp(1)
        keepsake = create_object(
            "typeclasses.objects.Item", key="keepsake", location=player
        )
        create_object(
            "typeclasses.objects.Item",
            key="deadly sword",
            location=mobile,
            attributes=(
                ("type", "weapon"),
                ("subtype", "slashing"),
                ("damage", "2d6"),
                ("weapon_category", "simple"),
                ("wear_locations", ["wield"]),
                ("worn_location", "wield"),
            ),
        )
        start_fight(player, mobile)
        set_combat_action_hook(
            lambda actor, target, event: resolve_basic_attack(
                actor,
                target,
                event,
                die_roller=(
                    (lambda _sides: 1) if actor is player else (lambda sides: sides)
                ),
                location_selector=lambda *_: "body",
                emit_messages=False,
            )
        )

        process_combat_pulse(PulseEvent(2, PulseLane.COMBAT, 1))
        record = injury_record(player)
        corpses = tuple(
            corpse
            for corpse in Corpse.objects.filter_family()
            if corpse_record(corpse).death_id == record.death_id
        )

        self.assertEqual(record.state, InjuryState.DEAD)
        self.assertFalse(is_fighting(player))
        self.assertEqual(len(corpses), 1)
        self.assertIs(keepsake.location, corpses[0])

        prepare_entry(player)

        self.assertIs(player.location, self.room2)
        self.assertEqual(player.stats.hp_current, player.stats.hp_max)
        self.assertEqual(player.db.position, "resting")
        self.assertEqual(injury_record(player).state, InjuryState.CONSCIOUS)
        self.assertIs(keepsake.location, corpses[0])
