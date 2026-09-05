"""Regression coverage for COMBAT-09 controls and safe presentation."""

from evennia import create_object
from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaTest
from systems.combat import COMBAT_CONFIG_KEY, start_fight
from systems.combat_controls import (
    DEFAULT_VERBOSE,
    combat_prompt_data,
    estimate_threat,
    get_combat_prompt,
    get_combat_verbose,
    get_wimpy,
    health_description,
    set_wimpy,
)
from systems.injury import apply_damage
from typeclasses.characters import Character
from typeclasses.exits import Exit


class TestCombatControls(EvenniaTest):
    """Controls observe canonical state without creating a parallel engine."""

    def setUp(self):
        super().setUp()
        ServerConfig.objects.conf(COMBAT_CONFIG_KEY, delete=True)
        self.pc = create_object(Character, key="PC", location=self.room1)
        self.pc.db.is_player_character = True
        self.char2.db.is_player_character = False
        self.pc.db.hp_base = self.char2.db.hp_base = 20
        self.pc.db.hp_max_override = self.char2.db.hp_max_override = 20
        self.pc.stats.set_hp(20)
        self.char2.stats.set_hp(20)

    def test_health_description_boundaries_and_terminal_states(self):
        for hp, description in (
            (20, "unhurt"),
            (19, "healthy"),
            (15, "wounded"),
            (10, "badly wounded"),
            (1, "near death"),
        ):
            self.pc.stats.set_hp(hp)
            self.assertEqual(health_description(self.pc), description)

        self.pc.stats.set_hp(0)
        self.assertEqual(health_description(self.pc), "dying")

    def test_estimate_reads_live_equipment_independent_stats_without_mutation(self):
        before = (self.pc.stats.hp_current, self.char2.stats.hp_current)
        first = estimate_threat(self.pc, self.char2)
        self.char2.db.armor_class_override = 30
        second = estimate_threat(self.pc, self.char2)

        self.assertEqual(
            before, (self.pc.stats.hp_current, self.char2.stats.hp_current)
        )
        self.assertLess(second.observer_hit_rate, first.observer_hit_rate)
        self.assertIn(
            first.band,
            {"trivial", "easy", "even", "dangerous", "deadly", "overwhelming"},
        )

    def test_malformed_preferences_repair_and_prompt_hides_target_numbers(self):
        self.pc.attributes.add("combat_prompt", "wrong")
        self.pc.attributes.add("combat_verbose", "wrong")
        self.pc.attributes.add("combat_wimpy", 99)
        self.assertTrue(get_combat_prompt(self.pc))
        self.assertEqual(get_combat_verbose(self.pc), DEFAULT_VERBOSE)
        self.assertEqual(get_wimpy(self.pc), 0)

        start_fight(self.pc, self.char2)
        data = combat_prompt_data(self.pc)
        self.assertEqual(data["hp"]["current"], self.pc.stats.hp_current)
        self.assertEqual(data["target_health"], "unhurt")
        self.assertNotIn("armor_class", data)

    def test_wimpy_queues_one_flee_and_healing_rearms_it(self):
        exit_obj = create_object(
            Exit, key="north", location=self.room1, destination=self.room2
        )
        start_fight(self.pc, self.char2)
        set_wimpy(self.pc, 50)
        apply_damage(self.pc, 10, emit_messages=False)

        state = ServerConfig.objects.conf(COMBAT_CONFIG_KEY)
        record = state["encounters"]["1"]["participants"][str(self.pc.id)]
        self.assertEqual(record["pending_intent"]["kind"], "flee")
        self.assertIn(
            record["pending_intent"]["exit"],
            {exit_obj.id, *[exit.id for exit in self.room1.exits]},
        )
        self.assertEqual(combat_prompt_data(self.pc)["wimpy"], "triggered")

        self.pc.stats.heal(1)
        self.assertEqual(combat_prompt_data(self.pc)["wimpy"], "armed")
