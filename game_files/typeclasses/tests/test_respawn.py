"""COMBAT-06 post-death entry and link-dead regression coverage."""

from django.test import override_settings
from evennia.utils.test_resources import EvenniaTest
from systems.areas import AREA_TAG_CATEGORY, ROOM_KEY_CATEGORY
from systems.effects import EFFECT_REGISTRY, EffectDefinition
from systems.injury import INJURY_ATTRIBUTE, INJURY_VERSION, InjuryState
from systems.pulses import PulseEvent, PulseLane
from systems.respawn import (
    LINKDEAD_ATTRIBUTE,
    _on_character_lifecycle,
    prepare_entry,
    process_linkdead_pulse,
)
from systems.lifecycle import (
    CharacterAvailability,
    CharacterLifecycleEvent,
    UnavailabilityCause,
)

_TEMPORARY = EffectDefinition(
    key="test.combat06.temporary", name="Temporary", duration=2
)
_PERMANENT = EffectDefinition(key="test.combat06.permanent", name="Permanent")
for _definition in (_TEMPORARY, _PERMANENT):
    if EFFECT_REGISTRY.get(_definition.key) is None:
        EFFECT_REGISTRY.register(_definition)


def _injury_payload(state: InjuryState) -> dict[str, object]:
    """Return a valid raw injury record without triggering terminal hooks."""
    return {
        "version": INJURY_VERSION,
        "state": state.value,
        "successes": 0,
        "failures": 0,
        "last_recovery": 0,
        "death_id": "death-test" if state is InjuryState.DEAD else None,
    }


class TestRespawn(EvenniaTest):
    """Entry uses stable sanctuary tags and does not need a respawn command."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.room2.tags.add("sanctuary", category=AREA_TAG_CATEGORY)
        self.room2.tags.add("return", category=ROOM_KEY_CATEGORY)

    @override_settings(COMBAT_RESPAWN_SANCTUARY="sanctuary:return")
    def test_dead_entry_restores_at_sanctuary_and_clears_only_temporary_effects(self):
        self.char1.db.hp_current = 0
        self.char1.db.position = "standing"
        self.char1.attributes.add(INJURY_ATTRIBUTE, _injury_payload(InjuryState.DEAD))
        self.char1.effects.add(_TEMPORARY.key, quiet=True)
        self.char1.effects.add(_PERMANENT.key, quiet=True)

        prepare_entry(self.char1)

        self.assertEqual(self.char1.location, self.room2)
        self.assertEqual(self.char1.stats.hp_current, self.char1.stats.hp_max)
        self.assertEqual(self.char1.db.position, "resting")
        self.assertFalse(self.char1.effects.has(_TEMPORARY.key))
        self.assertTrue(self.char1.effects.has(_PERMANENT.key))

    @override_settings(COMBAT_LINKDEAD_MINUTES=1)
    def test_expired_stable_linkdead_returns_at_one_hp_to_original_room(self):
        self.char1.db.hp_current = 0
        self.char1.attributes.add(
            INJURY_ATTRIBUTE, _injury_payload(InjuryState.INCAPACITATED)
        )
        _on_character_lifecycle(
            CharacterLifecycleEvent(
                self.char1,
                CharacterAvailability.UNAVAILABLE,
                1,
                UnavailabilityCause.DISCONNECT,
            )
        )

        process_linkdead_pulse(PulseEvent(60, PulseLane.RECOVERY, 30))
        process_linkdead_pulse(PulseEvent(120, PulseLane.RECOVERY, 31))

        self.assertIsNone(self.char1.location)
        self.assertTrue(self.char1.attributes.get(LINKDEAD_ATTRIBUTE)["stowed"])
        prepare_entry(self.char1)

        self.assertEqual(self.char1.location, self.room1)
        self.assertEqual(self.char1.stats.hp_current, 1)
        self.assertEqual(self.char1.db.position, "resting")
        self.assertIsNone(self.char1.attributes.get(LINKDEAD_ATTRIBUTE))
