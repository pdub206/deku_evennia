"""COMBAT-03 flee intent and movement-bypass regression tests."""

from evennia import create_object
from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaTest
from systems.combat import (
    COMBAT_CONFIG_KEY,
    process_combat_pulse,
    schedule_flee,
    start_fight,
)
from systems.combat_movement import (
    choose_flee_exit,
    eligible_flee_exits,
    execute_flee_intent,
)
from systems.pulses import PulseEvent, PulseLane
from typeclasses.characters import Character
from typeclasses.exits import Exit


class TestCombatFlee(EvenniaTest):
    """Flee moves only at the actor's next scheduled combat action."""

    def setUp(self):
        super().setUp()
        ServerConfig.objects.conf(COMBAT_CONFIG_KEY, delete=True)
        self.exit = create_object(
            Exit, key="north", location=self.room1, destination=self.room2
        )
        self.assertEqual(self.exit.destination, self.room2)
        start_fight(self.char1, self.char2)

    def test_flee_queues_then_moves_once_on_due_pulse(self):
        queued = schedule_flee(self.char1, self.exit.id)
        self.assertTrue(queued.accepted)
        state = ServerConfig.objects.conf(COMBAT_CONFIG_KEY)
        encounter = state["encounters"][str(queued.encounter_id)]
        self.assertEqual(
            encounter["participants"][str(self.char1.id)]["pending_intent"],
            {"kind": "flee", "exit": self.exit.id},
        )
        self.assertEqual(self.char1.location, self.room1)

        result = process_combat_pulse(PulseEvent(2, PulseLane.COMBAT, 1))

        self.assertGreaterEqual(result.actions, 1)
        self.assertEqual(Character.objects.get(id=self.char1.id).location, self.room2)
        # The persisted intent is consumed under the pulse token and cannot replay.
        self.assertFalse(
            process_combat_pulse(PulseEvent(2, PulseLane.COMBAT, 1)).processed
        )

    def test_normal_traversal_stays_blocked_while_fighting(self):
        self.assertFalse(
            self.char1.move_to(self.room2, quiet=True, move_type="traverse")
        )
        self.assertEqual(self.char1.location, self.room1)

    def test_internal_flee_service_can_move_a_fighting_character(self):
        self.assertTrue(
            execute_flee_intent(self.char1, {"kind": "flee", "exit": self.exit.id})
        )
        self.assertEqual(self.char1.location, self.room2)

    def test_internal_flee_service_can_move_a_reloaded_character(self):
        actor = Character.objects.get(id=self.char1.id)
        self.assertTrue(
            execute_flee_intent(actor, {"kind": "flee", "exit": self.exit.id})
        )

    def test_only_eligible_routes_are_selectable(self):
        for exit_obj in self.room1.exits:
            exit_obj.locks.add("traverse:false()")
        self.assertEqual(eligible_flee_exits(self.char1), ())
        self.assertFalse(choose_flee_exit(self.char1).allowed)
