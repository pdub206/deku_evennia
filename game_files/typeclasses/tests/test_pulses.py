"""Tests for the persistent WORLD-01 game pulse service."""

from unittest.mock import MagicMock, call, patch

from django.test import override_settings
from evennia import create_object
from evennia.utils.containers import GlobalScriptContainer
from evennia.utils.test_resources import EvenniaTest
from systems.effects import (EFFECT_REGISTRY, EFFECTS_ATTRIBUTE,
                             EffectDefinition, EffectMessage)
from systems.pulses import (PULSE_LANES, PulseError, PulseEvent, PulseLane,
                            advance_pulse_state, configured_cadences,
                            initial_pulse_state, process_effect_pulse)
from typeclasses.scripts import GamePulseScript


def _register(definition: EffectDefinition) -> None:
    """Register a test definition once across repeated test discovery."""
    if EFFECT_REGISTRY.get(definition.key) is None:
        EFFECT_REGISTRY.register(definition)


_TIMED = EffectDefinition(
    key="test.world01.timed",
    name="Pulse Slow",
    duration=2,
    messages={
        "expire": EffectMessage(
            target="The pulse slow ends.", room="{target}'s pulse slow ends."
        )
    },
)
_PERMANENT = EffectDefinition(
    key="test.world01.permanent",
    name="Pulse Ward",
)
for _definition in (_TIMED, _PERMANENT):
    _register(_definition)


class TestPulseState(EvenniaTest):
    """The scheduler advances validated lanes with stable sequence tokens."""

    script_typeclass = GamePulseScript

    def test_initial_state_and_script_timer_contract(self):
        self.assertEqual(self.script.db.pulse_state, initial_pulse_state())
        self.assertEqual(self.script.interval, 1)
        self.assertTrue(self.script.start_delay)
        self.assertEqual(self.script.repeats, 0)
        self.assertTrue(self.script.persistent)

    @override_settings(
        GAME_PULSE_CADENCES={
            "combat": 2,
            "recovery": 100,
            "mobiles": 100,
            "effects": 3,
            "corpses": 100,
            "world_time": 100,
            "weather": 100,
            "resets": 100,
        }
    )
    def test_lanes_dispatch_only_when_due_with_independent_sequences(self):
        with (
            patch.object(self.script, "at_combat_pulse") as combat,
            patch.object(self.script, "at_effects_pulse") as effects,
        ):
            for _ in range(6):
                self.script.at_repeat()

        self.assertEqual(
            [call.args[0] for call in combat.call_args_list],
            [
                PulseEvent(2, PulseLane.COMBAT, 1),
                PulseEvent(4, PulseLane.COMBAT, 2),
                PulseEvent(6, PulseLane.COMBAT, 3),
            ],
        )
        self.assertEqual(
            [call.args[0] for call in effects.call_args_list],
            [
                PulseEvent(3, PulseLane.EFFECTS, 1),
                PulseEvent(6, PulseLane.EFFECTS, 2),
            ],
        )
        self.assertEqual(self.script.db.pulse_state["heartbeat"], 6)

    @override_settings(GAME_PULSE_CADENCES={lane.value: 1 for lane in PULSE_LANES})
    def test_lane_failure_does_not_block_other_due_lanes(self):
        with (
            patch.object(
                self.script,
                "at_combat_pulse",
                side_effect=RuntimeError("combat failed"),
            ),
            patch.object(self.script, "at_effects_pulse") as effects,
            patch("typeclasses.scripts.logger.log_trace") as log_trace,
        ):
            self.script.at_repeat()

        effects.assert_called_once_with(PulseEvent(1, PulseLane.EFFECTS, 1))
        log_trace.assert_called_once()
        self.assertIn("lane 'combat'", log_trace.call_args.args[0])
        self.assertEqual(self.script.db.pulse_state["heartbeat"], 1)
        self.assertEqual(self.script.db.pulse_state["lane_sequences"]["combat"], 1)

    def test_recovery_lane_uses_the_shared_resource_processor(self):
        event = PulseEvent(60, PulseLane.RECOVERY, 1)
        with patch("typeclasses.scripts.process_resource_recovery_pulse") as recovery:
            self.script.at_recovery_pulse(event)
        recovery.assert_called_once_with(event)

    def test_persisted_state_reconstructs_without_replaying_a_token(self):
        cadences = {lane: 1 for lane in PULSE_LANES}
        first_state, first_events = advance_pulse_state(initial_pulse_state(), cadences)
        self.script.db.pulse_state = first_state

        reloaded = GamePulseScript.objects.get(pk=self.script.pk)
        second_state, second_events = advance_pulse_state(
            reloaded.db.pulse_state, cadences
        )

        self.assertTrue(all(event.sequence == 1 for event in first_events))
        self.assertTrue(all(event.sequence == 2 for event in second_events))
        self.assertEqual(second_state["heartbeat"], 2)

    def test_reload_hooks_do_not_advance_or_replace_persistent_state(self):
        before = self.script.db.pulse_state
        before_next = self.script.time_until_next_repeat()

        self.script._pause_task(auto_pause=True)
        paused_for = self.script.db._paused_time
        self.script.at_server_reload()
        self.script._unpause_task(auto_unpause=True)
        self.script.at_server_start()

        self.assertEqual(self.script.db.pulse_state, before)
        self.assertIsNotNone(before_next)
        self.assertIsNotNone(paused_for)
        after_next = self.script.time_until_next_repeat()
        self.assertIsNotNone(after_next)
        self.assertLessEqual(abs(after_next - before_next), 1)

    def test_global_script_setting_reuses_one_database_script(self):
        container = GlobalScriptContainer()

        first = container.get("game_pulse")
        second = container.get("game_pulse")

        self.assertIsNotNone(first)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            GamePulseScript.objects.filter(
                db_key="game_pulse", db_account__isnull=True, db_obj__isnull=True
            ).count(),
            1,
        )

    @override_settings(GAME_PULSE_CADENCES={"effects": True})
    def test_invalid_cadence_is_rejected(self):
        with self.assertRaises(PulseError):
            configured_cadences()

    def test_malformed_persistent_state_is_rejected(self):
        malformed = initial_pulse_state()
        del malformed["lane_sequences"]["effects"]

        with self.assertRaises(PulseError):
            advance_pulse_state(malformed, {lane: 1 for lane in PULSE_LANES})


class TestEffectPulse(EvenniaTest):
    """The live effect lane advances PCs and NPCs without shared failures."""

    def setUp(self):
        super().setUp()
        self.char1.attributes.remove(EFFECTS_ATTRIBUTE)
        self.char2.attributes.remove(EFFECTS_ATTRIBUTE)

    def test_pc_npc_and_permanent_effects_share_the_live_pulse(self):
        npc = create_object(
            "typeclasses.characters.Character",
            key="Pulse NPC",
            location=self.room1,
        )
        self.char1.msg = MagicMock()
        self.room1.msg_contents = MagicMock()
        pc_effect = self.char1.effects.add(_TIMED.key, quiet=True)
        npc_effect = npc.effects.add(_TIMED.key, duration=1, quiet=True)
        permanent = self.char2.effects.add(_PERMANENT.key, quiet=True)

        first = process_effect_pulse(PulseEvent(6, PulseLane.EFFECTS, 1))
        second = process_effect_pulse(PulseEvent(12, PulseLane.EFFECTS, 2))
        third = process_effect_pulse(PulseEvent(18, PulseLane.EFFECTS, 3))

        self.assertEqual((first.processed, first.removals, first.failures), (3, 1, 0))
        self.assertEqual(
            (second.processed, second.removals, second.failures), (2, 1, 0)
        )
        self.assertEqual((third.processed, third.removals, third.failures), (1, 0, 0))
        self.assertIsNone(npc.effects.get(npc_effect.effect.instance_id))
        self.assertIsNone(self.char1.effects.get(pc_effect.effect.instance_id))
        self.assertIsNotNone(self.char2.effects.get(permanent.effect.instance_id))
        self.char1.msg.assert_called_once_with("The pulse slow ends.")
        self.assertEqual(
            self.room1.msg_contents.call_args_list,
            [
                call(f"{npc.key}'s pulse slow ends.", exclude=[npc], from_obj=npc),
                call(
                    f"{self.char1.key}'s pulse slow ends.",
                    exclude=[self.char1],
                    from_obj=self.char1,
                ),
            ],
        )

    def test_malformed_owner_does_not_block_valid_effect_expiry(self):
        self.char1.effects.add(_TIMED.key, duration=1, quiet=True)
        self.char2.db.active_effects = {"version": 999, "instances": {}}

        with patch("systems.pulses.logger.log_trace") as log_trace:
            result = process_effect_pulse(PulseEvent(6, PulseLane.EFFECTS, 1))

        self.assertEqual(
            (result.processed, result.removals, result.failures), (1, 1, 1)
        )
        self.assertFalse(self.char1.effects.has(_TIMED.key))
        log_trace.assert_called_once()
        self.assertIn(f"object #{self.char2.id}", log_trace.call_args.args[0])
