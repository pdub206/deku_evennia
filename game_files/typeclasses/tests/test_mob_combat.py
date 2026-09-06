"""Regression coverage for MOB-02's combat decisions and retaliation seam."""

from unittest.mock import patch

from evennia import create_object
from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaTest
from systems.attacks import AttackOutcome, AttackResult, can_attack
from systems.combat import COMBAT_CONFIG_KEY, get_target, is_fighting, start_fight
from systems.injury import apply_damage
from systems.mob_combat import (
    MOB_COMBAT_STATE_ATTRIBUTE,
    combat_profile,
    default_combat_profile,
    reconcile_mob_wimpy,
    resolve_mob_combat_action,
    set_combat_profile,
    select_combat_target,
    validate_combat_profile,
)
from systems.pulses import PulseEvent, PulseLane
from typeclasses.characters import Character
from typeclasses.exits import Exit


class TestMobCombat(EvenniaTest):
    """NPCs use the combat lane without a second loop or unprovoked attacks."""

    def setUp(self):
        super().setUp()
        ServerConfig.objects.conf(COMBAT_CONFIG_KEY, delete=True)
        self.npc = create_object(Character, key="Guard", location=self.room1)
        self.npc.db.is_player_character = False
        self.target = create_object(Character, key="Target", location=self.room1)
        self.other = create_object(Character, key="Other", location=self.room1)
        self.target.db.is_player_character = True
        self.other.db.is_player_character = True
        self.npc.db.hp_max_override = self.npc.db.hp_current = 20
        self.event = PulseEvent(2, PulseLane.COMBAT, 1)

    def test_damage_starts_one_retaliation_but_idle_npc_never_attacks_first(self):
        self.assertFalse(is_fighting(self.npc))

        apply_damage(self.npc, 1, source=self.target, emit_messages=False)
        self.assertTrue(is_fighting(self.npc))
        encounter_id = start_fight(self.npc, self.target).encounter_id
        self.assertEqual(encounter_id, start_fight(self.npc, self.target).encounter_id)

    def test_profile_rejects_code_unknown_tactics_and_nonprimitive_data(self):
        with self.assertRaises(ValueError):
            validate_combat_profile(
                {**default_combat_profile(), "tactics": [lambda: None]}
            )
        with self.assertRaises(ValueError):
            validate_combat_profile(
                {
                    **default_combat_profile(),
                    "tactics": [
                        {"action": "gone", "weight": 1, "arguments": {}, "cooldown": 0}
                    ],
                }
            )

    def test_invalid_current_target_retargets_the_lowest_legal_opponent(self):
        start_fight(self.npc, self.other)
        start_fight(self.target, self.npc)
        self.other.db.protected = True

        with patch("systems.mob_combat.resolve_basic_attack") as attack:
            attack.return_value = AttackResult(acted=True, accepted=True)
            resolve_mob_combat_action(self.npc, self.other, self.event)

        self.assertIs(get_target(self.npc), self.target)
        self.assertIs(attack.call_args.args[1], self.target)

    def test_illegal_tactic_falls_back_to_one_basic_attack(self):
        start_fight(self.npc, self.target)
        self.assertTrue(can_attack(self.npc, self.target).allowed)
        set_combat_profile(
            self.npc,
            {
                **default_combat_profile(),
                "tactics": [
                    {"action": "bash", "weight": 2, "arguments": {}, "cooldown": 3}
                ],
            },
        )
        self.assertEqual(combat_profile(self.npc)["tactics"][0]["action"], "bash")
        self.assertIsNotNone(
            select_combat_target(self.npc, self.target, combat_profile(self.npc))
        )
        basic = AttackResult(acted=True, accepted=True, outcome=AttackOutcome.MISS)
        with patch(
            "systems.mob_combat.resolve_basic_attack", return_value=basic
        ) as attack:
            result = resolve_mob_combat_action(self.npc, self.target, self.event)

        self.assertIs(result, basic)
        attack.assert_called_once()

    def test_injected_tactic_selection_and_cooldown_use_the_existing_intent_api(self):
        start_fight(self.npc, self.target)
        self.assertTrue(can_attack(self.npc, self.target).allowed)
        set_combat_profile(
            self.npc,
            {
                **default_combat_profile(),
                "tactics": [
                    {"action": "kick", "weight": 1, "arguments": {}, "cooldown": 3}
                ],
            },
        )
        self.assertEqual(combat_profile(self.npc)["tactics"][0]["action"], "kick")
        accepted = AttackResult(acted=True, accepted=True, outcome=AttackOutcome.MISS)
        with patch(
            "systems.mob_combat.execute_tactical_intent", return_value=accepted
        ), patch(
            "systems.mob_combat.schedule_tactical_action",
            wraps=__import__(
                "systems.mob_combat", fromlist=["schedule_tactical_action"]
            ).schedule_tactical_action,
        ) as schedule:
            result = resolve_mob_combat_action(
                self.npc,
                self.target,
                self.event,
                tactic_chooser=lambda choices: choices[0],
            )

        self.assertIs(result, accepted)
        schedule.assert_called_once()
        state = self.npc.attributes.get(MOB_COMBAT_STATE_ATTRIBUTE)
        self.assertEqual(state["tactic_ready"], {"kick": 4})

    def test_wimpy_latches_no_route_and_rearms_after_healing(self):
        start_fight(self.npc, self.target)
        self.assertTrue(is_fighting(self.npc))
        set_combat_profile(self.npc, {**default_combat_profile(), "wimpy": 50})
        maximum = self.npc.stats.hp_max
        boundary = maximum // 2
        for exit_obj in self.room1.exits:
            exit_obj.locks.add("traverse:false()")
        reconcile_mob_wimpy(self.npc, maximum, boundary)
        self.assertTrue(
            self.npc.attributes.get(MOB_COMBAT_STATE_ATTRIBUTE)["wimpy_triggered"]
        )

        create_object(Exit, key="north", location=self.room1, destination=self.room2)
        reconcile_mob_wimpy(self.npc, boundary, boundary - 1)
        state = ServerConfig.objects.conf(COMBAT_CONFIG_KEY)
        self.assertIsNone(
            state["encounters"]["1"]["participants"][str(self.npc.id)]["pending_intent"]
        )

        reconcile_mob_wimpy(self.npc, boundary, boundary + 1)
        reconcile_mob_wimpy(self.npc, boundary + 1, boundary)
        state = ServerConfig.objects.conf(COMBAT_CONFIG_KEY)
        self.assertEqual(
            state["encounters"]["1"]["participants"][str(self.npc.id)][
                "pending_intent"
            ]["kind"],
            "flee",
        )
