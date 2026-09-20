"""INTERACT-02B room environment, recovery, and hazard coverage."""

from unittest.mock import patch

from evennia.utils.test_resources import EvenniaTest
from systems.effects import EFFECT_REGISTRY, EffectDefinition
from systems.pulses import PulseEvent, PulseLane
from systems.resources import HP_RESOURCE, recover_resource
from systems.room_environment import (
    ENTRY_HAZARD_TOKENS_ATTRIBUTE,
    ENTRY_HAZARDS,
    ROOM_ENVIRONMENT_ATTRIBUTE,
    EntryHazard,
    HazardConsequence,
    LightLevel,
    RoomEnvironmentError,
    room_environment,
    set_room_environment_value,
    trigger_entry_hazard,
    validate_room_environment,
)

_TEST_EFFECT = EffectDefinition(key="test.interact02b.effect", name="Hazard Effect")
if EFFECT_REGISTRY.get(_TEST_EFFECT.key) is None:
    EFFECT_REGISTRY.register(_TEST_EFFECT)

for _hazard in (
    EntryHazard(
        "test.interact02b.damage",
        HazardConsequence.DAMAGE,
        amount=2,
        damage_type="fire",
    ),
    EntryHazard(
        "test.interact02b.effect",
        HazardConsequence.EFFECT,
        effect_key=_TEST_EFFECT.key,
    ),
    EntryHazard("test.interact02b.death", HazardConsequence.DEATH),
):
    if ENTRY_HAZARDS.get(_hazard.key) is None:
        ENTRY_HAZARDS.register(_hazard)


class TestRoomEnvironment(EvenniaTest):
    """All environment consumers share one closed validated record."""

    def setUp(self):
        super().setUp()
        for room in (self.room1, self.room2):
            self.addCleanup(room.attributes.remove, ROOM_ENVIRONMENT_ATTRIBUTE)
        self.addCleanup(self.char1.attributes.remove, ENTRY_HAZARD_TOKENS_ATTRIBUTE)
        self.char1.db.level = 1
        self.char1.db.constitution = 14
        self.char1.db.hp_base = 20
        self.char1.db.hp_current = 10
        self.char2.permissions.clear()
        self.char2.db.level = 1
        self.char2.db.constitution = 14
        self.char2.db.hp_base = 20
        self.char2.db.hp_current = 10

    def test_defaults_independent_fields_levels_and_malformed_data(self):
        environment = room_environment(self.room1)
        self.assertFalse(environment.indoors)
        self.assertEqual(environment.light, LightLevel.BRIGHT)
        for light in LightLevel:
            set_room_environment_value(self.room1, "light", light.value)
            self.assertEqual(room_environment(self.room1).light, light)
        set_room_environment_value(self.room1, "indoors", True)
        self.assertEqual(room_environment(self.room1).light, LightLevel.DARK)
        for raw in (
            {"light": "gloomy"},
            {"recovery_multiplier": 4},
            {"entry_hazard": "unknown"},
            {"extra": True},
        ):
            with self.assertRaises(RoomEnvironmentError):
                validate_room_environment(raw)

    def test_recovery_multiplier_bounds_feed_existing_formula(self):
        def event(sequence):
            return PulseEvent(sequence * 60, PulseLane.RECOVERY, sequence)

        set_room_environment_value(self.room1, "recovery_multiplier", 0)
        zero = recover_resource(self.char1, HP_RESOURCE, event(1))
        self.assertEqual(zero.attempted_gain, 0)
        set_room_environment_value(self.room1, "recovery_multiplier", 3)
        maximum = recover_resource(self.char1, HP_RESOURCE, event(2))
        self.assertEqual(maximum.attempted_gain, 9)
        self.char1.db.hp_base = 11
        changed_maximum = self.char1.stats.hp_max
        self.char1.stats.set_hp(changed_maximum - 1)
        capped = recover_resource(self.char1, HP_RESOURCE, event(3))
        self.assertEqual(capped.final_value, changed_maximum)

    def test_damage_and_effect_hazards_are_once_per_arrival(self):
        set_room_environment_value(
            self.room2, "entry_hazard", "test.interact02b.damage"
        )
        self.assertTrue(
            self.char2.move_to(self.room2, quiet=True, move_type="traverse")
        )
        self.assertEqual(self.char2.stats.hp_current, 8)
        token = self.char2.attributes.get(ENTRY_HAZARD_TOKENS_ATTRIBUTE)["tokens"][0]
        self.assertFalse(trigger_entry_hazard(self.char2, self.room2, token))
        self.assertEqual(self.char2.stats.hp_current, 8)

        self.char2.move_to(self.room1, quiet=True, move_type="forced")
        set_room_environment_value(
            self.room2, "entry_hazard", "test.interact02b.effect"
        )
        self.char2.move_to(self.room2, quiet=True, move_type="traverse")
        self.assertTrue(self.char2.effects.has(_TEST_EFFECT.key))

    def test_failed_and_bypassed_moves_do_not_trigger_unless_opted_in(self):
        set_room_environment_value(
            self.room2, "entry_hazard", "test.interact02b.damage"
        )
        with patch.object(self.char2, "at_pre_move", return_value=False):
            self.assertFalse(
                self.char2.move_to(self.room2, quiet=True, move_type="traverse")
            )
        self.assertEqual(self.char2.stats.hp_current, 10)
        self.char2.move_to(self.room2, quiet=True, move_type="forced")
        self.assertEqual(self.char2.stats.hp_current, 10)
        self.char2.move_to(self.room1, quiet=True, move_type="forced")
        self.char2.move_to(
            self.room2,
            quiet=True,
            move_type="forced",
            trigger_entry_hazard=True,
            arrival_id="explicit-arrival",
        )
        self.assertEqual(self.char2.stats.hp_current, 8)

    def test_malformed_hazard_data_cannot_undo_a_committed_arrival(self):
        self.room2.db.room_environment = {"entry_hazard": "unknown"}
        self.assertTrue(
            self.char2.move_to(self.room2, quiet=True, move_type="traverse")
        )
        self.assertIs(self.char2.location, self.room2)
        self.assertEqual(self.char2.stats.hp_current, 10)

    def test_death_hazard_delegates_to_terminal_injury_service(self):
        set_room_environment_value(self.room2, "entry_hazard", "test.interact02b.death")
        with patch("systems.injury.apply_terminal_death") as terminal:
            trigger_entry_hazard(self.char1, self.room2, "death-arrival")
        terminal.assert_called_once_with(
            self.char1, source_kind="environment:test.interact02b.death"
        )
